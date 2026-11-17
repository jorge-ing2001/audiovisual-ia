"""Persistencia y versionado del ``EditProject`` sobre ``Timeline`` + ``Version``.

Reutiliza las tablas existentes SIN migraciones de esquema (§3.4 de la arquitectura):

- El ``EditProject`` activo se guarda como una fila de la tabla ``Timeline`` cuyo campo
  ``json`` lleva el documento marcado con ``kind='edit_project'``. La fila de MAYOR
  ``version`` marcada así (y de la secuencia pedida, ver abajo) es el estado activo.
- Cada guardado registra además un snapshot en ``Version`` con
  ``entity_type='edit_project'`` y ``entity_id=project_id``, para undo/redo. Undo/redo
  mueven el "puntero" de versión activa: re-guardan el snapshot destino como una nueva
  fila ``Timeline`` activa (append-only, sin perder historial) y anotan el puntero en el
  propio snapshot/fila.

El puntero de historial se deriva del contenido: cada fila ``Version`` lleva en su
``snapshot`` el ``EditProject`` y un campo ``_cursor`` (índice 0-based dentro de la
secuencia de versiones) para saber en qué punto del historial está el usuario tras
undo/redo. ``save_edit_project`` (edición nueva) trunca el redo: descarta las versiones
por delante del cursor actual.

Secuencias múltiples (FEAT-008, §7). Una "secuencia" es un ``EditProject`` independiente
identificado por ``sequence_id`` (default ``main``). La persistencia se segmenta por
secuencia: ``_edit_timeline_rows``/``load_active_edit_project`` filtran por ``sequence_id``
y ``save_edit_project``/``undo``/``redo``/``history`` calculan el cursor/``_max`` SOLO sobre
las ``Version`` de esa ``sequence_id`` (se añade ``sequence_id`` al snapshot). Así cada
secuencia tiene su propio historial de undo/redo. REGRESIÓN: sin indicar ``sequence_id`` el
comportamiento de la secuencia ``main`` es EXACTAMENTE el de antes (documentos y snapshots
previos sin el campo se tratan como ``main``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.editor.model import EDIT_PROJECT_KIND, EditProject
from app.models.entities import Project, Version
from app.models.entities import Timeline as TimelineRow

__all__ = [
    "ENTITY_TYPE",
    "DEFAULT_SEQUENCE_ID",
    "load_active_edit_project",
    "save_edit_project",
    "undo",
    "redo",
    "history",
    "list_sequences",
    "create_sequence",
]

ENTITY_TYPE = "edit_project"
#: Secuencia por defecto (compatibilidad: documentos previos sin ``sequence_id`` = main).
DEFAULT_SEQUENCE_ID = "main"
_CURSOR_KEY = "_cursor"
# Frontera de redo válida en el momento de ese movimiento. Una edición nueva la fija a su
# propio cursor (trunca cualquier redo pendiente); undo/redo la conservan.
_MAX_KEY = "_max"
_SEQUENCE_KEY = "sequence_id"


def _row_sequence_id(payload: object) -> str:
    """``sequence_id`` de una fila/snapshot (default ``main`` si no lo trae: regresión)."""
    if isinstance(payload, dict):
        value = payload.get(_SEQUENCE_KEY)
        if isinstance(value, str) and value:
            return value
    return DEFAULT_SEQUENCE_ID


def _edit_timeline_rows(
    project_id: int, session: Session, sequence_id: str = DEFAULT_SEQUENCE_ID
) -> list[TimelineRow]:
    """Filas ``Timeline`` de edición de la secuencia, de más antigua a más reciente."""
    stmt = (
        select(TimelineRow)
        .where(TimelineRow.project_id == project_id)
        .order_by(TimelineRow.version.asc())
    )
    rows = list(session.execute(stmt).scalars())
    return [
        r
        for r in rows
        if isinstance(r.json, dict)
        and r.json.get("kind") == EDIT_PROJECT_KIND
        and _row_sequence_id(r.json) == sequence_id
    ]


def _all_edit_timeline_rows(project_id: int, session: Session) -> list[TimelineRow]:
    """Todas las filas ``Timeline`` de edición del proyecto (cualquier secuencia)."""
    stmt = (
        select(TimelineRow)
        .where(TimelineRow.project_id == project_id)
        .order_by(TimelineRow.version.asc())
    )
    rows = list(session.execute(stmt).scalars())
    return [
        r for r in rows if isinstance(r.json, dict) and r.json.get("kind") == EDIT_PROJECT_KIND
    ]


def _next_timeline_version(project_id: int, session: Session) -> int:
    stmt = select(TimelineRow.version).where(TimelineRow.project_id == project_id)
    versions = [v for v in session.execute(stmt).scalars()]
    return (max(versions) if versions else 0) + 1


def _versions(
    project_id: int, session: Session, sequence_id: str = DEFAULT_SEQUENCE_ID
) -> list[Version]:
    """Filas ``Version`` de la secuencia pedida, ordenadas por ``version`` ascendente.

    Se filtran por el ``sequence_id`` del snapshot (default ``main`` si no lo trae: los
    snapshots previos sin el campo pertenecen a la secuencia ``main``). Así cada secuencia
    tiene su propio historial de undo/redo (cursor/``_max`` independientes).
    """
    stmt = (
        select(Version)
        .where(Version.entity_type == ENTITY_TYPE, Version.entity_id == project_id)
        .order_by(Version.version.asc())
    )
    rows = list(session.execute(stmt).scalars())
    return [r for r in rows if _row_sequence_id(r.snapshot) == sequence_id]


def _active_cursor(versions: list[Version]) -> int:
    """Cursor activo = el ``_cursor`` del último movimiento (fila ``Version`` más reciente).

    ``versions`` viene ordenado por ``version`` ascendente, así que la última fila es el
    movimiento más reciente (edición, undo o redo) y su ``_cursor`` marca dónde está el
    usuario en el historial.
    """
    if not versions:
        return -1
    last = versions[-1]
    return int((last.snapshot or {}).get(_CURSOR_KEY, len(versions) - 1))


def _redo_frontier(versions: list[Version]) -> int:
    """Frontera de redo vigente (``_max`` del último movimiento)."""
    if not versions:
        return -1
    last = versions[-1]
    return int((last.snapshot or {}).get(_MAX_KEY, _active_cursor(versions)))


def load_active_edit_project(
    project: Project, session: Session, sequence_id: str = DEFAULT_SEQUENCE_ID
) -> EditProject | None:
    """Devuelve el ``EditProject`` activo de la secuencia, o ``None`` si no hay ninguno.

    El activo es la fila ``Timeline`` de edición de MAYOR versión CON ese ``sequence_id``.
    Sin indicar ``sequence_id`` opera sobre ``main`` (regresión: igual que antes).
    """
    rows = _edit_timeline_rows(project.id, session, sequence_id)
    if not rows:
        return None
    latest = rows[-1]
    return EditProject.model_validate(latest.json)


def save_edit_project(
    project: Project,
    session: Session,
    edit_project: EditProject,
    sequence_id: str | None = None,
) -> EditProject:
    """Persiste ``edit_project`` como nueva versión activa y registra un snapshot.

    Escribe una nueva fila ``Timeline`` (versión incremental) con el documento marcado
    ``kind='edit_project'`` y añade una fila ``Version`` con el snapshot. Al ser una
    edición nueva, el cursor avanza al final del historial DE ESA SECUENCIA (se trunca
    cualquier redo pendiente de la secuencia). ``sequence_id`` se deriva del propio
    documento si no se pasa explícitamente (default ``main``).
    """
    if sequence_id is None:
        sequence_id = edit_project.sequence_id or DEFAULT_SEQUENCE_ID

    # Normaliza la marca de documento de edición y asegura el ``sequence_id`` coherente.
    payload = edit_project.model_dump(mode="json", by_alias=False)
    payload["kind"] = EDIT_PROJECT_KIND
    payload[_SEQUENCE_KEY] = sequence_id

    version = _next_timeline_version(project.id, session)
    row = TimelineRow(project_id=project.id, json=payload, version=version)
    session.add(row)

    existing = _versions(project.id, session, sequence_id)
    # El número de ``Version`` es global del proyecto (no de la secuencia) para no colisionar.
    all_versions = (
        select(Version.version)
        .where(Version.entity_type == ENTITY_TYPE, Version.entity_id == project.id)
    )
    next_version = (max([v for v in session.execute(all_versions).scalars()], default=0)) + 1
    # El cursor de una edición nueva es "una posición más allá" del estado activo de la
    # secuencia; la frontera de redo se fija a ese mismo cursor, truncando redos pendientes.
    new_cursor = _active_cursor(existing) + 1
    snapshot = dict(payload)
    snapshot[_CURSOR_KEY] = new_cursor
    snapshot[_MAX_KEY] = new_cursor
    snapshot[_SEQUENCE_KEY] = sequence_id
    session.add(
        Version(
            project_id=project.id,
            entity_type=ENTITY_TYPE,
            entity_id=project.id,
            version=next_version,
            snapshot=snapshot,
        )
    )
    session.commit()
    return EditProject.model_validate(payload)


def _restore_cursor(
    project: Project,
    session: Session,
    versions: list[Version],
    target_cursor: int,
    frontier: int,
    sequence_id: str,
) -> EditProject:
    """Hace activo el ``EditProject`` del ``_cursor`` destino (re-guarda como nueva fila)."""
    # Busca la versión cuyo _cursor coincide con el destino (la más reciente que lo tenga).
    chosen: Version | None = None
    for v in versions:
        if int((v.snapshot or {}).get(_CURSOR_KEY, -1)) == target_cursor:
            chosen = v
    if chosen is None:
        # Fallback defensivo: índice directo.
        chosen = versions[max(0, min(target_cursor, len(versions) - 1))]

    payload = {
        k: val
        for k, val in (chosen.snapshot or {}).items()
        if k not in (_CURSOR_KEY, _MAX_KEY)
    }
    payload["kind"] = EDIT_PROJECT_KIND
    payload[_SEQUENCE_KEY] = sequence_id

    version = _next_timeline_version(project.id, session)
    session.add(TimelineRow(project_id=project.id, json=payload, version=version))

    all_versions = (
        select(Version.version)
        .where(Version.entity_type == ENTITY_TYPE, Version.entity_id == project.id)
    )
    next_version = (max([v for v in session.execute(all_versions).scalars()], default=0)) + 1
    snapshot = dict(payload)
    snapshot[_CURSOR_KEY] = target_cursor
    snapshot[_MAX_KEY] = frontier
    snapshot[_SEQUENCE_KEY] = sequence_id
    session.add(
        Version(
            project_id=project.id,
            entity_type=ENTITY_TYPE,
            entity_id=project.id,
            version=next_version,
            snapshot=snapshot,
        )
    )
    session.commit()
    return EditProject.model_validate(payload)


def undo(
    project: Project, session: Session, sequence_id: str = DEFAULT_SEQUENCE_ID
) -> EditProject | None:
    """Retrocede un paso en el historial de la secuencia. Devuelve el estado o ``None``.

    ``None`` si no hay nada que deshacer (cursor ya en el primer estado de la secuencia).
    """
    versions = _versions(project.id, session, sequence_id)
    if not versions:
        return None
    cursor = _active_cursor(versions)
    if cursor <= 0:
        return None
    return _restore_cursor(
        project, session, versions, cursor - 1, _redo_frontier(versions), sequence_id
    )


def redo(
    project: Project, session: Session, sequence_id: str = DEFAULT_SEQUENCE_ID
) -> EditProject | None:
    """Avanza un paso en el historial de la secuencia. Devuelve el estado o ``None``.

    ``None`` si no hay nada que rehacer (cursor en el estado más avanzado de la secuencia).
    """
    versions = _versions(project.id, session, sequence_id)
    if not versions:
        return None
    cursor = _active_cursor(versions)
    frontier = _redo_frontier(versions)
    if cursor >= frontier:
        return None
    return _restore_cursor(
        project, session, versions, cursor + 1, frontier, sequence_id
    )


def history(
    project: Project, session: Session, sequence_id: str = DEFAULT_SEQUENCE_ID
) -> list[dict]:
    """Lista las versiones del historial de edición de la secuencia (para una UI)."""
    versions = _versions(project.id, session, sequence_id)
    active = _active_cursor(versions)
    out: list[dict] = []
    for v in versions:
        cursor = int((v.snapshot or {}).get(_CURSOR_KEY, -1))
        out.append(  # noqa: PERF401
            {
                "version": v.version,
                "cursor": cursor,
                "active": cursor == active,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Catálogo de secuencias (FEAT-008)
# --------------------------------------------------------------------------- #


def list_sequences(project: Project, session: Session) -> list[dict]:
    """Lista las secuencias del proyecto derivadas de las filas ``Timeline`` de edición.

    Cada secuencia aparece una vez (su fila activa, la de mayor versión con ese
    ``sequence_id``). Devuelve ``[{sequence_id, sequence_name, duration_s, version}]``
    ordenado con ``main`` primero y el resto por orden de aparición. No crea nada.
    """
    rows = _all_edit_timeline_rows(project.id, session)
    # De más reciente a más antigua: la primera vista de cada sequence_id es su activa.
    latest_by_seq: dict[str, TimelineRow] = {}
    for row in reversed(rows):
        seq = _row_sequence_id(row.json)
        if seq not in latest_by_seq:
            latest_by_seq[seq] = row

    out: list[dict] = []
    for seq, row in latest_by_seq.items():
        try:
            edit_project = EditProject.model_validate(row.json)
        except Exception:  # noqa: BLE001 - fila corrupta: se ignora en el catálogo
            continue
        out.append(
            {
                "sequence_id": seq,
                "sequence_name": edit_project.sequence_name,
                "duration_s": edit_project.duration_s,
                "version": row.version,
            }
        )
    # ``main`` primero, luego por versión activa ascendente (orden de creación aproximado).
    out.sort(key=lambda s: (s["sequence_id"] != DEFAULT_SEQUENCE_ID, s["version"]))
    return out


def create_sequence(
    project: Project,
    session: Session,
    sequence_id: str,
    name: str | None = None,
    *,
    base_from_active: EditProject | None = None,
    from_timeline: EditProject | None = None,
) -> EditProject:
    """Crea una secuencia nueva (``sequence_id``) y la persiste como su primera versión.

    Punto de partida (exclusivo): ``base_from_active`` (duplica un ``EditProject`` ya
    cargado, p. ej. la secuencia activa) o ``from_timeline`` (un ``EditProject`` derivado
    del ``Timeline`` de generación, como hace ``open``). El documento resultante se marca
    con el nuevo ``sequence_id``/``sequence_name`` y se guarda con ``save_edit_project``, de
    modo que la secuencia arranca con su propio historial (cursor en 0). Rechaza
    ``ValueError`` si la secuencia ya existe o si no se pasa un punto de partida.
    """
    if not sequence_id:
        raise ValueError("create_sequence: 'sequence_id' es obligatorio.")
    if _edit_timeline_rows(project.id, session, sequence_id):
        raise ValueError(f"create_sequence: la secuencia {sequence_id!r} ya existe.")

    base = base_from_active if base_from_active is not None else from_timeline
    if base is None:
        raise ValueError(
            "create_sequence: indica un punto de partida (base_from_active o from_timeline)."
        )

    new_name = name or base.sequence_name or sequence_id
    new_project = base.model_copy(
        deep=True,
        update={
            "project_id": project.id,
            "sequence_id": sequence_id,
            "sequence_name": new_name,
        },
    )
    return save_edit_project(project, session, new_project, sequence_id=sequence_id)

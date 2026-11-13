"""Persistencia y versionado del ``EditProject`` sobre ``Timeline`` + ``Version``.

Reutiliza las tablas existentes SIN migraciones de esquema (§3.4 de la arquitectura):

- El ``EditProject`` activo se guarda como una fila de la tabla ``Timeline`` cuyo campo
  ``json`` lleva el documento marcado con ``kind='edit_project'``. La fila de MAYOR
  ``version`` marcada así es el estado activo del editor.
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
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.editor.model import EDIT_PROJECT_KIND, EditProject
from app.models.entities import Project, Version
from app.models.entities import Timeline as TimelineRow

__all__ = [
    "ENTITY_TYPE",
    "load_active_edit_project",
    "save_edit_project",
    "undo",
    "redo",
    "history",
]

ENTITY_TYPE = "edit_project"
_CURSOR_KEY = "_cursor"
# Frontera de redo válida en el momento de ese movimiento. Una edición nueva la fija a su
# propio cursor (trunca cualquier redo pendiente); undo/redo la conservan.
_MAX_KEY = "_max"


def _edit_timeline_rows(project_id: int, session: Session) -> list[TimelineRow]:
    """Filas ``Timeline`` de edición del proyecto, de más antigua a más reciente."""
    stmt = (
        select(TimelineRow)
        .where(TimelineRow.project_id == project_id)
        .order_by(TimelineRow.version.asc())
    )
    rows = list(session.execute(stmt).scalars())
    return [r for r in rows if isinstance(r.json, dict) and r.json.get("kind") == EDIT_PROJECT_KIND]


def _next_timeline_version(project_id: int, session: Session) -> int:
    stmt = select(TimelineRow.version).where(TimelineRow.project_id == project_id)
    versions = [v for v in session.execute(stmt).scalars()]
    return (max(versions) if versions else 0) + 1


def _versions(project_id: int, session: Session) -> list[Version]:
    stmt = (
        select(Version)
        .where(Version.entity_type == ENTITY_TYPE, Version.entity_id == project_id)
        .order_by(Version.version.asc())
    )
    return list(session.execute(stmt).scalars())


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


def load_active_edit_project(project: Project, session: Session) -> EditProject | None:
    """Devuelve el ``EditProject`` activo del proyecto, o ``None`` si no hay ninguno.

    El activo es la fila ``Timeline`` de edición de MAYOR versión.
    """
    rows = _edit_timeline_rows(project.id, session)
    if not rows:
        return None
    latest = rows[-1]
    return EditProject.model_validate(latest.json)


def save_edit_project(
    project: Project, session: Session, edit_project: EditProject
) -> EditProject:
    """Persiste ``edit_project`` como nueva versión activa y registra un snapshot.

    Escribe una nueva fila ``Timeline`` (versión incremental) con el documento marcado
    ``kind='edit_project'`` y añade una fila ``Version`` con el snapshot. Al ser una
    edición nueva, el cursor avanza al final del historial (se trunca cualquier redo
    pendiente avanzando el cursor más allá de las versiones futuras existentes).
    """
    # Normaliza la marca de documento de edición.
    payload = edit_project.model_dump(mode="json", by_alias=False)
    payload["kind"] = EDIT_PROJECT_KIND

    version = _next_timeline_version(project.id, session)
    row = TimelineRow(project_id=project.id, json=payload, version=version)
    session.add(row)

    existing = _versions(project.id, session)
    next_version = (max((v.version for v in existing), default=0)) + 1
    # El cursor de una edición nueva es "una posición más allá" del estado activo; la
    # frontera de redo se fija a ese mismo cursor, truncando cualquier redo pendiente.
    new_cursor = _active_cursor(existing) + 1
    snapshot = dict(payload)
    snapshot[_CURSOR_KEY] = new_cursor
    snapshot[_MAX_KEY] = new_cursor
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

    version = _next_timeline_version(project.id, session)
    session.add(TimelineRow(project_id=project.id, json=payload, version=version))

    next_version = (max((v.version for v in versions), default=0)) + 1
    snapshot = dict(payload)
    snapshot[_CURSOR_KEY] = target_cursor
    snapshot[_MAX_KEY] = frontier
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


def undo(project: Project, session: Session) -> EditProject | None:
    """Retrocede un paso en el historial. Devuelve el estado resultante o ``None``.

    ``None`` si no hay nada que deshacer (cursor ya en el primer estado).
    """
    versions = _versions(project.id, session)
    if not versions:
        return None
    cursor = _active_cursor(versions)
    if cursor <= 0:
        return None
    return _restore_cursor(project, session, versions, cursor - 1, _redo_frontier(versions))


def redo(project: Project, session: Session) -> EditProject | None:
    """Avanza un paso en el historial. Devuelve el estado resultante o ``None``.

    ``None`` si no hay nada que rehacer (cursor en el estado más avanzado alcanzado).
    """
    versions = _versions(project.id, session)
    if not versions:
        return None
    cursor = _active_cursor(versions)
    frontier = _redo_frontier(versions)
    if cursor >= frontier:
        return None
    return _restore_cursor(project, session, versions, cursor + 1, frontier)


def history(project: Project, session: Session) -> list[dict]:
    """Lista las versiones del historial de edición (para una UI de historial)."""
    versions = _versions(project.id, session)
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

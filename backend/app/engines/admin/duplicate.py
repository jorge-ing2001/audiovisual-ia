"""Duplicación profunda de proyectos (copia con identidades nuevas).

Dos piezas (ver ``docs/ADMIN-QUALITY.md`` §1.1):

- ``build_duplication_plan(...)``: función PURA que NO toca disco ni BD. Calcula, a partir
  de las entidades del origen y de un ``new_id`` tentativo, QUÉ filas crear (con las FKs
  ya re-mapeadas), QUÉ ficheros copiar (``path_map``) y cómo quedan los JSON reescritos
  (rutas ``project_{old}`` -> ``project_{new}``). Es determinista y testeable sin E/S.
- ``run_duplication_plan(...)``: EJECUTA el plan sobre una sesión SQLAlchemy y el sistema
  de ficheros, en el orden que hace recuperable un fallo a mitad (INSERT Project + flush,
  workspace, INSERT Scene + flush para fijar ``scene_map``, INSERT Asset/Timeline/
  Version/Script con FKs y JSON re-mapeados, copia de ficheros, commit único). Ante
  cualquier excepción hace rollback de BD y borra el workspace destino parcial con la
  rutina de borrado seguro, dejando el sistema como estaba.

La identidad es NUEVA (ids autoincrement de SQLite al hacer flush); lo que se PRESERVA son
las relaciones internas vía los mapas ``project_id -> new_id`` y ``scene_id`` old -> new.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Asset, Project, Scene, Script, Timeline, Version
from app.storage.media import ensure_project_workspace, project_dir, safe_rmtree

__all__ = [
    "PlannedRow",
    "DuplicationPlan",
    "build_duplication_plan",
    "run_duplication_plan",
    "load_source_entities",
    "count_source_assets",
    "duplicate_project",
]


@dataclass
class PlannedRow:
    """Fila a crear, con sus campos ya re-mapeados.

    ``scene_ref`` referencia al id ORIGINAL de la escena del origen (para assets ligados a
    escena); al ejecutar se traduce vía ``scene_map`` al id nuevo. ``None`` significa que
    el asset no está ligado a una escena (se mantiene ``scene_id=None``).
    """

    fields: dict[str, Any]
    scene_ref: int | None = None


@dataclass
class DuplicationPlan:
    """Resultado PURO de planificar una duplicación."""

    old_id: int
    new_id: int
    project_fields: dict[str, Any]
    scenes: list[PlannedRow] = field(default_factory=list)
    assets: list[PlannedRow] = field(default_factory=list)
    timelines: list[PlannedRow] = field(default_factory=list)
    versions: list[PlannedRow] = field(default_factory=list)
    scripts: list[PlannedRow] = field(default_factory=list)
    # Pares (src_abs, dst_abs) de ficheros a copiar.
    path_map: list[tuple[str, str]] = field(default_factory=list)


def _old_prefix(old_id: int) -> str:
    return project_dir(old_id).as_posix()


def _new_prefix(new_id: int) -> str:
    return project_dir(new_id).as_posix()


def _rewrite_path(value: str, old_prefix: str, new_prefix: str) -> str:
    """Reescribe un string de ruta reemplazando el prefijo ``project_{old}`` por el nuevo.

    Tolerante a separadores: compara sobre la forma POSIX (``\\`` -> ``/``) y, si coincide
    el prefijo, devuelve la ruta nueva en forma POSIX. Si no empieza por el prefijo del
    origen, devuelve el valor sin cambios.
    """
    posix = value.replace("\\", "/")
    if posix == old_prefix:
        return new_prefix
    if posix.startswith(old_prefix + "/"):
        return new_prefix + posix[len(old_prefix):]
    return value


def _rewrite_json(
    node: Any, old_prefix: str, new_prefix: str
) -> Any:
    """Reescribe recursivamente todas las cadenas de ruta de un JSON (dict/list/str).

    Cualquier string que empiece por el prefijo ``project_{old}`` se reescribe a
    ``project_{new}``; el resto de valores se conservan. Devuelve una copia (no muta la
    estructura de entrada), para que el origen quede intacto.
    """
    if isinstance(node, dict):
        return {k: _rewrite_json(v, old_prefix, new_prefix) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite_json(v, old_prefix, new_prefix) for v in node]
    if isinstance(node, str):
        return _rewrite_path(node, old_prefix, new_prefix)
    return node


def _copy_project_metadata(project: Project, new_name: str | None) -> dict[str, Any]:
    """Campos del Project nuevo: copia salvo id/created_at/status, nombre configurable."""
    name = new_name if new_name else f"{project.name} (copia)"
    return {
        "name": name,
        "prompt": project.prompt,
        "production_type": project.production_type,
        "aspect_ratio": project.aspect_ratio,
        "resolution": project.resolution,
        # Un proyecto duplicado arranca en 'draft' (aún no renderizado/generado).
        "status": "draft",
        "provider_overrides": project.provider_overrides,
        "folder": project.folder,
        # Copia defensiva de la lista de tags (no compartir referencia con el origen).
        "tags": list(project.tags) if project.tags else None,
    }


def build_duplication_plan(
    project: Project,
    scenes: list[Scene],
    assets: list[Asset],
    timelines: list[Timeline],
    scripts: list[Script],
    new_id: int,
) -> DuplicationPlan:
    """Planifica una duplicación SIN tocar disco ni BD (ver módulo).

    ``new_id`` es el id tentativo del proyecto destino (se usa para derivar el prefijo de
    rutas ``project_{new_id}``). En ejecución, el id real lo fija SQLite al hacer flush;
    ``run_duplication_plan`` vuelve a calcular las rutas con el id real, por lo que aquí
    ``new_id`` solo necesita ser el que se persistirá.
    """
    old_id = project.id
    old_prefix = _old_prefix(old_id)
    new_prefix = _new_prefix(new_id)

    plan = DuplicationPlan(
        old_id=old_id,
        new_id=new_id,
        project_fields=_copy_project_metadata(project, None),
    )

    path_pairs: list[tuple[str, str]] = []

    # Scenes (conservan 'order' y el resto de campos; project_id se fija en ejecución).
    for scene in sorted(scenes, key=lambda s: (s.order, s.id)):
        plan.scenes.append(
            PlannedRow(
                fields={
                    "_old_id": scene.id,
                    "order": scene.order,
                    "narration_text": scene.narration_text,
                    "visual_prompt": scene.visual_prompt,
                    "duration_s": scene.duration_s,
                    "camera_notes": scene.camera_notes,
                    "status": scene.status,
                }
            )
        )

    # Assets (scene_id re-mapeado vía scene_ref; path reescrito al workspace nuevo).
    for asset in assets:
        new_path = _rewrite_path(asset.path, old_prefix, new_prefix)
        if new_path != asset.path:
            path_pairs.append((asset.path, new_path))
        plan.assets.append(
            PlannedRow(
                fields={
                    "kind": asset.kind,
                    "path": new_path,
                    "provider": asset.provider,
                    "meta": _rewrite_json(asset.meta, old_prefix, new_prefix)
                    if asset.meta is not None
                    else None,
                },
                scene_ref=asset.scene_id,
            )
        )

    # Timelines (incluye EditProject persistido): JSON reescrito; rutas de recurso -> copia.
    for timeline in timelines:
        rewritten = _rewrite_json(timeline.json, old_prefix, new_prefix)
        plan.timelines.append(
            PlannedRow(fields={"json": rewritten, "version": timeline.version})
        )
        for src in _collect_resource_paths(timeline.json, old_prefix):
            dst = _rewrite_path(src, old_prefix, new_prefix)
            if dst != src:
                path_pairs.append((src, dst))

    # Scripts (sin rutas de media; copia directa de campos).
    for script in scripts:
        plan.scripts.append(
            PlannedRow(
                fields={
                    "structure": script.structure,
                    "tone": script.tone,
                    "language": script.language,
                    "raw_text": script.raw_text,
                    "version": script.version,
                }
            )
        )

    # path_map sin duplicados, preservando orden de descubrimiento.
    seen: set[tuple[str, str]] = set()
    for pair in path_pairs:
        if pair not in seen:
            seen.add(pair)
            plan.path_map.append(pair)

    return plan


def _collect_resource_paths(node: Any, old_prefix: str) -> list[str]:
    """Reúne todas las cadenas que son rutas bajo ``project_{old}`` dentro de un JSON."""
    found: list[str] = []
    if isinstance(node, dict):
        for value in node.values():
            found.extend(_collect_resource_paths(value, old_prefix))
    elif isinstance(node, list):
        for value in node:
            found.extend(_collect_resource_paths(value, old_prefix))
    elif isinstance(node, str):
        posix = node.replace("\\", "/")
        if posix == old_prefix or posix.startswith(old_prefix + "/"):
            found.append(node)
    return found


def run_duplication_plan(
    session: Session,
    project: Project,
    scenes: list[Scene],
    assets: list[Asset],
    timelines: list[Timeline],
    scripts: list[Script],
    versions: list[Version],
    *,
    new_name: str | None = None,
    copy_file: Callable[[str, str], None] | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> int:
    """Ejecuta una duplicación completa y devuelve el id del proyecto nuevo.

    Orden (recuperable ante fallo): INSERT Project + flush (fija ``new_id``);
    ``ensure_project_workspace``; INSERT Scene + flush (puebla ``scene_map``); INSERT
    Asset/Timeline/Version/Script con FKs y JSON re-mapeados al ``new_id`` REAL; copia de
    ficheros; commit único. Ante cualquier excepción: rollback de BD y borrado seguro del
    workspace destino parcial (nunca el origen).

    ``copy_file`` permite inyectar la rutina de copia (por defecto ``shutil.copy2`` creando
    subdirectorios); los tests la usan para simular un fallo a mitad. ``progress_cb`` recibe
    una fracción 0..1 conforme avanza la copia (modo job).
    """
    import shutil

    def _default_copy(src: str, dst: str) -> None:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    copier = copy_file or _default_copy

    old_id = project.id
    old_prefix = _old_prefix(old_id)
    new_workspace: Path | None = None

    try:
        # (1) Project nuevo + flush para fijar el id real.
        fields = _copy_project_metadata(project, new_name)
        new_project = Project(**fields)
        session.add(new_project)
        session.flush()
        new_id = new_project.id
        new_prefix = _new_prefix(new_id)

        # (2) Workspace destino.
        new_workspace = ensure_project_workspace(new_id)

        # (3) Scenes + flush para poblar scene_map (old -> new).
        scene_map: dict[int, int] = {}
        for scene in sorted(scenes, key=lambda s: (s.order, s.id)):
            new_scene = Scene(
                project_id=new_id,
                order=scene.order,
                narration_text=scene.narration_text,
                visual_prompt=scene.visual_prompt,
                duration_s=scene.duration_s,
                camera_notes=scene.camera_notes,
                status=scene.status,
            )
            session.add(new_scene)
            session.flush()
            scene_map[scene.id] = new_scene.id

        # (4) Assets (scene_id re-mapeado, path reescrito).
        path_pairs: list[tuple[str, str]] = []
        for asset in assets:
            new_path = _rewrite_path(asset.path, old_prefix, new_prefix)
            if new_path != asset.path:
                path_pairs.append((asset.path, new_path))
            session.add(
                Asset(
                    project_id=new_id,
                    scene_id=scene_map.get(asset.scene_id)
                    if asset.scene_id is not None
                    else None,
                    kind=asset.kind,
                    path=new_path,
                    provider=asset.provider,
                    meta=_rewrite_json(asset.meta, old_prefix, new_prefix)
                    if asset.meta is not None
                    else None,
                )
            )

        # (4b) Timelines (incluye EditProject) con JSON reescrito + recolectar rutas.
        for timeline in timelines:
            session.add(
                Timeline(
                    project_id=new_id,
                    json=_rewrite_json(timeline.json, old_prefix, new_prefix),
                    version=timeline.version,
                )
            )
            for src in _collect_resource_paths(timeline.json, old_prefix):
                dst = _rewrite_path(src, old_prefix, new_prefix)
                if dst != src:
                    path_pairs.append((src, dst))

        # (4c) Versions (historial undo/redo) con snapshot reescrito y entity_id re-mapeado.
        for ver in versions:
            new_entity_id = ver.entity_id
            if ver.entity_type == "edit_project":
                new_entity_id = new_id
            session.add(
                Version(
                    project_id=new_id,
                    entity_type=ver.entity_type,
                    entity_id=new_entity_id,
                    version=ver.version,
                    snapshot=_rewrite_json(ver.snapshot, old_prefix, new_prefix),
                )
            )

        # (4d) Scripts.
        for script in scripts:
            session.add(
                Script(
                    project_id=new_id,
                    structure=script.structure,
                    tone=script.tone,
                    language=script.language,
                    raw_text=script.raw_text,
                    version=script.version,
                )
            )

        # (5) Copia de ficheros (path_map deduplicado, orden de descubrimiento).
        seen: set[tuple[str, str]] = set()
        unique_pairs: list[tuple[str, str]] = []
        for pair in path_pairs:
            if pair not in seen:
                seen.add(pair)
                unique_pairs.append(pair)

        total = len(unique_pairs)
        for idx, (src, dst) in enumerate(unique_pairs, start=1):
            copier(src, dst)
            if progress_cb is not None and total:
                progress_cb(idx / total)

        # (6) Commit único.
        session.commit()
        return new_id

    except Exception:
        # Deshacer en orden: primero BD, luego workspace parcial.
        session.rollback()
        if new_workspace is not None:
            try:
                safe_rmtree(new_workspace)
            except Exception:  # noqa: BLE001 - la limpieza no debe enmascarar el error real
                pass
        raise


# --------------------------------------------------------------------------- #
# Servicio de alto nivel (carga de entidades + ejecución)
# --------------------------------------------------------------------------- #


def load_source_entities(
    session: Session, project: Project
) -> tuple[list[Scene], list[Asset], list[Timeline], list[Script], list[Version]]:
    """Lee del origen todas las entidades a duplicar (escenas/assets/timelines/scripts/versions)."""
    from sqlalchemy import select

    scenes = list(
        session.execute(
            select(Scene).where(Scene.project_id == project.id).order_by(Scene.id.asc())
        ).scalars()
    )
    assets = list(
        session.execute(
            select(Asset).where(Asset.project_id == project.id).order_by(Asset.id.asc())
        ).scalars()
    )
    timelines = list(
        session.execute(
            select(Timeline)
            .where(Timeline.project_id == project.id)
            .order_by(Timeline.id.asc())
        ).scalars()
    )
    scripts = list(
        session.execute(
            select(Script).where(Script.project_id == project.id).order_by(Script.id.asc())
        ).scalars()
    )
    versions = list(
        session.execute(
            select(Version)
            .where(Version.project_id == project.id)
            .order_by(Version.id.asc())
        ).scalars()
    )
    return scenes, assets, timelines, scripts, versions


def count_source_assets(session: Session, project: Project) -> int:
    """Número de assets del proyecto origen (métrica para decidir sync vs job)."""
    from sqlalchemy import func, select

    return int(
        session.execute(
            select(func.count()).select_from(Asset).where(Asset.project_id == project.id)
        ).scalar_one()
    )


def duplicate_project(
    session: Session,
    project: Project,
    *,
    new_name: str | None = None,
    copy_file: Callable[[str, str], None] | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> int:
    """Carga las entidades del origen y ejecuta la duplicación completa. Devuelve el new_id."""
    scenes, assets, timelines, scripts, versions = load_source_entities(session, project)
    return run_duplication_plan(
        session,
        project,
        scenes,
        assets,
        timelines,
        scripts,
        versions,
        new_name=new_name,
        copy_file=copy_file,
        progress_cb=progress_cb,
    )

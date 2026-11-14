"""Handlers de jobs del pipeline (§6). Registra los handlers de render y pipeline.

El worker (``app.jobs.worker``) reclama los jobs encolados por los endpoints y ejecuta
el handler correspondiente:

- ``render`` (FEAT-005): ``render_handler`` invoca el ``RenderEngine`` sobre el proyecto
  y devuelve el id del ``Asset(kind='video')`` y la ruta del MP4.
- ``generate_video`` (FEAT-006): ``generate_video_handler`` encadena el pipeline completo
  prompt->MP4 (guion, imagen/voz por escena, música/SFX, subtítulos, timeline y render).

Importar este módulo (lo hace ``app.main`` al arrancar) basta para registrar los handlers.
"""

from __future__ import annotations

from app.engines.render import RenderEngine, RenderUnavailable
from app.jobs.pipeline import generate_video_handler
from app.jobs.worker import register_handler
from app.models.entities import Project
from app.storage.db import session_scope


def render_handler(payload: dict | None, job_id: int) -> dict:
    """Ejecuta el render de un proyecto y devuelve el resultado para el job.

    ``payload`` debe traer ``project_id``. Lanza (para que el worker reintente o marque
    ``failed``) si el proyecto no existe o si el render falla. ``RenderUnavailable`` se
    propaga tal cual: indica que el entorno no puede renderizar (p. ej. sin Chromium).
    """
    project_id = (payload or {}).get("project_id")
    if project_id is None:
        raise ValueError("El job de render requiere 'project_id' en el payload.")

    with session_scope() as session:
        project = session.get(Project, int(project_id))
        if project is None:
            raise ValueError(f"Proyecto {project_id} no encontrado para el render.")
        try:
            result = RenderEngine().compose(project, session)
        except RenderUnavailable as exc:
            # No se puede renderizar aquí: se expone como error del job (no se simula).
            raise RenderUnavailable(str(exc)) from exc

        return {
            "project_id": int(project_id),
            "asset_id": result.asset_id,
            "video_path": result.video_path,
            "timeline_path": result.timeline_path,
            "duration_s": result.meta.get("duration_s"),
        }


def edit_export_handler(payload: dict | None, job_id: int) -> dict:
    """Re-renderiza el MP4 DESDE el ``EditProject`` activo del proyecto (editor).

    ``payload`` debe traer ``project_id``. Carga el ``EditProject`` activo (persistido en
    la tabla ``Timeline`` reutilizada) y llama a ``RenderEngine.compose_edit``, que
    re-renderiza desde el estado editado (no desde el prompt) reutilizando Remotion +
    ``AudioMixer`` + mux. ``RenderUnavailable`` se propaga tal cual (no se simula un MP4):
    indica que la composición ``Edit`` no está disponible aquí (p. ej. sin Chromium o
    antes de FEAT-003).
    """
    from app.engines.editor.store import load_active_edit_project

    project_id = (payload or {}).get("project_id")
    if project_id is None:
        raise ValueError("El job edit_export requiere 'project_id' en el payload.")

    with session_scope() as session:
        project = session.get(Project, int(project_id))
        if project is None:
            raise ValueError(f"Proyecto {project_id} no encontrado para el export de edición.")

        edit_project = load_active_edit_project(project, session)
        if edit_project is None:
            raise ValueError(
                f"El proyecto {project_id} no tiene un EditProject; abre el editor "
                "(POST /projects/{id}/editor/open) antes de exportar."
            )

        try:
            result = RenderEngine().compose_edit(project, edit_project, session)
        except RenderUnavailable as exc:
            raise RenderUnavailable(str(exc)) from exc

        return {
            "project_id": int(project_id),
            "asset_id": result.asset_id,
            "video_path": result.video_path,
            "timeline_path": result.timeline_path,
            "duration_s": result.meta.get("duration_s"),
            "source": "edit_export",
        }


register_handler("render", render_handler)
# Pipeline end-to-end prompt->video (FEAT-006).
register_handler("generate_video", generate_video_handler)
# Export del editor multipista: re-render desde el EditProject (editor, FEAT-002).
register_handler("edit_export", edit_export_handler)

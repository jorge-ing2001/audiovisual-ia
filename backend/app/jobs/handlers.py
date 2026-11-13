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


register_handler("render", render_handler)
# Pipeline end-to-end prompt->video (FEAT-006).
register_handler("generate_video", generate_video_handler)

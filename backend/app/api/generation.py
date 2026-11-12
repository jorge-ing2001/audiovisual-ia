"""Router REST de generación (motor de guion y de imagen de escena) — FEAT-003.

- ``POST /projects/{id}/script`` ejecuta el motor de guion (motor 1) y devuelve las
  escenas persistidas.
- ``POST /scenes/{scene_id}/image`` (re)genera la imagen de una escena con el motor
  visual 2D (motor 2), reutilizando la cache salvo ``force=true``.

Los motores resuelven sus proveedores vía el registry; este router solo orquesta.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.script import ScriptEngine
from app.engines.visual2d import VisualEngine
from app.jobs.pipeline import enqueue_generate_video
from app.models.entities import Asset, Job, Project, Scene
from app.models.schemas import (
    AssetRead,
    GenerateVideoRequest,
    GenerateVideoResponse,
    SceneImageRequest,
    SceneRead,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
)
from app.storage.db import get_db

router = APIRouter(tags=["generation"])


@router.post(
    "/projects/{project_id}/script",
    response_model=ScriptGenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_script(
    project_id: int,
    body: ScriptGenerateRequest | None = None,
    session: Session = Depends(get_db),
) -> ScriptGenerateResponse:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")

    body = body or ScriptGenerateRequest()
    if body.prompt is not None:
        project.prompt = body.prompt
        session.commit()

    if not (project.prompt or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El proyecto no tiene prompt; envía 'prompt' en el cuerpo o actualízalo antes.",
        )

    try:
        result = ScriptEngine().generate_script(project, session, options=body.options)
    except ValueError as exc:
        # Salida del LLM inválida / no parseable.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    stmt = (
        select(Scene)
        .where(Scene.project_id == project_id)
        .order_by(Scene.order.asc(), Scene.id.asc())
    )
    scenes = list(session.execute(stmt).scalars())
    return ScriptGenerateResponse(
        script_id=result.script_id,
        scenes=[SceneRead.model_validate(s) for s in scenes],
    )


@router.post(
    "/scenes/{scene_id}/image",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_scene_image(
    scene_id: int,
    body: SceneImageRequest | None = None,
    session: Session = Depends(get_db),
) -> Asset:
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escena no encontrada")

    body = body or SceneImageRequest()
    return VisualEngine().render_scene_image(
        scene, session, style=body.style, seed=body.seed, force=body.force
    )


@router.post(
    "/projects/{project_id}/generate",
    response_model=GenerateVideoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_video(
    project_id: int,
    body: GenerateVideoRequest | None = None,
    session: Session = Depends(get_db),
) -> GenerateVideoResponse:
    """Encola el pipeline end-to-end prompt->video (FEAT-006) y devuelve el job.

    El trabajo pesado (guion, imagen/voz por escena, música/SFX, subtítulos, timeline y
    render+mux) lo ejecuta ``generate_video_handler`` en el worker. El estado y el progreso
    por paso se consultan con ``GET /jobs/{id}``.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")

    body = body or GenerateVideoRequest()
    if body.prompt is not None:
        project.prompt = body.prompt
        session.commit()

    # Validación de prompt (§8): sin prompt, sin guion previo y sin fixture no hay entrada.
    has_prompt = bool((project.prompt or "").strip())
    if not project.scenes and body.script_fixture is None and not has_prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El proyecto no tiene prompt ni guion; envía 'prompt' (o un "
                "'script_fixture' etiquetado) para generar el vídeo."
            ),
        )

    options = {
        "style": body.style,
        "script_fixture": body.script_fixture,
        "script_options": body.script_options,
        "audio_tracks": body.audio_tracks,
    }
    job_id = enqueue_generate_video(project_id, options=options)
    job = session.get(Job, job_id)
    if job is None:  # pragma: no cover - acabamos de crearlo
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo crear el job de generación.",
        )
    return GenerateVideoResponse(job_id=job.id, status=job.status, type=job.type)

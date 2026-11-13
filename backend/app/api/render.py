"""Router REST de render (motor 6, FEAT-005).

- ``POST /projects/{id}/render`` encola un job de render (procesamiento asíncrono con la
  cola/worker existentes) y devuelve el estado inicial del job para polling.
- ``GET /projects/{id}/render`` devuelve el estado del último render del proyecto.
- ``GET /projects/{id}/video`` descarga el MP4 final (``Asset(kind='video')`` más reciente)
  una vez el job ha terminado.

El endpoint solo orquesta: encola y consulta. El trabajo pesado lo hace el
``render_handler`` en el worker, que invoca el ``RenderEngine``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.queue import JobQueue
from app.models.entities import Asset, Job, Project
from app.models.schemas import JobRead
from app.storage.db import get_db

router = APIRouter(tags=["render"])


def _get_project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado"
        )
    return project


@router.post(
    "/projects/{project_id}/render",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_render(project_id: int, session: Session = Depends(get_db)) -> Job:
    """Encola un job de render del proyecto y devuelve el job recién creado."""
    _get_project_or_404(session, project_id)

    job_id = JobQueue().enqueue(
        "render", project_id=project_id, payload={"project_id": project_id}
    )
    job = session.get(Job, job_id)
    if job is None:  # pragma: no cover - acabamos de crearlo
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo crear el job de render.",
        )
    return job


@router.get("/projects/{project_id}/render", response_model=JobRead)
def latest_render(project_id: int, session: Session = Depends(get_db)) -> Job:
    """Devuelve el job de render más reciente del proyecto (para polling de estado)."""
    _get_project_or_404(session, project_id)
    stmt = (
        select(Job)
        .where(Job.project_id == project_id, Job.type == "render")
        .order_by(Job.id.desc())
        .limit(1)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El proyecto no tiene ningún render; dispara POST .../render primero.",
        )
    return job


@router.get("/projects/{project_id}/video")
def download_video(project_id: int, session: Session = Depends(get_db)) -> FileResponse:
    """Descarga el MP4 final del proyecto (``Asset(kind='video')`` más reciente)."""
    _get_project_or_404(session, project_id)
    stmt = (
        select(Asset)
        .where(Asset.project_id == project_id, Asset.kind == "video")
        .order_by(Asset.id.desc())
        .limit(1)
    )
    asset = session.execute(stmt).scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El proyecto aún no tiene vídeo renderizado.",
        )
    path = Path(asset.path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El fichero de vídeo ya no está disponible en disco.",
        )
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=f"project_{project_id}.mp4",
    )

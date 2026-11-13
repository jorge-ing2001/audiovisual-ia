"""Router REST de jobs (estado y polling).

El frontend consulta el estado de los jobs (progreso, error, resultado) para su
panel de render. El encolado lo disparan los endpoints de generación (FEAT-006);
aquí solo se expone la lectura de estado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Job
from app.models.schemas import JobRead
from app.storage.db import get_db

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRead])
def list_jobs(
    project_id: int | None = None,
    status_filter: str | None = None,
    session: Session = Depends(get_db),
) -> list[Job]:
    stmt = select(Job)
    if project_id is not None:
        stmt = stmt.where(Job.project_id == project_id)
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    return list(session.execute(stmt.order_by(Job.id.asc())).scalars())


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, session: Session = Depends(get_db)) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job no encontrado")
    return job

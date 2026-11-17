"""Router REST de proyectos (CRUD + administración del Bloque 4).

Persiste en SQLite vía SQLAlchemy y crea el workspace de media al crear el proyecto
(ver docs/ARCHITECTURE.md §5/§8). El Bloque 4 (FEAT-002) añade:

- ``POST /projects/{id}/duplicate``: copia profunda con identidades nuevas (síncrona o
  vía Job ``duplicate_project`` para proyectos grandes).
- Filtros opcionales en ``GET /projects`` (``q``/``tag``/``folder``) manteniendo la
  compatibilidad (sin parámetros devuelve todo como antes).
- ``folder``/``tags`` en ``PATCH``.
- ``DELETE`` con borrado seguro del workspace (ver docs/ADMIN-QUALITY.md §1.4).
"""

from __future__ import annotations

from typing import Union

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engines.admin.duplicate import count_source_assets, duplicate_project
from app.jobs.queue import JobQueue
from app.models.entities import Job, Project
from app.models.schemas import (
    JobRead,
    ProjectCreate,
    ProjectDuplicateRequest,
    ProjectRead,
    ProjectUpdate,
)
from app.storage.db import get_db
from app.storage.media import (
    UnsafeRemoveError,
    ensure_project_workspace,
    project_dir,
    safe_rmtree,
)

router = APIRouter(prefix="/projects", tags=["projects"])

# Estados de job que bloquean el borrado de un proyecto (operaciones en curso).
_ACTIVE_JOB_STATUSES = ("pending", "running")


def _get_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")
    return project


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, session: Session = Depends(get_db)) -> Project:
    data = body.model_dump()
    # production_type omitido => default global (AIV_PRODUCTION_TYPE); explícito manda.
    if data.get("production_type") is None:
        data["production_type"] = get_settings().production_type
    # tags [] en el esquema -> None en BD (compatibilidad); se normaliza a [] en lectura.
    if not data.get("tags"):
        data["tags"] = None
    project = Project(**data)
    session.add(project)
    session.commit()
    session.refresh(project)
    ensure_project_workspace(project.id)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(
    q: str | None = None,
    tag: str | None = None,
    folder: str | None = None,
    session: Session = Depends(get_db),
) -> list[Project]:
    """Lista proyectos con filtros OPCIONALES (sin parámetros devuelve todo, regresión).

    - ``q``: substring case-insensitive sobre ``name`` y ``prompt``.
    - ``tag``: proyectos cuyo ``tags`` contenga ese tag (filtrado en Python por portabilidad
      de SQLite con columnas JSON).
    - ``folder``: igualdad exacta de ``folder``.
    """
    stmt = select(Project).order_by(Project.id.asc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Project.name.ilike(like), Project.prompt.ilike(like)))
    if folder is not None:
        stmt = stmt.where(Project.folder == folder)
    projects = list(session.execute(stmt).scalars())
    if tag:
        projects = [p for p in projects if p.tags and tag in p.tags]
    return projects


@router.get("/tags", response_model=list[str])
def list_tags(session: Session = Depends(get_db)) -> list[str]:
    """Lista de tags distintos existentes (para poblar filtros de la UI)."""
    seen: set[str] = set()
    for p in session.execute(select(Project)).scalars():
        for t in p.tags or []:
            seen.add(t)
    return sorted(seen)


@router.get("/folders", response_model=list[str])
def list_folders(session: Session = Depends(get_db)) -> list[str]:
    """Lista de carpetas distintas existentes (para poblar filtros de la UI)."""
    rows = session.execute(
        select(Project.folder).where(Project.folder.isnot(None)).distinct()
    ).scalars()
    return sorted({f for f in rows if f})


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, session: Session = Depends(get_db)) -> Project:
    return _get_or_404(session, project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: int, body: ProjectUpdate, session: Session = Depends(get_db)
) -> Project:
    project = _get_or_404(session, project_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    session.commit()
    session.refresh(project)
    return project


@router.post(
    "/{project_id}/duplicate",
    response_model=Union[ProjectRead, JobRead],
    status_code=status.HTTP_201_CREATED,
)
def duplicate_project_endpoint(
    project_id: int,
    body: ProjectDuplicateRequest | None = None,
    session: Session = Depends(get_db),
):
    """Duplica un proyecto (copia profunda con identidades nuevas).

    Proyectos pequeños (assets <= umbral) se duplican de forma SÍNCRONA y devuelven el
    ``ProjectRead`` nuevo (201). Proyectos grandes encolan un Job
    ``type='duplicate_project'`` con seguimiento de estado y devuelven el ``JobRead`` para
    polling (como render). El umbral es ``AIV_DUPLICATE_JOB_ASSET_THRESHOLD``.
    """
    project = _get_or_404(session, project_id)
    new_name = body.name if body else None

    threshold = get_settings().duplicate_job_asset_threshold
    asset_count = count_source_assets(session, project)

    if asset_count > threshold:
        # Modo job: el handler hace el trabajo pesado y actualiza progress.
        job_id = JobQueue().enqueue(
            "duplicate_project",
            project_id=project_id,
            payload={"project_id": project_id, "new_name": new_name},
        )
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - acabamos de crearlo
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo crear el job de duplicación.",
            )
        return job

    # Modo síncrono. Ante fallo, run_duplication_plan ya hizo rollback de BD + limpieza
    # del workspace parcial; se traduce a un 500 para el cliente.
    try:
        new_id = duplicate_project(session, project, new_name=new_name)
    except Exception as exc:  # noqa: BLE001 - se expone como error de servidor
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"La duplicación falló: {exc}",
        ) from exc
    new_project = session.get(Project, new_id)
    if new_project is None:  # pragma: no cover - acabamos de crearlo
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La duplicación no produjo un proyecto.",
        )
    return new_project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, session: Session = Depends(get_db)) -> None:
    """Borra un proyecto y su workspace de media de forma segura (docs §1.4).

    Orden de comprobaciones: (1-4) borrado seguro del workspace (resolve + contención en
    media_root + no-raíz + defensa de symlinks), (5) rechazo con 409 si el proyecto tiene
    un Job ``pending``/``running``, (6) borrado de filas (cascada ORM) + rmtree seguro.
    """
    project = _get_or_404(session, project_id)

    # (5) No borrar con operaciones activas: ANTES de tocar BD o disco.
    active = session.execute(
        select(Job.id).where(
            Job.project_id == project_id, Job.status.in_(_ACTIVE_JOB_STATUSES)
        )
    ).first()
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El proyecto tiene operaciones en curso; no se puede borrar.",
        )

    target = project_dir(project_id)
    # (6) Borrar filas (cascada ORM) y luego el workspace de forma segura.
    session.delete(project)
    session.commit()
    try:
        safe_rmtree(target)
    except UnsafeRemoveError as exc:
        # El workspace no supera las comprobaciones de seguridad: no se borró disco, pero
        # las filas ya se borraron (la cascada ORM es lo que pedía el usuario). Se informa.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se pudo borrar el workspace de forma segura: {exc}",
        ) from exc

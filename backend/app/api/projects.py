"""Router REST de proyectos (CRUD completo).

Persiste en SQLite vía SQLAlchemy y crea el workspace de media al crear el
proyecto (ver docs/ARCHITECTURE.md §5/§8).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Project
from app.models.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.storage.db import get_db
from app.storage.media import ensure_project_workspace

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")
    return project


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, session: Session = Depends(get_db)) -> Project:
    project = Project(**body.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    ensure_project_workspace(project.id)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(session: Session = Depends(get_db)) -> list[Project]:
    return list(session.execute(select(Project).order_by(Project.id.asc())).scalars())


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


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, session: Session = Depends(get_db)) -> None:
    project = _get_or_404(session, project_id)
    session.delete(project)
    session.commit()

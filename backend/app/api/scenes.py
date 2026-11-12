"""Router REST de escenas (listar y editar).

Las escenas las crea el motor de IA (FEAT-003) a partir del guion; aquí se exponen
para que el editor del frontend pueda listarlas y ajustarlas entre pasos.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Project, Scene
from app.models.schemas import SceneRead, SceneUpdate
from app.storage.db import get_db

router = APIRouter(tags=["scenes"])


@router.get("/projects/{project_id}/scenes", response_model=list[SceneRead])
def list_scenes(project_id: int, session: Session = Depends(get_db)) -> list[Scene]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")
    stmt = (
        select(Scene)
        .where(Scene.project_id == project_id)
        .order_by(Scene.order.asc(), Scene.id.asc())
    )
    return list(session.execute(stmt).scalars())


@router.patch("/scenes/{scene_id}", response_model=SceneRead)
def update_scene(
    scene_id: int, body: SceneUpdate, session: Session = Depends(get_db)
) -> Scene:
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escena no encontrada")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(scene, key, value)
    session.commit()
    session.refresh(scene)
    return scene

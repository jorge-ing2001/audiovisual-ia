"""Esquemas Pydantic de request/response para la API REST.

Separan el contrato HTTP de los modelos ORM. Los esquemas de respuesta usan
``from_attributes`` para serializar directamente desde instancias ORM.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Project
# --------------------------------------------------------------------------- #


class ProjectCreate(BaseModel):
    """Cuerpo para crear un proyecto."""

    name: str = Field(..., min_length=1, max_length=200)
    prompt: str = ""
    production_type: str = "2d"
    aspect_ratio: str = "16:9"
    resolution: str = "1080p"
    provider_overrides: dict | None = None


class ProjectUpdate(BaseModel):
    """Cuerpo para actualizar parcialmente un proyecto (PATCH)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = None
    production_type: str | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    status: str | None = None
    provider_overrides: dict | None = None


class ProjectRead(BaseModel):
    """Representación de un proyecto en respuestas."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prompt: str
    production_type: str
    aspect_ratio: str
    resolution: str
    status: str
    provider_overrides: dict | None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Scene
# --------------------------------------------------------------------------- #


class SceneUpdate(BaseModel):
    """Cuerpo para editar una escena."""

    order: int | None = None
    narration_text: str | None = None
    visual_prompt: str | None = None
    duration_s: float | None = None
    camera_notes: str | None = None
    status: str | None = None


class SceneRead(BaseModel):
    """Representación de una escena en respuestas."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    order: int
    narration_text: str
    visual_prompt: str
    duration_s: float
    camera_notes: str | None
    status: str


# --------------------------------------------------------------------------- #
# Generación (motor de guion / imagen de escena)
# --------------------------------------------------------------------------- #


class ScriptGenerateRequest(BaseModel):
    """Cuerpo opcional para disparar el motor de guion.

    Si se envía ``prompt`` se actualiza el del proyecto antes de generar; ``options``
    permite pistas (p. ej. ``tone``) para el proveedor LLM.
    """

    prompt: str | None = None
    options: dict | None = None


class ScriptGenerateResponse(BaseModel):
    """Resultado de generar el guion: metadatos del Script y sus escenas."""

    script_id: int
    scenes: list["SceneRead"]


class SceneImageRequest(BaseModel):
    """Cuerpo opcional para (re)generar la imagen de una escena."""

    style: str | None = None
    seed: int | None = None
    force: bool = False


# --------------------------------------------------------------------------- #
# Asset
# --------------------------------------------------------------------------- #


class AssetRead(BaseModel):
    """Representación de un asset en respuestas."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    scene_id: int | None
    kind: str
    path: str
    provider: str | None
    meta: dict | None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Job
# --------------------------------------------------------------------------- #


class JobRead(BaseModel):
    """Estado de un job para polling desde el frontend."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    type: str
    status: str
    progress: float
    error: str | None
    retries: int
    max_retries: int
    payload: dict | None
    result: dict | None
    created_at: datetime
    updated_at: datetime

"""Declarative base de SQLAlchemy y enums compartidos del modelo de datos."""

from __future__ import annotations

import enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa común a todas las entidades ORM."""


class JobStatus(str, enum.Enum):
    """Estados del ciclo de vida de un job (ver docs/ARCHITECTURE.md §5/§6)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AssetKind(str, enum.Enum):
    """Tipos de artefacto producidos por los motores."""

    IMAGE = "image"
    VOICE = "voice"
    MUSIC = "music"
    SFX = "sfx"
    VIDEO = "video"
    SRT = "srt"

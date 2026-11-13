"""Workspace de media por proyecto bajo ``AIV_MEDIA_ROOT``.

Cada proyecto tiene un árbol de directorios aislado donde los motores escriben
sus artefactos (imágenes, voz, música, subtítulos, video). Aquí solo se gestiona
la estructura de carpetas; el contenido lo producen los motores en FEAT-003..007.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings

# Subcarpetas estándar del workspace de un proyecto (una por tipo de artefacto).
PROJECT_SUBDIRS = ("images", "voice", "music", "sfx", "video", "srt", "timeline")


def media_root() -> Path:
    """Raíz de media resuelta desde ``AIV_MEDIA_ROOT`` (creada si no existe)."""
    root = Path(get_settings().media_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_dir(project_id: int) -> Path:
    """Directorio base del workspace de un proyecto."""
    return media_root() / f"project_{project_id}"


def ensure_project_workspace(project_id: int) -> Path:
    """Crea (idempotente) el workspace del proyecto con sus subcarpetas estándar."""
    base = project_dir(project_id)
    base.mkdir(parents=True, exist_ok=True)
    for sub in PROJECT_SUBDIRS:
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base

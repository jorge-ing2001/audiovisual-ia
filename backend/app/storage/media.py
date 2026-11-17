"""Workspace de media por proyecto bajo ``AIV_MEDIA_ROOT``.

Cada proyecto tiene un árbol de directorios aislado donde los motores escriben
sus artefactos (imágenes, voz, música, subtítulos, video). Aquí solo se gestiona
la estructura de carpetas; el contenido lo producen los motores en FEAT-003..007.
"""

from __future__ import annotations

import shutil
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


class UnsafeRemoveError(Exception):
    """El destino de borrado no supera las comprobaciones de seguridad (no se borra)."""


def assert_safe_project_target(target: Path) -> Path:
    """Valida que ``target`` es un directorio de proyecto borrable de forma segura.

    Aplica, EN ESTE ORDEN (ver ``docs/ADMIN-QUALITY.md`` §1.4), devolviendo la ruta
    resuelta si todas pasan o lanzando ``UnsafeRemoveError`` si alguna falla:

    1. Resolución de rutas absolutas: ``root = media_root().resolve()`` y
       ``resolved = target.resolve()`` (normaliza ``..`` y resuelve symlinks del propio
       destino, de modo que se compara la ruta física real).
    2. Contención real dentro de ``media_root``: exige ``resolved.is_relative_to(root)``
       (Python 3.11). Un destino que cae fuera se rechaza; esto cubre también un symlink
       que apunta fuera de ``media_root`` (al resolverlo sale de la raíz).
    3. No borrar la raíz: rechaza si ``resolved == root``.
    4. Defensa frente a symlinks: si el propio ``target`` (sin resolver) es un symlink, se
       rechaza; no se sigue el enlace para borrar su objetivo.
    """
    root = media_root().resolve()
    # Paso 4a: el propio target no puede ser un symlink (no seguir el enlace).
    if target.is_symlink():
        raise UnsafeRemoveError(
            f"El destino {target} es un enlace simbólico; no se sigue para borrar."
        )
    resolved = target.resolve()
    # Paso 2: contención (cubre symlinks internos que apunten fuera tras resolver).
    if not resolved.is_relative_to(root):
        raise UnsafeRemoveError(
            f"El destino {resolved} está fuera de media_root ({root}); no se borra."
        )
    # Paso 3: no borrar la raíz.
    if resolved == root:
        raise UnsafeRemoveError("No se permite borrar la raíz de media_root.")
    return resolved


def safe_rmtree(target: Path) -> bool:
    """Borra ``target`` de forma segura si supera ``assert_safe_project_target``.

    Reutilizada por el borrado de proyectos (DELETE) y por la limpieza del workspace
    parcial de una duplicación fallida. Si el directorio no existe, no hace nada y devuelve
    ``False``. Lanza ``UnsafeRemoveError`` si el destino no es seguro, y propaga errores de
    disco de ``shutil.rmtree`` (``ignore_errors=False``) para no enmascarar fallos.
    """
    resolved = assert_safe_project_target(target)
    if not resolved.exists():
        return False
    shutil.rmtree(resolved, ignore_errors=False)
    return True

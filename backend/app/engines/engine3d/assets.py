"""Catálogo acotado de assets 3D del MVP (personaje, animaciones, sets) y resolución de rutas.

Capa PURA (salvo ``os.path.exists``): no importa Blender. Mantiene el catálogo cerrado y
verificable descrito en ``docs/ENGINE-3D-MVP-STYLIZED.md`` §3:

  - **Personaje:** un único personaje riggeado (identidad constante entre escenas).
  - **Animaciones** (nombres estables usados por ``mapping.py``):
    ``idle`` | ``walk`` | ``turn_head`` | ``point`` | ``react``.
  - **Sets:** ``room_interior`` | ``outdoor_street``.

Las rutas se resuelven bajo ``AIV_BLENDER_ASSETS_DIR`` (o ``media/assets3d`` si está
vacío). Los assets de terceros (Mixamo/CC0) NO se versionan en el repo: el usuario los
puebla en esa carpeta; aquí solo se referencian y se detecta si faltan para dar mensajes
accionables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings

__all__ = [
    "CHARACTER_ID",
    "ANIMATIONS",
    "SETS",
    "DEFAULT_SET",
    "ResolvedAsset",
    "ResolvedAssets",
    "MissingAsset",
    "assets_dir",
    "character_path",
    "animation_path",
    "set_path",
    "resolve_assets",
    "missing_assets",
]

# --- Catálogo estable del MVP (ver docs/ENGINE-3D-MVP-STYLIZED.md §3) ---------------- #

#: Id del único personaje riggeado del MVP (identidad constante entre escenas).
CHARACTER_ID = "hero"

#: Nombres estables de animación que ``mapping.py`` puede elegir.
ANIMATIONS: tuple[str, ...] = ("idle", "walk", "turn_head", "point", "react")

#: Sets/escenarios CC0 disponibles.
SETS: tuple[str, ...] = ("room_interior", "outdoor_street")

#: Set por defecto cuando el guion no sugiere exterior.
DEFAULT_SET = "room_interior"

# Nombres de fichero relativos dentro de AIV_BLENDER_ASSETS_DIR. No se versionan; el
# usuario los puebla (Mixamo/CC0). El catálogo solo referencia estas rutas.
_CHARACTERS_SUBDIR = "characters"
_ANIMATIONS_SUBDIR = "animations"
_SETS_SUBDIR = "sets"
_CHARACTER_EXT = ".blend"
_ANIMATION_EXT = ".fbx"
_SET_EXT = ".blend"


@dataclass(frozen=True)
class MissingAsset:
    """Un asset del catálogo cuya ruta no existe en disco (para mensajes accionables)."""

    kind: str  # "character" | "animation" | "set"
    name: str
    path: str

    def message(self) -> str:
        return (
            f"Falta el asset 3D {self.kind} '{self.name}': no existe '{self.path}'. "
            "Descárgalo (Mixamo/CC0) a AIV_BLENDER_ASSETS_DIR; ver media/assets3d/README.md."
        )


@dataclass(frozen=True)
class ResolvedAsset:
    """Un asset del catálogo con su ruta resuelta y si existe en disco."""

    kind: str
    name: str
    path: str
    exists: bool


@dataclass(frozen=True)
class ResolvedAssets:
    """Conjunto de rutas resueltas para una escena (personaje + animación + set)."""

    character: ResolvedAsset
    animation: ResolvedAsset
    set: ResolvedAsset
    missing: tuple[MissingAsset, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """``True`` si todos los assets necesarios existen en disco."""
        return not self.missing


# --- Resolución de rutas (pura salvo os.path.exists) --------------------------------- #


def assets_dir(settings: Settings | None = None) -> Path:
    """Carpeta raíz de assets 3D: ``AIV_BLENDER_ASSETS_DIR`` o ``media/assets3d`` si vacío."""
    settings = settings or get_settings()
    if settings.blender_assets_dir:
        return Path(settings.blender_assets_dir).expanduser()
    return Path(settings.media_root).expanduser() / "assets3d"


def character_path(character_id: str, *, settings: Settings | None = None) -> Path:
    """Ruta esperada del fichero del personaje ``character_id``."""
    return assets_dir(settings) / _CHARACTERS_SUBDIR / f"{character_id}{_CHARACTER_EXT}"


def animation_path(animation: str, *, settings: Settings | None = None) -> Path:
    """Ruta esperada del clip de animación ``animation``."""
    return assets_dir(settings) / _ANIMATIONS_SUBDIR / f"{animation}{_ANIMATION_EXT}"


def set_path(set_id: str, *, settings: Settings | None = None) -> Path:
    """Ruta esperada del fichero del set ``set_id``."""
    return assets_dir(settings) / _SETS_SUBDIR / f"{set_id}{_SET_EXT}"


def _resolve_one(kind: str, name: str, path: Path) -> ResolvedAsset:
    return ResolvedAsset(kind=kind, name=name, path=str(path), exists=os.path.exists(path))


def resolve_assets(
    character_id: str,
    animation: str,
    set_id: str,
    *,
    settings: Settings | None = None,
) -> ResolvedAssets:
    """Resuelve las rutas de personaje/animación/set y detecta los que falten.

    Valida que los nombres pertenezcan al catálogo acotado del MVP (``ValueError`` si no).
    No lanza por asset ausente: lo reporta en ``missing`` para que la capa superior genere
    un error accionable (requisito §9 del prompt maestro).
    """
    settings = settings or get_settings()
    if animation not in ANIMATIONS:
        raise ValueError(
            f"Animación '{animation}' fuera del catálogo del MVP {ANIMATIONS}."
        )
    if set_id not in SETS:
        raise ValueError(f"Set '{set_id}' fuera del catálogo del MVP {SETS}.")

    character = _resolve_one(
        "character", character_id, character_path(character_id, settings=settings)
    )
    anim = _resolve_one(
        "animation", animation, animation_path(animation, settings=settings)
    )
    scene_set = _resolve_one("set", set_id, set_path(set_id, settings=settings))

    missing = tuple(
        MissingAsset(kind=a.kind, name=a.name, path=a.path)
        for a in (character, anim, scene_set)
        if not a.exists
    )
    return ResolvedAssets(
        character=character, animation=anim, set=scene_set, missing=missing
    )


def missing_assets(resolved: ResolvedAssets) -> list[MissingAsset]:
    """Helper: lista de assets que faltan en una resolución (para mensajes accionables)."""
    return list(resolved.missing)

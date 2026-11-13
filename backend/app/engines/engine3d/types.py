"""Tipos e interfaz pública del motor 4 (3D/Blender).

Define las dataclasses deterministas que viajan por la capa pura del motor 3D
(``Scene3DSpec``, ``CameraSpec``, ``VideoClip``), el ``Protocol`` ``Engine3D`` y la
excepción ``Engine3DUnavailable`` (análoga a ``RenderUnavailable`` del motor 6,
``app/engines/render/engine.py``).

Las formas exactas están especificadas en ``docs/ENGINE-3D-ARCHITECTURE.md`` §2.1 y §5.
Este módulo es lógica PURA: no importa Blender ni ejecuta subprocesos; es seguro de
importar en cualquier entorno (incluido el sandbox sin Blender).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "CameraSpec",
    "Scene3DSpec",
    "VideoClip",
    "Engine3D",
    "Engine3DUnavailable",
]


class Engine3DUnavailable(RuntimeError):
    """El render 3D de Blender no se puede ejecutar aquí (p. ej. sin binario de Blender).

    Análoga a ``RenderUnavailable`` del motor 6: la capa de ejecución la lanza en vez de
    simular un clip, y la capa superior (job/test) la propaga como "render 3D no
    disponible" en vez de inventar un vídeo.
    """


@dataclass(frozen=True)
class CameraSpec:
    """Dirección de cámara de una escena (plano + movimiento + óptica), ver §5.

    Valores canónicos del MVP:
      - ``shot``: ``closeup`` | ``medium`` | ``wide`` | ``aerial`` | ``low_angle`` |
        ``over_shoulder``.
      - ``move``: ``static`` | ``zoom_in`` | ``zoom_out`` | ``dolly`` | ``orbit`` | ``pan``.
    """

    shot: str = "medium"
    move: str = "static"
    focal_mm: float = 50.0
    dof: bool = True


@dataclass(frozen=True)
class Scene3DSpec:
    """Parámetros deterministas de UNA escena 3D (derivados de ``Scene`` + guion 3D).

    Es el contrato de entrada de la capa pura: ``script_builder.build_blender_script``
    produce, a partir de este spec (más los assets resueltos), el texto del script ``.py``
    que Blender ejecutará en headless.
    """

    scene_id: int
    order: int
    duration_s: float
    character_id: str
    character_animation: str
    set_id: str
    camera: CameraSpec
    style_preset: str = "stylized"
    width: int = 1080
    height: int = 1920
    fps: int = 30
    render_engine: str = "eevee"
    seed: int | None = None


@dataclass(frozen=True)
class VideoClip:
    """Clip de vídeo de una escena producido por Blender (lo consume el timeline)."""

    path: str
    scene_id: int
    duration_s: float
    width: int
    height: int
    fps: int
    provider: str
    meta: dict = field(default_factory=dict)


@runtime_checkable
class Engine3D(Protocol):
    """Interfaz "escena 3D -> clip de vídeo" (ver ``docs/ENGINE-3D-ARCHITECTURE.md`` §2.1).

    La implementación real (Blender headless) llega en FEAT-303 (``runner.py``). Cuando
    Blender no está disponible, ``available()`` devuelve ``False`` y ``render_sequence``
    lanza ``Engine3DUnavailable`` (nunca simula un clip).
    """

    def render_sequence(self, spec: Scene3DSpec, out_dir: str) -> VideoClip:
        """Renderiza la escena ``spec`` a un clip de vídeo en ``out_dir``."""
        ...

    def available(self) -> bool:
        """¿Hay un binario de Blender utilizable en este entorno?"""
        ...

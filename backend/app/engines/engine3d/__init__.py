"""Motor 4 — 3D/Blender (Fase 3).

Proveedor de un clip de vídeo por escena que corre Blender 4.x en modo headless (EEVEE),
con un personaje 3D riggeado de identidad CONSTANTE entre escenas. Arquitectura completa
en ``docs/ENGINE-3D-ARCHITECTURE.md`` y alcance del MVP estilizado en
``docs/ENGINE-3D-MVP-STYLIZED.md``.

El motor se parte en dos capas con una frontera dura (ver §2.2 de la arquitectura):

  * **Capa pura (sin Blender, 100% testeable en el sandbox)**:
      - :mod:`~app.engines.engine3d.types`: dataclasses ``Scene3DSpec``, ``CameraSpec``,
        ``VideoClip``, el ``Protocol`` ``Engine3D`` y la excepción ``Engine3DUnavailable``.
      - :mod:`~app.engines.engine3d.assets`: catálogo acotado + resolución de rutas.
      - :mod:`~app.engines.engine3d.mapping`: ``scene_to_spec`` (guion -> escena 3D).
      - :mod:`~app.engines.engine3d.script_builder`: ``build_blender_script`` (texto del
        script ``.py`` de Blender, determinista).
      - :mod:`~app.engines.engine3d.output_parser`: parsing de stdout/stderr -> ``VideoClip``.

  * **Capa de ejecución (requiere Blender real, SE SALTA en el sandbox)**:
      - :class:`~app.engines.engine3d.runner.BlenderEngine3D` (FEAT-303): invoca
        ``blender -b -P <script> -- <out_dir>`` como subproceso con timeout.

Importar el paquete NUNCA falla sin Blender: la indisponibilidad se gestiona en tiempo de
ejecución vía ``BlenderEngine3D.available()`` (``False`` sin binario) y
``Engine3DUnavailable`` (lanzada por ``render_sequence``). Nunca se simula un clip
(requisito §9 del prompt maestro).
"""

from __future__ import annotations

from app.engines.engine3d.runner import BlenderEngine3D
from app.engines.engine3d.types import (
    CameraSpec,
    Engine3D,
    Engine3DUnavailable,
    Scene3DSpec,
    VideoClip,
)

__all__ = [
    "BlenderEngine3D",
    "CameraSpec",
    "Engine3D",
    "Engine3DUnavailable",
    "Scene3DSpec",
    "VideoClip",
]

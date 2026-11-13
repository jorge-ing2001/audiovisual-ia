"""``BlenderClipProvider`` — adaptador del motor 3D a la capacidad 'clip'.

Envuelve :class:`~app.engines.engine3d.runner.BlenderEngine3D` para cumplir el
``ClipProvider`` de ``providers.base`` (``clip_for(scene_spec, out_dir) -> VideoClip``).
Es el default local/gratuito de la capacidad 'clip' (``AIV_CLIP_BACKEND=blender``). Si
Blender no está disponible, ``clip_for`` propaga ``Engine3DUnavailable`` (nunca simula un
clip; §9 del prompt maestro).
"""

from __future__ import annotations

from app.engines.engine3d.runner import BlenderEngine3D
from app.engines.engine3d.types import Scene3DSpec, VideoClip


class BlenderClipProvider:
    """Proveedor 'clip' que renderiza con Blender headless (motor 4)."""

    name = "blender"

    def __init__(self, *, engine: BlenderEngine3D | None = None) -> None:
        self._engine = engine or BlenderEngine3D()

    def available(self) -> bool:
        """¿Hay un binario de Blender utilizable? (delega en el motor)."""
        return self._engine.available()

    def clip_for(self, scene_spec: Scene3DSpec, out_dir: str) -> VideoClip:
        """Renderiza la escena ``scene_spec`` a un clip en ``out_dir`` con Blender."""
        return self._engine.render_sequence(scene_spec, out_dir)

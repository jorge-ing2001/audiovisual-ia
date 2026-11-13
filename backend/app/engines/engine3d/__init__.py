"""Motor 4 — 3D/Blender (Fase 3).

Proveedor de un clip de vídeo por escena que corre Blender 4.x en modo headless (EEVEE),
con un personaje 3D riggeado de identidad CONSTANTE entre escenas. Arquitectura completa
en ``docs/ENGINE-3D-ARCHITECTURE.md`` y alcance del MVP estilizado en
``docs/ENGINE-3D-MVP-STYLIZED.md``.

El motor se parte en dos capas con una frontera dura (ver §2.2 de la arquitectura):

  * **Capa pura (sin Blender, 100% testeable en el sandbox)** — ya implementada aquí:
      - :mod:`~app.engines.engine3d.types`: dataclasses ``Scene3DSpec``, ``CameraSpec``,
        ``VideoClip``, el ``Protocol`` ``Engine3D`` y la excepción ``Engine3DUnavailable``.
      - :mod:`~app.engines.engine3d.assets`: catálogo acotado + resolución de rutas.
      - :mod:`~app.engines.engine3d.mapping`: ``scene_to_spec`` (guion -> escena 3D).
      - :mod:`~app.engines.engine3d.script_builder`: ``build_blender_script`` (texto del
        script ``.py`` de Blender, determinista).
      - :mod:`~app.engines.engine3d.output_parser`: parsing de stdout/stderr -> ``VideoClip``.

  * **Capa de ejecución (requiere Blender real, SE SALTA en el sandbox)** — llega en
    FEAT-303 (``runner.py``). Mientras tanto, el import del paquete NO falla sin Blender;
    solo la ejecución real está sin implementar: :func:`render_sequence` lanza
    ``NotImplementedError`` hasta FEAT-303. Nunca se simula un clip (requisito §9 del
    prompt maestro).
"""

from __future__ import annotations

from app.engines.engine3d.types import (
    CameraSpec,
    Engine3D,
    Engine3DUnavailable,
    Scene3DSpec,
    VideoClip,
)

__all__ = [
    "CameraSpec",
    "Engine3D",
    "Engine3DUnavailable",
    "Scene3DSpec",
    "VideoClip",
    "render_sequence",
    "available",
]


def available() -> bool:
    """¿Hay un binario de Blender utilizable? La detección real llega en FEAT-303.

    Hasta que exista el ``runner``, el motor se considera NO disponible (no hay forma de
    renderizar sin la capa de ejecución), de modo que la capa superior/tests salten el
    render real con razón en vez de simular un clip.
    """
    return False


def render_sequence(spec: Scene3DSpec, out_dir: str) -> VideoClip:
    """Render real de la escena (Blender headless). Reservado a FEAT-303.

    La lógica pura (``script_builder``/``mapping``/``output_parser``/``assets``) ya está
    disponible; la ejecución del subproceso de Blender se implementa en ``runner.py``
    (FEAT-303). No se simula un clip.
    """
    raise NotImplementedError(
        "La ejecución real del motor 3D (Blender headless) llega en FEAT-303 (runner.py). "
        "La lógica pura ya está disponible en app.engines.engine3d.{types,assets,mapping,"
        "script_builder,output_parser}."
    )

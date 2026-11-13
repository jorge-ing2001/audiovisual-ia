"""Stubs conmutables de los proveedores 'clip' de PAGO: Veo / Runway / Kling.

Puntos de extensión declarados para PRESERVAR la vía de vídeo IA de pago (Fase 4), igual
que ``ElevenLabsTTS``/``OpenAITTS`` conviven con los defaults locales de TTS (ver
docs/ENGINE-3D-ARCHITECTURE.md §3.2 y §10). Construir cualquiera de estos stubs lanza
``NotImplementedError``: la integración real llega en una feature posterior. El default
local/gratuito de la capacidad 'clip' es ``blender``. No se simula ningún clip.
"""

from __future__ import annotations


class _PaidClipStub:
    """Base de los stubs de pago: construirlos lanza ``NotImplementedError``."""

    name = "paid"
    _service = "proveedor de pago"

    def __init__(self) -> None:
        raise NotImplementedError(
            f"El proveedor de clip de pago '{self.name}' ({self._service}) se implementa "
            "en una feature posterior (Fase 4). El default local/gratuito de la capacidad "
            "'clip' es 'blender' (AIV_CLIP_BACKEND=blender). No se simula ningún clip."
        )

    def clip_for(self, scene_spec, out_dir: str):  # pragma: no cover - nunca se construye
        raise NotImplementedError


class VeoClipProvider(_PaidClipStub):
    """Stub del ``ClipProvider`` de Google Veo (vía de pago futura)."""

    name = "veo"
    _service = "Google Veo"


class RunwayClipProvider(_PaidClipStub):
    """Stub del ``ClipProvider`` de Runway (vía de pago futura)."""

    name = "runway"
    _service = "Runway"


class KlingClipProvider(_PaidClipStub):
    """Stub del ``ClipProvider`` de Kling (vía de pago futura)."""

    name = "kling"
    _service = "Kling"

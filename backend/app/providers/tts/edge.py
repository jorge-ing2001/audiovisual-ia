"""``EdgeTTS`` — proveedor TTS online de respaldo (reutiliza lemo-opuscar, MIT).

Invoca ``core/tts/tts_zh.py`` de lemo-opuscar (Microsoft edge-tts, voces neuronales)
vía subproceso, con el mismo contrato de salida que Kokoro (``<id>.wav`` + ``dur.json``).
Es la degradación de ``kokoro`` cuando el modelo offline no está disponible (§3) y
también sirve de default para idiomas que Kokoro no cubre bien. Necesita red; si no
hay conexión, ``synthesize`` lanza ``LemoNotAvailable`` en vez de simular audio.
"""

from __future__ import annotations

from app.config import get_settings
from app.providers.base import AudioAsset, VoiceLine
from app.providers.tts._lemo import run_tts_script


class EdgeTTS:
    """Implementa ``TTSProvider`` con edge-tts online (código MIT de lemo-opuscar)."""

    name = "edge"

    def __init__(self, *, default_voice: str | None = None) -> None:
        settings = get_settings()
        # tts_zh.py usa voces de Microsoft (p. ej. es-ES-ElviraNeural); si el proyecto
        # no fija una, el script aplica su propio default.
        self.default_voice = default_voice or (settings.edge_voice or None)

    def synthesize(self, lines: list[VoiceLine], out_dir: str) -> list[AudioAsset]:
        return run_tts_script(
            "core/tts/tts_zh.py",
            lines,
            out_dir,
            provider_name=self.name,
            default_voice=self.default_voice,
            use_rate=True,
        )

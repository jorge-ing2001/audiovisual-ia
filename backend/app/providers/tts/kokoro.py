"""``KokoroTTS`` — proveedor TTS offline por defecto (reutiliza lemo-opuscar, MIT).

Invoca ``core/tts/tts.py`` de lemo-opuscar (Kokoro ONNX offline) vía subproceso y
recoge ``<id>.wav`` + ``dur.json``. Es el default local/gratuito de la capacidad
``tts``. Si el modelo offline no está disponible (o espeak-ng/kokoro-onnx faltan),
``synthesize`` lanza ``LemoNotAvailable`` para que el motor de voz degrade a edge-tts
(``kokoro->edge``, §3) en vez de simular audio.

Kokoro necesita escribir una copia de los datos de espeak-ng en ``~/.cache`` cuando
la ruta del paquete es demasiado larga; en entornos con ``$HOME`` de solo lectura se
puede redirigir ``HOME`` vía ``AIV_TTS_CACHE_HOME`` (ver config).
"""

from __future__ import annotations

from app.config import get_settings
from app.providers.base import AudioAsset, VoiceLine
from app.providers.tts._lemo import run_tts_script


class KokoroTTS:
    """Implementa ``TTSProvider`` con Kokoro offline (código MIT de lemo-opuscar)."""

    name = "kokoro"

    def __init__(self, *, default_voice: str | None = None) -> None:
        settings = get_settings()
        self.default_voice = default_voice or (settings.tts_voice or None)
        self._cache_home = settings.tts_cache_home or ""

    def synthesize(self, lines: list[VoiceLine], out_dir: str) -> list[AudioAsset]:
        env = {"HOME": self._cache_home} if self._cache_home else None
        return run_tts_script(
            "core/tts/tts.py",
            lines,
            out_dir,
            provider_name=self.name,
            default_voice=self.default_voice,
            use_rate=False,
            env=env,
        )

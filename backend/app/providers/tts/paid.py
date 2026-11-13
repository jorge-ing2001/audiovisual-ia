"""Stubs pluggables de TTS de pago (opcionales): ElevenLabs y OpenAI.

Se registran como puntos de extensión conmutables (igual que ``OpenAILLM``): sin la
clave correspondiente, construirlos falla con un mensaje accionable; con clave, la
síntesis real se deja para una feature posterior (Fase 4) y lanza
``NotImplementedError`` en vez de simular audio. El default local/gratuito es
``kokoro`` (con degradación a ``edge``).
"""

from __future__ import annotations

from app.config import get_settings
from app.providers.base import AudioAsset, VoiceLine


class ElevenLabsTTS:
    """Stub del ``TTSProvider`` de ElevenLabs (requiere ``ELEVENLABS_API_KEY``)."""

    name = "elevenlabs"

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key or get_settings().elevenlabs_api_key
        if not self.api_key:
            raise RuntimeError(
                "ElevenLabsTTS requiere ELEVENLABS_API_KEY. Configúrala en tu .env o "
                "usa el backend por defecto 'kokoro' (AIV_TTS_BACKEND=kokoro)."
            )

    def synthesize(self, lines: list[VoiceLine], out_dir: str) -> list[AudioAsset]:
        raise NotImplementedError(
            "La integración de ElevenLabs se implementa en una feature posterior. "
            "El backend TTS por defecto local/gratuito es 'kokoro'."
        )


class OpenAITTS:
    """Stub del ``TTSProvider`` de OpenAI (requiere ``OPENAI_API_KEY``)."""

    name = "openai"

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key or get_settings().openai_api_key
        if not self.api_key:
            raise RuntimeError(
                "OpenAITTS requiere OPENAI_API_KEY. Configúrala en tu .env o usa el "
                "backend por defecto 'kokoro' (AIV_TTS_BACKEND=kokoro)."
            )

    def synthesize(self, lines: list[VoiceLine], out_dir: str) -> list[AudioAsset]:
        raise NotImplementedError(
            "La integración de OpenAI TTS se implementa en una feature posterior. "
            "El backend TTS por defecto local/gratuito es 'kokoro'."
        )

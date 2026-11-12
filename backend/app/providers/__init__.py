"""Capa de proveedores conmutables (base + registry + implementaciones).

Cada capacidad (LLM, imágenes, TTS, ASR) es una interfaz (Protocol) resuelta por
un registry según ``AIV_*_BACKEND``, con defaults locales/gratuitos y degradación
(stock->placeholder, kokoro->edge). FEAT-002 define las interfaces (``base``), el
registry (``registry``) y stubs FAKE de test (``fakes``); los proveedores reales
llegan en FEAT-003/004.
"""

from __future__ import annotations

from app.providers.base import (
    ASRProvider,
    AudioAsset,
    ImageAsset,
    ImageProvider,
    LLMProvider,
    TTSProvider,
    VoiceLine,
    WordTiming,
)

__all__ = [
    "LLMProvider",
    "ImageProvider",
    "TTSProvider",
    "ASRProvider",
    "ImageAsset",
    "AudioAsset",
    "VoiceLine",
    "WordTiming",
]

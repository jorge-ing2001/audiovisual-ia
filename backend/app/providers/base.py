"""Interfaces de la capa de proveedores (ver docs/ARCHITECTURE.md §3).

Este módulo define SOLO contratos: los ``Protocol`` de cada capacidad de IA y las
dataclasses de artefactos que esos proveedores devuelven. Las implementaciones
reales (OllamaLLM, StockImageProvider, KokoroTTS, FasterWhisper, ...) llegan en
FEAT-003/004 y viven en sus propios módulos; aquí no hay lógica de proveedor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Dataclasses de artefactos
# --------------------------------------------------------------------------- #


@dataclass
class ImageAsset:
    """Imagen producida por un ``ImageProvider`` para una escena."""

    path: str
    width: int
    height: int
    provider: str
    query: str | None = None
    seed: int | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class AudioAsset:
    """Clip de audio (narración, música o sfx) producido por la capa de voz."""

    path: str
    duration_s: float
    provider: str
    sample_rate: int | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class VoiceLine:
    """Una línea de narración a sintetizar por un ``TTSProvider``."""

    text: str
    voice: str | None = None
    language: str = "es"
    speed: float = 1.0
    meta: dict = field(default_factory=dict)


@dataclass
class WordTiming:
    """Tiempo de una palabra transcrita por un ``ASRProvider`` (para subtítulos)."""

    word: str
    start_s: float
    end_s: float


# --------------------------------------------------------------------------- #
# Protocols por capacidad
# --------------------------------------------------------------------------- #


@runtime_checkable
class LLMProvider(Protocol):
    """Genera texto (guion, división en escenas, storyboard)."""

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
        **opts: object,
    ) -> str: ...


@runtime_checkable
class ImageProvider(Protocol):
    """Obtiene o genera una imagen por escena."""

    def image_for(
        self,
        query: str,
        *,
        aspect: str,
        out_dir: str,
        seed: int | None = None,
    ) -> ImageAsset: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Sintetiza voz a partir de líneas de narración."""

    def synthesize(self, lines: list[VoiceLine], out_dir: str) -> list[AudioAsset]: ...


@runtime_checkable
class ASRProvider(Protocol):
    """Transcribe audio a tiempos de palabra (alineación para subtítulos)."""

    def transcribe(self, audio_path: str) -> list[WordTiming]: ...

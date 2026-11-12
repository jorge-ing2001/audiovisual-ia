"""Motor 5 — Voz/Audio (Fase 1).

TTS multivoz, música de fondo, SFX, mezcla a -14 LUFS y subtítulos (ASR).
Interfaces: ``VoiceEngine.synthesize(lines) -> list[AudioAsset]`` / ``synthesize_project``
/ ``generate_subtitles`` y ``AudioMixer.mix(...)``.
Reutiliza código MIT de lemo-opuscar (ver ``NOTICE``). Implementado en FEAT-004.
"""

from __future__ import annotations

from app.engines.voice.engine import VoiceEngine, VoiceResult
from app.engines.voice.mixer import AudioMixer, MixResult, MixTrack
from app.engines.voice.subtitles import Cue, cues_from_word_timings, write_srt

__all__ = [
    "VoiceEngine",
    "VoiceResult",
    "AudioMixer",
    "MixResult",
    "MixTrack",
    "Cue",
    "cues_from_word_timings",
    "write_srt",
]

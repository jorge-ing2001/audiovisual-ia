"""Motor 5 — Voz/Audio (Fase 1).

TTS multivoz, música de fondo, SFX, mezcla y masterización a -14 LUFS, recorte de
silencios y subtítulos (ASR, por línea y palabra-por-palabra).
Interfaces: ``VoiceEngine.synthesize(lines) -> list[AudioAsset]`` / ``synthesize_project``
/ ``generate_subtitles``, ``AudioMixer.mix(...)``, ``VoiceMaster.master_file(...)``,
``SilenceTrimmer.trim(...)`` y ``RecordedVoiceEngine.process(...)``.

Reutiliza código MIT de lemo-opuscar y ADAPTA (reimplementación propia) técnicas MIT de
JustCarlos30/skills-videos-cortos (ver ``NOTICE``). Implementado en FEAT-004.
"""

from __future__ import annotations

from app.engines.voice.engine import VoiceEngine, VoiceResult
from app.engines.voice.master import (
    CleanupOptions,
    MasterResult,
    VoiceMaster,
)
from app.engines.voice.mixer import AudioMixer, MixResult, MixTrack
from app.engines.voice.recorded import RecordedVoiceEngine, RecordedVoiceResult
from app.engines.voice.subtitles import (
    Cue,
    apply_corrections,
    cues_from_text,
    cues_from_word_timings,
    cues_per_word,
    words_payload,
    write_srt,
    write_words_json,
)
from app.engines.voice.trim import Segment, SilenceTrimmer, TrimResult

__all__ = [
    "VoiceEngine",
    "VoiceResult",
    "AudioMixer",
    "MixResult",
    "MixTrack",
    "VoiceMaster",
    "MasterResult",
    "CleanupOptions",
    "SilenceTrimmer",
    "Segment",
    "TrimResult",
    "RecordedVoiceEngine",
    "RecordedVoiceResult",
    "Cue",
    "cues_from_text",
    "cues_from_word_timings",
    "cues_per_word",
    "apply_corrections",
    "words_payload",
    "write_srt",
    "write_words_json",
]

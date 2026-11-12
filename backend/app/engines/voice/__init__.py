"""Motor 5 — Voz/Audio (Fase 1).

TTS multivoz, música de fondo, SFX, mezcla a -14 LUFS y subtítulos (ASR).
Interfaces borrador: ``VoiceEngine.synthesize(lines) -> list[AudioAsset]`` y ``AudioMixer.mix(...)``.
Reutiliza código MIT de lemo-opuscar. Se implementa en FEAT-004.
"""

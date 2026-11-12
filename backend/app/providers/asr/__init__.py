"""Proveedores ASR (alineación de palabras para subtítulos, motor 5).

El default ``faster_whisper`` reutiliza el enfoque de ``core/tts/asr_check.py`` de
lemo-opuscar (código MIT): transcribe cada WAV con faster-whisper y devuelve tiempos
por palabra (``WordTiming``) a partir de los cuales el motor de voz genera los cues y
el ``.srt``.
"""

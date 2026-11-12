"""Recorte de silencios con re-mapeo de timings (FEAT-004, técnica de voz.py MIT).

Dos niveles:
  - Lógica pura (sin ffmpeg): construcción de tramos de habla y re-mapeo de WordTiming.
  - Integración real con ffmpeg sobre un WAV sintético con un silencio en medio: la
    duración se reduce y los timings se re-mapean sin solapes.

Si ffmpeg/numpy/soundfile no están, solo se salta la parte de integración.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import get_settings
from app.engines.voice.trim import SilenceTrimmer, parse_silencedetect
from app.providers.base import WordTiming

# --- lógica pura -------------------------------------------------------------- #


def test_parse_silencedetect_pairs():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 1.0\n"
        "[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.5\n"
    )
    assert parse_silencedetect(stderr) == [(1.0, 2.5)]


def test_speech_segments_complement_of_silence():
    # Audio de 5 s con un silencio [2.0, 3.0]; habla = [0,2] y [3,5].
    segments = SilenceTrimmer.speech_segments([(2.0, 3.0)], duration=5.0, pad=0.0)
    assert len(segments) == 2
    assert segments[0].start == 0.0 and segments[0].end == 2.0
    assert segments[1].start == 3.0 and segments[1].end == 5.0


def test_remap_timings_shifts_after_gap_without_overlap():
    # Habla en [0,2] y [3,5] (se elimina el silencio [2,3], 1 s).
    segments = SilenceTrimmer.speech_segments([(2.0, 3.0)], duration=5.0, pad=0.0)
    timings = [
        WordTiming("uno", 0.2, 0.8),      # tramo 1 -> sin cambio
        WordTiming("dos", 1.2, 1.8),      # tramo 1 -> sin cambio
        WordTiming("silencio", 2.3, 2.7),  # dentro del silencio -> se descarta
        WordTiming("tres", 3.2, 3.8),     # tramo 2 -> -1 s
        WordTiming("cuatro", 4.2, 4.8),   # tramo 2 -> -1 s
    ]
    remapped = SilenceTrimmer.remap_timings(timings, segments)
    words = [w.word for w in remapped]
    assert words == ["uno", "dos", "tres", "cuatro"]  # 'silencio' descartada
    # 'tres' se desplaza 1 s (silencio eliminado).
    tres = next(w for w in remapped if w.word == "tres")
    assert abs(tres.start_s - 2.2) < 1e-6
    # Sin solapes: cada palabra empieza >= fin de la anterior.
    for a, b in zip(remapped, remapped[1:]):
        assert b.start_s >= a.start_s
        assert a.end_s <= b.start_s + 1e-6 or b.start_s >= a.start_s


# --- integración real con ffmpeg --------------------------------------------- #

np = pytest.importorskip("numpy", reason="numpy no instalado (tier de voz)")
sf = pytest.importorskip("soundfile", reason="soundfile no instalado (tier de voz)")


def _write_with_silence(path, *, sr=24000):
    """Tono 1 s + silencio 1 s + tono 1 s (3 s en total)."""
    def tone(freq, seconds, amp=0.3):
        t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
        return (amp * np.sin(2 * np.pi * freq * t)).astype("float32")

    silence = np.zeros(int(sr * 1.0), dtype="float32")
    sig = np.concatenate([tone(220.0, 1.0), silence, tone(330.0, 1.0)])
    sf.write(str(path), sig, sr)
    return str(path)


@pytest.fixture()
def trimmer(temp_env):
    if shutil.which(get_settings().ffmpeg_bin) is None:
        pytest.skip(
            f"ffmpeg no disponible en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN (sandbox: /projects/sandbox/.localbin/ffmpeg)."
        )
    return SilenceTrimmer()


def test_trim_reduces_duration_and_remaps(trimmer, tmp_path):
    src = _write_with_silence(tmp_path / "with_silence.wav")
    timings = [
        WordTiming("hola", 0.2, 0.8),    # primer tono
        WordTiming("adios", 2.2, 2.8),   # segundo tono (tras el silencio)
    ]
    result = trimmer.trim(
        src,
        str(tmp_path / "trimmed.wav"),
        timings,
        silence_db=-40.0,
        min_silence=0.3,
        pad=0.05,
    )
    assert Path(result.path).is_file()
    # El audio recortado dura menos que el original (se quitó ~1 s de silencio).
    assert result.trimmed_duration < result.original_duration
    # Ambas palabras sobreviven y 'adios' se ha desplazado hacia la izquierda.
    words = {w.word: w for w in result.timings}
    assert "hola" in words and "adios" in words
    assert words["adios"].start_s < 2.2
    # Sin solapes en el nuevo eje.
    ordered = sorted(result.timings, key=lambda w: w.start_s)
    for a, b in zip(ordered, ordered[1:]):
        assert b.start_s >= a.start_s

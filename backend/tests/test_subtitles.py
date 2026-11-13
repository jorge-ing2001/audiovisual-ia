"""Subtítulos: cues desde WordTiming y export .srt (FEAT-004).

Verifica el formato de timestamps (idéntico a core/render/srt.py de lemo-opuscar),
el redondeo a milisegundos antes de descomponer y el recorte de solapes adyacentes.
No requiere modelos ni red: es lógica pura.
"""

from __future__ import annotations

from pathlib import Path

from app.engines.voice.subtitles import Cue, cues_from_word_timings, write_srt
from app.providers.base import WordTiming


def test_fmt_rounds_to_milliseconds_before_split(tmp_path):
    # 1.9996 s debe redondear a 00:00:02,000 (sin perder el acarreo).
    srt = tmp_path / "out.srt"
    write_srt([Cue(t0=1.9996, t1=3.0, text="hola")], str(srt))
    content = srt.read_text(encoding="utf-8")
    assert "00:00:02,000 -->" in content


def test_cues_group_words_and_keep_times():
    timings = [
        WordTiming("Hola", 0.0, 0.4),
        WordTiming("mundo", 0.4, 0.8),
        WordTiming("bonito", 0.8, 1.2),
    ]
    cues = cues_from_word_timings(timings, max_words_per_cue=2)
    assert len(cues) == 2
    assert cues[0].text == "Hola mundo"
    assert cues[0].t0 == 0.0 and cues[0].t1 == 0.8
    assert cues[1].text == "bonito"
    assert cues[1].t0 == 0.8 and cues[1].t1 == 1.2


def test_write_srt_trims_overlaps_and_formats(tmp_path):
    cues = [
        Cue(t0=0.0, t1=2.0, text="primero"),
        Cue(t0=1.5, t1=3.0, text="segundo"),  # solapa con el anterior
    ]
    srt = tmp_path / "subs.srt"
    out = write_srt(cues, str(srt))
    assert Path(out).is_file()
    lines = srt.read_text(encoding="utf-8").splitlines()
    # Primer cue: su final se recorta al inicio del segundo (1.5 s).
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,500"
    assert lines[2] == "primero"
    # Segundo cue.
    assert "2" in lines
    assert "00:00:01,500 --> 00:00:03,000" in lines
    assert "segundo" in lines


def test_write_srt_sorts_by_start(tmp_path):
    cues = [
        Cue(t0=2.0, t1=3.0, text="dos"),
        Cue(t0=0.0, t1=1.0, text="uno"),
    ]
    srt = tmp_path / "s.srt"
    write_srt(cues, str(srt))
    content = srt.read_text(encoding="utf-8")
    assert content.index("uno") < content.index("dos")

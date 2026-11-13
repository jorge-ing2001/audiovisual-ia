"""Subtítulos palabra-por-palabra + palabras.json + diccionario de correcciones.

Técnica adaptada de voz.py (MIT): cues de una sola palabra, exportación de un
palabras.json ([{text,startMs,endMs}]) para el render, y reemplazo de tokens mal
transcritos repartiendo proporcionalmente los tiempos. Lógica pura, sin ffmpeg ni modelos.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.engines.voice.subtitles import (
    apply_corrections,
    cues_per_word,
    words_payload,
    write_words_json,
)
from app.providers.base import WordTiming


def test_cues_per_word_one_cue_per_word():
    timings = [
        WordTiming("Hola", 0.0, 0.4),
        WordTiming("mundo", 0.4, 0.9),
    ]
    cues = cues_per_word(timings)
    assert len(cues) == 2
    assert cues[0].text == "Hola" and cues[0].t0 == 0.0 and cues[0].t1 == 0.4
    assert cues[1].text == "mundo" and cues[1].t0 == 0.4 and cues[1].t1 == 0.9


def test_words_payload_and_json(tmp_path):
    timings = [
        WordTiming("Hola", 0.0, 0.4),
        WordTiming("  ", 0.4, 0.5),  # vacío -> se omite
        WordTiming("mundo", 0.5, 1.2345),
    ]
    payload = words_payload(timings)
    assert payload == [
        {"text": "Hola", "startMs": 0, "endMs": 400},
        {"text": "mundo", "startMs": 500, "endMs": 1234},
    ]
    out = tmp_path / "palabras.json"
    write_words_json(timings, str(out))
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data == payload


def test_corrections_single_word_keeps_times():
    timings = [WordTiming("Pyton", 1.0, 1.6)]
    out = apply_corrections(timings, {"pyton": "Python"})
    assert len(out) == 1
    assert out[0].word == "Python"
    assert out[0].start_s == 1.0 and out[0].end_s == 1.6


def test_corrections_multiword_splits_times_proportionally():
    # "osea" mal transcrito -> "o sea" (dos palabras), reparte [2.0, 3.0] en mitades.
    timings = [
        WordTiming("Esto", 0.0, 1.0),
        WordTiming("osea", 2.0, 3.0),
        WordTiming("bien", 3.0, 3.5),
    ]
    out = apply_corrections(timings, {"osea": "o sea"})
    words = [w.word for w in out]
    assert words == ["Esto", "o", "sea", "bien"]
    o_word = out[1]
    sea_word = out[2]
    assert o_word.start_s == 2.0
    assert abs(o_word.end_s - 2.5) < 1e-6
    assert abs(sea_word.start_s - 2.5) < 1e-6
    assert sea_word.end_s == 3.0  # fin preservado


def test_corrections_ignore_case_and_punctuation():
    timings = [WordTiming("Pyton,", 0.0, 0.5)]
    out = apply_corrections(timings, {"pyton": "Python"})
    assert out[0].word == "Python"

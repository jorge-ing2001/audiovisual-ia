"""Parsers/serializadores puros de subtítulos (SRT/VTT/ASS): round-trip + color + VTT."""

from __future__ import annotations

import pytest

from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
)
from app.engines.editor.subtitle_formats import (
    ass_color_to_hex,
    cues_to_subtitle_clips,
    dump_ass,
    dump_vtt,
    hex_to_ass_color,
    parse_ass,
    parse_srt,
    parse_vtt,
    subtitle_clips_to_cues,
)
from app.engines.voice.subtitles import Cue


def _cues() -> list[Cue]:
    return [
        Cue(t0=0.0, t1=1.5, text="Hola mundo"),
        Cue(t0=1.5, t1=3.25, text="Segunda línea"),
        Cue(t0=10.0, t1=12.333, text="Con acentos: ñáé"),
    ]


def _assert_round_trip(cues: list[Cue], parsed: list[Cue]) -> None:
    assert len(parsed) == len(cues)
    for original, got in zip(cues, parsed):
        # Tiempos preservados al milisegundo.
        assert round(got.t0, 3) == pytest.approx(round(original.t0, 3))
        assert round(got.t1, 3) == pytest.approx(round(original.t1, 3))
        assert got.text == original.text


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #


def test_srt_round_trip_preserves_times_and_text():
    cues = _cues()
    from app.api.editor import _dump_srt

    _assert_round_trip(cues, parse_srt(_dump_srt(cues)))


def test_vtt_round_trip_preserves_times_and_text():
    cues = _cues()
    dumped = dump_vtt(cues)
    assert dumped.startswith("WEBVTT")
    assert "." in dumped.split("\n")[2]  # ms con punto, no coma
    _assert_round_trip(cues, parse_vtt(dumped))


def test_ass_round_trip_preserves_times_and_text():
    cues = _cues()
    dumped = dump_ass(cues)
    assert "[Script Info]" in dumped
    assert "[V4+ Styles]" in dumped
    assert "[Events]" in dumped
    parsed = parse_ass(dumped)
    # ASS tiene precisión de centésimas: los tiempos se preservan a 2 decimales.
    assert len(parsed) == len(cues)
    for original, got in zip(cues, parsed):
        assert got.t0 == pytest.approx(round(original.t0, 2), abs=0.01)
        assert got.t1 == pytest.approx(round(original.t1, 2), abs=0.01)
        assert got.text == original.text


# --------------------------------------------------------------------------- #
# Color hex <-> ASS
# --------------------------------------------------------------------------- #


def test_hex_to_ass_color_inverts_to_bgr():
    assert hex_to_ass_color("#FF0000") == "&H000000FF"  # rojo -> BGR
    assert hex_to_ass_color("#00FF00") == "&H0000FF00"  # verde
    assert hex_to_ass_color("00FF00") == "&H0000FF00"  # tolera sin '#'
    assert hex_to_ass_color("#123456") == "&H00563412"


def test_ass_color_to_hex_round_trips():
    for hexv in ("#FF0000", "#00FF00", "#123456", "#FFFFFF"):
        assert ass_color_to_hex(hex_to_ass_color(hexv)) == hexv
    # Con alpha explícito también funciona.
    assert ass_color_to_hex("&H80563412") == "#123456"


def test_color_helpers_reject_invalid():
    with pytest.raises(ValueError):
        hex_to_ass_color("nope")
    with pytest.raises(ValueError):
        ass_color_to_hex("rojo")


# --------------------------------------------------------------------------- #
# VTT: tolera cabecera / NOTE / cues sin índice
# --------------------------------------------------------------------------- #


def test_parse_vtt_tolerates_header_note_and_cue_ids():
    text = (
        "WEBVTT - alguna descripción\n"
        "\n"
        "NOTE Esto es una nota que se ignora\n"
        "\n"
        "intro\n"  # identificador de cue (no es timing)
        "00:00:00.000 --> 00:00:01.500 align:start\n"
        "Primera\n"
        "\n"
        "00:00:01.500 --> 00:00:03.000\n"
        "Segunda sin índice\n"
    )
    cues = parse_vtt(text)
    assert [c.text for c in cues] == ["Primera", "Segunda sin índice"]
    assert cues[0].t0 == pytest.approx(0.0)
    assert cues[0].t1 == pytest.approx(1.5)
    assert cues[1].t1 == pytest.approx(3.0)


def test_parse_ass_ignores_inline_overrides():
    text = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,{\\pos(10,20)}Hola {\\b1}mundo\n"
    )
    cues = parse_ass(text)
    assert len(cues) == 1
    assert cues[0].text == "Hola mundo"


# --------------------------------------------------------------------------- #
# Adaptadores cues <-> clips
# --------------------------------------------------------------------------- #


def test_cues_to_clips_and_back_round_trips_through_project():
    cues = _cues()
    clips = cues_to_subtitle_clips(cues, "track_subtitle")
    assert all(c.kind == "subtitle" for c in clips)
    assert clips[0].start_s == pytest.approx(0.0)
    assert clips[0].duration_s == pytest.approx(1.5)

    project = EditProject(
        project_id=1,
        meta=EditMeta(),
        tracks=[EditTrack(id="track_subtitle", kind="subtitle", clips=clips)],
    )
    back = subtitle_clips_to_cues(project)
    _assert_round_trip(cues, back)


def test_subtitle_clips_to_cues_sorts_and_skips_disabled():
    clips = [
        EditClip(id="b", track_id="t", kind="subtitle", start_s=2.0, duration_s=1.0, text="dos"),
        EditClip(id="a", track_id="t", kind="subtitle", start_s=0.0, duration_s=1.0, text="uno"),
        EditClip(
            id="c", track_id="t", kind="subtitle", start_s=5.0, duration_s=1.0,
            text="oculto", enabled=False,
        ),
    ]
    project = EditProject(
        project_id=1,
        meta=EditMeta(),
        tracks=[EditTrack(id="t", kind="subtitle", clips=clips)],
    )
    cues = subtitle_clips_to_cues(project)
    assert [c.text for c in cues] == ["uno", "dos"]  # ordenado por start_s, sin el deshabilitado

"""Operaciones de edición PURAS (sin ffmpeg ni Chromium)."""

from __future__ import annotations

import pytest

from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    SubtitleStyle,
)
from app.engines.editor.operations import EditOperationError, apply_operation


def _project() -> EditProject:
    visual = EditTrack(
        id="track_image",
        kind="image",
        order=0,
        clips=[
            EditClip(
                id="v0",
                track_id="track_image",
                kind="image",
                start_s=0.0,
                duration_s=4.0,
                source_in_s=0.0,
                source_out_s=4.0,
                asset_id=1,
            )
        ],
    )
    voice = EditTrack(
        id="track_voice",
        kind="voice",
        order=1,
        clips=[
            EditClip(
                id="a0",
                track_id="track_voice",
                kind="voice",
                start_s=0.0,
                duration_s=4.0,
                source_in_s=0.0,
                source_out_s=4.0,
                asset_id=2,
                gain=1.0,
            )
        ],
    )
    voice2 = EditTrack(id="track_voice2", kind="voice", order=2)
    subs = EditTrack(
        id="track_subtitle",
        kind="subtitle",
        order=3,
        clips=[
            EditClip(
                id="s0",
                track_id="track_subtitle",
                kind="subtitle",
                start_s=0.0,
                duration_s=2.0,
                text="hola",
                style=SubtitleStyle(),
            )
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(), tracks=[visual, voice, voice2, subs])


def test_operations_are_pure():
    project = _project()
    before = project.model_dump(mode="json")
    apply_operation(project, "delete", {"clip_id": "v0"})
    # El proyecto original no se muta.
    assert project.model_dump(mode="json") == before


def test_trim_adjusts_source_and_duration():
    out = apply_operation(
        _project(), "trim", {"clip_id": "v0", "source_in_s": 1.0, "source_out_s": 3.0}
    )
    _, clip = out.find_clip("v0")
    assert clip.source_in_s == pytest.approx(1.0)
    assert clip.source_out_s == pytest.approx(3.0)
    assert clip.duration_s == pytest.approx(2.0)


def test_trim_rejects_inverted_window():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(), "trim", {"clip_id": "v0", "source_in_s": 3.0, "source_out_s": 1.0}
        )


def test_split_creates_two_clips():
    out = apply_operation(_project(), "split", {"clip_id": "v0", "t_s": 1.5})
    track = out.track_by_id("track_image")
    assert [c.id for c in track.clips] == ["v0", "v0_b"]
    left = track.clips[0]
    right = track.clips[1]
    assert left.duration_s == pytest.approx(1.5)
    assert right.start_s == pytest.approx(1.5)
    assert right.duration_s == pytest.approx(2.5)
    # El recorte del origen se reparte en el punto de corte.
    assert left.source_out_s == pytest.approx(1.5)
    assert right.source_in_s == pytest.approx(1.5)


def test_split_rejects_time_outside_clip():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "split", {"clip_id": "v0", "t_s": 10.0})


def test_delete_removes_clip():
    out = apply_operation(_project(), "delete", {"clip_id": "v0"})
    assert out.find_clip("v0") is None


def test_duplicate_appends_copy_after_original():
    out = apply_operation(_project(), "duplicate", {"clip_id": "v0"})
    track = out.track_by_id("track_image")
    assert [c.id for c in track.clips] == ["v0", "v0_copy"]
    assert track.clips[1].start_s == pytest.approx(4.0)


def test_move_repositions_clip_in_time():
    out = apply_operation(_project(), "move", {"clip_id": "s0", "start_s": 5.0})
    _, clip = out.find_clip("s0")
    assert clip.start_s == pytest.approx(5.0)


def test_move_to_other_track_same_kind():
    out = apply_operation(_project(), "move", {"clip_id": "a0", "track_id": "track_voice2"})
    assert not out.track_by_id("track_voice").clips
    moved = out.track_by_id("track_voice2").clips
    assert len(moved) == 1 and moved[0].track_id == "track_voice2"


def test_move_to_mismatched_track_kind_rejected():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "move", {"clip_id": "a0", "track_id": "track_image"})


def test_set_duration_respects_source_span():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_duration", {"clip_id": "v0", "duration_s": 99.0})
    out = apply_operation(_project(), "set_duration", {"clip_id": "v0", "duration_s": 2.0})
    _, clip = out.find_clip("v0")
    assert clip.duration_s == pytest.approx(2.0)
    assert clip.source_out_s == pytest.approx(2.0)


def test_set_duration_rejects_non_positive():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_duration", {"clip_id": "v0", "duration_s": 0.0})


def test_set_clip_gain_on_audio():
    out = apply_operation(_project(), "set_clip_gain", {"clip_id": "a0", "gain": 0.5})
    _, clip = out.find_clip("a0")
    assert clip.gain == pytest.approx(0.5)


def test_set_clip_gain_rejects_non_audio():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_clip_gain", {"clip_id": "v0", "gain": 0.5})


def test_set_clip_gain_rejects_negative():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_clip_gain", {"clip_id": "a0", "gain": -1.0})


def test_set_text_on_subtitle():
    out = apply_operation(_project(), "set_text", {"clip_id": "s0", "text": "nuevo"})
    _, clip = out.find_clip("s0")
    assert clip.text == "nuevo"


def test_set_text_rejects_non_subtitle():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_text", {"clip_id": "v0", "text": "x"})


def test_set_timing_updates_start_and_duration():
    out = apply_operation(
        _project(), "set_timing", {"clip_id": "s0", "start_s": 1.0, "duration_s": 3.0}
    )
    _, clip = out.find_clip("s0")
    assert clip.start_s == pytest.approx(1.0)
    assert clip.duration_s == pytest.approx(3.0)


def test_set_timing_rejects_bad_duration():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_timing", {"clip_id": "s0", "duration_s": 0.0})


def test_unknown_operation_rejected():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "frobnicate", {"clip_id": "v0"})


def test_missing_clip_rejected():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "delete", {"clip_id": "ghost"})

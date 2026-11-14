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


def test_set_track_volume_on_audio_track():
    out = apply_operation(
        _project(), "set_track_volume", {"track_id": "track_voice", "volume": 0.3}
    )
    assert out.track_by_id("track_voice").volume == pytest.approx(0.3)


def test_set_track_volume_rejects_non_audio_track():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(), "set_track_volume", {"track_id": "track_image", "volume": 0.3}
        )


def test_set_track_volume_rejects_negative():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(), "set_track_volume", {"track_id": "track_voice", "volume": -0.1}
        )


def test_set_fade_sets_in_and_out():
    out = apply_operation(
        _project(), "set_fade", {"clip_id": "a0", "fade_in_s": 0.5, "fade_out_s": 0.8}
    )
    _, clip = out.find_clip("a0")
    assert clip.fade_in_s == pytest.approx(0.5)
    assert clip.fade_out_s == pytest.approx(0.8)


def test_set_fade_rejects_longer_than_clip():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(), "set_fade", {"clip_id": "a0", "fade_in_s": 3.0, "fade_out_s": 3.0}
        )


def test_set_fade_rejects_non_audio():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_fade", {"clip_id": "v0", "fade_in_s": 0.1})


# --------------------------------------------------------------------------- #
# Visual: replace_asset / set_transform / set_transition
# --------------------------------------------------------------------------- #


def test_replace_asset_changes_asset_and_resets_source():
    out = apply_operation(_project(), "replace_asset", {"clip_id": "v0", "asset_id": 99})
    _, clip = out.find_clip("v0")
    assert clip.asset_id == 99
    assert clip.source_in_s == pytest.approx(0.0)
    assert clip.source_out_s is None
    # La duración en la línea de tiempo se conserva.
    assert clip.duration_s == pytest.approx(4.0)


def test_replace_asset_rejects_non_visual():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "replace_asset", {"clip_id": "a0", "asset_id": 5})


def test_replace_asset_requires_int_asset_id():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "replace_asset", {"clip_id": "v0", "asset_id": "x"})


def test_set_transform_updates_scale_position_fit():
    out = apply_operation(
        _project(),
        "set_transform",
        {"clip_id": "v0", "scale": 1.5, "x": 40.0, "y": -20.0, "fit": "contain"},
    )
    _, clip = out.find_clip("v0")
    assert clip.transform is not None
    assert clip.transform.scale == pytest.approx(1.5)
    assert clip.transform.x == pytest.approx(40.0)
    assert clip.transform.y == pytest.approx(-20.0)
    assert clip.transform.fit == "contain"


def test_set_transform_rejects_non_positive_scale():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_transform", {"clip_id": "v0", "scale": 0.0})


def test_set_transform_rejects_crop_out_of_range():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_transform", {"clip_id": "v0", "crop_l": 1.5})


def test_set_transition_fade_in():
    out = apply_operation(
        _project(),
        "set_transition",
        {"clip_id": "v0", "edge": "in", "type": "fade", "duration_s": 0.7},
    )
    _, clip = out.find_clip("v0")
    assert clip.transition_in is not None
    assert clip.transition_in.type == "fade"
    assert clip.transition_in.duration_s == pytest.approx(0.7)


def test_set_transition_none_clears_duration():
    out = apply_operation(
        _project(), "set_transition", {"clip_id": "v0", "edge": "out", "type": "none"}
    )
    _, clip = out.find_clip("v0")
    assert clip.transition_out is not None
    assert clip.transition_out.type == "none"
    assert clip.transition_out.duration_s == pytest.approx(0.0)


def test_set_transition_rejects_non_visual():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_transition", {"clip_id": "a0", "type": "fade"})


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


def test_set_style_updates_typography_color_position():
    out = apply_operation(
        _project(),
        "set_style",
        {
            "clip_id": "s0",
            "font_family": "Roboto, sans-serif",
            "font_size": 72,
            "color": "#FF0000",
            "bg_color": "#000000",
            "bold": False,
            "outline": False,
            "position": "top",
            "preset": "tiktok",
            "x": 10,
            "y": -5,
        },
    )
    _, clip = out.find_clip("s0")
    assert clip.style is not None
    assert clip.style.font_family == "Roboto, sans-serif"
    assert clip.style.font_size == 72
    assert clip.style.color == "#FF0000"
    assert clip.style.bg_color == "#000000"
    assert clip.style.bold is False
    assert clip.style.outline is False
    assert clip.style.position == "top"
    assert clip.style.preset == "tiktok"
    assert clip.style.x == pytest.approx(10.0)
    assert clip.style.y == pytest.approx(-5.0)


def test_set_style_rejects_non_subtitle():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_style", {"clip_id": "v0", "font_size": 60})


def test_set_style_rejects_non_positive_font_size():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_style", {"clip_id": "s0", "font_size": 0})


def test_set_style_rejects_bad_position():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_style", {"clip_id": "s0", "position": "middle"})


def test_split_then_merge_subtitle_roundtrip():
    # Partimos el subtítulo s0 (0..2s) en 1.0s y luego volvemos a unirlo.
    split = apply_operation(_project(), "split", {"clip_id": "s0", "t_s": 1.0})
    track = split.track_by_id("track_subtitle")
    assert [c.id for c in track.clips] == ["s0", "s0_b"]

    merged = apply_operation(split, "merge", {"clip_id": "s0", "other_id": "s0_b"})
    mtrack = merged.track_by_id("track_subtitle")
    assert [c.id for c in mtrack.clips] == ["s0"]
    merged_clip = mtrack.clips[0]
    assert merged_clip.start_s == pytest.approx(0.0)
    assert merged_clip.duration_s == pytest.approx(2.0)


def test_merge_rejects_non_adjacent_clips():
    # Movemos el subtítulo para crear un hueco antes de intentar unir.
    project = _project()
    dup = apply_operation(project, "duplicate", {"clip_id": "s0"})
    moved = apply_operation(dup, "move", {"clip_id": "s0_copy", "start_s": 10.0})
    with pytest.raises(EditOperationError):
        apply_operation(moved, "merge", {"clip_id": "s0", "other_id": "s0_copy"})


def test_merge_rejects_clips_in_different_tracks():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "merge", {"clip_id": "v0", "other_id": "a0"})


def test_unknown_operation_rejected():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "frobnicate", {"clip_id": "v0"})


def test_missing_clip_rejected():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "delete", {"clip_id": "ghost"})

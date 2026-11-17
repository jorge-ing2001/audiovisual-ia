"""Validación del modelo ``EditProject`` (puro, sin ffmpeg ni Chromium)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engines.editor.model import (
    EDIT_PROJECT_KIND,
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
)


def _project() -> EditProject:
    track = EditTrack(
        id="track_image",
        kind="image",
        clips=[
            EditClip(
                id="c0",
                track_id="track_image",
                kind="image",
                start_s=0.0,
                duration_s=2.0,
                source_in_s=0.0,
                source_out_s=2.0,
                asset_id=1,
            ),
            EditClip(
                id="c1",
                track_id="track_image",
                kind="image",
                start_s=2.0,
                duration_s=3.0,
                asset_id=2,
            ),
        ],
    )
    return EditProject(project_id=7, meta=EditMeta(), tracks=[track])


def test_edit_project_marks_kind_and_duration() -> None:
    project = _project()
    assert project.kind == EDIT_PROJECT_KIND
    # Duración = fin del clip más tardío (2.0 + 3.0).
    assert project.duration_s == pytest.approx(5.0)


def test_find_clip_and_track_lookup() -> None:
    project = _project()
    found = project.find_clip("c1")
    assert found is not None
    track, clip = found
    assert track.id == "track_image"
    assert clip.end_s == pytest.approx(5.0)
    assert project.track_by_id("track_image") is track
    assert project.find_clip("nope") is None


def test_negative_duration_rejected() -> None:
    with pytest.raises(ValidationError):
        EditClip(id="x", track_id="t", kind="image", duration_s=-1.0)


def test_source_window_invariant() -> None:
    with pytest.raises(ValidationError):
        EditClip(
            id="x",
            track_id="t",
            kind="voice",
            source_in_s=3.0,
            source_out_s=1.0,
        )


def test_clip_kind_must_match_track() -> None:
    with pytest.raises(ValidationError):
        EditTrack(
            id="track_voice",
            kind="voice",
            clips=[EditClip(id="c", track_id="track_voice", kind="image")],
        )


def test_clip_track_id_must_match_track() -> None:
    with pytest.raises(ValidationError):
        EditTrack(
            id="track_voice",
            kind="voice",
            clips=[EditClip(id="c", track_id="other", kind="voice")],
        )


def test_roundtrip_serialization() -> None:
    project = _project()
    payload = project.model_dump(mode="json", by_alias=False)
    restored = EditProject.model_validate(payload)
    assert restored.duration_s == pytest.approx(project.duration_s)
    assert restored.kind == EDIT_PROJECT_KIND


def test_title_track_accepts_title_clip() -> None:
    from app.engines.editor.model import TitleAnimation, TitleStyle

    track = EditTrack(
        id="track_title",
        kind="title",
        clips=[
            EditClip(
                id="t0",
                track_id="track_title",
                kind="title",
                start_s=0.0,
                duration_s=2.0,
                text="Hola",
                title=TitleStyle(),
                title_animation=TitleAnimation(),
            )
        ],
    )
    assert track.clips[0].title.font_size == 96


def test_title_style_rejects_bad_anchor() -> None:
    from app.engines.editor.model import TitleStyle

    with pytest.raises(ValidationError):
        TitleStyle(anchor_y=1.5)


def test_overlay_track_accepts_overlay_clip() -> None:
    from app.engines.editor.model import OverlayStyle

    track = EditTrack(
        id="track_overlay",
        kind="overlay",
        clips=[
            EditClip(
                id="o0",
                track_id="track_overlay",
                kind="overlay",
                start_s=0.0,
                duration_s=3.0,
                asset_id=1,
                overlay=OverlayStyle(opacity=0.5, tile=True),
            )
        ],
    )
    assert track.clips[0].overlay.tile is True


def test_overlay_style_rejects_out_of_range() -> None:
    from app.engines.editor.model import OverlayStyle

    with pytest.raises(ValidationError):
        OverlayStyle(width_frac=2.0)


def test_title_overlay_clip_must_match_track() -> None:
    with pytest.raises(ValidationError):
        EditTrack(
            id="track_title",
            kind="title",
            clips=[EditClip(id="c", track_id="track_title", kind="overlay")],
        )

"""Operaciones y compilación de títulos (FEAT-005) y overlays (FEAT-006), puras.

Cubre:
- ``op_add_track`` creando pistas ``title``/``overlay``.
- ``op_add_title``/``op_set_title_style``/``op_set_title_animation`` (+ validaciones).
- ``op_add_overlay``/``op_set_overlay_style`` (+ validaciones de asset/kind/rango).
- El compilador emite ``titleClips``/``overlayClips`` en inputProps.
- REGRESIÓN: un EditProject sin títulos/overlays NO gana claves ``titleClips``/
  ``overlayClips`` (mismo inputProps que antes del Bloque 2).
"""

from __future__ import annotations

import pytest

from app.engines.editor.compile import compile_remotion_props
from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    OverlayStyle,
    TitleAnimation,
    TitleStyle,
)
from app.engines.editor.operations import EditOperationError, apply_operation


def _base() -> EditProject:
    image = EditTrack(
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
                asset_id=1,
            )
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(fps=30), tracks=[image])


# --------------------------------------------------------------------------- #
# add_track
# --------------------------------------------------------------------------- #


def test_add_track_creates_title_and_overlay_tracks():
    out = apply_operation(_base(), "add_track", {"kind": "title", "name": "Títulos"})
    track = out.track_by_id("track_title")
    assert track is not None
    assert track.kind == "title"
    assert track.name == "Títulos"
    assert track.order == 1  # al final

    out2 = apply_operation(out, "add_track", {"kind": "overlay"})
    assert out2.track_by_id("track_overlay") is not None


def test_add_track_rejects_unknown_kind():
    with pytest.raises(EditOperationError):
        apply_operation(_base(), "add_track", {"kind": "banana"})


def test_add_track_derives_unique_id_on_collision():
    out = apply_operation(_base(), "add_track", {"kind": "title"})
    out = apply_operation(out, "add_track", {"kind": "title"})
    ids = [t.id for t in out.tracks if t.kind == "title"]
    assert ids == ["track_title", "track_title_2"]


# --------------------------------------------------------------------------- #
# Títulos
# --------------------------------------------------------------------------- #


def _with_title_track() -> EditProject:
    return apply_operation(_base(), "add_track", {"kind": "title"})


def test_add_title_creates_clip_with_defaults():
    out = apply_operation(
        _with_title_track(),
        "add_title",
        {"track_id": "track_title", "text": "Hola", "start_s": 0.0, "duration_s": 2.0},
    )
    track = out.track_by_id("track_title")
    assert len(track.clips) == 1
    clip = track.clips[0]
    assert clip.kind == "title"
    assert clip.text == "Hola"
    assert clip.title is not None and clip.title.kind == "plain"
    assert clip.title_animation is not None and clip.title_animation.type == "fade"


def test_add_title_with_explicit_style_and_animation():
    out = apply_operation(
        _with_title_track(),
        "add_title",
        {
            "track_id": "track_title",
            "text": "Intro",
            "start_s": 1.0,
            "duration_s": 3.0,
            "title": {"kind": "intro", "font_size": 120, "align": "center"},
            "animation": {"type": "scale_in", "duration_s": 0.8},
        },
    )
    clip = out.track_by_id("track_title").clips[0]
    assert clip.title.font_size == 120
    assert clip.title.kind == "intro"
    assert clip.title_animation.type == "scale_in"


def test_add_title_rejects_empty_text_and_bad_duration():
    with pytest.raises(EditOperationError):
        apply_operation(
            _with_title_track(),
            "add_title",
            {"track_id": "track_title", "text": "   ", "start_s": 0.0, "duration_s": 1.0},
        )
    with pytest.raises(EditOperationError):
        apply_operation(
            _with_title_track(),
            "add_title",
            {"track_id": "track_title", "text": "x", "start_s": 0.0, "duration_s": 0.0},
        )


def test_add_title_rejects_non_title_track():
    with pytest.raises(EditOperationError):
        apply_operation(
            _base(),
            "add_title",
            {"track_id": "track_image", "text": "x", "start_s": 0.0, "duration_s": 1.0},
        )


def test_set_title_style_edits_fields():
    out = apply_operation(
        _with_title_track(),
        "add_title",
        {"track_id": "track_title", "text": "T", "start_s": 0.0, "duration_s": 2.0},
    )
    clip_id = out.track_by_id("track_title").clips[0].id
    out = apply_operation(
        out,
        "set_title_style",
        {
            "clip_id": clip_id,
            "font_size": 72,
            "color": "#FF0000",
            "align": "left",
            "anchor_x": 0.2,
            "anchor_y": 0.8,
            "kind": "lower_third",
            "shadow": True,
            "shadow_blur": 6.0,
        },
    )
    title = out.find_clip(clip_id)[1].title
    assert title.font_size == 72
    assert title.color == "#FF0000"
    assert title.align == "left"
    assert title.anchor_x == pytest.approx(0.2)
    assert title.kind == "lower_third"
    assert title.shadow is True


def test_set_title_style_rejects_out_of_range_anchor():
    out = apply_operation(
        _with_title_track(),
        "add_title",
        {"track_id": "track_title", "text": "T", "start_s": 0.0, "duration_s": 2.0},
    )
    clip_id = out.track_by_id("track_title").clips[0].id
    with pytest.raises(EditOperationError):
        apply_operation(out, "set_title_style", {"clip_id": clip_id, "anchor_x": 2.0})


def test_set_title_animation_edits_fields():
    out = apply_operation(
        _with_title_track(),
        "add_title",
        {"track_id": "track_title", "text": "T", "start_s": 0.0, "duration_s": 2.0},
    )
    clip_id = out.track_by_id("track_title").clips[0].id
    out = apply_operation(
        out,
        "set_title_animation",
        {"clip_id": clip_id, "type": "typewriter", "duration_s": 1.2, "direction": "in"},
    )
    anim = out.find_clip(clip_id)[1].title_animation
    assert anim.type == "typewriter"
    assert anim.duration_s == pytest.approx(1.2)
    assert anim.direction == "in"


def test_set_title_animation_rejects_bad_type():
    out = apply_operation(
        _with_title_track(),
        "add_title",
        {"track_id": "track_title", "text": "T", "start_s": 0.0, "duration_s": 2.0},
    )
    clip_id = out.track_by_id("track_title").clips[0].id
    with pytest.raises(EditOperationError):
        apply_operation(out, "set_title_animation", {"clip_id": clip_id, "type": "nope"})


# --------------------------------------------------------------------------- #
# Overlays
# --------------------------------------------------------------------------- #


def _with_overlay_track() -> EditProject:
    return apply_operation(_base(), "add_track", {"kind": "overlay"})


def test_add_overlay_creates_clip_with_defaults():
    out = apply_operation(
        _with_overlay_track(),
        "add_overlay",
        {"track_id": "track_overlay", "asset_id": 9, "start_s": 0.0, "duration_s": 5.0},
    )
    clip = out.track_by_id("track_overlay").clips[0]
    assert clip.kind == "overlay"
    assert clip.asset_id == 9
    assert clip.overlay is not None
    assert clip.overlay.width_frac == pytest.approx(0.2)
    assert clip.overlay.tile is False


def test_add_overlay_with_explicit_style():
    out = apply_operation(
        _with_overlay_track(),
        "add_overlay",
        {
            "track_id": "track_overlay",
            "asset_id": 9,
            "start_s": 0.0,
            "duration_s": 5.0,
            "overlay": {"opacity": 0.3, "width_frac": 0.5, "tile": True},
        },
    )
    overlay = out.track_by_id("track_overlay").clips[0].overlay
    assert overlay.opacity == pytest.approx(0.3)
    assert overlay.width_frac == pytest.approx(0.5)
    assert overlay.tile is True


def test_add_overlay_requires_asset_id():
    with pytest.raises(EditOperationError):
        apply_operation(
            _with_overlay_track(),
            "add_overlay",
            {"track_id": "track_overlay", "start_s": 0.0, "duration_s": 5.0},
        )


def test_add_overlay_rejects_non_overlay_track():
    with pytest.raises(EditOperationError):
        apply_operation(
            _base(),
            "add_overlay",
            {"track_id": "track_image", "asset_id": 9, "start_s": 0.0, "duration_s": 5.0},
        )


def test_add_overlay_rejects_out_of_range_style():
    with pytest.raises(EditOperationError):
        apply_operation(
            _with_overlay_track(),
            "add_overlay",
            {
                "track_id": "track_overlay",
                "asset_id": 9,
                "start_s": 0.0,
                "duration_s": 5.0,
                "overlay": {"opacity": 1.5},
            },
        )


def test_set_overlay_style_edits_fields():
    out = apply_operation(
        _with_overlay_track(),
        "add_overlay",
        {"track_id": "track_overlay", "asset_id": 9, "start_s": 0.0, "duration_s": 5.0},
    )
    clip_id = out.track_by_id("track_overlay").clips[0].id
    out = apply_operation(
        out,
        "set_overlay_style",
        {"clip_id": clip_id, "opacity": 0.6, "width_frac": 0.4, "rotation": 15.0, "tile": True},
    )
    overlay = out.find_clip(clip_id)[1].overlay
    assert overlay.opacity == pytest.approx(0.6)
    assert overlay.width_frac == pytest.approx(0.4)
    assert overlay.rotation == pytest.approx(15.0)
    assert overlay.tile is True


def test_set_overlay_style_rejects_out_of_range():
    out = apply_operation(
        _with_overlay_track(),
        "add_overlay",
        {"track_id": "track_overlay", "asset_id": 9, "start_s": 0.0, "duration_s": 5.0},
    )
    clip_id = out.track_by_id("track_overlay").clips[0].id
    with pytest.raises(EditOperationError):
        apply_operation(out, "set_overlay_style", {"clip_id": clip_id, "width_frac": 3.0})


def test_operations_titles_overlays_are_pure():
    project = _with_title_track()
    before = project.model_dump(mode="json")
    apply_operation(
        project,
        "add_title",
        {"track_id": "track_title", "text": "x", "start_s": 0.0, "duration_s": 1.0},
    )
    assert project.model_dump(mode="json") == before


# --------------------------------------------------------------------------- #
# Compilador: titleClips / overlayClips + regresión
# --------------------------------------------------------------------------- #


def _title_project() -> EditProject:
    title_track = EditTrack(
        id="track_title",
        kind="title",
        order=5,
        clips=[
            EditClip(
                id="t0",
                track_id="track_title",
                kind="title",
                start_s=1.0,
                duration_s=2.0,
                text="Mi título",
                title=TitleStyle(kind="intro", font_size=100, align="center"),
                title_animation=TitleAnimation(type="scale_in", duration_s=0.5),
            )
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(fps=30), tracks=[title_track])


def _overlay_project() -> EditProject:
    overlay_track = EditTrack(
        id="track_overlay",
        kind="overlay",
        order=6,
        clips=[
            EditClip(
                id="o0",
                track_id="track_overlay",
                kind="overlay",
                start_s=0.0,
                duration_s=5.0,
                asset_id=42,
                overlay=OverlayStyle(opacity=0.5, width_frac=0.3, tile=True),
            )
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(fps=30), tracks=[overlay_track])


def test_compile_emits_title_clips():
    props = compile_remotion_props(_title_project())
    assert "titleClips" in props
    titles = props["titleClips"]
    assert len(titles) == 1
    t = titles[0]
    assert t["from"] == 30  # 1.0s * 30fps
    assert t["durationInFrames"] == 60
    assert t["text"] == "Mi título"
    assert t["style"]["kind"] == "intro"
    assert t["style"]["fontSize"] == 100
    assert t["style"]["align"] == "center"
    assert t["animation"]["type"] == "scale_in"
    assert t["animation"]["durationInFrames"] == 15  # 0.5s * 30


def test_compile_emits_overlay_clips_with_resolved_src():
    props = compile_remotion_props(_overlay_project(), lambda aid: f"overlays/{aid}.png")
    assert "overlayClips" in props
    overlays = props["overlayClips"]
    assert len(overlays) == 1
    o = overlays[0]
    assert o["from"] == 0
    assert o["durationInFrames"] == 150  # 5.0s * 30
    assert o["src"] == "overlays/42.png"
    assert o["overlay"]["opacity"] == pytest.approx(0.5)
    assert o["overlay"]["widthFrac"] == pytest.approx(0.3)
    assert o["overlay"]["tile"] is True


def test_compile_regression_no_titles_overlays_keys():
    """REGRESIÓN: sin títulos/overlays, el inputProps NO gana esas claves."""
    props = compile_remotion_props(_base(), lambda aid: f"asset/{aid}.png")
    assert "titleClips" not in props
    assert "overlayClips" not in props

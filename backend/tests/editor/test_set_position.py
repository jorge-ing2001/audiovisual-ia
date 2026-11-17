"""Operación pura ``op_set_position`` (FEAT-008): drag&drop de posición.

Cubre los TRES tipos de clip posicionables y sus validaciones:
- ``title`` / ``overlay``: fija el anclaje ``anchor_x``/``anchor_y`` (fracción 0..1).
- ``subtitle``: fija el desplazamiento ``x``/``y`` en píxeles.
- Rechaza tipos no posicionables (video/image/audio), parámetros ausentes y anclas
  fuera de rango. Comprueba pureza (no muta el proyecto de entrada) y que ``set_position``
  está registrada en ``OPERATIONS``.
"""

from __future__ import annotations

import pytest

from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    OverlayStyle,
    SubtitleStyle,
    TitleStyle,
)
from app.engines.editor.operations import (
    OPERATIONS,
    EditOperationError,
    apply_operation,
)


def _project() -> EditProject:
    title = EditTrack(
        id="track_title",
        kind="title",
        order=0,
        clips=[
            EditClip(
                id="t0",
                track_id="track_title",
                kind="title",
                start_s=0.0,
                duration_s=2.0,
                text="Hola",
                title=TitleStyle(),
            )
        ],
    )
    overlay = EditTrack(
        id="track_overlay",
        kind="overlay",
        order=1,
        clips=[
            EditClip(
                id="o0",
                track_id="track_overlay",
                kind="overlay",
                start_s=0.0,
                duration_s=2.0,
                asset_id=7,
                overlay=OverlayStyle(),
            )
        ],
    )
    subtitle = EditTrack(
        id="track_sub",
        kind="subtitle",
        order=2,
        clips=[
            EditClip(
                id="s0",
                track_id="track_sub",
                kind="subtitle",
                start_s=0.0,
                duration_s=2.0,
                text="Texto",
                style=SubtitleStyle(),
            )
        ],
    )
    image = EditTrack(
        id="track_image",
        kind="image",
        order=3,
        clips=[
            EditClip(
                id="v0",
                track_id="track_image",
                kind="image",
                start_s=0.0,
                duration_s=2.0,
                asset_id=1,
            )
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(fps=30), tracks=[title, overlay, subtitle, image])


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #


def test_set_position_registered():
    assert "set_position" in OPERATIONS


# --------------------------------------------------------------------------- #
# Título / overlay (ancla)
# --------------------------------------------------------------------------- #


def test_set_position_title_sets_anchor():
    out = apply_operation(
        _project(), "set_position", {"clip_id": "t0", "anchor_x": 0.25, "anchor_y": 0.75}
    )
    title = out.find_clip("t0")[1].title
    assert title.anchor_x == pytest.approx(0.25)
    assert title.anchor_y == pytest.approx(0.75)


def test_set_position_overlay_sets_anchor_partial():
    # Solo anchor_x: anchor_y conserva su valor previo (default 0.5).
    out = apply_operation(_project(), "set_position", {"clip_id": "o0", "anchor_x": 0.1})
    overlay = out.find_clip("o0")[1].overlay
    assert overlay.anchor_x == pytest.approx(0.1)
    assert overlay.anchor_y == pytest.approx(0.5)


def test_set_position_title_without_style_uses_default():
    project = _project()
    project.find_clip("t0")[1].title = None
    out = apply_operation(project, "set_position", {"clip_id": "t0", "anchor_y": 0.9})
    title = out.find_clip("t0")[1].title
    assert title is not None
    assert title.anchor_y == pytest.approx(0.9)
    assert title.anchor_x == pytest.approx(0.5)


def test_set_position_rejects_anchor_out_of_range():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_position", {"clip_id": "t0", "anchor_x": 1.5})
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_position", {"clip_id": "o0", "anchor_y": -0.1})


def test_set_position_title_requires_an_anchor():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_position", {"clip_id": "t0"})


# --------------------------------------------------------------------------- #
# Subtítulo (x/y px)
# --------------------------------------------------------------------------- #


def test_set_position_subtitle_sets_xy():
    out = apply_operation(
        _project(), "set_position", {"clip_id": "s0", "x": 40.0, "y": -120.0}
    )
    style = out.find_clip("s0")[1].style
    assert style.x == pytest.approx(40.0)
    assert style.y == pytest.approx(-120.0)


def test_set_position_subtitle_partial_y_only():
    out = apply_operation(_project(), "set_position", {"clip_id": "s0", "y": 30.0})
    style = out.find_clip("s0")[1].style
    assert style.y == pytest.approx(30.0)
    assert style.x == pytest.approx(0.0)


def test_set_position_subtitle_requires_x_or_y():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_position", {"clip_id": "s0"})


# --------------------------------------------------------------------------- #
# Tipos no posicionables + pureza
# --------------------------------------------------------------------------- #


def test_set_position_rejects_visual_clip():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_position", {"clip_id": "v0", "anchor_x": 0.5})


def test_set_position_rejects_unknown_clip():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_position", {"clip_id": "nope", "anchor_x": 0.5})


def test_set_position_is_pure():
    project = _project()
    before = project.model_dump(mode="json")
    apply_operation(project, "set_position", {"clip_id": "t0", "anchor_x": 0.3})
    assert project.model_dump(mode="json") == before

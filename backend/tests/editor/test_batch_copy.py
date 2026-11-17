"""FEAT-004: operaciones batch + copiar/pegar atributos (puras, sin render).

Cubre:
- ``op_batch_delete`` atómica (todo o nada): rechaza clips inexistentes y pistas locked
  sin mutar; borra todos cuando valida.
- ``op_batch_move`` desplaza por el mismo delta conservando offsets relativos; rechaza
  start_s<0 y pistas locked; atómica.
- ``op_copy_attributes`` copia solo atributos compatibles con el TIPO del destino, nunca
  id/start_s/duration_s/asset_id; atributos no aplicables se ignoran; atómica.
"""

from __future__ import annotations

import pytest

from app.engines.editor.model import (
    ClipTransition,
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    OverlayStyle,
    SubtitleStyle,
    TitleAnimation,
    TitleStyle,
    VisualTransform,
)
from app.engines.editor.operations import (
    OPERATIONS,
    EditOperationError,
    apply_operation,
)


def _project() -> EditProject:
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
                duration_s=2.0,
                asset_id=1,
                transform=VisualTransform(scale=2.0, x=10.0),
                transition_in=ClipTransition(type="fade", duration_s=0.5),
            ),
            EditClip(
                id="v1",
                track_id="track_image",
                kind="image",
                start_s=3.0,
                duration_s=1.0,
                asset_id=2,
            ),
            EditClip(
                id="v2",
                track_id="track_image",
                kind="image",
                start_s=5.0,
                duration_s=1.0,
                asset_id=3,
            ),
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
                start_s=1.0,
                duration_s=2.0,
                asset_id=20,
                gain=0.4,
                fade_in_s=0.3,
                fade_out_s=0.2,
            ),
            EditClip(
                id="a1",
                track_id="track_voice",
                kind="voice",
                start_s=4.0,
                duration_s=2.0,
                asset_id=21,
                gain=1.0,
            ),
        ],
    )
    subs = EditTrack(
        id="track_subtitle",
        kind="subtitle",
        order=2,
        clips=[
            EditClip(
                id="s0",
                track_id="track_subtitle",
                kind="subtitle",
                start_s=0.0,
                duration_s=2.0,
                text="uno",
                style=SubtitleStyle(font_size=72, color="#ff0000"),
            ),
            EditClip(
                id="s1",
                track_id="track_subtitle",
                kind="subtitle",
                start_s=2.0,
                duration_s=2.0,
                text="dos",
                style=SubtitleStyle(),
            ),
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(fps=30), tracks=[image, voice, subs])


def _lock(project: EditProject, track_id: str) -> EditProject:
    return apply_operation(
        project, "set_track_flags", {"track_id": track_id, "locked": True}
    )


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #


def test_batch_ops_registered():
    assert "batch_delete" in OPERATIONS
    assert "batch_move" in OPERATIONS
    assert "copy_attributes" in OPERATIONS


# --------------------------------------------------------------------------- #
# op_batch_delete
# --------------------------------------------------------------------------- #


def test_batch_delete_removes_all():
    out = apply_operation(_project(), "batch_delete", {"clip_ids": ["v0", "a0", "s0"]})
    assert out.find_clip("v0") is None
    assert out.find_clip("a0") is None
    assert out.find_clip("s0") is None
    # Los no listados siguen.
    assert out.find_clip("v1") is not None


def test_batch_delete_requires_non_empty_list():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "batch_delete", {"clip_ids": []})
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "batch_delete", {})


def test_batch_delete_atomic_on_missing_clip():
    project = _project()
    before = project.model_dump(mode="json")
    with pytest.raises(EditOperationError):
        apply_operation(project, "batch_delete", {"clip_ids": ["v0", "nope"]})
    # No se mutó nada (ni siquiera v0, que sí existía): atomicidad.
    assert project.model_dump(mode="json") == before


def test_batch_delete_atomic_rejects_locked():
    project = _lock(_project(), "track_image")
    before = project.model_dump(mode="json")
    with pytest.raises(EditOperationError):
        apply_operation(project, "batch_delete", {"clip_ids": ["v0", "a0"]})
    assert project.model_dump(mode="json") == before


def test_batch_delete_is_pure():
    project = _project()
    before = project.model_dump(mode="json")
    apply_operation(project, "batch_delete", {"clip_ids": ["v0"]})
    assert project.model_dump(mode="json") == before


# --------------------------------------------------------------------------- #
# op_batch_move
# --------------------------------------------------------------------------- #


def test_batch_move_preserves_relative_offsets():
    # v0 en 0, v2 en 5; mover ambos +2 => 2 y 7 (offset relativo 5 conservado).
    out = apply_operation(_project(), "batch_move", {"clip_ids": ["v0", "v2"], "delta_s": 2.0})
    assert out.find_clip("v0")[1].start_s == pytest.approx(2.0)
    assert out.find_clip("v2")[1].start_s == pytest.approx(7.0)


def test_batch_move_negative_delta():
    out = apply_operation(_project(), "batch_move", {"clip_ids": ["v1", "v2"], "delta_s": -1.0})
    assert out.find_clip("v1")[1].start_s == pytest.approx(2.0)
    assert out.find_clip("v2")[1].start_s == pytest.approx(4.0)


def test_batch_move_rejects_resulting_negative():
    project = _project()
    before = project.model_dump(mode="json")
    with pytest.raises(EditOperationError):
        # v0 está en 0; -1 lo llevaría a -1.
        apply_operation(project, "batch_move", {"clip_ids": ["v0", "v2"], "delta_s": -1.0})
    assert project.model_dump(mode="json") == before


def test_batch_move_requires_delta():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "batch_move", {"clip_ids": ["v0"]})


def test_batch_move_rejects_missing_clip_atomically():
    project = _project()
    before = project.model_dump(mode="json")
    with pytest.raises(EditOperationError):
        apply_operation(project, "batch_move", {"clip_ids": ["v0", "nope"], "delta_s": 1.0})
    assert project.model_dump(mode="json") == before


def test_batch_move_rejects_locked():
    project = _lock(_project(), "track_image")
    with pytest.raises(EditOperationError):
        apply_operation(project, "batch_move", {"clip_ids": ["v0"], "delta_s": 1.0})


# --------------------------------------------------------------------------- #
# op_copy_attributes
# --------------------------------------------------------------------------- #


def test_copy_transform_between_visual_clips():
    out = apply_operation(
        _project(),
        "copy_attributes",
        {"source_clip_id": "v0", "target_clip_ids": ["v1"], "attrs": ["transform"]},
    )
    target = out.find_clip("v1")[1]
    assert target.transform is not None
    assert target.transform.scale == pytest.approx(2.0)
    assert target.transform.x == pytest.approx(10.0)


def test_copy_does_not_touch_position_or_asset():
    out = apply_operation(
        _project(),
        "copy_attributes",
        {"source_clip_id": "v0", "target_clip_ids": ["v2"], "attrs": ["transform"]},
    )
    target = out.find_clip("v2")[1]
    # start_s/duration_s/asset_id del destino se conservan (no se copian del origen).
    assert target.start_s == pytest.approx(5.0)
    assert target.duration_s == pytest.approx(1.0)
    assert target.asset_id == 3


def test_copy_gain_and_fade_between_audio_clips():
    out = apply_operation(
        _project(),
        "copy_attributes",
        {"source_clip_id": "a0", "target_clip_ids": ["a1"], "attrs": ["gain", "fade"]},
    )
    target = out.find_clip("a1")[1]
    assert target.gain == pytest.approx(0.4)
    assert target.fade_in_s == pytest.approx(0.3)
    assert target.fade_out_s == pytest.approx(0.2)


def test_copy_style_between_subtitles():
    out = apply_operation(
        _project(),
        "copy_attributes",
        {"source_clip_id": "s0", "target_clip_ids": ["s1"], "attrs": ["style"]},
    )
    target = out.find_clip("s1")[1]
    assert target.style.font_size == 72
    assert target.style.color == "#ff0000"


def test_copy_incompatible_attr_ignored_for_type():
    # Copiar 'style' (subtítulo) a un clip de vídeo: se IGNORA (no falla, no muta el vídeo).
    out = apply_operation(
        _project(),
        "copy_attributes",
        {"source_clip_id": "s0", "target_clip_ids": ["v1"], "attrs": ["style"]},
    )
    target = out.find_clip("v1")[1]
    assert target.style is None  # no se copió el estilo de subtítulo al vídeo


def test_copy_mixed_selection_applies_only_what_fits():
    # attrs mezcla transform (visual) + style (subtítulo); cada destino recibe lo suyo.
    out = apply_operation(
        _project(),
        "copy_attributes",
        {
            "source_clip_id": "v0",
            "target_clip_ids": ["v1"],
            "attrs": ["transform", "style"],
        },
    )
    v1 = out.find_clip("v1")[1]
    assert v1.transform is not None and v1.transform.scale == pytest.approx(2.0)
    assert v1.style is None


def test_copy_transition_between_visuals():
    out = apply_operation(
        _project(),
        "copy_attributes",
        {"source_clip_id": "v0", "target_clip_ids": ["v1"], "attrs": ["transition"]},
    )
    target = out.find_clip("v1")[1]
    assert target.transition_in is not None
    assert target.transition_in.type == "fade"


def test_copy_rejects_unknown_attr():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(),
            "copy_attributes",
            {"source_clip_id": "v0", "target_clip_ids": ["v1"], "attrs": ["bogus"]},
        )


def test_copy_rejects_missing_source():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(),
            "copy_attributes",
            {"source_clip_id": "nope", "target_clip_ids": ["v1"], "attrs": ["transform"]},
        )


def test_copy_atomic_on_missing_target():
    project = _project()
    before = project.model_dump(mode="json")
    with pytest.raises(EditOperationError):
        apply_operation(
            project,
            "copy_attributes",
            {"source_clip_id": "v0", "target_clip_ids": ["v1", "nope"], "attrs": ["transform"]},
        )
    assert project.model_dump(mode="json") == before


def test_copy_rejects_locked_target():
    project = _lock(_project(), "track_image")
    with pytest.raises(EditOperationError):
        apply_operation(
            project,
            "copy_attributes",
            {"source_clip_id": "v0", "target_clip_ids": ["v1"], "attrs": ["transform"]},
        )


def test_copy_requires_attrs_and_targets():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(),
            "copy_attributes",
            {"source_clip_id": "v0", "target_clip_ids": [], "attrs": ["transform"]},
        )
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(),
            "copy_attributes",
            {"source_clip_id": "v0", "target_clip_ids": ["v1"], "attrs": []},
        )


def test_copy_is_pure():
    project = _project()
    before = project.model_dump(mode="json")
    apply_operation(
        project,
        "copy_attributes",
        {"source_clip_id": "v0", "target_clip_ids": ["v1"], "attrs": ["transform"]},
    )
    assert project.model_dump(mode="json") == before

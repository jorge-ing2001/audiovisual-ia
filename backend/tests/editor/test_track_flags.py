"""FEAT-002: propiedades de pista (lock/hide/mute) + ripple delete (puro, sin render).

Cubre:
- ``op_set_track_flags`` fija ``locked``/``hidden``/``muted`` y queda registrada.
- La guarda ``_require_unlocked``: mutar un clip de una pista ``locked`` lanza
  ``EditOperationError``.
- ``op_ripple_delete`` cierra el hueco en su pista y NO toca otras pistas; rechaza locked.
- ``compile_remotion_props`` omite las pistas VISUALES ``hidden`` y mantiene el filtro por
  ``muted`` en audio.
- Regresión: un ``EditProject`` sin ``hidden`` produce el MISMO ``inputProps`` que antes.
"""

from __future__ import annotations

import pytest

from app.engines.editor.compile import compile_audio_tracks, compile_remotion_props
from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    SubtitleStyle,
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
            ),
            EditClip(
                id="v1",
                track_id="track_image",
                kind="image",
                start_s=2.0,
                duration_s=3.0,
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
    image2 = EditTrack(
        id="track_image2",
        kind="image",
        order=1,
        clips=[
            EditClip(
                id="w0",
                track_id="track_image2",
                kind="image",
                start_s=3.0,
                duration_s=2.0,
                asset_id=4,
            )
        ],
    )
    voice = EditTrack(
        id="track_voice",
        kind="voice",
        order=2,
        clips=[
            EditClip(
                id="a0",
                track_id="track_voice",
                kind="voice",
                start_s=0.0,
                duration_s=4.0,
                asset_id=20,
                gain=1.0,
            )
        ],
    )
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
    return EditProject(
        project_id=1, meta=EditMeta(fps=30), tracks=[image, image2, voice, subs]
    )


# --------------------------------------------------------------------------- #
# op_set_track_flags
# --------------------------------------------------------------------------- #


def test_set_track_flags_registered():
    assert "set_track_flags" in OPERATIONS
    assert "ripple_delete" in OPERATIONS


def test_set_track_flags_sets_all_three():
    out = apply_operation(
        _project(),
        "set_track_flags",
        {"track_id": "track_image", "locked": True, "hidden": True, "muted": True},
    )
    track = out.track_by_id("track_image")
    assert track.locked is True
    assert track.hidden is True
    assert track.muted is True


def test_set_track_flags_partial_keeps_others():
    out = apply_operation(
        _project(), "set_track_flags", {"track_id": "track_voice", "muted": True}
    )
    track = out.track_by_id("track_voice")
    assert track.muted is True
    assert track.locked is False
    assert track.hidden is False


def test_set_track_flags_requires_a_flag():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_track_flags", {"track_id": "track_image"})


def test_set_track_flags_rejects_non_bool():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(),
            "set_track_flags",
            {"track_id": "track_image", "locked": "yes"},
        )


def test_set_track_flags_unknown_track():
    with pytest.raises(EditOperationError):
        apply_operation(
            _project(), "set_track_flags", {"track_id": "nope", "locked": True}
        )


def test_set_track_flags_is_pure():
    project = _project()
    before = project.model_dump(mode="json")
    apply_operation(
        project, "set_track_flags", {"track_id": "track_image", "hidden": True}
    )
    assert project.model_dump(mode="json") == before


# --------------------------------------------------------------------------- #
# Guarda de locked en operaciones mutadoras
# --------------------------------------------------------------------------- #


def _lock(project: EditProject, track_id: str) -> EditProject:
    return apply_operation(
        project, "set_track_flags", {"track_id": track_id, "locked": True}
    )


@pytest.mark.parametrize(
    "op,params",
    [
        ("trim", {"clip_id": "v0", "source_in_s": 0.5}),
        ("split", {"clip_id": "v0", "t_s": 1.0}),
        ("delete", {"clip_id": "v0"}),
        ("duplicate", {"clip_id": "v0"}),
        ("move", {"clip_id": "v0", "start_s": 1.0}),
        ("set_duration", {"clip_id": "v0", "duration_s": 1.0}),
        ("set_transform", {"clip_id": "v0", "scale": 2.0}),
        ("ripple_delete", {"clip_id": "v0"}),
    ],
)
def test_locked_track_rejects_mutating_ops(op, params):
    project = _lock(_project(), "track_image")
    with pytest.raises(EditOperationError):
        apply_operation(project, op, params)


def test_locked_audio_track_rejects_gain_and_fade():
    project = _lock(_project(), "track_voice")
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_clip_gain", {"clip_id": "a0", "gain": 0.5})
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_fade", {"clip_id": "a0", "fade_in_s": 0.1})


def test_locked_subtitle_track_rejects_text_and_style():
    project = _lock(_project(), "track_subtitle")
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_text", {"clip_id": "s0", "text": "x"})
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_style", {"clip_id": "s0", "font_size": 60})


def test_move_into_locked_target_rejected():
    project = _lock(_project(), "track_image2")
    with pytest.raises(EditOperationError):
        apply_operation(
            project, "move", {"clip_id": "v0", "track_id": "track_image2"}
        )


def test_unlocked_track_still_mutates():
    # Sanity: sin locked, la operación funciona (la guarda no bloquea de más).
    out = apply_operation(_project(), "delete", {"clip_id": "v0"})
    assert out.find_clip("v0") is None


# --------------------------------------------------------------------------- #
# op_ripple_delete
# --------------------------------------------------------------------------- #


def test_ripple_delete_closes_gap_in_track():
    out = apply_operation(_project(), "ripple_delete", {"clip_id": "v0"})
    track = out.track_by_id("track_image")
    assert out.find_clip("v0") is None
    ids = {c.id: c for c in track.clips}
    # v1 empezaba en 2.0, v2 en 5.0; tras borrar v0 (dur 2.0) se desplazan -2.0.
    assert ids["v1"].start_s == pytest.approx(0.0)
    assert ids["v2"].start_s == pytest.approx(3.0)
    # No quedan huecos: v1 (0..3) y v2 (3..4) son contiguos.
    assert ids["v1"].end_s == pytest.approx(ids["v2"].start_s)


def test_ripple_delete_does_not_touch_other_tracks():
    out = apply_operation(_project(), "ripple_delete", {"clip_id": "v0"})
    # Otra pista de imagen y la de voz/subtítulo quedan intactas.
    assert out.track_by_id("track_image2").clips[0].start_s == pytest.approx(3.0)
    assert out.track_by_id("track_voice").clips[0].start_s == pytest.approx(0.0)
    assert out.track_by_id("track_subtitle").clips[0].start_s == pytest.approx(0.0)


def test_ripple_delete_middle_clip():
    out = apply_operation(_project(), "ripple_delete", {"clip_id": "v1"})
    track = out.track_by_id("track_image")
    ids = {c.id: c for c in track.clips}
    # v0 (anterior) no se mueve; v2 se desplaza -3.0 (dur de v1).
    assert ids["v0"].start_s == pytest.approx(0.0)
    assert ids["v2"].start_s == pytest.approx(2.0)


def test_ripple_delete_rejects_locked_track():
    project = _lock(_project(), "track_image")
    with pytest.raises(EditOperationError):
        apply_operation(project, "ripple_delete", {"clip_id": "v0"})


def test_ripple_delete_unknown_clip():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "ripple_delete", {"clip_id": "nope"})


# --------------------------------------------------------------------------- #
# compile honra hidden (visual) y muted (audio)
# --------------------------------------------------------------------------- #


def test_compile_hides_visual_track():
    project = apply_operation(
        _project(), "set_track_flags", {"track_id": "track_image", "hidden": True}
    )
    props = compile_remotion_props(project, lambda aid: str(aid))
    # Los clips de la pista oculta no aparecen; sí los de la otra pista de imagen.
    ids = {c["id"] for c in props["visualClips"]}
    assert ids == {"w0"}


def test_compile_hides_subtitle_track():
    project = apply_operation(
        _project(), "set_track_flags", {"track_id": "track_subtitle", "hidden": True}
    )
    props = compile_remotion_props(project, lambda aid: str(aid))
    assert props["subtitleClips"] == []


def test_compile_hidden_does_not_affect_audio_mixtracks():
    # hidden es concepto VISUAL: no afecta a compile_audio_tracks (solo muted lo hace).
    project = apply_operation(
        _project(), "set_track_flags", {"track_id": "track_voice", "hidden": True}
    )
    tracks = compile_audio_tracks(project, lambda aid: f"/a/{aid}.wav")
    assert len(tracks) == 1  # la voz sigue aportando su MixTrack


def test_compile_muted_audio_still_filtered():
    project = apply_operation(
        _project(), "set_track_flags", {"track_id": "track_voice", "muted": True}
    )
    props = compile_remotion_props(project, lambda aid: str(aid))
    assert props["audioClips"] == []
    tracks = compile_audio_tracks(project, lambda aid: f"/a/{aid}.wav")
    assert tracks == []


# --------------------------------------------------------------------------- #
# Regresión: sin hidden => mismo inputProps que antes del campo
# --------------------------------------------------------------------------- #


def test_regression_no_hidden_same_inputprops():
    """Un EditProject sin hidden (default False) produce el MISMO inputProps.

    Se compara contra el documento SIN el campo ``hidden`` en el JSON (como venía del
    Bloque 2): ambos validan y compilan al mismo ``inputProps``.
    """
    project = _project()
    props_new = compile_remotion_props(project, lambda aid: str(aid))

    # Documento "antiguo": serializado y despojado del campo hidden de cada pista.
    legacy_dump = project.model_dump(mode="json")
    for track in legacy_dump["tracks"]:
        track.pop("hidden", None)
    legacy = EditProject.model_validate(legacy_dump)
    props_legacy = compile_remotion_props(legacy, lambda aid: str(aid))

    assert props_new == props_legacy
    # Y ninguna clave nueva aparece por el hecho de añadir el campo.
    assert "hidden" not in props_new

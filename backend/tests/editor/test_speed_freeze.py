"""FEAT-006: velocidad (acelerar/ralentizar/invertir) + congelar fotograma — puro.

Cubre:
- ``op_set_speed`` valida ``rate > 0``, recalcula ``duration_s`` (con y sin rango de
  origen), rechaza ``reverse`` en no-vídeo, y limpia ``speed`` al volver a neutro.
- ``op_set_freeze`` valida rango de ``at_s`` + ``hold_s > 0``, rechaza ``image`` y limpia
  con ``hold_s=0``.
- ``compile_remotion_props`` emite ``speed``/``freeze`` SOLO cuando difieren del default.
- ``_clip_prep_filters`` compone la cadena de ``atempo`` (encadenando fuera de [0.5, 2.0])
  y añade ``areverse`` para audio invertido.
- Regresión: un ``EditProject`` sin speed/freeze produce el mismo inputProps/prep.
"""

from __future__ import annotations

import pytest

from app.engines.editor.compile import (
    _atempo_chain,
    _clip_prep_filters,
    compile_remotion_props,
)
from app.engines.editor.model import (
    ClipSpeed,
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
)
from app.engines.editor.operations import (
    OPERATIONS,
    EditOperationError,
    apply_operation,
)


def _video_clip(**overrides) -> EditClip:
    base = dict(
        id="v0",
        track_id="track_video",
        kind="video",
        start_s=0.0,
        duration_s=4.0,
        source_in_s=1.0,
        source_out_s=5.0,
        asset_id=7,
    )
    base.update(overrides)
    return EditClip(**base)


def _project(clip: EditClip | None = None, kind: str = "video") -> EditProject:
    clip = clip or _video_clip()
    return EditProject(
        project_id=1,
        meta=EditMeta(fps=30),
        tracks=[EditTrack(id=clip.track_id, kind=kind, clips=[clip])],
    )


# --------------------------------------------------------------------------- #
# op_set_speed
# --------------------------------------------------------------------------- #


def test_speed_freeze_registered() -> None:
    assert "set_speed" in OPERATIONS
    assert "set_freeze" in OPERATIONS


def test_set_speed_recalculates_duration_with_source_range() -> None:
    # Rango de origen 4 s (1..5); rate=2.0 => duration = 4/2 = 2.0.
    project = _project(_video_clip())
    result = apply_operation(project, "set_speed", {"clip_id": "v0", "rate": 2.0})
    _, clip = result.find_clip("v0")
    assert clip.duration_s == pytest.approx(2.0)
    assert clip.speed is not None and clip.speed.rate == pytest.approx(2.0)


def test_set_speed_slowdown_lengthens_duration() -> None:
    project = _project(_video_clip())
    result = apply_operation(project, "set_speed", {"clip_id": "v0", "rate": 0.5})
    _, clip = result.find_clip("v0")
    assert clip.duration_s == pytest.approx(8.0)  # 4 / 0.5


def test_set_speed_without_source_range_scales_duration() -> None:
    # Clip sin source_out_s (sin rango recortable): escala la duración por el rate.
    clip = _video_clip(source_in_s=0.0, source_out_s=None, duration_s=4.0)
    project = _project(clip)
    result = apply_operation(project, "set_speed", {"clip_id": "v0", "rate": 2.0})
    _, out = result.find_clip("v0")
    assert out.duration_s == pytest.approx(2.0)  # 4 * (1.0 / 2.0)


def test_set_speed_rejects_non_positive_rate() -> None:
    project = _project(_video_clip())
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_speed", {"clip_id": "v0", "rate": 0})
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_speed", {"clip_id": "v0", "rate": -1.0})


def test_set_speed_reverse_rejected_on_non_video() -> None:
    clip = _video_clip(kind="image", source_out_s=None)
    project = _project(clip, kind="image")
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_speed", {"clip_id": "v0", "reverse": True})


def test_set_speed_reverse_allowed_on_video() -> None:
    project = _project(_video_clip())
    result = apply_operation(project, "set_speed", {"clip_id": "v0", "reverse": True})
    _, clip = result.find_clip("v0")
    assert clip.speed is not None and clip.speed.reverse is True


def test_set_speed_neutral_clears_speed() -> None:
    clip = _video_clip(speed=ClipSpeed(rate=2.0))
    project = _project(clip)
    result = apply_operation(project, "set_speed", {"clip_id": "v0", "rate": 1.0})
    _, out = result.find_clip("v0")
    assert out.speed is None, "rate=1.0 sin reverse limpia speed (estado neutro)"


# --------------------------------------------------------------------------- #
# op_set_freeze
# --------------------------------------------------------------------------- #


def test_set_freeze_sets_within_source_range() -> None:
    project = _project(_video_clip())
    result = apply_operation(
        project, "set_freeze", {"clip_id": "v0", "at_s": 3.0, "hold_s": 1.5}
    )
    _, clip = result.find_clip("v0")
    assert clip.freeze is not None
    assert clip.freeze.at_s == pytest.approx(3.0)
    assert clip.freeze.hold_s == pytest.approx(1.5)


def test_set_freeze_rejects_at_s_outside_source() -> None:
    project = _project(_video_clip())  # origen 1..5
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_freeze", {"clip_id": "v0", "at_s": 9.0, "hold_s": 1.0})
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_freeze", {"clip_id": "v0", "at_s": 0.0, "hold_s": 1.0})


def test_set_freeze_rejects_image() -> None:
    clip = _video_clip(kind="image", source_out_s=None)
    project = _project(clip, kind="image")
    with pytest.raises(EditOperationError):
        apply_operation(project, "set_freeze", {"clip_id": "v0", "at_s": 0.0, "hold_s": 1.0})


def test_set_freeze_hold_zero_clears() -> None:
    clip = _video_clip()
    clip.freeze = None
    project = _project(clip)
    project = apply_operation(
        project, "set_freeze", {"clip_id": "v0", "at_s": 2.0, "hold_s": 1.0}
    )
    _, with_freeze = project.find_clip("v0")
    assert with_freeze.freeze is not None
    cleared = apply_operation(project, "set_freeze", {"clip_id": "v0", "at_s": 2.0, "hold_s": 0})
    _, out = cleared.find_clip("v0")
    assert out.freeze is None


# --------------------------------------------------------------------------- #
# Compilación condicional (speed/freeze)
# --------------------------------------------------------------------------- #


def test_compile_emits_speed_only_when_non_default() -> None:
    project = _project(_video_clip())
    # Sin speed: no se emite.
    assert "speed" not in compile_remotion_props(project)["visualClips"][0]
    # Con rate != 1.0: se emite.
    sped = apply_operation(project, "set_speed", {"clip_id": "v0", "rate": 2.0})
    vc = compile_remotion_props(sped)["visualClips"][0]
    assert vc["speed"] == {"rate": 2.0, "reverse": False}


def test_compile_emits_freeze_only_when_defined() -> None:
    project = _project(_video_clip())
    assert "freeze" not in compile_remotion_props(project)["visualClips"][0]
    frozen = apply_operation(
        project, "set_freeze", {"clip_id": "v0", "at_s": 3.0, "hold_s": 1.0}
    )
    vc = compile_remotion_props(frozen)["visualClips"][0]
    # at_s=3.0 @30fps => atFrame=90; hold_s=1.0 => holdInFrames=30.
    assert vc["freeze"]["atFrame"] == 90
    assert vc["freeze"]["holdInFrames"] == 30


# --------------------------------------------------------------------------- #
# Cadena de atempo (audio) y areverse
# --------------------------------------------------------------------------- #


def test_atempo_chain_within_range() -> None:
    assert _atempo_chain(1.0) == []
    assert _atempo_chain(1.5) == ["atempo=1.5"]
    assert _atempo_chain(0.75) == ["atempo=0.75"]


def test_atempo_chain_above_two() -> None:
    # rate=4.0 => 2.0 * 2.0
    assert _atempo_chain(4.0) == ["atempo=2.0", "atempo=2.0"]
    # rate=3.0 => 2.0 * 1.5
    assert _atempo_chain(3.0) == ["atempo=2.0", "atempo=1.5"]


def test_atempo_chain_below_half() -> None:
    # rate=0.25 => 0.5 * 0.5
    assert _atempo_chain(0.25) == ["atempo=0.5", "atempo=0.5"]


def test_audio_prep_includes_atempo_after_atrim() -> None:
    clip = EditClip(
        id="a0",
        track_id="track_voice",
        kind="voice",
        start_s=0.0,
        duration_s=2.0,
        source_in_s=1.0,
        source_out_s=5.0,
        asset_id=7,
        speed=ClipSpeed(rate=4.0),
    )
    prep = _clip_prep_filters(clip)
    # atrim primero, luego la cadena de atempo.
    atrim_idx = next(i for i, f in enumerate(prep) if f.startswith("atrim="))
    tempo_idx = next(i for i, f in enumerate(prep) if f.startswith("atempo="))
    assert atrim_idx < tempo_idx
    assert [f for f in prep if f.startswith("atempo=")] == ["atempo=2.0", "atempo=2.0"]


def test_audio_prep_includes_areverse() -> None:
    clip = EditClip(
        id="a0",
        track_id="track_voice",
        kind="voice",
        start_s=0.0,
        duration_s=2.0,
        source_in_s=0.0,
        source_out_s=2.0,
        asset_id=7,
        speed=ClipSpeed(rate=1.0, reverse=True),
    )
    prep = _clip_prep_filters(clip)
    assert "areverse" in prep


def test_regression_without_speed_freeze_prep_unchanged() -> None:
    """Un clip de audio sin speed produce la MISMA prep que el baseline (atrim/fades)."""
    clip = EditClip(
        id="a0",
        track_id="track_voice",
        kind="voice",
        start_s=0.0,
        duration_s=2.0,
        source_in_s=1.0,
        source_out_s=3.0,
        asset_id=7,
        fade_in_s=0.5,
    )
    prep = _clip_prep_filters(clip)
    assert prep == [
        "atrim=start=1.0:end=3.0",
        "asetpts=PTS-STARTPTS",
        "afade=t=in:st=0:d=0.5",
    ]
    assert not any(f.startswith("atempo=") for f in prep)
    assert "areverse" not in prep

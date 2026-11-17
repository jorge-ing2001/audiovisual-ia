"""FEAT-005: desvincular audio y vídeo (detach) — puro, sin render.

Cubre:
- ``op_detach_audio`` crea un clip ``voice`` equivalente (mismo asset/rango/tiempos/gain) y
  marca ``mute_source_audio=True`` en el clip de vídeo.
- Crea la pista ``voice`` si no existe; respeta ``target_track_id`` cuando se indica.
- Rechaza clips que no sean de vídeo y pistas bloqueadas.
- ``compile_remotion_props`` emite ``muted`` SOLO con el flag; el clip voice aporta su
  ``MixTrack`` (``compile_audio_tracks``).
- Regresión: un clip sin el flag compila igual que el baseline.
"""

from __future__ import annotations

import pytest

from app.engines.editor.compile import compile_audio_tracks, compile_remotion_props
from app.engines.editor.model import (
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


def _project(with_voice_track: bool = False) -> EditProject:
    video = EditTrack(
        id="track_video",
        kind="video",
        order=0,
        clips=[
            EditClip(
                id="v0",
                track_id="track_video",
                kind="video",
                start_s=1.5,
                duration_s=3.0,
                source_in_s=2.0,
                source_out_s=5.0,
                asset_id=7,
                gain=0.8,
            )
        ],
    )
    tracks = [video]
    if with_voice_track:
        tracks.append(EditTrack(id="track_voice", kind="voice", order=1))
    return EditProject(project_id=1, meta=EditMeta(), tracks=tracks)


def test_detach_audio_registered() -> None:
    assert "detach_audio" in OPERATIONS


def test_detach_audio_creates_voice_track_if_missing() -> None:
    project = _project(with_voice_track=False)
    result = apply_operation(project, "detach_audio", {"clip_id": "v0"})

    voice_tracks = [t for t in result.tracks if t.kind == "voice"]
    assert len(voice_tracks) == 1, "debe crear una pista voice"
    voice = voice_tracks[0]
    assert len(voice.clips) == 1
    audio_clip = voice.clips[0]
    # Mismo asset/rango/tiempos/gain que el clip de vídeo.
    assert audio_clip.kind == "voice"
    assert audio_clip.asset_id == 7
    assert audio_clip.start_s == pytest.approx(1.5)
    assert audio_clip.duration_s == pytest.approx(3.0)
    assert audio_clip.source_in_s == pytest.approx(2.0)
    assert audio_clip.source_out_s == pytest.approx(5.0)
    assert audio_clip.gain == pytest.approx(0.8)
    assert audio_clip.track_id == voice.id


def test_detach_audio_marks_mute_flag() -> None:
    project = _project()
    result = apply_operation(project, "detach_audio", {"clip_id": "v0"})
    _, video_clip = result.find_clip("v0")
    assert video_clip.mute_source_audio is True


def test_detach_audio_reuses_existing_voice_track() -> None:
    project = _project(with_voice_track=True)
    result = apply_operation(project, "detach_audio", {"clip_id": "v0"})
    voice_tracks = [t for t in result.tracks if t.kind == "voice"]
    assert len(voice_tracks) == 1, "no debe crear una segunda pista voice"
    assert len(voice_tracks[0].clips) == 1


def test_detach_audio_honors_target_track_id() -> None:
    project = _project(with_voice_track=True)
    project = apply_operation(project, "add_track", {"kind": "voice", "track_id": "track_voice_2"})
    result = apply_operation(
        project, "detach_audio", {"clip_id": "v0", "target_track_id": "track_voice_2"}
    )
    target = result.track_by_id("track_voice_2")
    assert target is not None and len(target.clips) == 1
    # La otra pista voice queda vacía.
    assert len(result.track_by_id("track_voice").clips) == 0


def test_detach_audio_rejects_non_video() -> None:
    project = EditProject(
        project_id=1,
        meta=EditMeta(),
        tracks=[
            EditTrack(
                id="track_image",
                kind="image",
                clips=[
                    EditClip(
                        id="i0",
                        track_id="track_image",
                        kind="image",
                        start_s=0.0,
                        duration_s=2.0,
                        asset_id=3,
                    )
                ],
            )
        ],
    )
    with pytest.raises(EditOperationError):
        apply_operation(project, "detach_audio", {"clip_id": "i0"})


def test_detach_audio_rejects_locked_track() -> None:
    project = _project()
    project.track_by_id("track_video").locked = True
    with pytest.raises(EditOperationError):
        apply_operation(project, "detach_audio", {"clip_id": "v0"})


def test_detach_audio_rejects_bad_target() -> None:
    project = _project()
    with pytest.raises(EditOperationError):
        apply_operation(
            project, "detach_audio", {"clip_id": "v0", "target_track_id": "track_image"}
        )


def test_compile_emits_muted_only_with_flag() -> None:
    # Sin flag: no se emite 'muted' en el clip de vídeo (regresión).
    project = _project()
    props = compile_remotion_props(project)
    assert len(props["visualClips"]) == 1
    assert "muted" not in props["visualClips"][0]

    # Con flag (detach): se emite 'muted': true.
    result = apply_operation(project, "detach_audio", {"clip_id": "v0"})
    props2 = compile_remotion_props(result)
    video_props = next(c for c in props2["visualClips"] if c["id"] == "v0")
    assert video_props["muted"] is True


def test_detached_voice_clip_produces_mixtrack() -> None:
    project = _project()
    result = apply_operation(project, "detach_audio", {"clip_id": "v0"})
    tracks = compile_audio_tracks(result, lambda aid: f"/media/{aid}.mp4" if aid else None)
    assert len(tracks) == 1
    mix = tracks[0]
    assert mix.path == "/media/7.mp4"
    assert mix.delay_s == pytest.approx(1.5)
    assert mix.gain == pytest.approx(0.8)
    # Recorte del origen reflejado como atrim en la preparación.
    assert any(p.startswith("atrim=") for p in mix.prep)


def test_regression_without_flag_matches_baseline() -> None:
    """Un EditProject sin mute_source_audio compila idéntico al baseline (sin la clave)."""
    project = _project()
    props = compile_remotion_props(project)
    for clip in props["visualClips"]:
        assert "muted" not in clip
    # El proyecto base no tiene clips de audio -> sin MixTracks.
    assert compile_audio_tracks(project, lambda _aid: None) == []

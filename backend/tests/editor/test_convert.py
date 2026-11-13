"""Conversión ``Timeline`` (por escena) → ``EditProject`` (puro, sin render)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engines.editor.convert import (
    MUSIC_DEFAULT_GAIN,
    SFX_DEFAULT_GAIN,
    timeline_to_edit_project,
)
from app.engines.render.timeline import SubtitleCue, Timeline, TimelineScene


def _asset(id, kind, *, scene_id=None, path="/x", meta=None):
    return SimpleNamespace(
        id=id, kind=kind, scene_id=scene_id, path=path, meta=meta or {}
    )


def _timeline() -> Timeline:
    return Timeline(
        project_id=1,
        fps=30,
        width=1920,
        height=1080,
        aspect_ratio="16:9",
        scenes=[
            TimelineScene(
                id="scene_0_10",
                duration_s=2.0,
                duration_in_frames=60,
                image_src="/media/img0.png",
                cues=[SubtitleCue(start_s=0.0, end_s=1.0, text="Hola")],
                subtitle_style="classic",
            ),
            TimelineScene(
                id="scene_1_11",
                duration_s=3.0,
                duration_in_frames=90,
                image_src="/media/img1.png",
                cues=[SubtitleCue(start_s=0.5, end_s=2.5, text="Mundo")],
                subtitle_style="classic",
            ),
        ],
    )


def _project_and_assets():
    assets = [
        _asset(10, "image", scene_id=10),
        _asset(11, "image", scene_id=11),
        _asset(20, "voice", scene_id=10, meta={"duration_s": 1.9}),
        _asset(21, "voice", scene_id=11, meta={"duration_s": 2.8}),
        _asset(30, "music"),
        _asset(31, "sfx"),
    ]
    project = SimpleNamespace(id=1, assets=assets, timelines=[SimpleNamespace(version=4)])
    return project, assets


def test_convert_builds_expected_tracks():
    project, assets = _project_and_assets()
    edit = timeline_to_edit_project(_timeline(), project, assets)

    kinds = {t.kind: t for t in edit.tracks}
    assert set(kinds) == {"video", "image", "voice", "music", "sfx", "subtitle"}
    assert edit.meta.fps == 30 and edit.meta.width == 1920
    assert edit.source_timeline_version == 4


def test_convert_visual_clips_use_accumulated_offsets():
    project, assets = _project_and_assets()
    edit = timeline_to_edit_project(_timeline(), project, assets)
    image_track = next(t for t in edit.tracks if t.kind == "image")
    assert len(image_track.clips) == 2
    assert image_track.clips[0].start_s == pytest.approx(0.0)
    assert image_track.clips[0].duration_s == pytest.approx(2.0)
    # Segunda escena arranca tras la primera (offset acumulado).
    assert image_track.clips[1].start_s == pytest.approx(2.0)
    assert image_track.clips[1].duration_s == pytest.approx(3.0)


def test_convert_voice_uses_real_duration_and_offset():
    project, assets = _project_and_assets()
    edit = timeline_to_edit_project(_timeline(), project, assets)
    voice_track = next(t for t in edit.tracks if t.kind == "voice")
    assert len(voice_track.clips) == 2
    assert voice_track.clips[0].start_s == pytest.approx(0.0)
    assert voice_track.clips[0].duration_s == pytest.approx(1.9)
    assert voice_track.clips[0].asset_id == 20
    assert voice_track.clips[1].start_s == pytest.approx(2.0)
    assert voice_track.clips[1].duration_s == pytest.approx(2.8)


def test_convert_subtitles_become_absolute_clips():
    project, assets = _project_and_assets()
    edit = timeline_to_edit_project(_timeline(), project, assets)
    sub_track = next(t for t in edit.tracks if t.kind == "subtitle")
    assert len(sub_track.clips) == 2
    # Cue 1 de la escena 1: start 0.0, dur 1.0.
    assert sub_track.clips[0].start_s == pytest.approx(0.0)
    assert sub_track.clips[0].duration_s == pytest.approx(1.0)
    assert sub_track.clips[0].text == "Hola"
    # Cue de la escena 2: offset 2.0 + 0.5 = 2.5, dur 2.0.
    assert sub_track.clips[1].start_s == pytest.approx(2.5)
    assert sub_track.clips[1].duration_s == pytest.approx(2.0)
    assert sub_track.clips[1].text == "Mundo"
    assert sub_track.clips[0].style.preset == "classic"


def test_convert_background_music_and_sfx_with_default_gains():
    project, assets = _project_and_assets()
    edit = timeline_to_edit_project(_timeline(), project, assets)
    music_track = next(t for t in edit.tracks if t.kind == "music")
    sfx_track = next(t for t in edit.tracks if t.kind == "sfx")
    assert len(music_track.clips) == 1
    assert music_track.clips[0].start_s == pytest.approx(0.0)
    assert music_track.clips[0].duration_s == pytest.approx(5.0)  # cubre todo el vídeo
    assert music_track.clips[0].gain == pytest.approx(MUSIC_DEFAULT_GAIN)
    assert sfx_track.clips[0].gain == pytest.approx(SFX_DEFAULT_GAIN)


def test_convert_total_duration_matches_timeline():
    project, assets = _project_and_assets()
    edit = timeline_to_edit_project(_timeline(), project, assets)
    # El fin del vídeo coincide con la suma de duraciones de escena (2 + 3).
    assert edit.duration_s == pytest.approx(5.0)


def test_convert_video_track_used_when_video_src_present():
    tl = _timeline()
    tl.scenes[0].video_src = "/media/clip0.mp4"
    tl.scenes[0].image_src = None
    project, assets = _project_and_assets()
    edit = timeline_to_edit_project(tl, project, assets)
    video_track = next(t for t in edit.tracks if t.kind == "video")
    image_track = next(t for t in edit.tracks if t.kind == "image")
    assert len(video_track.clips) == 1
    assert video_track.clips[0].kind == "video"
    # La segunda escena sigue usando imagen.
    assert len(image_track.clips) == 1

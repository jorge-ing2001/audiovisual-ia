"""Compilador del ``EditProject`` a inputProps Remotion + MixTracks (puro)."""

from __future__ import annotations

import pytest

from app.engines.editor.compile import compile_audio_tracks, compile_remotion_props
from app.engines.editor.model import (
    ClipTransition,
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    SubtitleStyle,
    VisualTransform,
)


def _project() -> EditProject:
    image = EditTrack(
        id="track_image",
        kind="image",
        order=1,
        clips=[
            EditClip(
                id="v0",
                track_id="track_image",
                kind="image",
                start_s=0.0,
                duration_s=2.0,
                asset_id=1,
                transform=VisualTransform(scale=1.5, x=30.0, y=-10.0, fit="contain"),
                transition_out=ClipTransition(type="fade", duration_s=0.5),
            ),
            EditClip(
                id="v1",
                track_id="track_image",
                kind="image",
                start_s=2.0,
                duration_s=3.0,
                asset_id=2,
                transition_in=ClipTransition(type="fade", duration_s=0.5),
            ),
        ],
    )
    voice = EditTrack(
        id="track_voice",
        kind="voice",
        order=2,
        volume=0.8,
        clips=[
            EditClip(
                id="a0",
                track_id="track_voice",
                kind="voice",
                start_s=2.0,
                duration_s=3.0,
                source_in_s=0.5,
                source_out_s=3.5,
                asset_id=20,
                gain=0.5,
                fade_in_s=0.2,
                fade_out_s=0.3,
            )
        ],
    )
    music = EditTrack(
        id="track_music",
        kind="music",
        order=3,
        muted=True,
        clips=[
            EditClip(
                id="m0",
                track_id="track_music",
                kind="music",
                start_s=0.0,
                duration_s=5.0,
                asset_id=30,
            )
        ],
    )
    subs = EditTrack(
        id="track_subtitle",
        kind="subtitle",
        order=4,
        clips=[
            EditClip(
                id="s0",
                track_id="track_subtitle",
                kind="subtitle",
                start_s=2.0,
                duration_s=1.0,
                text="hola",
                style=SubtitleStyle(
                    preset="tiktok",
                    position="center",
                    font_family="Roboto, sans-serif",
                    font_size=72,
                    color="#FF0000",
                    bg_color="#101010",
                    bold=False,
                    outline=False,
                    x=12.0,
                    y=-8.0,
                ),
            )
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(fps=30), tracks=[image, voice, music, subs])


def test_compile_remotion_props_places_clips_in_frames():
    props = compile_remotion_props(_project(), lambda aid: f"asset/{aid}.png")
    assert props["fps"] == 30
    assert props["durationInFrames"] == 150  # 5.0s * 30fps
    visuals = props["visualClips"]
    assert len(visuals) == 2
    assert visuals[0]["from"] == 0 and visuals[0]["durationInFrames"] == 60
    assert visuals[1]["from"] == 60 and visuals[1]["durationInFrames"] == 90
    assert visuals[0]["src"] == "asset/1.png"


def test_compile_remotion_props_visual_carry_transform_and_transition():
    props = compile_remotion_props(_project(), lambda aid: f"asset/{aid}.png")
    v0, v1 = props["visualClips"]
    # Transform del primer clip.
    assert v0["transform"]["scale"] == pytest.approx(1.5)
    assert v0["transform"]["x"] == pytest.approx(30.0)
    assert v0["transform"]["y"] == pytest.approx(-10.0)
    assert v0["transform"]["fit"] == "contain"
    # Transición fade de salida de v0 (0.5s * 30fps = 15 frames) y de entrada de v1.
    assert v0["transitionOut"]["type"] == "fade"
    assert v0["transitionOut"]["durationInFrames"] == 15
    assert v1["transitionIn"]["type"] == "fade"
    assert v1["transitionIn"]["durationInFrames"] == 15
    # v1 sin transform explícito -> None.
    assert v1["transform"] is None


def test_compile_remotion_props_subtitles_carry_style():
    props = compile_remotion_props(_project())
    subs = props["subtitleClips"]
    assert len(subs) == 1
    assert subs[0]["from"] == 60  # 2.0s
    assert subs[0]["text"] == "hola"
    style = subs[0]["style"]
    assert style["preset"] == "tiktok"
    assert style["position"] == "center"
    # El estilo COMPLETO llega a inputProps (tipografía/tamaño/color/fondo/negrita/contorno/pos).
    assert style["fontFamily"] == "Roboto, sans-serif"
    assert style["fontSize"] == 72
    assert style["color"] == "#FF0000"
    assert style["bgColor"] == "#101010"
    assert style["bold"] is False
    assert style["outline"] is False
    assert style["x"] == pytest.approx(12.0)
    assert style["y"] == pytest.approx(-8.0)


def test_compile_audio_tracks_delay_equals_start_and_effective_gain():
    tracks = compile_audio_tracks(_project(), lambda aid: f"/abs/{aid}.wav")
    # La pista music está muteada: no aporta MixTrack.
    assert len(tracks) == 1
    mt = tracks[0]
    assert mt.path == "/abs/20.wav"
    assert mt.delay_s == pytest.approx(2.0)  # delay_s = start_s
    assert mt.gain == pytest.approx(0.8 * 0.5)  # pista.volume * clip.gain


def test_compile_audio_tracks_prep_has_trim_and_fades():
    tracks = compile_audio_tracks(_project(), lambda aid: f"/abs/{aid}.wav")
    prep = tracks[0].prep
    assert any(p.startswith("atrim=start=0.5:end=3.5") for p in prep)
    assert any("asetpts=PTS-STARTPTS" == p for p in prep)
    assert any(p.startswith("afade=t=in:st=0:d=0.2") for p in prep)
    assert any("afade=t=out" in p for p in prep)


def test_track_volume_scales_effective_gain_in_props_and_mixtracks():
    """Subir/bajar el volumen de la pista reescala el gain efectivo en preview y export."""
    project = _project()
    # La pista de voz tiene volume=0.8 y el clip gain=0.5 -> efectivo 0.4.
    props = compile_remotion_props(project, lambda aid: str(aid))
    assert props["audioClips"][0]["volume"] == pytest.approx(0.8 * 0.5)
    tracks = compile_audio_tracks(project, lambda aid: f"/a/{aid}.wav")
    assert tracks[0].gain == pytest.approx(0.8 * 0.5)

    # Bajamos el volumen de la pista a la mitad: el efectivo cae en consecuencia.
    project.tracks[1].volume = 0.4
    props2 = compile_remotion_props(project, lambda aid: str(aid))
    tracks2 = compile_audio_tracks(project, lambda aid: f"/a/{aid}.wav")
    assert props2["audioClips"][0]["volume"] == pytest.approx(0.4 * 0.5)
    assert tracks2[0].gain == pytest.approx(0.4 * 0.5)


def test_compile_audio_skips_unresolved_assets():
    tracks = compile_audio_tracks(_project(), lambda aid: None)
    assert tracks == []


def test_sync_visual_audio_subtitle_share_timeline():
    """La escena 2 arranca a los 2.0s en vídeo, voz y subtítulo (sincronía)."""
    project = _project()
    props = compile_remotion_props(project, lambda aid: str(aid))
    audio = compile_audio_tracks(project, lambda aid: f"/a/{aid}.wav")

    visual_second = props["visualClips"][1]  # v1, start 2.0s
    sub = props["subtitleClips"][0]  # start 2.0s
    assert visual_second["startS"] == pytest.approx(2.0)
    assert sub["startS"] == pytest.approx(2.0)
    assert audio[0].delay_s == pytest.approx(2.0)


def test_disabled_clip_excluded_from_both_outputs():
    project = _project()
    # Deshabilita el clip de voz.
    project.tracks[1].clips[0].enabled = False
    props = compile_remotion_props(project, lambda aid: str(aid))
    audio = compile_audio_tracks(project, lambda aid: f"/a/{aid}.wav")
    assert audio == []
    # Los visuales no se ven afectados.
    assert len(props["visualClips"]) == 2

"""Parser de la salida de Blender (FEAT-302): frames, VideoClip en éxito y error accionable.

Lógica pura sobre strings: no requiere Blender.
"""

from __future__ import annotations

import pytest

from app.engines.engine3d import output_parser
from app.engines.engine3d.types import CameraSpec, Engine3DUnavailable, Scene3DSpec, VideoClip


def _spec(duration_s=4.0, fps=30, render_engine="eevee"):
    return Scene3DSpec(
        scene_id=5,
        order=0,
        duration_s=duration_s,
        character_id="hero",
        character_animation="idle",
        set_id="room_interior",
        camera=CameraSpec(),
        fps=fps,
        render_engine=render_engine,
    )


# stdout de ejemplo tipo Blender animando 4 frames a FFMPEG.
_STDOUT_OK = """\
Blender 4.2.0
Read blend: /assets/sets/room_interior.blend
Fra:1 Mem:120.00M | Time:00:00.50 | Rendering 1 / 32 samples
Append frame 1
Fra:2 Mem:121.00M | Time:00:01.00 | Rendering 1 / 32 samples
Append frame 2
Warning: Eevee Next is experimental
Fra:3 Mem:121.00M | Time:00:01.50 | Rendering 1 / 32 samples
Append frame 3
Fra:4 Mem:121.00M | Time:00:02.00 | Rendering 1 / 32 samples
Append frame 4
AIV_RENDER_DONE out=/out/scene_0_5.mp4
"""

_STDERR_FAIL = """\
Error: Cannot read file '/assets/characters/hero.blend': No such file or directory
"""


def test_parse_progress_and_frames():
    assert output_parser.parse_progress(_STDOUT_OK) == 4
    assert output_parser.parse_rendered_frames(_STDOUT_OK) == 4


def test_parse_frames_falls_back_to_saved_lines():
    stdout = "Saved: '/out/0001.png'\nSaved: '/out/0002.png'\n"
    assert output_parser.parse_rendered_frames(stdout) == 2


def test_parse_warnings():
    warnings = output_parser.parse_warnings(_STDOUT_OK)
    assert any("experimental" in w for w in warnings)


def test_build_video_clip_success():
    spec = _spec(duration_s=4.0, fps=30)
    clip = output_parser.build_video_clip(
        spec, "/out/scene_0_5.mp4", _STDOUT_OK, "", 0
    )
    assert isinstance(clip, VideoClip)
    assert clip.path == "/out/scene_0_5.mp4"
    assert clip.scene_id == 5
    assert clip.width == 1080 and clip.height == 1920
    assert clip.provider == "blender-eevee"
    assert clip.meta["frames_rendered"] == 4
    assert clip.meta["frames_expected"] == 120


def test_build_video_clip_cycles_provider():
    spec = _spec(render_engine="cycles")
    clip = output_parser.build_video_clip(spec, "/out/x.mp4", _STDOUT_OK, "", 0)
    assert clip.provider == "blender-cycles"


def test_build_video_clip_raises_actionable_on_failure():
    spec = _spec()
    with pytest.raises(Engine3DUnavailable) as exc:
        output_parser.build_video_clip(spec, "/out/x.mp4", "", _STDERR_FAIL, 1)
    msg = str(exc.value)
    assert "código 1" in msg
    assert "hero.blend" in msg  # el error de Blender queda en el mensaje accionable
    assert "AIV_BLENDER_BIN" in msg or "AIV_BLENDER_ASSETS_DIR" in msg


def test_build_video_clip_raises_when_no_frames_despite_rc0():
    spec = _spec()
    with pytest.raises(Engine3DUnavailable) as exc:
        output_parser.build_video_clip(spec, "/out/x.mp4", "Blender 4.2\n", "", 0)
    assert "no se simula un clip" in str(exc.value)

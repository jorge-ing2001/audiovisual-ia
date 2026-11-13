"""Generador del script de Blender (FEAT-302): determinismo y contenido del MVP.

Lógica pura: no requiere Blender ni ``bpy``. Verifica que ``build_blender_script`` es
determinista y que el texto contiene el personaje/set resueltos, el rango de frames
correcto, el motor EEVEE, la resolución vertical 1080x1920 y la ruta de salida ``.mp4``.
"""

from __future__ import annotations

from app.engines.engine3d.assets import ResolvedAsset, ResolvedAssets
from app.engines.engine3d.script_builder import (
    build_blender_script,
    frame_count,
    output_filename,
)
from app.engines.engine3d.types import CameraSpec, Scene3DSpec


def _resolved():
    return ResolvedAssets(
        character=ResolvedAsset("character", "hero", "/assets/characters/hero.blend", True),
        animation=ResolvedAsset("animation", "walk", "/assets/animations/walk.fbx", True),
        set=ResolvedAsset("set", "room_interior", "/assets/sets/room_interior.blend", True),
    )


def _spec(**over):
    base = dict(
        scene_id=3,
        order=1,
        duration_s=4.0,
        character_id="hero",
        character_animation="walk",
        set_id="room_interior",
        camera=CameraSpec(shot="wide", move="dolly", focal_mm=35.0, dof=True),
        style_preset="stylized",
        width=1080,
        height=1920,
        fps=30,
        render_engine="eevee",
        seed=None,
    )
    base.update(over)
    return Scene3DSpec(**base)


def test_frame_count_round():
    assert frame_count(4.0, 30) == 120
    assert frame_count(2.5, 30) == 75
    assert frame_count(0.0, 30) == 1  # mínimo 1 frame


def test_build_is_deterministic():
    spec = _spec()
    resolved = _resolved()
    a = build_blender_script(spec, resolved)
    b = build_blender_script(spec, resolved)
    assert a == b
    assert isinstance(a, str) and len(a) > 0


def test_script_contains_resolved_assets():
    script = build_blender_script(_spec(), _resolved())
    assert "/assets/characters/hero.blend" in script
    assert "/assets/sets/room_interior.blend" in script
    assert "/assets/animations/walk.fbx" in script
    assert "'hero'" in script
    assert "'room_interior'" in script


def test_script_contains_frame_range_engine_resolution_and_output():
    spec = _spec(duration_s=4.0, fps=30)
    script = build_blender_script(spec, _resolved())
    # Rango de frames = round(duration_s * fps) = 120.
    assert "FRAME_COUNT = 120" in script
    # Motor EEVEE por defecto (id de Blender 4.x).
    assert "BLENDER_EEVEE_NEXT" in script
    # Resolución vertical 1080x1920.
    assert "WIDTH = 1080" in script
    assert "HEIGHT = 1920" in script
    assert "resolution_x = WIDTH" in script
    assert "resolution_y = HEIGHT" in script
    # Salida .mp4 determinista.
    assert output_filename(spec) == "scene_1_3.mp4"
    assert "scene_1_3.mp4" in script
    assert "FFMPEG" in script and "MPEG4" in script


def test_script_uses_cycles_when_requested():
    script = build_blender_script(_spec(render_engine="cycles"), _resolved())
    assert "'CYCLES'" in script
    assert "cycles.samples = SAMPLES" in script


def test_script_contains_cinematic_lighting_and_camera():
    script = build_blender_script(_spec(), _resolved())
    assert "KeyLight" in script
    assert "FillLight" in script
    assert "RimLight" in script
    assert "CAMERA_SHOT = 'wide'" in script
    assert "CAMERA_MOVE = 'dolly'" in script

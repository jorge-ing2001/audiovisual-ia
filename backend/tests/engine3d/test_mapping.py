"""Mapeo guion -> escena 3D (FEAT-302): camera_notes, heurística de animación, duración.

Lógica pura: no requiere Blender. Usa objetos ligeros (no ORM) para la Scene/Project con
los atributos que ``scene_to_spec`` consume (duck typing).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import app.config as config
from app.config import Settings
from app.engines.engine3d import mapping
from app.engines.engine3d.types import CameraSpec, Scene3DSpec


@dataclass
class FakeAsset:
    kind: str
    meta: dict = field(default_factory=dict)


@dataclass
class FakeScene:
    id: int = 1
    order: int = 0
    narration_text: str = ""
    visual_prompt: str = ""
    duration_s: float = 0.0
    camera_notes: str | None = None
    assets: list = field(default_factory=list)


@dataclass
class FakeProject:
    id: int = 1
    production_type: str = "3d"
    aspect_ratio: str = "9:16"


@pytest.fixture()
def settings(monkeypatch):
    config._settings = None
    for var in ("AIV_RENDER_ENGINE_3D", "AIV_FPS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    yield s
    config._settings = None


# --- camera_notes -> CameraSpec ----------------------------------------------------- #


@pytest.mark.parametrize(
    "notes,expected_shot,expected_move",
    [
        ("primer plano del rostro", "closeup", "static"),
        ("plano general con travelling", "wide", "dolly"),
        ("toma aérea con órbita", "aerial", "orbit"),
        ("contrapicado, zoom in", "low_angle", "zoom_in"),
        ("over shoulder, zoom out", "over_shoulder", "zoom_out"),
    ],
)
def test_camera_from_notes_known(notes, expected_shot, expected_move):
    cam = mapping.camera_from_notes(notes)
    assert cam.shot == expected_shot
    assert cam.move == expected_move
    assert cam.dof is True


def test_camera_from_notes_defaults_on_unknown():
    cam = mapping.camera_from_notes("texto sin pistas de cámara reconocibles")
    assert cam == CameraSpec(shot="medium", move="static", focal_mm=50.0, dof=True)
    # None también cae en defaults seguros.
    assert mapping.camera_from_notes(None).shot == "medium"


# --- heurística de animación -------------------------------------------------------- #


@pytest.mark.parametrize(
    "narration,expected",
    [
        ("el personaje camina por la calle", "walk"),
        ("the hero walks forward", "walk"),
        ("ella señala el horizonte", "point"),
        ("gira la cabeza sorprendido", "turn_head"),
        ("reacciona con asombro", "react"),
        ("el personaje habla tranquilo", "idle"),
    ],
)
def test_animation_from_text(narration, expected):
    assert mapping.animation_from_text(narration) == expected


# --- set por defecto / exterior ----------------------------------------------------- #


def test_set_from_text_default_and_outdoor():
    assert mapping.set_from_text("una conversación en una habitación") == "room_interior"
    assert mapping.set_from_text("caminando por la calle de la ciudad") == "outdoor_street"


# --- scene_to_spec end to end ------------------------------------------------------- #


def test_scene_to_spec_dimensions_and_defaults(settings):
    scene = FakeScene(
        id=7,
        order=2,
        narration_text="el personaje camina por la avenida",
        camera_notes="plano general con travelling",
        duration_s=4.0,
    )
    spec = mapping.scene_to_spec(scene, FakeProject(), settings=settings)
    assert isinstance(spec, Scene3DSpec)
    assert spec.width == 1080
    assert spec.height == 1920
    assert spec.fps == settings.fps
    assert spec.render_engine == "eevee"
    assert spec.style_preset == "stylized"
    assert spec.character_id == "hero"
    assert spec.character_animation == "walk"
    assert spec.set_id == "outdoor_street"
    assert spec.camera.shot == "wide"
    assert spec.camera.move == "dolly"
    assert spec.scene_id == 7
    assert spec.order == 2


def test_scene_to_spec_duration_prefers_voice(settings):
    # Sin voz: usa Scene.duration_s.
    scene = FakeScene(duration_s=3.5)
    spec = mapping.scene_to_spec(scene, FakeProject(), settings=settings)
    assert spec.duration_s == 3.5

    # Con voz: la duración de la voz tiene prioridad sobre Scene.duration_s.
    scene_with_voice = FakeScene(
        duration_s=3.5,
        assets=[FakeAsset(kind="voice", meta={"duration_s": 5.2})],
    )
    spec2 = mapping.scene_to_spec(scene_with_voice, FakeProject(), settings=settings)
    assert spec2.duration_s == 5.2


def test_scene_to_spec_render_engine_from_settings(monkeypatch):
    config._settings = None
    monkeypatch.setenv("AIV_RENDER_ENGINE_3D", "cycles")
    s = Settings()
    spec = mapping.scene_to_spec(FakeScene(duration_s=2.0), FakeProject(), settings=s)
    assert spec.render_engine == "cycles"
    config._settings = None

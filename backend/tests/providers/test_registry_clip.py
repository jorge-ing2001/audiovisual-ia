"""Capacidad 'clip' en el registry (FEAT-303): default 'blender', stubs de pago y fakes.

Verifica que:
  - ``active_backend('clip')`` es ``'blender'`` por defecto y ``resolve('clip')`` devuelve
    el proveedor Blender (sin requerir Blender instalado: solo se construye el adaptador).
  - Las capacidades existentes (llm/image/tts/asr) siguen resolviendo sin cambios.
  - Los stubs de pago (veo/runway/kling) están DECLARADOS y lanzan ``NotImplementedError``
    al construirse (vía de pago preservada).
  - ``FakeClip`` solo se resuelve con ``AIV_CLIP_BACKEND=fake`` y nunca es el default.
"""

from __future__ import annotations

import pytest

import app.config as config
from app.providers import registry
from app.providers.base import ClipProvider
from app.providers.clip.blender import BlenderClipProvider
from app.providers.fakes import FakeClip


@pytest.fixture()
def clean_settings(monkeypatch):
    """Resetea la cache de settings para que las env vars surtan efecto."""
    config._settings = None
    yield
    config._settings = None


def test_clip_in_capabilities():
    assert "clip" in registry.CAPABILITIES


def test_active_backend_clip_default_is_blender(clean_settings, monkeypatch):
    monkeypatch.delenv("AIV_CLIP_BACKEND", raising=False)
    assert registry.active_backend("clip") == "blender"


def test_resolve_clip_returns_blender_provider(clean_settings, monkeypatch):
    monkeypatch.delenv("AIV_CLIP_BACKEND", raising=False)
    provider = registry.resolve("clip")
    assert isinstance(provider, BlenderClipProvider)
    assert isinstance(provider, ClipProvider)
    assert provider.name == "blender"


def test_existing_capabilities_still_resolve(clean_settings, monkeypatch):
    # Las capacidades del MVP 2D siguen funcionando sin cambios.
    monkeypatch.setenv("AIV_LLM_BACKEND", "ollama")
    monkeypatch.setenv("AIV_IMAGE_BACKEND", "placeholder")
    monkeypatch.setenv("AIV_TTS_BACKEND", "kokoro")
    monkeypatch.setenv("AIV_ASR_BACKEND", "faster_whisper")
    assert registry.resolve("llm").name == "ollama"
    assert registry.resolve("image").name == "placeholder"
    assert registry.resolve("tts").name == "kokoro"
    assert registry.resolve("asr").name == "faster_whisper"


@pytest.mark.parametrize("backend", ["veo", "runway", "kling"])
def test_paid_clip_stubs_raise_not_implemented(clean_settings, monkeypatch, backend):
    # Los stubs de pago están registrados (vía conmutable preservada) pero construirlos
    # lanza NotImplementedError hasta que se implementen (Fase 4).
    monkeypatch.setenv("AIV_CLIP_BACKEND", backend)
    assert registry.active_backend("clip") == backend
    with pytest.raises(NotImplementedError):
        registry.resolve("clip")


def test_fake_clip_only_with_env_and_never_default(clean_settings, monkeypatch):
    # Nunca es el default...
    monkeypatch.delenv("AIV_CLIP_BACKEND", raising=False)
    assert registry.active_backend("clip") != "fake"
    # ...pero se puede seleccionar explícitamente para tests.
    monkeypatch.setenv("AIV_CLIP_BACKEND", "fake")
    config._settings = None  # releer settings tras cambiar la env var
    provider = registry.resolve("clip")
    assert isinstance(provider, FakeClip)
    assert isinstance(provider, ClipProvider)


def test_fake_clip_produces_valid_video_clip_without_blender(tmp_path):
    from app.engines.engine3d.types import CameraSpec, Scene3DSpec, VideoClip

    spec = Scene3DSpec(
        scene_id=3,
        order=1,
        duration_s=2.5,
        character_id="hero",
        character_animation="idle",
        set_id="room_interior",
        camera=CameraSpec(),
    )
    clip = FakeClip().clip_for(spec, str(tmp_path))
    assert isinstance(clip, VideoClip)
    assert clip.scene_id == 3
    assert clip.provider == "fake"
    assert clip.meta.get("fake") is True

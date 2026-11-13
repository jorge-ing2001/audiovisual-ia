"""Configuración del motor 3D / Blender (FEAT-301).

Verifica los defaults de las settings AIV_* del motor 3D y que una variable de
entorno (AIV_RENDER_ENGINE_3D=cycles) los sobreescribe, siguiendo el mismo patrón
que los tests de config/registry existentes (reset de la cache de settings).
"""

from __future__ import annotations

import pytest

import app.config as config
from app.config import Settings


@pytest.fixture()
def clean_settings(monkeypatch):
    """Resetea la cache de settings para que las env vars surtan efecto."""
    config._settings = None
    yield
    config._settings = None


def test_engine3d_defaults(clean_settings, monkeypatch):
    # Sin ninguna env var del motor 3D, el sistema sigue siendo el MVP 2D actual.
    for var in (
        "AIV_CLIP_BACKEND",
        "AIV_BLENDER_BIN",
        "AIV_RENDER_ENGINE_3D",
        "AIV_BLENDER_ASSETS_DIR",
        "AIV_BLENDER_SAMPLES",
        "AIV_BLENDER_TIMEOUT_S",
        "AIV_PRODUCTION_TYPE",
    ):
        monkeypatch.delenv(var, raising=False)

    s = Settings()
    assert s.clip_backend == "blender"
    assert s.blender_bin == "blender"
    assert s.render_engine_3d == "eevee"
    assert s.blender_assets_dir == ""
    assert s.blender_samples == 32
    assert s.blender_timeout_s == 3600
    assert s.production_type == "2d"


def test_render_engine_3d_override_by_env(clean_settings, monkeypatch):
    # AIV_RENDER_ENGINE_3D=cycles cambia el motor de render (eevee -> cycles).
    monkeypatch.setenv("AIV_RENDER_ENGINE_3D", "cycles")
    s = Settings()
    assert s.render_engine_3d == "cycles"
    # El resto de defaults no se ven afectados.
    assert s.clip_backend == "blender"
    assert s.production_type == "2d"


def test_production_type_override_by_env(clean_settings, monkeypatch):
    # El default global de production_type puede ponerse a 3d por env var.
    monkeypatch.setenv("AIV_PRODUCTION_TYPE", "3d")
    s = Settings()
    assert s.production_type == "3d"

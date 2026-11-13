"""Catálogo de assets 3D (FEAT-302): nombres estables y detección de asset faltante.

Lógica pura (salvo ``os.path.exists``): no requiere Blender. Verifica que el catálogo
expone los nombres esperados y que ``resolve_assets`` detecta rutas ausentes con un
mensaje accionable (nunca simula un asset).
"""

from __future__ import annotations

import pytest

import app.config as config
from app.config import Settings
from app.engines.engine3d import assets


@pytest.fixture()
def assets_settings(tmp_path, monkeypatch):
    """Settings con AIV_BLENDER_ASSETS_DIR apuntando a un tmp vacío (cache limpia)."""
    config._settings = None
    monkeypatch.setenv("AIV_BLENDER_ASSETS_DIR", str(tmp_path))
    settings = Settings()
    yield settings, tmp_path
    config._settings = None


def test_catalog_lists_expected_names():
    assert assets.CHARACTER_ID == "hero"
    assert assets.ANIMATIONS == ("idle", "walk", "turn_head", "point", "react")
    assert assets.SETS == ("room_interior", "outdoor_street")
    assert assets.DEFAULT_SET == "room_interior"


def test_assets_dir_defaults_to_media_assets3d(monkeypatch):
    config._settings = None
    monkeypatch.delenv("AIV_BLENDER_ASSETS_DIR", raising=False)
    monkeypatch.setenv("AIV_MEDIA_ROOT", "/tmp/aiv-media-test")
    settings = Settings()
    assert assets.assets_dir(settings).name == "assets3d"
    assert str(assets.assets_dir(settings)).endswith("aiv-media-test/assets3d")
    config._settings = None


def test_resolve_detects_missing_assets(assets_settings):
    settings, _ = assets_settings
    resolved = assets.resolve_assets("hero", "walk", "room_interior", settings=settings)
    # Nada existe en el tmp vacío: los tres faltan y complete es False.
    assert not resolved.complete
    missing = assets.missing_assets(resolved)
    kinds = {m.kind for m in missing}
    assert kinds == {"character", "animation", "set"}
    # El mensaje es accionable (menciona el asset y AIV_BLENDER_ASSETS_DIR).
    msg = missing[0].message()
    assert "AIV_BLENDER_ASSETS_DIR" in msg


def test_resolve_marks_present_asset(assets_settings):
    settings, tmp_path = assets_settings
    # Crear el fichero del set para que exista.
    set_file = assets.set_path("room_interior", settings=settings)
    set_file.parent.mkdir(parents=True, exist_ok=True)
    set_file.write_text("fake-blend", encoding="utf-8")

    resolved = assets.resolve_assets("hero", "walk", "room_interior", settings=settings)
    assert resolved.set.exists is True
    assert resolved.character.exists is False
    # Solo faltan personaje y animación.
    assert {m.kind for m in resolved.missing} == {"character", "animation"}


def test_resolve_rejects_unknown_animation_or_set(assets_settings):
    settings, _ = assets_settings
    with pytest.raises(ValueError):
        assets.resolve_assets("hero", "fly", "room_interior", settings=settings)
    with pytest.raises(ValueError):
        assets.resolve_assets("hero", "walk", "moon_base", settings=settings)

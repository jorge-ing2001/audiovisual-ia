"""Motor visual 2D: creación de Asset y cache por hash (FEAT-003).

Registra un ImageProvider contador bajo un backend de test y verifica que la
segunda generación con la misma entrada reutiliza el Asset cacheado SIN volver a
llamar al proveedor (cumple §8: no regenerar innecesariamente).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engines.visual2d import VisualEngine
from app.models.entities import Asset, Project, Scene
from app.providers import registry
from app.providers.base import ImageAsset
from app.storage.db import session_scope


class _CountingImageProvider:
    """ImageProvider de test que cuenta llamadas y escribe un PNG mínimo real."""

    name = "counting"
    calls = 0

    def image_for(self, query, *, aspect, out_dir, seed=None):
        type(self).calls += 1
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        path = Path(out_dir) / f"counting_{type(self).calls}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        return ImageAsset(
            path=str(path),
            width=1920,
            height=1080,
            provider=self.name,
            query=query,
            seed=seed,
            meta={"source": "counting"},
        )


@pytest.fixture()
def counting_backend(db_ready, monkeypatch):
    """Registra el backend 'counting' como ImageProvider activo."""
    _CountingImageProvider.calls = 0
    registry.register("image", "counting", _CountingImageProvider)
    monkeypatch.setenv("AIV_IMAGE_BACKEND", "counting")
    import app.config as config

    config._settings = None
    yield _CountingImageProvider
    registry.PROVIDERS["image"].pop("counting", None)
    config._settings = None


def _make_scene(session):
    project = Project(name="P", prompt="p", aspect_ratio="16:9")
    session.add(project)
    session.flush()
    scene = Scene(project_id=project.id, order=0, visual_prompt="un bosque de niebla")
    session.add(scene)
    session.flush()
    return project.id, scene.id


def test_creates_image_asset(counting_backend):
    with session_scope() as session:
        _, scene_id = _make_scene(session)
        scene = session.get(Scene, scene_id)
        asset = VisualEngine().render_scene_image(scene, session)
        asset_id = asset.id

    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        assert asset.kind == "image"
        assert asset.provider == "counting"
        assert asset.scene_id == scene_id
        assert "cache_key" in asset.meta
        assert Path(asset.path).is_file()
    assert counting_backend.calls == 1


def test_cache_avoids_second_provider_call(counting_backend):
    with session_scope() as session:
        _, scene_id = _make_scene(session)

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        first = VisualEngine().render_scene_image(scene, session)
        first_id = first.id

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        second = VisualEngine().render_scene_image(scene, session)
        second_id = second.id

    # Mismo Asset reutilizado y el proveedor solo se llamó una vez.
    assert first_id == second_id
    assert counting_backend.calls == 1


def test_force_regenerates(counting_backend):
    with session_scope() as session:
        _, scene_id = _make_scene(session)

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        VisualEngine().render_scene_image(scene, session)

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        VisualEngine().render_scene_image(scene, session, force=True)

    assert counting_backend.calls == 2

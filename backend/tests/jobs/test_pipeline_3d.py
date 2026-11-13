"""Pipeline 3D: bifurcación de la fuente visual por ``production_type`` (FEAT-304).

Prueba de FORMA (sin Blender ni Chromium): con ``production_type='3d'`` y el proveedor
``FakeClip`` (``AIV_CLIP_BACKEND=fake`` / override por proyecto), ``_generate_scene_assets``
crea un ``Asset(kind='video')`` por escena y NO llama a ``VisualEngine``; con
``production_type='2d'`` sigue creando ``Asset(kind='image')`` como hoy. Si el motor 3D no
está disponible (``Engine3DUnavailable``), el pipeline propaga la excepción (no simula).
"""

from __future__ import annotations

import pytest

from app.jobs.pipeline import _generate_scene_assets, _Progress
from app.models.entities import Asset, Project, Scene
from app.storage.db import session_scope


def _seed_scene(session, project: Project, *, order: int = 0) -> Scene:
    scene = Scene(
        project_id=project.id, order=order,
        narration_text="Un héroe camina por la calle.", visual_prompt="un héroe 3D",
        duration_s=2.0, camera_notes="plano medio",
    )
    session.add(scene)
    session.flush()
    return scene


def _no_voice(monkeypatch):
    """Neutraliza la síntesis de voz: aquí solo interesa la fuente visual."""
    import app.jobs.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_synthesize_scene_voice", lambda *a, **k: None)


def test_3d_creates_video_asset_with_fake_clip(db_ready, monkeypatch):
    """production_type='3d' + FakeClip: un Asset(kind='video') por escena, sin VisualEngine."""
    _no_voice(monkeypatch)

    # Si el pipeline intentara usar el VisualEngine (ruta 2d), lo delataría.
    import app.jobs.pipeline as pipeline

    class _BoomVisual:
        def __init__(self, *a, **k):
            raise AssertionError("no debe instanciar VisualEngine en producción 3d")

    monkeypatch.setattr(pipeline, "VisualEngine", _BoomVisual)

    with session_scope() as session:
        project = Project(
            name="3d", prompt="x", production_type="3d",
            provider_overrides={"clip": "fake"},
        )
        session.add(project)
        session.flush()
        _seed_scene(session, project, order=0)
        _seed_scene(session, project, order=1)
        session.commit()
        pid = project.id

    with session_scope() as session:
        project = session.get(Project, pid)
        _generate_scene_assets(project, session, {}, _Progress(job_id=0))

        videos = [a for a in project.assets if a.kind == "video"]
        images = [a for a in project.assets if a.kind == "image"]
        assert len(videos) == 2
        assert images == []
        for asset in videos:
            assert asset.provider == "fake"
            assert asset.scene_id is not None
            assert (asset.meta or {}).get("fake") is True


def test_2d_creates_image_asset_as_before(db_ready, monkeypatch):
    """production_type='2d': comportamiento idéntico al actual (Asset kind='image')."""
    _no_voice(monkeypatch)

    import app.jobs.pipeline as pipeline

    rendered: list[int] = []

    class _FakeVisual:
        def render_scene_image(self, scene, session, *, style=None):
            rendered.append(scene.id)
            asset = Asset(
                project_id=scene.project_id, scene_id=scene.id, kind="image",
                path=f"/tmp/fake_{scene.id}.png", provider="placeholder",
            )
            session.add(asset)
            session.commit()

    monkeypatch.setattr(pipeline, "VisualEngine", _FakeVisual)

    with session_scope() as session:
        project = Project(name="2d", prompt="x", production_type="2d")
        session.add(project)
        session.flush()
        _seed_scene(session, project, order=0)
        session.commit()
        pid = project.id

    with session_scope() as session:
        project = session.get(Project, pid)
        _generate_scene_assets(project, session, {}, _Progress(job_id=0))

        images = [a for a in project.assets if a.kind == "image"]
        videos = [a for a in project.assets if a.kind == "video"]
        assert len(images) == 1
        assert videos == []
        assert rendered  # VisualEngine SÍ se usó en 2d


def test_3d_propagates_engine3d_unavailable(db_ready, monkeypatch):
    """Si el proveedor 'clip' no puede renderizar, se propaga Engine3DUnavailable (no simula)."""
    _no_voice(monkeypatch)

    from app.engines.engine3d import Engine3DUnavailable
    from app.providers import registry

    class _UnavailableClip:
        name = "blender"

        def clip_for(self, scene_spec, out_dir):
            raise Engine3DUnavailable("Blender no disponible (AIV_BLENDER_BIN).")

    monkeypatch.setattr(registry, "resolve", lambda cap, overrides=None: _UnavailableClip())

    with session_scope() as session:
        project = Project(name="3d-down", prompt="x", production_type="3d")
        session.add(project)
        session.flush()
        _seed_scene(session, project, order=0)
        session.commit()
        pid = project.id

    with session_scope() as session:
        project = session.get(Project, pid)
        with pytest.raises(Engine3DUnavailable):
            _generate_scene_assets(project, session, {}, _Progress(job_id=0))
        # No se persistió ningún clip simulado.
        assert [a for a in project.assets if a.kind == "video"] == []

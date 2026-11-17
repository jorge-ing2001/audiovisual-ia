"""Administración de proyectos (Bloque 4, FEAT-002).

Cubre: plan de duplicación PURO, duplicación completa vía API, fallo en copia con
rollback + limpieza, filtros de búsqueda (y regresión sin parámetros), borrado seguro
(fuera de media_root, symlink que apunta fuera, raíz, operaciones activas) y migración
idempotente repetida.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _seed_project_with_media(client, name="Origen"):
    """Crea un proyecto con una escena, un asset ligado a la escena, un timeline con una
    ruta de recurso embebida y ficheros reales en el workspace. Devuelve el dict del
    proyecto creado.
    """
    from app.models.entities import Asset, Scene, Timeline
    from app.storage.db import session_scope
    from app.storage.media import ensure_project_workspace, project_dir

    resp = client.post("/projects", json={"name": name, "prompt": "un gato astronauta"})
    assert resp.status_code == 201, resp.text
    project = resp.json()
    pid = project["id"]

    ensure_project_workspace(pid)
    base = project_dir(pid)
    img = base / "images" / "scene_1.png"
    img.write_bytes(b"\x89PNG-fake-image")
    voice = base / "voice" / "scene_1.wav"
    voice.write_bytes(b"RIFF-fake-wav")

    with session_scope() as session:
        scene = Scene(project_id=pid, order=0, narration_text="hola", visual_prompt="gato")
        session.add(scene)
        session.flush()
        scene_id = scene.id
        session.add(
            Asset(project_id=pid, scene_id=scene_id, kind="image", path=str(img))
        )
        session.add(
            Asset(project_id=pid, scene_id=scene_id, kind="voice", path=str(voice))
        )
        # Timeline con una ruta embebida que incrusta project_{pid}.
        session.add(
            Timeline(
                project_id=pid,
                json={
                    "kind": "generation",
                    "scenes": [{"image": str(img), "audio": str(voice)}],
                },
                version=1,
            )
        )

    return project, scene_id


# --------------------------------------------------------------------------- #
# (a) Plan de duplicación PURO (sin disco)
# --------------------------------------------------------------------------- #


def test_duplication_plan_pure(temp_env):
    """build_duplication_plan re-mapea scene_id y rutas project_{old}->project_{new} sin E/S."""
    from app.engines.admin.duplicate import build_duplication_plan
    from app.models.entities import Asset, Project, Scene, Timeline
    from app.storage.media import project_dir

    old_id, new_id = 7, 42
    old_dir = project_dir(old_id).as_posix()
    new_dir = project_dir(new_id).as_posix()

    project = Project(id=old_id, name="Origen", prompt="p")
    scene = Scene(id=100, project_id=old_id, order=0, narration_text="n", visual_prompt="v")
    asset = Asset(
        id=200,
        project_id=old_id,
        scene_id=100,
        kind="image",
        path=f"{old_dir}/images/a.png",
    )
    asset_unlinked = Asset(
        id=201, project_id=old_id, scene_id=None, kind="music", path=f"{old_dir}/music/m.mp3"
    )
    timeline = Timeline(
        id=300,
        project_id=old_id,
        json={"scenes": [{"image": f"{old_dir}/images/a.png"}]},
        version=1,
    )

    plan = build_duplication_plan(
        project, [scene], [asset, asset_unlinked], [timeline], [], new_id
    )

    # El asset ligado conserva la referencia a la escena ORIGEN (se re-mapea en ejecución).
    assert plan.assets[0].scene_ref == 100
    assert plan.assets[1].scene_ref is None
    # Rutas reescritas al prefijo nuevo.
    assert plan.assets[0].fields["path"] == f"{new_dir}/images/a.png"
    assert plan.assets[1].fields["path"] == f"{new_dir}/music/m.mp3"
    # JSON del timeline reescrito.
    assert plan.timelines[0].fields["json"]["scenes"][0]["image"] == f"{new_dir}/images/a.png"
    # path_map incluye ambos ficheros de assets (src->dst) y la ruta del timeline.
    srcs = {src for src, _ in plan.path_map}
    assert f"{old_dir}/images/a.png" in srcs
    assert f"{old_dir}/music/m.mp3" in srcs
    for src, dst in plan.path_map:
        assert src.startswith(old_dir)
        assert dst.startswith(new_dir)


# --------------------------------------------------------------------------- #
# (b) Duplicación completa vía API
# --------------------------------------------------------------------------- #


def test_duplicate_complete(client):
    from app.models.entities import Asset, Scene
    from app.storage.db import session_scope
    from app.storage.media import project_dir

    project, old_scene_id = _seed_project_with_media(client)
    old_id = project["id"]

    resp = client.post(f"/projects/{old_id}/duplicate", json={"name": "Copia"})
    assert resp.status_code == 201, resp.text
    new_project = resp.json()
    new_id = new_project["id"]

    # Ids NUEVOS.
    assert new_id != old_id
    assert new_project["name"] == "Copia"
    assert new_project["status"] == "draft"

    with session_scope() as session:
        new_scenes = list(
            session.query(Scene).filter(Scene.project_id == new_id).all()
        )
        new_assets = list(
            session.query(Asset).filter(Asset.project_id == new_id).all()
        )
        old_assets = list(
            session.query(Asset).filter(Asset.project_id == old_id).all()
        )

        # Escena nueva con id distinto.
        assert len(new_scenes) == 1
        new_scene_id = new_scenes[0].id
        assert new_scene_id != old_scene_id

        # Relaciones preservadas: scene_id de los assets apunta a la escena NUEVA.
        assert len(new_assets) == 2
        for a in new_assets:
            assert a.scene_id == new_scene_id
            # Asset.path dentro de project_{new} y existente en disco.
            assert f"project_{new_id}" in a.path
            assert Path(a.path).is_file()

        # El origen queda intacto en disco y BD.
        for a in old_assets:
            assert f"project_{old_id}" in a.path
            assert Path(a.path).is_file()

    new_base = project_dir(new_id)
    assert (new_base / "images" / "scene_1.png").is_file()
    assert (new_base / "voice" / "scene_1.wav").is_file()


# --------------------------------------------------------------------------- #
# (c) Fallo durante la copia -> rollback BD + limpieza del workspace parcial
# --------------------------------------------------------------------------- #


def test_copy_failure_rollback(client, monkeypatch):
    from app.models.entities import Asset, Project, Scene
    from app.storage.db import session_scope
    from app.storage.media import project_dir

    project, _ = _seed_project_with_media(client)
    old_id = project["id"]

    with session_scope() as session:
        before_projects = session.query(Project).count()
        before_scenes = session.query(Scene).count()
        before_assets = session.query(Asset).count()

    import shutil as real_shutil

    def _boom(src, dst):  # copy2 que falla a mitad
        raise OSError("fallo simulado de copia")

    monkeypatch.setattr(real_shutil, "copy2", _boom)

    resp = client.post(f"/projects/{old_id}/duplicate", json={"name": "Copia fallida"})
    assert resp.status_code >= 500

    with session_scope() as session:
        # Ningún Project/Scene/Asset nuevo persiste (rollback).
        assert session.query(Project).count() == before_projects
        assert session.query(Scene).count() == before_scenes
        assert session.query(Asset).count() == before_assets
        # El origen sigue existiendo.
        assert session.get(Project, old_id) is not None

    # El workspace del proyecto nuevo (id = old+1) NO queda en disco.
    assert not project_dir(old_id + 1).exists()


# --------------------------------------------------------------------------- #
# (b2) Modo job con seguimiento de estado (proyectos grandes)
# --------------------------------------------------------------------------- #


def test_duplicate_via_job(client, monkeypatch):
    """Por encima del umbral, la duplicación se encola como Job 'duplicate_project',
    reporta progress y, al completarse, deja un proyecto nuevo con relaciones/ficheros.
    """
    import asyncio

    import app.config as config
    from app.api import projects as projects_api
    from app.jobs.queue import JobQueue
    from app.jobs.worker import run_job_to_completion
    from app.models.entities import Asset
    from app.storage.db import session_scope
    from app.storage.media import project_dir

    project, scene_id = _seed_project_with_media(client)
    old_id = project["id"]

    # Umbral 0 -> siempre vía job. Se resetea la cache de config y el router lee en caliente.
    config._settings = None
    monkeypatch.setenv("AIV_DUPLICATE_JOB_ASSET_THRESHOLD", "0")
    monkeypatch.setattr(projects_api, "get_settings", lambda: config.Settings())

    resp = client.post(f"/projects/{old_id}/duplicate", json={"name": "Copia job"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # En modo job la respuesta es un JobRead.
    assert body["type"] == "duplicate_project"
    assert body["status"] == "pending"
    job_id = body["id"]

    # Ejecuta el worker hasta completar el job.
    asyncio.run(run_job_to_completion(JobQueue()))

    job = JobQueue().get(job_id)
    assert job.status == "succeeded", job.error
    assert job.progress == 1.0
    new_id = job.result["project_id"]
    assert new_id != old_id

    # Proyecto nuevo con relaciones preservadas y ficheros copiados.
    with session_scope() as session:
        new_assets = list(session.query(Asset).filter(Asset.project_id == new_id).all())
        assert len(new_assets) == 2
        for a in new_assets:
            assert f"project_{new_id}" in a.path
            assert Path(a.path).is_file()
    assert project_dir(new_id).exists()

    config._settings = None


# --------------------------------------------------------------------------- #
# (d) Filtros q/tag/folder + sin params = todo (regresión)
# --------------------------------------------------------------------------- #


def test_filters_regression(client):
    a = client.post(
        "/projects", json={"name": "Alpha gato", "prompt": "uno"}
    ).json()
    b = client.post(
        "/projects", json={"name": "Beta perro", "prompt": "dos"}
    ).json()
    c = client.post(
        "/projects", json={"name": "Gamma", "prompt": "tres"}
    ).json()

    client.patch(f"/projects/{a['id']}", json={"folder": "F1", "tags": ["animal", "gato"]})
    client.patch(f"/projects/{b['id']}", json={"folder": "F1", "tags": ["animal"]})
    client.patch(f"/projects/{c['id']}", json={"folder": "F2", "tags": []})

    # Sin parámetros -> todo (regresión).
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert {p["id"] for p in resp.json()} == {a["id"], b["id"], c["id"]}

    # q sobre name/prompt (case-insensitive).
    resp = client.get("/projects", params={"q": "gato"})
    assert {p["id"] for p in resp.json()} == {a["id"]}
    resp = client.get("/projects", params={"q": "DOS"})
    assert {p["id"] for p in resp.json()} == {b["id"]}

    # folder (igualdad).
    resp = client.get("/projects", params={"folder": "F1"})
    assert {p["id"] for p in resp.json()} == {a["id"], b["id"]}

    # tag (contención).
    resp = client.get("/projects", params={"tag": "gato"})
    assert {p["id"] for p in resp.json()} == {a["id"]}
    resp = client.get("/projects", params={"tag": "animal"})
    assert {p["id"] for p in resp.json()} == {a["id"], b["id"]}

    # tags NULL se lee como [] (regresión de proyectos existentes).
    resp = client.get(f"/projects/{c['id']}")
    assert resp.json()["tags"] == []

    # Endpoints auxiliares de tags/folders.
    assert set(client.get("/projects/tags").json()) == {"animal", "gato"}
    assert set(client.get("/projects/folders").json()) == {"F1", "F2"}


def test_patch_tags_must_be_strings(client):
    p = client.post("/projects", json={"name": "P"}).json()
    resp = client.patch(f"/projects/{p['id']}", json={"tags": [1, 2, 3]})
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# (e) Borrado seguro
# --------------------------------------------------------------------------- #


def test_delete_normal_removes_rows_and_workspace(client):
    project, _ = _seed_project_with_media(client)
    pid = project["id"]
    from app.storage.media import project_dir

    assert project_dir(pid).exists()
    resp = client.delete(f"/projects/{pid}")
    assert resp.status_code == 204
    assert client.get(f"/projects/{pid}").status_code == 404
    assert not project_dir(pid).exists()


def test_delete_active_jobs_returns_409(client):
    from app.models.entities import Job
    from app.storage.db import session_scope

    project = client.post("/projects", json={"name": "Con job"}).json()
    pid = project["id"]
    with session_scope() as session:
        session.add(Job(project_id=pid, type="render", status="running"))

    resp = client.delete(f"/projects/{pid}")
    assert resp.status_code == 409
    # No se borró nada.
    assert client.get(f"/projects/{pid}").status_code == 200


def test_delete_outside_media_root_rejected(temp_env, tmp_path):
    """safe_rmtree rechaza un destino fuera de media_root (incl. symlink que apunta fuera)."""
    from app.storage.media import UnsafeRemoveError, media_root, safe_rmtree

    # Directorio fuera de media_root con contenido.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.txt").write_text("no borrar")

    # Ruta directa fuera de media_root.
    with pytest.raises(UnsafeRemoveError):
        safe_rmtree(outside)
    assert outside.exists()

    # Symlink dentro de media_root que apunta fuera -> rechazado, objetivo intacto.
    root = media_root()
    link = root / "project_evil"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeRemoveError):
        safe_rmtree(link)
    assert outside.exists()
    assert (outside / "important.txt").exists()


def test_delete_media_root_itself_rejected(temp_env):
    from app.storage.media import UnsafeRemoveError, media_root, safe_rmtree

    root = media_root()
    with pytest.raises(UnsafeRemoveError):
        safe_rmtree(root)
    assert root.exists()


# --------------------------------------------------------------------------- #
# (f) Migración idempotente repetida
# --------------------------------------------------------------------------- #


def test_migration_repeated(temp_env):
    """Ejecutar la migración 2x sobre una BD sin columnas: sin error, sin duplicados,
    proyectos preexistentes intactos (folder=None, tags=[]).
    """
    from sqlalchemy import text

    from app.models.base import Base
    from app.models.entities import Project
    from app.storage.db import (
        _ensure_project_columns,
        get_engine,
        session_scope,
    )

    engine = get_engine()

    # Crea una tabla 'projects' MÍNIMA sin folder/tags (simula una BD antigua).
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE projects ("
                "id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, "
                "prompt TEXT, production_type VARCHAR(16), aspect_ratio VARCHAR(16), "
                "resolution VARCHAR(16), status VARCHAR(32), provider_overrides JSON, "
                "created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO projects (id, name, prompt, production_type, aspect_ratio, "
                "resolution, status, created_at) VALUES "
                "(1, 'Viejo', 'p', '2d', '16:9', '1080p', 'draft', '2024-01-01 00:00:00')"
            )
        )

    # Primera pasada: añade folder y tags.
    _ensure_project_columns(engine)
    # Segunda pasada: no-op (no debe lanzar 'duplicate column name').
    _ensure_project_columns(engine)

    # El resto de tablas se crean normalmente (y la migración vuelve a ser no-op).
    Base.metadata.create_all(bind=engine)
    _ensure_project_columns(engine)

    with engine.begin() as conn:
        all_names = [row[1] for row in conn.execute(text("PRAGMA table_info('projects')"))]
        # Sin columnas duplicadas: 'folder' y 'tags' aparecen exactamente una vez.
        assert all_names.count("folder") == 1
        assert all_names.count("tags") == 1
        # Un único registro 'Viejo' preservado.
        count = conn.execute(text("SELECT COUNT(*) FROM projects")).scalar_one()
        assert count == 1

    # El proyecto preexistente lee folder=None y tags=[] vía ORM/esquema.
    with session_scope() as session:
        old = session.get(Project, 1)
        assert old is not None
        assert old.folder is None
        assert old.tags is None  # NULL en BD

    from app.models.schemas import ProjectRead

    with session_scope() as session:
        old = session.get(Project, 1)
        read = ProjectRead.model_validate(old)
        assert read.folder is None
        assert read.tags == []

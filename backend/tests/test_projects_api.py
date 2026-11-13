"""CRUD de Project vía TestClient (acceptance_criteria FEAT-002)."""

from __future__ import annotations


def test_project_crud_cycle(client):
    # CREATE
    resp = client.post("/projects", json={"name": "Mi proyecto", "prompt": "un gato astronauta"})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    project_id = created["id"]
    assert created["name"] == "Mi proyecto"
    assert created["production_type"] == "2d"
    assert created["status"] == "draft"

    # LIST
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()] == [project_id]

    # GET
    resp = client.get(f"/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["prompt"] == "un gato astronauta"

    # PATCH
    resp = client.patch(f"/projects/{project_id}", json={"status": "ready", "name": "Renombrado"})
    assert resp.status_code == 200
    patched = resp.json()
    assert patched["status"] == "ready"
    assert patched["name"] == "Renombrado"

    # DELETE
    resp = client.delete(f"/projects/{project_id}")
    assert resp.status_code == 204

    # GET tras borrar -> 404
    resp = client.get(f"/projects/{project_id}")
    assert resp.status_code == 404


def test_create_uses_default_production_type_from_settings(client, monkeypatch):
    """Si no se envía production_type, se usa el default global AIV_PRODUCTION_TYPE."""
    import app.config as config
    from app.api import projects as projects_api

    config._settings = None
    monkeypatch.setenv("AIV_PRODUCTION_TYPE", "3d")
    # get_settings cachea; el router lo consulta en caliente, así que reseteamos la cache.
    monkeypatch.setattr(projects_api, "get_settings", lambda: config.Settings())

    resp = client.post("/projects", json={"name": "proyecto 3d por defecto"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["production_type"] == "3d"
    config._settings = None


def test_create_explicit_production_type_overrides_default(client, monkeypatch):
    """Un production_type explícito siempre manda sobre el default global."""
    import app.config as config
    from app.api import projects as projects_api

    config._settings = None
    monkeypatch.setenv("AIV_PRODUCTION_TYPE", "3d")
    monkeypatch.setattr(projects_api, "get_settings", lambda: config.Settings())

    resp = client.post(
        "/projects", json={"name": "proyecto 2d explícito", "production_type": "2d"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["production_type"] == "2d"
    config._settings = None


def test_get_missing_project_returns_404(client):
    assert client.get("/projects/999").status_code == 404


def test_health_still_ok(client):
    """El /health de FEAT-001 sigue funcionando tras montar los routers."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

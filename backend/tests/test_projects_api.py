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


def test_get_missing_project_returns_404(client):
    assert client.get("/projects/999").status_code == 404


def test_health_still_ok(client):
    """El /health de FEAT-001 sigue funcionando tras montar los routers."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

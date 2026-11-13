"""API del editor: open idempotente, get/put/op persisten + Version, undo/redo."""

from __future__ import annotations


def _make_project_with_scene(client) -> int:
    created = client.post("/projects", json={"name": "editor-api", "prompt": "faro"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    from app.models.entities import Asset, Scene
    from app.storage.db import session_scope

    with session_scope() as session:
        scene = Scene(
            project_id=project_id,
            order=0,
            narration_text="Un faro en la tormenta.",
            visual_prompt="faro",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()
        session.add(
            Asset(
                project_id=project_id,
                scene_id=scene.id,
                kind="image",
                path="/tmp/img.png",
                provider="placeholder",
            )
        )
        session.add(
            Asset(
                project_id=project_id,
                scene_id=scene.id,
                kind="voice",
                path="/tmp/voice.wav",
                provider="kokoro",
                meta={"duration_s": 2.0},
            )
        )
        session.commit()
    return project_id


def _version_rows(project_id: int) -> list:
    from app.engines.editor.store import ENTITY_TYPE
    from app.models.entities import Version
    from app.storage.db import session_scope

    with session_scope() as session:
        from sqlalchemy import select

        stmt = select(Version).where(
            Version.entity_type == ENTITY_TYPE, Version.entity_id == project_id
        )
        return list(session.execute(stmt).scalars())


def test_open_is_idempotent(client):
    project_id = _make_project_with_scene(client)

    first = client.post(f"/projects/{project_id}/editor/open")
    assert first.status_code == 200
    body1 = first.json()
    assert body1["project_id"] == project_id
    assert body1["edit_project"]["kind"] == "edit_project"
    tracks = body1["edit_project"]["tracks"]
    assert any(t["kind"] == "image" and t["clips"] for t in tracks)

    second = client.post(f"/projects/{project_id}/editor/open")
    assert second.status_code == 200
    # Idempotente: mismo estado (no se duplican clips).
    assert second.json()["edit_project"] == body1["edit_project"]
    # Solo una versión (el open inicial), no dos.
    assert len(_version_rows(project_id)) == 1


def test_get_returns_state(client):
    project_id = _make_project_with_scene(client)
    client.post(f"/projects/{project_id}/editor/open")
    got = client.get(f"/projects/{project_id}/editor")
    assert got.status_code == 200
    assert got.json()["project_id"] == project_id


def test_get_before_open_is_404(client):
    project_id = _make_project_with_scene(client)
    assert client.get(f"/projects/{project_id}/editor").status_code == 404


def _first_clip_id(edit_project: dict, kind: str) -> str:
    for track in edit_project["tracks"]:
        if track["kind"] == kind and track["clips"]:
            return track["clips"][0]["id"]
    raise AssertionError(f"no hay clip de tipo {kind}")


def test_op_persists_and_creates_version(client):
    project_id = _make_project_with_scene(client)
    opened = client.post(f"/projects/{project_id}/editor/open").json()
    clip_id = _first_clip_id(opened["edit_project"], "subtitle") if any(
        t["kind"] == "subtitle" and t["clips"] for t in opened["edit_project"]["tracks"]
    ) else None

    # Si no hay subtítulos (sin .srt), edita el gain de la voz.
    if clip_id is None:
        voice_id = _first_clip_id(opened["edit_project"], "voice")
        resp = client.post(
            f"/projects/{project_id}/editor/op",
            json={"op": "set_clip_gain", "params": {"clip_id": voice_id, "gain": 0.4}},
        )
        assert resp.status_code == 200
        updated = resp.json()["edit_project"]
        voice_clip = next(
            c for t in updated["tracks"] if t["kind"] == "voice" for c in t["clips"]
        )
        assert voice_clip["gain"] == 0.4
    else:
        resp = client.post(
            f"/projects/{project_id}/editor/op",
            json={"op": "set_text", "params": {"clip_id": clip_id, "text": "editado"}},
        )
        assert resp.status_code == 200

    # Se persiste (GET lo refleja) y se creó una Version adicional (open + op).
    assert client.get(f"/projects/{project_id}/editor").status_code == 200
    assert len(_version_rows(project_id)) == 2


def test_op_invalid_returns_422(client):
    project_id = _make_project_with_scene(client)
    client.post(f"/projects/{project_id}/editor/open")
    resp = client.post(
        f"/projects/{project_id}/editor/op",
        json={"op": "set_clip_gain", "params": {"clip_id": "ghost", "gain": 0.5}},
    )
    assert resp.status_code == 422


def test_put_replaces_state_and_versions(client):
    project_id = _make_project_with_scene(client)
    opened = client.post(f"/projects/{project_id}/editor/open").json()
    document = opened["edit_project"]
    # Muta la duración de la meta y vuelve a guardar.
    document["meta"]["fps"] = 24
    resp = client.put(f"/projects/{project_id}/editor", json={"edit_project": document})
    assert resp.status_code == 200
    assert resp.json()["edit_project"]["meta"]["fps"] == 24
    assert len(_version_rows(project_id)) == 2


def test_undo_redo_change_active_state(client):
    project_id = _make_project_with_scene(client)
    opened = client.post(f"/projects/{project_id}/editor/open").json()
    voice_id = _first_clip_id(opened["edit_project"], "voice")

    client.post(
        f"/projects/{project_id}/editor/op",
        json={"op": "set_clip_gain", "params": {"clip_id": voice_id, "gain": 0.2}},
    )

    def _gain() -> float:
        state = client.get(f"/projects/{project_id}/editor").json()["edit_project"]
        return next(
            c["gain"] for t in state["tracks"] if t["kind"] == "voice" for c in t["clips"]
        )

    assert _gain() == 0.2

    undo = client.post(f"/projects/{project_id}/editor/undo")
    assert undo.status_code == 200
    assert _gain() == 1.0  # volvió al estado del open

    redo = client.post(f"/projects/{project_id}/editor/redo")
    assert redo.status_code == 200
    assert _gain() == 0.2  # re-aplicado


def test_undo_without_history_conflicts(client):
    project_id = _make_project_with_scene(client)
    client.post(f"/projects/{project_id}/editor/open")
    # Solo el estado inicial: no hay nada que deshacer.
    assert client.post(f"/projects/{project_id}/editor/undo").status_code == 409


def test_history_lists_versions(client):
    project_id = _make_project_with_scene(client)
    opened = client.post(f"/projects/{project_id}/editor/open").json()
    voice_id = _first_clip_id(opened["edit_project"], "voice")
    client.post(
        f"/projects/{project_id}/editor/op",
        json={"op": "set_clip_gain", "params": {"clip_id": voice_id, "gain": 0.3}},
    )
    hist = client.get(f"/projects/{project_id}/editor/history")
    assert hist.status_code == 200
    rows = hist.json()
    assert len(rows) == 2
    assert sum(1 for r in rows if r["active"]) == 1


def test_export_requires_open(client):
    project_id = _make_project_with_scene(client)
    assert client.post(f"/projects/{project_id}/editor/export").status_code == 409
    client.post(f"/projects/{project_id}/editor/open")
    resp = client.post(f"/projects/{project_id}/editor/export")
    assert resp.status_code == 202
    body = resp.json()
    assert body["type"] == "edit_export"
    assert body["status"] == "pending"

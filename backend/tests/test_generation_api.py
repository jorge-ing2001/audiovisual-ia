"""Endpoints de generación: POST /projects/{id}/script e imagen de escena (FEAT-003).

- El guion usa el backend por defecto (ollama) con httpx mockeado -> devuelve >=1
  escena persistida con narration_text/visual_prompt/duration_s.
- La imagen de escena, sin clave de stock ni red, degrada a placeholder y crea un
  Asset con un PNG real en disco (acceptance_criteria FEAT-003).
"""

from __future__ import annotations

import json
from pathlib import Path

SCRIPT_JSON = {
    "structure": "lineal",
    "tone": "didactico",
    "language": "es",
    "scenes": [
        {
            "narration_text": "Primera escena narrada.",
            "visual_prompt": "un amanecer sobre montañas",
            "duration_s": 5.0,
            "camera_notes": "plano fijo",
        },
        {
            "narration_text": "Segunda escena narrada.",
            "visual_prompt": "un río entre bosques",
            "duration_s": 4.0,
            "camera_notes": None,
        },
    ],
}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


SCRIPT_RESPONSE = json.dumps(SCRIPT_JSON)


def _mock_ollama(monkeypatch):
    def fake_post(url, json=None, timeout=None):  # noqa: A002
        return _Resp({"response": SCRIPT_RESPONSE})

    monkeypatch.setattr("app.providers.llm.ollama.httpx.post", fake_post)


def test_post_script_returns_scenes(client, monkeypatch):
    _mock_ollama(monkeypatch)

    created = client.post("/projects", json={"name": "Proyecto guion"}).json()
    pid = created["id"]

    resp = client.post(f"/projects/{pid}/script", json={"prompt": "un documental natural"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["script_id"] >= 1
    assert len(body["scenes"]) == 2
    first = body["scenes"][0]
    assert first["narration_text"] == "Primera escena narrada."
    assert first["visual_prompt"]
    assert first["duration_s"] == 5.0

    # Las escenas quedaron persistidas y son listables.
    listed = client.get(f"/projects/{pid}/scenes").json()
    assert len(listed) == 2


def test_scene_image_degrades_to_placeholder(client, monkeypatch):
    # Sin clave de stock, el provider real no toca la red: cae a placeholder.
    def no_network(*args, **kwargs):
        raise AssertionError("no debería llamarse a la red sin clave")

    monkeypatch.setattr("app.providers.image.stock.httpx.get", no_network)
    _mock_ollama(monkeypatch)

    pid = client.post("/projects", json={"name": "Proyecto imagen"}).json()["id"]
    scenes = client.post(
        f"/projects/{pid}/script", json={"prompt": "paisajes"}
    ).json()["scenes"]
    scene_id = scenes[0]["id"]

    resp = client.post(f"/scenes/{scene_id}/image")
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "image"
    assert asset["provider"] == "placeholder"
    assert Path(asset["path"]).is_file()
    assert Path(asset["path"]).stat().st_size > 0


def test_scene_image_cached_on_second_call(client, monkeypatch):
    monkeypatch.setattr(
        "app.providers.image.stock.httpx.get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sin red")),
    )
    _mock_ollama(monkeypatch)

    pid = client.post("/projects", json={"name": "P cache"}).json()["id"]
    scene_id = client.post(
        f"/projects/{pid}/script", json={"prompt": "algo"}
    ).json()["scenes"][0]["id"]

    first = client.post(f"/scenes/{scene_id}/image").json()
    second = client.post(f"/scenes/{scene_id}/image").json()
    # Misma entrada -> mismo Asset reutilizado desde cache.
    assert first["id"] == second["id"]

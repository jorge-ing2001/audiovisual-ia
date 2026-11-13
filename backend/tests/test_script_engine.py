"""Motor de guion con OllamaLLM mockeado (FEAT-003).

En el sandbox no hay Ollama: se mockea ``httpx.post`` para que el proveedor real
``OllamaLLM`` devuelva un JSON de guion, y se verifica que ``ScriptEngine`` lo
parsea y persiste a N escenas. No se llama al modelo real en ningún momento.
"""

from __future__ import annotations

import json

import pytest

from app.engines.script import ScriptEngine
from app.models.entities import Project, Scene
from app.providers.llm.ollama import OllamaLLM
from app.storage.db import session_scope

SCRIPT_JSON = {
    "structure": "intro-desarrollo-cierre",
    "tone": "inspirador",
    "language": "es",
    "scenes": [
        {
            "narration_text": "Un gato astronauta despega hacia las estrellas.",
            "visual_prompt": "gato con casco espacial frente a un cohete",
            "duration_s": 5.0,
            "camera_notes": "plano general lento",
        },
        {
            "narration_text": "Flota ingrávido admirando la Tierra.",
            "visual_prompt": "gato flotando en el espacio con la Tierra de fondo",
            "duration_s": 6.0,
            "camera_notes": None,
        },
        {
            "narration_text": "Regresa convertido en héroe.",
            "visual_prompt": "gato aterrizando entre multitudes que lo aclaman",
            "duration_s": 4.5,
            "camera_notes": "cierre con zoom",
        },
    ],
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_script_engine_parses_mocked_ollama_json(db_ready, monkeypatch):
    # OllamaLLM.complete llama a httpx.post: lo mockeamos para no tocar la red/modelo.
    captured: dict = {}

    response_text = json.dumps(SCRIPT_JSON)

    def fake_post(url, json=None, timeout=None):  # noqa: A002 - firma de httpx
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse({"response": response_text})

    monkeypatch.setattr("app.providers.llm.ollama.httpx.post", fake_post)

    with session_scope() as session:
        project = Project(name="Gato astronauta", prompt="un gato astronauta heroico")
        session.add(project)
        session.flush()
        project_id = project.id

        result = ScriptEngine().generate_script(project, session)

    # Llamó a Ollama con el system prompt y pidió salida JSON.
    assert captured["url"].endswith("/api/generate")
    assert captured["payload"]["format"] == "json"
    assert "guionista" in captured["payload"]["system"]

    # Persistió 3 escenas con los campos exigidos.
    assert len(result.scene_ids) == 3
    with session_scope() as session:
        scenes = (
            session.query(Scene)
            .filter(Scene.project_id == project_id)
            .order_by(Scene.order)
            .all()
        )
    assert [s.order for s in scenes] == [0, 1, 2]
    assert scenes[0].narration_text.startswith("Un gato astronauta")
    assert scenes[0].visual_prompt
    assert scenes[1].duration_s == 6.0


def test_script_engine_rejects_invalid_llm_json(db_ready, monkeypatch):
    def fake_post(url, json=None, timeout=None):  # noqa: A002
        return _FakeResponse({"response": "esto no es json"})

    monkeypatch.setattr("app.providers.llm.ollama.httpx.post", fake_post)

    with session_scope() as session:
        project = Project(name="X", prompt="algo")
        session.add(project)
        session.flush()
        with pytest.raises(ValueError):
            ScriptEngine().generate_script(project, session)


def test_ollama_provider_sends_model_and_prompt(db_ready, monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):  # noqa: A002
        captured["payload"] = json
        return _FakeResponse({"response": "hola"})

    monkeypatch.setattr("app.providers.llm.ollama.httpx.post", fake_post)
    out = OllamaLLM(model="llama3.1:8b").complete("di hola")
    assert out == "hola"
    assert captured["payload"]["model"] == "llama3.1:8b"
    assert captured["payload"]["prompt"] == "di hola"

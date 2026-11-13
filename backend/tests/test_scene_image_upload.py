"""Subida de imagen propia por escena (FEAT-B): POST /scenes/{id}/image/upload.

Demuestra que:
- Subir un PNG real a una escena crea un ``Asset(kind='image', provider='upload')``
  cuyo fichero existe DENTRO del media_root del proyecto.
- Tras subir, el ``TimelineBuilder`` / inputProps usa esa imagen subida para esa
  escena (es la más reciente) y NO cambia la imagen de las otras escenas (reemplazo
  aislado y conservación en producción/edición).
- La validación rechaza ficheros no-imagen o demasiado grandes con 4xx y mensaje claro.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

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


def _mock_ollama(monkeypatch):
    def fake_post(url, json=None, timeout=None):  # noqa: A002
        return _Resp({"response": json_dumps(SCRIPT_JSON)})

    monkeypatch.setattr("app.providers.llm.ollama.httpx.post", fake_post)


def json_dumps(obj) -> str:
    return json.dumps(obj)


def _png_bytes(color=(10, 120, 200), size=(32, 24)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _bootstrap_two_scenes(client, monkeypatch) -> tuple[int, list[dict]]:
    monkeypatch.setattr(
        "app.providers.image.stock.httpx.get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sin red")),
    )
    _mock_ollama(monkeypatch)
    pid = client.post("/projects", json={"name": "Proyecto upload"}).json()["id"]
    scenes = client.post(
        f"/projects/{pid}/script", json={"prompt": "paisajes"}
    ).json()["scenes"]
    assert len(scenes) == 2
    return pid, scenes


def test_upload_creates_image_asset_inside_media_root(client, monkeypatch, tmp_path):
    pid, scenes = _bootstrap_two_scenes(client, monkeypatch)
    scene_id = scenes[0]["id"]

    resp = client.post(
        f"/scenes/{scene_id}/image/upload",
        files={"file": ("mi_foto.png", _png_bytes(size=(40, 30)), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "image"
    assert asset["provider"] == "upload"
    assert asset["scene_id"] == scene_id
    assert asset["meta"]["source"] == "upload"
    assert asset["meta"]["original_filename"] == "mi_foto.png"
    assert asset["meta"]["width"] == 40
    assert asset["meta"]["height"] == 30

    # El fichero existe y vive DENTRO del media_root (para que Remotion lo sirva).
    from app.storage.media import ensure_project_workspace, media_root

    path = Path(asset["path"])
    assert path.is_file()
    assert path.stat().st_size > 0
    assert path.resolve().is_relative_to(media_root().resolve())
    uploads_dir = ensure_project_workspace(pid) / "images" / "uploads"
    assert path.resolve().is_relative_to(uploads_dir.resolve())


def test_uploaded_image_used_in_timeline_and_isolated(client, monkeypatch):
    pid, scenes = _bootstrap_two_scenes(client, monkeypatch)
    scene0, scene1 = scenes[0]["id"], scenes[1]["id"]

    # Genera imagen (placeholder) para AMBAS escenas (estado previo).
    gen0 = client.post(f"/scenes/{scene0}/image").json()
    gen1 = client.post(f"/scenes/{scene1}/image").json()
    assert gen0["provider"] == "placeholder"
    assert gen1["provider"] == "placeholder"

    # Sube una imagen propia SOLO a la escena 0.
    uploaded = client.post(
        f"/scenes/{scene0}/image/upload",
        files={"file": ("propia.png", _png_bytes(), "image/png")},
    ).json()
    assert uploaded["provider"] == "upload"

    # Construye el timeline del proyecto y comprueba qué imagen usa cada escena.
    from app.engines.render.timeline import TimelineBuilder
    from app.models.entities import Project
    from app.storage.db import session_scope

    with session_scope() as session:
        project = session.get(Project, pid)
        timeline = TimelineBuilder().build(project, session)

    # La escena 0 usa la imagen SUBIDA (más reciente); la escena 1 conserva su placeholder.
    scene0_tl = timeline.scenes[0]
    scene1_tl = timeline.scenes[1]
    assert scene0_tl.image_src == uploaded["path"]
    assert scene1_tl.image_src == gen1["path"]
    assert scene0_tl.image_src != gen0["path"]  # reemplazo real de la escena 0

    # inputProps para Remotion: la imagen subida es relativa al media_root (staticFile).
    from app.storage.media import media_root

    props = timeline.remotion_input_props(public_dir=str(media_root()))
    assert props["scenes"][0]["imageSrc"].endswith(Path(uploaded["path"]).name)
    # No es una ruta absoluta: es relativa al publicDir -> Remotion la sirve.
    assert not Path(props["scenes"][0]["imageSrc"]).is_absolute()


def test_upload_rejects_non_image(client, monkeypatch):
    _, scenes = _bootstrap_two_scenes(client, monkeypatch)
    scene_id = scenes[0]["id"]

    resp = client.post(
        f"/scenes/{scene_id}/image/upload",
        files={"file": ("notas.txt", b"esto no es una imagen", "text/plain")},
    )
    assert resp.status_code == 400, resp.text
    assert "imagen" in resp.json()["detail"].lower()


def test_upload_rejects_corrupt_image_extension(client, monkeypatch):
    _, scenes = _bootstrap_two_scenes(client, monkeypatch)
    scene_id = scenes[0]["id"]

    # Extensión de imagen pero contenido no-imagen: Pillow lo rechaza.
    resp = client.post(
        f"/scenes/{scene_id}/image/upload",
        files={"file": ("falsa.png", b"PNG? no realmente", "image/png")},
    )
    assert resp.status_code == 400, resp.text
    assert "imagen" in resp.json()["detail"].lower()


def test_upload_rejects_too_large(client, monkeypatch):
    from app.api import generation

    _, scenes = _bootstrap_two_scenes(client, monkeypatch)
    scene_id = scenes[0]["id"]

    # Fuerza un límite pequeño para no materializar 15 MB en el test.
    monkeypatch.setattr(generation, "_MAX_UPLOAD_BYTES", 256)
    big = _png_bytes(size=(256, 256))
    assert len(big) > 256

    resp = client.post(
        f"/scenes/{scene_id}/image/upload",
        files={"file": ("grande.png", big, "image/png")},
    )
    assert resp.status_code == 413, resp.text
    assert "máximo" in resp.json()["detail"].lower()


def test_upload_scene_not_found(client, monkeypatch):
    _mock_ollama(monkeypatch)
    resp = client.post(
        "/scenes/999999/image/upload",
        files={"file": ("x.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 404, resp.text

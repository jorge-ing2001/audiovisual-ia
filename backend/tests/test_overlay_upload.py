"""Subida de overlay/logo/marca de agua a nivel de proyecto (FEAT-006).

POST /projects/{id}/overlays/upload generaliza ``upload_scene_image`` a recursos
gráficos del PROYECTO (sin escena). Demuestra que:
- Subir un PNG real crea un ``Asset(kind='image', provider='upload', scene_id=None)``
  cuyo fichero vive DENTRO del media_root del proyecto (``overlays/uploads/``).
- Un SVG se admite validando MIME + tamaño (sin Pillow).
- La validación rechaza ficheros no-imagen, corruptos o demasiado grandes con 4xx y
  mensaje claro (mismo patrón que ``test_scene_image_upload.py``).
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


def _png_bytes(color=(200, 40, 40), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _project(client) -> int:
    return client.post("/projects", json={"name": "Proyecto overlay"}).json()["id"]


def test_upload_creates_project_level_image_asset(client):
    pid = _project(client)

    resp = client.post(
        f"/projects/{pid}/overlays/upload",
        files={"file": ("logo.png", _png_bytes(size=(80, 60)), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "image"
    assert asset["provider"] == "upload"
    assert asset["scene_id"] is None
    assert asset["meta"]["source"] == "upload"
    assert asset["meta"]["role"] == "overlay"
    assert asset["meta"]["original_filename"] == "logo.png"
    assert asset["meta"]["width"] == 80
    assert asset["meta"]["height"] == 60

    from app.storage.media import ensure_project_workspace, media_root

    path = Path(asset["path"])
    assert path.is_file()
    assert path.stat().st_size > 0
    assert path.resolve().is_relative_to(media_root().resolve())
    uploads_dir = ensure_project_workspace(pid) / "overlays" / "uploads"
    assert path.resolve().is_relative_to(uploads_dir.resolve())


def test_upload_overlay_accepts_svg(client):
    pid = _project(client)
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" ' \
          b'width="10" height="10"><rect width="10" height="10"/></svg>'
    resp = client.post(
        f"/projects/{pid}/overlays/upload",
        files={"file": ("marca.svg", svg, "image/svg+xml")},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "image"
    assert asset["meta"]["svg"] is True
    assert Path(asset["path"]).suffix == ".svg"


def test_upload_overlay_rejects_non_image(client):
    pid = _project(client)
    resp = client.post(
        f"/projects/{pid}/overlays/upload",
        files={"file": ("notas.txt", b"esto no es una imagen", "text/plain")},
    )
    assert resp.status_code == 400, resp.text
    assert "imagen" in resp.json()["detail"].lower()


def test_upload_overlay_rejects_corrupt_image(client):
    pid = _project(client)
    resp = client.post(
        f"/projects/{pid}/overlays/upload",
        files={"file": ("falso.png", b"PNG? no realmente", "image/png")},
    )
    assert resp.status_code == 400, resp.text
    assert "imagen" in resp.json()["detail"].lower()


def test_upload_overlay_rejects_corrupt_svg(client):
    pid = _project(client)
    resp = client.post(
        f"/projects/{pid}/overlays/upload",
        files={"file": ("falso.svg", b"no soy svg", "image/svg+xml")},
    )
    assert resp.status_code == 400, resp.text
    assert "svg" in resp.json()["detail"].lower()


def test_upload_overlay_rejects_too_large(client, monkeypatch):
    from app.api import generation

    pid = _project(client)
    monkeypatch.setattr(generation, "_MAX_UPLOAD_BYTES", 256)
    big = _png_bytes(size=(256, 256))
    assert len(big) > 256

    resp = client.post(
        f"/projects/{pid}/overlays/upload",
        files={"file": ("grande.png", big, "image/png")},
    )
    assert resp.status_code == 413, resp.text
    assert "máximo" in resp.json()["detail"].lower()


def test_upload_overlay_project_not_found(client):
    resp = client.post(
        "/projects/999999/overlays/upload",
        files={"file": ("x.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 404, resp.text

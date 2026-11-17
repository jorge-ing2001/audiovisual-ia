"""Integración VisualEngine + backend sd_local (ComfyUI) mockeado (FEAT-004).

Verifica que, con ``AIV_IMAGE_BACKEND='sd_local'`` y httpx MOCKEADO (sin ComfyUI
real), el motor visual 2D:

- produce un Asset con ``provider=='sd_local'`` cuyo ``meta`` conserva los metadatos
  de generación del punto 4 Y la identificación del workflow (workflow_version/
  workflow_hash con prefijo 'sha256:') SIN migración de esquema, y que VisualEngine
  NO sobreescribe workflow_version/workflow_hash;
- respeta la cache por hash (incluye backend) en la segunda llamada;
- degrada honestamente a ``placeholder`` (status='fallback') cuando sd_local falla;
- NO REGRESION (punto 6): con el default 'stock' nada cambia, el registry mantiene
  stock/placeholder y los stubs de pago (clip=blender + veo/runway/kling) intactos, y
  con sd_local desactivado el flujo por defecto no cambia.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

import app.config as config
from app.engines.visual2d import VisualEngine
from app.models.entities import Asset, Project, Scene
from app.providers import registry
from app.providers.image import comfyui as comfyui_module
from app.providers.image.comfyui import ComfyUIImageProvider
from app.providers.image.stock import StockImageProvider
from app.storage.db import session_scope


def _valid_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 60, 90)).save(buf, format="PNG")
    return buf.getvalue()


PNG_BYTES = _valid_png_bytes()


class _HttpxShim:
    """Reexpone post/get del fake y las excepciones reales de httpx."""

    ConnectError = httpx.ConnectError
    ConnectTimeout = httpx.ConnectTimeout
    TimeoutException = httpx.TimeoutException
    HTTPError = httpx.HTTPError

    def __init__(self, post, get):
        self._post = post
        self._get = get

    def post(self, url, json=None, timeout=None):
        return self._post(url, json)

    def get(self, url, params=None, timeout=None):
        return self._get(url, params)


class _Resp:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _success_http():
    def post(url, body):
        return _Resp(payload={"prompt_id": "pid1"})

    def get(url, params):
        if "/history/" in url:
            return _Resp(
                payload={
                    "pid1": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "aiv.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                }
            )
        return _Resp(content=PNG_BYTES)

    return _HttpxShim(post, get)


def _failing_http():
    def post(url, body):
        raise httpx.ConnectError("sin red")

    def get(url, params):
        # La gestión de VRAM de FEAT-005 puede consultar /system_stats y /queue en el
        # camino de error (free_after). Eso es esperable; lo que NO debe ocurrir es
        # llegar a /history si /prompt falló.
        if "/history/" in url:  # pragma: no cover - no debería alcanzarse
            raise AssertionError("no debería llegar al history si /prompt falla")
        if "/system_stats" in url:
            raise httpx.ConnectError("sin red")
        if "/queue" in url:
            raise httpx.ConnectError("sin red")
        return _Resp(content=b"")

    return _HttpxShim(post, get)


@pytest.fixture()
def sd_local_backend(db_ready, monkeypatch):
    """Activa sd_local como backend de imagen con httpx mockeado (éxito)."""
    monkeypatch.setattr(comfyui_module, "httpx", _success_http())
    monkeypatch.setenv("AIV_IMAGE_BACKEND", "sd_local")
    monkeypatch.setenv("AIV_SD_LOCAL_ENABLED", "true")
    # Timeouts rápidos: parchea el poll para no esperar en bucle.
    config._settings = None
    yield
    config._settings = None


def _make_scene(session, prompt="un faro en la tormenta"):
    project = Project(name="P", prompt="p", aspect_ratio="16:9")
    session.add(project)
    session.flush()
    scene = Scene(project_id=project.id, order=0, visual_prompt=prompt)
    session.add(scene)
    session.flush()
    return project.id, scene.id


# --------------------------------------------------------------------------- #
# Integración: éxito sd_local, meta y cache
# --------------------------------------------------------------------------- #


def test_render_scene_with_sd_local_persists_generation_meta(sd_local_backend):
    with session_scope() as session:
        _, scene_id = _make_scene(session)

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        asset = VisualEngine().render_scene_image(scene, session, seed=77)
        asset_id = asset.id

    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        assert asset.kind == "image"
        assert asset.provider == "sd_local"
        meta = asset.meta
        # Metadatos de generación (punto 4).
        assert meta["model"] == "v1-5-pruned-emaonly.safetensors"
        assert meta["prompt"]
        assert meta["seed"] == 77
        assert meta["steps"] == 20
        assert meta["sampler"] == "euler"
        assert meta["cfg"] == 7.0
        assert meta["status"] == "ok"
        assert meta["generation_time_s"] >= 0
        # Identificación del workflow (refinamiento 5/punto E): NO la sobreescribe el engine.
        assert meta["workflow_version"] == "sd15-embedded-v1"
        assert isinstance(meta["workflow_hash"], str)
        assert meta["workflow_hash"].startswith("sha256:")
        # Claves que SI añade el engine.
        assert "cache_key" in meta
        assert meta["backend"] == "sd_local"
        # Resolución real de generación preservada bajo claves propias.
        assert meta["gen_width"] > 0 and meta["gen_height"] > 0
        assert Path(asset.path).is_file()


def test_sd_local_cache_reuses_second_call(sd_local_backend):
    with session_scope() as session:
        _, scene_id = _make_scene(session)

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        first = VisualEngine().render_scene_image(scene, session, seed=5)
        first_id = first.id

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        second = VisualEngine().render_scene_image(scene, session, seed=5)
        second_id = second.id

    assert first_id == second_id


def test_sd_local_same_params_same_workflow_hash(sd_local_backend):
    with session_scope() as session:
        p1, s1 = _make_scene(session, prompt="paisaje identico")
        p2, s2 = _make_scene(session, prompt="paisaje identico")

    with session_scope() as session:
        a1 = VisualEngine().render_scene_image(session.get(Scene, s1), session, seed=9)
        a2 = VisualEngine().render_scene_image(session.get(Scene, s2), session, seed=9)
        # Mismo workflow/params => mismo hash determinista (trazabilidad).
        assert a1.meta["workflow_hash"] == a2.meta["workflow_hash"]


# --------------------------------------------------------------------------- #
# Degradación integrada
# --------------------------------------------------------------------------- #


def test_render_scene_degrades_to_placeholder_on_failure(db_ready, monkeypatch):
    monkeypatch.setattr(comfyui_module, "httpx", _failing_http())
    monkeypatch.setenv("AIV_IMAGE_BACKEND", "sd_local")
    monkeypatch.setenv("AIV_SD_LOCAL_ENABLED", "true")
    monkeypatch.setenv("AIV_SD_MAX_RETRIES", "0")
    config._settings = None

    with session_scope() as session:
        _, scene_id = _make_scene(session)

    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        asset = VisualEngine().render_scene_image(scene, session)
        asset_id = asset.id

    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        assert asset.provider == "placeholder"
        assert asset.meta["status"] == "fallback"
        assert asset.meta["fallback_reason"] == "connection"
        assert Path(asset.path).is_file()

    config._settings = None


# --------------------------------------------------------------------------- #
# NO REGRESION (punto 6)
# --------------------------------------------------------------------------- #


def test_default_backend_is_stock(db_ready, monkeypatch):
    monkeypatch.delenv("AIV_IMAGE_BACKEND", raising=False)
    config._settings = None
    try:
        assert registry.active_backend("image") == "stock"
        provider = registry.resolve("image")
        assert isinstance(provider, StockImageProvider)
        assert provider.name == "stock"
    finally:
        config._settings = None


def test_sd_local_resolves_to_comfyui_provider(db_ready, monkeypatch):
    monkeypatch.setenv("AIV_IMAGE_BACKEND", "sd_local")
    config._settings = None
    try:
        assert registry.active_backend("image") == "sd_local"
        provider = registry.resolve("image")
        assert isinstance(provider, ComfyUIImageProvider)
        assert provider.name == "sd_local"
    finally:
        config._settings = None


def test_registry_keeps_existing_image_and_clip_providers():
    # El registro de sd_local no desplaza a stock/placeholder ni a los stubs de pago.
    image_backends = registry.PROVIDERS["image"]
    assert "stock" in image_backends
    assert "placeholder" in image_backends
    assert "sd_local" in image_backends
    clip_backends = registry.PROVIDERS["clip"]
    for name in ("blender", "veo", "runway", "kling"):
        assert name in clip_backends


def test_sd_local_disabled_keeps_default_behavior(db_ready, monkeypatch):
    # Con sd_local desactivado y el default 'stock', el flujo por defecto no cambia:
    # sin claves de stock cae a placeholder (comportamiento previo intacto).
    monkeypatch.delenv("AIV_IMAGE_BACKEND", raising=False)
    monkeypatch.setenv("AIV_SD_LOCAL_ENABLED", "false")
    monkeypatch.setenv("AIV_PIXABAY_API_KEY", "")
    monkeypatch.setenv("AIV_PEXELS_API_KEY", "")
    config._settings = None
    try:
        with session_scope() as session:
            _, scene_id = _make_scene(session)
        with session_scope() as session:
            scene = session.get(Scene, scene_id)
            asset = VisualEngine().render_scene_image(scene, session)
            # Backend default stock, sin claves => degrada a placeholder como antes.
            assert asset.meta["backend"] == "stock"
            assert asset.provider == "placeholder"
    finally:
        config._settings = None

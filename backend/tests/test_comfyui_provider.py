"""Parte de red de ComfyUIImageProvider con httpx MOCKEADO (FEAT-003).

Patrón de ``test_stock_provider.py``: se monkeypatchea ``app.providers.image.comfyui.httpx``
y NUNCA se hace una llamada real a ComfyUI. Se cubren:

- EXITO: /prompt -> prompt_id, /history/<id> -> outputs con una imagen, /view ->
  bytes de un PNG válido (generado con Pillow); se afirma provider=='sd_local', PNG
  en disco no vacío y validable, y meta completo (status='ok', model==checkpoint,
  workflow_version/workflow_hash, etc.).
- DEGRADACION por cada una de las 7 categorías del punto 3: en todas provider==
  'placeholder', meta.status=='fallback', meta.fallback_reason==la categoría esperada
  e image_for NUNCA lanza.
- REINTENTOS: las transitorias (connection/timeout) se reintentan; las no
  transitorias (checkpoint_missing) NO (se cuentan las llamadas a _submit).
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.providers.image import comfyui as comfyui_module
from app.providers.image.comfyui import ComfyUIImageProvider


# --------------------------------------------------------------------------- #
# Utilidades de test
# --------------------------------------------------------------------------- #


def _valid_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


PNG_BYTES = _valid_png_bytes()


class _Resp:
    """Respuesta httpx mínima para los tests (json()/content/raise_for_status)."""

    def __init__(self, *, payload=None, content=b"", status_error=None):
        self._payload = payload
        self.content = content
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error
        return None

    def json(self):
        return self._payload


def _provider(**overrides) -> ComfyUIImageProvider:
    """Construye el provider con valores rápidos para los tests (timeouts cortos)."""
    kwargs = dict(
        base_url="http://localhost:8188",
        checkpoint="v1-5-pruned-emaonly.safetensors",
        steps=20,
        sampler="euler",
        scheduler="normal",
        cfg=7.0,
        base_size=256,
        negative_prompt="lowres",
        workflow_path="",
        enabled=True,
    )
    kwargs.update(overrides)
    provider = ComfyUIImageProvider(**kwargs)
    # Timeouts/poll cortos para no ralentizar los tests de timeout/reintento.
    provider.timeout_s = 1
    provider.poll_interval_s = 0.0
    provider.connect_timeout_s = 0.5
    provider.free_before = False
    provider.free_after = False
    return provider


class _FakeHTTP:
    """Fake de httpx.get/post configurable por endpoint; cuenta llamadas."""

    def __init__(self, *, post=None, get=None):
        self._post = post
        self._get = get
        self.post_calls: list[str] = []
        self.get_calls: list[str] = []

    def post(self, url, json=None, timeout=None):
        self.post_calls.append(url)
        return self._post(url, json)

    def get(self, url, params=None, timeout=None):
        self.get_calls.append(url)
        return self._get(url, params)


def _install_http(monkeypatch, fake):
    monkeypatch.setattr(comfyui_module, "httpx", _HttpxShim(fake))


class _HttpxShim:
    """Expone post/get del fake y reexpone las excepciones reales de httpx."""

    ConnectError = httpx.ConnectError
    ConnectTimeout = httpx.ConnectTimeout
    TimeoutException = httpx.TimeoutException
    HTTPError = httpx.HTTPError

    def __init__(self, fake):
        self._fake = fake

    def post(self, *a, **kw):
        return self._fake.post(*a, **kw)

    def get(self, *a, **kw):
        return self._fake.get(*a, **kw)


# --------------------------------------------------------------------------- #
# (a) EXITO
# --------------------------------------------------------------------------- #


def test_success_returns_sd_local_asset_with_meta(tmp_path, monkeypatch):
    def post(url, body):
        return _Resp(payload={"prompt_id": "abc123"})

    def get(url, params):
        if "/history/" in url:
            return _Resp(
                payload={
                    "abc123": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {
                                        "filename": "aiv_sd_0001.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
            )
        if "/view" in url:
            return _Resp(content=PNG_BYTES)
        raise AssertionError(f"GET inesperado: {url}")

    fake = _FakeHTTP(post=post, get=get)
    _install_http(monkeypatch, fake)

    provider = _provider()
    asset = provider.image_for(
        "un gato astronauta", aspect="16:9", out_dir=str(tmp_path), seed=1234
    )

    assert asset.provider == "sd_local"
    assert Path(asset.path).is_file()
    assert Path(asset.path).stat().st_size > 0
    # El PNG en disco es validable por PIL.
    Image.open(asset.path).verify()

    meta = asset.meta
    assert meta["status"] == "ok"
    assert meta["fallback_reason"] is None
    assert meta["model"] == "v1-5-pruned-emaonly.safetensors"
    assert meta["prompt"] == "un gato astronauta"
    assert meta["negative_prompt"] == "lowres"
    assert meta["seed"] == 1234
    assert meta["steps"] == 20
    assert meta["sampler"] == "euler"
    assert meta["cfg"] == 7.0
    assert meta["width"] > 0 and meta["height"] > 0
    assert meta["generation_time_s"] >= 0
    # Identificación del workflow (refinamiento 5/punto E).
    assert meta["workflow_version"] == "sd15-embedded-v1"
    assert isinstance(meta["workflow_hash"], str)
    assert meta["workflow_hash"].startswith("sha256:")
    assert meta["backend_engine"] == "comfyui"


# --------------------------------------------------------------------------- #
# (b) DEGRADACION por cada categoría (7)
# --------------------------------------------------------------------------- #


def _assert_fallback(asset, reason):
    assert asset.provider == "placeholder"
    assert asset.meta["status"] == "fallback"
    assert asset.meta["fallback_reason"] == reason
    assert Path(asset.path).is_file()


def test_degrade_disabled(tmp_path, monkeypatch):
    # enabled=False: no se toca la red en absoluto.
    def boom(*a, **kw):
        raise AssertionError("no debería llamarse a httpx cuando enabled=False")

    monkeypatch.setattr(comfyui_module.httpx, "post", boom, raising=False)
    monkeypatch.setattr(comfyui_module.httpx, "get", boom, raising=False)

    provider = _provider(enabled=False)
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))
    _assert_fallback(asset, "disabled")


def test_degrade_connection(tmp_path, monkeypatch):
    def post(url, body):
        raise httpx.ConnectError("sin red")

    fake = _FakeHTTP(post=post, get=lambda u, p: None)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    provider.max_retries = 0
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))
    _assert_fallback(asset, "connection")


def test_degrade_checkpoint_missing(tmp_path, monkeypatch):
    def post(url, body):
        return _Resp(
            payload={
                "node_errors": {
                    "4": {"errors": [{"message": "ckpt_name not found: foo.safetensors"}]}
                }
            }
        )

    fake = _FakeHTTP(post=post, get=lambda u, p: None)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))
    _assert_fallback(asset, "checkpoint_missing")


def test_degrade_invalid_workflow(tmp_path, monkeypatch):
    def post(url, body):
        return _Resp(
            payload={
                "error": {"type": "invalid_prompt", "message": "validation failed"},
                "node_errors": {"3": {"errors": [{"message": "required input missing"}]}},
            }
        )

    fake = _FakeHTTP(post=post, get=lambda u, p: None)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))
    _assert_fallback(asset, "invalid_workflow")


def test_degrade_timeout(tmp_path, monkeypatch):
    def post(url, body):
        return _Resp(payload={"prompt_id": "pending"})

    def get(url, params):
        # /history nunca devuelve outputs -> se supera sd_timeout_s.
        return _Resp(payload={"pending": {"outputs": {}}})

    fake = _FakeHTTP(post=post, get=get)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    provider.timeout_s = 0  # fuerza timeout inmediato tras el primer sondeo
    provider.max_retries = 0
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))
    _assert_fallback(asset, "timeout")


def test_degrade_cuda_oom(tmp_path, monkeypatch):
    def post(url, body):
        return _Resp(payload={"prompt_id": "oom1"})

    def get(url, params):
        return _Resp(
            payload={
                "oom1": {
                    "status": {
                        "status_str": "error",
                        "messages": [
                            [
                                "execution_error",
                                {"exception_message": "CUDA out of memory. Tried to allocate"},
                            ]
                        ],
                    },
                    "outputs": {},
                }
            }
        )

    fake = _FakeHTTP(post=post, get=get)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))
    _assert_fallback(asset, "cuda_oom")


def test_degrade_corrupt_image(tmp_path, monkeypatch):
    def post(url, body):
        return _Resp(payload={"prompt_id": "c1"})

    def get(url, params):
        if "/history/" in url:
            return _Resp(
                payload={
                    "c1": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {"images": [{"filename": "bad.png", "subfolder": "", "type": "output"}]}
                        },
                    }
                }
            )
        # /view devuelve bytes que PIL no puede decodificar.
        return _Resp(content=b"not-a-real-png-\x00\x01\x02")

    fake = _FakeHTTP(post=post, get=get)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))
    _assert_fallback(asset, "corrupt_image")


# --------------------------------------------------------------------------- #
# (c) REINTENTOS
# --------------------------------------------------------------------------- #


def test_retries_transient_connection_then_succeeds(tmp_path, monkeypatch):
    submit_calls = {"n": 0}

    def post(url, body):
        submit_calls["n"] += 1
        if submit_calls["n"] == 1:
            raise httpx.ConnectError("transitorio")
        return _Resp(payload={"prompt_id": "ok2"})

    def get(url, params):
        if "/history/" in url:
            return _Resp(
                payload={
                    "ok2": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {"images": [{"filename": "r.png", "subfolder": "", "type": "output"}]}
                        },
                    }
                }
            )
        return _Resp(content=PNG_BYTES)

    fake = _FakeHTTP(post=post, get=get)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    provider.max_retries = 1
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))

    assert asset.provider == "sd_local"
    assert asset.meta["status"] == "ok"
    # Un fallo transitorio + un éxito = dos llamadas a _submit (POST /prompt).
    assert submit_calls["n"] == 2


def test_no_retry_on_checkpoint_missing(tmp_path, monkeypatch):
    submit_calls = {"n": 0}

    def post(url, body):
        submit_calls["n"] += 1
        return _Resp(payload={"node_errors": {"4": {"message": "checkpoint not found"}}})

    fake = _FakeHTTP(post=post, get=lambda u, p: None)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    provider.max_retries = 3  # aunque permita reintentos, NO transitoria => 1 sola vez
    asset = provider.image_for("x", aspect="16:9", out_dir=str(tmp_path))

    _assert_fallback(asset, "checkpoint_missing")
    assert submit_calls["n"] == 1


def test_image_for_never_raises(tmp_path, monkeypatch):
    # Cualquier excepción de red se traduce en degradación, nunca propaga.
    def post(url, body):
        raise httpx.ConnectError("boom")

    fake = _FakeHTTP(post=post, get=lambda u, p: None)
    _install_http(monkeypatch, fake)

    provider = _provider(enabled=True)
    provider.max_retries = 0
    try:
        asset = provider.image_for("x", aspect="1:1", out_dir=str(tmp_path))
    except Exception as exc:  # pragma: no cover - no debe ocurrir
        pytest.fail(f"image_for no debe lanzar: {exc!r}")
    assert asset.provider == "placeholder"

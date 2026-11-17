"""Gestión de VRAM de ComfyUI con httpx MOCKEADO (FEAT-005, refinamientos 1-4).

Cubre ``_free_vram`` y sus cuatro guardas, más la detección de capacidades y
``_comfyui_busy``. Patrón del resto de tests de ComfyUI: se monkeypatchea
``app.providers.image.comfyui.httpx`` con un shim que reexpone las excepciones reales,
sin llamadas de red reales. Se usa ``caplog`` para comprobar que los errores de
liberación se registran con nivel y detalle SIN ocultarlos (refinamiento 3).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.providers.image import comfyui as comfyui_module
from app.providers.image.comfyui import ComfyUIImageProvider


@pytest.fixture(autouse=True)
def _reset_caps():
    comfyui_module._reset_capabilities_cache()
    yield
    comfyui_module._reset_capabilities_cache()


class _Resp:
    def __init__(self, *, payload=None, status_code=200, status_error=None):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error
        return None

    def json(self):
        return self._payload


class _Router:
    """Enruta get/post por substring de la URL y cuenta llamadas por endpoint."""

    def __init__(self, *, routes):
        self._routes = routes
        self.calls: dict[str, int] = {}

    def _dispatch(self, url):
        for key, handler in self._routes.items():
            if key in url:
                self.calls[key] = self.calls.get(key, 0) + 1
                return handler(url)
        raise AssertionError(f"URL no enrutada: {url}")

    def get(self, url, params=None, timeout=None):
        return self._dispatch(url)

    def post(self, url, json=None, timeout=None):
        return self._dispatch(url)


class _HttpxShim:
    ConnectError = httpx.ConnectError
    ConnectTimeout = httpx.ConnectTimeout
    TimeoutException = httpx.TimeoutException
    HTTPError = httpx.HTTPError

    def __init__(self, router):
        self._router = router

    def get(self, *a, **kw):
        return self._router.get(*a, **kw)

    def post(self, *a, **kw):
        return self._router.post(*a, **kw)


def _install(monkeypatch, router):
    monkeypatch.setattr(comfyui_module, "httpx", _HttpxShim(router))


def _provider() -> ComfyUIImageProvider:
    provider = ComfyUIImageProvider(base_url="http://localhost:8188", enabled=True)
    provider.connect_timeout_s = 0.2
    return provider


# Estado típico de /system_stats con un modelo/VRAM ocupada (hay algo que liberar).
_STATS_LOADED = {
    "system": {"comfyui_version": "0.3.1"},
    "devices": [{"vram_total": 4_000_000_000, "vram_free": 1_000_000_000}],
}
# /system_stats con toda la VRAM libre (nada cargado).
_STATS_EMPTY = {
    "system": {"comfyui_version": "0.3.1"},
    "devices": [{"vram_total": 4_000_000_000, "vram_free": 4_000_000_000}],
}


# --------------------------------------------------------------------------- #
# (a) GUARDA /queue (refinamiento 2)
# --------------------------------------------------------------------------- #


def test_free_skipped_when_queue_busy(monkeypatch):
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_LOADED),
            "/queue": lambda u: _Resp(
                payload={"queue_running": [["job", 1]], "queue_pending": []}
            ),
            "/free": lambda u: _Resp(payload={}),
        }
    )
    _install(monkeypatch, router)

    _provider()._free_vram(reason="test")

    assert router.calls.get("/free", 0) == 0  # con cola ocupada NO se libera


def test_free_called_when_queue_empty(monkeypatch):
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_LOADED),
            "/queue": lambda u: _Resp(
                payload={"queue_running": [], "queue_pending": []}
            ),
            "/free": lambda u: _Resp(payload={}),
        }
    )
    _install(monkeypatch, router)

    _provider()._free_vram(reason="test")

    assert router.calls.get("/free", 0) == 1  # con cola vacía SÍ se libera


# --------------------------------------------------------------------------- #
# (b) ESTADO PREVIO via /system_stats (refinamiento 1)
# --------------------------------------------------------------------------- #


def test_free_skipped_when_nothing_loaded(monkeypatch, caplog):
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_EMPTY),
            "/queue": lambda u: _Resp(payload={}),
            "/free": lambda u: _Resp(payload={}),
        }
    )
    _install(monkeypatch, router)

    with caplog.at_level(logging.INFO):
        _provider()._free_vram(reason="test")

    # /system_stats se consulta (una vez en capabilities + una en la guarda de estado).
    assert router.calls.get("/system_stats", 0) >= 1
    # Nada cargado -> no se fuerza /free (ni siquiera se consulta /queue).
    assert router.calls.get("/free", 0) == 0
    assert any("no hay modelos cargados" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# (c) REGISTRO SIN OCULTAR (refinamiento 3)
# --------------------------------------------------------------------------- #


def test_free_error_500_logged_without_hiding(monkeypatch, caplog):
    err = httpx.HTTPStatusError(
        "500", request=httpx.Request("POST", "http://x/free"),
        response=httpx.Response(500),
    )
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_LOADED),
            "/queue": lambda u: _Resp(payload={"queue_running": [], "queue_pending": []}),
            "/free": lambda u: _Resp(status_error=err),
        }
    )
    _install(monkeypatch, router)

    meta: dict = {}
    with caplog.at_level(logging.WARNING):
        _provider()._free_vram(reason="test", meta=meta)  # no lanza

    assert router.calls.get("/free", 0) == 1
    errores = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert errores, "un fallo de /free debe registrarse con nivel warning/error"
    assert any("/free" in r.message for r in errores)
    assert "free_vram_error" in meta


def test_free_error_connect_logged_without_hiding(monkeypatch, caplog):
    def free_boom(u):
        raise httpx.ConnectError("sin red")

    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_LOADED),
            "/queue": lambda u: _Resp(payload={"queue_running": [], "queue_pending": []}),
            "/free": free_boom,
        }
    )
    _install(monkeypatch, router)

    with caplog.at_level(logging.WARNING):
        _provider()._free_vram(reason="test")  # no lanza

    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warns and any("sin red" in r.message or "ConnectError" in r.message
                         or "/free" in r.message for r in warns)


def test_free_success_logs_info(monkeypatch, caplog):
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_LOADED),
            "/queue": lambda u: _Resp(payload={"queue_running": [], "queue_pending": []}),
            "/free": lambda u: _Resp(payload={}),
        }
    )
    _install(monkeypatch, router)

    with caplog.at_level(logging.INFO):
        _provider()._free_vram(reason="test")

    assert router.calls.get("/free", 0) == 1
    assert any("VRAM liberada" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# (d) COMPATIBILIDAD POR VERSION (refinamiento 4): 404 en cada endpoint
# --------------------------------------------------------------------------- #


def test_system_stats_404_degrades(monkeypatch, caplog):
    """/system_stats 404 -> supports_system_stats=False y se continúa sin lanzar."""
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(status_code=404),
            "/queue": lambda u: _Resp(payload={"queue_running": [], "queue_pending": []}),
            "/free": lambda u: _Resp(payload={}),
        }
    )
    _install(monkeypatch, router)

    provider = _provider()
    caps = provider.capabilities()
    assert caps.supports_system_stats is False

    # Sin estado previo: se salta la guarda 2, se consulta /queue y (vacía) se libera.
    provider._free_vram(reason="test")
    assert router.calls.get("/free", 0) == 1


def test_queue_404_skips_free_conservatively(monkeypatch):
    """/queue 404 -> no se puede confirmar libre -> OMITIR /free (conservador)."""
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_LOADED),
            "/queue": lambda u: _Resp(status_code=404),
            "/free": lambda u: _Resp(payload={}),
        }
    )
    _install(monkeypatch, router)

    _provider()._free_vram(reason="test")

    assert router.calls.get("/free", 0) == 0  # conservador: no libera


def test_free_404_marks_unsupported(monkeypatch, caplog):
    """/free 404 -> se salta el paso, se registra y supports_free pasa a False."""
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(payload=_STATS_LOADED),
            "/queue": lambda u: _Resp(payload={"queue_running": [], "queue_pending": []}),
            "/free": lambda u: _Resp(status_code=404),
        }
    )
    _install(monkeypatch, router)

    provider = _provider()
    with caplog.at_level(logging.WARNING):
        provider._free_vram(reason="test")  # no lanza

    assert provider.capabilities().supports_free is False
    assert any("404" in r.message or "no soportado" in r.message for r in caplog.records)


def test_free_vram_never_raises_on_any_404(monkeypatch):
    """Ningún 404 (/system_stats, /queue, /free) propaga excepción al pipeline."""
    router = _Router(
        routes={
            "/system_stats": lambda u: _Resp(status_code=404),
            "/queue": lambda u: _Resp(status_code=404),
            "/free": lambda u: _Resp(status_code=404),
        }
    )
    _install(monkeypatch, router)

    try:
        _provider()._free_vram(reason="test")
    except Exception as exc:  # pragma: no cover - no debe ocurrir
        pytest.fail(f"_free_vram no debe lanzar: {exc!r}")


# --------------------------------------------------------------------------- #
# _comfyui_busy y detección de capacidades
# --------------------------------------------------------------------------- #


def test_comfyui_busy_true_when_pending(monkeypatch):
    router = _Router(
        routes={"/queue": lambda u: _Resp(payload={"queue_pending": [["j", 1]]})}
    )
    _install(monkeypatch, router)
    assert _provider()._comfyui_busy() is True


def test_comfyui_busy_conservative_on_queue_error(monkeypatch):
    def boom(u):
        raise httpx.ConnectError("sin red")

    router = _Router(routes={"/queue": boom})
    _install(monkeypatch, router)
    # No se puede confirmar que esté libre -> conservador -> True.
    assert _provider()._comfyui_busy() is True


def test_capabilities_detects_version(monkeypatch):
    router = _Router(routes={"/system_stats": lambda u: _Resp(payload=_STATS_LOADED)})
    _install(monkeypatch, router)
    caps = _provider().capabilities()
    assert caps.supports_system_stats is True
    assert caps.version == "0.3.1"

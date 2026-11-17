"""Unload verificado de Ollama con httpx MOCKEADO (FEAT-006, refinamientos 1 y 3).

Sin Ollama ni GPU reales: se monkeypatchea ``app.providers.llm.ollama.httpx`` con un
shim que reexpone las excepciones reales de httpx y enruta get/post por URL. Se usa
``caplog`` para comprobar que los fallos de liberación se registran con nivel y detalle
SIN ocultarlos (refinamiento 3).

Cubre:
  (a) ESTADO PREVIO (refinamiento 1): si /api/ps lista el modelo, unload() hace POST
      /api/generate con keep_alive:0; si NO lo lista, no hace el POST y lo registra.
  (b) error en el POST (ConnectError): unload() es no-op SIN excepción pero registra el
      fallo con nivel warning y detalle.
  (c) verify_unloaded True cuando /api/ps deja de listar el modelo (registra éxito).
  (d) verify_unloaded False (sin lanzar) si el modelo sigue cargado hasta el timeout.
  (e) /api/ps ConnectError/404: loaded_models -> [] y verify degrada sin romper.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.providers.llm import ollama as ollama_module
from app.providers.llm.ollama import OllamaLLM


MODEL = "llama3.1:8b"


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
    """Enruta get/post por substring de la URL; cuenta llamadas; soporta secuencias."""

    def __init__(self, *, ps=None, generate=None):
        self._ps = ps
        self._generate = generate
        self.calls: dict[str, int] = {}

    def get(self, url, params=None, timeout=None):
        if "/api/ps" in url:
            self.calls["ps"] = self.calls.get("ps", 0) + 1
            return self._ps(self.calls["ps"])
        raise AssertionError(f"GET no enrutado: {url}")

    def post(self, url, json=None, timeout=None):
        if "/api/generate" in url:
            self.calls["generate"] = self.calls.get("generate", 0) + 1
            return self._generate(json)
        raise AssertionError(f"POST no enrutado: {url}")


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
    monkeypatch.setattr(ollama_module, "httpx", _HttpxShim(router))


def _ps_with(model: bool):
    payload = {"models": [{"name": MODEL}]} if model else {"models": []}
    return lambda n: _Resp(payload=payload)


# --------------------------------------------------------------------------- #
# (a) ESTADO PREVIO (refinamiento 1)
# --------------------------------------------------------------------------- #


def test_unload_posts_when_model_loaded(monkeypatch):
    router = _Router(
        ps=_ps_with(True),
        generate=lambda body: _Resp(payload={"done": True}),
    )
    _install(monkeypatch, router)

    OllamaLLM(model=MODEL).unload()

    assert router.calls.get("generate", 0) == 1  # el modelo estaba cargado -> se libera


def test_unload_skips_post_when_model_not_loaded(monkeypatch, caplog):
    router = _Router(
        ps=_ps_with(False),
        generate=lambda body: _Resp(payload={"done": True}),
    )
    _install(monkeypatch, router)

    with caplog.at_level(logging.INFO):
        OllamaLLM(model=MODEL).unload()

    assert router.calls.get("generate", 0) == 0  # no cargado -> NO se hace el POST
    assert any("no está cargado" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# (b) ERROR SIN OCULTAR (refinamiento 3)
# --------------------------------------------------------------------------- #


def test_unload_logs_warning_on_connect_error(monkeypatch, caplog):
    def generate_boom(body):
        raise httpx.ConnectError("conexión rechazada")

    router = _Router(ps=_ps_with(True), generate=generate_boom)
    _install(monkeypatch, router)

    with caplog.at_level(logging.WARNING):
        OllamaLLM(model=MODEL).unload()  # no lanza

    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warns, "un fallo de unload debe registrarse con nivel warning"
    assert any("Fallo al liberar" in r.message for r in warns)
    assert any("conexión rechazada" in r.message for r in warns)


# --------------------------------------------------------------------------- #
# (c)/(d) verify_unloaded True/False
# --------------------------------------------------------------------------- #


def test_verify_unloaded_true_when_model_disappears(monkeypatch, caplog):
    # 1ª consulta: el modelo figura cargado; 2ª: ya no.
    def ps(n):
        return _Resp(payload={"models": [{"name": MODEL}] if n == 1 else []})

    router = _Router(ps=ps, generate=lambda b: _Resp())
    _install(monkeypatch, router)

    with caplog.at_level(logging.INFO):
        ok = OllamaLLM(model=MODEL).verify_unloaded(timeout_s=2.0, poll_interval_s=0.0)

    assert ok is True
    assert any("liberó el modelo" in r.message for r in caplog.records)


def test_verify_unloaded_false_on_timeout(monkeypatch, caplog):
    # El modelo sigue cargado siempre -> expira el timeout corto.
    router = _Router(ps=_ps_with(True), generate=lambda b: _Resp())
    _install(monkeypatch, router)

    with caplog.at_level(logging.WARNING):
        ok = OllamaLLM(model=MODEL).verify_unloaded(timeout_s=0.0, poll_interval_s=0.0)

    assert ok is False
    assert any("no confirmó la descarga" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# (e) /api/ps ConnectError/404 degrada sin romper
# --------------------------------------------------------------------------- #


def test_loaded_models_empty_on_connect_error(monkeypatch):
    def ps_boom(n):
        raise httpx.ConnectError("sin red")

    router = _Router(ps=ps_boom, generate=lambda b: _Resp())
    _install(monkeypatch, router)

    assert OllamaLLM(model=MODEL).loaded_models() == []


def test_loaded_models_empty_on_404(monkeypatch, caplog):
    router = _Router(ps=lambda n: _Resp(status_code=404), generate=lambda b: _Resp())
    _install(monkeypatch, router)

    with caplog.at_level(logging.INFO):
        models = OllamaLLM(model=MODEL).loaded_models()

    assert models == []
    assert any("/api/ps no disponible" in r.message for r in caplog.records)


def test_verify_unloaded_degrades_when_ps_missing(monkeypatch):
    # /api/ps 404 -> loaded_models [] -> verify devuelve True (best-effort) sin lanzar.
    router = _Router(ps=lambda n: _Resp(status_code=404), generate=lambda b: _Resp())
    _install(monkeypatch, router)

    try:
        ok = OllamaLLM(model=MODEL).verify_unloaded(timeout_s=1.0, poll_interval_s=0.0)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"verify_unloaded no debe lanzar: {exc!r}")
    assert ok is True


def test_unload_idempotent_no_raise(monkeypatch):
    # /api/ps vacío + generate que fallaría: unload es no-op (no cargado) y no lanza.
    router = _Router(ps=_ps_with(False), generate=lambda b: _Resp())
    _install(monkeypatch, router)
    OllamaLLM(model=MODEL).unload()
    OllamaLLM(model=MODEL).unload()

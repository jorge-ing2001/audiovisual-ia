"""Concurrencia del ComfyUIImageProvider: nunca dos generaciones SD a la vez (FEAT-005).

El invariante (punto 1) se protege con un semáforo a NIVEL DE MÓDULO cuyo límite es
``AIV_SD_MAX_CONCURRENCY`` (default 1, ampliable por config). Estos tests NO necesitan
ComfyUI ni GPU: se monkeypatchea ``_submit`` para que bloquee en un ``threading.Event``
y se mide la concurrencia máxima observada con un contador protegido por lock. Los
Events usan timeouts cortos para que el test nunca cuelgue.

Se cubre:
  (a) con sd_max_concurrency=1, dos llamadas concurrentes a image_for nunca solapan la
      generación (max observada <= 1);
  (b) el semáforo se libera también en el camino de degradación (un fallo no deja el
      lock tomado y una generación posterior progresa);
  (c) con sd_max_concurrency=2 el límite observado sube a 2.
"""

from __future__ import annotations

import threading

import pytest

from app.providers.image import comfyui as comfyui_module
from app.providers.image.comfyui import ComfyUIImageProvider


@pytest.fixture(autouse=True)
def _reset_semaphore():
    """Descarta el semáforo cacheado antes y después de cada test."""
    comfyui_module._reset_sd_semaphore()
    yield
    comfyui_module._reset_sd_semaphore()


def _set_concurrency(monkeypatch, value: int) -> None:
    """Fija AIV_SD_MAX_CONCURRENCY y limpia la caché de config y del semáforo."""
    monkeypatch.setenv("AIV_SD_MAX_CONCURRENCY", str(value))
    import app.config as config

    config._settings = None
    comfyui_module._reset_sd_semaphore()


class _ConcurrencyProbe:
    """Cuenta la concurrencia observada dentro de la sección crítica (generación)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.max_observed = 0

    def enter(self) -> None:
        with self._lock:
            self.current += 1
            self.max_observed = max(self.max_observed, self.current)

    def leave(self) -> None:
        with self._lock:
            self.current -= 1


def _provider() -> ComfyUIImageProvider:
    provider = ComfyUIImageProvider(
        base_url="http://localhost:8188",
        enabled=True,
        base_size=64,
    )
    provider.free_before = False
    provider.free_after = False
    provider.max_retries = 0
    return provider


def _run_concurrently(targets) -> None:
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    for t in threads:
        assert not t.is_alive(), "un hilo de generación quedó colgado"


def test_max_concurrency_one_never_overlaps(tmp_path, monkeypatch):
    """(a) Con límite 1, dos image_for concurrentes nunca solapan la generación."""
    _set_concurrency(monkeypatch, 1)

    probe = _ConcurrencyProbe()
    # Puerta que mantiene a cada hilo dentro de la sección crítica el tiempo justo
    # para que, si el semáforo no serializara, se observara concurrencia > 1.
    release = threading.Event()
    entered = threading.Semaphore(0)

    def fake_submit(self, graph):
        probe.enter()
        entered.release()
        # Espera a que el test libere (con timeout para no colgar nunca).
        release.wait(timeout=5.0)
        probe.leave()
        return "pid"

    def fake_poll(self, prompt_id):
        return [{"filename": "x.png", "subfolder": "", "type": "output"}]

    def fake_download(self, image_ref, out_dir):
        from pathlib import Path

        p = Path(out_dir) / "sd.png"
        p.write_bytes(b"not-a-png")  # fuerza degradación tras la sección crítica
        raise comfyui_module.ComfyUIImageError("forzado")

    monkeypatch.setattr(ComfyUIImageProvider, "_submit", fake_submit)
    monkeypatch.setattr(ComfyUIImageProvider, "_poll_history", fake_poll)
    monkeypatch.setattr(ComfyUIImageProvider, "_download", fake_download)

    provider = _provider()

    def call():
        provider.image_for("gato", aspect="1:1", out_dir=str(tmp_path))

    t1 = threading.Thread(target=call)
    t2 = threading.Thread(target=call)
    t1.start()
    # El primer hilo debe entrar en la sección crítica; el segundo quedará esperando
    # el semáforo (no debe entrar hasta que el primero salga).
    assert entered.acquire(timeout=5.0)
    release.set()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)
    assert not t1.is_alive() and not t2.is_alive()

    assert probe.max_observed <= 1


def test_semaphore_released_on_degradation(tmp_path, monkeypatch):
    """(b) Un fallo (degradación) libera el semáforo; la siguiente llamada progresa."""
    _set_concurrency(monkeypatch, 1)

    calls = {"n": 0}

    def fake_submit(self, graph):
        calls["n"] += 1
        raise comfyui_module.ComfyUIConnectionError("sin red")

    monkeypatch.setattr(ComfyUIImageProvider, "_submit", fake_submit)
    provider = _provider()

    first = provider.image_for("a", aspect="1:1", out_dir=str(tmp_path))
    assert first.provider == "placeholder"
    assert first.meta["status"] == "fallback"

    # Si el semáforo no se hubiera liberado, esta segunda llamada se bloquearía.
    # La ejecutamos en un hilo con join(timeout) para detectar un posible deadlock.
    done = threading.Event()

    def call():
        provider.image_for("b", aspect="1:1", out_dir=str(tmp_path))
        done.set()

    t = threading.Thread(target=call)
    t.start()
    t.join(timeout=5.0)
    assert done.is_set(), "el semáforo no se liberó tras la degradación (deadlock)"
    assert calls["n"] == 2


def test_max_concurrency_two_allows_two(tmp_path, monkeypatch):
    """(c) Con límite 2, el test observa hasta 2 generaciones simultáneas."""
    _set_concurrency(monkeypatch, 2)

    probe = _ConcurrencyProbe()
    both_in = threading.Barrier(2, timeout=5.0)

    def fake_submit(self, graph):
        probe.enter()
        try:
            # Ambos hilos deben poder estar dentro a la vez (límite 2).
            both_in.wait()
        except threading.BrokenBarrierError:  # pragma: no cover - solo si expira
            pass
        probe.leave()
        return "pid"

    def fake_poll(self, prompt_id):
        return [{"filename": "x.png", "subfolder": "", "type": "output"}]

    def fake_download(self, image_ref, out_dir):
        raise comfyui_module.ComfyUIImageError("forzado")

    monkeypatch.setattr(ComfyUIImageProvider, "_submit", fake_submit)
    monkeypatch.setattr(ComfyUIImageProvider, "_poll_history", fake_poll)
    monkeypatch.setattr(ComfyUIImageProvider, "_download", fake_download)

    provider = _provider()

    def call():
        provider.image_for("gato", aspect="1:1", out_dir=str(tmp_path))

    _run_concurrently([call, call])

    assert probe.max_observed == 2

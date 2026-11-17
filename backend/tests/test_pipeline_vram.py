"""Paso condicional de VRAM en el pipeline (FEAT-006).

Verifica que ``generate_video_handler`` libera (y verifica) la VRAM de Ollama EXACTAMENTE
UNA VEZ entre el guion y las imágenes SOLO cuando LLM=ollama e imagen=sd_local y el flag
``AIV_OLLAMA_UNLOAD_BETWEEN_STAGES`` está activo; y que con el backend de imagen por
defecto (stock) NO se invoca (invariante punto 6).

Se espía ``_release_llm_vram`` con monkeypatch (sin Ollama ni ComfyUI reales) y se usa
un guion FIXTURE etiquetado para no depender del LLM. Las unidades de Ollama (unload/
verify_unloaded) se cubren en test_ollama_unload.py; la lógica de condición se prueba
aquí directamente sobre ``_should_unload_ollama_before_images``.
"""

from __future__ import annotations

import pytest

import app.config as config
import app.jobs.pipeline as pipeline
from app.models.entities import Project
from app.storage.db import session_scope


SCRIPT_FIXTURE = {
    "structure": "intro",
    "tone": "neutro",
    "language": "es",
    "scenes": [
        {
            "narration_text": "Una escena breve.",
            "visual_prompt": "un bosque al amanecer",
            "duration_s": 1.0,
            "camera_notes": None,
        }
    ],
}


# --------------------------------------------------------------------------- #
# Lógica de condición: _should_unload_ollama_before_images
# --------------------------------------------------------------------------- #


def test_condition_true_only_for_ollama_plus_sd_local(db_ready, monkeypatch):
    monkeypatch.setenv("AIV_OLLAMA_UNLOAD_BETWEEN_STAGES", "true")
    config._settings = None

    with session_scope() as session:
        # ollama + sd_local -> True.
        p1 = Project(
            name="a", prompt="x",
            provider_overrides={"llm": "ollama", "image": "sd_local"},
        )
        # ollama + stock -> False.
        p2 = Project(
            name="b", prompt="x",
            provider_overrides={"llm": "ollama", "image": "stock"},
        )
        # openai + sd_local -> False.
        p3 = Project(
            name="c", prompt="x",
            provider_overrides={"llm": "openai", "image": "sd_local"},
        )
        session.add_all([p1, p2, p3])
        session.flush()

        assert pipeline._should_unload_ollama_before_images(p1) is True
        assert pipeline._should_unload_ollama_before_images(p2) is False
        assert pipeline._should_unload_ollama_before_images(p3) is False


def test_condition_false_when_flag_disabled(db_ready, monkeypatch):
    monkeypatch.setenv("AIV_OLLAMA_UNLOAD_BETWEEN_STAGES", "false")
    config._settings = None

    with session_scope() as session:
        p = Project(
            name="a", prompt="x",
            provider_overrides={"llm": "ollama", "image": "sd_local"},
        )
        session.add(p)
        session.flush()
        assert pipeline._should_unload_ollama_before_images(p) is False


# --------------------------------------------------------------------------- #
# Integración en el handler: se invoca una vez con sd_local+ollama, 0 con stock
# --------------------------------------------------------------------------- #


def _run_pipeline_counting_release(monkeypatch, overrides):
    """Ejecuta el handler con un spy en _release_llm_vram; devuelve nº de invocaciones.

    Se cortocircuita el pipeline tras el paso de imágenes lanzando una excepción
    controlada desde _generate_scene_assets (ya se verificó el punto de interés), de modo
    que no se necesita ComfyUI ni render real.
    """
    calls = {"n": 0}

    def spy_release(project):
        calls["n"] += 1

    class _Stop(RuntimeError):
        pass

    def fake_scene_assets(project, session, options, progress):
        raise _Stop("corte tras el punto de VRAM")

    monkeypatch.setattr(pipeline, "_release_llm_vram", spy_release)
    monkeypatch.setattr(pipeline, "_generate_scene_assets", fake_scene_assets)

    with session_scope() as session:
        project = Project(name="vram", prompt="algo", provider_overrides=overrides)
        session.add(project)
        session.flush()
        pid = project.id

    with pytest.raises(_Stop):
        pipeline.generate_video_handler(
            {"project_id": pid, "options": {"script_fixture": SCRIPT_FIXTURE}}, job_id=0
        )
    return calls["n"]


def test_release_invoked_once_for_sd_local_ollama(db_ready, monkeypatch):
    monkeypatch.setenv("AIV_OLLAMA_UNLOAD_BETWEEN_STAGES", "true")
    config._settings = None

    n = _run_pipeline_counting_release(
        monkeypatch, {"llm": "ollama", "image": "sd_local"}
    )
    assert n == 1  # exactamente una vez entre guion e imágenes


def test_release_not_invoked_for_stock_default(db_ready, monkeypatch):
    monkeypatch.setenv("AIV_OLLAMA_UNLOAD_BETWEEN_STAGES", "true")
    config._settings = None

    n = _run_pipeline_counting_release(
        monkeypatch, {"llm": "ollama", "image": "stock"}
    )
    assert n == 0  # con imagen por defecto (stock) NO se toca Ollama (invariante punto 6)

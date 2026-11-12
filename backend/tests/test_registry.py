"""Resolución de backend por el registry de proveedores (FEAT-002).

Verifica: default (ollama) por env var, cambio por env var, y override por
proyecto. También que los FAKE de test resuelven a instancias usables.
"""

from __future__ import annotations

import pytest

import app.config as config
from app.providers import registry
from app.providers.base import LLMProvider


@pytest.fixture()
def clean_settings(monkeypatch):
    """Resetea la cache de settings para que las env vars surtan efecto."""
    config._settings = None
    yield
    config._settings = None


def test_active_backend_default_is_ollama(clean_settings, monkeypatch):
    monkeypatch.delenv("AIV_LLM_BACKEND", raising=False)
    assert registry.active_backend("llm") == "ollama"


def test_active_backend_switches_with_env_var(clean_settings, monkeypatch):
    monkeypatch.setenv("AIV_LLM_BACKEND", "openai")
    assert registry.active_backend("llm") == "openai"


def test_project_override_takes_precedence(clean_settings, monkeypatch):
    monkeypatch.setenv("AIV_LLM_BACKEND", "ollama")
    assert registry.active_backend("llm", {"llm": "openai"}) == "openai"
    # Sin override, vuelve al valor del env.
    assert registry.active_backend("llm", {"image": "placeholder"}) == "ollama"


def test_resolve_fake_backend_returns_protocol_instance(clean_settings, monkeypatch):
    monkeypatch.setenv("AIV_LLM_BACKEND", "fake")
    provider = registry.resolve("llm")
    assert isinstance(provider, LLMProvider)
    assert provider.complete("hola") == "[fake-llm] hola"


def test_resolve_unregistered_backend_raises(clean_settings, monkeypatch):
    # ollama es el default pero su factoría real llega en FEAT-003.
    monkeypatch.setenv("AIV_LLM_BACKEND", "ollama")
    with pytest.raises(KeyError):
        registry.resolve("llm")

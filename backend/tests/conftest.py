"""Fixtures compartidas: BD y media root aislados por test (FEAT-002).

Cada test corre contra una BD SQLite temporal y un ``AIV_MEDIA_ROOT`` temporal,
para no tocar la base de datos ni el workspace reales. Se resetean las cachés de
config y engine para que la nueva URL/ruta surta efecto.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest


@pytest.fixture()
def temp_env(tmp_path, monkeypatch) -> Iterator[None]:
    """Apunta la config a una BD y media root temporales, con cachés limpias."""
    db_path = tmp_path / "test.db"
    media_path = tmp_path / "media"
    monkeypatch.setenv("AIV_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AIV_MEDIA_ROOT", str(media_path))

    import app.config as config
    from app.storage import db as db_module

    config._settings = None
    db_module.reset_engine()

    yield

    db_module.reset_engine()
    config._settings = None
    importlib.invalidate_caches()


@pytest.fixture()
def client(temp_env):
    """TestClient con la app recreada sobre la BD temporal."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_ready(temp_env):
    """Garantiza que las tablas existen en la BD temporal (para tests de cola)."""
    from app.storage.db import init_db

    init_db()
    yield

"""Creación de la aplicación FastAPI y endpoints base.

FEAT-001 añadió ``GET /health``. FEAT-002 monta los routers de negocio
(projects, scenes, assets, jobs) e inicializa la BD. FEAT-003 añade el router de
generación (motor de guion y de imagen de escena). FEAT-005 añade el router de render
(motor 6) y registra el handler de render en el worker. FEAT-006 añade el endpoint
``POST /projects/{id}/generate`` (pipeline end-to-end prompt->vídeo) y registra el
handler ``generate_video`` en el worker (vía ``app.jobs.handlers``).
"""

from __future__ import annotations

from fastapi import FastAPI

import app.jobs.handlers  # noqa: F401  (importar registra el handler de render, §6)
from app import __version__
from app.api import assets, generation, jobs, projects, render, scenes
from app.config import get_settings
from app.storage.db import init_db


def create_app() -> FastAPI:
    """Construye y configura la instancia de FastAPI."""
    settings = get_settings()
    app = FastAPI(
        title="audiovisual-ia",
        version=__version__,
        description="Plataforma prompt-to-video 2D (Fase 1 / MVP) — modo local/gratuito.",
    )

    # Crea las tablas del modelo de datos si no existen (SQLite local por defecto).
    init_db()

    @app.get("/health")
    def health() -> dict[str, str]:
        """Comprobación de salud del servicio."""
        return {"status": "ok"}

    # Routers de negocio (FEAT-002).
    app.include_router(projects.router)
    app.include_router(scenes.router)
    app.include_router(assets.router)
    app.include_router(jobs.router)
    # Router de generación: motor de guion y de imagen de escena (FEAT-003).
    app.include_router(generation.router)
    # Router de render: encola el render (motor 6) y sirve el MP4 (FEAT-005).
    app.include_router(render.router)

    # Referencia a settings para dejar explícita la carga de config al arranque.
    app.state.settings = settings
    return app


app = create_app()

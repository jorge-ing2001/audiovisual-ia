"""Creación de la aplicación FastAPI y endpoints base.

FEAT-001 añadió ``GET /health``. FEAT-002 monta los routers de negocio
(projects, scenes, assets, jobs) e inicializa la BD. FEAT-003 añade el router de
generación (motor de guion y de imagen de escena). El router de render llega después.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api import assets, generation, jobs, projects, scenes
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

    # Referencia a settings para dejar explícita la carga de config al arranque.
    app.state.settings = settings
    return app


app = create_app()

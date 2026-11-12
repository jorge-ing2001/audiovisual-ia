"""Creación de la aplicación FastAPI y endpoints base.

Andamiaje (FEAT-001): solo expone ``GET /health``. Los routers de negocio
(projects, scenes, assets, generation, jobs, render) se añaden en FEAT-002.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.config import get_settings


def create_app() -> FastAPI:
    """Construye y configura la instancia de FastAPI."""
    settings = get_settings()
    app = FastAPI(
        title="audiovisual-ia",
        version=__version__,
        description="Plataforma prompt-to-video 2D (Fase 1 / MVP) — modo local/gratuito.",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Comprobación de salud del servicio."""
        return {"status": "ok"}

    # Referencia a settings para dejar explícita la carga de config al arranque.
    app.state.settings = settings
    return app


app = create_app()

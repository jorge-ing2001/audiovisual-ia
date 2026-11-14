
"""Creación de la aplicación FastAPI y gestión del worker."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.jobs.handlers  # noqa: F401
from app import __version__
from app.api import assets, editor, generation, jobs, projects, render, scenes
from app.config import get_settings
from app.jobs.queue import JobQueue
from app.jobs.worker import worker_loop
from app.storage.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia y detiene el worker junto con la aplicación."""
    stop_event = asyncio.Event()

    worker_task = asyncio.create_task(
        worker_loop(
            JobQueue(),
            poll_interval=0.5,
            base_backoff=1.0,
            stop_event=stop_event,
        )
    )

    app.state.worker_task = worker_task

    try:
        yield
    finally:
        stop_event.set()
        await worker_task


def create_app() -> FastAPI:
    """Construye y configura la instancia de FastAPI."""
    settings = get_settings()

    init_db()

    app = FastAPI(
        title="audiovisual-ia",
        version=__version__,
        description="Plataforma prompt-to-video 2D (Fase 1 / MVP) — modo local/gratuito.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(projects.router)
    app.include_router(scenes.router)
    app.include_router(assets.router)
    app.include_router(jobs.router)
    app.include_router(generation.router)
    app.include_router(render.router)
    app.include_router(editor.router)

    app.state.settings = settings

    return app


app = create_app()

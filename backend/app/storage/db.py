"""Engine y sesión de SQLAlchemy (SQLite por defecto vía ``AIV_DATABASE_URL``).

Diseño agnóstico para migrar a Postgres en Fase 4 (ver docs/ARCHITECTURE.md §5):
solo aquí se conoce el dialecto concreto. El resto del backend trabaja con
sesiones y modelos ORM.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.base import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _connect_args(database_url: str) -> dict[str, object]:
    """Argumentos de conexión específicos del dialecto.

    SQLite necesita ``check_same_thread=False`` para poder compartir la conexión
    entre el worker asyncio y los manejadores de la API.
    """
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def get_engine() -> Engine:
    """Devuelve el engine (cacheado) resuelto desde la configuración."""
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            future=True,
            connect_args=_connect_args(settings.database_url),
        )
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, expire_on_commit=False, future=True
        )
    return _engine


def _session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None  # garantizado por get_engine()
    return _SessionLocal


def init_db() -> None:
    """Crea las tablas declaradas en los modelos ORM si no existen."""
    # Importa los modelos para poblar los metadatos antes de create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())


def get_session() -> Session:
    """Crea una nueva sesión de BD (el llamador se encarga de cerrarla)."""
    return _session_factory()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager transaccional: commit al salir, rollback ante error."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI: sesión por request con cierre garantizado."""
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Descarta el engine/factory cacheados (útil en tests que cambian la URL)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None

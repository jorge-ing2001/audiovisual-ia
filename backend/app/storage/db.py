"""Engine y sesión de SQLAlchemy (SQLite por defecto vía ``AIV_DATABASE_URL``).

Diseño agnóstico para migrar a Postgres en Fase 4 (ver docs/ARCHITECTURE.md §5):
solo aquí se conoce el dialecto concreto. El resto del backend trabaja con
sesiones y modelos ORM.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
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


# Columnas de administracion de Project que create_all NO anade a una tabla ya existente
# (FEAT-002, Bloque 4). Se incorporan con una migracion ligera e idempotente para SQLite.
# Cada entrada: nombre -> definicion de columna (tipo + NULL por defecto, compatible con
# proyectos existentes).
_PROJECT_ADMIN_COLUMNS: dict[str, str] = {
    "folder": "TEXT",
    "tags": "JSON",
}


def _ensure_project_columns(engine: Engine) -> None:
    """Migracion idempotente: anade ``folder``/``tags`` a ``projects`` si faltan (SQLite).

    ``Base.metadata.create_all`` crea tablas que faltan pero NO altera tablas ya
    existentes, de modo que una BD de un proyecto en uso no incorporaria las columnas
    nuevas. Esta rutina las anade sin recrear la tabla ni perder datos:

    1. Solo actua sobre SQLite (``engine.dialect.name == 'sqlite'``); otros dialectos se
       gestionan con migraciones propias (Fase 4) y aqui es un no-op.
    2. Lee el esquema real con ``PRAGMA table_info('projects')`` y arma el conjunto de
       columnas presentes.
    3. Por cada columna objetivo que NO exista, ejecuta ``ALTER TABLE ... ADD COLUMN`` con
       un default compatible (NULL). Si ya existe, NO ejecuta el ALTER (evita el error
       'duplicate column name' de SQLite).

    Es seguro de repetir: la decision se toma SIEMPRE a partir del esquema real justo
    antes de cada ALTER, nunca a ciegas. Una segunda pasada es un no-op.
    """
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info('projects')")).fetchall()
        if not rows:
            # La tabla aun no existe (p. ej. BD vacia antes de create_all): nada que migrar.
            return
        existing = {row[1] for row in rows}  # row[1] = nombre de columna
        for name, coltype in _PROJECT_ADMIN_COLUMNS.items():
            if name not in existing:
                conn.execute(
                    text(f"ALTER TABLE projects ADD COLUMN {name} {coltype}")
                )


def init_db() -> None:
    """Crea las tablas declaradas en los modelos ORM si no existen."""
    # Importa los modelos para poblar los metadatos antes de create_all.
    from app import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Migracion idempotente de columnas de administracion (FEAT-002): corre tras create_all
    # en cada arranque de forma segura y repetible.
    _ensure_project_columns(engine)


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

"""Registry de proveedores: resolución del backend activo por capacidad.

Mapa ``capacidad -> {nombre_backend: factoría}``. El backend activo se resuelve
por variable de entorno (``AIV_LLM_BACKEND``, ``AIV_IMAGE_BACKEND``,
``AIV_TTS_BACKEND``, ``AIV_ASR_BACKEND``), con un hook de override por proyecto
(``Project.provider_overrides``). Este es el punto único donde se decide qué
implementación concreta se usa: cambiar de backend por env var NO toca motores,
API ni frontend (invariante central de la arquitectura, §3).

Las factorías reales (OllamaLLM, StockImageProvider, KokoroTTS, FasterWhisper)
se registran en FEAT-003/004 con ``register``. Mientras tanto, el registry conoce
los *nombres* de los defaults (para que ``resolve`` acierte el nombre activo) y
expone los proveedores FAKE de test bajo el nombre ``"fake"``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config import get_settings
from app.providers import fakes

# Capacidades soportadas y su variable de entorno de selección.
CAPABILITIES = ("llm", "image", "tts", "asr")

_ENV_FIELD: dict[str, str] = {
    "llm": "llm_backend",
    "image": "image_backend",
    "tts": "tts_backend",
    "asr": "asr_backend",
}

# Factoría = callable sin argumentos que construye una instancia del proveedor.
Factory = Callable[[], Any]

# Mapa capacidad -> {nombre_backend: factoría}.
# Los proveedores reales se añaden con ``register`` en FEAT-003/004. Los fakes
# ("fake") son solo para tests y nunca son el default de ninguna capacidad.
PROVIDERS: dict[str, dict[str, Factory]] = {
    "llm": {"fake": fakes.FakeLLM},
    "image": {"fake": fakes.FakeImage},
    "tts": {"fake": fakes.FakeTTS},
    "asr": {"fake": fakes.FakeASR},
}


def register(capability: str, name: str, factory: Factory) -> None:
    """Registra la factoría de un backend para una capacidad.

    Punto de extensión para FEAT-003/004 (y proveedores de pago en Fase 4):
    implementar la interfaz de ``providers.base`` y registrarla aquí, sin tocar
    el resto del sistema.
    """
    if capability not in PROVIDERS:
        raise ValueError(f"Capacidad desconocida: {capability!r}")
    PROVIDERS[capability][name] = factory


def active_backend(capability: str, project_overrides: dict | None = None) -> str:
    """Nombre del backend activo para una capacidad.

    Prioridad: override por proyecto > variable de entorno (default de config).
    El override por proyecto permite que distintos proyectos usen distintos
    backends sin cambiar la configuración global (hook de ``Project``).
    """
    if capability not in _ENV_FIELD:
        raise ValueError(f"Capacidad desconocida: {capability!r}")
    if project_overrides and capability in project_overrides:
        return str(project_overrides[capability])
    return getattr(get_settings(), _ENV_FIELD[capability])


def resolve(capability: str, project_overrides: dict | None = None) -> Any:
    """Construye el proveedor del backend activo para una capacidad.

    Lanza ``KeyError`` con un mensaje accionable si el backend seleccionado no
    tiene una factoría registrada (p. ej. ``ollama`` antes de FEAT-003).
    """
    name = active_backend(capability, project_overrides)
    registered = PROVIDERS.get(capability, {})
    if name not in registered:
        disponibles = ", ".join(sorted(registered)) or "(ninguno)"
        raise KeyError(
            f"Backend {name!r} para la capacidad {capability!r} no está registrado. "
            f"Registrados: {disponibles}. (Los proveedores reales se añaden en FEAT-003/004.)"
        )
    return registered[name]()

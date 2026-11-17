"""Router REST de ajustes de solo lectura (FEAT-007, Bloque 3).

Expone la selección del backend de IMÁGENES de forma real y transparente para que la UI
no tenga que hardcodear nada:

- ``GET /settings/image-backends`` devuelve los backends de imagen REGISTRADOS en el
  registry (``PROVIDERS['image']``), el ACTIVO por defecto (``active_backend('image')``)
  y un puñado de flags NO sensibles relacionados con la generación local SD
  (``sd_local_enabled``, ``sd_local_url``, ``sd_max_concurrency``).

Es estrictamente de SOLO LECTURA y NO expone ninguna clave/secreto (pixabay/pexels/
openai/stability/elevenlabs). La selección global del backend se hace por variable de
entorno (``AIV_IMAGE_BACKEND``); el override por proyecto vive en ``Project.provider_
overrides`` (API de proyectos). Este endpoint solo informa.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.providers import registry

router = APIRouter(tags=["settings"])


@router.get("/settings/image-backends")
def get_image_backends() -> dict:
    """Backends de imagen disponibles + activo + flags NO sensibles (solo lectura).

    No incluye el backend de test ``fake`` (interno de la suite). NUNCA devuelve claves
    ni secretos: solo los flags de generación local explícitamente seguros.
    """
    settings = get_settings()
    registered = sorted(
        name for name in registry.PROVIDERS.get("image", {}) if name != "fake"
    )
    return {
        "capability": "image",
        "active": registry.active_backend("image"),
        "available": registered,
        "flags": {
            "sd_local_enabled": settings.sd_local_enabled,
            "sd_local_url": settings.sd_local_url,
            "sd_max_concurrency": settings.sd_max_concurrency,
        },
    }

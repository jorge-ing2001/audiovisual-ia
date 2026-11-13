"""Proveedores de imagen reales (FEAT-003).

``StockImageProvider`` es el default: busca imágenes libres en Pixabay/Pexels
(orden configurable) y, si no hay clave/red/resultados, degrada a
``PlaceholderProvider``, que genera un PNG real con Pillow. Ambos se registran en
el registry bajo ``"stock"`` / ``"placeholder"``.
"""

from __future__ import annotations

from app.providers.image.placeholder import PlaceholderProvider
from app.providers.image.stock import StockImageProvider

__all__ = ["PlaceholderProvider", "StockImageProvider"]

"""Motor 2 — Visual 2D (Fase 1).

Obtiene/genera una imagen por escena (stock/placeholder por defecto) con cache por
hash. Interfaz: ``VisualEngine.render_scene_image(scene, session, ...) -> Asset``.
Implementado en FEAT-003 (resuelve el ImageProvider activo vía registry).
"""

from __future__ import annotations

from app.engines.visual2d.engine import VisualEngine, cache_key

__all__ = ["VisualEngine", "cache_key"]

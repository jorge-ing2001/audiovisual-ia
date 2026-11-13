"""Resolución de aspecto (``"16:9"``) a dimensiones en píxeles.

Compartido por los proveedores de imagen para producir artefactos consistentes.
"""

from __future__ import annotations

# Dimensiones objetivo por aspecto (lado largo = 1920). Se amplía según se
# necesiten nuevos aspectos sin tocar a los proveedores.
_ASPECT_DIMS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "4:3": (1440, 1080),
}

_DEFAULT = (1920, 1080)


def aspect_to_dims(aspect: str) -> tuple[int, int]:
    """Devuelve ``(ancho, alto)`` en píxeles para un aspecto dado.

    Acepta ``"W:H"`` arbitrario: si no está en la tabla, calcula las dimensiones
    manteniendo el lado largo en 1920. Cae al default (16:9) si el formato es
    inválido.
    """
    if aspect in _ASPECT_DIMS:
        return _ASPECT_DIMS[aspect]
    try:
        w_ratio, h_ratio = (int(part) for part in aspect.split(":", 1))
        if w_ratio <= 0 or h_ratio <= 0:
            return _DEFAULT
    except (ValueError, TypeError):
        return _DEFAULT
    if w_ratio >= h_ratio:
        width = 1920
        height = round(1920 * h_ratio / w_ratio)
    else:
        height = 1920
        width = round(1920 * w_ratio / h_ratio)
    # Las dimensiones pares facilitan el encoding de video posterior.
    return (width - (width % 2), height - (height % 2))

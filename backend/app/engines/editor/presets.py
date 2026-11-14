"""Catálogo de PRESETS de subtítulo y formato (datos puros, sin render ni IO).

- ``SUBTITLE_PRESETS``: ``dict[str, dict]`` con los valores de ``SubtitleStyle`` por preset
  (``classic``/``tiktok``/``reels``/``shorts``/``cinema``/``clean``). Añadir un preset NO
  toca el render: son datos. Cada entrada produce un ``SubtitleStyle`` válido
  (``subtitle_style_from_preset``), con el campo ``preset`` ya fijado.
- ``FORMAT_PRESETS``: ``dict[str, dict]`` con ``{name, width, height, aspect_ratio,
  safe_top_frac, safe_bottom_frac}`` para 9:16 (1080x1920) Reels/Shorts/TikTok, 16:9 y 1:1.
  Las fracciones de zona segura son los márgenes verticales que la UI de la red social
  puede tapar; el compilador las emite para que el render encuadre dentro de ellas.

Los presets ``tiktok``/``reels``/``shorts`` son verticales (9:16): el frontend puede
cambiar el formato del proyecto al aplicarlos. ``classic``/``cinema`` son 16:9 y
``clean`` es neutro (sirve en cualquier formato).
"""

from __future__ import annotations

from app.engines.editor.model import SubtitleStyle

__all__ = [
    "SUBTITLE_PRESETS",
    "FORMAT_PRESETS",
    "subtitle_style_from_preset",
    "PRESET_FORMAT",
]


# --------------------------------------------------------------------------- #
# Presets de subtítulo (valores de SubtitleStyle; defaults neutros para el resto)
# --------------------------------------------------------------------------- #
#
# ``classic`` y ``tiktok`` conservan EXACTAMENTE el estilo previo al Bloque 2 (caja negra
# translúcida / mayúsculas con contorno), de modo que aplicarlos reproduce el render
# actual. Los demás usan la decoración fina (TextDecoration) para variantes.

SUBTITLE_PRESETS: dict[str, dict] = {
    # 16:9 clásico: caja negra translúcida, texto blanco, abajo (comportamiento actual).
    "classic": {
        "preset": "classic",
        "font_family": "Inter, sans-serif",
        "font_size": 48,
        "color": "#FFFFFF",
        "bg_color": None,
        "bold": True,
        "outline": True,
        "position": "bottom",
    },
    # 9:16: mayúsculas, contorno negro grueso, sin caja, subido sobre la zona de UI.
    "tiktok": {
        "preset": "tiktok",
        "font_family": "Inter, sans-serif",
        "font_size": 64,
        "color": "#FFFFFF",
        "bg_color": None,
        "bold": True,
        "outline": True,
        "position": "bottom",
    },
    # 9:16 (Instagram Reels): sans bold, caja semitransparente redondeada, centro-bajo.
    "reels": {
        "preset": "reels",
        "font_family": "Inter, sans-serif",
        "font_size": 60,
        "color": "#FFFFFF",
        "bg_color": "#000000",
        "bold": True,
        "outline": False,
        "position": "bottom",
        "bg_opacity": 0.55,
        "bg_padding": 0.25,
        "bg_radius": 14.0,
    },
    # 9:16 (YouTube Shorts): alto contraste, contorno + sombra, posición media-baja.
    "shorts": {
        "preset": "shorts",
        "font_family": "Inter, sans-serif",
        "font_size": 62,
        "color": "#FFFFFF",
        "bg_color": None,
        "bold": True,
        "outline": True,
        "position": "bottom",
        "outline_color": "#000000",
        "outline_width": 3.0,
        "shadow": True,
        "shadow_color": "#000000",
        "shadow_blur": 6.0,
        "shadow_x": 0.0,
        "shadow_y": 2.0,
    },
    # 16:9 cortometraje: sin caja, sombra suave, subtítulo discreto.
    "cinema": {
        "preset": "cinema",
        "font_family": "Inter, sans-serif",
        "font_size": 46,
        "color": "#F5F5F5",
        "bg_color": None,
        "bold": False,
        "outline": False,
        "position": "bottom",
        "shadow": True,
        "shadow_color": "#000000",
        "shadow_blur": 8.0,
        "shadow_x": 0.0,
        "shadow_y": 1.0,
    },
    # Neutro: sans, sin caja ni contorno, sombra mínima. Sirve en cualquier formato.
    "clean": {
        "preset": "clean",
        "font_family": "Inter, sans-serif",
        "font_size": 48,
        "color": "#FFFFFF",
        "bg_color": None,
        "bold": True,
        "outline": False,
        "position": "bottom",
        "shadow": True,
        "shadow_color": "#000000",
        "shadow_blur": 3.0,
    },
}


def subtitle_style_from_preset(preset: str) -> SubtitleStyle:
    """Construye un ``SubtitleStyle`` validado a partir de un preset de ``SUBTITLE_PRESETS``.

    Lanza ``KeyError`` (envuelto como ``ValueError``) si el preset no existe.
    """
    if preset not in SUBTITLE_PRESETS:
        raise ValueError(
            f"Preset de subtítulo desconocido: {preset!r}. "
            f"Disponibles: {', '.join(sorted(SUBTITLE_PRESETS))}."
        )
    return SubtitleStyle.model_validate(SUBTITLE_PRESETS[preset])


# --------------------------------------------------------------------------- #
# Presets de formato (resolución + zonas seguras verticales)
# --------------------------------------------------------------------------- #

FORMAT_PRESETS: dict[str, dict] = {
    "16:9": {
        "name": "Horizontal 16:9",
        "width": 1920,
        "height": 1080,
        "aspect_ratio": "16:9",
        "safe_top_frac": 0.0,
        "safe_bottom_frac": 0.0,
    },
    "1:1": {
        "name": "Cuadrado 1:1",
        "width": 1080,
        "height": 1080,
        "aspect_ratio": "1:1",
        "safe_top_frac": 0.0,
        "safe_bottom_frac": 0.0,
    },
    # 9:16 (1080x1920) con zonas seguras por plataforma (márgenes que tapa la UI).
    "tiktok": {
        "name": "TikTok 9:16",
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "safe_top_frac": 0.12,
        "safe_bottom_frac": 0.18,
    },
    "reels": {
        "name": "Instagram Reels 9:16",
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "safe_top_frac": 0.12,
        "safe_bottom_frac": 0.20,
    },
    "shorts": {
        "name": "YouTube Shorts 9:16",
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "safe_top_frac": 0.10,
        "safe_bottom_frac": 0.16,
    },
}

#: Formato vertical implícito de cada preset de subtítulo 9:16 (el resto no fuerza formato).
PRESET_FORMAT: dict[str, str] = {
    "tiktok": "tiktok",
    "reels": "reels",
    "shorts": "shorts",
}

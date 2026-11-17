"""Catálogo de presets de subtítulo y formato (datos puros)."""

from __future__ import annotations

import pytest

from app.engines.editor.model import SubtitleStyle
from app.engines.editor.presets import (
    FORMAT_PRESETS,
    PRESET_FORMAT,
    SUBTITLE_PRESETS,
    subtitle_style_from_preset,
)


def test_every_subtitle_preset_builds_a_valid_style():
    assert set(SUBTITLE_PRESETS) == {
        "classic",
        "tiktok",
        "reels",
        "shorts",
        "cinema",
        "clean",
    }
    for name in SUBTITLE_PRESETS:
        style = subtitle_style_from_preset(name)
        assert isinstance(style, SubtitleStyle)
        # El campo preset del estilo coincide con la clave del catálogo.
        assert style.preset == name


def test_unknown_subtitle_preset_raises():
    with pytest.raises(ValueError):
        subtitle_style_from_preset("no-existe")


def test_vertical_presets_map_to_9_16_formats():
    assert set(PRESET_FORMAT) == {"tiktok", "reels", "shorts"}
    for preset, fmt in PRESET_FORMAT.items():
        meta = FORMAT_PRESETS[fmt]
        assert meta["width"] == 1080 and meta["height"] == 1920
        assert meta["aspect_ratio"] == "9:16"
        # Las zonas seguras verticales son positivas (hay margen de UI).
        assert meta["safe_top_frac"] > 0
        assert meta["safe_bottom_frac"] > 0


def test_format_presets_cover_16_9_1_1_and_9_16():
    assert FORMAT_PRESETS["16:9"]["aspect_ratio"] == "16:9"
    assert FORMAT_PRESETS["1:1"]["aspect_ratio"] == "1:1"
    # 16:9 y 1:1 no tienen zonas seguras (0).
    assert FORMAT_PRESETS["16:9"]["safe_top_frac"] == 0.0
    assert FORMAT_PRESETS["1:1"]["safe_bottom_frac"] == 0.0


def test_classic_preset_preserves_legacy_look():
    """classic produce el estilo clásico (caja negra translúcida por defecto, abajo)."""
    style = subtitle_style_from_preset("classic")
    assert style.preset == "classic"
    assert style.position == "bottom"
    assert style.bg_color is None
    assert style.outline is True
    # Decoración fina en default (no cambia el aspecto heredado).
    assert style.outline_width == 0.0
    assert style.shadow is False

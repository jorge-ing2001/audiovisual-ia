"""``PlaceholderProvider`` — genera un PNG real con Pillow (sin red).

Produce una imagen de marcador al aspecto pedido: un gradiente vertical derivado
determinísticamente de la consulta más el texto de la escena centrado encima. Es
la red de seguridad del pipeline: si el stock no tiene clave/red/resultados, cada
escena obtiene igualmente una imagen real en disco, de modo que el pipeline nunca
se bloquea (degradación de la §3 de la arquitectura).
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.providers.base import ImageAsset
from app.providers.image.aspect import aspect_to_dims


def _color_from_query(query: str, offset: int = 0) -> tuple[int, int, int]:
    """Color RGB determinista derivado de la consulta (gradiente reproducible)."""
    digest = hashlib.sha256(f"{query}:{offset}".encode()).digest()
    return (digest[0], digest[1], digest[2])


def _vertical_gradient(width: int, height: int, top: tuple[int, int, int],
                       bottom: tuple[int, int, int]) -> Image.Image:
    """Crea un gradiente vertical de ``top`` a ``bottom``."""
    base = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(base)
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return base


class PlaceholderProvider:
    """Implementa ``ImageProvider`` generando un PNG local con Pillow."""

    name = "placeholder"

    def image_for(
        self,
        query: str,
        *,
        aspect: str,
        out_dir: str,
        seed: int | None = None,
    ) -> ImageAsset:
        width, height = aspect_to_dims(aspect)
        top = _color_from_query(query, offset=seed or 0)
        bottom = _color_from_query(query, offset=(seed or 0) + 7)
        image = _vertical_gradient(width, height, top, bottom)

        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        # Envuelve el texto de la escena para que quepa en el ancho.
        wrapped = textwrap.fill(query.strip() or "escena", width=40)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        pos = ((width - text_w) // 2, (height - text_h) // 2)
        # Sombra + texto para legibilidad sobre cualquier gradiente.
        draw.multiline_text(
            (pos[0] + 2, pos[1] + 2), wrapped, font=font, fill=(0, 0, 0), align="center"
        )
        draw.multiline_text(
            pos, wrapped, font=font, fill=(255, 255, 255), align="center"
        )

        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256(f"{query}:{aspect}:{seed}".encode()).hexdigest()[:16]
        file_path = out_path / f"placeholder_{stem}.png"
        image.save(file_path, format="PNG")

        return ImageAsset(
            path=str(file_path),
            width=width,
            height=height,
            provider=self.name,
            query=query,
            seed=seed,
            meta={
                "source": "placeholder",
                "license": "generado-localmente",
                "attribution": None,
            },
        )

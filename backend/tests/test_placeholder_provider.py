"""PlaceholderProvider genera un PNG real no vacío (FEAT-003).

Sin red ni dependencias externas: Pillow produce un fichero PNG válido en disco al
aspecto pedido. Es la red de seguridad del pipeline cuando no hay stock.
"""

from __future__ import annotations

from PIL import Image

from app.providers.base import ImageAsset
from app.providers.image.placeholder import PlaceholderProvider


def test_placeholder_generates_real_png(tmp_path):
    provider = PlaceholderProvider()
    asset = provider.image_for(
        "un gato astronauta en el espacio", aspect="16:9", out_dir=str(tmp_path)
    )

    assert isinstance(asset, ImageAsset)
    assert asset.provider == "placeholder"
    path = tmp_path / asset.path.rsplit("/", 1)[-1]
    assert path.is_file()
    # Fichero no vacío.
    assert path.stat().st_size > 0

    # PNG válido y con las dimensiones del aspecto 16:9.
    with Image.open(path) as img:
        assert img.format == "PNG"
        assert img.size == (1920, 1080)


def test_placeholder_respects_vertical_aspect(tmp_path):
    asset = PlaceholderProvider().image_for("escena", aspect="9:16", out_dir=str(tmp_path))
    with Image.open(asset.path) as img:
        assert img.size == (1080, 1920)

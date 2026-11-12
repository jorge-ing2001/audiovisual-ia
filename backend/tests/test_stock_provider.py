"""StockImageProvider con httpx mockeado y degradación a placeholder (FEAT-003).

- Con clave y httpx mockeado, mapea la respuesta de Pixabay a un ImageAsset con la
  atribución en meta.
- Sin clave (ni red), degrada a placeholder generando un PNG real.
No se hace ninguna llamada real a Pexels/Pixabay.
"""

from __future__ import annotations

import httpx

from app.providers.image.stock import StockImageProvider

PIXABAY_PAYLOAD = {
    "hits": [
        {
            "id": 12345,
            "largeImageURL": "https://cdn.pixabay.com/photo/large.jpg",
            "webformatURL": "https://cdn.pixabay.com/photo/web.jpg",
            "user": "Fotografo_X",
            "pageURL": "https://pixabay.com/photos/gato-12345/",
        }
    ]
}

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # cabecera PNG + relleno (contenido no vacío)
)


class _Resp:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_stock_maps_pixabay_response_to_imageasset(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=False):
        calls.append(url)
        if url.startswith("https://pixabay.com/api"):
            return _Resp(payload=PIXABAY_PAYLOAD)
        # descarga del binario
        return _Resp(content=PNG_BYTES)

    monkeypatch.setattr("app.providers.image.stock.httpx.get", fake_get)

    provider = StockImageProvider(
        provider_order=["pixabay", "pexels"], pixabay_key="TEST_KEY", pexels_key=""
    )
    asset = provider.image_for("gato astronauta", aspect="16:9", out_dir=str(tmp_path))

    assert asset.provider == "stock"
    assert asset.meta["source"] == "pixabay"
    assert asset.meta["author"] == "Fotografo_X"
    assert asset.meta["license"] == "Pixabay License"
    assert asset.meta["attribution"]
    # Fichero descargado en disco, no vacío.
    from pathlib import Path

    assert Path(asset.path).is_file()
    assert Path(asset.path).stat().st_size > 0
    # No se consultó Pexels (pixabay fue primero y tuvo resultado).
    assert not any("api.pexels.com" in c for c in calls)


def test_stock_degrades_to_placeholder_without_key(tmp_path, monkeypatch):
    # Sin clave, el provider NO debe llamar a la red en absoluto.
    def boom(*args, **kwargs):
        raise AssertionError("no debería llamarse a httpx sin clave")

    monkeypatch.setattr("app.providers.image.stock.httpx.get", boom)

    provider = StockImageProvider(
        provider_order=["pixabay", "pexels"], pixabay_key="", pexels_key=""
    )
    asset = provider.image_for("paisaje", aspect="16:9", out_dir=str(tmp_path))

    assert asset.provider == "placeholder"
    from pathlib import Path

    assert Path(asset.path).is_file()
    assert Path(asset.path).stat().st_size > 0


def test_stock_degrades_to_placeholder_on_network_error(tmp_path, monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=False):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr("app.providers.image.stock.httpx.get", fake_get)

    provider = StockImageProvider(
        provider_order=["pixabay", "pexels"], pixabay_key="K1", pexels_key="K2"
    )
    asset = provider.image_for("montaña", aspect="16:9", out_dir=str(tmp_path))
    assert asset.provider == "placeholder"

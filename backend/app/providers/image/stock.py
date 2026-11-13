"""``StockImageProvider`` — imágenes de stock libres (Pixabay / Pexels).

Busca una imagen para la consulta en los proveedores de stock en el orden de
``AIV_IMAGE_PROVIDER_ORDER`` (por defecto pixabay, luego pexels), usando las claves
del entorno (``AIV_PIXABAY_API_KEY`` / ``AIV_PEXELS_API_KEY``) vía ``httpx``,
descarga el fichero al workspace y mapea la respuesta a un ``ImageAsset`` con la
atribución (autor/fuente/licencia) en ``meta`` — Pexels exige crédito.

Degradación (§3 de la arquitectura): si no hay clave, no hay red o ningún
proveedor devuelve resultados, cae a ``PlaceholderProvider`` para que el pipeline
nunca se bloquee por falta de imágenes. NUNCA se escribe una clave real en el
repo: las claves se leen solo del entorno/Settings.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from app.config import get_settings
from app.providers.base import ImageAsset
from app.providers.image.aspect import aspect_to_dims
from app.providers.image.placeholder import PlaceholderProvider

PIXABAY_URL = "https://pixabay.com/api/"
PEXELS_URL = "https://api.pexels.com/v1/search"


class StockImageProvider:
    """Implementa ``ImageProvider`` contra Pixabay/Pexels con fallback a placeholder."""

    name = "stock"

    def __init__(
        self,
        *,
        provider_order: list[str] | None = None,
        pixabay_key: str | None = None,
        pexels_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        if provider_order is None:
            provider_order = [
                p.strip()
                for p in settings.image_provider_order.split(",")
                if p.strip()
            ]
        self.provider_order = provider_order or ["pixabay", "pexels"]
        # Las claves se resuelven solo desde el entorno/Settings (nunca del repo).
        self.pixabay_key = settings.pixabay_api_key if pixabay_key is None else pixabay_key
        self.pexels_key = settings.pexels_api_key if pexels_key is None else pexels_key
        self.timeout = timeout
        self._fallback = PlaceholderProvider()

    # --- búsqueda por proveedor --------------------------------------------- #

    def _search_pixabay(self, query: str, orientation: str) -> dict | None:
        if not self.pixabay_key:
            return None
        params = {
            "key": self.pixabay_key,
            "q": query,
            "image_type": "photo",
            "orientation": orientation,
            "safesearch": "true",
            "per_page": 3,
        }
        resp = httpx.get(PIXABAY_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
        if not hits:
            return None
        hit = hits[0]
        return {
            "download_url": hit.get("largeImageURL") or hit.get("webformatURL"),
            "provider": "pixabay",
            "meta": {
                "source": "pixabay",
                "source_id": hit.get("id"),
                "author": hit.get("user"),
                "page_url": hit.get("pageURL"),
                # Pixabay publica bajo licencia propia tipo CC0 (crédito no exigido,
                # pero lo guardamos igualmente para trazabilidad/atribución).
                "license": "Pixabay License",
                "attribution": f"{hit.get('user')} (Pixabay)" if hit.get("user") else "Pixabay",
            },
        }

    def _search_pexels(self, query: str, orientation: str) -> dict | None:
        if not self.pexels_key:
            return None
        params = {"query": query, "orientation": orientation, "per_page": 3}
        headers = {"Authorization": self.pexels_key}
        resp = httpx.get(PEXELS_URL, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        photos = resp.json().get("photos") or []
        if not photos:
            return None
        photo = photos[0]
        src = photo.get("src") or {}
        return {
            "download_url": src.get("large2x") or src.get("large") or src.get("original"),
            "provider": "pexels",
            "meta": {
                "source": "pexels",
                "source_id": photo.get("id"),
                "author": photo.get("photographer"),
                "author_url": photo.get("photographer_url"),
                "page_url": photo.get("url"),
                # Pexels EXIGE crédito al autor y a Pexels.
                "license": "Pexels License",
                "attribution": (
                    f"Foto de {photo.get('photographer')} en Pexels"
                    if photo.get("photographer")
                    else "Pexels"
                ),
            },
        }

    def _orientation(self, aspect: str) -> str:
        width, height = aspect_to_dims(aspect)
        if width > height:
            return "horizontal"
        if height > width:
            return "vertical"
        return "all"

    # --- API pública --------------------------------------------------------- #

    def image_for(
        self,
        query: str,
        *,
        aspect: str,
        out_dir: str,
        seed: int | None = None,
    ) -> ImageAsset:
        orientation = self._orientation(aspect)
        searchers = {"pixabay": self._search_pixabay, "pexels": self._search_pexels}

        for name in self.provider_order:
            search = searchers.get(name)
            if search is None:
                continue
            try:
                found = search(query, orientation)
            except (httpx.HTTPError, ValueError):
                # Sin red / error del proveedor: probar el siguiente y, si no, placeholder.
                continue
            if not found or not found.get("download_url"):
                continue
            try:
                asset = self._download(found, query, aspect, out_dir, seed)
            except (httpx.HTTPError, OSError):
                continue
            if asset is not None:
                return asset

        # Degradación: ningún proveedor sirvió -> placeholder real.
        return self._fallback.image_for(query, aspect=aspect, out_dir=out_dir, seed=seed)

    def _download(
        self, found: dict, query: str, aspect: str, out_dir: str, seed: int | None
    ) -> ImageAsset | None:
        resp = httpx.get(found["download_url"], timeout=self.timeout, follow_redirects=True)
        resp.raise_for_status()
        content = resp.content
        if not content:
            return None

        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        raw_key = f"{query}:{aspect}:{seed}:{found['provider']}"
        stem = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
        file_path = out_path / f"stock_{found['provider']}_{stem}.jpg"
        file_path.write_bytes(content)

        width, height = aspect_to_dims(aspect)
        return ImageAsset(
            path=str(file_path),
            width=width,
            height=height,
            provider=self.name,
            query=query,
            seed=seed,
            meta={**found["meta"], "stock_provider": found["provider"]},
        )

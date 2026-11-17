"""Endpoint de ajustes de solo lectura de backends de imagen (FEAT-007).

Con el fixture ``client`` (TestClient sobre BD temporal) se verifica que
``GET /settings/image-backends``:
  - lista ``sd_local`` entre los backends disponibles (junto a stock/placeholder);
  - el activo por defecto es ``stock``;
  - expone los flags NO sensibles esperados (sd_local_enabled/url/max_concurrency);
  - NO expone NINGUNA clave/secreto (ninguna *_api_key ni su valor).
"""

from __future__ import annotations

import json


def test_image_backends_lists_sd_local_and_active_stock(client):
    resp = client.get("/settings/image-backends")
    assert resp.status_code == 200
    data = resp.json()

    assert data["capability"] == "image"
    assert data["active"] == "stock"  # default local/gratuito (invariante punto 6)

    available = data["available"]
    assert "sd_local" in available
    assert "stock" in available
    assert "placeholder" in available
    assert "fake" not in available  # el backend de test no se expone

    flags = data["flags"]
    assert flags["sd_local_enabled"] is False
    assert flags["sd_local_url"] == "http://localhost:8188"
    assert flags["sd_max_concurrency"] == 1


def test_image_backends_never_exposes_secrets(client):
    resp = client.get("/settings/image-backends")
    assert resp.status_code == 200

    # Ninguna clave de pago/gratuita aparece ni como nombre de campo ni como valor.
    blob = json.dumps(resp.json()).lower()
    for secret in ("api_key", "pixabay", "pexels", "openai", "stability", "elevenlabs"):
        assert secret not in blob, f"el endpoint no debe exponer '{secret}'"

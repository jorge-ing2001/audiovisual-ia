"""KokoroTTS real: WAV + dur.json con Kokoro offline de lemo-opuscar (FEAT-004).

Intenta sintetizar una línea corta con el código MIT real (``core/tts/tts.py``). Si
lemo-opuscar no está configurado (AIV_LEMO_OPUSCAR_PATH) o el modelo offline /
dependencias (kokoro-onnx, espeak-ng) no están disponibles, el test se SALTA con la
razón concreta. Nunca se simula el audio: o se genera un WAV real o se salta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.base import VoiceLine
from app.providers.tts._lemo import LemoNotAvailable
from app.providers.tts.kokoro import KokoroTTS


def test_kokoro_produces_real_wav_and_duration(temp_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AIV_LEMO_OPUSCAR_PATH", "/projects/sandbox/lemo-opuscar")
    import app.config as config

    config._settings = None

    provider = KokoroTTS()
    lines = [VoiceLine(text="Hello world, this is a short test.", language="en-us")]
    try:
        assets = provider.synthesize(lines, str(tmp_path))
    except LemoNotAvailable as exc:
        pytest.skip(f"Kokoro offline no disponible: {exc}")

    assert len(assets) == 1
    asset = assets[0]
    assert Path(asset.path).is_file()
    assert Path(asset.path).stat().st_size > 0
    assert asset.duration_s > 0
    assert (tmp_path / "dur.json").is_file()
    # Atribución del origen MIT en meta.
    assert asset.meta["license"].startswith("MIT")

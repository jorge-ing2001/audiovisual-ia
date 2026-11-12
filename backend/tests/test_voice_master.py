"""Cadena de limpieza + masterización a ~-14 LUFS (FEAT-004, técnica de voz.py MIT).

Genera un tono real con numpy/soundfile, lo pasa por ``VoiceMaster.master_file`` (limpieza
+ loudnorm de 2 pasadas + limitador) y mide la loudness integrada del resultado con
ffmpeg/ebur128. El objetivo es -14 LUFS con una tolerancia razonable.

Si ffmpeg (AIV_FFMPEG_BIN) o numpy/soundfile no están, el test se SALTA con una razón
explícita, sin simular la medición.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import get_settings

np = pytest.importorskip("numpy", reason="numpy no instalado (tier de voz)")
sf = pytest.importorskip("soundfile", reason="soundfile no instalado (tier de voz)")


def _write_tone(path, *, freq=220.0, seconds=2.0, amp=0.3, sr=24000):
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    # Voz "sucia": tono + un poco de ruido blanco para que la limpieza tenga qué hacer.
    tone = amp * np.sin(2 * np.pi * freq * t)
    noise = 0.02 * np.random.default_rng(0).standard_normal(t.shape)
    sig = (tone + noise).astype("float32")
    sf.write(str(path), sig, sr)
    return str(path)


@pytest.fixture()
def master(temp_env):
    from app.engines.voice.master import VoiceMaster

    if shutil.which(get_settings().ffmpeg_bin) is None:
        pytest.skip(
            f"ffmpeg no disponible en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN (sandbox: /projects/sandbox/.localbin/ffmpeg)."
        )
    return VoiceMaster()


def test_master_file_reaches_minus_14_lufs(master, tmp_path):
    src = _write_tone(tmp_path / "raw.wav")
    result = master.master_file(src, str(tmp_path / "mastered.wav"))

    assert Path(result.path).is_file()
    assert Path(result.path).stat().st_size > 0
    # Tolerancia de ±1 LU (margen de aviso de mux.sh).
    assert abs(result.integrated_lufs - (-14.0)) <= 1.0


def test_master_without_cleanup_also_reaches_target(master, tmp_path):
    from app.engines.voice.master import CleanupOptions

    src = _write_tone(tmp_path / "raw.wav")
    result = master.master_file(
        src, str(tmp_path / "m.wav"), cleanup=CleanupOptions(enabled=False)
    )
    assert abs(result.integrated_lufs - (-14.0)) <= 1.0


def test_chain_contains_cleanup_and_loudnorm_filters(master):
    from app.engines.voice.master import CleanupOptions

    chain = master.chain("mix", "out", measured=None, cleanup=CleanupOptions())
    assert chain.startswith("[mix]") and chain.endswith("[out]")
    assert "highpass" in chain
    assert "afftdn" in chain
    assert "acompressor" in chain
    assert "loudnorm" in chain
    assert "alimiter" in chain


def test_chain_cleanup_disabled_skips_voice_filters(master):
    from app.engines.voice.master import CleanupOptions

    chain = master.chain("mix", "out", measured=None, cleanup=CleanupOptions(enabled=False))
    assert "highpass" not in chain
    assert "afftdn" not in chain
    assert "loudnorm" in chain  # la masterización siempre está

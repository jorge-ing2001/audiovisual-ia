"""AudioMixer: mezcla y normalización a ~-14 LUFS con ffmpeg (FEAT-004).

Genera un tono real con numpy/soundfile, lo mezcla y mide la loudness integrada del
resultado con ffprobe/ebur128 (vía el propio mixer). El objetivo es -14 LUFS con una
tolerancia razonable (patrón de core/render/mux.sh de lemo-opuscar).

Si ffmpeg (AIV_FFMPEG_BIN) o numpy/soundfile no están disponibles, el test se SALTA
con una razón explícita, sin simular la medición.
"""

from __future__ import annotations

import shutil

import pytest

from app.config import get_settings

np = pytest.importorskip("numpy", reason="numpy no instalado (tier de voz)")
sf = pytest.importorskip("soundfile", reason="soundfile no instalado (tier de voz)")


def _write_tone(path, *, freq=220.0, seconds=2.0, amp=0.3, sr=24000):
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    tone = (amp * np.sin(2 * np.pi * freq * t)).astype("float32")
    sf.write(str(path), tone, sr)
    return str(path)


@pytest.fixture()
def mixer(temp_env):
    from app.engines.voice.mixer import AudioMixer

    if shutil.which(get_settings().ffmpeg_bin) is None:
        pytest.skip(
            f"ffmpeg no disponible en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN (sandbox: /projects/sandbox/.localbin/ffmpeg)."
        )
    return AudioMixer()


def test_single_track_normalized_to_minus_14_lufs(mixer, tmp_path):
    from app.engines.voice.mixer import MixTrack

    voice = _write_tone(tmp_path / "voice.wav")
    result = mixer.mix([MixTrack(path=voice)], str(tmp_path / "mix.wav"))

    from pathlib import Path

    assert Path(result.path).is_file()
    assert Path(result.path).stat().st_size > 0
    # Tolerancia de ±1 LU (el mismo margen de aviso que usa mux.sh).
    assert abs(result.integrated_lufs - (-14.0)) <= 1.0


def test_mix_of_voice_music_and_sfx(mixer, tmp_path):
    from app.engines.voice.mixer import MixTrack

    voice = _write_tone(tmp_path / "voice.wav", freq=220.0, amp=0.3)
    music = _write_tone(tmp_path / "music.wav", freq=440.0, amp=0.2)
    sfx = _write_tone(tmp_path / "sfx.wav", freq=880.0, amp=0.1, seconds=1.0)

    result = mixer.mix(
        [
            MixTrack(path=voice, gain=1.0),
            MixTrack(path=music, gain=0.3),
            MixTrack(path=sfx, gain=0.5),
        ],
        str(tmp_path / "mix.wav"),
    )
    assert result.meta["tracks"] == 3
    assert abs(result.integrated_lufs - (-14.0)) <= 1.0


def test_mix_requires_at_least_one_track(mixer, tmp_path):
    with pytest.raises(ValueError):
        mixer.mix([], str(tmp_path / "x.wav"))

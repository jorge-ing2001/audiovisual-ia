"""Backend opcional de voz propia grabada (FEAT-004).

El usuario sube un audio grabado; ``RecordedVoiceEngine`` lo limpia/masteriza (master.py),
lo transcribe con el ASR del registry y produce los mismos Assets que el camino TTS:
voz (masterizada), .srt y palabras.json.

Se usa un ASRProvider de test (tiempos deterministas) para no depender de modelos reales.
La masterización requiere ffmpeg real: si no está (AIV_FFMPEG_BIN), se salta con razón.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.config import get_settings
from app.models.entities import Project
from app.providers import registry
from app.providers.base import WordTiming
from app.storage.db import session_scope
from app.storage.media import ensure_project_workspace

np = pytest.importorskip("numpy", reason="numpy no instalado (tier de voz)")
sf = pytest.importorskip("soundfile", reason="soundfile no instalado (tier de voz)")


class _FakeASR:
    name = "fakeasr_rec"

    def transcribe(self, audio_path):
        return [
            WordTiming("Hola", 0.0, 0.4),
            WordTiming("Pyton", 0.4, 0.9),
        ]


@pytest.fixture()
def recorded_env(db_ready, monkeypatch):
    if shutil.which(get_settings().ffmpeg_bin) is None:
        pytest.skip(
            f"ffmpeg no disponible en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN (sandbox: /projects/sandbox/.localbin/ffmpeg)."
        )
    registry.register("asr", "fakeasr_rec", _FakeASR)
    monkeypatch.setenv("AIV_ASR_BACKEND", "fakeasr_rec")
    import app.config as config

    config._settings = None
    yield
    registry.PROVIDERS["asr"].pop("fakeasr_rec", None)
    config._settings = None


def _write_recording(path, *, sr=24000, seconds=1.5):
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    sig = (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype("float32")
    sf.write(str(path), sig, sr)
    return str(path)


def test_recorded_voice_produces_voice_srt_and_words(recorded_env, tmp_path):
    from app.engines.voice.recorded import RecordedVoiceEngine

    with session_scope() as session:
        project = Project(name="Grabada", prompt="p")
        session.add(project)
        session.flush()
        project_id = project.id
        ensure_project_workspace(project_id)

    recording = _write_recording(tmp_path / "mi_voz.wav")

    with session_scope() as session:
        project = session.get(Project, project_id)
        result = RecordedVoiceEngine().process(
            project, session, recording, corrections={"pyton": "Python"}
        )
        assert result.voice_asset_id is not None
        assert result.srt_asset_id is not None
        words_path = result.words_path

    # Voz masterizada existe.
    with session_scope() as session:
        project = session.get(Project, project_id)
        voices = [a for a in project.assets if a.kind == "voice"]
        srts = [a for a in project.assets if a.kind == "srt"]
        assert len(voices) == 1 and voices[0].provider == "recorded"
        assert Path(voices[0].path).is_file()
        assert len(srts) == 1

    # palabras.json bien formado y con la corrección aplicada (Pyton -> Python).
    data = json.loads(Path(words_path).read_text(encoding="utf-8"))
    texts = [w["text"] for w in data]
    assert "Python" in texts and "Pyton" not in texts
    for w in data:
        assert isinstance(w["startMs"], int) and isinstance(w["endMs"], int)
        assert w["endMs"] >= w["startMs"]

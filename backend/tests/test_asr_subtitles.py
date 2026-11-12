"""Subtítulos vía ASR: VoiceEngine.generate_subtitles (FEAT-004).

Con un ASRProvider de test (tiempos deterministas) y un Asset de voz, el motor genera
un .srt persistido. Con el ASR real no disponible (faster-whisper/modelo), devuelve
None en vez de inventar tiempos (no se simula). También se cubre el proveedor real con
un skip explícito cuando faster-whisper o su modelo no están.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engines.voice import VoiceEngine
from app.models.entities import Asset, Project, Scene
from app.providers import registry
from app.providers.base import WordTiming
from app.storage.db import session_scope
from app.storage.media import ensure_project_workspace


class _FakeASR:
    name = "fakeasr"

    def transcribe(self, audio_path):
        return [
            WordTiming("Hola", 0.0, 0.4),
            WordTiming("mundo", 0.4, 0.9),
        ]


@pytest.fixture()
def fake_asr_backend(db_ready, monkeypatch):
    registry.register("asr", "fakeasr", _FakeASR)
    monkeypatch.setenv("AIV_ASR_BACKEND", "fakeasr")
    import app.config as config

    config._settings = None
    yield
    registry.PROVIDERS["asr"].pop("fakeasr", None)
    config._settings = None


def _project_with_voice(session):
    project = Project(name="P", prompt="p")
    session.add(project)
    session.flush()
    scene = Scene(project_id=project.id, order=0, narration_text="Hola mundo")
    session.add(scene)
    session.flush()
    voice_dir = ensure_project_workspace(project.id) / "voice"
    wav = Path(voice_dir) / "line_0.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 40)
    session.add(
        Asset(
            project_id=project.id,
            scene_id=scene.id,
            kind="voice",
            path=str(wav),
            provider="faketts",
            meta={"duration_s": 1.0},
        )
    )
    session.flush()
    return project.id


def test_generate_subtitles_writes_srt(fake_asr_backend):
    with session_scope() as session:
        project_id = _project_with_voice(session)

    with session_scope() as session:
        project = session.get(Project, project_id)
        asset = VoiceEngine().generate_subtitles(project, session)
        assert asset is not None
        assert asset.kind == "srt"
        srt_path = asset.path

    content = Path(srt_path).read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in content
    assert "Hola mundo" in content


def test_generate_subtitles_returns_none_without_voice(fake_asr_backend):
    with session_scope() as session:
        project = Project(name="Empty", prompt="p")
        session.add(project)
        session.flush()
        result = VoiceEngine().generate_subtitles(project, session)
        assert result is None


def test_faster_whisper_provider_skips_when_unavailable():
    """El proveedor ASR real: si faster-whisper/modelo no está, se salta con razón."""
    from app.providers.asr.faster_whisper import AsrNotAvailable, FasterWhisper

    provider = FasterWhisper()
    try:
        provider._load()
    except AsrNotAvailable as exc:
        pytest.skip(f"faster-whisper/modelo no disponible: {exc}")
    # Si el modelo cargó de verdad, no hay nada que falsear: la carga ya es la prueba.
    assert provider._model is not None

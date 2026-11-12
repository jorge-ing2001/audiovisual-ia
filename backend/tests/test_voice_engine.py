"""Motor 5 — VoiceEngine: síntesis, persistencia y degradación kokoro->edge (FEAT-004).

- Con un TTS de test registrado, la narración de las escenas produce un
  Asset(kind='voice') por línea con su duración.
- Cuando el backend activo es 'kokoro' y el modelo offline no está disponible
  (LemoNotAvailable), el motor degrada a 'edge' automáticamente (§3).
- register_audio_asset persiste música/SFX.

No se simula audio: el proveedor de test escribe un WAV mínimo real en disco.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engines.voice import VoiceEngine
from app.models.entities import Asset, Project, Scene, Script
from app.providers import registry
from app.providers.base import AudioAsset, VoiceLine
from app.providers.tts._lemo import LemoNotAvailable
from app.storage.db import session_scope


class _FakeFileTTS:
    """TTS de test: escribe un WAV mínimo real por línea (no simula, crea fichero)."""

    name = "faketts"
    calls = 0

    def synthesize(self, lines, out_dir):
        type(self).calls += 1
        out: list[AudioAsset] = []
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        for i, line in enumerate(lines):
            path = Path(out_dir) / f"faketts_{i}.wav"
            path.write_bytes(b"RIFF" + b"\x00" * 40)
            out.append(
                AudioAsset(
                    path=str(path),
                    duration_s=float(len(line.text)) / 15.0,
                    provider=self.name,
                    sample_rate=24000,
                )
            )
        return out


class _MissingKokoroTTS:
    """Simula a Kokoro SIN modelo offline: siempre lanza LemoNotAvailable."""

    name = "kokoro"

    def synthesize(self, lines, out_dir):
        raise LemoNotAvailable("modelo Kokoro offline no disponible (test)")


@pytest.fixture()
def fake_tts_backend(db_ready, monkeypatch):
    _FakeFileTTS.calls = 0
    registry.register("tts", "faketts", _FakeFileTTS)
    monkeypatch.setenv("AIV_TTS_BACKEND", "faketts")
    import app.config as config

    config._settings = None
    yield _FakeFileTTS
    registry.PROVIDERS["tts"].pop("faketts", None)
    config._settings = None


def _make_project_with_scenes(session):
    project = Project(name="P", prompt="una historia", aspect_ratio="16:9")
    session.add(project)
    session.flush()
    session.add(Script(project_id=project.id, language="es", version=1))
    for order, text in enumerate(["Hola mundo.", "Segunda escena narrada."]):
        session.add(
            Scene(
                project_id=project.id,
                order=order,
                narration_text=text,
                visual_prompt="algo",
            )
        )
    session.flush()
    return project.id


def test_synthesize_project_creates_voice_assets(fake_tts_backend):
    with session_scope() as session:
        project_id = _make_project_with_scenes(session)

    with session_scope() as session:
        project = session.get(Project, project_id)
        result = VoiceEngine().synthesize_project(project, session)
        assert len(result.asset_ids) == 2
        assert result.provider == "faketts"
        assert result.degraded is False

    with session_scope() as session:
        from sqlalchemy import select

        voices = list(
            session.execute(select(Asset).where(Asset.kind == "voice")).scalars()
        )
        assert len(voices) == 2
        for asset in voices:
            assert asset.provider == "faketts"
            assert "duration_s" in asset.meta
            assert Path(asset.path).is_file()


def test_kokoro_degrades_to_edge_when_offline_model_missing(db_ready, monkeypatch):
    """Backend activo kokoro + LemoNotAvailable => se usa edge sin fallar."""
    registry.register("tts", "kokoro", _MissingKokoroTTS)
    registry.register("tts", "edge", _FakeFileTTS)
    monkeypatch.setenv("AIV_TTS_BACKEND", "kokoro")
    import app.config as config

    config._settings = None
    try:
        audio = VoiceEngine().synthesize(
            [VoiceLine(text="hola", language="es")], str(_tmp()),
        )
        # Degradó a edge (nuestro _FakeFileTTS) y produjo un WAV real.
        assert len(audio) == 1
        assert Path(audio[0].path).is_file()
    finally:
        # Restaurar las factorías reales.
        from app.providers.tts.edge import EdgeTTS
        from app.providers.tts.kokoro import KokoroTTS

        registry.register("tts", "kokoro", KokoroTTS)
        registry.register("tts", "edge", EdgeTTS)
        config._settings = None


def test_register_audio_asset_music_and_sfx(fake_tts_backend, tmp_path):
    with session_scope() as session:
        project_id = _make_project_with_scenes(session)

    music_file = tmp_path / "bg.wav"
    music_file.write_bytes(b"RIFF" + b"\x00" * 40)

    with session_scope() as session:
        project = session.get(Project, project_id)
        asset = VoiceEngine().register_audio_asset(
            project, session, kind="music", path=str(music_file), provider="local"
        )
        assert asset.kind == "music"
        assert asset.provider == "local"


def _tmp():
    import tempfile

    return tempfile.mkdtemp()

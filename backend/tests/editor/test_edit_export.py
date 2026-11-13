"""Export del editor: re-render desde el ``EditProject`` (ffprobe o se salta con razón).

Patrón de ``tests/test_render_engine.py``: construye un ``EditProject`` mínimo con una
imagen placeholder real + voz WAV real y ejecuta ``RenderEngine.compose_edit``. Si la
composición Remotion ``Edit`` no está disponible (no existe aún —es de FEAT-003— o no hay
Chromium), ``compose_edit`` lanza ``RenderUnavailable`` y el test se SALTA con la razón
(nunca simula un MP4). El eslabón ``AudioMixer`` + ``_mux`` se ejercita por separado con
el compilador de audio del editor cuando hay ffmpeg.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import get_settings

np = pytest.importorskip("numpy", reason="numpy no instalado (tier de voz)")
sf = pytest.importorskip("soundfile", reason="soundfile no instalado (tier de voz)")


def _ffmpeg_available() -> bool:
    return shutil.which(get_settings().ffmpeg_bin) is not None


def _ffprobe_available() -> bool:
    return shutil.which(get_settings().ffprobe_bin) is not None


def _write_tone(path: Path, *, seconds: float = 2.0, freq: float = 220.0, sr: int = 24000) -> str:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * freq * t)).astype("float32")
    sf.write(str(path), tone, sr)
    return str(path)


def _probe_streams(ffprobe: str, path: str) -> list[str]:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _probe_duration(ffprobe: str, path: str) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    )
    try:
        return float(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


@pytest.fixture()
def render_env(temp_env):
    if not (_ffmpeg_available() and _ffprobe_available()):
        pytest.skip(
            f"ffmpeg/ffprobe no disponibles en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN/AIV_FFPROBE_BIN (sandbox: /projects/sandbox/.localbin)."
        )


# --------------------------------------------------------------------------- #
# 1. Eslabón AudioMixer + mux del editor (activo si hay ffmpeg, NO simula MP4)
# --------------------------------------------------------------------------- #


def test_editor_audio_compile_mix_and_mux(render_env, tmp_path):
    """compile_audio_tracks -> AudioMixer.mix -> _mux produce un MP4 con audio real."""
    from app.engines.editor.compile import compile_audio_tracks
    from app.engines.editor.model import EditClip, EditMeta, EditProject, EditTrack
    from app.engines.render import RenderEngine
    from app.engines.voice.mixer import AudioMixer

    settings = get_settings()

    # Voz real (tono) de 2 s referenciada por el clip (asset_id=1).
    tone = _write_tone(tmp_path / "voice.wav", seconds=2.0)

    edit = EditProject(
        project_id=1,
        meta=EditMeta(fps=30, width=320, height=240),
        tracks=[
            EditTrack(
                id="track_voice",
                kind="voice",
                clips=[
                    EditClip(
                        id="a0",
                        track_id="track_voice",
                        kind="voice",
                        start_s=0.0,
                        duration_s=2.0,
                        source_in_s=0.0,
                        source_out_s=2.0,
                        asset_id=1,
                        gain=1.0,
                    )
                ],
            )
        ],
    )

    tracks = compile_audio_tracks(edit, lambda aid: tone if aid == 1 else None)
    assert len(tracks) == 1
    mix = AudioMixer().mix(tracks, str(tmp_path / "edit_mix.wav"))
    assert Path(mix.path).is_file()

    # Vídeo mudo de prueba (lavfi) y mux con la mezcla del editor.
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=2:r=30",
            "-pix_fmt", "yuv420p", str(silent),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = tmp_path / "edit_final.mp4"
    RenderEngine()._mux(silent, mix.path, out)

    assert out.is_file() and out.stat().st_size > 0
    streams = _probe_streams(settings.ffprobe_bin, str(out))
    assert "video" in streams
    assert "audio" in streams
    assert abs(_probe_duration(settings.ffprobe_bin, str(out)) - 2.0) <= 0.3


# --------------------------------------------------------------------------- #
# 2. compose_edit completo (se salta con razón si la composición Edit no está)
# --------------------------------------------------------------------------- #


def test_compose_edit_full_render(render_env, db_ready, tmp_path):
    from app.engines.editor.convert import timeline_to_edit_project
    from app.engines.editor.store import save_edit_project
    from app.engines.render import RenderEngine, RenderUnavailable, TimelineBuilder
    from app.engines.render.engine import FfmpegError
    from app.models.entities import Asset, Project, Scene
    from app.providers.image.placeholder import PlaceholderProvider
    from app.storage.db import session_scope
    from app.storage.media import ensure_project_workspace

    settings = get_settings()

    with session_scope() as session:
        project = Project(name="edit-export", prompt="faro en la tormenta", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        scene = Scene(
            project_id=project.id,
            order=0,
            narration_text="Un faro resiste la tormenta.",
            visual_prompt="faro en la tormenta",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()

        img_dir = ensure_project_workspace(project.id) / "images"
        img = PlaceholderProvider().image_for(
            "faro en la tormenta", aspect="16:9", out_dir=str(img_dir)
        )
        voice = _write_tone(tmp_path / "voice.wav", seconds=2.0)
        session.add_all(
            [
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="image",
                    path=img.path,
                    provider="placeholder",
                ),
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="voice",
                    path=voice,
                    provider="kokoro",
                    meta={"duration_s": 2.0},
                ),
            ]
        )
        session.commit()
        session.refresh(project)

        # Deriva + persiste el EditProject (como hace POST /editor/open).
        timeline = TimelineBuilder().build(project, session)
        edit_project = timeline_to_edit_project(timeline, project, list(project.assets))
        save_edit_project(project, session, edit_project)

        try:
            result = RenderEngine().compose_edit(project, edit_project, session)
        except RenderUnavailable as exc:
            pytest.skip(
                "Composición Remotion 'Edit' no disponible en este entorno "
                "(es de FEAT-003 / necesita Chromium headless): "
                f"{exc}"
            )
        except FfmpegError as exc:  # pragma: no cover - solo si ffmpeg falla el mux
            pytest.fail(f"El mux de FFmpeg falló: {exc}")

        video = Path(result.video_path)
        assert video.is_file() and video.stat().st_size > 0
        streams = _probe_streams(settings.ffprobe_bin, str(video))
        assert "video" in streams
        assert "audio" in streams
        duration = _probe_duration(settings.ffprobe_bin, str(video))
        assert abs(duration - 2.0) <= 0.5
        assert result.meta.get("source") == "edit_export"
        # El Asset de vídeo nuevo es descargable por GET /projects/{id}/video.
        asset = session.get(Asset, result.asset_id)
        assert asset is not None and asset.kind == "video"

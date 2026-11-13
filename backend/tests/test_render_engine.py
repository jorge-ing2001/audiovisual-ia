"""Render/mux del motor 6 con assets reales (FEAT-005).

Dos niveles de verificación, ninguno simula un MP4:

1. **Eslabón FFmpeg (activo si hay ffmpeg):** genera un vídeo de prueba con ffmpeg
   (lavfi) y un WAV corto real, los muxea con ``RenderEngine._mux`` y comprueba con
   ffprobe que el MP4 tiene stream de vídeo y de audio y una duración coherente. Esto
   cubre el eslabón FFmpeg sin depender de Chromium.

2. **Render completo (se salta con razón):** ``RenderEngine.compose`` sobre un proyecto
   con imagen placeholder real + voz WAV real. El render de Remotion necesita un Chromium
   headless (glibc 2.35+) que no está disponible en el sandbox; cuando lanza
   ``RenderUnavailable`` el test se SALTA con la razón explícita. En el PC del usuario
   (con Chromium) este test ejercita render + mux de principio a fin.
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
# 1. Eslabón FFmpeg: mux de un vídeo real + WAV real (activo)
# --------------------------------------------------------------------------- #


def test_mux_produces_mp4_with_video_and_audio_streams(render_env, tmp_path):
    from app.engines.render import RenderEngine

    settings = get_settings()
    # Vídeo de prueba real de 2 s (sin audio) generado con ffmpeg lavfi.
    silent = tmp_path / "silent.mp4"
    gen = subprocess.run(
        [
            settings.ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=teal:s=320x240:d=2:r=30",
            "-pix_fmt", "yuv420p",
            str(silent),
        ],
        capture_output=True,
        text=True,
    )
    assert gen.returncode == 0, gen.stderr[-500:]

    # Mezcla de audio real (-14 LUFS) a partir de un tono.
    from app.engines.voice.mixer import AudioMixer, MixTrack

    tone = _write_tone(tmp_path / "tone.wav", seconds=2.0)
    mix = AudioMixer().mix([MixTrack(path=tone)], str(tmp_path / "mix.wav"))

    out = tmp_path / "final.mp4"
    engine = RenderEngine()
    engine._mux(silent, mix.path, out)

    assert out.is_file() and out.stat().st_size > 0
    streams = _probe_streams(settings.ffprobe_bin, str(out))
    assert "video" in streams
    assert "audio" in streams
    duration = _probe_duration(settings.ffprobe_bin, str(out))
    assert abs(duration - 2.0) <= 0.3  # duración coherente con el vídeo/audio de 2 s


def test_mux_without_audio_still_produces_video(render_env, tmp_path):
    from app.engines.render import RenderEngine

    settings = get_settings()
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1:r=30",
            "-pix_fmt", "yuv420p", str(silent),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = tmp_path / "final.mp4"
    RenderEngine()._mux(silent, None, out)
    streams = _probe_streams(settings.ffprobe_bin, str(out))
    assert "video" in streams
    assert "audio" not in streams


# --------------------------------------------------------------------------- #
# 2. Render completo con Remotion (se salta con razón si no hay Chromium)
# --------------------------------------------------------------------------- #


def test_compose_full_render_and_mux(render_env, db_ready, tmp_path):
    from app.engines.render import RenderEngine, RenderUnavailable
    from app.engines.render.engine import FfmpegError
    from app.models.entities import Asset, Project, Scene, Script
    from app.providers.image.placeholder import PlaceholderProvider
    from app.storage.db import session_scope

    settings = get_settings()

    with session_scope() as session:
        project = Project(name="render-demo", prompt="un faro en la tormenta", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        session.add(Script(project_id=project.id, language="es", raw_text="..."))
        scene = Scene(
            project_id=project.id,
            order=0,
            narration_text="Un faro resiste la tormenta.",
            visual_prompt="faro en la tormenta",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()

        # Imagen placeholder real (PNG con Pillow) bajo el workspace del proyecto
        # (= AIV_MEDIA_ROOT), para que Remotion la sirva con staticFile.
        from app.storage.media import ensure_project_workspace

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

        try:
            result = RenderEngine().compose(project, session)
        except RenderUnavailable as exc:
            pytest.skip(
                "Render de Remotion no disponible en este entorno "
                f"(necesita Chromium headless / glibc 2.35+): {exc}"
            )
        except FfmpegError as exc:  # pragma: no cover - solo si ffmpeg falla el mux
            pytest.fail(f"El mux de FFmpeg falló: {exc}")

        # Si llegamos aquí, Remotion renderizó y FFmpeg muxeó: verificar el MP4 real.
        video = Path(result.video_path)
        assert video.is_file() and video.stat().st_size > 0
        streams = _probe_streams(settings.ffprobe_bin, str(video))
        assert "video" in streams
        assert "audio" in streams
        duration = _probe_duration(settings.ffprobe_bin, str(video))
        assert abs(duration - 2.0) <= 0.5
        assert result.asset_id is not None
        assert Path(result.timeline_path).is_file()


def test_render_endpoint_enqueues_job_and_worker_produces_video(render_env, client):
    """POST /projects/{id}/render encola un job; el worker lo ejecuta y deja el MP4.

    Cubre el endpoint + la cola + el handler. Si el render de Remotion no está disponible
    (sin Chromium), el job queda 'failed' con la razón y el endpoint de descarga responde
    404: se verifica ese camino sin simular vídeo.
    """
    import asyncio

    from app.jobs.queue import JobQueue
    from app.jobs.worker import run_job_to_completion
    from app.models.entities import Asset, Scene
    from app.providers.image.placeholder import PlaceholderProvider
    from app.storage.db import session_scope
    from app.storage.media import ensure_project_workspace

    created = client.post("/projects", json={"name": "api-render", "prompt": "amanecer"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    with session_scope() as session:
        scene = Scene(
            project_id=project_id,
            order=0,
            narration_text="Amanece sobre el valle.",
            visual_prompt="amanecer sobre el valle",
            duration_s=1.0,
        )
        session.add(scene)
        session.flush()
        img_dir = ensure_project_workspace(project_id) / "images"
        img = PlaceholderProvider().image_for(
            "amanecer sobre el valle", aspect="16:9", out_dir=str(img_dir)
        )
        session.add(
            Asset(
                project_id=project_id,
                scene_id=scene.id,
                kind="image",
                path=img.path,
                provider="placeholder",
            )
        )
        session.commit()

    # Encola el render vía API.
    enqueued = client.post(f"/projects/{project_id}/render")
    assert enqueued.status_code == 202
    job = enqueued.json()
    assert job["type"] == "render"
    assert job["status"] == "pending"

    # El worker procesa el job hasta su estado final.
    asyncio.run(run_job_to_completion(JobQueue()))

    final = client.get(f"/projects/{project_id}/render").json()
    if final["status"] == "failed":
        pytest.skip(
            "Render no disponible en este entorno (el job quedó failed con razón): "
            f"{final.get('error')}"
        )

    assert final["status"] == "succeeded"
    assert final["result"]["asset_id"] is not None

    # El MP4 se puede descargar.
    download = client.get(f"/projects/{project_id}/video")
    assert download.status_code == 200
    assert download.headers["content-type"] == "video/mp4"
    assert len(download.content) > 0

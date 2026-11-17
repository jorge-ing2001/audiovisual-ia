"""Importar medios al editor: POST /projects/{id}/editor/media/upload (FEAT-007).

Generaliza la subida (hoy solo imagen) a audio/vídeo/imagen a nivel de PROYECTO. El
fichero se valida con Pillow (imagen) o **ffprobe** (audio/vídeo), se guarda bajo
``media/uploads/`` del workspace (dentro de ``AIV_MEDIA_ROOT``) y se crea el ``Asset`` con
el ``kind`` correcto y ``duration_s`` en ``meta``. Demuestra además que el asset subido es
servible por ``GET /assets/{id}/download`` (lo usa el Player).

Los fixtures de audio (WAV) y vídeo (MP4) se generan con ffmpeg real (``AIV_FFMPEG_BIN``);
si ffmpeg/ffprobe no están disponibles, los tests que los necesitan se SALTAN con razón
(sin simular la validación). En este sandbox ffmpeg/ffprobe SÍ están.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.config import get_settings


def _png_bytes(color=(10, 120, 200), size=(48, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _ffmpeg_or_skip() -> str:
    ffmpeg = get_settings().ffmpeg_bin
    if shutil.which(ffmpeg) is None:
        pytest.skip(
            f"ffmpeg no disponible en '{ffmpeg}'. Configura AIV_FFMPEG_BIN "
            "(sandbox: /projects/sandbox/.localbin/ffmpeg)."
        )
    return ffmpeg


def _make_wav(tmp_path: Path) -> bytes:
    ffmpeg = _ffmpeg_or_skip()
    out = tmp_path / "tone.wav"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "1", "-ar", "16000", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    if not out.is_file() or out.stat().st_size == 0:
        pytest.skip("ffmpeg no pudo generar el WAV de prueba.")
    return out.read_bytes()


def _make_mp4(tmp_path: Path) -> bytes:
    ffmpeg = _ffmpeg_or_skip()
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-pix_fmt", "yuv420p", "-shortest", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    if not out.is_file() or out.stat().st_size == 0:
        pytest.skip("ffmpeg no pudo generar el MP4 de prueba.")
    return out.read_bytes()


def _project(client) -> int:
    return client.post("/projects", json={"name": "Proyecto media"}).json()["id"]


def test_upload_image_media_creates_image_asset(client):
    pid = _project(client)
    resp = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("foto.png", _png_bytes(size=(64, 40)), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "image"
    assert asset["provider"] == "upload"
    assert asset["scene_id"] is None
    assert asset["meta"]["width"] == 64
    assert asset["meta"]["height"] == 40

    from app.storage.media import ensure_project_workspace, media_root

    path = Path(asset["path"])
    assert path.is_file()
    assert path.resolve().is_relative_to(media_root().resolve())
    uploads_dir = ensure_project_workspace(pid) / "media" / "uploads"
    assert path.resolve().is_relative_to(uploads_dir.resolve())


def test_upload_audio_wav_creates_music_asset_with_duration(client, tmp_path):
    pid = _project(client)
    wav = _make_wav(tmp_path)
    resp = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("tono.wav", wav, "audio/wav")},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "music"  # default role
    assert asset["meta"]["role"] == "music"
    assert asset["meta"]["has_audio"] is True
    assert asset["meta"]["duration_s"] > 0.5


def test_upload_audio_role_voice(client, tmp_path):
    pid = _project(client)
    wav = _make_wav(tmp_path)
    resp = client.post(
        f"/projects/{pid}/editor/media/upload?role=voice",
        files={"file": ("voz.wav", wav, "audio/wav")},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "voice"
    assert asset["meta"]["role"] == "voice"


def test_upload_video_mp4_creates_video_asset(client, tmp_path):
    pid = _project(client)
    mp4 = _make_mp4(tmp_path)
    resp = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("clip.mp4", mp4, "video/mp4")},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["kind"] == "video"
    assert asset["meta"]["has_video"] is True
    assert asset["meta"]["duration_s"] > 0.5
    assert asset["meta"]["width"] == 160
    assert asset["meta"]["height"] == 120


def test_uploaded_media_is_servable_by_download(client, tmp_path):
    pid = _project(client)
    wav = _make_wav(tmp_path)
    asset = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("tono.wav", wav, "audio/wav")},
    ).json()
    resp = client.get(f"/assets/{asset['id']}/download")
    assert resp.status_code == 200, resp.text
    assert len(resp.content) > 0


def test_upload_rejects_empty_file(client):
    pid = _project(client)
    resp = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("vacio.wav", b"", "audio/wav")},
    )
    assert resp.status_code == 400, resp.text
    assert "vac" in resp.json()["detail"].lower()


def test_upload_rejects_non_media(client):
    pid = _project(client)
    resp = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("notas.txt", b"esto no es media", "text/plain")},
    )
    assert resp.status_code == 400, resp.text
    assert "medio" in resp.json()["detail"].lower()


def test_upload_rejects_fake_audio_via_ffprobe(client):
    """Un fichero con extensión de audio pero contenido basura lo rechaza ffprobe."""
    _ffmpeg_or_skip()
    pid = _project(client)
    resp = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("falso.wav", b"RIFF no soy un wav de verdad", "audio/wav")},
    )
    assert resp.status_code == 400, resp.text


def test_upload_rejects_too_large(client, monkeypatch, tmp_path):
    from app.api import editor

    pid = _project(client)
    wav = _make_wav(tmp_path)
    monkeypatch.setattr(editor, "MAX_MEDIA_UPLOAD_BYTES", 16)
    assert len(wav) > 16
    resp = client.post(
        f"/projects/{pid}/editor/media/upload",
        files={"file": ("tono.wav", wav, "audio/wav")},
    )
    assert resp.status_code == 413, resp.text
    assert "máximo" in resp.json()["detail"].lower()


def test_upload_project_not_found(client):
    resp = client.post(
        "/projects/999999/editor/media/upload",
        files={"file": ("x.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 404, resp.text

"""Mediciones REALES del QA con un MP4 lavfi + endpoint con regla de frescura (FEAT-004).

1. **Mediciones (se saltan con razón si no hay ffmpeg/ffprobe):** genera un MP4 con lavfi
   (un tramo NEGRO al inicio + tono de audio) y verifica blackdetect, duración, resolución
   y loudness REALES vía ``engines.qa.measure``. NUNCA simula un MP4 ni una medición.

2. **Persistencia + endpoint:** persiste un ``qa_report`` en ``Asset.meta`` y comprueba la
   regla de frescura de ``GET /projects/{id}/render/qa`` (dos renders: devuelve el del
   último asset; si el último no tiene reporte, responde 'no_report' y NO el del anterior).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import get_settings


def _ffmpeg_available() -> bool:
    return shutil.which(get_settings().ffmpeg_bin) is not None


def _ffprobe_available() -> bool:
    return shutil.which(get_settings().ffprobe_bin) is not None


def _make_lavfi_mp4(path: Path, *, black_s: float = 1.5, total_s: float = 3.0) -> None:
    """Genera un MP4 320x180 (16:9) con un tramo NEGRO al inicio + tono de 220 Hz.

    Concatena ``black_s`` de negro con el resto de color para que blackdetect encuentre un
    tramo negro al principio, y añade un tono para poder medir loudness.
    """
    settings = get_settings()
    color_s = max(0.1, total_s - black_s)
    vf = (
        f"color=c=black:s=320x180:d={black_s}:r=30[b];"
        f"color=c=teal:s=320x180:d={color_s}:r=30[c];"
        f"[b][c]concat=n=2:v=1:a=0[v]"
    )
    cmd = [
        settings.ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:duration={total_s}",
        "-filter_complex",
        vf,
        "-map",
        "[v]",
        "-map",
        "0:a",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]


@pytest.fixture()
def qa_env():
    if not (_ffmpeg_available() and _ffprobe_available()):
        pytest.skip(
            f"ffmpeg/ffprobe no disponibles en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN/AIV_FFPROBE_BIN (sandbox: /projects/sandbox/.localbin)."
        )


# --------------------------------------------------------------------------- #
# 1. Mediciones reales sobre un MP4 lavfi
# --------------------------------------------------------------------------- #


def test_measure_probe_reads_dimensions_and_duration(qa_env, tmp_path):
    from app.engines.qa.measure import measure_probe

    mp4 = tmp_path / "clip.mp4"
    _make_lavfi_mp4(mp4, black_s=1.0, total_s=3.0)
    result = measure_probe(mp4)
    assert result.skip_reason is None
    probe = result.value
    assert probe.width == 320 and probe.height == 180
    assert probe.has_audio is True and probe.has_video is True
    assert abs(probe.duration_s - 3.0) <= 0.5


def test_measure_black_frames_detects_initial_black(qa_env, tmp_path):
    from app.engines.qa.measure import measure_black_frames

    mp4 = tmp_path / "clip.mp4"
    _make_lavfi_mp4(mp4, black_s=1.5, total_s=3.0)
    result = measure_black_frames(mp4)
    assert result.skip_reason is None
    segments = result.value
    assert len(segments) >= 1
    # El primer tramo negro empieza cerca de t=0.
    assert segments[0][0] <= 0.2


def test_measure_loudness_returns_lufs(qa_env, tmp_path):
    from app.engines.qa.measure import measure_loudness

    mp4 = tmp_path / "clip.mp4"
    _make_lavfi_mp4(mp4, black_s=0.5, total_s=3.0)
    result = measure_loudness(mp4, has_audio=True)
    assert result.skip_reason is None
    assert isinstance(result.value, float)


def test_measure_skips_when_video_missing(tmp_path):
    # Sin fichero (y sin depender de ffmpeg): skip_reason no_video.
    from app.engines.qa.measure import measure_black_frames, measure_probe

    missing = tmp_path / "nope.mp4"
    assert measure_probe(missing).skip_reason == "no_video"
    assert measure_black_frames(missing).skip_reason == "no_video"


def test_render_hash_is_sha256_of_file(tmp_path):
    import hashlib

    from app.engines.qa.measure import measure_render_hash

    f = tmp_path / "x.bin"
    f.write_bytes(b"hola-qa")
    expected = "sha256:" + hashlib.sha256(b"hola-qa").hexdigest()
    assert measure_render_hash(f) == expected
    assert measure_render_hash(tmp_path / "missing.bin") is None


def test_build_report_real_mp4_overall_and_hash(qa_env, db_ready, tmp_path):
    """Reporte REAL end-to-end sobre un MP4 lavfi: 16:9, duración coherente, hash sha256."""
    from app.engines.qa.report import build_qa_report
    from app.models.entities import Asset, Project, Scene
    from app.storage.db import session_scope

    mp4 = tmp_path / "final.mp4"
    _make_lavfi_mp4(mp4, black_s=0.3, total_s=3.0)

    with session_scope() as session:
        project = Project(name="qa-demo", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        session.add(Scene(project_id=project.id, order=0, duration_s=3.0))
        asset = Asset(project_id=project.id, kind="video", path=str(mp4))
        session.add(asset)
        session.commit()
        session.refresh(project)
        session.refresh(asset)

        report = build_qa_report(project, asset, session)

    assert report["schema"] == "qa_report/v1"
    assert report["asset_id"] == asset.id
    assert report["render_hash"].startswith("sha256:")
    ids = {c["id"] for c in report["checks"]}
    assert ids == {
        "loudness",
        "black_frames",
        "duration_match",
        "resolution_aspect",
        "subtitle_sync",
    }
    # 16:9 real -> resolution_aspect pass; duración ~3s vs 3s esperados -> pass.
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["resolution_aspect"]["status"] == "pass"
    assert by_id["duration_match"]["status"] in {"pass", "warn"}
    # Sin subtítulos -> subtitle_sync skipped (no_subtitles) => overall nunca 'pass' seco.
    assert by_id["subtitle_sync"]["status"] == "skipped"
    assert report["overall"] in {"warn", "fail", "pass_with_skips"}
    assert report["overall"] != "pass"


# --------------------------------------------------------------------------- #
# 2. Persistencia + endpoint con regla de frescura (sin ffmpeg)
# --------------------------------------------------------------------------- #


def _add_video_asset(session, project_id: int, path: str, qa_report: dict | None):
    from app.models.entities import Asset

    meta = {"qa_report": qa_report} if qa_report is not None else None
    asset = Asset(project_id=project_id, kind="video", path=path, meta=meta)
    session.add(asset)
    session.flush()
    return asset


def test_qa_endpoint_404_when_no_render(client):
    created = client.post("/projects", json={"name": "sin-render"})
    pid = created.json()["id"]
    resp = client.get(f"/projects/{pid}/render/qa")
    assert resp.status_code == 404


def test_qa_endpoint_freshness_returns_latest_render(client):
    """Dos renders: el endpoint devuelve el reporte del ÚLTIMO asset (por asset_id)."""
    from app.storage.db import session_scope

    created = client.post("/projects", json={"name": "dos-renders"})
    pid = created.json()["id"]

    with session_scope() as session:
        a1 = _add_video_asset(session, pid, "/tmp/r1.mp4", None)
        # El reporte del primer asset referencia su propio id.
        a1.meta = {"qa_report": {"asset_id": a1.id, "overall": "pass", "render_hash": None}}
        a2 = _add_video_asset(session, pid, "/tmp/r2.mp4", None)
        a2.meta = {
            "qa_report": {"asset_id": a2.id, "overall": "warn", "render_hash": None}
        }
        session.commit()
        a2_id = a2.id

    resp = client.get(f"/projects/{pid}/render/qa")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["report"]["asset_id"] == a2_id
    assert body["report"]["overall"] == "warn"


def test_qa_endpoint_no_report_for_latest_does_not_return_previous(client):
    """Si el ÚLTIMO render no tiene reporte, responde 'no_report' (nunca el anterior)."""
    from app.storage.db import session_scope

    created = client.post("/projects", json={"name": "ultimo-sin-reporte"})
    pid = created.json()["id"]

    with session_scope() as session:
        a1 = _add_video_asset(session, pid, "/tmp/r1.mp4", None)
        a1.meta = {"qa_report": {"asset_id": a1.id, "overall": "pass", "render_hash": None}}
        # Segundo render SIN qa_report.
        a2 = _add_video_asset(session, pid, "/tmp/r2.mp4", None)
        session.commit()
        a2_id = a2.id

    resp = client.get(f"/projects/{pid}/render/qa")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_report"
    assert body["asset_id"] == a2_id
    assert "report" not in body


def test_qa_endpoint_stale_report_when_hash_mismatch(client, tmp_path):
    """Si el MP4 existe y su hash difiere del render_hash del reporte, marca stale=true."""
    from app.storage.db import session_scope

    created = client.post("/projects", json={"name": "obsoleto"})
    pid = created.json()["id"]

    mp4 = tmp_path / "final.mp4"
    mp4.write_bytes(b"contenido-actual")

    with session_scope() as session:
        asset = _add_video_asset(session, pid, str(mp4), None)
        asset.meta = {
            "qa_report": {
                "asset_id": asset.id,
                "overall": "pass",
                "render_hash": "sha256:deadbeef",  # no coincide con el fichero real
            }
        }
        session.commit()

    resp = client.get(f"/projects/{pid}/render/qa")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["stale"] is True

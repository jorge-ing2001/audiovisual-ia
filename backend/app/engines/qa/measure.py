"""Mediciones REALES del QA con ffprobe/ffmpeg (motor de calidad, FEAT-004).

Cada función ejecuta la herramienta externa sobre el MP4 y devuelve los números crudos
(loudness, duración/dimensiones, tramos negros) o un ``skip_reason`` cuando no se puede
medir (binario ausente, MP4 inexistente, MP4 sin audio, ...). NUNCA finge una medición:
reutiliza los patrones ya presentes en el repo (``VoiceMaster``/``probe_media``/
``parse_silencedetect``) y acota el resultado para que ``checks.py`` lo clasifique como
``skipped`` con su razón.

Separada de ``checks.py`` (pura) para que la clasificación sea testeable sin ffmpeg y las
mediciones con un MP4 lavfi real (ver ``tests/test_qa_measure.py``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.engines.voice.master import VoiceMaster, parse_integrated_lufs
from app.engines.voice.trim import parse_silencedetect
from app.engines.qa.checks import BLACK_MIN_S
from app.storage.uploads import MediaProbeError, probe_media

__all__ = [
    "MeasureResult",
    "measure_loudness",
    "measure_probe",
    "measure_black_frames",
    "measure_render_hash",
]


@dataclass
class MeasureResult:
    """Resultado crudo de una medición. ``skip_reason`` != None => no se pudo medir."""

    value: object = None
    skip_reason: str | None = None
    meta: dict = field(default_factory=dict)


def _ffmpeg_ok(bin_path: str) -> bool:
    """¿Es ejecutable el binario de ffmpeg/ffprobe indicado?"""
    try:
        subprocess.run([bin_path, "-version"], capture_output=True, timeout=30)
        return True
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


def measure_probe(video_path: str | Path) -> MeasureResult:
    """Duración/dimensiones del MP4 con ffprobe (``probe_media``).

    ``value`` = ``MediaProbe`` si se midió; ``skip_reason`` ``no_video`` (fichero ausente)
    o ``ffprobe_unavailable`` si falta el binario.
    """
    path = Path(video_path)
    if not path.is_file():
        return MeasureResult(skip_reason="no_video")
    try:
        probe = probe_media(path)
    except MediaProbeError as exc:
        reason = "ffprobe_unavailable" if "No se encontró ffprobe" in str(exc) else "no_video"
        return MeasureResult(skip_reason=reason, meta={"error": str(exc)[:300]})
    return MeasureResult(value=probe)


def measure_loudness(video_path: str | Path, *, has_audio: bool | None = None) -> MeasureResult:
    """Loudness integrada (LUFS) del audio del MP4 vía ``VoiceMaster`` (ebur128).

    ``skip_reason``: ``no_video`` (fichero ausente), ``ffmpeg_unavailable`` (sin binario),
    ``no_audio`` (el MP4 no tiene pista de audio). ``value`` = LUFS (float) si se midió.
    """
    path = Path(video_path)
    if not path.is_file():
        return MeasureResult(skip_reason="no_video")
    if has_audio is False:
        return MeasureResult(skip_reason="no_audio")
    settings = get_settings()
    if not _ffmpeg_ok(settings.ffmpeg_bin):
        return MeasureResult(skip_reason="ffmpeg_unavailable")
    try:
        master = VoiceMaster()
        proc = subprocess.run(
            [
                settings.ffmpeg_bin,
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "ebur128",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (FileNotFoundError, OSError):
        return MeasureResult(skip_reason="ffmpeg_unavailable")
    except subprocess.SubprocessError as exc:
        return MeasureResult(skip_reason="no_audio", meta={"error": str(exc)[:300]})
    stderr = proc.stderr or ""
    # Sin stream de audio, ffmpeg no emite el resumen de ebur128 (o falla).
    if "Stream map '0:a'" in stderr or "does not contain any stream" in stderr:
        return MeasureResult(skip_reason="no_audio")
    value = parse_integrated_lufs(stderr)
    if value is None:
        return MeasureResult(skip_reason="no_audio", meta={"stderr": stderr[-300:]})
    _ = master  # VoiceMaster se usa para fijar binarios/parsers reutilizados
    return MeasureResult(value=value)


def measure_black_frames(
    video_path: str | Path, *, black_min_s: float | None = None
) -> MeasureResult:
    """Tramos negros del MP4 vía ffmpeg ``blackdetect`` (parseo tipo ``silencedetect``).

    ``value`` = lista de pares ``(black_start, black_end)`` (puede ser ``[]`` si no hay
    negros). ``skip_reason``: ``no_video`` o ``ffmpeg_unavailable``.
    """
    path = Path(video_path)
    if not path.is_file():
        return MeasureResult(skip_reason="no_video")
    settings = get_settings()
    if not _ffmpeg_ok(settings.ffmpeg_bin):
        return MeasureResult(skip_reason="ffmpeg_unavailable")
    threshold = black_min_s if black_min_s is not None else BLACK_MIN_S
    chain = f"blackdetect=d={threshold}:pic_th=0.98"
    try:
        proc = subprocess.run(
            [
                settings.ffmpeg_bin,
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-vf",
                chain,
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (FileNotFoundError, OSError):
        return MeasureResult(skip_reason="ffmpeg_unavailable")
    except subprocess.SubprocessError as exc:
        return MeasureResult(skip_reason="no_video", meta={"error": str(exc)[:300]})
    segments = parse_blackdetect(proc.stderr or "")
    return MeasureResult(value=segments)


def parse_blackdetect(stderr: str) -> list[tuple[float, float]]:
    """Parsea las líneas ``black_start:.. black_end:..`` de ``blackdetect``.

    Mismo espíritu que ``parse_silencedetect``: ffmpeg emite por cada tramo una línea
    ``[blackdetect @ ...] black_start:1.0 black_end:2.5 black_duration:1.5``.
    """
    segments: list[tuple[float, float]] = []
    for line in stderr.splitlines():
        line = line.strip()
        if "black_start:" not in line or "black_end:" not in line:
            continue
        try:
            start = float(line.split("black_start:")[1].strip().split()[0])
            end = float(line.split("black_end:")[1].strip().split()[0])
        except (IndexError, ValueError):
            continue
        segments.append((start, end))
    return segments


def measure_render_hash(video_path: str | Path) -> str | None:
    """SHA-256 del fichero MP4 (identifica el render exacto). ``None`` si no existe.

    Devuelve el hash con prefijo ``sha256:`` (shape del reporte §2.5). Lectura por bloques
    para no cargar el fichero completo en memoria.
    """
    import hashlib

    path = Path(video_path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


# `parse_silencedetect` se referencia como patrón homólogo del parser de blackdetect.
_PATTERN_REF = parse_silencedetect

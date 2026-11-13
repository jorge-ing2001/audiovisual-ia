"""Parser PURO de la salida de Blender headless (stdout/stderr) -> ``VideoClip`` / error.

Funciones puras sobre strings (no tocan disco, no invocan Blender):
  - ``parse_progress``: extrae el frame actual de líneas tipo ``Fra:N ...``.
  - ``parse_rendered_frames``: nº de frames guardados (líneas ``Saved: '...'``).
  - ``parse_warnings``: recoge warnings relevantes del stdout/stderr.
  - ``build_video_clip``: construye un ``VideoClip`` en éxito o lanza un error accionable
    (``Engine3DUnavailable``) si ``returncode != 0`` o la salida indica fallo.

El parsing se apoya en el formato de progreso estable de Blender (``Fra:<n>`` y
``Saved: '<path>'``); ver docs/ENGINE-3D-ARCHITECTURE.md §7.
"""

from __future__ import annotations

import re

from app.engines.engine3d.script_builder import frame_count
from app.engines.engine3d.types import Engine3DUnavailable, Scene3DSpec, VideoClip

__all__ = [
    "parse_progress",
    "parse_rendered_frames",
    "parse_warnings",
    "build_video_clip",
]

# "Fra:120 Mem:..." -> frame actual que Blender está procesando.
_FRA_RE = re.compile(r"\bFra:(\d+)\b")
# "Saved: '/ruta/0001.png'" o "Append frame 120" tras un render de animación.
_SAVED_RE = re.compile(r"Saved:\s*'([^']+)'")
_APPEND_FRAME_RE = re.compile(r"\bAppend frame\s+(\d+)\b")
_WARNING_RE = re.compile(r"^\s*(Warning:.*)$", re.MULTILINE)
_ERROR_RE = re.compile(r"^\s*(Error:.*)$", re.MULTILINE)


def parse_progress(stdout: str) -> int | None:
    """Último frame en proceso según las líneas ``Fra:N`` del stdout (``None`` si ninguna)."""
    last: int | None = None
    for match in _FRA_RE.finditer(stdout or ""):
        last = int(match.group(1))
    return last


def parse_rendered_frames(stdout: str) -> int:
    """Nº de frames efectivamente renderizados/guardados a partir del stdout.

    Prioriza las líneas ``Append frame N`` (render de animación FFMPEG), si no cuenta las
    líneas ``Saved: '...'``; como último recurso usa el mayor ``Fra:N`` observado.
    """
    text = stdout or ""
    appended = _APPEND_FRAME_RE.findall(text)
    if appended:
        return len(appended)
    saved = _SAVED_RE.findall(text)
    if saved:
        return len(saved)
    last = parse_progress(text)
    return last or 0


def parse_warnings(stdout: str, stderr: str = "") -> list[str]:
    """Lista de warnings (líneas ``Warning:``) encontradas en stdout/stderr."""
    blob = f"{stdout or ''}\n{stderr or ''}"
    return [w.strip() for w in _WARNING_RE.findall(blob)]


def _parse_errors(stdout: str, stderr: str) -> list[str]:
    blob = f"{stdout or ''}\n{stderr or ''}"
    return [e.strip() for e in _ERROR_RE.findall(blob)]


def build_video_clip(
    spec: Scene3DSpec,
    output_path: str,
    stdout: str,
    stderr: str,
    returncode: int,
    *,
    provider: str | None = None,
) -> VideoClip:
    """Construye el ``VideoClip`` de una escena a partir de la salida de Blender.

    En éxito (``returncode == 0`` y hay frames) devuelve un ``VideoClip`` con el nº de
    frames y warnings en ``meta``. Ante fallo (``returncode != 0`` o 0 frames) lanza
    ``Engine3DUnavailable`` con un mensaje accionable (nunca simula un clip).
    """
    provider_name = provider or (
        "blender-cycles" if (spec.render_engine or "").lower() == "cycles" else "blender-eevee"
    )

    if returncode != 0:
        errors = _parse_errors(stdout, stderr)
        detail = "; ".join(errors) if errors else (stderr or stdout or "").strip()[-600:]
        raise Engine3DUnavailable(
            f"Blender falló al renderizar la escena {spec.scene_id} "
            f"(código {returncode}): {detail or 'sin detalle en la salida'}. "
            "Revisa AIV_BLENDER_BIN, los assets en AIV_BLENDER_ASSETS_DIR y el log de Blender."
        )

    rendered = parse_rendered_frames(stdout)
    expected = frame_count(spec.duration_s, spec.fps)
    if rendered <= 0:
        raise Engine3DUnavailable(
            f"Blender terminó con código 0 pero no guardó ningún frame para la escena "
            f"{spec.scene_id} (esperados {expected}). La salida no indica frames guardados; "
            "render 3D no disponible en este entorno (no se simula un clip)."
        )

    warnings = parse_warnings(stdout, stderr)
    return VideoClip(
        path=output_path,
        scene_id=spec.scene_id,
        duration_s=spec.duration_s,
        width=spec.width,
        height=spec.height,
        fps=spec.fps,
        provider=provider_name,
        meta={
            "frames_rendered": rendered,
            "frames_expected": expected,
            "render_engine": spec.render_engine,
            "warnings": warnings,
            "returncode": returncode,
        },
    )

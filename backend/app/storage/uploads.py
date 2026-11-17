"""Helpers compartidos de subida de ficheros (imagen / audio / vídeo).

Centraliza las constantes y utilidades de validación de subidas que antes vivían en
``app.api.generation`` (imagen/overlay) para poder reutilizarlas desde
``app.api.editor`` (importar medios: audio/vídeo/imagen a nivel de proyecto) sin
duplicarlas. El refactor NO cambia el comportamiento de las subidas existentes:
``generation.py`` sigue exponiendo los mismos nombres (los importa de aquí) y los tests
de ``upload_scene_image``/``upload_overlay_image`` siguen pasando.

Novedades para el editor (FEAT-007):
- Listas de extensiones/content-types para VÍDEO (``.mp4/.mov/.webm/.mkv``) y AUDIO
  (``.mp3/.wav/.m4a/.aac/.ogg/.flac``).
- ``_MAX_MEDIA_UPLOAD_BYTES`` (tope de media mayor que el de imagen; documentado).
- ``probe_media(path)``: valida con **ffprobe** (``AIV_FFPROBE_BIN``) que el fichero tiene
  streams válidos y lee ``duration_s``/dimensiones. Si ffprobe no está disponible, lanza
  ``MediaProbeError`` con un mensaje accionable (NO finge la validación).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

__all__ = [
    "ALLOWED_IMAGE_EXTS",
    "ALLOWED_IMAGE_CONTENT_TYPES",
    "ALLOWED_OVERLAY_EXTS",
    "ALLOWED_OVERLAY_CONTENT_TYPES",
    "ALLOWED_VIDEO_EXTS",
    "ALLOWED_VIDEO_CONTENT_TYPES",
    "ALLOWED_AUDIO_EXTS",
    "ALLOWED_AUDIO_CONTENT_TYPES",
    "MAX_UPLOAD_BYTES",
    "MAX_MEDIA_UPLOAD_BYTES",
    "safe_stem",
    "MediaProbeError",
    "MediaProbe",
    "probe_media",
]

# --------------------------------------------------------------------------- #
# Imagen / overlay (extraído de generation.py sin cambio de comportamiento)
# --------------------------------------------------------------------------- #

#: Extensiones de imagen admitidas para recursos visuales del usuario.
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
#: Content-types de imagen admitidos.
ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
#: Overlays/logos/marcas de agua: además de los rasters admite SVG (vectorial),
#: que se valida por MIME + tamaño (Pillow no decodifica SVG).
ALLOWED_OVERLAY_EXTS = ALLOWED_IMAGE_EXTS | {".svg"}
ALLOWED_OVERLAY_CONTENT_TYPES = ALLOWED_IMAGE_CONTENT_TYPES | {"image/svg+xml"}

#: Tamaño máximo razonable de una IMAGEN subida (15 MB).
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# --------------------------------------------------------------------------- #
# Audio / vídeo (FEAT-007: importar medios al editor)
# --------------------------------------------------------------------------- #

#: Extensiones de VÍDEO admitidas para importar al editor.
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
#: Content-types de vídeo admitidos (orientativos: la validación real la hace ffprobe).
ALLOWED_VIDEO_CONTENT_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-matroska",
}

#: Extensiones de AUDIO admitidas para importar al editor.
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
#: Content-types de audio admitidos (orientativos: la validación real la hace ffprobe).
ALLOWED_AUDIO_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
    "audio/x-flac",
}

#: Tamaño máximo de un MEDIA (audio/vídeo) subido. Mayor que el de imagen porque un clip
#: de vídeo pesa más: 200 MB. Documentado en ``docs/EDITOR-ADVANCED.md`` §6.1.
MAX_MEDIA_UPLOAD_BYTES = 200 * 1024 * 1024


def safe_stem(name: str) -> str:
    """Normaliza el nombre original a un stem seguro para el sistema de ficheros."""
    stem = Path(name or "").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem[:64] or "media"


# --------------------------------------------------------------------------- #
# Validación de media con ffprobe
# --------------------------------------------------------------------------- #


class MediaProbeError(ValueError):
    """ffprobe no está disponible o el fichero no es un medio válido con streams."""


@dataclass(frozen=True)
class MediaProbe:
    """Resultado de sondear un medio con ffprobe."""

    duration_s: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None


def probe_media(path: str | Path) -> MediaProbe:
    """Sondea ``path`` con ffprobe y devuelve ``MediaProbe`` (streams + duración).

    Confirma que el fichero tiene al menos un stream de audio o vídeo y lee la duración de
    formato (``format.duration``). Si ffprobe no está disponible lanza ``MediaProbeError``
    con un mensaje accionable (nunca finge la validación). Si el fichero no tiene streams
    válidos o ffprobe falla, también lanza ``MediaProbeError``.
    """
    ffprobe = get_settings().ffprobe_bin
    args = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise MediaProbeError(
            f"No se encontró ffprobe en '{ffprobe}'; no se puede validar el audio/vídeo. "
            "Configura AIV_FFPROBE_BIN (en el sandbox: /projects/sandbox/.localbin/ffprobe)."
        ) from exc

    if proc.returncode != 0:
        raise MediaProbeError(
            "El fichero no es un audio/vídeo válido (ffprobe no pudo analizarlo): "
            f"{(proc.stderr or '').strip()[-300:]}"
        )

    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise MediaProbeError(
            "ffprobe devolvió una salida no interpretable al analizar el fichero."
        ) from exc

    streams = data.get("streams") or []
    if not isinstance(streams, list) or not streams:
        raise MediaProbeError(
            "El fichero no contiene ningún stream de audio/vídeo reconocible."
        )

    has_video = False
    has_audio = False
    width: int | None = None
    height: int | None = None
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            has_video = True
            if width is None and isinstance(stream.get("width"), int):
                width = stream["width"]
            if height is None and isinstance(stream.get("height"), int):
                height = stream["height"]
        elif codec_type == "audio":
            has_audio = True

    if not has_video and not has_audio:
        raise MediaProbeError(
            "El fichero no contiene streams de audio ni de vídeo válidos."
        )

    duration_s = 0.0
    fmt = data.get("format")
    if isinstance(fmt, dict):
        try:
            duration_s = float(fmt.get("duration"))
        except (TypeError, ValueError):
            duration_s = 0.0
    if duration_s <= 0.0:
        # Fallback: mayor duración de stream conocida.
        for stream in streams:
            if isinstance(stream, dict):
                try:
                    duration_s = max(duration_s, float(stream.get("duration")))
                except (TypeError, ValueError):
                    continue

    return MediaProbe(
        duration_s=round(duration_s, 3),
        has_video=has_video,
        has_audio=has_audio,
        width=width,
        height=height,
    )

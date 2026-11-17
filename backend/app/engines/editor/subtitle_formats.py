r"""Parsers y serializadores PUROS de subtítulos (SRT, WebVTT, ASS/SSA).

100% sin ffmpeg ni IO: trabaja sobre ``list[Cue]`` (``backend/app/engines/voice/
subtitles.py``, que YA escribe SRT) y lo reutiliza. Se añaden:

- ``parse_srt(text)``            -> ``list[Cue]``
- ``dump_vtt(cues)`` / ``parse_vtt(text)`` (WebVTT: cabecera ``WEBVTT``, ms con ``.``;
  el parser tolera ``NOTE`` y cues sin índice).
- ``dump_ass(cues, style=None)`` / ``parse_ass(text)`` (``[Script Info]`` + ``[V4+
  Styles]`` + ``[Events] Dialogue``). El parser conserva el texto plano de cada
  ``Dialogue`` e ignora overrides inline ``{\...}`` (limitación documentada en
  ``docs/SUBTITLES-TITLES-PRO.md`` §12).
- Helpers de color hex ``#RRGGBB`` <-> ASS ``&HBBGGRR`` con tests.
- Adaptadores ``cues_to_subtitle_clips`` / ``subtitle_clips_to_cues`` entre ``Cue``
  (tiempos absolutos) y los clips de la pista ``subtitle`` del ``EditProject``.

Todas las rutas preservan tiempos al milisegundo y el texto, de modo que
``parse(dump(cues))`` es un round-trip fiel (ver ``tests/editor/test_subtitle_formats.py``).
"""

from __future__ import annotations

import re

from app.engines.editor.model import EditClip
from app.engines.voice.subtitles import Cue, _fmt

__all__ = [
    "parse_srt",
    "dump_vtt",
    "parse_vtt",
    "dump_ass",
    "parse_ass",
    "hex_to_ass_color",
    "ass_color_to_hex",
    "cues_to_subtitle_clips",
    "subtitle_clips_to_cues",
]


# --------------------------------------------------------------------------- #
# Tiempos
# --------------------------------------------------------------------------- #

# "HH:MM:SS,mmm" (SRT) o "HH:MM:SS.mmm" (VTT): el separador de ms puede ser , o .
_TS_RE = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})")
# ASS: "H:MM:SS.cc" (centésimas de segundo).
_ASS_TS_RE = re.compile(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<cs>\d{2})")


def _parse_timestamp(value: str) -> float | None:
    """Convierte ``HH:MM:SS,mmm`` / ``HH:MM:SS.mmm`` a segundos (o ``None``)."""
    match = _TS_RE.fullmatch(value.strip())
    if match is None:
        return None
    ms = match.group("ms").ljust(3, "0")  # ".5" -> 500 ms
    seconds = (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(ms) / 1000.0
    )
    return round(seconds, 3)


def _fmt_vtt(seconds: float) -> str:
    """``HH:MM:SS.mmm`` (WebVTT): idéntico a SRT pero con ``.`` en los milisegundos."""
    return _fmt(seconds).replace(",", ".")


def _fmt_ass(seconds: float) -> str:
    """``H:MM:SS.cc`` (ASS): horas sin cero a la izquierda, centésimas de segundo."""
    cs = int(round(max(0.0, seconds) * 100))  # redondear a centésimas antes de descomponer
    return (
        f"{cs // 360000:d}:{cs // 6000 % 60:02d}:"
        f"{cs // 100 % 60:02d}.{cs % 100:02d}"
    )


def _parse_ass_timestamp(value: str) -> float | None:
    match = _ASS_TS_RE.fullmatch(value.strip())
    if match is None:
        return None
    seconds = (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("cs")) / 100.0
    )
    return round(seconds, 3)


# --------------------------------------------------------------------------- #
# Color hex <-> ASS (&HBBGGRR)
# --------------------------------------------------------------------------- #

_HEX_RE = re.compile(r"^#?(?P<r>[0-9a-fA-F]{2})(?P<g>[0-9a-fA-F]{2})(?P<b>[0-9a-fA-F]{2})$")
_ASS_COLOR_RE = re.compile(
    r"^&H(?P<a>[0-9a-fA-F]{2})?(?P<b>[0-9a-fA-F]{2})"
    r"(?P<g>[0-9a-fA-F]{2})(?P<r>[0-9a-fA-F]{2})&?$"
)


def hex_to_ass_color(value: str) -> str:
    """``#RRGGBB`` -> ``&H00BBGGRR`` (ASS invierte el orden a BGR, alpha 00 = opaco)."""
    match = _HEX_RE.match(value.strip())
    if match is None:
        raise ValueError(f"Color hex inválido: {value!r} (se espera #RRGGBB).")
    r = match.group("r").upper()
    g = match.group("g").upper()
    b = match.group("b").upper()
    return f"&H00{b}{g}{r}"


def ass_color_to_hex(value: str) -> str:
    """``&H[AA]BBGGRR`` -> ``#RRGGBB`` (ignora el alpha; ASS es BGR)."""
    match = _ASS_COLOR_RE.match(value.strip())
    if match is None:
        raise ValueError(f"Color ASS inválido: {value!r} (se espera &HBBGGRR).")
    r = match.group("r").upper()
    g = match.group("g").upper()
    b = match.group("b").upper()
    return f"#{r}{g}{b}"


# --------------------------------------------------------------------------- #
# SRT
# --------------------------------------------------------------------------- #


def parse_srt(text: str) -> list[Cue]:
    """Parsea SRT (``índice`` + ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` + texto) a cues."""
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip())
    for block in blocks:
        lines = block.split("\n")
        # Salta un posible índice numérico en la primera línea.
        idx = 0
        if idx < len(lines) and lines[idx].strip().isdigit():
            idx += 1
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        start_raw, _, end_raw = lines[idx].partition("-->")
        start = _parse_timestamp(start_raw)
        end = _parse_timestamp(end_raw)
        if start is None or end is None:
            continue
        body = "\n".join(lines[idx + 1 :]).strip("\n")
        cues.append(Cue(t0=start, t1=end, text=body.strip()))
    return cues


# --------------------------------------------------------------------------- #
# WebVTT
# --------------------------------------------------------------------------- #


def dump_vtt(cues: list[Cue]) -> str:
    """Serializa cues a WebVTT (cabecera ``WEBVTT`` + bloques con ms separados por ``.``)."""
    parts = ["WEBVTT", ""]
    for cue in cues:
        parts.append(f"{_fmt_vtt(cue.t0)} --> {_fmt_vtt(cue.t1)}")
        parts.append(cue.text)
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def parse_vtt(text: str) -> list[Cue]:
    """Parsea WebVTT a cues. Tolera cabecera ``WEBVTT``, notas ``NOTE`` y cues sin índice."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", normalized.strip())
    cues: list[Cue] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        # Cabecera WEBVTT (con posibles metadatos en la misma primera línea) y NOTE.
        first = stripped.split("\n", 1)[0].strip()
        if first.upper().startswith("WEBVTT") or first.upper().startswith("NOTE"):
            continue
        lines = block.split("\n")
        # Un identificador de cue (línea sin "-->") puede preceder al timing.
        idx = 0
        if idx < len(lines) and "-->" not in lines[idx]:
            idx += 1
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        timing = lines[idx].split("-->")
        if len(timing) < 2:
            continue
        start = _parse_timestamp(timing[0])
        # La parte de fin puede llevar ajustes de posición ("... align:start"): toma el 1er token.
        end = _parse_timestamp(timing[1].strip().split()[0]) if timing[1].strip() else None
        if start is None or end is None:
            continue
        body = "\n".join(lines[idx + 1 :]).strip("\n")
        cues.append(Cue(t0=start, t1=end, text=body.strip()))
    return cues


# --------------------------------------------------------------------------- #
# ASS / SSA
# --------------------------------------------------------------------------- #

_ASS_FORMAT_FIELDS = [
    "Layer",
    "Start",
    "End",
    "Style",
    "Name",
    "MarginL",
    "MarginR",
    "MarginV",
    "Effect",
    "Text",
]

# Overrides inline de ASS: "{\pos(...)}", "{\c&H..&}", etc.
_ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")


def dump_ass(cues: list[Cue], style=None) -> str:
    """Serializa cues a ASS (``[Script Info]`` + ``[V4+ Styles]`` + ``[Events]``).

    ``style`` es un ``SubtitleStyle`` opcional del que se deriva la fila de estilo
    (fuente/tamaño/color primario). Si no se pasa, se emite un estilo ``Default`` neutro.
    El texto de cada cue escapa los saltos de línea a ``\\N`` (convención ASS).
    """
    font_name = "Arial"
    font_size = 48
    primary = "&H00FFFFFF"
    if style is not None:
        font_name = str(getattr(style, "font_family", font_name)).split(",")[0].strip() or "Arial"
        font_size = int(getattr(style, "font_size", font_size))
        try:
            primary = hex_to_ass_color(str(getattr(style, "color", "#FFFFFF")))
        except ValueError:
            primary = "&H00FFFFFF"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding"
        ),
        (
            f"Style: Default,{font_name},{font_size},{primary},&H000000FF,&H00000000,"
            "&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1"
        ),
        "",
        "[Events]",
        "Format: " + ", ".join(_ASS_FORMAT_FIELDS),
    ]
    for cue in cues:
        text = cue.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_fmt_ass(cue.t0)},{_fmt_ass(cue.t1)},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def _strip_ass_overrides(text: str) -> str:
    """Quita overrides inline ``{\\...}`` y convierte ``\\N``/``\\n`` en saltos de línea."""
    without = _ASS_OVERRIDE_RE.sub("", text)
    return without.replace("\\N", "\n").replace("\\n", "\n").strip()


def parse_ass(text: str) -> list[Cue]:
    """Parsea las líneas ``Dialogue`` de la sección ``[Events]`` a cues.

    Usa la cabecera ``Format:`` de ``[Events]`` para localizar las columnas Start/End/Text
    (tolera variaciones de orden). Conserva el texto plano, ignorando overrides inline.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    fields = list(_ASS_FORMAT_FIELDS)
    in_events = False
    cues: list[Cue] = []
    for raw in normalized.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "Format":
            fields = [f.strip() for f in value.split(",")]
            continue
        if key != "Dialogue":
            continue
        # El último campo (Text) puede contener comas: limitar el split.
        max_split = len(fields) - 1
        parts = value.split(",", max_split)
        if len(parts) < len(fields):
            continue
        row = dict(zip(fields, parts))
        start = _parse_ass_timestamp(row.get("Start", ""))
        end = _parse_ass_timestamp(row.get("End", ""))
        if start is None or end is None:
            continue
        body = _strip_ass_overrides(row.get("Text", ""))
        cues.append(Cue(t0=start, t1=end, text=body))
    return cues


# --------------------------------------------------------------------------- #
# Adaptadores cues <-> clips de la pista subtitle
# --------------------------------------------------------------------------- #


def cues_to_subtitle_clips(
    cues: list[Cue], track_id: str, *, id_prefix: str = "clip_sub_import"
) -> list[EditClip]:
    """Convierte cues (tiempos ABSOLUTOS) en clips de la pista ``subtitle`` dada.

    Los clips llevan ``start_s = cue.t0`` y ``duration_s = cue.t1 - cue.t0`` (siempre
    ``> 0``; los cues de duración no positiva se descartan). El estilo queda ``None``
    (defaults del render) para no imponer un preset al importar.
    """
    clips: list[EditClip] = []
    for i, cue in enumerate(cues):
        duration = round(float(cue.t1) - float(cue.t0), 3)
        if duration <= 0:
            continue
        clips.append(
            EditClip(
                id=f"{id_prefix}_{i}",
                track_id=track_id,
                kind="subtitle",
                start_s=round(float(cue.t0), 3),
                duration_s=duration,
                text=cue.text,
            )
        )
    return clips


def subtitle_clips_to_cues(edit_project) -> list[Cue]:
    """Extrae los cues (tiempos ABSOLUTOS) de la pista ``subtitle`` del ``EditProject``.

    Recorre todas las pistas de tipo ``subtitle``, toma sus clips habilitados y los
    ordena por ``start_s``. Es la inversa de ``cues_to_subtitle_clips`` para el export.
    """
    cues: list[Cue] = []
    for track in edit_project.tracks:
        if track.kind != "subtitle":
            continue
        for clip in track.clips:
            if not clip.enabled:
                continue
            cues.append(
                Cue(
                    t0=round(clip.start_s, 3),
                    t1=round(clip.start_s + clip.duration_s, 3),
                    text=clip.text or "",
                )
            )
    cues.sort(key=lambda c: c.t0)
    return cues

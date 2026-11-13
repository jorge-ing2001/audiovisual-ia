"""Subtítulos: de ``WordTiming`` a cues y exportación a ``.srt`` / ``palabras.json``.

Reutiliza el formato de ``core/render/srt.py`` de lemo-opuscar (código MIT): mismos
timestamps ``HH:MM:SS,mmm`` redondeados a milisegundos antes de descomponer (para no
perder acarreos, p. ej. 1.9996 -> 00:00:02,000) y el mismo recorte de solapes
adyacentes (el final de un cue se corta al inicio del siguiente).

Los cues se agrupan por palabras (``max_words_per_cue``) a partir de los tiempos del
ASR; también se expone un modo **palabra por palabra** (un cue por palabra) y la
exportación de un ``palabras.json`` (``[{text,startMs,endMs}]``) para el render de
subtítulos palabra-por-palabra (§3.G del prompt maestro).

El **diccionario de correcciones** (``{"mal": "bien"}``) es una reimplementación propia
*inspirada* en ``voz.py`` de skills-videos-cortos (MIT): reemplaza tokens mal transcritos
y, cuando la corrección tiene varias palabras, reparte proporcionalmente el intervalo del
token original entre las palabras de reemplazo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.providers.base import WordTiming


@dataclass
class Cue:
    """Un intervalo de subtítulo: ``[t0, t1]`` con su texto."""

    t0: float
    t1: float
    text: str


def _fmt(seconds: float) -> str:
    """Formatea segundos como ``HH:MM:SS,mmm`` (idéntico a core/render/srt.py)."""
    ms = int(round(max(0.0, seconds) * 1000))  # redondear a ms antes de descomponer
    return (
        f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:"
        f"{ms // 1000 % 60:02d},{ms % 1000:03d}"
    )


# --------------------------------------------------------------------------- #
# Diccionario de correcciones (patrón de voz.py, reimplementación propia)
# --------------------------------------------------------------------------- #


def _norm_token(text: str) -> str:
    """Normaliza un token para comparar con el diccionario (minúsculas, sin puntuación)."""
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE).lower()


def apply_corrections(
    timings: list[WordTiming], corrections: dict[str, str]
) -> list[WordTiming]:
    """Reemplaza tokens mal transcritos repartiendo proporcionalmente los tiempos.

    ``corrections`` mapea ``{"mal": "bien"}``. Si el valor de corrección tiene varias
    palabras, el intervalo ``[start, end]`` del token original se reparte de forma
    uniforme entre las palabras de reemplazo (preservando la continuidad temporal). La
    comparación ignora mayúsculas y puntuación.
    """
    if not corrections:
        return list(timings)
    lut = {_norm_token(k): v for k, v in corrections.items()}
    result: list[WordTiming] = []
    for w in timings:
        replacement = lut.get(_norm_token(w.word))
        if replacement is None:
            result.append(w)
            continue
        parts = replacement.split()
        if len(parts) <= 1:
            result.append(
                WordTiming(word=replacement, start_s=w.start_s, end_s=w.end_s)
            )
            continue
        span = max(0.0, w.end_s - w.start_s)
        step = span / len(parts)
        for i, part in enumerate(parts):
            start = round(w.start_s + i * step, 3)
            end = round(w.start_s + (i + 1) * step, 3) if i < len(parts) - 1 else w.end_s
            result.append(WordTiming(word=part, start_s=start, end_s=round(end, 3)))
    return result


# --------------------------------------------------------------------------- #
# Cues
# --------------------------------------------------------------------------- #


def cues_from_word_timings(
    timings: list[WordTiming], *, max_words_per_cue: int = 7
) -> list[Cue]:
    """Agrupa ``WordTiming`` consecutivos en cues de hasta ``max_words_per_cue`` palabras."""
    cues: list[Cue] = []
    group: list[WordTiming] = []
    for timing in timings:
        if not timing.word:
            continue
        group.append(timing)
        if len(group) >= max_words_per_cue:
            cues.append(_cue_from_group(group))
            group = []
    if group:
        cues.append(_cue_from_group(group))
    return cues


def cues_per_word(timings: list[WordTiming]) -> list[Cue]:
    """Genera un cue por cada palabra (modo palabra-por-palabra)."""
    return [
        Cue(t0=w.start_s, t1=w.end_s, text=w.word.strip())
        for w in timings
        if w.word and w.word.strip()
    ]


def _cue_from_group(group: list[WordTiming]) -> Cue:
    text = " ".join(w.word for w in group).strip()
    return Cue(t0=group[0].start_s, t1=group[-1].end_s, text=text)


def cues_from_text(
    text: str,
    *,
    start_s: float,
    duration_s: float,
    max_words_per_cue: int = 7,
) -> list[Cue]:
    """Reparte ``text`` en cues uniformes dentro de ``[start_s, start_s+duration_s)``.

    Fallback DETERMINISTA cuando no hay ASR disponible: NO estima tiempos por palabra
    (eso lo haría el ASR real), sino que divide el texto de la narración en grupos de
    hasta ``max_words_per_cue`` palabras y reparte la duración de la escena de forma
    proporcional al nº de palabras de cada grupo. Es un derivado del guion (texto), no
    una transcripción simulada: los tiempos son aproximados y así se etiqueta en el
    Asset (``provider='text-derived'``). Devuelve [] si no hay texto o duración.
    """
    words = [w for w in (text or "").split() if w.strip()]
    if not words or duration_s <= 0:
        return []

    groups: list[list[str]] = [
        words[i : i + max_words_per_cue]
        for i in range(0, len(words), max_words_per_cue)
    ]
    total_words = sum(len(g) for g in groups)
    cues: list[Cue] = []
    cursor = start_s
    end_limit = start_s + duration_s
    for group in groups:
        span = duration_s * (len(group) / total_words)
        t0 = round(cursor, 3)
        t1 = round(min(cursor + span, end_limit), 3)
        if t1 <= t0:
            t1 = round(min(t0 + 0.001, end_limit), 3)
        cues.append(Cue(t0=t0, t1=t1, text=" ".join(group)))
        cursor += span
    # Ajusta el último cue para que cierre exactamente al final de la escena.
    if cues:
        cues[-1].t1 = round(end_limit, 3)
    return cues


# --------------------------------------------------------------------------- #
# Exportación
# --------------------------------------------------------------------------- #


def write_srt(cues: list[Cue], out_path: str) -> str:
    """Escribe los cues como ``.srt`` (formato de core/render/srt.py) y devuelve la ruta.

    Ordena por ``t0`` y recorta el final de cada cue al inicio del siguiente, igual
    que el script MIT de lemo-opuscar, para que no haya solapes.
    """
    ordered = sorted(cues, key=lambda c: c.t0)
    for k in range(len(ordered) - 1):
        ordered[k].t1 = min(ordered[k].t1, ordered[k + 1].t0)
    body = "\n".join(
        f"{k + 1}\n{_fmt(c.t0)} --> {_fmt(c.t1)}\n{c.text}\n"
        for k, c in enumerate(ordered)
    )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return str(path)


def words_payload(timings: list[WordTiming]) -> list[dict]:
    """Devuelve la lista ``[{text,startMs,endMs}]`` para el render palabra-por-palabra."""
    payload: list[dict] = []
    for w in timings:
        if not w.word or not w.word.strip():
            continue
        payload.append(
            {
                "text": w.word.strip(),
                "startMs": int(round(max(0.0, w.start_s) * 1000)),
                "endMs": int(round(max(0.0, w.end_s) * 1000)),
            }
        )
    return payload


def write_words_json(timings: list[WordTiming], out_path: str) -> str:
    """Escribe ``palabras.json`` (``[{text,startMs,endMs}]``) y devuelve la ruta."""
    payload = words_payload(timings)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)

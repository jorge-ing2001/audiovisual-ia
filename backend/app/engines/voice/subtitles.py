"""Subtítulos: de ``WordTiming`` a cues y exportación a ``.srt``.

Reutiliza el formato de ``core/render/srt.py`` de lemo-opuscar (código MIT): mismos
timestamps ``HH:MM:SS,mmm`` redondeados a milisegundos antes de descomponer (para no
perder acarreos, p. ej. 1.9996 -> 00:00:02,000) y el mismo recorte de solapes
adyacentes (el final de un cue se corta al inicio del siguiente).

Los cues se agrupan por palabras (``max_words_per_cue``) a partir de los tiempos del
ASR; también se expone un ``cue_from_timings`` por línea para cuando se conoce la
narración de cada escena.
"""

from __future__ import annotations

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


def _cue_from_group(group: list[WordTiming]) -> Cue:
    text = " ".join(w.word for w in group).strip()
    return Cue(t0=group[0].start_s, t1=group[-1].end_s, text=text)


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

"""Evaluación PURA de los 5 controles de calidad + regla de ``overall``.

Sin ninguna invocación de ffmpeg/ffprobe: cada función recibe las MEDICIONES ya tomadas
(o un ``skip_reason`` cuando no se pudieron tomar) más los METADATOS del proyecto, y
devuelve un dict ``{id,label,status,detail,measured,threshold[,skip_reason]}`` con uno de
los CUATRO estados ``pass``/``warn``/``fail``/``skipped`` según la tabla de
``docs/ADMIN-QUALITY.md`` §2.2. Esta separación permite cubrir TODA la clasificación y el
``overall`` con tests sin ffmpeg (tablas de mediciones dadas -> veredicto esperado).

Un control ``skipped`` NUNCA cuenta como aprobado: ver ``compute_overall`` (§2.3).

Umbrales: constantes documentadas, configurables por entorno ``AIV_QA_*`` donde tiene
sentido. ``load_thresholds()`` resuelve la configuración EFECTIVA (defaults + overrides)
que se incrusta en el reporte.
"""

from __future__ import annotations

import os
from typing import Any

# --------------------------------------------------------------------------- #
# Estados y umbrales (constantes documentadas, override por AIV_QA_*)
# --------------------------------------------------------------------------- #

#: Los cuatro estados posibles de un control (orden de severidad descendente).
STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_SKIPPED = "skipped"

#: Estados de ``overall`` (``pass_with_skips`` es DISTINTO de ``pass``).
OVERALL_FAIL = "fail"
OVERALL_WARN = "warn"
OVERALL_PASS_WITH_SKIPS = "pass_with_skips"
OVERALL_PASS = "pass"

# Loudness (LUFS). Objetivo -14; |Δ| <= warn => pass, <= fail => warn, > fail => fail.
LOUDNESS_TARGET_LUFS = -14.0
LOUDNESS_WARN_LU = 1.0
LOUDNESS_FAIL_LU = 2.0

# Frames negros. Detección a partir de BLACK_MIN_S; un tramo >= BLACK_FAIL_S es fail.
BLACK_MIN_S = 0.1
BLACK_FAIL_S = 1.0

# Coherencia de duración (vs duración esperada).
DURATION_WARN_S = 0.5
DURATION_FAIL_S = 1.0
DURATION_WARN_PCT = 0.02
DURATION_FAIL_PCT = 0.05

# Resolución / aspecto.
ASPECT_TOL = 0.01

# Subtítulos: margen admitido más allá de la duración del vídeo.
SUB_MARGIN_S = 0.25


def _env_float(name: str, default: float) -> float:
    """Lee un override ``AIV_QA_*`` float; si no es válido, usa el default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_thresholds() -> dict[str, dict[str, float]]:
    """Resuelve la configuración EFECTIVA de umbrales (defaults + overrides ``AIV_QA_*``).

    Es la estructura que se incrusta en ``qa_report['thresholds']`` y que cada check
    recibe como su ``threshold``. Mantener el shape estable (ver §2.5 del doc).
    """
    return {
        "loudness": {
            "target_lufs": _env_float("AIV_QA_LOUDNESS_TARGET_LUFS", LOUDNESS_TARGET_LUFS),
            "warn_lu": _env_float("AIV_QA_LOUDNESS_WARN_LU", LOUDNESS_WARN_LU),
            "fail_lu": _env_float("AIV_QA_LOUDNESS_FAIL_LU", LOUDNESS_FAIL_LU),
        },
        "black_frames": {
            "black_min_s": _env_float("AIV_QA_BLACK_MIN_S", BLACK_MIN_S),
            "black_fail_s": _env_float("AIV_QA_BLACK_FAIL_S", BLACK_FAIL_S),
        },
        "duration_match": {
            "warn_s": _env_float("AIV_QA_DURATION_WARN_S", DURATION_WARN_S),
            "fail_s": _env_float("AIV_QA_DURATION_FAIL_S", DURATION_FAIL_S),
            "warn_pct": _env_float("AIV_QA_DURATION_WARN_PCT", DURATION_WARN_PCT),
            "fail_pct": _env_float("AIV_QA_DURATION_FAIL_PCT", DURATION_FAIL_PCT),
        },
        "resolution_aspect": {
            "aspect_tol": _env_float("AIV_QA_ASPECT_TOL", ASPECT_TOL),
        },
        "subtitle_sync": {
            "margin_s": _env_float("AIV_QA_SUB_MARGIN_S", SUB_MARGIN_S),
        },
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _check(
    check_id: str,
    label: str,
    status: str,
    detail: str,
    measured: Any,
    threshold: dict,
    skip_reason: str | None = None,
) -> dict:
    """Construye el dict de un control con el shape estándar del reporte."""
    result: dict = {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "measured": measured,
        "threshold": threshold,
    }
    if skip_reason is not None:
        result["skip_reason"] = skip_reason
    return result


def _parse_aspect_ratio(aspect: str | None) -> float | None:
    """Convierte ``"16:9"``/``"1.777"`` a un float (w/h). ``None`` si no es interpretable."""
    if not aspect:
        return None
    text = str(aspect).strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                w = float(parts[0])
                h = float(parts[1])
            except ValueError:
                return None
            if h > 0:
                return w / h
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


# --------------------------------------------------------------------------- #
# Los 5 controles (evaluación pura)
# --------------------------------------------------------------------------- #


def check_loudness(
    integrated_lufs: float | None,
    thresholds: dict,
    *,
    skip_reason: str | None = None,
) -> dict:
    """Loudness integrada (LUFS) vs objetivo (-14). ``|Δ| <= warn`` pass, ``<= fail`` warn.

    ``skip_reason`` (p. ej. ``no_audio``/``ffmpeg_unavailable``) o ``integrated_lufs=None``
    -> ``skipped`` (nunca se promueve a pass por ausencia de datos).
    """
    th = thresholds["loudness"]
    label = "Loudness integrada (-14 LUFS)"
    if skip_reason is not None or integrated_lufs is None:
        reason = skip_reason or "no_measurement"
        return _check(
            "loudness",
            label,
            STATUS_SKIPPED,
            f"No se pudo medir la loudness ({reason}).",
            None,
            th,
            skip_reason=reason,
        )
    target = th["target_lufs"]
    delta = abs(integrated_lufs - target)
    measured = {"integrated_lufs": round(integrated_lufs, 2), "delta_lu": round(delta, 2)}
    if delta <= th["warn_lu"]:
        status = STATUS_PASS
    elif delta <= th["fail_lu"]:
        status = STATUS_WARN
    else:
        status = STATUS_FAIL
    detail = (
        f"I medido = {integrated_lufs:.1f} LUFS (objetivo {target:.0f} "
        f"± {th['warn_lu']:.0f}); Δ = {delta:.1f} LU"
    )
    return _check("loudness", label, status, detail, measured, th)


def check_black_frames(
    black_segments: list[tuple[float, float]] | None,
    thresholds: dict,
    *,
    skip_reason: str | None = None,
) -> dict:
    """Frames negros (``blackdetect``). 0 tramos pass; tramo ``< fail_s`` warn; ``>=`` fail.

    ``black_segments`` es la lista de pares ``(black_start, black_end)`` detectados por
    ffmpeg. ``skip_reason`` o ``None`` -> ``skipped``.
    """
    th = thresholds["black_frames"]
    label = "Frames negros"
    if skip_reason is not None or black_segments is None:
        reason = skip_reason or "no_measurement"
        return _check(
            "black_frames",
            label,
            STATUS_SKIPPED,
            f"No se pudo ejecutar blackdetect ({reason}).",
            None,
            th,
            skip_reason=reason,
        )
    if not black_segments:
        return _check(
            "black_frames",
            label,
            STATUS_PASS,
            "Sin tramos negros por encima del umbral de detección.",
            {"segments": [], "max_black_s": 0.0},
            th,
        )
    durations = [max(0.0, end - start) for start, end in black_segments]
    longest = max(durations)
    longest_idx = durations.index(longest)
    start_of_longest = black_segments[longest_idx][0]
    measured = {
        "segments": [
            {"start_s": round(s, 3), "end_s": round(e, 3), "duration_s": round(e - s, 3)}
            for s, e in black_segments
        ],
        "max_black_s": round(longest, 3),
    }
    if longest >= th["black_fail_s"]:
        status = STATUS_FAIL
        detail = (
            f"Tramo negro de {longest:.2f}s en t={start_of_longest:.2f}s "
            f"(>= {th['black_fail_s']:.1f}s)."
        )
    else:
        status = STATUS_WARN
        detail = (
            f"Tramo negro de {longest:.2f}s en t={start_of_longest:.2f}s "
            f"(< {th['black_fail_s']:.1f}s)."
        )
    return _check("black_frames", label, status, detail, measured, th)


def check_duration_match(
    actual_duration_s: float | None,
    expected_duration_s: float | None,
    thresholds: dict,
    *,
    skip_reason: str | None = None,
) -> dict:
    """Duración real del MP4 vs esperada (suma de escenas o EditProject).

    pass si ``|Δ| <= warn_s`` Y ``<= warn_pct``; warn si ``<= fail_s`` O ``<= fail_pct``;
    fail si ``> fail_s`` Y ``> fail_pct``. Sin duración esperada o sin MP4 -> ``skipped``.
    """
    th = thresholds["duration_match"]
    label = "Coherencia de duración"
    if skip_reason is not None or actual_duration_s is None or expected_duration_s is None:
        reason = skip_reason or (
            "no_expected_duration" if expected_duration_s is None else "no_video"
        )
        return _check(
            "duration_match",
            label,
            STATUS_SKIPPED,
            f"No se pudo comparar la duración ({reason}).",
            None,
            th,
            skip_reason=reason,
        )
    delta = abs(actual_duration_s - expected_duration_s)
    pct = delta / expected_duration_s if expected_duration_s > 0 else float("inf")
    measured = {
        "actual_s": round(actual_duration_s, 3),
        "expected_s": round(expected_duration_s, 3),
        "delta_s": round(delta, 3),
        "delta_pct": round(pct, 4),
    }
    within_warn = delta <= th["warn_s"] and pct <= th["warn_pct"]
    within_fail_band = delta <= th["fail_s"] or pct <= th["fail_pct"]
    if within_warn:
        status = STATUS_PASS
    elif within_fail_band:
        status = STATUS_WARN
    else:
        status = STATUS_FAIL
    detail = (
        f"Duración real {actual_duration_s:.2f}s vs esperada {expected_duration_s:.2f}s "
        f"(Δ = {delta:.2f}s / {pct * 100:.1f}%)."
    )
    return _check("duration_match", label, status, detail, measured, th)


def check_resolution_aspect(
    width: int | None,
    height: int | None,
    thresholds: dict,
    *,
    expected_aspect: str | None = None,
    expected_resolution: tuple[int, int] | None = None,
    skip_reason: str | None = None,
) -> dict:
    """Resolución/aspecto del MP4 vs los declarados en el proyecto. Sin ``warn``.

    pass si el aspecto coincide dentro de ``±aspect_tol`` (y la resolución exacta si se
    conoce); fail si no coincide. Sin MP4 (width/height ausentes) -> ``skipped``.
    """
    th = thresholds["resolution_aspect"]
    label = "Resolución y aspecto"
    if skip_reason is not None or width is None or height is None or height == 0:
        reason = skip_reason or "no_video"
        return _check(
            "resolution_aspect",
            label,
            STATUS_SKIPPED,
            f"No se pudieron leer dimensiones del vídeo ({reason}).",
            None,
            th,
            skip_reason=reason,
        )
    actual_aspect = width / height
    measured: dict = {
        "width": width,
        "height": height,
        "aspect": round(actual_aspect, 4),
    }
    problems: list[str] = []

    expected_aspect_value = _parse_aspect_ratio(expected_aspect)
    if expected_aspect_value is not None:
        measured["expected_aspect"] = round(expected_aspect_value, 4)
        if abs(actual_aspect - expected_aspect_value) > th["aspect_tol"]:
            problems.append(
                f"aspecto {actual_aspect:.3f} != esperado {expected_aspect_value:.3f} "
                f"(tol ±{th['aspect_tol']})"
            )

    if expected_resolution is not None:
        exp_w, exp_h = expected_resolution
        measured["expected_width"] = exp_w
        measured["expected_height"] = exp_h
        if (width, height) != (exp_w, exp_h):
            problems.append(f"resolución {width}x{height} != esperada {exp_w}x{exp_h}")

    if problems:
        return _check(
            "resolution_aspect",
            label,
            STATUS_FAIL,
            "; ".join(problems),
            measured,
            th,
        )
    return _check(
        "resolution_aspect",
        label,
        STATUS_PASS,
        f"{width}x{height} (aspecto {actual_aspect:.3f}) coincide con lo declarado.",
        measured,
        th,
    )


def check_subtitle_sync(
    cues: list[tuple[float, float]] | None,
    video_duration_s: float | None,
    thresholds: dict,
    *,
    skip_reason: str | None = None,
) -> dict:
    """Cues de subtítulo dentro de la duración y con ``start < end``.

    ``cues`` es una lista de pares ``(start_s, end_s)``. pass si todos cumplen
    ``0 <= start < end <= dur + margin``; warn si algún exceso ``<= 0.5s`` o micro-solape
    ``<= 0.1s``; fail si exceso ``> 0.5s``, ``end <= start`` o solapes relevantes. Sin
    subtítulos o sin MP4 -> ``skipped``.
    """
    th = thresholds["subtitle_sync"]
    margin = th["margin_s"]
    label = "Sincronía de subtítulos"
    if skip_reason is not None:
        return _check(
            "subtitle_sync",
            label,
            STATUS_SKIPPED,
            f"No se pudo validar la sincronía de subtítulos ({skip_reason}).",
            None,
            th,
            skip_reason=skip_reason,
        )
    if cues is None or len(cues) == 0:
        return _check(
            "subtitle_sync",
            label,
            STATUS_SKIPPED,
            "El proyecto no tiene subtítulos que validar.",
            None,
            th,
            skip_reason="no_subtitles",
        )
    if video_duration_s is None:
        return _check(
            "subtitle_sync",
            label,
            STATUS_SKIPPED,
            "No se conoce la duración del vídeo para validar los subtítulos.",
            None,
            th,
            skip_reason="no_video",
        )

    # Umbrales de severidad de la banda warn (constantes del doc §2.2).
    overflow_warn_s = 0.5
    micro_overlap_s = 0.1

    worst = STATUS_PASS
    problems: list[str] = []

    def _escalate(level: str) -> None:
        nonlocal worst
        order = {STATUS_PASS: 0, STATUS_WARN: 1, STATUS_FAIL: 2}
        if order[level] > order[worst]:
            worst = level

    ordered = sorted(cues, key=lambda c: c[0])
    limit = video_duration_s + margin
    for i, (start, end) in enumerate(ordered):
        if end <= start:
            _escalate(STATUS_FAIL)
            problems.append(f"cue {i}: end {end:.2f} <= start {start:.2f}")
            continue
        if start < 0:
            _escalate(STATUS_FAIL)
            problems.append(f"cue {i}: start negativo ({start:.2f})")
        overflow = end - limit
        if overflow > 0:
            if overflow <= overflow_warn_s:
                _escalate(STATUS_WARN)
                problems.append(f"cue {i}: excede {overflow:.2f}s el margen")
            else:
                _escalate(STATUS_FAIL)
                problems.append(f"cue {i}: excede {overflow:.2f}s (> {overflow_warn_s}s)")
        # Solapes con el cue siguiente (en orden por start).
        if i + 1 < len(ordered):
            next_start = ordered[i + 1][0]
            overlap = end - next_start
            if overlap > 0:
                if overlap <= micro_overlap_s:
                    _escalate(STATUS_WARN)
                    problems.append(f"cue {i}/{i + 1}: micro-solape {overlap:.2f}s")
                else:
                    _escalate(STATUS_FAIL)
                    problems.append(f"cue {i}/{i + 1}: solape {overlap:.2f}s")

    measured = {
        "cue_count": len(cues),
        "video_duration_s": round(video_duration_s, 3),
        "last_end_s": round(max(e for _, e in ordered), 3),
    }
    if worst == STATUS_PASS:
        detail = f"{len(cues)} cues dentro de la duración (margen {margin}s)."
    else:
        detail = "; ".join(problems[:5])
    return _check("subtitle_sync", label, worst, detail, measured, th)


# --------------------------------------------------------------------------- #
# Regla de overall (skipped NUNCA es pass)
# --------------------------------------------------------------------------- #


def compute_overall(checks: list[dict]) -> str:
    """Calcula el ``overall`` conservador con precedencia ``fail > warn > skipped > pass``.

    1. Si algún check es ``fail`` -> ``fail``.
    2. Si no hay fail pero hay ``warn`` -> ``warn``.
    3. Si no hay fail/warn pero hay ``skipped`` -> ``pass_with_skips`` (NUNCA ``pass``:
       lo evaluado pasó, pero el reporte está incompleto).
    4. Si TODOS son ``pass`` (ninguno skipped) -> ``pass``.

    Un ``skipped`` degrada un ``pass`` global a ``pass_with_skips``: un control omitido no
    cuenta como aprobado. Sin checks -> ``pass_with_skips`` (no hay nada verificado).
    """
    statuses = {c.get("status") for c in checks}
    if STATUS_FAIL in statuses:
        return OVERALL_FAIL
    if STATUS_WARN in statuses:
        return OVERALL_WARN
    if STATUS_SKIPPED in statuses or not checks:
        return OVERALL_PASS_WITH_SKIPS
    return OVERALL_PASS

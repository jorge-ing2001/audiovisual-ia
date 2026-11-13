"""Cadena de limpieza + masterización de voz a ~-14 LUFS (FFmpeg).

Reimplementación **propia** (código de audiovisual-ia) *inspirada* en el flujo de
``skills/voz-whisper/scripts/voz.py`` de JustCarlos30/skills-videos-cortos (MIT):

  1. Limpieza opcional de la voz: ``highpass`` (quita graves/rumble ~90 Hz),
     ``afftdn`` (de-noise por FFT), un de-esser suave (ecualización negativa en la
     banda de sibilancias ~6 kHz), una ecualización ligera de presencia y un
     ``acompressor`` que iguala dinámica.
  2. Masterización a loudness objetivo con ``loudnorm`` de DOS pasadas (medir en la
     primera, aplicar en modo ``linear=true`` con ``measured_*`` + ``offset`` en la
     segunda) seguida de un ``alimiter`` sobremuestreado para respetar el true-peak.

Objetivo: I = -14 LUFS, TP <= -1 dBFS (coherente con ``core/render/mux.sh`` de
lemo-opuscar, que el ``AudioMixer`` ya usaba). No se copia código de voz.py: aquí se
construye el grafo de filtros de FFmpeg de forma propia y se expone como función
reutilizable por el mixer y por el backend de voz grabada.

El binario de ffmpeg se toma de ``AIV_FFMPEG_BIN`` (configurable; en el sandbox vive
en ``/projects/sandbox/.localbin/ffmpeg`` y no está en el PATH).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

# Objetivos de loudness (idénticos a core/render/mux.sh y al mixer previo).
TARGET_I = -14.0
TARGET_TP = -1.2
TARGET_LRA = 11.0

# Techo del limitador true-peak (dBFS) tras el sobremuestreo, como en mux.sh.
LIMITER_CEIL_DB = -1.6


class FfmpegError(RuntimeError):
    """ffmpeg/ffprobe falló o no está disponible."""


@dataclass
class CleanupOptions:
    """Parámetros de la cadena de limpieza (adaptada de voz.py, valores propios).

    Todos los pasos son activables de forma independiente; con ``enabled=False`` la
    cadena de limpieza se omite por completo y solo se aplica la masterización.
    """

    enabled: bool = True
    highpass_hz: int = 90          # corta rumble/graves por debajo de ~90 Hz
    denoise: bool = True           # afftdn (de-noise por FFT)
    denoise_nr: float = 12.0       # reducción de ruido (dB) de afftdn
    deesser: bool = True           # atenúa sibilancias (~6 kHz)
    deesser_gain_db: float = -4.0
    presence: bool = True          # realce suave de presencia (~3 kHz)
    presence_gain_db: float = 2.0
    compress: bool = True          # acompressor para igualar dinámica
    compress_ratio: float = 3.0
    compress_threshold_db: float = -20.0


@dataclass
class MasterResult:
    """Resultado de masterizar un audio: ruta de salida y medidas de loudness."""

    path: str
    integrated_lufs: float
    measured: dict | None = None
    meta: dict = field(default_factory=dict)


def _clean_filters(opts: CleanupOptions) -> list[str]:
    """Construye la lista de filtros de limpieza (vacía si ``enabled=False``)."""
    if not opts.enabled:
        return []
    filters: list[str] = []
    if opts.highpass_hz > 0:
        filters.append(f"highpass=f={opts.highpass_hz}")
    if opts.denoise:
        filters.append(f"afftdn=nr={opts.denoise_nr}:nf=-25")
    if opts.deesser:
        # De-esser simple: campana negativa en la banda de sibilancias.
        filters.append(f"equalizer=f=6500:t=q:w=2.0:g={opts.deesser_gain_db}")
    if opts.presence:
        filters.append(f"equalizer=f=3000:t=q:w=1.5:g={opts.presence_gain_db}")
    if opts.compress:
        filters.append(
            f"acompressor=threshold={opts.compress_threshold_db}dB:"
            f"ratio={opts.compress_ratio}:attack=20:release=250:makeup=2"
        )
    return filters


def _loudnorm_measure_filter() -> str:
    return (
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json"
    )


def _loudnorm_apply_filter(measured: dict | None) -> str:
    if measured:
        return (
            f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true"
        )
    return f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"


def _limiter_filter() -> str:
    """Limitador sobremuestreado (192 kHz) con techo true-peak, como mux.sh."""
    ceil = round(10 ** (LIMITER_CEIL_DB / 20), 4)
    return (
        f"aresample=192000,"
        f"alimiter=limit={ceil}:attack=10:release=200:level=disabled:latency=1,"
        f"aresample=48000"
    )


class VoiceMaster:
    """Aplica la cadena de limpieza + masterización a ~-14 LUFS usando ffmpeg.

    Pensada para dos usos: (a) el ``AudioMixer`` la invoca sobre el label del mix ya
    combinado (sin re-leer archivos); (b) el backend de voz grabada la invoca sobre
    un WAV subido por el usuario antes de pasarlo al ASR.
    """

    def __init__(self, *, ffmpeg_bin: str | None = None, ffprobe_bin: str | None = None) -> None:
        settings = get_settings()
        self.ffmpeg = ffmpeg_bin or settings.ffmpeg_bin
        self.ffprobe = ffprobe_bin or settings.ffprobe_bin

    # --- construcción de la cadena (reutilizable por el mixer) -------------- #

    def chain(
        self,
        in_label: str,
        out_label: str,
        *,
        measured: dict | None,
        cleanup: CleanupOptions | None = None,
    ) -> str:
        """Devuelve el fragmento de ``filter_complex`` ``[in]...[out]``.

        ``in_label``/``out_label`` son los nombres de pad (p. ej. ``mix`` / ``out``)
        para que el mixer pueda encadenarlo tras su ``amix`` sin tocar archivos.
        ``measured`` son las medidas de la primera pasada de loudnorm (o ``None``).
        """
        opts = cleanup if cleanup is not None else CleanupOptions()
        steps = _clean_filters(opts)
        steps.append(_loudnorm_apply_filter(measured))
        steps.append(_limiter_filter())
        body = ",".join(steps)
        return f"[{in_label}]{body}[{out_label}]"

    def measure_chain(
        self,
        in_label: str,
        out_label: str,
        *,
        cleanup: CleanupOptions | None = None,
    ) -> str:
        """Fragmento de la PRIMERA pasada: limpieza + loudnorm en modo medición."""
        opts = cleanup if cleanup is not None else CleanupOptions()
        steps = _clean_filters(opts)
        steps.append(_loudnorm_measure_filter())
        body = ",".join(steps)
        return f"[{in_label}]{body}[{out_label}]"

    # --- API de alto nivel sobre archivos ---------------------------------- #

    def master_file(
        self,
        in_path: str,
        out_path: str,
        *,
        cleanup: CleanupOptions | None = None,
    ) -> MasterResult:
        """Limpia + masteriza un WAV de entrada y escribe ``out_path`` (~-14 LUFS)."""
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        measured = self._measure(in_path, cleanup)
        self._apply(in_path, str(out), measured, cleanup)
        integrated = self.measure_integrated(str(out))
        return MasterResult(
            path=str(out),
            integrated_lufs=integrated,
            measured=measured,
            meta={"target_i": TARGET_I, "target_tp": TARGET_TP},
        )

    # --- helpers de ejecución ---------------------------------------------- #

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.ffmpeg, *args], capture_output=True, text=True, timeout=600
            )
        except FileNotFoundError as exc:
            raise FfmpegError(
                f"No se encontró ffmpeg en '{self.ffmpeg}'. Configura AIV_FFMPEG_BIN "
                "(en el sandbox: /projects/sandbox/.localbin/ffmpeg)."
            ) from exc

    def _measure(self, in_path: str, cleanup: CleanupOptions | None) -> dict | None:
        chain = self.measure_chain("0:a", "out", cleanup=cleanup)
        proc = self._run(
            ["-i", in_path, "-filter_complex", chain, "-map", "[out]", "-f", "null", "-"]
        )
        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg no pudo medir la loudness: {(proc.stderr or '')[-600:]}"
            )
        return parse_loudnorm_json(proc.stderr or "")

    def _apply(
        self,
        in_path: str,
        out_path: str,
        measured: dict | None,
        cleanup: CleanupOptions | None,
    ) -> None:
        chain = self.chain("0:a", "out", measured=measured, cleanup=cleanup)
        proc = self._run(
            [
                "-y",
                "-i",
                in_path,
                "-filter_complex",
                chain,
                "-map",
                "[out]",
                "-c:a",
                "pcm_s16le",
                out_path,
            ]
        )
        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg no pudo masterizar el audio: {(proc.stderr or '')[-600:]}"
            )

    def measure_integrated(self, path: str) -> float:
        """Mide la loudness integrada (LUFS) con ebur128."""
        proc = self._run(
            ["-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"]
        )
        value = parse_integrated_lufs(proc.stderr or "")
        if value is None:
            raise FfmpegError(f"No se pudo medir la loudness integrada de '{path}'.")
        return value


# --------------------------------------------------------------------------- #
# Parsers compartidos (usados también por el mixer)
# --------------------------------------------------------------------------- #


def parse_loudnorm_json(stderr: str) -> dict | None:
    """Extrae el bloque JSON que imprime ``loudnorm=print_format=json``."""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
    needed = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(k in data for k in needed):
        return None
    # loudnorm no puede medir audio silencioso (valores inf/nan): devolver None.
    for key in needed:
        if any(tok in str(data[key]) for tok in ("inf", "nan")):
            return None
    return data


def parse_integrated_lufs(stderr: str) -> float | None:
    """Lee el valor ``I: <n> LUFS`` del resumen de ebur128."""
    marker = "I:"
    best: float | None = None
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith(marker) and "LUFS" in stripped:
            token = stripped[len(marker) :].replace("LUFS", "").strip()
            try:
                best = float(token)
            except ValueError:
                continue
    return best

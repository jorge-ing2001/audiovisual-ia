"""``AudioMixer`` — mezcla voz + música + SFX y normaliza a ~-14 LUFS (FFmpeg).

Reutiliza el enfoque de audio de ``core/render/mux.sh`` de lemo-opuscar (código MIT):
dos pasadas de ``loudnorm`` (I=-14 / TP=-1.2 / LRA=11, modo lineal con las medidas de
la primera pasada) seguidas de un limitador sobremuestreado, para alcanzar -14 LUFS de
forma fiable (una sola pasada se desvía ~0.5 LU).

Las pistas se combinan con ``amix`` (la voz manda; música/SFX se atenúan). Si solo hay
una pista, se normaliza directamente. El binario de ffmpeg se toma de ``AIV_FFMPEG_BIN``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings


class FfmpegError(RuntimeError):
    """ffmpeg/ffprobe falló o no está disponible."""


@dataclass
class MixTrack:
    """Una pista de la mezcla: ruta al audio y ganancia relativa (1.0 = sin cambio)."""

    path: str
    gain: float = 1.0


@dataclass
class MixResult:
    """Resultado de la mezcla: ruta del WAV y loudness integrada medida (LUFS)."""

    path: str
    integrated_lufs: float
    meta: dict = field(default_factory=dict)


# Objetivos de loudness (idénticos a core/render/mux.sh).
TARGET_I = -14.0
TARGET_TP = -1.2
TARGET_LRA = 11.0


class AudioMixer:
    """Mezcla y normaliza audio a ~-14 LUFS usando ffmpeg (patrón de mux.sh)."""

    def __init__(self, *, ffmpeg_bin: str | None = None, ffprobe_bin: str | None = None) -> None:
        settings = get_settings()
        self.ffmpeg = ffmpeg_bin or settings.ffmpeg_bin
        self.ffprobe = ffprobe_bin or settings.ffprobe_bin

    # --- API pública -------------------------------------------------------- #

    def mix(self, tracks: list[MixTrack], out_path: str) -> MixResult:
        """Mezcla ``tracks`` y escribe un WAV normalizado a ~-14 LUFS en ``out_path``.

        La primera pista (habitualmente la voz) marca el canal de referencia; las
        demás (música, SFX) se suman con su ganancia. Devuelve la loudness medida.
        """
        if not tracks:
            raise ValueError("AudioMixer.mix requiere al menos una pista")

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        measured = self._measure_loudnorm(tracks)
        self._render(tracks, measured, str(out))
        integrated = self._measure_integrated(str(out))
        return MixResult(
            path=str(out),
            integrated_lufs=integrated,
            meta={"target_i": TARGET_I, "tracks": len(tracks), "measured": measured},
        )

    # --- helpers de filtros ------------------------------------------------- #

    def _mix_filter(self, tracks: list[MixTrack]) -> str:
        """Construye el grafo de entrada que combina las pistas antes de loudnorm."""
        if len(tracks) == 1:
            if tracks[0].gain != 1.0:
                return f"[0:a]volume={tracks[0].gain}[mix]"
            return "[0:a]anull[mix]"
        parts = []
        labels = []
        for i, track in enumerate(tracks):
            label = f"a{i}"
            parts.append(f"[{i}:a]volume={track.gain}[{label}]")
            labels.append(f"[{label}]")
        joined = "".join(labels)
        parts.append(
            f"{joined}amix=inputs={len(tracks)}:duration=longest:"
            f"normalize=0[mix]"
        )
        return ";".join(parts)

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

    def _measure_loudnorm(self, tracks: list[MixTrack]) -> dict | None:
        """Primera pasada: mide I/TP/LRA del mix para la segunda pasada lineal."""
        mix = self._mix_filter(tracks)
        loudnorm = (
            f"[mix]loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:"
            f"print_format=json[out]"
        )
        args = []
        for track in tracks:
            args += ["-i", track.path]
        args += [
            "-filter_complex",
            f"{mix};{loudnorm}",
            "-map",
            "[out]",
            "-f",
            "null",
            "-",
        ]
        proc = self._run(args)
        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg no pudo medir la loudness: {(proc.stderr or '')[-600:]}"
            )
        return _parse_loudnorm_json(proc.stderr or "")

    def _render(self, tracks: list[MixTrack], measured: dict | None, out_path: str) -> None:
        """Segunda pasada: aplica loudnorm lineal + limitador y escribe el WAV."""
        mix = self._mix_filter(tracks)
        if measured:
            loudnorm = (
                f"[mix]loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:"
                f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
                f"measured_LRA={measured['input_lra']}:"
                f"measured_thresh={measured['input_thresh']}:"
                f"offset={measured['target_offset']}:linear=true[ln]"
            )
        else:
            loudnorm = f"[mix]loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}[ln]"
        # Limitador sobremuestreado (como mux.sh: 192 kHz, techo -1.6 dBFS) y de vuelta a 48 kHz.
        ceil = round(10 ** (-1.6 / 20), 4)
        chain = (
            f"{loudnorm};[ln]aresample=192000,"
            f"alimiter=limit={ceil}:attack=10:release=200:level=disabled:latency=1,"
            f"aresample=48000[out]"
        )
        args = []
        for track in tracks:
            args += ["-i", track.path]
        args += [
            "-y",
            "-filter_complex",
            f"{mix};{chain}",
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            out_path,
        ]
        proc = self._run(args)
        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg no pudo escribir la mezcla: {(proc.stderr or '')[-600:]}"
            )

    def _measure_integrated(self, path: str) -> float:
        """Mide la loudness integrada (LUFS) del WAV final con ebur128."""
        args = ["-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"]
        proc = self._run(args)
        text = proc.stderr or ""
        value = _parse_integrated_lufs(text)
        if value is None:
            raise FfmpegError(
                f"No se pudo medir la loudness integrada de '{path}'."
            )
        return value


def _parse_loudnorm_json(stderr: str) -> dict | None:
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


def _parse_integrated_lufs(stderr: str) -> float | None:
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

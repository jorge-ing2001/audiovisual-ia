"""``AudioMixer`` — mezcla voz + música + SFX y normaliza a ~-14 LUFS (FFmpeg).

Reutiliza el enfoque de audio de ``core/render/mux.sh`` de lemo-opuscar (código MIT):
dos pasadas de ``loudnorm`` (I=-14 / TP=-1.2 / LRA=11, modo lineal con las medidas de
la primera pasada) seguidas de un limitador sobremuestreado, para alcanzar -14 LUFS de
forma fiable (una sola pasada se desvía ~0.5 LU).

La cadena de limpieza + masterización vive en ``engines/voice/master.py`` (reimplementación
propia inspirada en ``voz.py`` de skills-videos-cortos, MIT) y el mixer la invoca sobre el
label del mix ya combinado. Por defecto el mixer NO aplica la limpieza de voz (highpass,
de-noise, de-esser, etc.) a la mezcla completa para no alterar música/SFX; solo masteriza
a loudness. Se puede activar con ``cleanup=`` si se mezcla únicamente voz.

Las pistas se combinan con ``amix`` (la voz manda; música/SFX se atenúan). Si solo hay
una pista, se normaliza directamente. El binario de ffmpeg se toma de ``AIV_FFMPEG_BIN``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.engines.voice.master import (
    TARGET_I,
    CleanupOptions,
    FfmpegError,
    VoiceMaster,
    parse_integrated_lufs,
    parse_loudnorm_json,
)

__all__ = ["MixTrack", "MixResult", "AudioMixer", "FfmpegError"]


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


class AudioMixer:
    """Mezcla y normaliza audio a ~-14 LUFS usando ffmpeg (patrón de mux.sh + master.py)."""

    def __init__(self, *, ffmpeg_bin: str | None = None, ffprobe_bin: str | None = None) -> None:
        settings = get_settings()
        self.ffmpeg = ffmpeg_bin or settings.ffmpeg_bin
        self.ffprobe = ffprobe_bin or settings.ffprobe_bin
        self.master = VoiceMaster(ffmpeg_bin=self.ffmpeg, ffprobe_bin=self.ffprobe)

    # --- API pública -------------------------------------------------------- #

    def mix(
        self,
        tracks: list[MixTrack],
        out_path: str,
        *,
        cleanup: CleanupOptions | None = None,
    ) -> MixResult:
        """Mezcla ``tracks`` y escribe un WAV normalizado a ~-14 LUFS en ``out_path``.

        La primera pista (habitualmente la voz) marca el canal de referencia; las
        demás (música, SFX) se suman con su ganancia. La masterización (loudnorm de 2
        pasadas + limitador) se delega en ``master.py``. ``cleanup`` activa además la
        limpieza de voz; por defecto se desactiva para no tocar música/SFX.
        """
        if not tracks:
            raise ValueError("AudioMixer.mix requiere al menos una pista")

        opts = cleanup if cleanup is not None else CleanupOptions(enabled=False)

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        measured = self._measure_loudnorm(tracks, opts)
        self._render(tracks, measured, str(out), opts)
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

    def _measure_loudnorm(self, tracks: list[MixTrack], cleanup: CleanupOptions) -> dict | None:
        """Primera pasada: mide I/TP/LRA del mix para la segunda pasada lineal."""
        mix = self._mix_filter(tracks)
        measure = self.master.measure_chain("mix", "out", cleanup=cleanup)
        args = []
        for track in tracks:
            args += ["-i", track.path]
        args += [
            "-filter_complex",
            f"{mix};{measure}",
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
        return parse_loudnorm_json(proc.stderr or "")

    def _render(
        self,
        tracks: list[MixTrack],
        measured: dict | None,
        out_path: str,
        cleanup: CleanupOptions,
    ) -> None:
        """Segunda pasada: aplica la cadena de master.py y escribe el WAV."""
        mix = self._mix_filter(tracks)
        chain = self.master.chain("mix", "out", measured=measured, cleanup=cleanup)
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
        value = parse_integrated_lufs(proc.stderr or "")
        if value is None:
            raise FfmpegError(f"No se pudo medir la loudness integrada de '{path}'.")
        return value

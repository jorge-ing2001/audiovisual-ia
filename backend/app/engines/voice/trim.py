"""Recorte de silencios con re-mapeo de timings (FFmpeg ``silencedetect``).

Reimplementación **propia** (código de audiovisual-ia) *inspirada* en el recorte de
silencios de ``skills/voz-whisper/scripts/voz.py`` de JustCarlos30/skills-videos-cortos
(MIT). No se copia código: aquí se detectan los silencios con ``silencedetect`` de
FFmpeg, se construyen los tramos de habla con pequeños márgenes pre/post, se recorta el
audio concatenando esos tramos con crossfades cortos y se **re-mapean los ``WordTiming``**
al nuevo eje temporal usando la misma lógica de desplazamiento acumulado que voz.py.

Se activa por configuración (``AIV_TRIM_SILENCE``). Si se desactiva, el audio y los
timings pasan sin cambios, de modo que voz y subtítulos nunca se desalinean.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.engines.voice.master import FfmpegError
from app.providers.base import WordTiming


@dataclass
class Segment:
    """Un tramo de habla ``[start, end]`` (segundos) sobre el audio original."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class TrimResult:
    """Resultado del recorte: ruta del audio recortado y timings re-mapeados."""

    path: str
    segments: list[Segment]
    timings: list[WordTiming]
    original_duration: float
    trimmed_duration: float
    meta: dict = field(default_factory=dict)


# Parámetros por defecto (propios, en el espíritu de voz.py).
DEFAULT_SILENCE_DB = -35.0        # umbral de silencio (dBFS)
DEFAULT_MIN_SILENCE = 0.35        # duración mínima de un silencio para cortarlo (s)
DEFAULT_PAD = 0.08                # margen pre/post que se conserva alrededor del habla (s)
DEFAULT_CROSSFADE = 0.02          # crossfade entre tramos concatenados (s)


class SilenceTrimmer:
    """Detecta y recorta silencios largos re-mapeando los ``WordTiming``."""

    def __init__(self, *, ffmpeg_bin: str | None = None, ffprobe_bin: str | None = None) -> None:
        settings = get_settings()
        self.ffmpeg = ffmpeg_bin or settings.ffmpeg_bin
        self.ffprobe = ffprobe_bin or settings.ffprobe_bin

    # --- API pública -------------------------------------------------------- #

    def trim(
        self,
        in_path: str,
        out_path: str,
        timings: list[WordTiming] | None = None,
        *,
        silence_db: float = DEFAULT_SILENCE_DB,
        min_silence: float = DEFAULT_MIN_SILENCE,
        pad: float = DEFAULT_PAD,
        crossfade: float = DEFAULT_CROSSFADE,
    ) -> TrimResult:
        """Recorta los silencios de ``in_path`` y re-mapea ``timings`` al nuevo eje.

        Devuelve un ``TrimResult`` con el audio recortado y los ``WordTiming`` ya
        desplazados. Si no se detectan silencios recortables, copia el audio y deja
        los timings como están.
        """
        duration = self._probe_duration(in_path)
        silences = self._detect_silences(in_path, silence_db, min_silence)
        segments = self._speech_segments(silences, duration, pad)

        if not segments:
            # Nada de habla detectada: no recortar (evita vaciar el audio).
            segments = [Segment(0.0, duration)]

        self._render(in_path, out_path, segments, crossfade)
        trimmed = sum(s.duration for s in segments)
        remapped = self._remap_timings(timings or [], segments)
        return TrimResult(
            path=out_path,
            segments=segments,
            timings=remapped,
            original_duration=duration,
            trimmed_duration=trimmed,
            meta={"silence_db": silence_db, "segments": len(segments)},
        )

    # --- construcción de tramos y re-mapeo (lógica pura, testeable) --------- #

    @staticmethod
    def speech_segments(
        silences: list[tuple[float, float]], duration: float, pad: float = DEFAULT_PAD
    ) -> list[Segment]:
        """API pública de la lógica pura de tramos (para tests y reutilización)."""
        return SilenceTrimmer._speech_segments(silences, duration, pad)

    @staticmethod
    def _speech_segments(
        silences: list[tuple[float, float]], duration: float, pad: float
    ) -> list[Segment]:
        """Construye los tramos de habla a partir de los silencios detectados.

        ``silences`` es una lista de pares ``(inicio, fin)`` de cada silencio. El habla
        es el complemento; cada tramo se amplía ``pad`` segundos por lado (sin salirse
        de ``[0, duration]`` ni invadir el silencio adyacente).
        """
        if duration <= 0:
            return []
        ordered = sorted(silences, key=lambda s: s[0])
        segments: list[Segment] = []
        cursor = 0.0
        for sil_start, sil_end in ordered:
            sil_start = max(0.0, min(sil_start, duration))
            sil_end = max(0.0, min(sil_end, duration))
            if sil_start > cursor:
                start = max(cursor, sil_start - 0.0)  # fin del habla previa
                segments.append(Segment(cursor, sil_start))
            cursor = max(cursor, sil_end)
        if cursor < duration:
            segments.append(Segment(cursor, duration))

        # Aplicar márgenes pre/post sin solapar.
        padded: list[Segment] = []
        for i, seg in enumerate(segments):
            start = seg.start - pad
            end = seg.end + pad
            # No invadir al vecino anterior/siguiente ni salir de [0, duration].
            prev_end = padded[-1].end if padded else 0.0
            start = max(0.0, start, prev_end)
            nxt_start = segments[i + 1].start if i + 1 < len(segments) else duration
            end = min(duration, end, nxt_start)
            if end > start:
                padded.append(Segment(start, end))
        return padded

    @staticmethod
    def remap_timings(
        timings: list[WordTiming], segments: list[Segment]
    ) -> list[WordTiming]:
        """API pública del re-mapeo (para tests y reutilización)."""
        return SilenceTrimmer._remap_timings(timings, segments)

    @staticmethod
    def _remap_timings(
        timings: list[WordTiming], segments: list[Segment]
    ) -> list[WordTiming]:
        """Re-mapea los ``WordTiming`` del eje original al eje recortado.

        Para cada palabra se busca el tramo de habla que la contiene (por su punto
        medio) y se le resta el tiempo total de los silencios eliminados antes de ese
        tramo (desplazamiento acumulado, igual que voz.py). Las palabras que caen por
        completo dentro de un silencio eliminado se descartan.
        """
        if not timings or not segments:
            return list(timings)

        # Offset acumulado: para cada tramo, cuánto tiempo original se ha eliminado
        # antes de su inicio en el nuevo eje.
        new_starts: list[float] = []
        acc = 0.0
        for seg in segments:
            new_starts.append(acc)
            acc += seg.duration

        remapped: list[WordTiming] = []
        for w in timings:
            mid = (w.start_s + w.end_s) / 2.0
            seg_index = None
            for i, seg in enumerate(segments):
                if seg.start <= mid <= seg.end:
                    seg_index = i
                    break
            if seg_index is None:
                # La palabra cae en un silencio eliminado: descartarla.
                continue
            seg = segments[seg_index]
            shift = seg.start - new_starts[seg_index]
            new_start = max(0.0, w.start_s - shift)
            new_end = max(new_start, w.end_s - shift)
            # Acotar al tramo en el nuevo eje para que no solape con el siguiente.
            seg_new_end = new_starts[seg_index] + seg.duration
            new_end = min(new_end, seg_new_end)
            remapped.append(
                WordTiming(
                    word=w.word,
                    start_s=round(new_start, 3),
                    end_s=round(new_end, 3),
                )
            )
        return remapped

    # --- ffmpeg ------------------------------------------------------------- #

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

    def _run_probe(self, args: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.ffprobe, *args], capture_output=True, text=True, timeout=120
            )
        except FileNotFoundError as exc:
            raise FfmpegError(
                f"No se encontró ffprobe en '{self.ffprobe}'. Configura AIV_FFPROBE_BIN "
                "(en el sandbox: /projects/sandbox/.localbin/ffprobe)."
            ) from exc

    def _probe_duration(self, path: str) -> float:
        proc = self._run_probe(
            [
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ]
        )
        try:
            return float((proc.stdout or "").strip())
        except ValueError as exc:
            raise FfmpegError(
                f"ffprobe no pudo medir la duración de '{path}': {(proc.stderr or '')[-300:]}"
            ) from exc

    def _detect_silences(
        self, in_path: str, silence_db: float, min_silence: float
    ) -> list[tuple[float, float]]:
        """Ejecuta ``silencedetect`` y devuelve pares ``(inicio, fin)`` de silencio."""
        chain = f"silencedetect=noise={silence_db}dB:d={min_silence}"
        proc = self._run(
            ["-i", in_path, "-af", chain, "-f", "null", "-"]
        )
        return parse_silencedetect(proc.stderr or "")

    def _render(
        self, in_path: str, out_path: str, segments: list[Segment], crossfade: float
    ) -> None:
        """Recorta y concatena los tramos de habla con crossfades cortos."""
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        if len(segments) == 1:
            seg = segments[0]
            args = [
                "-y",
                "-i",
                in_path,
                "-af",
                f"atrim=start={seg.start:.3f}:end={seg.end:.3f},asetpts=PTS-STARTPTS",
                "-c:a",
                "pcm_s16le",
                str(out),
            ]
            proc = self._run(args)
            if proc.returncode != 0:
                raise FfmpegError(
                    f"ffmpeg no pudo recortar el audio: {(proc.stderr or '')[-600:]}"
                )
            return

        # Varios tramos: cortar cada uno y concatenar con acrossfade encadenado.
        parts = []
        labels = []
        for i, seg in enumerate(segments):
            label = f"s{i}"
            parts.append(
                f"[0:a]atrim=start={seg.start:.3f}:end={seg.end:.3f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
            labels.append(label)

        cf = max(0.001, crossfade)
        prev = labels[0]
        for i in range(1, len(labels)):
            out_label = "mix" if i == len(labels) - 1 else f"x{i}"
            parts.append(
                f"[{prev}][{labels[i]}]acrossfade=d={cf}:c1=tri:c2=tri[{out_label}]"
            )
            prev = out_label

        filter_complex = ";".join(parts)
        args = [
            "-y",
            "-i",
            in_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[mix]",
            "-c:a",
            "pcm_s16le",
            str(out),
        ]
        proc = self._run(args)
        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg no pudo concatenar los tramos: {(proc.stderr or '')[-600:]}"
            )


def parse_silencedetect(stderr: str) -> list[tuple[float, float]]:
    """Parsea las líneas ``silence_start``/``silence_end`` de ``silencedetect``."""
    silences: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                pending_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (IndexError, ValueError):
                pending_start = None
        elif "silence_end:" in line:
            try:
                end = float(line.split("silence_end:")[1].strip().split()[0].rstrip("|"))
            except (IndexError, ValueError):
                continue
            start = pending_start if pending_start is not None else max(0.0, end)
            silences.append((start, end))
            pending_start = None
    return silences

"""Puente con los scripts TTS MIT de lemo-opuscar (reutilización, §12 / NOTICE).

Los proveedores ``KokoroTTS`` y ``EdgeTTS`` no reimplementan la síntesis: invocan
los scripts ``core/tts/tts.py`` (Kokoro offline) y ``core/tts/tts_zh.py`` (edge-tts
online) del repositorio MIT lemo-opuscar, localizado por ``AIV_LEMO_OPUSCAR_PATH``.

Ambos scripts comparten el mismo contrato de entrada/salida:
  - Entrada: un ``lines.json`` = ``[{"id","text","voice","speed"/"rate","lang"}, ...]``.
  - Salida en ``out_dir``: ``<id>.wav`` (24 kHz mono) por línea + ``dur.json`` con
    las duraciones en segundos.

Este módulo construye el ``lines.json`` a partir de ``list[VoiceLine]`` y ejecuta el
script con el intérprete de Python actual, devolviendo los ``AudioAsset`` resultantes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.config import get_settings
from app.providers.base import AudioAsset, VoiceLine


class LemoNotAvailable(RuntimeError):
    """El repositorio lemo-opuscar (o el modelo/dependencia requerido) no está disponible."""


def lemo_path() -> Path:
    """Ruta a la copia de lemo-opuscar (``AIV_LEMO_OPUSCAR_PATH``).

    Lanza ``LemoNotAvailable`` con un mensaje accionable si no está configurada o no
    existe, para que los motores/tests degraden o se salten sin simular audio.
    """
    raw = get_settings().lemo_opuscar_path
    if not raw:
        raise LemoNotAvailable(
            "AIV_LEMO_OPUSCAR_PATH no está configurada: apúntala a la copia de "
            "lemo-opuscar (p. ej. /projects/sandbox/lemo-opuscar) para usar la voz MIT."
        )
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise LemoNotAvailable(f"AIV_LEMO_OPUSCAR_PATH no existe: {path}")
    return path


def script_path(relative: str) -> Path:
    """Ruta a un script dentro de lemo-opuscar (p. ej. ``core/tts/tts.py``)."""
    path = lemo_path() / relative
    if not path.is_file():
        raise LemoNotAvailable(f"No se encontró {relative} en lemo-opuscar: {path}")
    return path


def _build_lines_payload(
    lines: list[VoiceLine], *, default_voice: str | None, use_rate: bool
) -> list[dict]:
    """Traduce ``VoiceLine`` al ``lines.json`` que esperan los scripts de lemo-opuscar.

    ``use_rate=True`` (edge-tts) emite ``rate`` como porcentaje derivado de ``speed``;
    ``use_rate=False`` (Kokoro) emite ``speed`` como multiplicador directo.
    """
    payload: list[dict] = []
    for i, line in enumerate(lines):
        entry: dict[str, object] = {"id": f"line_{i}", "text": line.text}
        voice = line.voice or default_voice
        if voice:
            entry["voice"] = voice
        if line.language:
            entry["lang"] = line.language
        if use_rate:
            entry["rate"] = f"{round((float(line.speed) - 1) * 100):+d}%"
        else:
            entry["speed"] = float(line.speed)
        payload.append(entry)
    return payload


def run_tts_script(
    relative_script: str,
    lines: list[VoiceLine],
    out_dir: str,
    *,
    provider_name: str,
    default_voice: str | None,
    use_rate: bool,
    extra_args: list[str] | None = None,
    env: dict | None = None,
    timeout: float = 600.0,
) -> list[AudioAsset]:
    """Ejecuta un script TTS de lemo-opuscar y recoge los WAV + ``dur.json``.

    Devuelve un ``AudioAsset`` por línea, en el mismo orden de ``lines``. Lanza
    ``LemoNotAvailable`` si el script falla por falta de modelo/dependencia/red, de
    modo que la capa superior pueda degradar (kokoro->edge) o saltar el test.
    """
    script = script_path(relative_script)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    payload = _build_lines_payload(lines, default_voice=default_voice, use_rate=use_rate)
    lines_file = out / "lines.json"
    lines_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    cmd = [sys.executable, str(script), str(lines_file), str(out)]
    if extra_args:
        cmd.extend(extra_args)

    import os

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except FileNotFoundError as exc:  # intérprete no encontrado (no debería pasar)
        raise LemoNotAvailable(f"No se pudo ejecutar {relative_script}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LemoNotAvailable(
            f"{relative_script} excedió el tiempo límite ({timeout}s)"
        ) from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise LemoNotAvailable(
            f"{relative_script} falló (código {proc.returncode}): {detail[:600]}"
        )

    dur_file = out / "dur.json"
    durations: dict[str, float] = {}
    if dur_file.is_file():
        durations = json.loads(dur_file.read_text(encoding="utf-8"))

    assets: list[AudioAsset] = []
    for entry in payload:
        wav = out / f"{entry['id']}.wav"
        if not wav.is_file():
            raise LemoNotAvailable(
                f"{relative_script} no produjo el WAV esperado para {entry['id']}"
            )
        assets.append(
            AudioAsset(
                path=str(wav),
                duration_s=float(durations.get(str(entry["id"]), 0.0)),
                provider=provider_name,
                sample_rate=24000,
                meta={
                    "source": provider_name,
                    "license": "MIT (lemo-opuscar)",
                    "text": entry["text"],
                    "voice": entry.get("voice"),
                    "lang": entry.get("lang"),
                },
            )
        )
    return assets

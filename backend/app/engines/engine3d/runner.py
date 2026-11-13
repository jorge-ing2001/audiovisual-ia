"""Capa de EJECUCIÓN del motor 4 — invoca Blender 4.x headless (``runner``).

Esta es la frontera dura entre la lógica pura (``script_builder``/``mapping``/
``output_parser``/``assets``, 100% testeable sin Blender) y la ejecución real del
subproceso de Blender. ``BlenderEngine3D`` implementa el ``Protocol`` ``Engine3D``:

  * ``available()`` -> ``True`` solo si ``AIV_BLENDER_BIN`` apunta a un binario existente
    y ejecutable (vía ``shutil.which`` o ``os.access``). En el sandbox (sin Blender)
    devuelve ``False``.
  * ``render_sequence(spec, out_dir)`` escribe el script de ``script_builder`` a un ``.py``
    temporal del workspace, invoca ``blender -b -P <script> -- <out_dir>`` como subproceso
    con ``timeout=AIV_BLENDER_TIMEOUT_S`` y entrega stdout/stderr/returncode al
    ``output_parser`` para construir el ``VideoClip``. Si Blender no está disponible lanza
    ``Engine3DUnavailable`` con un mensaje accionable (nunca simula un clip, §9 del prompt
    maestro).

Patrón análogo a ``RenderEngine._run_remotion`` (``app/engines/render/engine.py``), con
``Engine3DUnavailable`` en vez de ``RenderUnavailable``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import Settings, get_settings
from app.engines.engine3d import assets as assets_mod
from app.engines.engine3d import output_parser, script_builder
from app.engines.engine3d.types import Engine3DUnavailable, Scene3DSpec, VideoClip

__all__ = ["BlenderEngine3D"]

# Documentación accionable que citan los mensajes de error.
_DOCS = "docs/ENGINE-3D-MVP-STYLIZED.md"


class BlenderEngine3D:
    """Motor 3D real: renderiza una escena a un clip de vídeo con Blender headless.

    Implementa el ``Protocol`` ``Engine3D``. El binario de Blender y el timeout se leen de
    la configuración (``AIV_BLENDER_BIN``, ``AIV_BLENDER_TIMEOUT_S``) salvo override por
    constructor (útil en tests).
    """

    name = "blender"

    def __init__(
        self,
        *,
        blender_bin: str | None = None,
        timeout_s: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.blender_bin = blender_bin or settings.blender_bin
        self.timeout_s = int(timeout_s if timeout_s is not None else settings.blender_timeout_s)
        self._settings = settings

    # --- disponibilidad ----------------------------------------------------- #

    def _resolve_binary(self) -> str | None:
        """Ruta absoluta al binario de Blender si existe y es ejecutable, si no ``None``.

        Acepta tanto un nombre en ``PATH`` (``shutil.which``) como una ruta concreta
        (``os.access(..., X_OK)``), de forma análoga a cómo se configuran los binarios de
        ffmpeg/node del proyecto.
        """
        candidate = (self.blender_bin or "").strip()
        if not candidate:
            return None
        # Ruta con separador o absoluta: comprobar directamente que es ejecutable.
        if os.sep in candidate or (os.altsep and os.altsep in candidate):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            return None
        # Nombre suelto: buscar en PATH.
        return shutil.which(candidate)

    def available(self) -> bool:
        """¿Hay un binario de Blender utilizable en este entorno?"""
        return self._resolve_binary() is not None

    # --- render ------------------------------------------------------------- #

    def render_sequence(self, spec: Scene3DSpec, out_dir: str) -> VideoClip:
        """Renderiza la escena ``spec`` a un clip en ``out_dir`` ejecutando Blender.

        Lanza ``Engine3DUnavailable`` (con mensaje accionable que cita ``AIV_BLENDER_BIN``
        y ``docs/ENGINE-3D-MVP-STYLIZED.md``) si Blender no está disponible, si no se
        encuentra el binario al ejecutar, o si el render falla; nunca escribe un clip
        simulado.
        """
        binary = self._resolve_binary()
        if binary is None:
            raise Engine3DUnavailable(
                "Motor 3D no disponible: no se encontró un binario de Blender ejecutable "
                f"en AIV_BLENDER_BIN ({self.blender_bin!r}). Instala Blender 4.x y apunta "
                f"AIV_BLENDER_BIN a su ejecutable; ver {_DOCS}. "
                "No se simula un clip."
            )

        out_path = Path(out_dir).expanduser()
        out_path.mkdir(parents=True, exist_ok=True)

        resolved = assets_mod.resolve_assets(
            spec.character_id,
            spec.character_animation,
            spec.set_id,
            settings=self._settings,
        )
        if not resolved.complete:
            detail = " ".join(m.message() for m in resolved.missing)
            raise Engine3DUnavailable(
                f"Motor 3D: faltan assets para la escena {spec.scene_id}. {detail} "
                f"Configura AIV_BLENDER_ASSETS_DIR; ver {_DOCS}. No se simula un clip."
            )

        script_text = script_builder.build_blender_script(spec, resolved)
        output_file = str(out_path / script_builder.output_filename(spec))

        script_path = self._write_script(script_text, out_path, spec)
        try:
            proc = self._invoke_blender(binary, script_path, str(out_path), spec)
        finally:
            self._cleanup_script(script_path)

        return output_parser.build_video_clip(
            spec,
            output_file,
            proc.stdout or "",
            proc.stderr or "",
            proc.returncode,
            provider=None,
        )

    # --- helpers ------------------------------------------------------------ #

    @staticmethod
    def _write_script(script_text: str, out_dir: Path, spec: Scene3DSpec) -> str:
        """Escribe el script ``.py`` de Blender a un fichero temporal del workspace."""
        fd, path = tempfile.mkstemp(
            prefix=f"blender_scene_{spec.order}_{spec.scene_id}_",
            suffix=".py",
            dir=str(out_dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(script_text)
        except Exception:
            os.close(fd)
            raise
        return path

    @staticmethod
    def _cleanup_script(script_path: str) -> None:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    def _invoke_blender(
        self, binary: str, script_path: str, out_dir: str, spec: Scene3DSpec
    ) -> subprocess.CompletedProcess:
        """Invoca ``blender -b -P <script> -- <out_dir>`` capturando stdout/stderr."""
        cmd = [binary, "-b", "-P", script_path, "--", out_dir]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except FileNotFoundError as exc:
            raise Engine3DUnavailable(
                "Motor 3D no disponible: no se pudo ejecutar el binario de Blender "
                f"({binary!r}). Revisa AIV_BLENDER_BIN; ver {_DOCS}. No se simula un clip."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise Engine3DUnavailable(
                f"El render de Blender para la escena {spec.scene_id} excedió el "
                f"tiempo límite de {self.timeout_s}s (AIV_BLENDER_TIMEOUT_S). "
                f"Ver {_DOCS}. No se simula un clip."
            ) from exc

"""Runner del motor 3D (FEAT-303): ejecución real de Blender y disponibilidad.

Dos bloques:
  (a) **Puro / sin Blender** (corre siempre, también en el sandbox): con AIV_BLENDER_BIN
      apuntando a un binario inexistente, ``available()`` es ``False`` y
      ``render_sequence`` lanza ``Engine3DUnavailable`` con un mensaje accionable, sin
      escribir ningún ``.mp4`` (nunca se simula un clip).
  (b) **Render real de Blender** (``skipif`` cuando no hay Blender): se SALTA en el
      sandbox con razón explícita y solo se ejecuta en un entorno con Blender 4.x.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engines.engine3d.runner import BlenderEngine3D
from app.engines.engine3d.types import CameraSpec, Engine3DUnavailable, Scene3DSpec, VideoClip


def _spec(duration_s: float = 2.0, fps: int = 30) -> Scene3DSpec:
    return Scene3DSpec(
        scene_id=7,
        order=0,
        duration_s=duration_s,
        character_id="hero",
        character_animation="idle",
        set_id="room_interior",
        camera=CameraSpec(),
        fps=fps,
        render_engine="eevee",
    )


# --------------------------------------------------------------------------- #
# (a) Lógica pura: sin Blender -> no disponible, error accionable, sin .mp4.
# --------------------------------------------------------------------------- #


def test_available_false_when_binary_missing(tmp_path):
    engine = BlenderEngine3D(blender_bin=str(tmp_path / "no_such_blender"))
    assert engine.available() is False


def test_available_false_when_binary_name_not_in_path(monkeypatch):
    # Un nombre suelto que no existe en PATH tampoco cuenta como disponible.
    engine = BlenderEngine3D(blender_bin="blender_que_no_existe_12345")
    assert engine.available() is False


def test_render_sequence_raises_actionable_without_blender(tmp_path):
    out_dir = tmp_path / "out"
    engine = BlenderEngine3D(blender_bin=str(tmp_path / "no_such_blender"))

    with pytest.raises(Engine3DUnavailable) as exc:
        engine.render_sequence(_spec(), str(out_dir))

    msg = str(exc.value)
    assert "AIV_BLENDER_BIN" in msg
    assert "docs/ENGINE-3D-MVP-STYLIZED.md" in msg
    # Jamás se escribe un clip simulado.
    assert not list(tmp_path.rglob("*.mp4"))


def test_render_sequence_does_not_write_mp4_on_failure(tmp_path):
    out_dir = tmp_path / "out"
    engine = BlenderEngine3D(blender_bin=str(tmp_path / "missing"))
    with pytest.raises(Engine3DUnavailable):
        engine.render_sequence(_spec(), str(out_dir))
    assert not list(tmp_path.rglob("*.mp4"))


# --------------------------------------------------------------------------- #
# (b) Render real de Blender: SE SALTA en el sandbox (sin Blender), con razón.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not BlenderEngine3D().available(),
    reason="Blender no disponible en este entorno",
)
def test_render_real_minimal_clip(tmp_path):
    """Render mínimo real con Blender: produce un VideoClip con el .mp4 de la escena.

    Solo se ejecuta en el PC del usuario con Blender 4.x y los assets poblados en
    AIV_BLENDER_ASSETS_DIR; en el sandbox se SALTA (nunca se simula un clip).
    """
    engine = BlenderEngine3D()
    clip = engine.render_sequence(_spec(duration_s=1.0), str(tmp_path))
    assert isinstance(clip, VideoClip)
    assert Path(clip.path).is_file()
    assert clip.scene_id == 7
    assert clip.meta.get("frames_rendered", 0) > 0

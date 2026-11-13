"""Generador PURO del script ``.py`` que Blender ejecuta en headless.

``build_blender_script(spec, resolved_assets) -> str`` construye el TEXTO del script de
Blender (``blender -b -P script.py``) de forma DETERMINISTA: el mismo ``Scene3DSpec`` (y
los mismos assets) producen exactamente el mismo string. NO invoca Blender ni importa
``bpy``; solo compone el código fuente que Blender interpretará con su propio Python.

El script generado (ver docs/ENGINE-3D-ARCHITECTURE.md §2.2 y §5):
  - importa el MISMO personaje riggeado y el set resueltos (identidad constante),
  - aplica la animación corporal del catálogo,
  - fija el rango de frames a ``round(duration_s * fps)``,
  - coloca la cámara según ``CameraSpec`` (plano/movimiento/óptica/DOF),
  - configura EEVEE/Cycles + samples e iluminación cinematográfica key/fill/rim,
  - fija la resolución vertical 1080x1920 y la salida a un ``.mp4`` en ``out_dir``.
"""

from __future__ import annotations

from app.engines.engine3d.assets import ResolvedAssets
from app.engines.engine3d.types import Scene3DSpec

__all__ = ["build_blender_script", "frame_count", "output_filename"]


def frame_count(duration_s: float, fps: int) -> int:
    """Número de frames del clip: ``round(duration_s * fps)`` (mínimo 1)."""
    return max(1, round(float(duration_s) * int(fps)))


def output_filename(spec: Scene3DSpec) -> str:
    """Nombre de fichero determinista del clip de la escena (``.mp4``)."""
    return f"scene_{spec.order}_{spec.scene_id}.mp4"


def _engine_id(render_engine: str) -> str:
    """Normaliza el nombre del motor al id de Blender 4.x.

    Blender 4.2+ usa ``BLENDER_EEVEE_NEXT``; se mantiene ``BLENDER_EEVEE`` como alias
    aceptado por compatibilidad. ``cycles`` -> ``CYCLES``.
    """
    engine = (render_engine or "eevee").strip().lower()
    if engine == "cycles":
        return "CYCLES"
    return "BLENDER_EEVEE_NEXT"


def build_blender_script(spec: Scene3DSpec, resolved_assets: ResolvedAssets) -> str:
    """Construye el texto del script ``.py`` de Blender para renderizar ``spec``.

    Determinista: mismas entradas -> mismo string (verificado en tests). Las rutas y
    valores se serializan con ``repr``/``json`` para que el código generado sea válido y
    estable.
    """
    frames = frame_count(spec.duration_s, spec.fps)
    engine = _engine_id(spec.render_engine)
    out_name = output_filename(spec)
    camera = spec.camera

    character_path = resolved_assets.character.path
    animation_path = resolved_assets.animation.path
    set_path = resolved_assets.set.path

    # Cabecera determinista con los parámetros de la escena como constantes del script.
    header = f'''# Auto-generado por audiovisual-ia (motor 3D). NO editar a mano.
# Determinista: mismo Scene3DSpec -> mismo script.
# Ejecutar con: blender -b -P <este script> -- <out_dir>
# Doc: docs/ENGINE-3D-ARCHITECTURE.md
import os
import sys
import bpy

# --- Parámetros de la escena (derivados del Scene3DSpec) ---
SCENE_ID = {spec.scene_id}
SCENE_ORDER = {spec.order}
DURATION_S = {float(spec.duration_s)!r}
CHARACTER_ID = {spec.character_id!r}
CHARACTER_ANIMATION = {spec.character_animation!r}
SET_ID = {spec.set_id!r}
STYLE_PRESET = {spec.style_preset!r}
WIDTH = {spec.width}
HEIGHT = {spec.height}
FPS = {spec.fps}
FRAME_COUNT = {frames}
RENDER_ENGINE = {engine!r}
SAMPLES = {_samples_placeholder(spec)}
SEED = {spec.seed!r}
CAMERA_SHOT = {camera.shot!r}
CAMERA_MOVE = {camera.move!r}
CAMERA_FOCAL_MM = {float(camera.focal_mm)!r}
CAMERA_DOF = {bool(camera.dof)!r}

CHARACTER_PATH = {character_path!r}
ANIMATION_PATH = {animation_path!r}
SET_PATH = {set_path!r}

# Directorio de salida pasado tras el "--" (fallback a cwd si no se indica).
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT_DIR = _argv[0] if _argv else os.getcwd()
OUTPUT_PATH = os.path.join(OUT_DIR, {out_name!r})
'''

    body = '''

def _reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = FRAME_COUNT
    scene.render.fps = FPS
    return scene


def _configure_render(scene):
    scene.render.engine = RENDER_ENGINE
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    if RENDER_ENGINE == "CYCLES":
        scene.cycles.samples = SAMPLES
        if SEED is not None:
            scene.cycles.seed = SEED
    else:
        eevee = scene.eevee
        if hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = SAMPLES
        if hasattr(eevee, "use_gtao"):
            eevee.use_gtao = True
        if hasattr(eevee, "use_bloom"):
            eevee.use_bloom = True


def _import_asset(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for obj in dst.objects:
            if obj is not None:
                bpy.context.collection.objects.link(obj)
        return [o for o in dst.objects if o is not None]
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
        return list(bpy.context.selected_objects)
    if ext == ".glb" or ext == ".gltf":
        bpy.ops.import_scene.gltf(filepath=path)
        return list(bpy.context.selected_objects)
    raise RuntimeError("Formato de asset no soportado: %s" % path)


def _import_set():
    return _import_asset(SET_PATH)


def _import_character():
    return _import_asset(CHARACTER_PATH)


def _apply_animation(character_objects):
    # Importa el clip de animación (Mixamo .fbx) y lo aplica al armature del personaje.
    armature = next((o for o in character_objects if o.type == "ARMATURE"), None)
    if armature is None:
        return
    before = set(bpy.data.actions.keys())
    _import_asset(ANIMATION_PATH)
    after = set(bpy.data.actions.keys())
    new_actions = [bpy.data.actions[name] for name in (after - before)]
    if new_actions:
        if armature.animation_data is None:
            armature.animation_data_create()
        armature.animation_data.action = new_actions[0]


def _setup_camera():
    cam_data = bpy.data.cameras.new("SceneCamera")
    cam_data.lens = CAMERA_FOCAL_MM
    cam_data.dof.use_dof = CAMERA_DOF
    cam = bpy.data.objects.new("SceneCamera", cam_data)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam

    # Distancia/altura segun el plano (closeup mas cerca, wide/aerial mas lejos).
    placement = {
        "closeup": (0.0, -2.0, 1.5),
        "over_shoulder": (0.6, -2.2, 1.6),
        "medium": (0.0, -4.0, 1.5),
        "low_angle": (0.0, -3.5, 0.6),
        "wide": (0.0, -8.0, 1.8),
        "aerial": (0.0, -4.0, 8.0),
    }
    cam.location = placement.get(CAMERA_SHOT, placement["medium"])

    # Movimiento de camara: keyframes al inicio/fin del rango segun CAMERA_MOVE.
    if CAMERA_MOVE != "static":
        cam.keyframe_insert(data_path="location", frame=1)
        x, y, z = cam.location
        if CAMERA_MOVE == "zoom_in":
            cam.location = (x, y * 0.6, z)
        elif CAMERA_MOVE == "zoom_out":
            cam.location = (x, y * 1.4, z)
        elif CAMERA_MOVE == "dolly":
            cam.location = (x + 2.0, y, z)
        elif CAMERA_MOVE == "orbit":
            cam.location = (y, x, z)
        elif CAMERA_MOVE == "pan":
            cam.location = (x + 1.0, y, z)
        cam.keyframe_insert(data_path="location", frame=FRAME_COUNT)
    return cam


def _setup_cinematic_lighting():
    # Iluminacion cinematografica de 3 puntos: key / fill / rim.
    def _add_light(name, kind, energy, location):
        data = bpy.data.lights.new(name=name, type=kind)
        data.energy = energy
        obj = bpy.data.objects.new(name=name, object_data=data)
        obj.location = location
        bpy.context.collection.objects.link(obj)
        return obj

    _add_light("KeyLight", "AREA", 1000.0, (4.0, -4.0, 6.0))
    _add_light("FillLight", "AREA", 400.0, (-4.0, -2.0, 4.0))
    _add_light("RimLight", "AREA", 700.0, (0.0, 4.0, 5.0))
    # Volumetrica suave via mundo (estilo cinematografico).
    world = bpy.data.worlds.new("SceneWorld")
    bpy.context.scene.world = world


def _configure_output(scene):
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.audio_codec = "NONE"
    scene.render.filepath = OUTPUT_PATH


def main():
    scene = _reset_scene()
    _configure_render(scene)
    _import_set()
    character_objects = _import_character()
    _apply_animation(character_objects)
    _setup_camera()
    _setup_cinematic_lighting()
    _configure_output(scene)
    print("AIV_RENDER_BEGIN scene=%d frames=%d engine=%s out=%s" % (
        SCENE_ID, FRAME_COUNT, RENDER_ENGINE, OUTPUT_PATH))
    bpy.ops.render.render(animation=True)
    print("AIV_RENDER_DONE out=%s" % OUTPUT_PATH)


if __name__ == "__main__":
    main()
'''

    return header + body


def _samples_placeholder(spec: Scene3DSpec) -> int:
    """Muestras de render del script.

    El número de samples vive en settings (``AIV_BLENDER_SAMPLES``); se lee en FEAT-303 al
    construir el spec del runner. Para la lógica pura usamos un valor determinista derivado
    del spec cuando no viene en él: el MVP fija 32 (default de settings) salvo override
    futuro. Mantener determinista.
    """
    # Scene3DSpec no transporta samples en el MVP; se usa el default documentado (32).
    return 32

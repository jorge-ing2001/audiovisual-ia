"""TimelineBuilder con clip de vídeo por escena (producción 3D, FEAT-304).

Prueba de FORMA (sin Blender ni Chromium): dado un proyecto con un ``Asset(kind='video')``
por escena, el timeline emite ``videoSrc`` por escena (relativo al ``publicDir`` en
``remotion_input_props``) y conserva ``imageSrc`` para escenas 2d. La regla de duración por
escena (voz > Scene.duration_s) no cambia.
"""

from __future__ import annotations

from app.engines.render.timeline import TimelineBuilder
from app.models.entities import Asset, Project, Scene, Script
from app.storage.db import session_scope


def _seed_project(session) -> Project:
    project = Project(name="demo-3d", prompt="un héroe 3D", production_type="3d",
                      aspect_ratio="9:16")
    session.add(project)
    session.flush()
    session.add(Script(project_id=project.id, language="es", raw_text="..."))

    scene_a = Scene(
        project_id=project.id, order=0,
        narration_text="Primera escena.", visual_prompt="héroe caminando",
        duration_s=5.0, camera_notes="plano medio",
    )
    scene_b = Scene(
        project_id=project.id, order=1,
        narration_text="Segunda escena.", visual_prompt="plano general",
        duration_s=3.0,
    )
    session.add_all([scene_a, scene_b])
    session.flush()
    return project


def test_timeline_emits_video_src_per_scene(db_ready, tmp_path):
    with session_scope() as session:
        project = _seed_project(session)
        scenes = sorted(project.scenes, key=lambda s: s.order)

        clip0 = tmp_path / "scene_0.mp4"
        clip0.write_bytes(b"\x00\x00\x00\x18ftyp")
        clip1 = tmp_path / "scene_1.mp4"
        clip1.write_bytes(b"\x00\x00\x00\x18ftyp")
        voice0 = tmp_path / "voice0.wav"
        voice0.write_bytes(b"RIFF")

        session.add_all(
            [
                Asset(project_id=project.id, scene_id=scenes[0].id, kind="video",
                      path=str(clip0), provider="blender", meta={"duration_s": 2.0}),
                Asset(project_id=project.id, scene_id=scenes[1].id, kind="video",
                      path=str(clip1), provider="blender"),
                Asset(project_id=project.id, scene_id=scenes[0].id, kind="voice",
                      path=str(voice0), provider="kokoro", meta={"duration_s": 2.0}),
            ]
        )
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session, fps=30)

        s0, s1 = timeline.scenes
        assert s0.video_src == str(clip0)
        assert s1.video_src == str(clip1)
        # Sin imagen 2d en estas escenas.
        assert s0.image_src is None and s1.image_src is None
        # Duración: voz real (2 s) en s0; objetivo del guion (3 s) en s1 (sin voz).
        assert s0.duration_s == 2.0
        assert s1.duration_s == 3.0


def test_input_props_video_src_relative_to_public_dir(db_ready, tmp_path):
    public_dir = tmp_path / "media"
    (public_dir / "project_1" / "clips3d").mkdir(parents=True)
    clip = public_dir / "project_1" / "clips3d" / "scene_0.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftyp")

    with session_scope() as session:
        project = _seed_project(session)
        scenes = sorted(project.scenes, key=lambda s: s.order)
        session.add(
            Asset(project_id=project.id, scene_id=scenes[0].id, kind="video",
                  path=str(clip), provider="blender")
        )
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session)
        props = timeline.remotion_input_props(public_dir=str(public_dir))

        assert "videoSrc" in props["scenes"][0]
        # Relativo al publicDir (POSIX), resoluble con staticFile en Remotion.
        assert props["scenes"][0]["videoSrc"] == "project_1/clips3d/scene_0.mp4"


def test_input_props_preserves_image_src_for_2d_scene(db_ready, tmp_path):
    """Escena sin clip 3d conserva imageSrc (coexistencia 2d/3d en el mismo timeline)."""
    with session_scope() as session:
        project = _seed_project(session)
        scenes = sorted(project.scenes, key=lambda s: s.order)

        img = tmp_path / "img.png"
        img.write_bytes(b"\x89PNG\r\n")
        clip = tmp_path / "scene_0.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftyp")

        session.add_all(
            [
                Asset(project_id=project.id, scene_id=scenes[0].id, kind="video",
                      path=str(clip), provider="blender"),
                Asset(project_id=project.id, scene_id=scenes[1].id, kind="image",
                      path=str(img), provider="placeholder"),
            ]
        )
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session)
        s0, s1 = timeline.scenes
        assert s0.video_src == str(clip) and s0.image_src is None
        assert s1.image_src == str(img) and s1.video_src is None

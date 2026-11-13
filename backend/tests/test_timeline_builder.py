"""TimelineBuilder: ensambla un timeline válido a partir de escenas + assets (FEAT-005).

No requiere ffmpeg ni Remotion: solo prueba el ensamblado del JSON (una entrada por
escena, imagen/voz enlazadas, duraciones reales de la voz y subtítulos repartidos por
escena a partir del .srt del proyecto). El resultado se valida con Pydantic.
"""

from __future__ import annotations

from pathlib import Path

from app.engines.render.timeline import Timeline, TimelineBuilder
from app.models.entities import Asset, Project, Scene, Script
from app.storage.db import session_scope


def _seed_project(session) -> Project:
    project = Project(name="demo", prompt="un gato astronauta", aspect_ratio="16:9")
    session.add(project)
    session.flush()
    session.add(Script(project_id=project.id, language="es", raw_text="..."))

    scene_a = Scene(
        project_id=project.id,
        order=0,
        narration_text="Primera escena de la narración.",
        visual_prompt="gato en el espacio",
        duration_s=5.0,
        camera_notes="plano general",
    )
    scene_b = Scene(
        project_id=project.id,
        order=1,
        narration_text="Segunda escena de la narración.",
        visual_prompt="nave espacial",
        duration_s=3.0,
    )
    session.add_all([scene_a, scene_b])
    session.flush()
    return project


def _write_srt(path: Path) -> None:
    # Cues globales (acumulados por escena como los produce el motor de voz):
    # el primero cae en la escena 0 (0-2 s), el segundo en la escena 1 (>2 s).
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\nhola mundo\n\n"
        "2\n00:00:02,200 --> 00:00:03,000\nsegunda linea\n",
        encoding="utf-8",
    )


def test_builder_produces_one_entry_per_scene_with_assets(db_ready, tmp_path):
    with session_scope() as session:
        project = _seed_project(session)
        scenes = sorted(project.scenes, key=lambda s: s.order)

        img0 = tmp_path / "img0.png"
        img0.write_bytes(b"\x89PNG\r\n")
        voice0 = tmp_path / "voice0.wav"
        voice0.write_bytes(b"RIFF")

        srt = tmp_path / "subtitles.srt"
        _write_srt(srt)

        session.add_all(
            [
                Asset(
                    project_id=project.id,
                    scene_id=scenes[0].id,
                    kind="image",
                    path=str(img0),
                    provider="placeholder",
                ),
                Asset(
                    project_id=project.id,
                    scene_id=scenes[0].id,
                    kind="voice",
                    path=str(voice0),
                    provider="kokoro",
                    meta={"duration_s": 2.0},  # duración real de la voz (dur.json)
                ),
                Asset(
                    project_id=project.id,
                    scene_id=None,
                    kind="srt",
                    path=str(srt),
                    provider="faster_whisper",
                ),
            ]
        )
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session, fps=30)

        # Validación Pydantic + una entrada por escena.
        assert isinstance(timeline, Timeline)
        assert len(timeline.scenes) == 2
        assert timeline.width == 1920 and timeline.height == 1080
        assert timeline.fps == 30

        s0, s1 = timeline.scenes
        # Escena 0: duración de la voz real (2 s), no la objetivo (5 s).
        assert s0.duration_s == 2.0
        assert s0.duration_in_frames == 60
        assert s0.image_src == str(img0)
        assert s0.audio_src == str(voice0)
        # El primer cue cae en la escena 0 y se reubica a coordenadas locales.
        assert s0.cues and s0.cues[0].text == "hola mundo"
        assert s0.cues[0].start_s == 0.0
        assert s0.cues[0].end_s == 1.5

        # Escena 1: sin voz -> cae a la duración objetivo del guion (3 s).
        assert s1.duration_s == 3.0
        assert s1.image_src is None
        # El segundo cue (global 2.2-3.0 s) cae en la escena 1 (empieza en 2 s).
        assert s1.cues and s1.cues[0].text == "segunda linea"
        assert abs(s1.cues[0].start_s - 0.2) < 1e-6


def test_builder_input_props_shape_for_remotion(db_ready):
    with session_scope() as session:
        project = _seed_project(session)
        session.refresh(project)
        timeline = TimelineBuilder().build(project, session)

        props = timeline.remotion_input_props()
        assert set(props.keys()) == {"fps", "width", "height", "scenes"}
        assert len(props["scenes"]) == 2
        for scene in props["scenes"]:
            assert "durationInFrames" in scene
            assert scene["durationInFrames"] >= 1
            assert "imageSrc" in scene
            assert "cues" in scene


def test_builder_scene_never_zero_duration(db_ready):
    with session_scope() as session:
        project = Project(name="vacio", prompt="x")
        session.add(project)
        session.flush()
        session.add(
            Scene(
                project_id=project.id,
                order=0,
                narration_text="",
                visual_prompt="y",
                duration_s=0.0,
            )
        )
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session)
        assert len(timeline.scenes) == 1
        assert timeline.scenes[0].duration_s >= 1.0
        assert timeline.scenes[0].duration_in_frames >= 1

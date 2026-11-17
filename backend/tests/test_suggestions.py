"""FEAT-006 §3.2: sugerencias deterministas (lógica PURA, sin ffmpeg).

Verifica que ``engines/qa/suggestions.py`` detecta correctamente los avisos a partir de
tablas de estado dadas: escenas sin imagen, escenas con duración inválida, subtítulos que
exceden la duración, clips solapados en pistas no solapables y proyecto sin voz. Todos los
avisos tienen el shape ``{id, label, status, detail}`` de los checks del QA.

Además, un test de integración ligero del endpoint ``GET /projects/{id}/suggestions`` sobre
una BD temporal (escena sin imagen -> aviso), sin necesidad de ffmpeg.
"""

from __future__ import annotations

from app.engines.qa.suggestions import (
    build_suggestions,
    suggest_project_without_voice,
    suggest_scene_bad_duration,
    suggest_scenes_without_image,
    suggest_subtitle_overflow,
    suggest_track_overlaps,
)

# --------------------------------------------------------------------------- #
# Shape común
# --------------------------------------------------------------------------- #


def _assert_shape(item: dict) -> None:
    assert set(("id", "label", "status", "detail")).issubset(item.keys())
    assert isinstance(item["id"], str) and item["id"]
    assert item["status"] in ("warn", "fail")
    assert isinstance(item["detail"], str) and item["detail"]


# --------------------------------------------------------------------------- #
# Escenas sin imagen
# --------------------------------------------------------------------------- #


def test_scenes_without_image_flags_only_missing():
    scenes = [
        {"id": 1, "order": 0, "has_image": True},
        {"id": 2, "order": 1, "has_image": False},
        {"id": 3, "order": 2, "has_image": False},
    ]
    out = suggest_scenes_without_image(scenes)
    assert [s["id"] for s in out] == ["scene_without_image:2", "scene_without_image:3"]
    for item in out:
        _assert_shape(item)
        assert item["status"] == "fail"
    # Todas con imagen => sin avisos.
    assert suggest_scenes_without_image([{"id": 9, "order": 0, "has_image": True}]) == []


# --------------------------------------------------------------------------- #
# Subtítulos fuera de duración
# --------------------------------------------------------------------------- #


def test_subtitle_overflow_detects_cues_beyond_duration():
    cues = [
        {"id": "sub1", "start_s": 0.0, "end_s": 2.0},   # dentro
        {"id": "sub2", "start_s": 4.0, "end_s": 6.5},   # excede (dur 6.0)
    ]
    out = suggest_subtitle_overflow(cues, reference_duration_s=6.0)
    assert len(out) == 1
    assert out[0]["id"] == "subtitle_overflow:sub2"
    _assert_shape(out[0])
    assert out[0]["status"] == "warn"


def test_subtitle_overflow_none_reference_is_noop():
    cues = [{"id": "sub1", "start_s": 0.0, "end_s": 99.0}]
    assert suggest_subtitle_overflow(cues, reference_duration_s=None) == []


def test_subtitle_overflow_tolerates_rounding():
    cues = [{"id": "sub1", "start_s": 0.0, "end_s": 6.0005}]
    assert suggest_subtitle_overflow(cues, reference_duration_s=6.0) == []


# --------------------------------------------------------------------------- #
# Clips solapados
# --------------------------------------------------------------------------- #


def test_track_overlaps_detects_overlapping_clips():
    tracks = [
        {
            "id": "track_voice",
            "kind": "voice",
            "clips": [
                {"id": "a", "start_s": 0.0, "duration_s": 3.0},
                {"id": "b", "start_s": 2.0, "duration_s": 2.0},  # solapa 1s con a
                {"id": "c", "start_s": 5.0, "duration_s": 1.0},  # sin solape
            ],
        }
    ]
    out = suggest_track_overlaps(tracks)
    assert len(out) == 1
    _assert_shape(out[0])
    assert out[0]["status"] == "warn"
    assert out[0]["clip_id"] == "b"
    assert "1.00" in out[0]["detail"] or "1.0" in out[0]["detail"]


def test_track_overlaps_ignores_overlay_tracks():
    tracks = [
        {
            "id": "track_overlay",
            "kind": "overlay",
            "clips": [
                {"id": "o1", "start_s": 0.0, "duration_s": 10.0},
                {"id": "o2", "start_s": 1.0, "duration_s": 10.0},
            ],
        }
    ]
    # Los overlays pueden solaparse: sin avisos.
    assert suggest_track_overlaps(tracks) == []


def test_track_overlaps_ignores_disabled_clips():
    tracks = [
        {
            "id": "track_video",
            "kind": "video",
            "clips": [
                {"id": "a", "start_s": 0.0, "duration_s": 3.0},
                {"id": "b", "start_s": 1.0, "duration_s": 3.0, "enabled": False},
            ],
        }
    ]
    assert suggest_track_overlaps(tracks) == []


# --------------------------------------------------------------------------- #
# Avisos opcionales
# --------------------------------------------------------------------------- #


def test_scene_bad_duration():
    scenes = [
        {"id": 1, "order": 0, "duration_s": 2.0},
        {"id": 2, "order": 1, "duration_s": 0.0},
        {"id": 3, "order": 2, "duration_s": -1.0},
    ]
    out = suggest_scene_bad_duration(scenes)
    assert [s["id"] for s in out] == ["scene_bad_duration:2", "scene_bad_duration:3"]
    for item in out:
        _assert_shape(item)


def test_project_without_voice():
    no_voice = [{"id": "t_music", "kind": "music", "clips": [{"id": "m", "start_s": 0.0}]}]
    out = suggest_project_without_voice(no_voice)
    assert len(out) == 1
    assert out[0]["id"] == "project_without_voice"
    _assert_shape(out[0])

    with_voice = [
        {"id": "t_voice", "kind": "voice", "clips": [{"id": "v", "start_s": 0.0}]}
    ]
    assert suggest_project_without_voice(with_voice) == []


# --------------------------------------------------------------------------- #
# Agregado determinista
# --------------------------------------------------------------------------- #


def test_build_suggestions_is_deterministic_and_ordered():
    scenes = [
        {"id": 1, "order": 0, "duration_s": 3.0, "has_image": False},
        {"id": 2, "order": 1, "duration_s": 0.0, "has_image": True},
    ]
    tracks = [
        {
            "id": "track_subtitle",
            "kind": "subtitle",
            "clips": [
                {"id": "sub1", "start_s": 0.0, "duration_s": 2.0},
                {"id": "sub2", "start_s": 2.0, "duration_s": 10.0},  # end 12 > dur
            ],
        },
        {
            "id": "track_voice",
            "kind": "voice",
            "clips": [
                {"id": "v1", "start_s": 0.0, "duration_s": 5.0},
                {"id": "v2", "start_s": 4.0, "duration_s": 2.0},  # solape
            ],
        },
    ]
    first = build_suggestions(scenes, tracks, reference_duration_s=6.0)
    second = build_suggestions(scenes, tracks, reference_duration_s=6.0)
    assert first == second  # determinista

    ids = [s["id"] for s in first]
    # Orden: escena sin imagen -> duración inválida -> subtítulo fuera -> solape.
    assert ids == [
        "scene_without_image:1",
        "scene_bad_duration:2",
        "subtitle_overflow:sub2",
        "track_overlap:track_voice:v1:v2",
    ]
    for item in first:
        _assert_shape(item)


# --------------------------------------------------------------------------- #
# Endpoint GET /projects/{id}/suggestions (sin ffmpeg)
# --------------------------------------------------------------------------- #


def test_suggestions_endpoint_scene_without_image(client):
    created = client.post("/projects", json={"name": "sug", "prompt": "faro"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    from app.models.entities import Scene
    from app.storage.db import session_scope

    with session_scope() as session:
        session.add(
            Scene(
                project_id=project_id,
                order=0,
                narration_text="Un faro.",
                visual_prompt="faro",
                duration_s=3.0,
            )
        )
        session.commit()

    resp = client.get(f"/projects/{project_id}/suggestions")
    assert resp.status_code == 200
    data = resp.json()
    ids = [s["id"] for s in data]
    assert any(i.startswith("scene_without_image:") for i in ids)
    for item in data:
        _assert_shape(item)

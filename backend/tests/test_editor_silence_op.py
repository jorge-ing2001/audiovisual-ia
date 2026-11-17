"""FEAT-006 §3.1: auto-corte por silencios como operación del editor.

Cubre lo crítico (sin ffmpeg: la detección real de silencios se salta con razón en
``test_silence_trim.py``; aquí los silencios se pasan directos como lógica pura):

- La operación ``auto_trim_silence`` recorta la ``duration_s`` del clip de voz como la
  suma de los tramos de habla (``SilenceTrimmer.speech_segments``) y re-mapea los cues de
  subtítulo asociados preservando sus tiempos (``SilenceTrimmer.remap_timings``), sin
  perder las referencias (ids/estilo) de los cues que sobreviven.
- Aplicada vía ``save_edit_project`` crea una NUEVA ``Version`` y ``store.undo`` la
  REVIERTE: restaura el clip de voz y los cues de subtítulo ORIGINALES.
- Validaciones: rechaza clips no-audio, pistas bloqueadas y ``silences`` ausente/ inválido.
"""

from __future__ import annotations

import pytest

from app.engines.editor.model import EditClip, EditMeta, EditProject, EditTrack
from app.engines.editor.operations import (
    EditOperationError,
    apply_operation,
    op_auto_trim_silence,
)

# --------------------------------------------------------------------------- #
# Construcción de un proyecto de prueba: voz [0,5] con silencio [2,3] + subtítulos
# --------------------------------------------------------------------------- #


def _project_with_voice_and_subs() -> EditProject:
    voice = EditClip(
        id="voice0",
        track_id="track_voice",
        kind="voice",
        start_s=0.0,
        duration_s=5.0,
        source_in_s=0.0,
        source_out_s=5.0,
        asset_id=10,
    )
    # Dos cues: uno antes del silencio (dentro de [0,2]) y otro después (dentro de [3,5]).
    sub_a = EditClip(
        id="sub_a",
        track_id="track_subtitle",
        kind="subtitle",
        start_s=0.2,
        duration_s=0.6,  # [0.2, 0.8]
        text="hola",
    )
    sub_b = EditClip(
        id="sub_b",
        track_id="track_subtitle",
        kind="subtitle",
        start_s=3.2,
        duration_s=0.6,  # [3.2, 3.8]
        text="adios",
    )
    return EditProject(
        project_id=1,
        meta=EditMeta(fps=30),
        tracks=[
            EditTrack(id="track_voice", kind="voice", clips=[voice]),
            EditTrack(id="track_subtitle", kind="subtitle", clips=[sub_a, sub_b]),
        ],
    )


def _clip(ep: EditProject, clip_id: str) -> EditClip:
    found = ep.find_clip(clip_id)
    assert found is not None
    return found[1]


# --------------------------------------------------------------------------- #
# Lógica pura: recorte + re-mapeo de subtítulos
# --------------------------------------------------------------------------- #


def test_auto_trim_recuts_voice_and_remaps_subtitles():
    ep = _project_with_voice_and_subs()
    # Silencio [2,3] (1 s). Habla = [0,2] + [3,5] => 4 s con pad=0.
    result = apply_operation(
        ep,
        "auto_trim_silence",
        {"clip_id": "voice0", "silences": [[2.0, 3.0]], "pad": 0.0},
    )

    # La duración del clip de voz pasa a ser la suma de los tramos de habla (4 s).
    voice = _clip(result, "voice0")
    assert voice.duration_s == 4.0

    # 'sub_a' (antes del silencio) no se desplaza; 'sub_b' se adelanta 1 s.
    sub_a = _clip(result, "sub_a")
    sub_b = _clip(result, "sub_b")
    assert sub_a.start_s == pytest.approx(0.2, abs=1e-3)
    assert sub_b.start_s == pytest.approx(2.2, abs=1e-3)  # 3.2 - 1.0
    assert sub_b.duration_s == pytest.approx(0.6, abs=1e-3)
    # Se preservan las referencias (ids/texto) de los cues supervivientes.
    assert sub_a.text == "hola" and sub_b.text == "adios"

    # El original no se muta (operación pura).
    assert _clip(ep, "voice0").duration_s == 5.0
    assert _clip(ep, "sub_b").start_s == 3.2


def test_auto_trim_drops_cue_inside_removed_silence():
    ep = _project_with_voice_and_subs()
    # Añade un cue que cae por completo dentro del silencio [2,3].
    sub_track = next(t for t in ep.tracks if t.kind == "subtitle")
    sub_track.clips.append(
        EditClip(
            id="sub_mid",
            track_id="track_subtitle",
            kind="subtitle",
            start_s=2.3,
            duration_s=0.4,  # [2.3, 2.7] dentro del silencio
            text="(silencio)",
        )
    )
    result = apply_operation(
        ep,
        "auto_trim_silence",
        {"clip_id": "voice0", "silences": [[2.0, 3.0]], "pad": 0.0},
    )
    sub_ids = {c.id for t in result.tracks if t.kind == "subtitle" for c in t.clips}
    assert "sub_mid" not in sub_ids  # el cue del silencio se descarta
    assert {"sub_a", "sub_b"}.issubset(sub_ids)  # el resto sobrevive


def test_auto_trim_no_silence_keeps_full_clip():
    ep = _project_with_voice_and_subs()
    result = apply_operation(
        ep, "auto_trim_silence", {"clip_id": "voice0", "silences": [], "pad": 0.0}
    )
    assert _clip(result, "voice0").duration_s == 5.0


# --------------------------------------------------------------------------- #
# Validaciones
# --------------------------------------------------------------------------- #


def test_auto_trim_rejects_non_audio_clip():
    ep = EditProject(
        project_id=1,
        meta=EditMeta(),
        tracks=[
            EditTrack(
                id="track_video",
                kind="video",
                clips=[
                    EditClip(
                        id="v0",
                        track_id="track_video",
                        kind="video",
                        start_s=0.0,
                        duration_s=4.0,
                        source_in_s=0.0,
                        source_out_s=4.0,
                        asset_id=1,
                    )
                ],
            )
        ],
    )
    with pytest.raises(EditOperationError, match="no es de audio"):
        op_auto_trim_silence(ep, {"clip_id": "v0", "silences": [[1.0, 2.0]]})


def test_auto_trim_rejects_locked_track():
    ep = _project_with_voice_and_subs()
    next(t for t in ep.tracks if t.kind == "voice").locked = True
    with pytest.raises(EditOperationError, match="bloqueada"):
        op_auto_trim_silence(ep, {"clip_id": "voice0", "silences": [[2.0, 3.0]]})


def test_auto_trim_requires_silences():
    ep = _project_with_voice_and_subs()
    with pytest.raises(EditOperationError, match="silences"):
        op_auto_trim_silence(ep, {"clip_id": "voice0"})


def test_auto_trim_rejects_bad_silence_shape():
    ep = _project_with_voice_and_subs()
    with pytest.raises(EditOperationError):
        op_auto_trim_silence(ep, {"clip_id": "voice0", "silences": [[1.0]]})
    with pytest.raises(EditOperationError):
        op_auto_trim_silence(ep, {"clip_id": "voice0", "silences": [[3.0, 1.0]]})


# --------------------------------------------------------------------------- #
# Version + reversibilidad (store.undo) — requisito vinculante del usuario
# --------------------------------------------------------------------------- #


def _project_and_session():
    """Crea un Project real en una BD temporal y devuelve un contexto (project, session)."""
    from app.models.entities import Project
    from app.storage.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        project = Project(name="trim", prompt="faro")
        session.add(project)
        session.commit()
        session.refresh(project)
        pid = project.id

    class _Ctx:
        def __enter__(self):
            self._cm = session_scope()
            self.session = self._cm.__enter__()
            self.project = self.session.get(Project, pid)
            return self.project, self.session

        def __exit__(self, *a):
            return self._cm.__exit__(*a)

    return _Ctx()


def test_auto_trim_creates_version_and_undo_reverts(temp_env):
    from app.engines.editor.store import (
        history,
        load_active_edit_project,
        save_edit_project,
        undo,
    )

    base = _project_with_voice_and_subs()

    with _project_and_session() as (project, session):
        base = base.model_copy(update={"project_id": project.id})
        for track in base.tracks:
            for clip in track.clips:
                clip.track_id = clip.track_id  # (sin cambios; claridad)
        # Guarda el estado inicial (1ª versión) y aplica el auto-corte (2ª versión).
        save_edit_project(project, session, base)
        versions_before = history(project, session)

        active = load_active_edit_project(project, session)
        trimmed = apply_operation(
            active,
            "auto_trim_silence",
            {"clip_id": "voice0", "silences": [[2.0, 3.0]], "pad": 0.0},
        )
        save_edit_project(project, session, trimmed)

        versions_after = history(project, session)
        # El auto-corte creó una NUEVA versión del historial.
        assert len(versions_after) == len(versions_before) + 1

        # Estado tras el corte: voz 4 s, 'sub_b' adelantado a 2.2 s.
        after = load_active_edit_project(project, session)
        assert _clip(after, "voice0").duration_s == 4.0
        assert _clip(after, "sub_b").start_s == pytest.approx(2.2, abs=1e-3)

        # undo REVIERTE: clip y cues originales restaurados.
        reverted = undo(project, session)
        assert reverted is not None
        assert _clip(reverted, "voice0").duration_s == 5.0
        assert _clip(reverted, "sub_b").start_s == pytest.approx(3.2, abs=1e-3)
        assert _clip(reverted, "sub_a").start_s == pytest.approx(0.2, abs=1e-3)

        # Y lo que queda activo tras el undo es, efectivamente, el estado anterior.
        active_after_undo = load_active_edit_project(project, session)
        assert _clip(active_after_undo, "voice0").duration_s == 5.0

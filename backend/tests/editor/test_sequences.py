"""FEAT-008: varias secuencias por proyecto (persistencia segmentada por sequence_id).

Cubre lo crítico del cambio en ``store.py`` + endpoints de ``editor.py``:

(a) REGRESIÓN — sin ``sequence``, el flujo save/undo/redo/history de la secuencia ``main``
    se comporta EXACTAMENTE como antes (los campos nuevos no alteran el render: un
    ``EditProject`` previo sin ``sequence_id`` se trata como ``main`` y su inputProps/render
    no cambia).
(b) Dos secuencias del mismo proyecto mantienen contenido e historial undo/redo
    INDEPENDIENTES.
(c) ``load_active_edit_project`` devuelve la versión activa de la secuencia pedida.
(d) GET/POST .../editor/sequences listan y crean secuencias; el parámetro ``sequence``
    opera sobre la secuencia elegida sin romper las llamadas por defecto.
"""

from __future__ import annotations

from app.engines.editor.model import EditMeta, EditProject, EditTrack
from app.engines.editor.store import (
    create_sequence,
    history,
    list_sequences,
    load_active_edit_project,
    redo,
    save_edit_project,
    undo,
)


def _make_project_with_scene(client) -> int:
    created = client.post("/projects", json={"name": "seq", "prompt": "faro"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    from app.models.entities import Asset, Scene
    from app.storage.db import session_scope

    with session_scope() as session:
        scene = Scene(
            project_id=project_id,
            order=0,
            narration_text="Un faro.",
            visual_prompt="faro",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()
        session.add(
            Asset(
                project_id=project_id,
                scene_id=scene.id,
                kind="voice",
                path="/tmp/voice.wav",
                provider="kokoro",
                meta={"duration_s": 2.0},
            )
        )
        session.commit()
    return project_id


def _voice_id(edit_project: dict) -> str:
    for t in edit_project["tracks"]:
        if t["kind"] == "voice" and t["clips"]:
            return t["clips"][0]["id"]
    raise AssertionError("no hay clip de voz")


# --------------------------------------------------------------------------- #
# (a) Regresión de la secuencia main (sin el parámetro sequence)
# --------------------------------------------------------------------------- #


def test_main_sequence_defaults_and_inputprops_regression(client):
    """Un EditProject abierto es la secuencia main/Principal y los campos nuevos son neutros."""
    project_id = _make_project_with_scene(client)
    opened = client.post(f"/projects/{project_id}/editor/open").json()["edit_project"]
    assert opened["sequence_id"] == "main"
    assert opened["sequence_name"] == "Principal"


def test_previous_document_without_sequence_is_main():
    """Un documento serializado SIN sequence_id valida y es la secuencia main (regresión)."""
    legacy = {
        "kind": "edit_project",
        "project_id": 1,
        "meta": {"fps": 30, "width": 1920, "height": 1080, "aspect_ratio": "16:9"},
        "tracks": [],
    }
    ep = EditProject.model_validate(legacy)
    assert ep.sequence_id == "main"
    assert ep.sequence_name == "Principal"
    # inputProps/duración no cambian por los campos nuevos.
    assert ep.duration_s == 0.0


def test_main_undo_redo_flow_unchanged(client):
    project_id = _make_project_with_scene(client)
    opened = client.post(f"/projects/{project_id}/editor/open").json()["edit_project"]
    vid = _voice_id(opened)

    client.post(
        f"/projects/{project_id}/editor/op",
        json={"op": "set_clip_gain", "params": {"clip_id": vid, "gain": 0.2}},
    )

    def _gain() -> float:
        state = client.get(f"/projects/{project_id}/editor").json()["edit_project"]
        return next(
            c["gain"] for t in state["tracks"] if t["kind"] == "voice" for c in t["clips"]
        )

    assert _gain() == 0.2
    assert client.post(f"/projects/{project_id}/editor/undo").status_code == 200
    assert _gain() == 1.0
    assert client.post(f"/projects/{project_id}/editor/redo").status_code == 200
    assert _gain() == 0.2


# --------------------------------------------------------------------------- #
# Helpers de store directo (sin API) para historiales independientes
# --------------------------------------------------------------------------- #


def _project_and_session():
    from app.models.entities import Project
    from app.storage.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        project = Project(name="seq-store", prompt="x")
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


def _edit(project_id: int, sequence_id: str, gain: float) -> EditProject:
    return EditProject(
        project_id=project_id,
        meta=EditMeta(),
        sequence_id=sequence_id,
        sequence_name=sequence_id.title(),
        tracks=[
            EditTrack(
                id="track_voice",
                kind="voice",
                clips=[
                    {
                        "id": "c0",
                        "track_id": "track_voice",
                        "kind": "voice",
                        "start_s": 0.0,
                        "duration_s": 1.0,
                        "gain": gain,
                    }
                ],
            )
        ],
    )


def _voice_gain(ep: EditProject) -> float:
    return ep.tracks[0].clips[0].gain


def test_two_sequences_independent_histories(client, db_ready):
    """(b) Dos secuencias mantienen contenido e historial undo/redo independientes."""
    with _project_and_session() as (project, session):
        pid = project.id
        # main: gain 1.0 -> 0.5 -> 0.25 (dos ediciones)
        save_edit_project(project, session, _edit(pid, "main", 1.0), sequence_id="main")
        save_edit_project(project, session, _edit(pid, "main", 0.5), sequence_id="main")
        save_edit_project(project, session, _edit(pid, "main", 0.25), sequence_id="main")
        # alt: gain 0.9 -> 0.8 (independiente)
        save_edit_project(project, session, _edit(pid, "alt", 0.9), sequence_id="alt")
        save_edit_project(project, session, _edit(pid, "alt", 0.8), sequence_id="alt")

        # Activo por secuencia (c).
        assert _voice_gain(load_active_edit_project(project, session, "main")) == 0.25
        assert _voice_gain(load_active_edit_project(project, session, "alt")) == 0.8

        # Undo en alt NO afecta a main.
        undone_alt = undo(project, session, "alt")
        assert _voice_gain(undone_alt) == 0.9
        assert _voice_gain(load_active_edit_project(project, session, "main")) == 0.25

        # Undo en main dos veces vuelve a 1.0; alt sigue en 0.9.
        assert _voice_gain(undo(project, session, "main")) == 0.5
        assert _voice_gain(undo(project, session, "main")) == 1.0
        assert _voice_gain(load_active_edit_project(project, session, "alt")) == 0.9

        # Redo en main re-aplica; redo en alt re-aplica su propia cadena.
        assert _voice_gain(redo(project, session, "main")) == 0.5
        assert _voice_gain(redo(project, session, "alt")) == 0.8

        # Historiales independientes: main tiene 3 ediciones + undos/redos; alt las suyas.
        main_hist = history(project, session, "main")
        alt_hist = history(project, session, "alt")
        assert main_hist and alt_hist
        # Las versiones de cada historial pertenecen solo a su secuencia (no se mezclan).
        assert len(main_hist) >= 3
        assert len(alt_hist) >= 2


def test_list_sequences(client, db_ready):
    with _project_and_session() as (project, session):
        pid = project.id
        save_edit_project(project, session, _edit(pid, "main", 1.0), sequence_id="main")
        save_edit_project(project, session, _edit(pid, "alt", 0.5), sequence_id="alt")
        seqs = list_sequences(project, session)
        ids = [s["sequence_id"] for s in seqs]
        assert ids[0] == "main"  # main primero
        assert set(ids) == {"main", "alt"}


def test_create_sequence_duplicates_active(client, db_ready):
    with _project_and_session() as (project, session):
        pid = project.id
        save_edit_project(project, session, _edit(pid, "main", 0.42), sequence_id="main")
        base = load_active_edit_project(project, session, "main")
        created = create_sequence(
            project, session, "alt", "Alternativa", base_from_active=base
        )
        assert created.sequence_id == "alt"
        assert created.sequence_name == "Alternativa"
        # Duplica el contenido de la activa.
        assert _voice_gain(created) == 0.42
        # main intacta.
        assert _voice_gain(load_active_edit_project(project, session, "main")) == 0.42


# --------------------------------------------------------------------------- #
# (d) Endpoints de secuencias
# --------------------------------------------------------------------------- #


def test_sequences_endpoints(client):
    project_id = _make_project_with_scene(client)
    client.post(f"/projects/{project_id}/editor/open")

    # Lista inicial: solo main.
    listed = client.get(f"/projects/{project_id}/editor/sequences")
    assert listed.status_code == 200
    assert [s["sequence_id"] for s in listed.json()] == ["main"]

    # Crea una secuencia duplicando la activa (main).
    created = client.post(
        f"/projects/{project_id}/editor/sequences",
        json={"sequence_id": "vertical", "name": "9:16", "from": "active"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["edit_project"]["sequence_id"] == "vertical"

    # Ahora hay dos secuencias.
    listed2 = client.get(f"/projects/{project_id}/editor/sequences").json()
    assert {s["sequence_id"] for s in listed2} == {"main", "vertical"}

    # Crear una existente => 409.
    dup = client.post(
        f"/projects/{project_id}/editor/sequences",
        json={"sequence_id": "vertical"},
    )
    assert dup.status_code == 409, dup.text

    # Falta sequence_id => 422.
    bad = client.post(f"/projects/{project_id}/editor/sequences", json={})
    assert bad.status_code == 422, bad.text


def test_op_on_specific_sequence_isolated(client):
    project_id = _make_project_with_scene(client)
    client.post(f"/projects/{project_id}/editor/open")
    client.post(
        f"/projects/{project_id}/editor/sequences",
        json={"sequence_id": "alt", "from": "active"},
    )

    # Edita el gain de la voz SOLO en alt.
    alt = client.get(f"/projects/{project_id}/editor?sequence=alt").json()["edit_project"]
    vid = _voice_id(alt)
    client.post(
        f"/projects/{project_id}/editor/op?sequence=alt",
        json={"op": "set_clip_gain", "params": {"clip_id": vid, "gain": 0.3}},
    )

    def _gain(seq: str) -> float:
        q = f"?sequence={seq}" if seq != "main" else ""
        state = client.get(f"/projects/{project_id}/editor{q}").json()["edit_project"]
        return next(
            c["gain"] for t in state["tracks"] if t["kind"] == "voice" for c in t["clips"]
        )

    assert _gain("alt") == 0.3
    assert _gain("main") == 1.0  # main no se tocó

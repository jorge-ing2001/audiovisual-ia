// Test puro de snapTime / collectSnapCandidates (FEAT-003).
//
// Ejecútalo con:  node --experimental-strip-types --test tests/
// Sin DOM ni backend: el snap es una función pura que redondea un tiempo al borde de clip
// o marcador más cercano dentro de un umbral.

import assert from "node:assert/strict";
import { test } from "node:test";

import { collectSnapCandidates, snapTime } from "../lib/editor/snap.ts";

test("snapTime devuelve el candidato más cercano dentro del umbral", () => {
  assert.equal(snapTime(2.05, [0, 2, 5], 0.1), 2);
});

test("snapTime deja el tiempo si no hay candidato dentro del umbral", () => {
  assert.equal(snapTime(3.5, [0, 2, 5], 0.1), 3.5);
});

test("snapTime con lista vacía devuelve el tiempo original", () => {
  assert.equal(snapTime(1.23, [], 0.5), 1.23);
});

test("snapTime con umbral <= 0 desactiva el snap", () => {
  assert.equal(snapTime(2.01, [2], 0), 2.01);
  assert.equal(snapTime(2.01, [2], -1), 2.01);
});

test("snapTime elige el más cercano entre dos candidatos", () => {
  // 2.4 está a 0.4 de 2 y a 0.6 de 3 => gana 2.
  assert.equal(snapTime(2.4, [2, 3], 1), 2);
});

test("snapTime en empate elige el candidato menor (determinista)", () => {
  // 2.5 está a 0.5 de 2 y de 3 => gana el menor (2).
  assert.equal(snapTime(2.5, [2, 3], 1), 2);
});

test("snapTime ignora candidatos no finitos", () => {
  assert.equal(snapTime(2.02, [NaN, 2], 0.1), 2);
});

test("snapTime nunca devuelve negativo (candidato recortado a 0)", () => {
  assert.equal(snapTime(0.02, [-0.5], 1), 0);
});

// --------------------------------------------------------------------------- //
// collectSnapCandidates
// --------------------------------------------------------------------------- //

function project(markers = []) {
  return {
    kind: "edit_project",
    project_id: 1,
    meta: { fps: 30, width: 1920, height: 1080, aspect_ratio: "16:9", safe_top_frac: 0, safe_bottom_frac: 0 },
    tracks: [
      {
        id: "t1",
        kind: "image",
        name: "",
        order: 0,
        muted: false,
        locked: false,
        hidden: false,
        volume: 1,
        clips: [
          { id: "c0", track_id: "t1", kind: "image", start_s: 0, duration_s: 2, source_in_s: 0, source_out_s: null, asset_id: 1, z_order: 0, enabled: true, transform: null, transition_in: null, transition_out: null, effects: [], gain: 1, fade_in_s: 0, fade_out_s: 0, text: null, style: null, words: [], title: null, title_animation: null, overlay: null },
          { id: "c1", track_id: "t1", kind: "image", start_s: 3, duration_s: 1, source_in_s: 0, source_out_s: null, asset_id: 2, z_order: 0, enabled: true, transform: null, transition_in: null, transition_out: null, effects: [], gain: 1, fade_in_s: 0, fade_out_s: 0, text: null, style: null, words: [], title: null, title_animation: null, overlay: null },
        ],
      },
    ],
    markers,
    source_timeline_version: null,
  };
}

test("collectSnapCandidates reúne bordes de clip + origen", () => {
  // Bordes: c0 [0,2], c1 [3,4] + origen 0 => {0, 2, 3, 4}.
  assert.deepEqual(collectSnapCandidates(project()), [0, 2, 3, 4]);
});

test("collectSnapCandidates incluye el t_s de los marcadores", () => {
  const p = project([{ id: "m0", t_s: 1.5, label: "", color: "#38bdf8" }]);
  assert.deepEqual(collectSnapCandidates(p), [0, 1.5, 2, 3, 4]);
});

test("collectSnapCandidates excluye los bordes del clip indicado", () => {
  // Excluyendo c0, sus bordes 2 desaparecen (0 sigue por el origen).
  assert.deepEqual(collectSnapCandidates(project(), "c0"), [0, 3, 4]);
});

test("collectSnapCandidates sin markers (undefined) no falla", () => {
  const p = project();
  delete p.markers;
  assert.deepEqual(collectSnapCandidates(p), [0, 2, 3, 4]);
});

test("snap integrado: mover un clip pega al borde del vecino", () => {
  const p = project();
  const candidates = collectSnapCandidates(p, "c1");
  // Mover c1 a 2.03 (cerca del fin de c0 en 2) => se pega a 2.
  assert.equal(snapTime(2.03, candidates, 0.1), 2);
});

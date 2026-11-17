// Test puro de dragToAnchor / dragToSubtitleOffset (FEAT-008).
//
// Ejecútalo con:  node --experimental-strip-types --test tests/
// Sin DOM: el rectángulo del Player se pasa como DATO, de modo que el mapeo de
// coordenadas del preview a anclas/offsets es determinista y testeable.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clamp,
  dragToAnchor,
  dragToSubtitleOffset,
} from "../lib/editor/drag.ts";

const RECT = { left: 100, top: 50, width: 400, height: 200 };

test("clamp limita al rango", () => {
  assert.equal(clamp(5, 0, 1), 1);
  assert.equal(clamp(-2, 0, 1), 0);
  assert.equal(clamp(0.3, 0, 1), 0.3);
});

test("dragToAnchor mapea el centro del rect a (0.5, 0.5)", () => {
  const a = dragToAnchor(100 + 200, 50 + 100, RECT);
  assert.equal(a.anchor_x, 0.5);
  assert.equal(a.anchor_y, 0.5);
});

test("dragToAnchor mapea la esquina superior izquierda a (0, 0)", () => {
  const a = dragToAnchor(100, 50, RECT);
  assert.equal(a.anchor_x, 0);
  assert.equal(a.anchor_y, 0);
});

test("dragToAnchor mapea un cuarto/tres cuartos", () => {
  const a = dragToAnchor(100 + 100, 50 + 150, RECT);
  assert.equal(a.anchor_x, 0.25);
  assert.equal(a.anchor_y, 0.75);
});

test("dragToAnchor recorta fuera del rect a [0,1]", () => {
  const a = dragToAnchor(2000, -500, RECT);
  assert.equal(a.anchor_x, 1);
  assert.equal(a.anchor_y, 0);
});

test("dragToAnchor con rect degenerado devuelve el centro", () => {
  const a = dragToAnchor(10, 10, { left: 0, top: 0, width: 0, height: 0 });
  assert.deepEqual(a, { anchor_x: 0.5, anchor_y: 0.5 });
});

test("dragToSubtitleOffset: centro con preset bottom da offset hacia arriba", () => {
  // Soltar en el centro vertical del frame cuando el preset está abajo (baseY=0.88)
  // => offset Y negativo (sube el subtítulo). X centrado => 0.
  const o = dragToSubtitleOffset(300, 150, RECT, 1080, 1920, "bottom");
  assert.equal(o.x, 0);
  // anchor_y 0.5 - baseY 0.88 = -0.38 * 1920 = -729.6 -> -730
  assert.equal(o.y, Math.round((0.5 - 0.88) * 1920));
});

test("dragToSubtitleOffset: preset bottom, soltar en la base da offset ~0", () => {
  // y en 0.88 del rect: top 50 + 0.88*200 = 226
  const o = dragToSubtitleOffset(300, 226, RECT, 1080, 1920, "bottom");
  assert.equal(o.x, 0);
  assert.equal(o.y, 0);
});

test("dragToSubtitleOffset: desplazamiento horizontal en píxeles de composición", () => {
  // x en 0.75 del rect => (0.75 - 0.5) * 1080 = 270
  const o = dragToSubtitleOffset(100 + 300, 150, RECT, 1080, 1920, "center");
  assert.equal(o.x, 270);
  assert.equal(o.y, 0);
});

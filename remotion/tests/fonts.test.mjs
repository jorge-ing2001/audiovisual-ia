// Test puro del catálogo de tipografías (FEAT-007), lado Remotion (render/export).
//
// Ejecútalo con:  node --experimental-strip-types --test tests/
// (desde remotion/). Garantiza que el catálogo de export tiene las MISMAS familias que
// el del frontend (paridad preview/export) y que loadFont acepta cada familia.

import assert from "node:assert/strict";
import { test } from "node:test";

import { FONT_CATALOG, findFont, loadFont } from "../src/fonts.ts";

// Familias esperadas (deben coincidir con frontend/lib/editor/fonts.ts).
const EXPECTED = ["Inter", "Montserrat", "Roboto", "Oswald", "Bebas Neue", "Anton", "Lobster"];

test("FONT_CATALOG no está vacío y tiene las familias esperadas", () => {
  assert.ok(FONT_CATALOG.length > 0);
  assert.deepEqual(
    FONT_CATALOG.map((f) => f.family),
    EXPECTED,
  );
});

test("las familias del catálogo son únicas", () => {
  const families = FONT_CATALOG.map((f) => f.family);
  assert.equal(new Set(families).size, families.length);
});

test("loadFont acepta cada familia del catálogo", () => {
  for (const f of FONT_CATALOG) {
    const p = loadFont(f.family);
    assert.equal(typeof p.then, "function");
    if (p.catch) p.catch(() => {});
  }
});

test("loadFont rechaza familias fuera del catálogo", () => {
  assert.throws(() => loadFont("Times New Roman"));
});

test("findFont resuelve por familia y por css", () => {
  for (const f of FONT_CATALOG) {
    assert.equal(findFont(f.family)?.family, f.family);
    assert.equal(findFont(f.css)?.family, f.family);
  }
});

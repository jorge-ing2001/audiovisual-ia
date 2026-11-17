// Test puro del catálogo de tipografías (FEAT-007), lado frontend.
//
// Ejecútalo con:  node --experimental-strip-types --test tests/
// (desde frontend/). No necesita DOM ni navegador: comprueba la forma del catálogo y el
// contrato de loadFont (acepta cada familia del catálogo; rechaza las que no están).

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FONT_CATALOG,
  fontCss,
  findFont,
  loadFont,
} from "../lib/editor/fonts.ts";

test("FONT_CATALOG no está vacío", () => {
  assert.ok(FONT_CATALOG.length > 0);
});

test("las familias del catálogo son únicas", () => {
  const families = FONT_CATALOG.map((f) => f.family);
  assert.equal(new Set(families).size, families.length);
});

test("cada entrada tiene css, origen y licencia libre", () => {
  for (const f of FONT_CATALOG) {
    assert.equal(typeof f.family, "string");
    assert.ok(f.family.length > 0, `familia vacía`);
    assert.ok(f.css.includes(","), `css sin fallback: ${f.css}`);
    assert.ok(["google", "local"].includes(f.source));
    assert.ok(["OFL", "Apache-2.0"].includes(f.license), `licencia no libre: ${f.license}`);
  }
});

test("loadFont acepta cada familia del catálogo (por nombre y por css)", () => {
  for (const f of FONT_CATALOG) {
    const byFamily = loadFont(f.family);
    assert.equal(typeof byFamily.then, "function");
    if (byFamily.catch) byFamily.catch(() => {});
    const byCss = loadFont(f.css);
    assert.equal(typeof byCss.then, "function");
    if (byCss.catch) byCss.catch(() => {});
  }
});

test("findFont resuelve por familia canónica y por css", () => {
  for (const f of FONT_CATALOG) {
    assert.equal(findFont(f.family)?.family, f.family);
    assert.equal(findFont(f.css)?.family, f.family);
    assert.equal(fontCss(f.family), f.css);
  }
});

test("loadFont rechaza familias fuera del catálogo", () => {
  assert.throws(() => loadFont("Comic Sans MS"));
  assert.throws(() => loadFont("Arial"));
});

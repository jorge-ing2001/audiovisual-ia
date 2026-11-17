// Catálogo CERRADO de tipografías del editor (FEAT-007) — lado frontend (preview + UI).
//
// Es el ESPEJO de remotion/src/fonts.ts (mismas familias, mismo `css`, mismo `loadFont`).
// Define las familias elegibles en el inspector (un <select>, NUNCA texto libre) y un
// `loadFont(family)` que delega en el loader de @remotion/google-fonts de esa familia. El
// loader registra internamente un delayRender mientras descarga el woff2, así que el
// @remotion/player del preview espera la fuente igual que el render headless de export:
// "lo que ves es lo que exportas".
//
// Catálogo cerrado por diseño: solo familias de licencia libre (SIL Open Font License /
// Apache 2.0). Licencias documentadas en NOTICE y docs/SUBTITLES-TITLES-PRO.md.

import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { loadFont as loadBebasNeue } from "@remotion/google-fonts/BebasNeue";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadLobster } from "@remotion/google-fonts/Lobster";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadOswald } from "@remotion/google-fonts/Oswald";
import { loadFont as loadRoboto } from "@remotion/google-fonts/Roboto";

export type FontSource = "google" | "local";

export interface FontCatalogEntry {
  // Nombre canónico de la familia (lo que se guarda en el estilo y lo que ve el usuario
  // en el <select>). Único dentro del catálogo.
  family: string;
  // Origen de carga ('local' reservado para .woff2 propios si la red impidiera Google).
  source: FontSource;
  // Valor CSS `font-family` (familia + fallback de sistema) aplicado en el render.
  css: string;
  // Categoría orientativa para agrupar en el inspector.
  category: "sans" | "serif" | "display" | "handwriting";
  // Licencia libre de la fuente (para NOTICE/documentación).
  license: "OFL" | "Apache-2.0";
  // Fichero local (solo cuando source==='local').
  file?: string;
}

type FontLoader = () => { waitUntilDone: () => Promise<unknown> };

const LOADERS: Record<string, FontLoader> = {
  Inter: loadInter,
  Montserrat: loadMontserrat,
  Roboto: loadRoboto,
  Oswald: loadOswald,
  "Bebas Neue": loadBebasNeue,
  Anton: loadAnton,
  Lobster: loadLobster,
};

// Catálogo cerrado (orden = orden del <select>), idéntico al de remotion/src/fonts.ts.
export const FONT_CATALOG: readonly FontCatalogEntry[] = [
  { family: "Inter", source: "google", css: "Inter, sans-serif", category: "sans", license: "OFL" },
  { family: "Montserrat", source: "google", css: "Montserrat, sans-serif", category: "sans", license: "OFL" },
  { family: "Roboto", source: "google", css: "Roboto, sans-serif", category: "sans", license: "Apache-2.0" },
  { family: "Oswald", source: "google", css: "Oswald, sans-serif", category: "sans", license: "OFL" },
  { family: "Bebas Neue", source: "google", css: "'Bebas Neue', sans-serif", category: "display", license: "OFL" },
  { family: "Anton", source: "google", css: "Anton, sans-serif", category: "display", license: "OFL" },
  { family: "Lobster", source: "google", css: "Lobster, cursive", category: "handwriting", license: "OFL" },
] as const;

const BY_FAMILY: Record<string, FontCatalogEntry> = Object.fromEntries(
  FONT_CATALOG.map((f) => [f.family, f]),
);

const BY_CSS: Record<string, FontCatalogEntry> = Object.fromEntries(
  FONT_CATALOG.map((f) => [f.css, f]),
);

/** Devuelve la entrada del catálogo para una familia canónica (o su CSS), o `null`. */
export function findFont(familyOrCss: string): FontCatalogEntry | null {
  return BY_FAMILY[familyOrCss] ?? BY_CSS[familyOrCss] ?? null;
}

/** Valor CSS `font-family` de una familia del catálogo (o la propia cadena si no está). */
export function fontCss(family: string): string {
  return BY_FAMILY[family]?.css ?? family;
}

/**
 * Carga una familia del catálogo con el loader de Remotion. Lanza si la familia (o su
 * CSS) no está en el catálogo cerrado. Puede invocarse sin DOM (devuelve una promesa).
 */
export function loadFont(family: string): Promise<unknown> {
  const entry = findFont(family);
  if (!entry) {
    throw new Error(
      `Fuente desconocida ${JSON.stringify(family)}: no está en FONT_CATALOG ` +
        `(${FONT_CATALOG.map((f) => f.family).join(", ")}).`,
    );
  }
  const loader = LOADERS[entry.family];
  if (!loader) {
    throw new Error(`Fuente ${JSON.stringify(entry.family)} sin loader registrado.`);
  }
  return loader().waitUntilDone();
}

/**
 * Carga (sin esperar) todas las familias del catálogo presentes en una lista de cadenas
 * `font_family` (CSS o canónicas). Cada loadFont registra su propio delayRender. Ignora
 * cadenas que no estén en el catálogo.
 */
export function loadUsedFonts(families: readonly (string | null | undefined)[]): void {
  const seen = new Set<string>();
  for (const f of families) {
    if (!f) continue;
    const entry = findFont(f);
    if (!entry || seen.has(entry.family)) continue;
    seen.add(entry.family);
    LOADERS[entry.family]().waitUntilDone();
  }
}

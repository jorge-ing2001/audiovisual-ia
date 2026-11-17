// Catálogo CERRADO de tipografías del editor (FEAT-007) — lado Remotion (render/export).
//
// Define las familias que el usuario puede elegir en el inspector de subtítulos/títulos y
// un `loadFont(family)` que delega en el loader de @remotion/google-fonts de esa familia.
// El loader de Google Fonts registra internamente un `delayRender()` mientras descarga el
// woff2, de modo que el Chromium headless del render ESPERA a que la fuente esté lista
// antes de pintar el frame: así el export usa EXACTAMENTE la misma tipografía que el
// preview del navegador ("lo que ves es lo que exportas").
//
// Este módulo es el ESPEJO de frontend/lib/editor/fonts.ts (mismas familias, mismo
// `css` y mismo `loadFont`). Catálogo cerrado por diseño: solo familias de licencia
// libre (SIL Open Font License / Apache 2.0), nunca texto libre ni fuentes propietarias.
// Las licencias se documentan en NOTICE y en docs/SUBTITLES-TITLES-PRO.md.

import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { loadFont as loadBebasNeue } from "@remotion/google-fonts/BebasNeue";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadLobster } from "@remotion/google-fonts/Lobster";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadOswald } from "@remotion/google-fonts/Oswald";
import { loadFont as loadRoboto } from "@remotion/google-fonts/Roboto";

// Origen de la fuente. Todas las del catálogo actual son de Google Fonts (cargadas por su
// propio loader); 'local' queda reservado para empaquetar .woff2 propios bajo
// remotion/public/fonts/ si en algún entorno la red impidiera Google Fonts.
export type FontSource = "google" | "local";

export interface FontCatalogEntry {
  // Nombre canónico de la familia (lo que se guarda en SubtitleStyle/TitleStyle y lo que
  // ve el usuario en el <select> del inspector). Único dentro del catálogo.
  family: string;
  // Origen de carga.
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

// Carga perezosa de cada familia vía su loader de @remotion/google-fonts. El loader
// devuelve un handle con `waitUntilDone()`; además registra un delayRender interno, por
// lo que basta con invocarlo durante el render para que Remotion espere la fuente.
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

// Catálogo cerrado (orden = orden del <select>). Fallback de sistema coherente con la
// categoría para que, aun antes de que cargue el woff2, el layout sea razonable.
export const FONT_CATALOG: readonly FontCatalogEntry[] = [
  { family: "Inter", source: "google", css: "Inter, sans-serif", category: "sans", license: "OFL" },
  { family: "Montserrat", source: "google", css: "Montserrat, sans-serif", category: "sans", license: "OFL" },
  { family: "Roboto", source: "google", css: "Roboto, sans-serif", category: "sans", license: "Apache-2.0" },
  { family: "Oswald", source: "google", css: "Oswald, sans-serif", category: "sans", license: "OFL" },
  { family: "Bebas Neue", source: "google", css: "'Bebas Neue', sans-serif", category: "display", license: "OFL" },
  { family: "Anton", source: "google", css: "Anton, sans-serif", category: "display", license: "OFL" },
  { family: "Lobster", source: "google", css: "Lobster, cursive", category: "handwriting", license: "OFL" },
] as const;

// Índice por familia canónica para búsquedas O(1).
const BY_FAMILY: Record<string, FontCatalogEntry> = Object.fromEntries(
  FONT_CATALOG.map((f) => [f.family, f]),
);

// Índice por valor CSS (lo que viaja en font_family del estilo), para resolver desde las
// props compiladas qué familia del catálogo cargar.
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
 * Carga una familia del catálogo con el loader de Remotion. Devuelve una promesa que se
 * resuelve cuando la fuente está lista; lanza si la familia (o su CSS) no está en el
 * catálogo cerrado (el inspector solo ofrece familias válidas, así que esto protege de
 * documentos manipulados). Se puede invocar sin DOM: en el render headless Remotion
 * espera la promesa; en el navegador la fuente se registra en `document.fonts`.
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
 * Carga (sin esperar) todas las familias del catálogo que aparecen en una lista de
 * cadenas `font_family` (CSS o canónicas). Pensada para invocarse en el cuerpo de la
 * composición: cada `loadFont` registra su propio delayRender. Ignora cadenas que no
 * estén en el catálogo (p. ej. estilos por defecto que ya son "Inter, sans-serif").
 */
export function loadUsedFonts(families: readonly (string | null | undefined)[]): void {
  const seen = new Set<string>();
  for (const f of families) {
    if (!f) continue;
    const entry = findFont(f);
    if (!entry || seen.has(entry.family)) continue;
    seen.add(entry.family);
    // Dispara la carga; el delayRender interno del loader hace esperar al render.
    LOADERS[entry.family]().waitUntilDone();
  }
}

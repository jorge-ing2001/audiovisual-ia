// Ajuste magnético (snap) del editor (FEAT-003). Funciones PURAS, sin dependencias del
// DOM ni del backend: al mover/colocar un clip, redondean el tiempo al borde de clip o
// marcador más cercano dentro de un umbral. El tiempo YA ajustado se persiste con las
// operaciones existentes (move / set_timing / batch_move).
//
// Ver docs/EDITOR-ADVANCED.md §8 (snap es UX de frontend; no cambia el backend).

import type { EditProject } from "@/lib/editor/types";

/**
 * Redondea `t` al candidato más cercano dentro de `thresholdSeconds`.
 *
 * Redondeo: devuelve el candidato cuya distancia a `t` sea mínima, siempre que esa
 * distancia sea <= `thresholdSeconds`. Si no hay ningún candidato dentro del umbral (o la
 * lista está vacía), devuelve `t` sin cambios (el snap es opcional, nunca fuerza). Ante
 * empate de distancia gana el candidato con menor valor (determinista). Candidatos NaN o
 * negativos (tras recorte a 0) se ignoran. El resultado no es negativo.
 *
 * @param t  Tiempo propuesto (segundos, >= 0).
 * @param candidates  Puntos de alineación (segundos): bordes de clip + marcadores.
 * @param thresholdSeconds  Distancia máxima para "pegar" (> 0). <= 0 desactiva el snap.
 */
export function snapTime(
  t: number,
  candidates: readonly number[],
  thresholdSeconds: number,
): number {
  if (!Number.isFinite(t)) return t;
  if (!(thresholdSeconds > 0) || candidates.length === 0) return t;

  let best = t;
  let bestDist = thresholdSeconds + 1; // fuera de umbral de entrada
  for (const raw of candidates) {
    if (!Number.isFinite(raw)) continue;
    const candidate = raw < 0 ? 0 : raw;
    const dist = Math.abs(candidate - t);
    if (dist > thresholdSeconds) continue;
    // Más cercano gana; ante empate, el candidato menor (determinista).
    if (dist < bestDist || (dist === bestDist && candidate < best)) {
      best = candidate;
      bestDist = dist;
    }
  }
  return best;
}

/**
 * Reúne los puntos de alineación del proyecto para el snap: los bordes (start_s y end_s)
 * de todos los clips habilitados + el `t_s` de cada marcador, más el origen (0).
 *
 * Se puede excluir un clip por id (`excludeClipId`), típicamente el que se está moviendo,
 * para que no se pegue a sus propios bordes. El resultado está ordenado y sin duplicados.
 *
 * @param project  Documento de edición (estado de React).
 * @param excludeClipId  Id de clip a excluir de los bordes (p. ej. el arrastrado).
 */
export function collectSnapCandidates(
  project: EditProject,
  excludeClipId?: string | null,
): number[] {
  const set = new Set<number>();
  set.add(0);
  for (const track of project.tracks) {
    for (const clip of track.clips) {
      if (!clip.enabled) continue;
      if (excludeClipId && clip.id === excludeClipId) continue;
      set.add(round(clip.start_s));
      set.add(round(clip.start_s + clip.duration_s));
    }
  }
  for (const marker of project.markers ?? []) {
    set.add(round(marker.t_s));
  }
  return [...set].sort((a, b) => a - b);
}

function round(n: number): number {
  return Math.round(n * 1000) / 1000;
}

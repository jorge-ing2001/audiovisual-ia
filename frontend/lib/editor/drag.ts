// Mapeo PURO de coordenadas del preview (Remotion Player) a parámetros de posición del
// clip (FEAT-008). Sin dependencias del DOM: recibe el rectángulo del Player como DATO
// (un PlayerRect), de modo que es testeable sin navegador.
//
// El drag&drop del preview actualiza el estado de React en tiempo real con estas funciones
// (onPointerMove) y, al soltar (onPointerUp), persiste UNA sola operación set_position
// (title/overlay -> anchor_x/anchor_y; subtitle -> x/y px) => una Version por gesto.

// Rectángulo del área visible del Player en coordenadas de viewport (como el que devuelve
// getBoundingClientRect: left/top/width/height en px CSS).
export interface PlayerRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Anchor {
  anchor_x: number;
  anchor_y: number;
}

export interface PixelOffset {
  x: number;
  y: number;
}

/** Limita un número al rango [min, max]. */
export function clamp(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  return Math.min(max, Math.max(min, value));
}

/**
 * Convierte un punto del puntero (clientX, clientY, en coordenadas de viewport) a un
 * anclaje NORMALIZADO 0..1 relativo al área del Player. Pensado para título/overlay, cuya
 * posición es por fracción del frame. Si el rect no tiene tamaño positivo, devuelve el
 * centro (0.5, 0.5) para evitar divisiones por cero.
 */
export function dragToAnchor(
  clientX: number,
  clientY: number,
  rect: PlayerRect,
): Anchor {
  if (rect.width <= 0 || rect.height <= 0) {
    return { anchor_x: 0.5, anchor_y: 0.5 };
  }
  const nx = (clientX - rect.left) / rect.width;
  const ny = (clientY - rect.top) / rect.height;
  return {
    anchor_x: clamp(nx, 0, 1),
    anchor_y: clamp(ny, 0, 1),
  };
}

/**
 * Convierte un punto del puntero a un desplazamiento en PÍXELES de la composición para un
 * subtítulo. El subtítulo se ancla por preset en el centro horizontal y en la banda
 * indicada por `position` (top/center/bottom); el drag mueve el texto respecto a ese
 * anclaje, así que calculamos el offset en px de la composición (no del viewport) entre el
 * puntero y el punto de anclaje del preset.
 *
 * @param compositionWidth  Ancho de la composición en px (meta.width).
 * @param compositionHeight  Alto de la composición en px (meta.height).
 * @param position  Banda del preset del subtítulo.
 */
export function dragToSubtitleOffset(
  clientX: number,
  clientY: number,
  rect: PlayerRect,
  compositionWidth: number,
  compositionHeight: number,
  position: "top" | "center" | "bottom" = "bottom",
): PixelOffset {
  const { anchor_x, anchor_y } = dragToAnchor(clientX, clientY, rect);
  // Punto de anclaje del preset en fracción del frame: centrado en X; en Y según banda
  // (con un margen del 12% arriba/abajo, coherente con el padding del overlay).
  const baseX = 0.5;
  const baseY = position === "top" ? 0.12 : position === "center" ? 0.5 : 0.88;
  const x = Math.round((anchor_x - baseX) * compositionWidth);
  const y = Math.round((anchor_y - baseY) * compositionHeight);
  return { x, y };
}

// Tipos del timeline que consume Remotion (FEAT-005).
//
// El backend (engines/render/timeline.py) escribe un timeline JSON con esta misma
// forma y lo pasa como inputProps a `remotion render`. Cada escena es una imagen a
// pantalla completa durante `durationInFrames`, con su pista de subtítulos (cues
// relativos al inicio de la escena) sobre la imagen. El audio NO se renderiza aquí:
// la mezcla final (voz+música+SFX a -14 LUFS) se muxea con FFmpeg sobre el vídeo.

export type SubtitleCue = {
  // Tiempos en segundos, relativos al INICIO de la escena.
  startS: number;
  endS: number;
  text: string;
};

export type TimelineScene = {
  id: string;
  durationInFrames: number;
  imageSrc?: string;
  // Clip de vídeo de la escena (producción 3D). Si está presente, la composición dibuja
  // <OffthreadVideo> a pantalla completa en vez de <Img>.
  videoSrc?: string;
  // Subtítulos de la escena (ya recortados al rango de la escena por el builder).
  cues?: SubtitleCue[];
  // Narración textual de respaldo (depuración / accesibilidad); no se dibuja si hay cues.
  narrationText?: string;
  // Nota de cámara (placeholder en el MVP 2D; reservado para el motor de animación).
  cameraNotes?: string;
  // Estilo de subtítulo EXPLÍCITO derivado del production_type del proyecto (no del
  // artefacto visual): "tiktok" en 3D, "classic" en 2D. Si falta, se asume "classic"
  // (retrocompatibilidad con timelines 2D previos que no emiten el campo).
  subtitleStyle?: "tiktok" | "classic";
};

export type TimelineProps = {
  fps: number;
  width: number;
  height: number;
  scenes: TimelineScene[];
};

export const defaultTimelineProps: TimelineProps = {
  fps: 30,
  width: 1920,
  height: 1080,
  scenes: [],
};

// Duración total en frames (al menos 1 para que la composición sea válida aunque
// el timeline llegue vacío en el estudio).
export const totalDurationInFrames = (props: TimelineProps): number => {
  const total = props.scenes.reduce(
    (acc, scene) => acc + Math.max(0, Math.round(scene.durationInFrames)),
    0,
  );
  return Math.max(1, total);
};

// --------------------------------------------------------------------------- //
// Editor multipista (composición "Edit") — espejo de compile_remotion_props
// (backend/app/engines/editor/compile.py). El MISMO objeto inputProps alimenta
// el Remotion Player del navegador (preview, con audio) y el render headless de
// export (muted=true, el AudioMixer compone el audio final a −14 LUFS).
// --------------------------------------------------------------------------- //

export type EditFit = "cover" | "contain";

export type EditTransformProps = {
  scale: number;
  x: number;
  y: number;
  rotation: number;
  crop: { l: number; t: number; r: number; b: number };
  fit: EditFit;
};

export type EditTransitionProps = {
  type: "none" | "fade" | "slide" | "wipe";
  durationInFrames: number;
};

export type EditEffectProps = {
  type: string;
  params: Record<string, unknown>;
};

// Control de velocidad compilado (espejo de _visual_clip_props en compile.py). Opcional:
// solo llega cuando rate != 1.0 o reverse (regresión: sin él, el clip es idéntico).
export type EditSpeedProps = {
  rate: number;
  reverse: boolean;
};

// Congelación de fotograma compilada (espejo de _visual_clip_props). Opcional: solo llega
// cuando el clip tiene freeze definido. Los frames son del ORIGEN (atFrame) y de la
// duración congelada (holdInFrames); atS/holdS son los segundos originales (depuración).
export type EditFreezeProps = {
  atFrame: number;
  holdInFrames: number;
  atS: number;
  holdS: number;
};

export type EditVisualClipProps = {
  id: string;
  kind: "video" | "image";
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  src?: string | null;
  assetId?: number | null;
  transform?: EditTransformProps | null;
  transitionIn?: EditTransitionProps | null;
  transitionOut?: EditTransitionProps | null;
  effects?: EditEffectProps[];
  trackId?: string;
  trackOrder?: number;
  // Detach de audio (FEAT-005): true si el <OffthreadVideo> no debe reproducir su audio de
  // origen (el audio se oye por el clip voice desvinculado). Opcional (solo si true).
  muted?: boolean;
  // Velocidad/congelación (FEAT-006). Opcionales: solo llegan cuando difieren del default.
  speed?: EditSpeedProps | null;
  freeze?: EditFreezeProps | null;
};

export type EditSubtitlePreset =
  | "classic"
  | "tiktok"
  | "reels"
  | "shorts"
  | "cinema"
  | "clean";

export type EditSubtitleStyleProps = {
  fontFamily: string;
  fontSize: number;
  color: string;
  bgColor?: string | null;
  bold: boolean;
  outline: boolean;
  position: "top" | "center" | "bottom";
  x: number;
  y: number;
  preset: EditSubtitlePreset;
  // Decoración fina (contorno/sombra/fondo). Opcionales: solo llegan cuando difieren del
  // default, de modo que un subtítulo con estilo por defecto produce el MISMO inputProps
  // que antes del Bloque 2 (regresión). El render cae al aspecto heredado cuando faltan.
  outlineColor?: string;
  outlineWidth?: number;
  shadow?: boolean;
  shadowColor?: string;
  shadowBlur?: number;
  shadowX?: number;
  shadowY?: number;
  bgOpacity?: number;
  bgPadding?: number;
  bgRadius?: number;
  // Karaoke (resaltado por palabra). Opcionales: solo llegan cuando karaoke=true, de
  // modo que un subtítulo sin karaoke produce el MISMO inputProps que antes.
  karaoke?: boolean;
  highlightColor?: string;
  highlightScale?: number;
};

// Una palabra del clip de subtítulo con su ventana en frames RELATIVOS al clip (espejo
// de compile_remotion_props). Solo se emite cuando el clip tiene karaoke activo y words.
export type EditSubtitleWordProps = {
  text: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
};

export type EditSubtitleClipProps = {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  text: string;
  style?: EditSubtitleStyleProps | null;
  words?: EditSubtitleWordProps[];
};

// Clip de audio para el PREVIEW del Player (voz/música/SFX). El compilador de
// backend emite estas pistas a partir del EditProject (ver §6.1); el render de
// export las ignora (muted=true) porque el audio final lo compone el AudioMixer.
export type EditAudioClipProps = {
  id: string;
  kind: "voice" | "music" | "sfx";
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  src?: string | null;
  assetId?: number | null;
  // Volumen efectivo del clip (pista.volume * clip.gain), 0..1+.
  volume: number;
};

// Estilo de un clip de título (texto animado). Espejo de TitleStyle (model.py):
// tipografía + layout por anclaje (fracción del frame) + decoración fina heredada.
export type EditTitleStyleProps = {
  fontFamily: string;
  fontSize: number;
  color: string;
  bold: boolean;
  align: "left" | "center" | "right";
  anchorX: number;
  anchorY: number;
  kind: "intro" | "lower_third" | "credits" | "plain";
  // Decoración fina (contorno/sombra/fondo) heredada de TextDecoration (siempre presente).
  outlineColor: string;
  outlineWidth: number;
  shadow: boolean;
  shadowColor: string;
  shadowBlur: number;
  shadowX: number;
  shadowY: number;
  bgOpacity: number;
  bgPadding: number;
  bgRadius: number;
};

// Animación de entrada/salida del título. Espejo de TitleAnimation (model.py).
export type EditTitleAnimationProps = {
  type:
    | "none"
    | "fade"
    | "slide_up"
    | "slide_down"
    | "typewriter"
    | "scale_in"
    | "credits_roll";
  durationInFrames: number;
  durationS: number;
  direction: "in" | "out" | "both";
};

export type EditTitleClipProps = {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  text: string;
  style?: EditTitleStyleProps | null;
  animation?: EditTitleAnimationProps | null;
  trackOrder?: number;
};

// Estilo de un clip de overlay (imagen/logo/marca de agua). Espejo de OverlayStyle.
export type EditOverlayStyleProps = {
  opacity: number;
  anchorX: number;
  anchorY: number;
  widthFrac: number;
  rotation: number;
  tile: boolean;
};

export type EditOverlayClipProps = {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  src?: string | null;
  assetId?: number | null;
  overlay?: EditOverlayStyleProps | null;
  trackOrder?: number;
};

export type EditProps = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  visualClips: EditVisualClipProps[];
  subtitleClips: EditSubtitleClipProps[];
  // Pistas de audio para el preview. Opcional: el export headless las silencia.
  audioClips?: EditAudioClipProps[];
  // Títulos y overlays. Opcionales: solo llegan cuando hay clips, de modo que un
  // EditProject sin títulos/overlays produce el MISMO inputProps que antes (regresión).
  titleClips?: EditTitleClipProps[];
  overlayClips?: EditOverlayClipProps[];
  // Silencia el audio del Player (true en el render de export para evitar doble
  // audio con el AudioMixer). En el preview del navegador va false.
  muted?: boolean;
  // Zonas seguras verticales (fracción 0..1 del alto) de formatos 9:16. Opcionales:
  // solo llegan cuando el formato las define (> 0); sin ellas, el render encuadra igual
  // que antes (regresión 16:9). El overlay de subtítulos las respeta al posicionar.
  safeTop?: number;
  safeBottom?: number;
};

export const defaultEditProps: EditProps = {
  fps: 30,
  width: 1920,
  height: 1080,
  durationInFrames: 1,
  visualClips: [],
  subtitleClips: [],
  audioClips: [],
  muted: false,
};

// Duración total en frames de la composición "Edit" (al menos 1).
export const editDurationInFrames = (props: EditProps): number =>
  Math.max(1, Math.round(props.durationInFrames || 0));

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
};

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
  preset: "classic" | "tiktok";
};

export type EditSubtitleClipProps = {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  text: string;
  style?: EditSubtitleStyleProps | null;
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

export type EditProps = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  visualClips: EditVisualClipProps[];
  subtitleClips: EditSubtitleClipProps[];
  // Pistas de audio para el preview. Opcional: el export headless las silencia.
  audioClips?: EditAudioClipProps[];
  // Silencia el audio del Player (true en el render de export para evitar doble
  // audio con el AudioMixer). En el preview del navegador va false.
  muted?: boolean;
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

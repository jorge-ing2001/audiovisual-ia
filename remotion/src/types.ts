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

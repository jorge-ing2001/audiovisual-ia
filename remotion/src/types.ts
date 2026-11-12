// Tipos del timeline que consume Remotion.
// Andamiaje (FEAT-001): la forma mínima; el ensamblado real llega en FEAT-005.

export type TimelineScene = {
  id: string;
  durationInFrames: number;
  imageSrc?: string;
  audioSrc?: string;
  srtSrc?: string;
  narrationText?: string;
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

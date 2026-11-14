// Compilador del EditProject -> inputProps de la composición Remotion "Edit", en el
// CLIENTE (espejo de backend/app/engines/editor/compile.py::compile_remotion_props).
//
// Se usa para alimentar el @remotion/player del preview en tiempo real: cada edición
// del EditProject en el estado de React produce un nuevo objeto de inputProps y el Player
// se re-renderiza al instante. Las `src` de media se resuelven con assetDownloadUrl
// (opción A de la arquitectura, §6.2): el Player carga los recursos por HTTP.
//
// El backend emite EXACTAMENTE la misma forma para el export headless (con muted=true);
// así "lo que ves en el preview = lo que exportas".

import type { EditClip, EditProject, EditTrack } from "@/lib/editor/types";
import { AUDIO_KINDS, VISUAL_KINDS } from "@/lib/editor/types";

// Forma de inputProps (espejo de remotion/src/types.ts::EditProps).
export interface EditTransformInput {
  scale: number;
  x: number;
  y: number;
  rotation: number;
  crop: { l: number; t: number; r: number; b: number };
  fit: "cover" | "contain";
}

export interface EditTransitionInput {
  type: "none" | "fade" | "slide" | "wipe";
  durationInFrames: number;
}

export interface EditVisualClipInput {
  id: string;
  kind: "video" | "image";
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  src: string | null;
  assetId: number | null;
  transform: EditTransformInput | null;
  transitionIn: EditTransitionInput | null;
  transitionOut: EditTransitionInput | null;
  effects: { type: string; params: Record<string, unknown> }[];
  trackId: string;
  trackOrder: number;
}

export interface EditSubtitleStyleInput {
  fontFamily: string;
  fontSize: number;
  color: string;
  bgColor: string | null;
  bold: boolean;
  outline: boolean;
  position: "top" | "center" | "bottom";
  x: number;
  y: number;
  preset: "classic" | "tiktok";
}

export interface EditSubtitleClipInput {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  text: string;
  style: EditSubtitleStyleInput | null;
}

export interface EditAudioClipInput {
  id: string;
  kind: "voice" | "music" | "sfx";
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  src: string | null;
  assetId: number | null;
  volume: number;
}

export interface EditInputProps {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  visualClips: EditVisualClipInput[];
  subtitleClips: EditSubtitleClipInput[];
  audioClips: EditAudioClipInput[];
  muted: boolean;
}

const round = (n: number, d = 3): number => {
  const f = 10 ** d;
  return Math.round(n * f) / f;
};

const frameAt = (seconds: number, fps: number): number =>
  Math.max(0, Math.round(seconds * fps));

const framesLen = (seconds: number, fps: number): number =>
  Math.max(1, Math.round(seconds * fps));

function visualProps(
  clip: EditClip,
  track: EditTrack,
  fps: number,
  resolveSrc: (assetId: number | null) => string | null,
): EditVisualClipInput {
  const t = clip.transform;
  const transform: EditTransformInput | null = t
    ? {
        scale: t.scale,
        x: t.x,
        y: t.y,
        rotation: t.rotation,
        crop: { l: t.crop_l, t: t.crop_t, r: t.crop_r, b: t.crop_b },
        fit: t.fit,
      }
    : null;

  const transition = (
    tr: EditClip["transition_in"],
  ): EditTransitionInput | null =>
    tr ? { type: tr.type, durationInFrames: framesLen(tr.duration_s, fps) } : null;

  return {
    id: clip.id,
    kind: clip.kind === "video" ? "video" : "image",
    from: frameAt(clip.start_s, fps),
    durationInFrames: framesLen(clip.duration_s, fps),
    startS: round(clip.start_s),
    durationS: round(clip.duration_s),
    src: resolveSrc(clip.asset_id),
    assetId: clip.asset_id,
    transform,
    transitionIn: transition(clip.transition_in),
    transitionOut: transition(clip.transition_out),
    effects: clip.effects.map((e) => ({ type: e.type, params: e.params })),
    trackId: track.id,
    trackOrder: track.order,
  };
}

function subtitleProps(clip: EditClip, fps: number): EditSubtitleClipInput {
  const s = clip.style;
  return {
    id: clip.id,
    from: frameAt(clip.start_s, fps),
    durationInFrames: framesLen(clip.duration_s, fps),
    startS: round(clip.start_s),
    durationS: round(clip.duration_s),
    text: clip.text ?? "",
    style: s
      ? {
          fontFamily: s.font_family,
          fontSize: s.font_size,
          color: s.color,
          bgColor: s.bg_color,
          bold: s.bold,
          outline: s.outline,
          position: s.position,
          x: s.x,
          y: s.y,
          preset: s.preset,
        }
      : null,
  };
}

function audioProps(
  clip: EditClip,
  track: EditTrack,
  fps: number,
  resolveSrc: (assetId: number | null) => string | null,
): EditAudioClipInput {
  return {
    id: clip.id,
    kind: clip.kind as "voice" | "music" | "sfx",
    from: frameAt(clip.start_s, fps),
    durationInFrames: framesLen(clip.duration_s, fps),
    startS: round(clip.start_s),
    durationS: round(clip.duration_s),
    src: resolveSrc(clip.asset_id),
    assetId: clip.asset_id,
    volume: round(track.volume * clip.gain, 6),
  };
}

/**
 * Compila el EditProject a inputProps de la composición "Edit" para el Player.
 *
 * @param project  Documento de edición (estado de React).
 * @param resolveSrc  Traduce un asset_id a su URL (assetDownloadUrl) o null.
 * @param muted  Silencia el audio del Player (normalmente false en el preview).
 */
export function compileEditInputProps(
  project: EditProject,
  resolveSrc: (assetId: number | null) => string | null,
  muted = false,
): EditInputProps {
  const fps = project.meta.fps;
  const visual: EditVisualClipInput[] = [];
  const subtitle: EditSubtitleClipInput[] = [];
  const audio: EditAudioClipInput[] = [];

  const tracks = [...project.tracks].sort((a, b) => a.order - b.order);
  for (const track of tracks) {
    for (const clip of track.clips) {
      if (!clip.enabled) continue;
      if ((VISUAL_KINDS as readonly string[]).includes(clip.kind)) {
        visual.push(visualProps(clip, track, fps, resolveSrc));
      } else if (clip.kind === "subtitle") {
        subtitle.push(subtitleProps(clip, fps));
      } else if (
        (AUDIO_KINDS as readonly string[]).includes(clip.kind) &&
        !track.muted
      ) {
        audio.push(audioProps(clip, track, fps, resolveSrc));
      }
    }
  }

  visual.sort((a, b) => a.from - b.from || a.trackOrder - b.trackOrder);
  subtitle.sort((a, b) => a.from - b.from);
  audio.sort((a, b) => a.from - b.from);

  const durationS = editProjectDuration(project);
  const durationInFrames = durationS > 0 ? framesLen(durationS, fps) : 1;

  return {
    fps,
    width: project.meta.width,
    height: project.meta.height,
    durationInFrames,
    visualClips: visual,
    subtitleClips: subtitle,
    audioClips: audio,
    muted,
  };
}

function editProjectDuration(project: EditProject): number {
  let end = 0;
  for (const track of project.tracks) {
    for (const clip of track.clips) {
      if (clip.enabled) end = Math.max(end, clip.start_s + clip.duration_s);
    }
  }
  return round(end);
}

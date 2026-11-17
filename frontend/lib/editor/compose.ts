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

import type {
  EditClip,
  EditProject,
  EditTrack,
  SubtitlePreset,
} from "@/lib/editor/types";
import { AUDIO_KINDS, VISUAL_HIDE_KINDS, VISUAL_KINDS } from "@/lib/editor/types";

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
  preset: SubtitlePreset;
  // Decoración fina: solo presente cuando difiere del default (regresión: con estilo por
  // defecto el props no cambia respecto al Bloque 1).
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
  // Karaoke: solo presentes cuando karaoke=true (regresión: sin karaoke el props no cambia).
  karaoke?: boolean;
  highlightColor?: string;
  highlightScale?: number;
}

// Palabra del clip de subtítulo con ventana en frames RELATIVOS al clip (karaoke).
export interface EditSubtitleWordInput {
  text: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
}

export interface EditSubtitleClipInput {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  text: string;
  style: EditSubtitleStyleInput | null;
  words?: EditSubtitleWordInput[];
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

// Estilo de título compilado (espejo de _title_clip_props en compile.py).
export interface EditTitleStyleInput {
  fontFamily: string;
  fontSize: number;
  color: string;
  bold: boolean;
  align: "left" | "center" | "right";
  anchorX: number;
  anchorY: number;
  kind: "intro" | "lower_third" | "credits" | "plain";
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
}

export interface EditTitleAnimationInput {
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
}

export interface EditTitleClipInput {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  text: string;
  style: EditTitleStyleInput | null;
  animation: EditTitleAnimationInput | null;
  trackOrder?: number;
}

export interface EditOverlayStyleInput {
  opacity: number;
  anchorX: number;
  anchorY: number;
  widthFrac: number;
  rotation: number;
  tile: boolean;
}

export interface EditOverlayClipInput {
  id: string;
  from: number;
  durationInFrames: number;
  startS: number;
  durationS: number;
  src: string | null;
  assetId: number | null;
  overlay: EditOverlayStyleInput | null;
  trackOrder?: number;
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
  // Títulos y overlays: solo presentes cuando hay clips (regresión: props idéntico sin ellos).
  titleClips?: EditTitleClipInput[];
  overlayClips?: EditOverlayClipInput[];
  // Zonas seguras 9:16: solo presentes cuando el formato las define (> 0). Sin ellas, el
  // render encuadra igual que antes (regresión 16:9).
  safeTop?: number;
  safeBottom?: number;
}

// Defaults NEUTROS de la decoración fina (espejo de compile.py::_DECORATION_DEFAULTS).
// Si todos coinciden, NO se emiten claves nuevas (regresión: props idéntico al Bloque 1).
const DECORATION_DEFAULTS = {
  outline_color: "#000000",
  outline_width: 0,
  shadow: false,
  shadow_color: "#000000",
  shadow_blur: 0,
  shadow_x: 0,
  shadow_y: 0,
  bg_opacity: 1,
  bg_padding: 0.2,
  bg_radius: 8,
} as const;

function decorationIsDefault(s: {
  outline_color: string;
  outline_width: number;
  shadow: boolean;
  shadow_color: string;
  shadow_blur: number;
  shadow_x: number;
  shadow_y: number;
  bg_opacity: number;
  bg_padding: number;
  bg_radius: number;
}): boolean {
  return (
    s.outline_color === DECORATION_DEFAULTS.outline_color &&
    s.outline_width === DECORATION_DEFAULTS.outline_width &&
    s.shadow === DECORATION_DEFAULTS.shadow &&
    s.shadow_color === DECORATION_DEFAULTS.shadow_color &&
    s.shadow_blur === DECORATION_DEFAULTS.shadow_blur &&
    s.shadow_x === DECORATION_DEFAULTS.shadow_x &&
    s.shadow_y === DECORATION_DEFAULTS.shadow_y &&
    s.bg_opacity === DECORATION_DEFAULTS.bg_opacity &&
    s.bg_padding === DECORATION_DEFAULTS.bg_padding &&
    s.bg_radius === DECORATION_DEFAULTS.bg_radius
  );
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
  const karaokeOn = !!(s && s.karaoke);
  const decoNonDefault = !!(s && !decorationIsDefault(s));
  const style: EditSubtitleStyleInput | null = s
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
        // Decoración fina SOLO cuando difiere del default (regresión: props idéntico).
        ...(decoNonDefault
          ? {
              outlineColor: s.outline_color,
              outlineWidth: s.outline_width,
              shadow: s.shadow,
              shadowColor: s.shadow_color,
              shadowBlur: s.shadow_blur,
              shadowX: s.shadow_x,
              shadowY: s.shadow_y,
              bgOpacity: s.bg_opacity,
              bgPadding: s.bg_padding,
              bgRadius: s.bg_radius,
            }
          : {}),
        // Karaoke SOLO cuando está activo (regresión: sin karaoke, props idéntico).
        ...(karaokeOn
          ? {
              karaoke: true,
              highlightColor: s.highlight_color,
              highlightScale: s.highlight_scale,
            }
          : {}),
      }
    : null;

  const props: EditSubtitleClipInput = {
    id: clip.id,
    from: frameAt(clip.start_s, fps),
    durationInFrames: framesLen(clip.duration_s, fps),
    startS: round(clip.start_s),
    durationS: round(clip.duration_s),
    text: clip.text ?? "",
    style,
  };

  // Array de palabras (frames relativos al clip) SOLO si hay karaoke activo y palabras.
  if (karaokeOn && clip.words && clip.words.length > 0) {
    props.words = clip.words.map((w) => ({
      text: w.text,
      from: frameAt(w.start_s, fps),
      durationInFrames: framesLen(Math.max(0, w.end_s - w.start_s), fps),
      startS: round(w.start_s),
      durationS: round(Math.max(0, w.end_s - w.start_s)),
    }));
  }
  return props;
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

function titleProps(clip: EditClip, fps: number): EditTitleClipInput {
  const t = clip.title;
  const style: EditTitleStyleInput | null = t
    ? {
        fontFamily: t.font_family,
        fontSize: t.font_size,
        color: t.color,
        bold: t.bold,
        align: t.align,
        anchorX: t.anchor_x,
        anchorY: t.anchor_y,
        kind: t.kind,
        outlineColor: t.outline_color,
        outlineWidth: t.outline_width,
        shadow: t.shadow,
        shadowColor: t.shadow_color,
        shadowBlur: t.shadow_blur,
        shadowX: t.shadow_x,
        shadowY: t.shadow_y,
        bgOpacity: t.bg_opacity,
        bgPadding: t.bg_padding,
        bgRadius: t.bg_radius,
      }
    : null;
  const a = clip.title_animation;
  const animation: EditTitleAnimationInput | null = a
    ? {
        type: a.type,
        durationInFrames: framesLen(a.duration_s, fps),
        durationS: round(a.duration_s),
        direction: a.direction,
      }
    : null;
  return {
    id: clip.id,
    from: frameAt(clip.start_s, fps),
    durationInFrames: framesLen(clip.duration_s, fps),
    startS: round(clip.start_s),
    durationS: round(clip.duration_s),
    text: clip.text ?? "",
    style,
    animation,
  };
}

function overlayProps(
  clip: EditClip,
  fps: number,
  resolveSrc: (assetId: number | null) => string | null,
): EditOverlayClipInput {
  const o = clip.overlay;
  const overlay: EditOverlayStyleInput | null = o
    ? {
        opacity: o.opacity,
        anchorX: o.anchor_x,
        anchorY: o.anchor_y,
        widthFrac: o.width_frac,
        rotation: o.rotation,
        tile: o.tile,
      }
    : null;
  return {
    id: clip.id,
    from: frameAt(clip.start_s, fps),
    durationInFrames: framesLen(clip.duration_s, fps),
    startS: round(clip.start_s),
    durationS: round(clip.duration_s),
    src: resolveSrc(clip.asset_id),
    assetId: clip.asset_id,
    overlay,
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
  const title: EditTitleClipInput[] = [];
  const overlay: EditOverlayClipInput[] = [];

  const tracks = [...project.tracks].sort((a, b) => a.order - b.order);
  for (const track of tracks) {
    // Pista VISUAL oculta (hidden): se omiten sus clips (espejo de compile.py). Con
    // hidden=false (default) el inputProps es idéntico al baseline (regresión).
    if (
      track.hidden &&
      (VISUAL_HIDE_KINDS as readonly string[]).includes(track.kind)
    ) {
      continue;
    }
    for (const clip of track.clips) {
      if (!clip.enabled) continue;
      if ((VISUAL_KINDS as readonly string[]).includes(clip.kind)) {
        visual.push(visualProps(clip, track, fps, resolveSrc));
      } else if (clip.kind === "subtitle") {
        subtitle.push(subtitleProps(clip, fps));
      } else if (clip.kind === "title") {
        title.push({ ...titleProps(clip, fps), trackOrder: track.order });
      } else if (clip.kind === "overlay") {
        overlay.push({
          ...overlayProps(clip, fps, resolveSrc),
          trackOrder: track.order,
        });
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
  title.sort((a, b) => a.from - b.from || (a.trackOrder ?? 0) - (b.trackOrder ?? 0));
  overlay.sort(
    (a, b) => a.from - b.from || (a.trackOrder ?? 0) - (b.trackOrder ?? 0),
  );

  const durationS = editProjectDuration(project);
  const durationInFrames = durationS > 0 ? framesLen(durationS, fps) : 1;

  const props: EditInputProps = {
    fps,
    width: project.meta.width,
    height: project.meta.height,
    durationInFrames,
    visualClips: visual,
    subtitleClips: subtitle,
    audioClips: audio,
    muted,
  };
  // Zonas seguras SOLO cuando el formato las define (> 0): sin ellas, props idéntico
  // (regresión 16:9). Espejo de compile.py.
  const safeTop = project.meta.safe_top_frac ?? 0;
  const safeBottom = project.meta.safe_bottom_frac ?? 0;
  if (safeTop > 0 || safeBottom > 0) {
    props.safeTop = Math.round(safeTop * 10000) / 10000;
    props.safeBottom = Math.round(safeBottom * 10000) / 10000;
  }
  // Títulos/overlays SOLO cuando hay clips (regresión: props idéntico sin ellos). Espejo
  // de compile.py (titleClips/overlayClips condicionales).
  if (title.length > 0) props.titleClips = title;
  if (overlay.length > 0) props.overlayClips = overlay;
  return props;
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

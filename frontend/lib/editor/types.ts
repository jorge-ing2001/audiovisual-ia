// Tipos del EditProject multipista (espejo de backend/app/engines/editor/model.py y
// de los esquemas Pydantic EditProjectRead de backend/app/models/schemas.py).
//
// Es el CONTRATO frontend ↔ backend del editor. El documento se recibe de
// GET/POST .../editor embebido en `EditProjectRead.edit_project` y se reenvía tal cual
// en PUT .../editor. El mismo documento, compilado por compile_remotion_props, alimenta
// la composición Remotion "Edit" (preview con @remotion/player + export headless).

export const EDIT_PROJECT_KIND = "edit_project" as const;

export type TrackKind =
  | "video"
  | "image"
  | "voice"
  | "music"
  | "sfx"
  | "subtitle"
  | "title"
  | "overlay";

export const VISUAL_KINDS: readonly TrackKind[] = ["video", "image"];
export const VISUAL_RENDER_KINDS: readonly TrackKind[] = [
  "video",
  "image",
  "title",
  "overlay",
];
// Pistas VISUALES a las que afecta `hidden` (se omiten en preview/export). Espejo de
// VISUAL_HIDE_KINDS en model.py. Incluye subtitle (contenido visual).
export const VISUAL_HIDE_KINDS: readonly TrackKind[] = [
  "video",
  "image",
  "title",
  "overlay",
  "subtitle",
];
export const AUDIO_KINDS: readonly TrackKind[] = ["voice", "music", "sfx"];

export interface VisualTransform {
  scale: number;
  x: number;
  y: number;
  rotation: number;
  crop_l: number;
  crop_t: number;
  crop_r: number;
  crop_b: number;
  fit: "cover" | "contain";
}

export interface ClipTransition {
  type: "none" | "fade" | "slide" | "wipe";
  duration_s: number;
}

export interface ClipEffect {
  type: string;
  params: Record<string, unknown>;
}

export type SubtitlePreset =
  | "classic"
  | "tiktok"
  | "reels"
  | "shorts"
  | "cinema"
  | "clean";

export interface SubtitleStyle {
  font_family: string;
  font_size: number;
  color: string;
  bg_color: string | null;
  bold: boolean;
  outline: boolean;
  position: "top" | "center" | "bottom";
  x: number;
  y: number;
  preset: SubtitlePreset;
  // Decoración fina (contorno/sombra/fondo). Defaults NEUTROS: un documento previo sin
  // estos campos valida igual y produce el mismo render (espejo de TextDecoration).
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
  // Karaoke (resaltado por palabra). Defaults neutros (karaoke=false): un documento
  // previo sin estos campos se trata igual que antes.
  karaoke: boolean;
  highlight_color: string;
  highlight_scale: number;
}

// Palabra con tiempos RELATIVOS al inicio del clip de subtítulo (karaoke).
export interface SubtitleWord {
  text: string;
  start_s: number;
  end_s: number;
}

// Animación de un clip de título (espejo de TitleAnimation en model.py).
export interface TitleAnimation {
  type:
    | "none"
    | "fade"
    | "slide_up"
    | "slide_down"
    | "typewriter"
    | "scale_in"
    | "credits_roll";
  duration_s: number;
  direction: "in" | "out" | "both";
}

// Estilo de un clip de título (espejo de TitleStyle en model.py). Reutiliza los campos de
// decoración fina de TextDecoration (contorno/sombra/fondo) + layout por anclaje.
export interface TitleStyle {
  font_family: string;
  font_size: number;
  color: string;
  bold: boolean;
  align: "left" | "center" | "right";
  anchor_x: number;
  anchor_y: number;
  kind: "intro" | "lower_third" | "credits" | "plain";
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
}

// Estilo de un clip de overlay (imagen/logo/marca de agua). Espejo de OverlayStyle.
export interface OverlayStyle {
  opacity: number;
  anchor_x: number;
  anchor_y: number;
  width_frac: number;
  rotation: number;
  tile: boolean;
}

export interface EditClip {
  id: string;
  track_id: string;
  kind: TrackKind;
  start_s: number;
  duration_s: number;
  source_in_s: number;
  source_out_s: number | null;
  asset_id: number | null;
  z_order: number;
  enabled: boolean;

  // Visual (video/image)
  transform: VisualTransform | null;
  transition_in: ClipTransition | null;
  transition_out: ClipTransition | null;
  effects: ClipEffect[];

  // Audio (voice/music/sfx)
  gain: number;
  fade_in_s: number;
  fade_out_s: number;

  // Subtitle
  text: string | null;
  style: SubtitleStyle | null;
  words: SubtitleWord[];

  // Title (texto animado: intro/lower-third/créditos). Reutiliza `text`.
  title: TitleStyle | null;
  title_animation: TitleAnimation | null;

  // Overlay (imagen/logo/marca de agua). Reutiliza `asset_id`.
  overlay: OverlayStyle | null;
}

export interface EditTrack {
  id: string;
  kind: TrackKind;
  name: string;
  order: number;
  // Banderas de pista (espejo de EditTrack en model.py):
  // - muted: silencia AUDIO (voice/music/sfx) en preview/export.
  // - hidden: oculta una pista VISUAL (video/image/title/overlay/subtitle) en
  //   preview/export (no en el timeline). Default neutro (false): un documento previo sin
  //   el campo se trata igual que antes (regresión).
  // - locked: protege la edición; las operaciones que mutan clips la rechazan.
  muted: boolean;
  locked: boolean;
  hidden: boolean;
  volume: number;
  clips: EditClip[];
}

export interface EditMeta {
  fps: number;
  width: number;
  height: number;
  aspect_ratio: string;
  // Zonas seguras verticales (fracción 0..1 del alto) de formatos 9:16. Defaults
  // neutros (0.0): un documento 16:9 previo valida igual (espejo de EditMeta backend).
  safe_top_frac: number;
  safe_bottom_frac: number;
}

export interface EditProject {
  kind: typeof EDIT_PROJECT_KIND;
  project_id: number;
  meta: EditMeta;
  tracks: EditTrack[];
  source_timeline_version: number | null;
}

// Respuesta de los endpoints del editor (EditProjectRead): el documento completo
// embebido en `edit_project` + metadatos derivados.
export interface EditProjectRead {
  project_id: number;
  duration_s: number;
  source_timeline_version: number | null;
  edit_project: EditProject;
}

// Cuerpo de POST .../editor/op.
export interface EditOperationRequest {
  op: string;
  params: Record<string, unknown>;
}

// Respuesta de POST .../editor/export (job encolado, para polling).
export interface EditExportResponse {
  job_id: number;
  status: string;
  type: string;
}

// Entrada del historial de edición (GET .../editor/history).
export interface EditHistoryEntry {
  version: number;
  active: boolean;
  created_at?: string;
  [key: string]: unknown;
}

// --------------------------------------------------------------------------- //
// Helpers de dominio (puros) reutilizados por el workspace
// --------------------------------------------------------------------------- //

export function isVisualKind(kind: TrackKind): boolean {
  return VISUAL_KINDS.includes(kind);
}

export function isAudioKind(kind: TrackKind): boolean {
  return AUDIO_KINDS.includes(kind);
}

/** Duración total (fin del clip habilitado más tardío) del EditProject. */
export function editProjectDurationS(project: EditProject): number {
  let end = 0;
  for (const track of project.tracks) {
    for (const clip of track.clips) {
      if (clip.enabled) end = Math.max(end, clip.start_s + clip.duration_s);
    }
  }
  return Math.round(end * 1000) / 1000;
}

/** Localiza (track, clip) por id de clip, o null. */
export function findClip(
  project: EditProject,
  clipId: string,
): { track: EditTrack; clip: EditClip } | null {
  for (const track of project.tracks) {
    for (const clip of track.clips) {
      if (clip.id === clipId) return { track, clip };
    }
  }
  return null;
}

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
  | "subtitle";

export const VISUAL_KINDS: readonly TrackKind[] = ["video", "image"];
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
  preset: "classic" | "tiktok";
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
}

export interface EditTrack {
  id: string;
  kind: TrackKind;
  name: string;
  order: number;
  muted: boolean;
  locked: boolean;
  volume: number;
  clips: EditClip[];
}

export interface EditMeta {
  fps: number;
  width: number;
  height: number;
  aspect_ratio: string;
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

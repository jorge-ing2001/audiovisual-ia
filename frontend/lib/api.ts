// Cliente REST tipado hacia el backend FastAPI de audiovisual-ia.
//
// La URL base es configurable por entorno (NEXT_PUBLIC_API_URL). TODO el frontend
// consume estos helpers: cada acción de la UI dispara una llamada real a la API
// (sin datos simulados). Las formas de request/response replican los esquemas
// Pydantic de backend/app/models/schemas.py.

import type {
  EditExportResponse,
  EditHistoryEntry,
  EditOperationRequest,
  EditProject,
  EditProjectRead,
  EditSequence,
  TrackKind,
} from "@/lib/editor/types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// --------------------------------------------------------------------------- //
// Tipos (espejo de los esquemas Pydantic del backend)
// --------------------------------------------------------------------------- //

export interface HealthResponse {
  status: string;
}

export interface Project {
  id: number;
  name: string;
  prompt: string;
  production_type: string;
  aspect_ratio: string;
  resolution: string;
  status: string;
  provider_overrides: Record<string, unknown> | null;
  // Administración (FEAT-002/003). `folder` agrupa proyectos; `tags` siempre es lista.
  folder: string | null;
  tags: string[];
  created_at: string;
}

export interface ProjectCreate {
  name: string;
  prompt?: string;
  production_type?: string;
  aspect_ratio?: string;
  resolution?: string;
  provider_overrides?: Record<string, unknown> | null;
  folder?: string | null;
  tags?: string[];
}

export interface ProjectUpdate {
  name?: string;
  prompt?: string;
  production_type?: string;
  aspect_ratio?: string;
  resolution?: string;
  status?: string;
  provider_overrides?: Record<string, unknown> | null;
  // Administración (FEAT-002/003): mover de carpeta y editar etiquetas.
  folder?: string | null;
  tags?: string[];
}

/** Filtros opcionales de `GET /projects` (q + tag + folder). */
export interface ProjectListFilters {
  q?: string;
  tag?: string;
  folder?: string;
}

export interface Scene {
  id: number;
  project_id: number;
  order: number;
  narration_text: string;
  visual_prompt: string;
  duration_s: number;
  camera_notes: string | null;
  status: string;
}

export interface SceneUpdate {
  order?: number;
  narration_text?: string;
  visual_prompt?: string;
  duration_s?: number;
  camera_notes?: string;
  status?: string;
}

export interface SceneImageRequest {
  style?: string;
  seed?: number;
  force?: boolean;
}

export interface Asset {
  id: number;
  project_id: number;
  scene_id: number | null;
  kind: string;
  path: string;
  provider: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export interface GenerateVideoRequest {
  prompt?: string;
  style?: string;
  // Guion FIXTURE etiquetado: SOLO utilidad de verificación sin Ollama.
  // El camino normal de producción NO lo envía (usa el LLM real del usuario).
  script_fixture?: Record<string, unknown>;
  script_options?: Record<string, unknown>;
  audio_tracks?: Array<Record<string, unknown>>;
}

export interface GenerateVideoResponse {
  job_id: number;
  status: string;
  type: string;
}

export interface JobStep {
  step: string;
  detail?: string;
  status?: string;
  done?: number;
  total?: number | null;
}

export interface JobResult {
  steps?: JobStep[];
  current?: JobStep | null;
  [key: string]: unknown;
}

export interface Job {
  id: number;
  project_id: number | null;
  type: string;
  status: string;
  progress: number;
  error: string | null;
  retries: number;
  max_retries: number;
  payload: Record<string, unknown> | null;
  result: JobResult | null;
  created_at: string;
  updated_at: string;
}

// --------------------------------------------------------------------------- //
// Error tipado + helper de fetch JSON
// --------------------------------------------------------------------------- //

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      headers:
        init?.body !== undefined
          ? { "Content-Type": "application/json", ...(init?.headers ?? {}) }
          : init?.headers,
      ...init,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Error de red";
    throw new ApiError(0, `No se pudo contactar con el backend (${API_BASE_URL}): ${message}`);
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // cuerpo no-JSON: mantenemos el código HTTP
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --------------------------------------------------------------------------- //
// Health
// --------------------------------------------------------------------------- //

export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

// --------------------------------------------------------------------------- //
// Ajustes de solo lectura (/settings/image-backends) — FEAT-007 (Bloque 3)
// --------------------------------------------------------------------------- //

/** Flags NO sensibles de la generación local de imágenes (sin claves/secretos). */
export interface ImageBackendFlags {
  sd_local_enabled: boolean;
  sd_local_url: string;
  sd_max_concurrency: number;
}

/** Respuesta de `GET /settings/image-backends` (espejo del router settings.py). */
export interface ImageBackendsSettings {
  capability: string;
  active: string;
  available: string[];
  flags: ImageBackendFlags;
}

/**
 * Lee del backend los backends de imagen disponibles, el activo y los flags locales
 * de SD. SOLO LECTURA: la selección global es por variable de entorno
 * (`AIV_IMAGE_BACKEND`); el override por proyecto vive en `provider_overrides`. El
 * endpoint nunca devuelve claves/secretos.
 */
export function fetchImageBackends(): Promise<ImageBackendsSettings> {
  return request<ImageBackendsSettings>("/settings/image-backends");
}

// --------------------------------------------------------------------------- //
// Proyectos (/projects)
// --------------------------------------------------------------------------- //

/**
 * Lista proyectos con filtros OPCIONALES (`q`/`tag`/`folder`) que se envían como query
 * params a `GET /projects`. Sin filtros devuelve todos (compatibilidad). El backend aplica
 * `q` como substring sobre name/prompt, `tag` como pertenencia y `folder` como igualdad.
 */
export function listProjects(filters: ProjectListFilters = {}): Promise<Project[]> {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.folder) params.set("folder", filters.folder);
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<Project[]>(`/projects${query}`);
}

/** Lista de tags distintos existentes (`GET /projects/tags`), para poblar filtros. */
export function listTags(): Promise<string[]> {
  return request<string[]>("/projects/tags");
}

/** Lista de carpetas distintas existentes (`GET /projects/folders`), para poblar filtros. */
export function listFolders(): Promise<string[]> {
  return request<string[]>("/projects/folders");
}

export function getProject(projectId: number): Promise<Project> {
  return request<Project>(`/projects/${projectId}`);
}

export function createProject(body: ProjectCreate): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateProject(projectId: number, body: ProjectUpdate): Promise<Project> {
  return request<Project>(`/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteProject(projectId: number): Promise<void> {
  return request<void>(`/projects/${projectId}`, { method: "DELETE" });
}

/** Cuerpo opcional de `duplicateProject` (nombre del proyecto duplicado). */
export interface ProjectDuplicateRequest {
  name?: string;
}

/**
 * Resultado de `POST /projects/{id}/duplicate`: el backend responde con el `Project`
 * duplicado (modo síncrono, proyectos pequeños) o con un `Job` de seguimiento (modo cola,
 * proyectos grandes). Se discrimina con `isJob` mirando el campo `type` del Job.
 */
export type DuplicateResult = Project | Job;

/** Type guard: distingue un `Job` de un `Project` en la respuesta de duplicación. */
export function isJob(result: DuplicateResult): result is Job {
  return (
    typeof (result as Job).type === "string" &&
    typeof (result as Job).status === "string" &&
    "progress" in result
  );
}

/**
 * Duplica un proyecto (copia profunda con identidades nuevas). Devuelve el `Project`
 * nuevo (síncrono) o un `Job` para polling (proyectos grandes). El llamante usa `isJob`
 * para decidir si debe sondear el job con `useJobPolling` antes de refrescar.
 */
export function duplicateProject(
  projectId: number,
  body: ProjectDuplicateRequest = {},
): Promise<DuplicateResult> {
  return request<DuplicateResult>(`/projects/${projectId}/duplicate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --------------------------------------------------------------------------- //
// Escenas (/scenes)
// --------------------------------------------------------------------------- //

export function listScenes(projectId: number): Promise<Scene[]> {
  return request<Scene[]>(`/projects/${projectId}/scenes`);
}

export function updateScene(sceneId: number, body: SceneUpdate): Promise<Scene> {
  return request<Scene>(`/scenes/${sceneId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function regenerateSceneImage(
  sceneId: number,
  body: SceneImageRequest = { force: true },
): Promise<Asset> {
  return request<Asset>(`/scenes/${sceneId}/image`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * Genera la imagen de una escena con el motor visual (FEAT-B «Generar imagen»).
 *
 * Con `force=false` reutiliza la cache del motor (primera generación); es el camino
 * natural de «Generar» cuando la escena aún no tiene imagen. Comparte endpoint con
 * `regenerateSceneImage` (que usa `force=true` para forzar una alternativa nueva).
 */
export function generateSceneImage(
  sceneId: number,
  body: SceneImageRequest = { force: false },
): Promise<Asset> {
  return request<Asset>(`/scenes/${sceneId}/image`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * Sube una imagen propia del usuario y la asocia a la escena (FEAT-B «Subir imagen»).
 *
 * Envía `multipart/form-data` al endpoint real del backend
 * (`POST /scenes/{id}/image/upload`). El backend valida tipo/tamaño, guarda el fichero
 * dentro del workspace del proyecto y crea un `Asset(kind='image', provider='upload')`
 * que, por ser el más reciente de la escena, el timeline/render usa automáticamente.
 */
export async function uploadSceneImage(sceneId: number, file: File): Promise<Asset> {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/scenes/${sceneId}/image/upload`, {
      method: "POST",
      cache: "no-store",
      body: form,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Error de red";
    throw new ApiError(
      0,
      `No se pudo contactar con el backend (${API_BASE_URL}): ${message}`,
    );
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // cuerpo no-JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as Asset;
}

/**
 * Sube un overlay/logo/marca de agua a nivel de PROYECTO (FEAT-006).
 *
 * Envía `multipart/form-data` a `POST /projects/{id}/overlays/upload`. El backend valida
 * tipo (PNG/JPG/JPEG/WEBP/GIF/SVG) y tamaño, guarda el fichero en `overlays/uploads/` del
 * workspace y crea un `Asset(kind='image', provider='upload', scene_id=null)` que luego se
 * referencia con la operación `add_overlay`.
 */
export async function uploadOverlay(projectId: number, file: File): Promise<Asset> {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/projects/${projectId}/overlays/upload`, {
      method: "POST",
      cache: "no-store",
      body: form,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Error de red";
    throw new ApiError(
      0,
      `No se pudo contactar con el backend (${API_BASE_URL}): ${message}`,
    );
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // cuerpo no-JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as Asset;
}

/** Rol de un AUDIO importado al editor (mapea a Asset.kind: voice/music/sfx). */
export type EditMediaRole = "voice" | "music" | "sfx";

/**
 * Importa un medio (audio/vídeo/imagen) a nivel de PROYECTO para el editor (FEAT-007).
 *
 * Envía `multipart/form-data` a `POST .../editor/media/upload`. El backend detecta el
 * `kind` por extensión + content-type (imagen/vídeo o audio mapeado a `role`), valida la
 * imagen con Pillow y el audio/vídeo con ffprobe (leyendo `duration_s`), guarda el fichero
 * bajo `media/uploads/` (servible por `GET /assets/{id}/download`) y crea el `Asset`
 * correspondiente. Tras subir, el workspace coloca el clip con `addMediaClip` (§4.9/§6.1).
 */
export async function uploadEditMedia(
  projectId: number,
  file: File,
  role?: EditMediaRole,
): Promise<Asset> {
  const form = new FormData();
  form.append("file", file);
  const query = role ? `?role=${encodeURIComponent(role)}` : "";
  let res: Response;
  try {
    res = await fetch(
      `${API_BASE_URL}/projects/${projectId}/editor/media/upload${query}`,
      { method: "POST", cache: "no-store", body: form },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Error de red";
    throw new ApiError(
      0,
      `No se pudo contactar con el backend (${API_BASE_URL}): ${message}`,
    );
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // cuerpo no-JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as Asset;
}

// --------------------------------------------------------------------------- //
// Assets (/assets)
// --------------------------------------------------------------------------- //

export function listAssets(projectId: number, kind?: string): Promise<Asset[]> {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return request<Asset[]>(`/projects/${projectId}/assets${query}`);
}

/** URL de descarga/preview directa de un asset (imagen, audio, vídeo…). */
export function assetDownloadUrl(assetId: number): string {
  return `${API_BASE_URL}/assets/${assetId}/download`;
}

// --------------------------------------------------------------------------- //
// Generación end-to-end (/projects/{id}/generate) + jobs
// --------------------------------------------------------------------------- //

export function generateVideo(
  projectId: number,
  body: GenerateVideoRequest = {},
): Promise<GenerateVideoResponse> {
  return request<GenerateVideoResponse>(`/projects/${projectId}/generate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getJob(jobId: number): Promise<Job> {
  return request<Job>(`/jobs/${jobId}`);
}

export function listJobs(projectId?: number, statusFilter?: string): Promise<Job[]> {
  const params = new URLSearchParams();
  if (projectId !== undefined) params.set("project_id", String(projectId));
  if (statusFilter !== undefined) params.set("status_filter", statusFilter);
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<Job[]>(`/jobs${query}`);
}

// --------------------------------------------------------------------------- //
// Render (/projects/{id}/render, /video)
// --------------------------------------------------------------------------- //

export function enqueueRender(projectId: number): Promise<Job> {
  return request<Job>(`/projects/${projectId}/render`, { method: "POST" });
}

export function latestRender(projectId: number): Promise<Job> {
  return request<Job>(`/projects/${projectId}/render`);
}

/** URL de descarga directa del MP4 final del proyecto. */
export function videoDownloadUrl(projectId: number): string {
  return `${API_BASE_URL}/projects/${projectId}/video`;
}

// --------------------------------------------------------------------------- //
// Editor multipista (/projects/{id}/editor) — espejo de app/api/editor.py
// --------------------------------------------------------------------------- //

/**
 * Abre el editor: devuelve el EditProject activo o lo deriva del último Timeline.
 * Idempotente (POST .../editor/open). Es el primer paso del EditorWorkspace.
 */
// Secuencia activa por defecto (FEAT-008). Sin pasar `sequence` el backend opera sobre
// `main`, idéntico al comportamiento previo (regresión).
const DEFAULT_SEQUENCE = "main";

/** Query `?sequence=...` solo cuando NO es la secuencia por defecto (regresión). */
function sequenceQuery(sequence?: string): string {
  return sequence && sequence !== DEFAULT_SEQUENCE
    ? `?sequence=${encodeURIComponent(sequence)}`
    : "";
}

export function openEditor(
  projectId: number,
  sequence?: string,
): Promise<EditProjectRead> {
  return request<EditProjectRead>(
    `/projects/${projectId}/editor/open${sequenceQuery(sequence)}`,
    { method: "POST" },
  );
}

/** Estado completo del EditProject activo (GET .../editor). 404 si no se abrió. */
export function getEditProject(
  projectId: number,
  sequence?: string,
): Promise<EditProjectRead> {
  return request<EditProjectRead>(
    `/projects/${projectId}/editor${sequenceQuery(sequence)}`,
  );
}

/** Reemplaza el EditProject completo (PUT .../editor) y crea una Version (undo/redo). */
export function putEditProject(
  projectId: number,
  project: EditProject,
  sequence?: string,
): Promise<EditProjectRead> {
  return request<EditProjectRead>(
    `/projects/${projectId}/editor${sequenceQuery(sequence)}`,
    {
      method: "PUT",
      body: JSON.stringify({ edit_project: project }),
    },
  );
}

/** Aplica UNA operación pura sobre el EditProject activo (POST .../editor/op). */
export function applyEditOp(
  projectId: number,
  op: string,
  params: Record<string, unknown> = {},
  sequence?: string,
): Promise<EditProjectRead> {
  const body: EditOperationRequest = { op, params };
  return request<EditProjectRead>(
    `/projects/${projectId}/editor/op${sequenceQuery(sequence)}`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/**
 * Fija banderas de una pista (lock/hide/mute) con la operación `set_track_flags` (FEAT-002).
 *
 * Azúcar tipado sobre `applyEditOp`: envía solo las banderas indicadas. `locked` protege la
 * edición, `hidden` oculta una pista VISUAL en preview/export y `muted` silencia AUDIO.
 */
export function setTrackFlags(
  projectId: number,
  trackId: string,
  flags: { locked?: boolean; hidden?: boolean; muted?: boolean },
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "set_track_flags", {
    track_id: trackId,
    ...flags,
  });
}

/**
 * Elimina un clip cerrando el hueco en su pista (ripple delete, operación `ripple_delete`).
 *
 * Desplaza a la izquierda los clips posteriores de la MISMA pista; no toca otras pistas y
 * rechaza (422) si la pista está bloqueada.
 */
export function rippleDeleteClip(
  projectId: number,
  clipId: string,
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "ripple_delete", { clip_id: clipId });
}

/**
 * Añade un marcador a la línea de tiempo (operación `add_marker`, FEAT-003).
 *
 * Los marcadores son metadatos (no afectan al render): el frontend los pinta en la regla
 * y el snap los usa como puntos de alineación. `t_s` es el instante (>= 0); `label`/`color`
 * son opcionales.
 */
export function addMarker(
  projectId: number,
  t_s: number,
  opts: { label?: string; color?: string } = {},
  sequence?: string,
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "add_marker", { t_s, ...opts }, sequence);
}

/** Edita un marcador existente (operación `set_marker`, FEAT-003). */
export function setMarker(
  projectId: number,
  markerId: string,
  fields: { t_s?: number; label?: string; color?: string },
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "set_marker", { marker_id: markerId, ...fields });
}

/** Elimina un marcador por id (operación `remove_marker`, FEAT-003). */
export function removeMarker(
  projectId: number,
  markerId: string,
  sequence?: string,
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "remove_marker", { marker_id: markerId }, sequence);
}

/**
 * Elimina VARIOS clips de forma atómica (operación `batch_delete`, FEAT-004).
 *
 * Todo o nada: si algún clip no existe o su pista está `locked`, falla entera (422) sin
 * mutar. Como una sola op, el gesto produce UNA Version (undo lo revierte de golpe).
 */
export function batchDeleteClips(
  projectId: number,
  clipIds: string[],
  sequence?: string,
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "batch_delete", { clip_ids: clipIds }, sequence);
}

/**
 * Desplaza VARIOS clips por el mismo `delta_s` (operación `batch_move`, FEAT-004).
 *
 * Conserva los offsets relativos; valida start_s >= 0 resultante y pistas no bloqueadas.
 * Atómica: una op = una Version. El `delta_s` debe venir ya ajustado por el snap si procede.
 */
export function batchMoveClips(
  projectId: number,
  clipIds: string[],
  deltaS: number,
  sequence?: string,
): Promise<EditProjectRead> {
  return applyEditOp(
    projectId,
    "batch_move",
    { clip_ids: clipIds, delta_s: deltaS },
    sequence,
  );
}

/** Atributos copiables por `copyAttributes` (espejo de `_COPYABLE_ATTRS` del backend). */
export type CopyableAttr =
  | "transform"
  | "gain"
  | "fade"
  | "style"
  | "title"
  | "overlay"
  | "transition"
  | "speed";

/**
 * Copia atributos de un clip a otros (operación `copy_attributes`, FEAT-004).
 *
 * Copia solo los atributos compatibles con el TIPO de cada destino (los no aplicables se
 * ignoran); nunca copia id/start_s/duration_s/asset_id. Atómica: una op = una Version.
 */
export function copyAttributes(
  projectId: number,
  sourceClipId: string,
  targetClipIds: string[],
  attrs: CopyableAttr[],
  sequence?: string,
): Promise<EditProjectRead> {
  return applyEditOp(
    projectId,
    "copy_attributes",
    {
      source_clip_id: sourceClipId,
      target_clip_ids: targetClipIds,
      attrs,
    },
    sequence,
  );
}

/**
 * Desvincula el audio de un clip de vídeo (operación `detach_audio`, FEAT-005).
 *
 * Crea un clip de audio equivalente en una pista `voice` (creándola si falta) y marca el
 * clip de vídeo para no reproducir su audio de origen. Rechaza (422) clips no-vídeo. Si se
 * indica `targetTrackId`, debe ser una pista `voice` existente.
 */
export function detachAudio(
  projectId: number,
  clipId: string,
  targetTrackId?: string,
): Promise<EditProjectRead> {
  const params: Record<string, unknown> = { clip_id: clipId };
  if (targetTrackId) params.target_track_id = targetTrackId;
  return applyEditOp(projectId, "detach_audio", params);
}

/**
 * Fija la velocidad de un clip (operación `set_speed`, FEAT-006).
 *
 * `rate > 0` (1.0 = normal; recalcula `duration_s`); `reverse` solo en vídeo (el reverse de
 * VÍDEO se aplica en export, en preview se muestra un indicador). `rate=1.0` sin `reverse`
 * vuelve al estado neutro (sin cambios de compilación).
 */
export function setClipSpeed(
  projectId: number,
  clipId: string,
  opts: { rate?: number; reverse?: boolean },
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "set_speed", { clip_id: clipId, ...opts });
}

/**
 * Congela un fotograma de un clip de vídeo (operación `set_freeze`, FEAT-006).
 *
 * `atS` es el instante del origen a congelar (dentro de su rango) y `holdS` cuánto se
 * mantiene. `holdS=0` limpia la congelación. Rechaza (422) clips de imagen.
 */
export function setClipFreeze(
  projectId: number,
  clipId: string,
  atS: number,
  holdS: number,
): Promise<EditProjectRead> {
  return applyEditOp(projectId, "set_freeze", { clip_id: clipId, at_s: atS, hold_s: holdS });
}

/**
 * Coloca un Asset subido como clip nuevo en una pista (operación `add_media_clip`, FEAT-007).
 *
 * Es el segundo paso del drag&drop de importación: tras `uploadEditMedia`, coloca el clip
 * del `kind` correcto en el `start_s` donde se soltó (ya ajustado por el snap). Para
 * audio/vídeo, `sourceDurationS` fija `source_out_s` (si se omite, usa `durationS`).
 */
export function addMediaClip(
  projectId: number,
  params: {
    trackId: string;
    assetId: number;
    startS: number;
    durationS: number;
    kind: TrackKind;
    sourceDurationS?: number;
  },
  sequence?: string,
): Promise<EditProjectRead> {
  const body: Record<string, unknown> = {
    track_id: params.trackId,
    asset_id: params.assetId,
    start_s: params.startS,
    duration_s: params.durationS,
    kind: params.kind,
  };
  if (params.sourceDurationS !== undefined) {
    body.source_duration_s = params.sourceDurationS;
  }
  return applyEditOp(projectId, "add_media_clip", body, sequence);
}

/** Deshace la última edición (POST .../editor/undo). 409 si no hay nada que deshacer. */
export function undoEdit(
  projectId: number,
  sequence?: string,
): Promise<EditProjectRead> {
  return request<EditProjectRead>(
    `/projects/${projectId}/editor/undo${sequenceQuery(sequence)}`,
    { method: "POST" },
  );
}

/** Rehace la edición deshecha (POST .../editor/redo). 409 si no hay nada que rehacer. */
export function redoEdit(
  projectId: number,
  sequence?: string,
): Promise<EditProjectRead> {
  return request<EditProjectRead>(
    `/projects/${projectId}/editor/redo${sequenceQuery(sequence)}`,
    { method: "POST" },
  );
}

/** Lista las versiones del historial de edición (GET .../editor/history). */
export function getEditHistory(
  projectId: number,
  sequence?: string,
): Promise<EditHistoryEntry[]> {
  return request<EditHistoryEntry[]>(
    `/projects/${projectId}/editor/history${sequenceQuery(sequence)}`,
  );
}

/** Lista las secuencias del proyecto (GET .../editor/sequences, FEAT-008). */
export function listSequences(projectId: number): Promise<EditSequence[]> {
  return request<EditSequence[]>(`/projects/${projectId}/editor/sequences`);
}

/**
 * Crea una secuencia nueva (POST .../editor/sequences, FEAT-008).
 *
 * `from` = "active" duplica la secuencia activa (`baseSequence`, default main) como punto
 * de partida; "timeline" la deriva del Timeline de generación (como `open`). Devuelve el
 * EditProject inicial de la secuencia. 409 si ya existe; 422 si falta `sequenceId`.
 */
export function createSequence(
  projectId: number,
  sequenceId: string,
  opts: { name?: string; from?: "active" | "timeline"; baseSequence?: string } = {},
): Promise<EditProjectRead> {
  const body: Record<string, unknown> = { sequence_id: sequenceId };
  if (opts.name !== undefined) body.name = opts.name;
  if (opts.from !== undefined) body.from = opts.from;
  if (opts.baseSequence !== undefined) body.base_sequence = opts.baseSequence;
  return request<EditProjectRead>(`/projects/${projectId}/editor/sequences`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Encola el job edit_export que re-renderiza desde el EditProject (POST .../editor/export). */
export function enqueueEditExport(
  projectId: number,
  sequence?: string,
): Promise<EditExportResponse> {
  return request<EditExportResponse>(
    `/projects/${projectId}/editor/export${sequenceQuery(sequence)}`,
    { method: "POST" },
  );
}

/** Estado del último export de edición del proyecto (GET .../editor/export, para polling). */
export function latestEditExport(projectId: number): Promise<Job> {
  return request<Job>(`/projects/${projectId}/editor/export`);
}

// --------------------------------------------------------------------------- //
// Import/export de subtítulos (/projects/{id}/editor/subtitles) — FEAT-003
// --------------------------------------------------------------------------- //

export type SubtitleFormat = "srt" | "vtt" | "ass";
export type SubtitleImportMode = "replace" | "append";

/**
 * Descarga la pista `subtitle` del EditProject activo en el formato pedido.
 *
 * Llama a `GET .../editor/subtitles/export?format=...`, recibe el fichero
 * (SRT/VTT/ASS) como texto y dispara una descarga en el navegador con el nombre
 * `subtitles.<ext>`. El contenido refleja los tiempos/texto editados en el editor.
 */
export async function exportSubtitles(
  projectId: number,
  format: SubtitleFormat,
): Promise<void> {
  const url = `${API_BASE_URL}/projects/${projectId}/editor/subtitles/export?format=${format}`;
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Error de red";
    throw new ApiError(
      0,
      `No se pudo contactar con el backend (${API_BASE_URL}): ${message}`,
    );
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // cuerpo no-JSON
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  // Descarga en el navegador (no en SSR: se invoca desde handlers de cliente).
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = `subtitles.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}

/**
 * Importa un fichero `.srt/.vtt/.ass` y reemplaza/añade la pista `subtitle`.
 *
 * Envía `multipart/form-data` a `POST .../editor/subtitles/import?mode=...`. El backend
 * valida formato, parsea a cues, actualiza la pista, crea una Version (undo/redo) y
 * guarda el fichero como Asset(kind='srt'). Devuelve el EditProject actualizado.
 */
export async function importSubtitles(
  projectId: number,
  file: File,
  mode: SubtitleImportMode = "replace",
): Promise<EditProjectRead> {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch(
      `${API_BASE_URL}/projects/${projectId}/editor/subtitles/import?mode=${mode}`,
      { method: "POST", cache: "no-store", body: form },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Error de red";
    throw new ApiError(
      0,
      `No se pudo contactar con el backend (${API_BASE_URL}): ${message}`,
    );
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // cuerpo no-JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as EditProjectRead;
}

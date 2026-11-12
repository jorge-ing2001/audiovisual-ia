// Cliente REST tipado hacia el backend FastAPI de audiovisual-ia.
//
// La URL base es configurable por entorno (NEXT_PUBLIC_API_URL). TODO el frontend
// consume estos helpers: cada acción de la UI dispara una llamada real a la API
// (sin datos simulados). Las formas de request/response replican los esquemas
// Pydantic de backend/app/models/schemas.py.

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
  created_at: string;
}

export interface ProjectCreate {
  name: string;
  prompt?: string;
  production_type?: string;
  aspect_ratio?: string;
  resolution?: string;
  provider_overrides?: Record<string, unknown> | null;
}

export interface ProjectUpdate {
  name?: string;
  prompt?: string;
  production_type?: string;
  aspect_ratio?: string;
  resolution?: string;
  status?: string;
  provider_overrides?: Record<string, unknown> | null;
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
// Proyectos (/projects)
// --------------------------------------------------------------------------- //

export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/projects");
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

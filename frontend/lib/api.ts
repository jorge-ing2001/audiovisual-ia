// Cliente REST mínimo hacia el backend FastAPI.
// La URL base es configurable por entorno (NEXT_PUBLIC_API_URL).

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface HealthResponse {
  status: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Health check falló: HTTP ${res.status}`);
  }
  return (await res.json()) as HealthResponse;
}

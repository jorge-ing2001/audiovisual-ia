"use client";

import { useEffect, useState } from "react";

import { API_BASE_URL, fetchHealth } from "@/lib/api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; status: string }
  | { kind: "error"; message: string };

export default function HealthStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((data) => {
        if (!cancelled) setState({ kind: "ok", status: data.status });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : "Error desconocido";
          setState({ kind: "error", message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
      <h2 className="mb-2 text-sm font-medium text-neutral-400">
        Estado del backend
      </h2>
      {state.kind === "loading" && (
        <p className="text-neutral-300">Comprobando {API_BASE_URL}…</p>
      )}
      {state.kind === "ok" && (
        <p className="text-green-400">
          Backend conectado — status: <span className="font-mono">{state.status}</span>
        </p>
      )}
      {state.kind === "error" && (
        <p className="text-red-400">
          No se pudo conectar con el backend ({API_BASE_URL}): {state.message}
        </p>
      )}
    </div>
  );
}

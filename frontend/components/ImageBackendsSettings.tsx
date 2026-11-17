"use client";

import { useEffect, useState } from "react";

import {
  API_BASE_URL,
  fetchImageBackends,
  type ImageBackendsSettings as Settings,
} from "@/lib/api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: Settings }
  | { kind: "error"; message: string };

/**
 * Panel de SOLO LECTURA del backend de imágenes (FEAT-007, Bloque 3).
 *
 * Lee los datos REALES del backend (`GET /settings/image-backends`): no hay nada
 * hardcodeado. Muestra el backend activo, la lista de disponibles (incluido
 * `sd_local` cuando ComfyUI está configurado) y los flags locales de Stable Diffusion.
 * La selección global se controla por variable de entorno (`AIV_IMAGE_BACKEND`); esta
 * vista informa, no cambia el estado (no hay selector decorativo sin efecto).
 */
export default function ImageBackendsSettings() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchImageBackends()
      .then((data) => {
        if (!cancelled) setState({ kind: "ok", data });
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
    <section className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
      <h2 className="mb-2 text-sm font-medium text-neutral-400">
        Backend de imágenes
      </h2>

      {state.kind === "loading" && (
        <p className="text-neutral-300">Cargando ajustes desde {API_BASE_URL}…</p>
      )}

      {state.kind === "error" && (
        <p className="text-red-400">
          No se pudieron leer los ajustes ({API_BASE_URL}): {state.message}
        </p>
      )}

      {state.kind === "ok" && (
        <div className="flex flex-col gap-3">
          <p className="text-neutral-300">
            Activo:{" "}
            <span className="rounded bg-neutral-800 px-2 py-0.5 font-mono text-green-400">
              {state.data.active}
            </span>
          </p>

          <div>
            <p className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
              Disponibles
            </p>
            <ul className="flex flex-wrap gap-2">
              {state.data.available.map((name) => (
                <li
                  key={name}
                  className={`rounded px-2 py-0.5 font-mono text-xs ${
                    name === state.data.active
                      ? "bg-green-900/40 text-green-300"
                      : "bg-neutral-800 text-neutral-300"
                  }`}
                >
                  {name}
                </li>
              ))}
            </ul>
          </div>

          <dl className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2">
            <dt className="text-neutral-500">SD local habilitado</dt>
            <dd className="font-mono text-neutral-200">
              {state.data.flags.sd_local_enabled ? "sí" : "no"}
            </dd>
            <dt className="text-neutral-500">URL de ComfyUI</dt>
            <dd className="font-mono text-neutral-200">
              {state.data.flags.sd_local_url}
            </dd>
            <dt className="text-neutral-500">Concurrencia máx. SD</dt>
            <dd className="font-mono text-neutral-200">
              {state.data.flags.sd_max_concurrency}
            </dd>
          </dl>

          <p className="text-xs text-neutral-500">
            La selección global se configura por variable de entorno
            (<span className="font-mono">AIV_IMAGE_BACKEND</span>); por proyecto, con{" "}
            <span className="font-mono">provider_overrides</span>.
          </p>
        </div>
      )}
    </section>
  );
}

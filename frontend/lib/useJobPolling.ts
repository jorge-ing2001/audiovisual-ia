"use client";

// Hook de polling de jobs del backend (GET /jobs/{id}).
// Consulta el estado cada `intervalMs` mientras el job está en curso y se detiene
// al alcanzar un estado terminal (succeeded/failed). Refleja progreso y error reales.

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getJob, type Job } from "@/lib/api";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export interface JobPollingState {
  job: Job | null;
  error: string | null;
  polling: boolean;
}

export function isTerminal(status: string | undefined): boolean {
  return status !== undefined && TERMINAL.has(status);
}

export function useJobPolling(
  jobId: number | null,
  intervalMs = 1500,
): JobPollingState & { refresh: () => void } {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(
    async (id: number) => {
      try {
        const next = await getJob(id);
        setJob(next);
        setError(null);
        if (isTerminal(next.status)) {
          setPolling(false);
          return;
        }
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "No se pudo consultar el job";
        setError(message);
      }
      timer.current = setTimeout(() => void poll(id), intervalMs);
    },
    [intervalMs],
  );

  useEffect(() => {
    if (jobId === null) {
      setJob(null);
      setError(null);
      setPolling(false);
      return;
    }
    setPolling(true);
    void poll(jobId);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [jobId, poll]);

  const refresh = useCallback(() => {
    if (jobId !== null) void poll(jobId);
  }, [jobId, poll]);

  return { job, error, polling, refresh };
}

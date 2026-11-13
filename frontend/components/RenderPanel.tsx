"use client";

// Panel de render: encola un render (POST /projects/{id}/render), consulta el último
// estado (GET /projects/{id}/render) y permite previsualizar/descargar el MP4
// (GET /projects/{id}/video). Toda acción es una llamada real a la API.

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiError,
  enqueueRender,
  latestRender,
  videoDownloadUrl,
  type Job,
} from "@/lib/api";
import { useJobPolling } from "@/lib/useJobPolling";
import { Button, Card, ErrorBanner } from "@/components/ui";
import JobProgress from "@/components/JobProgress";

export default function RenderPanel({ projectId }: { projectId: number }) {
  const [jobId, setJobId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastDone, setLastDone] = useState<Job | null>(null);

  const { job, error: pollError } = useJobPolling(jobId);

  // Al montar, intenta recuperar el último render del proyecto (si lo hay).
  useEffect(() => {
    let cancelled = false;
    latestRender(projectId)
      .then((j) => {
        if (cancelled) return;
        if (j.status === "succeeded") setLastDone(j);
        else setJobId(j.id);
      })
      .catch((err) => {
        // 404 significa "sin render todavía": no es un error de UI.
        if (!cancelled && err instanceof ApiError && err.status !== 404) {
          setError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Cuando el job en curso termina con éxito, lo fijamos como "último render listo".
  useEffect(() => {
    if (job && job.status === "succeeded") setLastDone(job);
  }, [job]);

  async function handleRender() {
    setStarting(true);
    setError(null);
    setLastDone(null);
    try {
      const created = await enqueueRender(projectId);
      setJobId(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo encolar el render");
    } finally {
      setStarting(false);
    }
  }

  const videoUrl = videoDownloadUrl(projectId);
  const videoReady = lastDone?.status === "succeeded";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-neutral-100">Render</h2>
        <Button onClick={() => void handleRender()} loading={starting}>
          Renderizar vídeo
        </Button>
      </div>

      {error && <ErrorBanner message={error} />}

      <JobProgress job={job} title="Progreso del render" pollingError={pollError} />

      {videoReady && (
        <Card>
          <h3 className="mb-3 font-medium text-neutral-100">Vídeo final</h3>
          <video
            controls
            src={videoUrl}
            className="aspect-video w-full rounded-lg border border-neutral-800 bg-black"
          />
          <div className="mt-3 flex gap-3">
            <a href={videoUrl} download>
              <Button variant="secondary">Descargar MP4</Button>
            </a>
            <Link href={`/projects/${projectId}/editor`}>
              <Button>Editar video</Button>
            </Link>
          </div>
        </Card>
      )}
    </div>
  );
}

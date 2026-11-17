"use client";

// Panel de render: encola un render (POST /projects/{id}/render), consulta el último
// estado (GET /projects/{id}/render) y permite previsualizar/descargar el MP4
// (GET /projects/{id}/video). Toda acción es una llamada real a la API.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  enqueueRender,
  fetchRenderQA,
  latestRender,
  videoDownloadUrl,
  type Job,
  type QACheck,
  type QAOverall,
  type RenderQAResult,
} from "@/lib/api";
import { useJobPolling } from "@/lib/useJobPolling";
import { Button, Card, ErrorBanner, StatusBadge } from "@/components/ui";
import JobProgress from "@/components/JobProgress";

type BadgeTone = "idle" | "progress" | "success" | "error";

/**
 * Mapea el `overall` del QA report a tono + etiqueta del badge global.
 *
 * `pass_with_skips` tiene tono y texto PROPIOS ("Aprobado con omisiones", tono de
 * progreso/azul) distintos de `pass` ("Aprobado", verde): un reporte incompleto nunca se
 * pinta como un aprobado limpio (§2.3).
 */
function overallBadge(overall: QAOverall): { tone: BadgeTone; label: string } {
  switch (overall) {
    case "pass":
      return { tone: "success", label: "Aprobado" };
    case "warn":
      return { tone: "idle", label: "Con advertencias" };
    case "fail":
      return { tone: "error", label: "Con fallos" };
    case "pass_with_skips":
      return { tone: "progress", label: "Aprobado con omisiones" };
    default:
      return { tone: "idle", label: overall };
  }
}

/** Mapea el estado de un check a tono + símbolo + etiqueta corta de estado. */
function checkStatusMeta(status: QACheck["status"]): {
  tone: BadgeTone;
  symbol: string;
  label: string;
} {
  switch (status) {
    case "pass":
      return { tone: "success", symbol: "✓", label: "Correcto" };
    case "warn":
      return { tone: "idle", symbol: "!", label: "Advertencia" };
    case "fail":
      return { tone: "error", symbol: "✕", label: "Fallo" };
    case "skipped":
      return { tone: "progress", symbol: "–", label: "Omitido" };
    default:
      return { tone: "idle", symbol: "?", label: status };
  }
}

/** Card 'Control de calidad' con badge global por `overall` y la lista de checks. */
function QAReportCard({ result }: { result: RenderQAResult }) {
  if (result.status === "no_report") {
    return (
      <Card>
        <h3 className="mb-2 font-medium text-neutral-100">Control de calidad</h3>
        <p className="text-sm text-neutral-400">{result.detail}</p>
      </Card>
    );
  }

  const { report, stale } = result;
  const badge = overallBadge(report.overall);

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="font-medium text-neutral-100">Control de calidad</h3>
        <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
      </div>

      {stale && (
        <p className="mb-3 text-xs text-amber-400">
          El vídeo cambió desde que se evaluó: el reporte puede estar obsoleto. Vuelve a
          renderizar para refrescarlo.
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {report.checks.map((check) => {
          const meta = checkStatusMeta(check.status);
          return (
            <li
              key={check.id}
              className="flex items-start gap-3 rounded-md border border-neutral-800 bg-neutral-950/40 px-3 py-2"
            >
              <span className="mt-0.5 shrink-0">
                <StatusBadge tone={meta.tone}>
                  <span aria-hidden="true">{meta.symbol}</span>
                  {meta.label}
                </StatusBadge>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-neutral-200">
                  {check.label}
                </span>
                <span className="block text-xs text-neutral-400">{check.detail}</span>
                {check.status === "skipped" && check.skip_reason && (
                  <span className="mt-0.5 block text-xs text-neutral-500">
                    Motivo: {check.skip_reason}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export default function RenderPanel({ projectId }: { projectId: number }) {
  const [jobId, setJobId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastDone, setLastDone] = useState<Job | null>(null);
  const [qa, setQa] = useState<RenderQAResult | null>(null);

  const { job, error: pollError } = useJobPolling(jobId);

  // Carga el QA report del último render (GET .../render/qa). 404 (sin render) => neutro.
  const loadQA = useCallback(() => {
    fetchRenderQA(projectId)
      .then((result) => setQa(result))
      .catch(() => {
        // Un fallo al leer el QA no debe romper el panel de render; se omite el Card.
        setQa(null);
      });
  }, [projectId]);

  // Al montar, intenta recuperar el último render del proyecto (si lo hay).
  useEffect(() => {
    let cancelled = false;
    latestRender(projectId)
      .then((j) => {
        if (cancelled) return;
        if (j.status === "succeeded") {
          setLastDone(j);
          loadQA();
        } else setJobId(j.id);
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
  }, [projectId, loadQA]);

  // Cuando el job en curso termina con éxito, lo fijamos como "último render listo"
  // y refrescamos el QA report asociado a ese render.
  useEffect(() => {
    if (job && job.status === "succeeded") {
      setLastDone(job);
      loadQA();
    }
  }, [job, loadQA]);

  async function handleRender() {
    setStarting(true);
    setError(null);
    setLastDone(null);
    setQa(null);
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

      {videoReady && qa && <QAReportCard result={qa} />}
    </div>
  );
}

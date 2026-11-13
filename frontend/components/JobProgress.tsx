"use client";

// Panel de progreso de un Job del backend: barra de progreso global y desglose por
// paso (Job.result.steps / Job.result.current). Refleja el estado real del polling.

import {
  Card,
  ErrorBanner,
  ProgressBar,
  Spinner,
  StatusBadge,
  statusTone,
} from "@/components/ui";
import type { Job } from "@/lib/api";

// Orden canónico de los pasos del pipeline (backend/app/jobs/pipeline.py).
const STEP_LABELS: Record<string, string> = {
  guion: "Guion",
  escenas: "Escenas (imagen + voz)",
  musica_sfx: "Música / SFX",
  subtitulos: "Subtítulos",
  timeline: "Timeline",
  render: "Render (MP4)",
};
const STEP_ORDER = Object.keys(STEP_LABELS);

export default function JobProgress({
  job,
  title,
  pollingError,
}: {
  job: Job | null;
  title: string;
  pollingError?: string | null;
}) {
  if (!job && !pollingError) return null;

  const doneSteps = new Set((job?.result?.steps ?? []).map((s) => s.step));
  const current = job?.result?.current ?? null;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-medium text-neutral-100">{title}</h3>
        {job && (
          <StatusBadge tone={statusTone(job.status)}>
            {job.status === "running" && <Spinner className="h-3 w-3" />}
            {job.status}
          </StatusBadge>
        )}
      </div>

      {job && (
        <>
          <ProgressBar value={job.progress} />
          <p className="mt-1 text-right text-xs text-neutral-500">
            {Math.round(job.progress * 100)}%
          </p>

          <ul className="mt-3 grid gap-1.5">
            {STEP_ORDER.map((step) => {
              const isDone = doneSteps.has(step);
              const isCurrent = current?.step === step;
              const detail = isCurrent ? current?.detail : undefined;
              return (
                <li
                  key={step}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span className="flex items-center gap-2">
                    <StepIcon done={isDone} current={isCurrent} />
                    <span
                      className={
                        isDone
                          ? "text-neutral-300"
                          : isCurrent
                            ? "text-neutral-100"
                            : "text-neutral-600"
                      }
                    >
                      {STEP_LABELS[step]}
                    </span>
                  </span>
                  {detail && (
                    <span className="truncate text-xs text-neutral-500">{detail}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}

      {job?.error && (
        <div className="mt-3">
          <ErrorBanner message={job.error} />
        </div>
      )}
      {pollingError && (
        <div className="mt-3">
          <ErrorBanner message={pollingError} />
        </div>
      )}
    </Card>
  );
}

function StepIcon({ done, current }: { done: boolean; current: boolean }) {
  if (done)
    return (
      <span className="flex h-4 w-4 items-center justify-center rounded-full bg-green-500/20 text-[10px] text-green-400">
        ✓
      </span>
    );
  if (current) return <Spinner className="h-4 w-4 text-blue-400" />;
  return <span className="h-4 w-4 rounded-full border border-neutral-700" />;
}

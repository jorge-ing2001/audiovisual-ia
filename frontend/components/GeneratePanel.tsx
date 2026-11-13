"use client";

// Editor de prompt + generación end-to-end. Permite editar prompt/aspect/resolución
// (PATCH /projects/{id}), dispara el pipeline (POST /projects/{id}/generate) y hace
// polling del job (GET /jobs/{id}) mostrando progreso por paso. Al terminar, recarga
// el storyboard. Cada botón es una llamada real a la API.

import { useEffect, useState } from "react";

import {
  ApiError,
  generateVideo,
  updateProject,
  type Project,
} from "@/lib/api";
import { useJobPolling, isTerminal } from "@/lib/useJobPolling";
import {
  Button,
  Card,
  ErrorBanner,
  Field,
  Select,
  TextArea,
} from "@/components/ui";
import JobProgress from "@/components/JobProgress";

const ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:5"];
const RESOLUTIONS = ["720p", "1080p", "1440p"];

export default function GeneratePanel({
  project,
  onProjectUpdated,
  onGenerationFinished,
}: {
  project: Project;
  onProjectUpdated: (project: Project) => void;
  onGenerationFinished: () => void;
}) {
  const [prompt, setPrompt] = useState(project.prompt);
  const [aspect, setAspect] = useState(project.aspect_ratio);
  const [resolution, setResolution] = useState(project.resolution);
  const [style, setStyle] = useState("");

  const [savingMeta, setSavingMeta] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);

  const { job, error: pollError } = useJobPolling(jobId);

  const metaDirty =
    aspect !== project.aspect_ratio || resolution !== project.resolution;
  const promptDirty = prompt !== project.prompt;

  // Al terminar la generación con éxito, recarga storyboard una sola vez.
  useEffect(() => {
    if (job && isTerminal(job.status)) {
      setGenerating(false);
      if (job.status === "succeeded") onGenerationFinished();
    }
  }, [job, onGenerationFinished]);

  async function persistMeta() {
    if (!metaDirty) return;
    setSavingMeta(true);
    setError(null);
    try {
      const updated = await updateProject(project.id, {
        aspect_ratio: aspect,
        resolution,
      });
      onProjectUpdated(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar el proyecto");
    } finally {
      setSavingMeta(false);
    }
  }

  async function handleGenerate() {
    if (!prompt.trim()) {
      setError("Escribe un prompt antes de generar.");
      return;
    }
    setGenerating(true);
    setError(null);
    try {
      // Persistimos aspect/resolución si cambiaron; el prompt viaja en /generate.
      if (metaDirty) {
        const updated = await updateProject(project.id, {
          aspect_ratio: aspect,
          resolution,
        });
        onProjectUpdated(updated);
      }
      const res = await generateVideo(project.id, {
        prompt: prompt.trim(),
        style: style.trim() || undefined,
      });
      setJobId(res.job_id);
    } catch (err) {
      setGenerating(false);
      setError(err instanceof ApiError ? err.message : "No se pudo iniciar la generación");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <h2 className="mb-4 text-lg font-semibold text-neutral-100">Prompt y generación</h2>
        <div className="flex flex-col gap-4">
          <Field
            label="Prompt"
            htmlFor="prompt"
            hint="Describe el vídeo. La IA (Ollama local del usuario) generará el guion y las escenas."
          >
            <TextArea
              id="prompt"
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onBlur={() => {
                if (promptDirty) {
                  void updateProject(project.id, { prompt })
                    .then(onProjectUpdated)
                    .catch(() => undefined);
                }
              }}
              placeholder="Un breve documental sobre el ciclo del agua, tono divulgativo…"
            />
          </Field>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Field label="Aspect ratio" htmlFor="aspect">
              <Select
                id="aspect"
                value={aspect}
                onChange={(e) => setAspect(e.target.value)}
                onBlur={() => void persistMeta()}
              >
                {ASPECT_RATIOS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Resolución" htmlFor="resolution">
              <Select
                id="resolution"
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
                onBlur={() => void persistMeta()}
              >
                {RESOLUTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Estilo visual (opcional)" htmlFor="style">
              <TextArea
                id="style"
                rows={1}
                value={style}
                onChange={(e) => setStyle(e.target.value)}
                placeholder="acuarela, cine…"
              />
            </Field>
          </div>

          {error && <ErrorBanner message={error} />}

          <div className="flex items-center gap-3">
            <Button
              onClick={() => void handleGenerate()}
              loading={generating}
              disabled={!prompt.trim()}
            >
              Generar vídeo
            </Button>
            {savingMeta && <span className="text-xs text-neutral-500">Guardando…</span>}
          </div>
        </div>
      </Card>

      <JobProgress job={job} title="Progreso de generación" pollingError={pollError} />
    </div>
  );
}

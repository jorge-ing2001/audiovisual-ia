"use client";

// Dashboard: lista los proyectos (GET /projects) y permite crear uno nuevo
// (POST /projects). Cada acción es una llamada real a la API.

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiError,
  createProject,
  listProjects,
  type Project,
} from "@/lib/api";
import {
  Button,
  Card,
  ErrorBanner,
  Field,
  Select,
  Spinner,
  StatusBadge,
  TextArea,
  TextInput,
  statusTone,
} from "@/components/ui";

const ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:5"];
const RESOLUTIONS = ["720p", "1080p", "1440p"];

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [aspect, setAspect] = useState(ASPECT_RATIOS[0]);
  const [resolution, setResolution] = useState(RESOLUTIONS[1]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function refresh() {
    try {
      setLoadError(null);
      setProjects(await listProjects());
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Error cargando proyectos");
      setProjects([]);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const project = await createProject({
        name: name.trim(),
        prompt: prompt.trim(),
        aspect_ratio: aspect,
        resolution,
      });
      setName("");
      setPrompt("");
      setProjects((prev) => (prev ? [...prev, project] : [project]));
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "No se pudo crear el proyecto");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_380px]">
      <section className="order-2 lg:order-1">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-neutral-100">Proyectos</h2>
          <Button variant="ghost" onClick={() => void refresh()}>
            Actualizar
          </Button>
        </div>

        {loadError && <ErrorBanner message={loadError} />}

        {projects === null && (
          <div className="flex items-center gap-2 text-neutral-400">
            <Spinner /> Cargando proyectos…
          </div>
        )}

        {projects !== null && projects.length === 0 && !loadError && (
          <Card className="text-sm text-neutral-400">
            Aún no hay proyectos. Crea el primero con el formulario.
          </Card>
        )}

        <ul className="grid gap-3">
          {projects?.map((p) => (
            <li key={p.id}>
              <Link href={`/projects/${p.id}`} className="block">
                <Card className="transition-colors hover:border-neutral-600">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h3 className="truncate font-medium text-neutral-100">{p.name}</h3>
                      <p className="mt-1 line-clamp-2 text-sm text-neutral-400">
                        {p.prompt || "Sin prompt todavía"}
                      </p>
                    </div>
                    <StatusBadge tone={statusTone(p.status)}>{p.status}</StatusBadge>
                  </div>
                  <div className="mt-3 flex gap-3 text-xs text-neutral-500">
                    <span>#{p.id}</span>
                    <span>{p.aspect_ratio}</span>
                    <span>{p.resolution}</span>
                    <span>{p.production_type}</span>
                  </div>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="order-1 lg:order-2">
        <Card>
          <h2 className="mb-4 text-lg font-semibold text-neutral-100">Nuevo proyecto</h2>
          <form onSubmit={handleCreate} className="flex flex-col gap-4">
            <Field label="Nombre" htmlFor="project-name">
              <TextInput
                id="project-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Mi vídeo corto"
                required
              />
            </Field>
            <Field
              label="Prompt (opcional)"
              htmlFor="project-prompt"
              hint="Puedes dejarlo vacío y editarlo en el proyecto."
            >
              <TextArea
                id="project-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={3}
                placeholder="Describe el vídeo que quieres generar…"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Aspect ratio" htmlFor="project-aspect">
                <Select
                  id="project-aspect"
                  value={aspect}
                  onChange={(e) => setAspect(e.target.value)}
                >
                  {ASPECT_RATIOS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Resolución" htmlFor="project-resolution">
                <Select
                  id="project-resolution"
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                >
                  {RESOLUTIONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
            {createError && <ErrorBanner message={createError} />}
            <Button type="submit" loading={creating} disabled={!name.trim()}>
              Crear proyecto
            </Button>
          </form>
        </Card>
      </section>
    </div>
  );
}

"use client";

// Dashboard: lista los proyectos (GET /projects) y permite crear uno nuevo
// (POST /projects). Añade administración por proyecto (FEAT-003): renombrar, duplicar
// (con polling si el backend devuelve un Job), borrar (con confirmación) y editar
// tags/carpeta, más una barra de búsqueda/filtrado (q + tag + folder) con debounce.
// Cada acción es una llamada REST real; no hay estado simulado.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createProject,
  deleteProject,
  duplicateProject,
  isJob,
  listFolders,
  listProjects,
  listTags,
  updateProject,
  type Job,
  type Project,
} from "@/lib/api";
import { isTerminal, useJobPolling } from "@/lib/useJobPolling";
import {
  Button,
  Card,
  ErrorBanner,
  Field,
  Modal,
  Select,
  Spinner,
  StatusBadge,
  TextArea,
  TextInput,
  statusTone,
} from "@/components/ui";

const ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:5"];
const RESOLUTIONS = ["720p", "1080p", "1440p"];

// Debounce del buscador: evita recargar la lista en cada pulsación.
const SEARCH_DEBOUNCE_MS = 350;

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Barra de búsqueda / filtros. `q` tiene debounce; tag/folder recargan al instante.
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [folder, setFolder] = useState("");
  const [tagOptions, setTagOptions] = useState<string[]>([]);
  const [folderOptions, setFolderOptions] = useState<string[]>([]);

  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [aspect, setAspect] = useState(ASPECT_RATIOS[0]);
  const [resolution, setResolution] = useState(RESOLUTIONS[1]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Job de duplicación en curso (proyectos grandes). Al terminar, refresca la lista.
  const [dupJobId, setDupJobId] = useState<number | null>(null);
  const dupPoll = useJobPolling(dupJobId);

  const refresh = useCallback(
    async (filters: { q?: string; tag?: string; folder?: string } = {}) => {
      try {
        setLoadError(null);
        setProjects(await listProjects(filters));
      } catch (err) {
        setLoadError(err instanceof ApiError ? err.message : "Error cargando proyectos");
        setProjects([]);
      }
    },
    [],
  );

  const reloadFilters = useCallback(async () => {
    try {
      const [tags, folders] = await Promise.all([listTags(), listFolders()]);
      setTagOptions(tags);
      setFolderOptions(folders);
    } catch {
      // Los filtros son auxiliares: si fallan, la lista sigue operando con los inputs.
    }
  }, []);

  // Carga inicial + recarga cuando cambian los filtros (q con debounce).
  useEffect(() => {
    const handle = setTimeout(() => {
      void refresh({
        q: q.trim() || undefined,
        tag: tag || undefined,
        folder: folder || undefined,
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [q, tag, folder, refresh]);

  useEffect(() => {
    void reloadFilters();
  }, [reloadFilters]);

  // Al terminar el job de duplicación, refresca lista + filtros y limpia el estado.
  const refreshAll = useCallback(() => {
    void refresh({
      q: q.trim() || undefined,
      tag: tag || undefined,
      folder: folder || undefined,
    });
    void reloadFilters();
  }, [refresh, reloadFilters, q, tag, folder]);

  useEffect(() => {
    if (dupJobId !== null && dupPoll.job && isTerminal(dupPoll.job.status)) {
      setDupJobId(null);
      refreshAll();
    }
  }, [dupJobId, dupPoll.job, refreshAll]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      await createProject({
        name: name.trim(),
        prompt: prompt.trim(),
        aspect_ratio: aspect,
        resolution,
      });
      setName("");
      setPrompt("");
      refreshAll();
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "No se pudo crear el proyecto");
    } finally {
      setCreating(false);
    }
  }

  const dupBusy =
    dupJobId !== null && (dupPoll.polling || !isTerminal(dupPoll.job?.status));

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_380px]">
      <section className="order-2 lg:order-1">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-neutral-100">Proyectos</h2>
          <Button variant="ghost" onClick={refreshAll}>
            Actualizar
          </Button>
        </div>

        {/* Barra de búsqueda y filtros (q + tag + folder) */}
        <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_160px_160px]">
          <Field label="Buscar" htmlFor="project-search">
            <TextInput
              id="project-search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Nombre o prompt…"
            />
          </Field>
          <Field label="Etiqueta" htmlFor="project-filter-tag">
            <Select
              id="project-filter-tag"
              value={tag}
              onChange={(e) => setTag(e.target.value)}
            >
              <option value="">Todas</option>
              {tagOptions.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Carpeta" htmlFor="project-filter-folder">
            <Select
              id="project-filter-folder"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
            >
              <option value="">Todas</option>
              {folderOptions.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {loadError && <ErrorBanner message={loadError} />}

        {dupBusy && (
          <div className="mb-3 flex items-center gap-2 text-sm text-neutral-400">
            <Spinner /> Duplicando proyecto…
            {dupPoll.job ? ` (${Math.round((dupPoll.job.progress ?? 0) * 100)}%)` : ""}
          </div>
        )}
        {dupPoll.error && <ErrorBanner message={dupPoll.error} />}

        {projects === null && (
          <div className="flex items-center gap-2 text-neutral-400">
            <Spinner /> Cargando proyectos…
          </div>
        )}

        {projects !== null && projects.length === 0 && !loadError && (
          <Card className="text-sm text-neutral-400">
            {q || tag || folder
              ? "Ningún proyecto coincide con los filtros."
              : "Aún no hay proyectos. Crea el primero con el formulario."}
          </Card>
        )}

        <ul className="grid gap-3">
          {projects?.map((p) => (
            <li key={p.id}>
              <ProjectCard
                project={p}
                busy={dupBusy}
                onChanged={refreshAll}
                onDuplicateJob={(jobId) => setDupJobId(jobId)}
              />
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

// --------------------------------------------------------------------------- //
// Card de proyecto con menú de acciones de administración
// --------------------------------------------------------------------------- //

type CardMode = "view" | "rename" | "tags" | "confirm-delete";

function ProjectCard({
  project,
  busy,
  onChanged,
  onDuplicateJob,
}: {
  project: Project;
  busy: boolean;
  onChanged: () => void;
  onDuplicateJob: (jobId: number) => void;
}) {
  const [mode, setMode] = useState<CardMode>("view");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Estado de edición inline (renombrar / tags / carpeta).
  const [draftName, setDraftName] = useState(project.name);
  const [draftTags, setDraftTags] = useState((project.tags ?? []).join(", "));
  const [draftFolder, setDraftFolder] = useState(project.folder ?? "");

  function resetDrafts() {
    setDraftName(project.name);
    setDraftTags((project.tags ?? []).join(", "));
    setDraftFolder(project.folder ?? "");
    setError(null);
  }

  async function run(action: () => Promise<void>) {
    setWorking(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "La acción falló");
      throw err;
    } finally {
      setWorking(false);
    }
  }

  async function handleRename() {
    const next = draftName.trim();
    if (!next || next === project.name) {
      setMode("view");
      return;
    }
    try {
      await run(async () => {
        await updateProject(project.id, { name: next });
      });
      setMode("view");
      onChanged();
    } catch {
      /* el error ya se muestra en la card */
    }
  }

  async function handleSaveTags() {
    const tags = draftTags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const folderValue = draftFolder.trim();
    try {
      await run(async () => {
        await updateProject(project.id, {
          tags,
          folder: folderValue === "" ? null : folderValue,
        });
      });
      setMode("view");
      onChanged();
    } catch {
      /* el error ya se muestra en la card */
    }
  }

  async function handleDuplicate() {
    try {
      await run(async () => {
        const result = await duplicateProject(project.id);
        if (isJob(result)) {
          // Proyecto grande: el backend devuelve un Job; sondearlo y refrescar al final.
          onDuplicateJob((result as Job).id);
        } else {
          // Síncrono: el nuevo proyecto ya existe.
          onChanged();
        }
      });
    } catch {
      /* el error ya se muestra en la card */
    }
  }

  async function handleDelete() {
    try {
      await run(async () => {
        await deleteProject(project.id);
      });
      setMode("view");
      onChanged();
    } catch {
      /* el error ya se muestra en la card */
    }
  }

  const disabled = working || busy;

  return (
    <Card className="transition-colors hover:border-neutral-600">
      {mode === "rename" ? (
        <div className="flex flex-col gap-2">
          <Field label="Nuevo nombre" htmlFor={`rename-${project.id}`}>
            <TextInput
              id={`rename-${project.id}`}
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              autoFocus
            />
          </Field>
          <div className="flex gap-2">
            <Button onClick={handleRename} loading={working} disabled={disabled}>
              Guardar
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                resetDrafts();
                setMode("view");
              }}
              disabled={working}
            >
              Cancelar
            </Button>
          </div>
        </div>
      ) : mode === "tags" ? (
        <div className="flex flex-col gap-2">
          <Field
            label="Etiquetas (separadas por comas)"
            htmlFor={`tags-${project.id}`}
          >
            <TextInput
              id={`tags-${project.id}`}
              value={draftTags}
              onChange={(e) => setDraftTags(e.target.value)}
              placeholder="promo, borrador"
            />
          </Field>
          <Field label="Carpeta" htmlFor={`folder-${project.id}`}>
            <TextInput
              id={`folder-${project.id}`}
              value={draftFolder}
              onChange={(e) => setDraftFolder(e.target.value)}
              placeholder="Sin carpeta"
            />
          </Field>
          <div className="flex gap-2">
            <Button onClick={handleSaveTags} loading={working} disabled={disabled}>
              Guardar
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                resetDrafts();
                setMode("view");
              }}
              disabled={working}
            >
              Cancelar
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <Link
                href={`/projects/${project.id}`}
                className="truncate font-medium text-neutral-100 hover:underline"
              >
                {project.name}
              </Link>
              <p className="mt-1 line-clamp-2 text-sm text-neutral-400">
                {project.prompt || "Sin prompt todavía"}
              </p>
            </div>
            <StatusBadge tone={statusTone(project.status)}>{project.status}</StatusBadge>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-neutral-500">
            <span>#{project.id}</span>
            <span>{project.aspect_ratio}</span>
            <span>{project.resolution}</span>
            <span>{project.production_type}</span>
            {project.folder && (
              <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-neutral-300">
                📁 {project.folder}
              </span>
            )}
          </div>

          {(project.tags ?? []).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(project.tags ?? []).map((t) => (
                <span
                  key={t}
                  className="rounded-full bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300"
                >
                  {t}
                </span>
              ))}
            </div>
          )}

          {error && (
            <div className="mt-3">
              <ErrorBanner message={error} />
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                resetDrafts();
                setMode("rename");
              }}
              disabled={disabled}
            >
              Renombrar
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                resetDrafts();
                setMode("tags");
              }}
              disabled={disabled}
            >
              Tags / Carpeta
            </Button>
            <Button variant="secondary" onClick={handleDuplicate} disabled={disabled}>
              Duplicar
            </Button>
            <Button
              variant="danger"
              onClick={() => setMode("confirm-delete")}
              disabled={disabled}
            >
              Borrar
            </Button>
          </div>
        </>
      )}

      <Modal
        open={mode === "confirm-delete"}
        onClose={() => setMode("view")}
        title="Borrar proyecto"
        footer={
          <>
            <Button variant="ghost" onClick={() => setMode("view")} disabled={working}>
              Cancelar
            </Button>
            <Button variant="danger" onClick={handleDelete} loading={working}>
              Borrar definitivamente
            </Button>
          </>
        }
      >
        <p className="text-sm text-neutral-300">
          ¿Seguro que quieres borrar <strong>{project.name}</strong> (#{project.id})? Se
          eliminarán sus escenas, assets y el workspace de media en disco. Esta acción no
          se puede deshacer.
        </p>
        {error && (
          <div className="mt-3">
            <ErrorBanner message={error} />
          </div>
        )}
      </Modal>
    </Card>
  );
}

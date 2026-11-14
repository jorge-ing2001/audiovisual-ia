"use client";

// Espacio de edición multipista (FEAT-003). Editor de vídeo integrado, NO una pantalla
// de parámetros: línea de tiempo multipista + inspector contextual + vista previa en
// tiempo real (@remotion/player con audio) + undo/redo + exportación.
//
// Principio rector: cada control está conectado al backend real (applyEditOp /
// putEditProject / undo / redo / export) y se refleja en el Player, que se alimenta del
// MISMO EditProject que el export headless ("lo que ves = lo que exportas").

import { Player, type PlayerRef } from "@remotion/player";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  applyEditOp,
  assetDownloadUrl,
  enqueueEditExport,
  exportSubtitles,
  getEditProject,
  importSubtitles,
  latestEditExport,
  listAssets,
  openEditor,
  putEditProject,
  redoEdit,
  undoEdit,
  uploadOverlay,
  videoDownloadUrl,
  type Asset,
  type Job,
  type SubtitleFormat,
} from "@/lib/api";
import {
  compileEditInputProps,
  type EditInputProps,
} from "@/lib/editor/compose";
import {
  editProjectDurationS,
  findClip,
  isAudioKind,
  isVisualKind,
  type EditClip,
  type EditMeta,
  type EditProject,
  type EditTrack,
} from "@/lib/editor/types";
import EditComposition from "@/components/remotion/EditComposition";
import JobProgress from "@/components/JobProgress";
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
import { useJobPolling } from "@/lib/useJobPolling";

const TRACK_COLORS: Record<string, string> = {
  video: "bg-indigo-500/70",
  image: "bg-sky-500/70",
  voice: "bg-emerald-500/70",
  music: "bg-amber-500/70",
  sfx: "bg-rose-500/70",
  subtitle: "bg-fuchsia-500/70",
  title: "bg-cyan-500/70",
  overlay: "bg-lime-500/70",
};

export default function EditorWorkspace({ projectId }: { projectId: number }) {
  const [project, setProject] = useState<EditProject | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);

  const [exportJobId, setExportJobId] = useState<number | null>(null);
  const [exportReady, setExportReady] = useState(false);
  const { job: exportJob, error: exportPollError } = useJobPolling(exportJobId);

  const playerRef = useRef<PlayerRef>(null);

  // Al montar: abre el editor (idempotente) y carga el EditProject + los assets.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    openEditor(projectId)
      .then((read) => {
        if (cancelled) return;
        setProject(read.edit_project);
      })
      .catch(async (err) => {
        // Si ya estaba abierto, un GET sirve igual.
        if (err instanceof ApiError) {
          try {
            const read = await getEditProject(projectId);
            if (!cancelled) setProject(read.edit_project);
            return;
          } catch {
            // cae al manejo de error de abajo
          }
        }
        if (!cancelled)
          setError(
            err instanceof ApiError ? err.message : "No se pudo abrir el editor",
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    listAssets(projectId)
      .then((list) => {
        if (!cancelled) setAssets(list);
      })
      .catch(() => {
        /* la lista de assets es auxiliar (reemplazo de recurso) */
      });

    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Detecta el último export previo (si lo hay) para rehabilitar la descarga.
  useEffect(() => {
    let cancelled = false;
    latestEditExport(projectId)
      .then((j: Job) => {
        if (cancelled) return;
        if (j.status === "succeeded") setExportReady(true);
        else if (!["failed", "cancelled"].includes(j.status)) setExportJobId(j.id);
      })
      .catch(() => {
        /* 404 = sin export todavía */
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (exportJob && exportJob.status === "succeeded") setExportReady(true);
  }, [exportJob]);

  // URL de media del Player (opción A: GET /assets/{id}/download, con range requests).
  const resolveSrc = useCallback(
    (assetId: number | null) => (assetId == null ? null : assetDownloadUrl(assetId)),
    [],
  );

  const inputProps: EditInputProps | null = useMemo(() => {
    if (!project) return null;
    return compileEditInputProps(project, resolveSrc, false);
  }, [project, resolveSrc]);

  const durationS = project ? editProjectDurationS(project) : 0;

  const selected = useMemo(() => {
    if (!project || !selectedClipId) return null;
    return findClip(project, selectedClipId);
  }, [project, selectedClipId]);

  // ---- helpers de edición (todos pegan al backend y refrescan el estado) ---- //

  const runOp = useCallback(
    async (op: string, params: Record<string, unknown>) => {
      setBusy(true);
      setActionError(null);
      try {
        const read = await applyEditOp(projectId, op, params);
        setProject(read.edit_project);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : `No se pudo aplicar ${op}`,
        );
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  // Edición de campos sin operación dedicada (transform, estilo de subtítulo, reemplazo
  // de recurso): mutamos una copia del documento y la persistimos entera con PUT (versiona
  // igual que una operación → undo/redo lo recoge).
  const patchClip = useCallback(
    async (clipId: string, mutate: (clip: EditClip) => void) => {
      if (!project) return;
      const next: EditProject = structuredClone(project);
      const found = findClip(next, clipId);
      if (!found) return;
      mutate(found.clip);
      setBusy(true);
      setActionError(null);
      try {
        const read = await putEditProject(projectId, next);
        setProject(read.edit_project);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "No se pudo guardar el cambio",
        );
      } finally {
        setBusy(false);
      }
    },
    [project, projectId],
  );

  const handleUndo = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      const read = await undoEdit(projectId);
      setProject(read.edit_project);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "No hay nada que deshacer",
      );
    } finally {
      setBusy(false);
    }
  }, [projectId]);

  const handleRedo = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      const read = await redoEdit(projectId);
      setProject(read.edit_project);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "No hay nada que rehacer",
      );
    } finally {
      setBusy(false);
    }
  }, [projectId]);

  const handleExport = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    setExportReady(false);
    try {
      const res = await enqueueEditExport(projectId);
      setExportJobId(res.job_id);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "No se pudo encolar el export",
      );
    } finally {
      setBusy(false);
    }
  }, [projectId]);

  // Import/export de subtítulos (FEAT-003): descarga el fichero o sube uno nuevo que
  // reemplaza/añade la pista subtitle (crea Version → undo/redo lo recoge).
  const handleExportSubtitles = useCallback(
    async (format: SubtitleFormat) => {
      setBusy(true);
      setActionError(null);
      try {
        await exportSubtitles(projectId, format);
      } catch (err) {
        setActionError(
          err instanceof ApiError
            ? err.message
            : "No se pudieron exportar los subtítulos",
        );
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  const handleImportSubtitles = useCallback(
    async (file: File, mode: "replace" | "append") => {
      setBusy(true);
      setActionError(null);
      try {
        const read = await importSubtitles(projectId, file, mode);
        setProject(read.edit_project);
      } catch (err) {
        setActionError(
          err instanceof ApiError
            ? err.message
            : "No se pudieron importar los subtítulos",
        );
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  // Cambio de formato del proyecto (9:16/16:9/1:1) vía la op pura set_format, que fija
  // resolución + zonas seguras en meta (afecta al encuadre del preview y del export).
  const handleSetFormat = useCallback(
    (format: string) => void runOp("set_format", { format }),
    [runOp],
  );

  // Añadir un título (FEAT-005): crea la pista `title` si no existe (add_track) y añade un
  // clip de título (add_title). Ambas ops versionan; el preview lo refleja al instante.
  const handleAddTitle = useCallback(async () => {
    if (!project) return;
    setBusy(true);
    setActionError(null);
    try {
      let current = project;
      let titleTrack = current.tracks.find((t) => t.kind === "title");
      if (!titleTrack) {
        const read = await applyEditOp(projectId, "add_track", {
          kind: "title",
          name: "Títulos",
        });
        current = read.edit_project;
        titleTrack = current.tracks.find((t) => t.kind === "title");
      }
      if (!titleTrack) throw new ApiError(0, "No se pudo crear la pista de títulos.");
      const start = editProjectDurationS(current) > 0 ? 0 : 0;
      const read = await applyEditOp(projectId, "add_title", {
        track_id: titleTrack.id,
        text: "Título",
        start_s: start,
        duration_s: 3.0,
        title: { kind: "intro" },
        animation: { type: "fade" },
      });
      setProject(read.edit_project);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "No se pudo añadir el título",
      );
    } finally {
      setBusy(false);
    }
  }, [project, projectId]);

  // Subir un logo/overlay (FEAT-006): sube la imagen (uploadOverlay), crea la pista
  // `overlay` si no existe y añade un clip de overlay con el asset_id devuelto.
  const handleUploadOverlay = useCallback(
    async (file: File) => {
      if (!project) return;
      setBusy(true);
      setActionError(null);
      try {
        const asset = await uploadOverlay(projectId, file);
        setAssets((prev) => [asset, ...prev]);
        let current = project;
        let overlayTrack = current.tracks.find((t) => t.kind === "overlay");
        if (!overlayTrack) {
          const read = await applyEditOp(projectId, "add_track", {
            kind: "overlay",
            name: "Overlays",
          });
          current = read.edit_project;
          overlayTrack = current.tracks.find((t) => t.kind === "overlay");
        }
        if (!overlayTrack)
          throw new ApiError(0, "No se pudo crear la pista de overlays.");
        const duration = Math.max(1, editProjectDurationS(current) || 5);
        const read = await applyEditOp(projectId, "add_overlay", {
          track_id: overlayTrack.id,
          asset_id: asset.id,
          start_s: 0,
          duration_s: duration,
        });
        setProject(read.edit_project);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "No se pudo subir el overlay",
        );
      } finally {
        setBusy(false);
      }
    },
    [project, projectId],
  );

  // ------------------------------- render ------------------------------- //

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-neutral-400">
        <Spinner /> Abriendo editor…
      </div>
    );
  }

  if (error || !project || !inputProps) {
    return (
      <div className="flex flex-col gap-4">
        <ErrorBanner message={error ?? "No se pudo cargar el proyecto de edición"} />
        <Link
          href={`/projects/${projectId}`}
          className="text-sm text-neutral-400 hover:text-neutral-200"
        >
          ← Volver al proyecto
        </Link>
      </div>
    );
  }

  const videoUrl = videoDownloadUrl(projectId);

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <Link
            href={`/projects/${projectId}`}
            className="text-sm text-neutral-400 hover:text-neutral-200"
          >
            ← Volver al proyecto
          </Link>
          <h1 className="text-xl font-bold text-neutral-100">Editor de vídeo</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => void handleUndo()} loading={busy}>
            ↶ Deshacer
          </Button>
          <Button variant="secondary" onClick={() => void handleRedo()} loading={busy}>
            ↷ Rehacer
          </Button>
          <Button onClick={() => void handleExport()} loading={busy}>
            Exportar vídeo
          </Button>
        </div>
      </header>

      {actionError && <ErrorBanner message={actionError} />}

      <div className="grid gap-5 lg:grid-cols-[2fr_1fr]">
        {/* Vista previa en tiempo real con audio */}
        <Card className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="font-medium text-neutral-100">Vista previa</h2>
            <span className="text-xs text-neutral-500">
              {project.meta.width}×{project.meta.height} · {project.meta.fps} fps ·{" "}
              {durationS.toFixed(1)} s
            </span>
          </div>
          <div className="relative overflow-hidden rounded-lg border border-neutral-800 bg-black">
            <Player
              ref={playerRef}
              component={EditComposition}
              inputProps={inputProps}
              durationInFrames={inputProps.durationInFrames}
              compositionWidth={inputProps.width}
              compositionHeight={inputProps.height}
              fps={inputProps.fps}
              controls
              style={{ width: "100%" }}
              acknowledgeRemotionLicense
            />
            {/* Guías de zona segura (formato vertical 9:16): bandas que la UI de la red
                social puede tapar; ayudan a no colocar subtítulos/títulos debajo. */}
            {(project.meta.safe_top_frac > 0 ||
              project.meta.safe_bottom_frac > 0) && (
              <div className="pointer-events-none absolute inset-0">
                {project.meta.safe_top_frac > 0 && (
                  <div
                    className="absolute inset-x-0 top-0 border-b border-dashed border-amber-400/50 bg-amber-400/10"
                    style={{ height: `${project.meta.safe_top_frac * 100}%` }}
                  >
                    <span className="absolute left-1 top-1 text-[9px] font-medium uppercase text-amber-300/80">
                      zona segura
                    </span>
                  </div>
                )}
                {project.meta.safe_bottom_frac > 0 && (
                  <div
                    className="absolute inset-x-0 bottom-0 border-t border-dashed border-amber-400/50 bg-amber-400/10"
                    style={{ height: `${project.meta.safe_bottom_frac * 100}%` }}
                  >
                    <span className="absolute bottom-1 left-1 text-[9px] font-medium uppercase text-amber-300/80">
                      zona segura
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Formato de salida + import/export de subtítulos (FEAT-003/004). */}
          <SubtitleToolbar
            meta={project.meta}
            busy={busy}
            onSetFormat={handleSetFormat}
            onExport={handleExportSubtitles}
            onImport={handleImportSubtitles}
          />
          <p className="text-xs text-neutral-500">
            El preview reproduce el audio del navegador sin masterizar. El export final lo
            normaliza a −14 LUFS; la posición, recorte, volumen y selección de pistas son
            los mismos.
          </p>
        </Card>

        {/* Inspector del clip seleccionado */}
        <Card className="flex flex-col gap-3">
          <h2 className="font-medium text-neutral-100">Inspector</h2>
          {selected ? (
            <ClipInspector
              key={selected.clip.id}
              clip={selected.clip}
              track={selected.track}
              assets={assets}
              busy={busy}
              onOp={runOp}
              onPatch={patchClip}
            />
          ) : (
            <p className="text-sm text-neutral-500">
              Selecciona un clip en la línea de tiempo para editarlo.
            </p>
          )}
        </Card>
      </div>

      {/* Export: progreso + descarga del MP4 */}
      {(exportJob || exportPollError) && (
        <JobProgress
          job={exportJob}
          title="Progreso del export"
          pollingError={exportPollError}
        />
      )}
      {exportReady && (
        <Card>
          <h3 className="mb-3 font-medium text-neutral-100">Vídeo exportado</h3>
          <video
            controls
            src={videoUrl}
            className="aspect-video w-full rounded-lg border border-neutral-800 bg-black"
          />
          <div className="mt-3 flex gap-3">
            <a href={videoUrl} download>
              <Button variant="secondary">Descargar MP4</Button>
            </a>
          </div>
        </Card>
      )}

      {/* Añadir títulos / subir overlays (FEAT-005/006). */}
      <TitleOverlayToolbar
        busy={busy}
        onAddTitle={handleAddTitle}
        onUploadOverlay={handleUploadOverlay}
      />

      {/* Línea de tiempo multipista */}
      <Timeline
        project={project}
        durationS={durationS}
        selectedClipId={selectedClipId}
        onSelect={setSelectedClipId}
      />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Barra de formato + import/export de subtítulos (FEAT-003/004)
// --------------------------------------------------------------------------- //

// Formato actual del proyecto a partir de meta (para el selector). Los verticales se
// distinguen por las zonas seguras (TikTok/Reels/Shorts comparten 1080×1920).
function currentFormatKey(meta: EditMeta): string {
  if (meta.aspect_ratio === "1:1") return "1:1";
  if (meta.aspect_ratio === "9:16") {
    if (meta.safe_bottom_frac >= 0.2) return "reels";
    if (meta.safe_bottom_frac >= 0.18) return "tiktok";
    return "shorts";
  }
  return "16:9";
}

function SubtitleToolbar({
  meta,
  busy,
  onSetFormat,
  onExport,
  onImport,
}: {
  meta: EditMeta;
  busy: boolean;
  onSetFormat: (format: string) => void;
  onExport: (format: SubtitleFormat) => void | Promise<void>;
  onImport: (file: File, mode: "replace" | "append") => void | Promise<void>;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [importMode, setImportMode] = useState<"replace" | "append">("replace");
  const [exportFormat, setExportFormat] = useState<SubtitleFormat>("srt");

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border border-neutral-800 bg-neutral-950/60 p-3">
      <Field label="Formato de salida">
        <Select
          value={currentFormatKey(meta)}
          onChange={(e) => onSetFormat(e.target.value)}
          disabled={busy}
        >
          <option value="16:9">16:9 horizontal</option>
          <option value="1:1">1:1 cuadrado</option>
          <option value="tiktok">9:16 TikTok</option>
          <option value="reels">9:16 Reels</option>
          <option value="shorts">9:16 Shorts</option>
        </Select>
      </Field>

      <div className="flex items-end gap-2">
        <Field label="Exportar subtítulos">
          <Select
            value={exportFormat}
            onChange={(e) => setExportFormat(e.target.value as SubtitleFormat)}
            disabled={busy}
          >
            <option value="srt">SRT</option>
            <option value="vtt">VTT</option>
            <option value="ass">ASS</option>
          </Select>
        </Field>
        <Button
          variant="secondary"
          loading={busy}
          onClick={() => void onExport(exportFormat)}
        >
          Descargar
        </Button>
      </div>

      <div className="flex items-end gap-2">
        <Field label="Importar subtítulos">
          <Select
            value={importMode}
            onChange={(e) =>
              setImportMode(e.target.value as "replace" | "append")
            }
            disabled={busy}
          >
            <option value="replace">reemplazar</option>
            <option value="append">añadir</option>
          </Select>
        </Field>
        <input
          ref={fileRef}
          type="file"
          accept=".srt,.vtt,.ass"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void onImport(file, importMode);
            e.target.value = "";
          }}
        />
        <Button
          variant="secondary"
          loading={busy}
          onClick={() => fileRef.current?.click()}
        >
          Subir fichero
        </Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Barra de títulos / overlays (FEAT-005/006)
// --------------------------------------------------------------------------- //

function TitleOverlayToolbar({
  busy,
  onAddTitle,
  onUploadOverlay,
}: {
  busy: boolean;
  onAddTitle: () => void | Promise<void>;
  onUploadOverlay: (file: File) => void | Promise<void>;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  return (
    <Card className="flex flex-wrap items-center gap-3">
      <h2 className="mr-2 font-medium text-neutral-100">Títulos y overlays</h2>
      <Button variant="secondary" loading={busy} onClick={() => void onAddTitle()}>
        + Añadir título
      </Button>
      <input
        ref={fileRef}
        type="file"
        accept=".png,.jpg,.jpeg,.webp,.gif,.svg,image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void onUploadOverlay(file);
          e.target.value = "";
        }}
      />
      <Button
        variant="secondary"
        loading={busy}
        onClick={() => fileRef.current?.click()}
      >
        + Subir logo / overlay
      </Button>
      <p className="text-xs text-neutral-500">
        El título se añade como clip animado; el logo/overlay se sube y se coloca como marca
        gráfica (ajusta opacidad, tamaño, rotación y mosaico en el inspector).
      </p>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Línea de tiempo multipista
// --------------------------------------------------------------------------- //

function Timeline({
  project,
  durationS,
  selectedClipId,
  onSelect,
}: {
  project: EditProject;
  durationS: number;
  selectedClipId: string | null;
  onSelect: (id: string) => void;
}) {
  const scale = durationS > 0 ? 100 / durationS : 0; // % por segundo
  const tracks = [...project.tracks].sort((a, b) => a.order - b.order);

  return (
    <Card className="flex flex-col gap-2">
      <h2 className="font-medium text-neutral-100">Línea de tiempo</h2>
      <div className="flex flex-col gap-2">
        {tracks.map((track) => (
          <div key={track.id} className="flex items-center gap-3">
            <div className="w-24 shrink-0 text-xs text-neutral-400">
              {track.name || track.kind}
              {track.muted && <span className="ml-1 text-rose-400">(mute)</span>}
            </div>
            <div className="relative h-9 flex-1 overflow-hidden rounded-md border border-neutral-800 bg-neutral-950">
              {track.clips.map((clip) => {
                const left = Math.max(0, clip.start_s * scale);
                const width = Math.max(1.5, clip.duration_s * scale);
                const isSel = clip.id === selectedClipId;
                return (
                  <button
                    key={clip.id}
                    type="button"
                    onClick={() => onSelect(clip.id)}
                    title={`${clip.kind} · ${clip.start_s.toFixed(2)}s → ${(
                      clip.start_s + clip.duration_s
                    ).toFixed(2)}s`}
                    className={`absolute top-0.5 bottom-0.5 flex items-center overflow-hidden rounded px-1.5 text-[10px] font-medium text-white/90 ${
                      TRACK_COLORS[track.kind] ?? "bg-neutral-600"
                    } ${
                      isSel
                        ? "ring-2 ring-white"
                        : "ring-1 ring-black/40 hover:ring-white/60"
                    } ${clip.enabled ? "" : "opacity-40"}`}
                    style={{ left: `${left}%`, width: `${width}%` }}
                  >
                    <span className="truncate">
                      {clip.kind === "subtitle" || clip.kind === "title"
                        ? clip.text || clip.kind
                        : clip.id}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <p className="text-right text-xs text-neutral-600">
        Duración total: {durationS.toFixed(2)} s
      </p>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Inspector contextual por tipo de clip
// --------------------------------------------------------------------------- //

function ClipInspector({
  clip,
  track,
  assets,
  busy,
  onOp,
  onPatch,
}: {
  clip: EditClip;
  track: EditTrack;
  assets: Asset[];
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
  onPatch: (clipId: string, mutate: (clip: EditClip) => void) => void | Promise<void>;
}) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs text-neutral-400">{clip.id}</span>
        <StatusBadge tone={statusTone(clip.enabled ? "ready" : "idle")}>
          {clip.kind}
        </StatusBadge>
      </div>

      {/* Posición / duración (común a todos) */}
      <ClipTiming clip={clip} busy={busy} onOp={onOp} />

      {isVisualKind(clip.kind) && (
        <VisualInspector clip={clip} assets={assets} busy={busy} onOp={onOp} />
      )}
      {isAudioKind(clip.kind) && (
        <AudioInspector
          clip={clip}
          track={track}
          assets={assets}
          busy={busy}
          onOp={onOp}
          onPatch={onPatch}
        />
      )}
      {clip.kind === "subtitle" && (
        <SubtitleInspector clip={clip} busy={busy} onOp={onOp} />
      )}
      {clip.kind === "title" && (
        <TitleInspector clip={clip} busy={busy} onOp={onOp} />
      )}
      {clip.kind === "overlay" && (
        <OverlayInspector clip={clip} busy={busy} onOp={onOp} />
      )}

      {/* Acciones de clip (operaciones puras del backend) */}
      <div className="flex flex-wrap gap-2 border-t border-neutral-800 pt-3">
        <Button
          variant="secondary"
          loading={busy}
          onClick={() =>
            void onOp("split", {
              clip_id: clip.id,
              t_s: clip.start_s + clip.duration_s / 2,
            })
          }
        >
          Dividir
        </Button>
        <Button
          variant="secondary"
          loading={busy}
          onClick={() => void onOp("duplicate", { clip_id: clip.id })}
        >
          Duplicar
        </Button>
        <Button
          variant="danger"
          loading={busy}
          onClick={() => void onOp("delete", { clip_id: clip.id })}
        >
          Eliminar
        </Button>
      </div>
    </div>
  );
}

function ClipTiming({
  clip,
  busy,
  onOp,
}: {
  clip: EditClip;
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const [start, setStart] = useState(String(clip.start_s));
  const [duration, setDuration] = useState(String(clip.duration_s));

  return (
    <div className="grid grid-cols-2 gap-3">
      <Field label="Inicio (s)">
        <TextInput
          type="number"
          step="0.1"
          min="0"
          value={start}
          onChange={(e) => setStart(e.target.value)}
          onBlur={() => {
            const v = Number(start);
            if (Number.isFinite(v) && v !== clip.start_s)
              void onOp("move", { clip_id: clip.id, start_s: v });
          }}
          disabled={busy}
        />
      </Field>
      <Field label="Duración (s)">
        <TextInput
          type="number"
          step="0.1"
          min="0.1"
          value={duration}
          onChange={(e) => setDuration(e.target.value)}
          onBlur={() => {
            const v = Number(duration);
            if (Number.isFinite(v) && v > 0 && v !== clip.duration_s) {
              const op = clip.kind === "subtitle" ? "set_timing" : "set_duration";
              void onOp(op, { clip_id: clip.id, duration_s: v });
            }
          }}
          disabled={busy}
        />
      </Field>
    </div>
  );
}

function VisualInspector({
  clip,
  assets,
  busy,
  onOp,
}: {
  clip: EditClip;
  assets: Asset[];
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const t = clip.transform;
  const scale = t?.scale ?? 1;
  const x = t?.x ?? 0;
  const y = t?.y ?? 0;
  const fit = t?.fit ?? "cover";
  const visualAssets = assets.filter((a) => a.kind === "image" || a.kind === "video");

  // Cada control del transform llama a la operación pura set_transform (versiona +
  // propaga a preview/export por igual).
  const setTransform = (patch: Record<string, unknown>) =>
    void onOp("set_transform", { clip_id: clip.id, ...patch });

  const setTransition = (edge: "in" | "out", type: "none" | "fade") =>
    void onOp("set_transition", {
      clip_id: clip.id,
      edge,
      type,
      duration_s: 0.5,
    });

  return (
    <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3">
      <h4 className="text-xs font-semibold uppercase text-neutral-500">Visual</h4>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Escala">
          <TextInput
            type="number"
            step="0.05"
            min="0.1"
            defaultValue={scale}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v > 0 && v !== scale)
                setTransform({ scale: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Ajuste">
          <Select
            value={fit}
            onChange={(e) => setTransform({ fit: e.target.value })}
            disabled={busy}
          >
            <option value="cover">cover</option>
            <option value="contain">contain</option>
          </Select>
        </Field>
        <Field label="Posición X (px)">
          <TextInput
            type="number"
            step="10"
            defaultValue={x}
            onBlur={(e) => setTransform({ x: Number(e.target.value) || 0 })}
            disabled={busy}
          />
        </Field>
        <Field label="Posición Y (px)">
          <TextInput
            type="number"
            step="10"
            defaultValue={y}
            onBlur={(e) => setTransform({ y: Number(e.target.value) || 0 })}
            disabled={busy}
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Transición entrada">
          <Select
            value={clip.transition_in?.type === "fade" ? "fade" : "none"}
            onChange={(e) =>
              setTransition("in", e.target.value as "none" | "fade")
            }
            disabled={busy}
          >
            <option value="none">corte</option>
            <option value="fade">fundido</option>
          </Select>
        </Field>
        <Field label="Transición salida">
          <Select
            value={clip.transition_out?.type === "fade" ? "fade" : "none"}
            onChange={(e) =>
              setTransition("out", e.target.value as "none" | "fade")
            }
            disabled={busy}
          >
            <option value="none">corte</option>
            <option value="fade">fundido</option>
          </Select>
        </Field>
      </div>
      <Field label="Reemplazar recurso">
        <Select
          value={clip.asset_id ?? ""}
          onChange={(e) => {
            if (!e.target.value) return;
            void onOp("replace_asset", {
              clip_id: clip.id,
              asset_id: Number(e.target.value),
            });
          }}
          disabled={busy}
        >
          <option value="">(elige un recurso)</option>
          {visualAssets.map((a) => (
            <option key={a.id} value={a.id}>
              #{a.id} · {a.kind}
            </option>
          ))}
        </Select>
      </Field>
    </div>
  );
}

function AudioInspector({
  clip,
  track,
  assets,
  busy,
  onOp,
  onPatch,
}: {
  clip: EditClip;
  track: EditTrack;
  assets: Asset[];
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
  onPatch: (clipId: string, mutate: (clip: EditClip) => void) => void | Promise<void>;
}) {
  const audioAssets = assets.filter(
    (a) => a.kind === "voice" || a.kind === "music" || a.kind === "sfx",
  );
  return (
    <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3">
      <h4 className="text-xs font-semibold uppercase text-neutral-500">Audio</h4>

      {/* Volumen de la PISTA entera (set_track_volume) — afecta a todo su audio. */}
      <Field label={`Volumen de pista (${track.volume.toFixed(2)})`}>
        <input
          type="range"
          min={0}
          max={2}
          step={0.05}
          defaultValue={track.volume}
          onMouseUp={(e) =>
            void onOp("set_track_volume", {
              track_id: track.id,
              volume: Number((e.target as HTMLInputElement).value),
            })
          }
          onTouchEnd={(e) =>
            void onOp("set_track_volume", {
              track_id: track.id,
              volume: Number((e.target as HTMLInputElement).value),
            })
          }
          disabled={busy}
          className="w-full"
        />
      </Field>

      {/* Volumen del CLIP (set_clip_gain). */}
      <Field label={`Volumen del clip (gain = ${clip.gain.toFixed(2)})`}>
        <input
          type="range"
          min={0}
          max={2}
          step={0.05}
          defaultValue={clip.gain}
          onMouseUp={(e) =>
            void onOp("set_clip_gain", {
              clip_id: clip.id,
              gain: Number((e.target as HTMLInputElement).value),
            })
          }
          onTouchEnd={(e) =>
            void onOp("set_clip_gain", {
              clip_id: clip.id,
              gain: Number((e.target as HTMLInputElement).value),
            })
          }
          disabled={busy}
          className="w-full"
        />
      </Field>

      {/* Fades in/out del clip (set_fade). */}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Fade in (s)">
          <TextInput
            type="number"
            min="0"
            step="0.1"
            defaultValue={clip.fade_in_s}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v !== clip.fade_in_s)
                void onOp("set_fade", { clip_id: clip.id, fade_in_s: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Fade out (s)">
          <TextInput
            type="number"
            min="0"
            step="0.1"
            defaultValue={clip.fade_out_s}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v !== clip.fade_out_s)
                void onOp("set_fade", { clip_id: clip.id, fade_out_s: v });
            }}
            disabled={busy}
          />
        </Field>
      </div>

      <Field label="Reemplazar recurso de audio">
        <Select
          value={clip.asset_id ?? ""}
          onChange={(e) => {
            const v = e.target.value ? Number(e.target.value) : null;
            void onPatch(clip.id, (c) => {
              c.asset_id = v;
            });
          }}
          disabled={busy}
        >
          <option value="">(sin recurso)</option>
          {audioAssets.map((a) => (
            <option key={a.id} value={a.id}>
              #{a.id} · {a.kind}
            </option>
          ))}
        </Select>
      </Field>
    </div>
  );
}

function SubtitleInspector({
  clip,
  busy,
  onOp,
}: {
  clip: EditClip;
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const [text, setText] = useState(clip.text ?? "");
  const style = clip.style;

  // Cada cambio de estilo llama a la operación pura set_style (versiona + paridad
  // preview/export).
  const setStyle = (patch: Record<string, unknown>) =>
    void onOp("set_style", { clip_id: clip.id, ...patch });

  return (
    <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3">
      <h4 className="text-xs font-semibold uppercase text-neutral-500">Subtítulo</h4>
      <Field label="Texto">
        <TextArea
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            if (text !== (clip.text ?? ""))
              void onOp("set_text", { clip_id: clip.id, text });
          }}
          disabled={busy}
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Preset" hint="Aplica un estilo completo (contorno/sombra/fondo)">
          <Select
            value={style?.preset ?? "classic"}
            onChange={(e) =>
              void onOp("apply_subtitle_preset", {
                clip_id: clip.id,
                preset: e.target.value,
              })
            }
            disabled={busy}
          >
            <option value="classic">classic</option>
            <option value="tiktok">tiktok</option>
            <option value="reels">reels</option>
            <option value="shorts">shorts</option>
            <option value="cinema">cinema</option>
            <option value="clean">clean</option>
          </Select>
        </Field>
        <Field label="Posición">
          <Select
            value={style?.position ?? "bottom"}
            onChange={(e) => setStyle({ position: e.target.value })}
            disabled={busy}
          >
            <option value="top">arriba</option>
            <option value="center">centro</option>
            <option value="bottom">abajo</option>
          </Select>
        </Field>
        <Field label="Tipografía">
          <TextInput
            type="text"
            defaultValue={style?.font_family ?? "Inter, sans-serif"}
            onBlur={(e) => {
              const v = e.target.value.trim();
              if (v) setStyle({ font_family: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Tamaño fuente">
          <TextInput
            type="number"
            min="8"
            step="2"
            defaultValue={style?.font_size ?? 48}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v > 0) setStyle({ font_size: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Color texto">
          <TextInput
            type="text"
            defaultValue={style?.color ?? "#FFFFFF"}
            onBlur={(e) => setStyle({ color: e.target.value || "#FFFFFF" })}
            disabled={busy}
          />
        </Field>
        <Field label="Color fondo">
          <TextInput
            type="text"
            placeholder="(sin fondo)"
            defaultValue={style?.bg_color ?? ""}
            onBlur={(e) => {
              const v = e.target.value.trim();
              setStyle({ bg_color: v === "" ? null : v });
            }}
            disabled={busy}
          />
        </Field>
      </div>
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-xs text-neutral-300">
          <input
            type="checkbox"
            defaultChecked={style?.bold ?? true}
            onChange={(e) => setStyle({ bold: e.target.checked })}
            disabled={busy}
          />
          Negrita
        </label>
        <label className="flex items-center gap-2 text-xs text-neutral-300">
          <input
            type="checkbox"
            defaultChecked={style?.outline ?? true}
            onChange={(e) => setStyle({ outline: e.target.checked })}
            disabled={busy}
          />
          Contorno
        </label>
      </div>

      {/* Decoración fina: contorno/sombra/fondo (FEAT-004). Cada control llama a
          set_style con el campo nuevo; los defaults conservan el aspecto heredado. */}
      <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3">
        <h4 className="text-xs font-semibold uppercase text-neutral-500">
          Contorno / sombra / fondo
        </h4>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Grosor contorno (px)">
            <TextInput
              type="number"
              min="0"
              step="0.5"
              defaultValue={style?.outline_width ?? 0}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0)
                  setStyle({ outline_width: v });
              }}
              disabled={busy}
            />
          </Field>
          <Field label="Color contorno">
            <TextInput
              type="text"
              defaultValue={style?.outline_color ?? "#000000"}
              onBlur={(e) =>
                setStyle({ outline_color: e.target.value || "#000000" })
              }
              disabled={busy}
            />
          </Field>
          <Field label="Desenfoque sombra (px)">
            <TextInput
              type="number"
              min="0"
              step="1"
              defaultValue={style?.shadow_blur ?? 0}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) setStyle({ shadow_blur: v });
              }}
              disabled={busy}
            />
          </Field>
          <Field label="Color sombra">
            <TextInput
              type="text"
              defaultValue={style?.shadow_color ?? "#000000"}
              onBlur={(e) =>
                setStyle({ shadow_color: e.target.value || "#000000" })
              }
              disabled={busy}
            />
          </Field>
          <Field label="Opacidad fondo (0–1)">
            <TextInput
              type="number"
              min="0"
              max="1"
              step="0.05"
              defaultValue={style?.bg_opacity ?? 1}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0 && v <= 1)
                  setStyle({ bg_opacity: v });
              }}
              disabled={busy}
            />
          </Field>
          <Field label="Radio caja (px)">
            <TextInput
              type="number"
              min="0"
              step="1"
              defaultValue={style?.bg_radius ?? 8}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) setStyle({ bg_radius: v });
              }}
              disabled={busy}
            />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-xs text-neutral-300">
          <input
            type="checkbox"
            defaultChecked={style?.shadow ?? false}
            onChange={(e) => setStyle({ shadow: e.target.checked })}
            disabled={busy}
          />
          Sombra activa
        </label>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Inspector de título (FEAT-005): texto, estilo, animación y tipo
// --------------------------------------------------------------------------- //

function TitleInspector({
  clip,
  busy,
  onOp,
}: {
  clip: EditClip;
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const [text, setText] = useState(clip.text ?? "");
  const title = clip.title;
  const anim = clip.title_animation;

  const setStyle = (patch: Record<string, unknown>) =>
    void onOp("set_title_style", { clip_id: clip.id, ...patch });
  const setAnim = (patch: Record<string, unknown>) =>
    void onOp("set_title_animation", { clip_id: clip.id, ...patch });

  return (
    <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3">
      <h4 className="text-xs font-semibold uppercase text-neutral-500">Título</h4>
      <Field label="Texto">
        <TextArea
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            const v = text.trim();
            if (v && v !== (clip.text ?? ""))
              void onOp("set_text", { clip_id: clip.id, text });
          }}
          disabled={busy}
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Tipo">
          <Select
            value={title?.kind ?? "plain"}
            onChange={(e) => setStyle({ kind: e.target.value })}
            disabled={busy}
          >
            <option value="plain">normal</option>
            <option value="intro">intro</option>
            <option value="lower_third">lower-third</option>
            <option value="credits">créditos</option>
          </Select>
        </Field>
        <Field label="Animación">
          <Select
            value={anim?.type ?? "fade"}
            onChange={(e) => setAnim({ type: e.target.value })}
            disabled={busy}
          >
            <option value="none">ninguna</option>
            <option value="fade">fundido</option>
            <option value="slide_up">deslizar arriba</option>
            <option value="slide_down">deslizar abajo</option>
            <option value="scale_in">zoom</option>
            <option value="typewriter">máquina de escribir</option>
            <option value="credits_roll">créditos (scroll)</option>
          </Select>
        </Field>
        <Field label="Alineación">
          <Select
            value={title?.align ?? "center"}
            onChange={(e) => setStyle({ align: e.target.value })}
            disabled={busy}
          >
            <option value="left">izquierda</option>
            <option value="center">centro</option>
            <option value="right">derecha</option>
          </Select>
        </Field>
        <Field label="Tamaño fuente">
          <TextInput
            type="number"
            min="8"
            step="4"
            defaultValue={title?.font_size ?? 96}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v > 0) setStyle({ font_size: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Color texto">
          <TextInput
            type="text"
            defaultValue={title?.color ?? "#FFFFFF"}
            onBlur={(e) => setStyle({ color: e.target.value || "#FFFFFF" })}
            disabled={busy}
          />
        </Field>
        <Field label="Duración animación (s)">
          <TextInput
            type="number"
            min="0"
            step="0.1"
            defaultValue={anim?.duration_s ?? 0.8}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0) setAnim({ duration_s: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Posición X (0–1)">
          <TextInput
            type="number"
            min="0"
            max="1"
            step="0.05"
            defaultValue={title?.anchor_x ?? 0.5}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v <= 1)
                setStyle({ anchor_x: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Posición Y (0–1)">
          <TextInput
            type="number"
            min="0"
            max="1"
            step="0.05"
            defaultValue={title?.anchor_y ?? 0.5}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v <= 1)
                setStyle({ anchor_y: v });
            }}
            disabled={busy}
          />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-xs text-neutral-300">
        <input
          type="checkbox"
          defaultChecked={title?.bold ?? true}
          onChange={(e) => setStyle({ bold: e.target.checked })}
          disabled={busy}
        />
        Negrita
      </label>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Inspector de overlay (FEAT-006): opacidad, tamaño, rotación, mosaico
// --------------------------------------------------------------------------- //

function OverlayInspector({
  clip,
  busy,
  onOp,
}: {
  clip: EditClip;
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const overlay = clip.overlay;
  const setStyle = (patch: Record<string, unknown>) =>
    void onOp("set_overlay_style", { clip_id: clip.id, ...patch });

  return (
    <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3">
      <h4 className="text-xs font-semibold uppercase text-neutral-500">Overlay</h4>
      <Field label={`Opacidad (${(overlay?.opacity ?? 1).toFixed(2)})`}>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          defaultValue={overlay?.opacity ?? 1}
          onMouseUp={(e) =>
            setStyle({ opacity: Number((e.target as HTMLInputElement).value) })
          }
          onTouchEnd={(e) =>
            setStyle({ opacity: Number((e.target as HTMLInputElement).value) })
          }
          disabled={busy}
          className="w-full"
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Tamaño (fracción ancho 0–1)">
          <TextInput
            type="number"
            min="0"
            max="1"
            step="0.05"
            defaultValue={overlay?.width_frac ?? 0.2}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v <= 1)
                setStyle({ width_frac: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Rotación (grados)">
          <TextInput
            type="number"
            step="5"
            defaultValue={overlay?.rotation ?? 0}
            onBlur={(e) => setStyle({ rotation: Number(e.target.value) || 0 })}
            disabled={busy}
          />
        </Field>
        <Field label="Posición X (0–1)">
          <TextInput
            type="number"
            min="0"
            max="1"
            step="0.05"
            defaultValue={overlay?.anchor_x ?? 0.5}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v <= 1)
                setStyle({ anchor_x: v });
            }}
            disabled={busy}
          />
        </Field>
        <Field label="Posición Y (0–1)">
          <TextInput
            type="number"
            min="0"
            max="1"
            step="0.05"
            defaultValue={overlay?.anchor_y ?? 0.5}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v <= 1)
                setStyle({ anchor_y: v });
            }}
            disabled={busy}
          />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-xs text-neutral-300">
        <input
          type="checkbox"
          defaultChecked={overlay?.tile ?? false}
          onChange={(e) => setStyle({ tile: e.target.checked })}
          disabled={busy}
        />
        Mosaico (marca de agua repetida)
      </label>
    </div>
  );
}

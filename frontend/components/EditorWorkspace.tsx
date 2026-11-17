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
  addMarker,
  applyEditOp,
  assetDownloadUrl,
  batchDeleteClips,
  batchMoveClips,
  copyAttributes,
  enqueueEditExport,
  exportSubtitles,
  getEditProject,
  importSubtitles,
  latestEditExport,
  listAssets,
  openEditor,
  putEditProject,
  redoEdit,
  removeMarker,
  undoEdit,
  uploadOverlay,
  videoDownloadUrl,
  type Asset,
  type CopyableAttr,
  type Job,
  type SubtitleFormat,
} from "@/lib/api";
import {
  compileEditInputProps,
  type EditInputProps,
} from "@/lib/editor/compose";
import { FONT_CATALOG, findFont } from "@/lib/editor/fonts";
import {
  dragToAnchor,
  dragToSubtitleOffset,
  type PlayerRect,
} from "@/lib/editor/drag";
import { collectSnapCandidates, snapTime } from "@/lib/editor/snap";
import {
  editProjectDurationS,
  findClip,
  isAudioKind,
  isVisualKind,
  VISUAL_HIDE_KINDS,
  type EditClip,
  type EditMarker,
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

// Estado del arrastre en curso sobre el preview (FEAT-008). Guarda el clip arrastrado y
// su posición provisional, ya mapeada por las funciones puras de drag.ts: ancla (fracción
// del frame) para título/overlay o desplazamiento en píxeles para subtítulo.
interface DragState {
  clipId: string;
  kind: "title" | "overlay" | "subtitle";
  anchor_x?: number;
  anchor_y?: number;
  x?: number;
  y?: number;
}

// Estilos por defecto (espejo de los defaults de model.py) para construir una posición
// provisional durante el arrastre cuando el clip aún no tiene estilo explícito.
function defaultTitleStyle(): NonNullable<EditClip["title"]> {
  return {
    font_family: "Inter, sans-serif",
    font_size: 96,
    color: "#FFFFFF",
    bold: true,
    align: "center",
    anchor_x: 0.5,
    anchor_y: 0.5,
    kind: "plain",
    outline_color: "#000000",
    outline_width: 0,
    shadow: false,
    shadow_color: "#000000",
    shadow_blur: 0,
    shadow_x: 0,
    shadow_y: 0,
    bg_opacity: 1,
    bg_padding: 0.2,
    bg_radius: 8,
  };
}

function defaultOverlayStyle(): NonNullable<EditClip["overlay"]> {
  return {
    opacity: 1,
    anchor_x: 0.5,
    anchor_y: 0.5,
    width_frac: 0.2,
    rotation: 0,
    tile: false,
  };
}

function defaultSubtitleStyle(): NonNullable<EditClip["style"]> {
  return {
    font_family: "Inter, sans-serif",
    font_size: 48,
    color: "#FFFFFF",
    bg_color: null,
    bold: true,
    outline: true,
    position: "bottom",
    x: 0,
    y: 0,
    preset: "classic",
    outline_color: "#000000",
    outline_width: 0,
    shadow: false,
    shadow_color: "#000000",
    shadow_blur: 0,
    shadow_x: 0,
    shadow_y: 0,
    bg_opacity: 1,
    bg_padding: 0.2,
    bg_radius: 8,
    karaoke: false,
    highlight_color: "#FFE34D",
    highlight_scale: 1,
  };
}

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
  // Selección múltiple (FEAT-004): además del clip activo (selectedClipId, que alimenta el
  // inspector) se guarda el conjunto de clips seleccionados para las operaciones batch. El
  // clip activo siempre está incluido en la selección.
  const [selectedClipIds, setSelectedClipIds] = useState<string[]>([]);
  // Ajuste magnético (snap) on/off (FEAT-003). Cuando está activo, los movimientos batch
  // de la barra de selección se pegan al borde de clip/marcador más cercano.
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [assets, setAssets] = useState<Asset[]>([]);

  // Arrastre en curso del clip seleccionado sobre el preview (FEAT-008). Mientras el
  // usuario arrastra, guardamos aquí la posición provisional para re-renderizar el Player
  // en tiempo real SIN pegar al backend; al soltar se persiste UNA sola op set_position.
  const [drag, setDrag] = useState<DragState | null>(null);
  const dragLayerRef = useRef<HTMLDivElement>(null);

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

  // Proyecto efectivo para el preview: el documento real + la posición provisional del
  // arrastre en curso (si lo hay). Así el Player refleja el movimiento en tiempo real sin
  // tocar el backend; al soltar se persiste y el documento real pasa a tener esa posición.
  const previewProject = useMemo(() => {
    if (!project || !drag) return project;
    const next: EditProject = structuredClone(project);
    const found = findClip(next, drag.clipId);
    if (!found) return project;
    const clip = found.clip;
    if (drag.kind === "title") {
      clip.title = {
        ...(clip.title ?? defaultTitleStyle()),
        anchor_x: drag.anchor_x ?? clip.title?.anchor_x ?? 0.5,
        anchor_y: drag.anchor_y ?? clip.title?.anchor_y ?? 0.5,
      };
    } else if (drag.kind === "overlay") {
      clip.overlay = {
        ...(clip.overlay ?? defaultOverlayStyle()),
        anchor_x: drag.anchor_x ?? clip.overlay?.anchor_x ?? 0.5,
        anchor_y: drag.anchor_y ?? clip.overlay?.anchor_y ?? 0.5,
      };
    } else if (drag.kind === "subtitle") {
      clip.style = {
        ...(clip.style ?? defaultSubtitleStyle()),
        x: drag.x ?? clip.style?.x ?? 0,
        y: drag.y ?? clip.style?.y ?? 0,
      };
    }
    return next;
  }, [project, drag]);

  const inputProps: EditInputProps | null = useMemo(() => {
    if (!previewProject) return null;
    return compileEditInputProps(previewProject, resolveSrc, false);
  }, [previewProject, resolveSrc]);

  const durationS = project ? editProjectDurationS(project) : 0;

  const selected = useMemo(() => {
    if (!project || !selectedClipId) return null;
    return findClip(project, selectedClipId);
  }, [project, selectedClipId]);

  // ¿El clip seleccionado es posicionable por arrastre? (título/overlay/subtítulo).
  const draggableKind: DragState["kind"] | null = useMemo(() => {
    const k = selected?.clip.kind;
    if (k === "title" || k === "overlay" || k === "subtitle") return k;
    return null;
  }, [selected]);

  // Selección en el timeline con modificadores (FEAT-004): click simple reemplaza la
  // selección; shift/ctrl/meta alternan el clip en la selección múltiple. El último clic
  // fija el clip activo (inspector). La selección se recorta a los clips que aún existen.
  const handleSelectClip = useCallback(
    (clipId: string, additive = false) => {
      setSelectedClipId(clipId);
      setSelectedClipIds((prev) => {
        if (!additive) return [clipId];
        if (prev.includes(clipId)) return prev.filter((id) => id !== clipId);
        return [...prev, clipId];
      });
    },
    [],
  );

  // Clips seleccionados que todavía existen en el documento (robusto a borrados/undo).
  const selectedClips = useMemo(() => {
    if (!project) return [] as { track: EditTrack; clip: EditClip }[];
    const out: { track: EditTrack; clip: EditClip }[] = [];
    for (const id of selectedClipIds) {
      const found = findClip(project, id);
      if (found) out.push(found);
    }
    return out;
  }, [project, selectedClipIds]);

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

  // ------- selección múltiple: operaciones batch (FEAT-004) ------- //

  // Elimina la selección entera en UN gesto (op batch_delete => una Version). Limpia la
  // selección tras aplicar.
  const handleBatchDelete = useCallback(async () => {
    const ids = selectedClips.map((s) => s.clip.id);
    if (ids.length === 0) return;
    setBusy(true);
    setActionError(null);
    try {
      const read = await batchDeleteClips(projectId, ids);
      setProject(read.edit_project);
      setSelectedClipIds([]);
      setSelectedClipId(null);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "No se pudo eliminar la selección",
      );
    } finally {
      setBusy(false);
    }
  }, [projectId, selectedClips]);

  // Desplaza la selección por delta_s en UN gesto (op batch_move => una Version). Si el
  // snap está activo, ajusta el destino del clip MÁS TEMPRANO al borde/marcador más cercano
  // y aplica ese delta real a toda la selección (conserva offsets relativos).
  const handleBatchMove = useCallback(
    async (deltaS: number) => {
      if (!project || selectedClips.length === 0) return;
      const ids = selectedClips.map((s) => s.clip.id);
      let delta = deltaS;
      if (snapEnabled) {
        const anchor = selectedClips.reduce(
          (min, s) => Math.min(min, s.clip.start_s),
          Infinity,
        );
        const candidates = collectSnapCandidates(
          project,
          ids.length === 1 ? ids[0] : null,
        );
        const snapped = snapTime(anchor + deltaS, candidates, 0.25);
        delta = Math.round((snapped - anchor) * 1000) / 1000;
      }
      // No permitir que ningún clip cruce a negativo (lo valida también el backend).
      const minStart = selectedClips.reduce(
        (min, s) => Math.min(min, s.clip.start_s),
        Infinity,
      );
      if (minStart + delta < 0) delta = -minStart;
      if (delta === 0) return;
      setBusy(true);
      setActionError(null);
      try {
        const read = await batchMoveClips(projectId, ids, delta);
        setProject(read.edit_project);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "No se pudo mover la selección",
        );
      } finally {
        setBusy(false);
      }
    },
    [project, projectId, selectedClips, snapEnabled],
  );

  // Copia los atributos del clip activo a los demás seleccionados (op copy_attributes =>
  // una Version). El backend copia a cada destino solo lo compatible con su tipo.
  const handleCopyAttributes = useCallback(
    async (attrs: CopyableAttr[]) => {
      if (!selectedClipId) return;
      const targets = selectedClips
        .map((s) => s.clip.id)
        .filter((id) => id !== selectedClipId);
      if (targets.length === 0 || attrs.length === 0) return;
      setBusy(true);
      setActionError(null);
      try {
        const read = await copyAttributes(projectId, selectedClipId, targets, attrs);
        setProject(read.edit_project);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "No se pudieron copiar los atributos",
        );
      } finally {
        setBusy(false);
      }
    },
    [projectId, selectedClipId, selectedClips],
  );

  // ------- marcadores (FEAT-003) ------- //

  // Añade un marcador en el instante actual del Player (o en 0 si no hay posición).
  const handleAddMarker = useCallback(async () => {
    const frame = playerRef.current?.getCurrentFrame() ?? 0;
    const fps = project?.meta.fps ?? 30;
    const t_s = Math.max(0, Math.round((frame / fps) * 1000) / 1000);
    setBusy(true);
    setActionError(null);
    try {
      const read = await addMarker(projectId, t_s);
      setProject(read.edit_project);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "No se pudo añadir el marcador",
      );
    } finally {
      setBusy(false);
    }
  }, [project, projectId]);

  const handleRemoveMarker = useCallback(
    async (markerId: string) => {
      setBusy(true);
      setActionError(null);
      try {
        const read = await removeMarker(projectId, markerId);
        setProject(read.edit_project);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "No se pudo quitar el marcador",
        );
      } finally {
        setBusy(false);
      }
    },
    [projectId],
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

  // --------------------- drag&drop de posición (FEAT-008) --------------------- //

  // Mapea un evento de puntero a la posición del clip usando las funciones PURAS de
  // drag.ts (dragToAnchor para título/overlay; dragToSubtitleOffset para subtítulo).
  const computeDrag = useCallback(
    (clientX: number, clientY: number): DragState | null => {
      if (!project || !selected || !draggableKind) return null;
      const layer = dragLayerRef.current;
      if (!layer) return null;
      const r = layer.getBoundingClientRect();
      const rect: PlayerRect = {
        left: r.left,
        top: r.top,
        width: r.width,
        height: r.height,
      };
      if (draggableKind === "subtitle") {
        const pos = selected.clip.style?.position ?? "bottom";
        const { x, y } = dragToSubtitleOffset(
          clientX,
          clientY,
          rect,
          project.meta.width,
          project.meta.height,
          pos,
        );
        return { clipId: selected.clip.id, kind: "subtitle", x, y };
      }
      const { anchor_x, anchor_y } = dragToAnchor(clientX, clientY, rect);
      return { clipId: selected.clip.id, kind: draggableKind, anchor_x, anchor_y };
    },
    [project, selected, draggableKind],
  );

  const handleDragStart = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggableKind) return;
      e.preventDefault();
      (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
      const next = computeDrag(e.clientX, e.clientY);
      if (next) setDrag(next);
    },
    [draggableKind, computeDrag],
  );

  const handleDragMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!drag) return;
      const next = computeDrag(e.clientX, e.clientY);
      if (next) setDrag(next);
    },
    [drag, computeDrag],
  );

  // Al soltar: persiste UNA sola op set_position (una Version por gesto) y limpia el drag.
  const handleDragEnd = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!drag) return;
      (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
      const final = drag;
      setDrag(null);
      const params: Record<string, unknown> =
        final.kind === "subtitle"
          ? { clip_id: final.clipId, x: final.x, y: final.y }
          : {
              clip_id: final.clipId,
              anchor_x: final.anchor_x,
              anchor_y: final.anchor_y,
            };
      void runOp("set_position", params);
    },
    [drag, runOp],
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
          <button
            type="button"
            onClick={() => setSnapEnabled((v) => !v)}
            aria-pressed={snapEnabled}
            title="Ajuste magnético: pega los movimientos a bordes de clip y marcadores"
            className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
              snapEnabled
                ? "bg-emerald-500/20 text-emerald-300"
                : "bg-neutral-800 text-neutral-400 hover:text-neutral-200"
            }`}
          >
            🧲 Snap {snapEnabled ? "on" : "off"}
          </button>
          <Button
            variant="secondary"
            onClick={() => void handleAddMarker()}
            loading={busy}
          >
            ＋ Marcador
          </Button>
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
          <div
            ref={dragLayerRef}
            className="relative overflow-hidden rounded-lg border border-neutral-800 bg-black"
          >
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

            {/* Capa de arrastre transparente superpuesta al Player: solo activa cuando hay
                un clip de título/overlay/subtítulo seleccionado. onPointerMove mueve el
                elemento en el preview en tiempo real; onPointerUp persiste UNA op
                set_position (una Version por gesto). Deja los controles del Player (barra
                inferior) libres reservando el 12% inferior. */}
            {draggableKind && (
              <div
                className="absolute inset-x-0 top-0 cursor-move"
                style={{ height: "88%", touchAction: "none" }}
                onPointerDown={handleDragStart}
                onPointerMove={handleDragMove}
                onPointerUp={handleDragEnd}
                onPointerCancel={handleDragEnd}
                title="Arrastra para reposicionar el elemento seleccionado"
              />
            )}
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

      {/* Barra de selección múltiple (FEAT-004): eliminar / mover / copiar atributos. */}
      {selectedClips.length > 0 && (
        <SelectionBar
          selectedClips={selectedClips}
          activeClipId={selectedClipId}
          snapEnabled={snapEnabled}
          busy={busy}
          onClear={() => {
            setSelectedClipIds([]);
            setSelectedClipId(null);
          }}
          onBatchDelete={handleBatchDelete}
          onBatchMove={handleBatchMove}
          onCopyAttributes={handleCopyAttributes}
        />
      )}

      {/* Línea de tiempo multipista */}
      <Timeline
        project={project}
        durationS={durationS}
        selectedClipId={selectedClipId}
        selectedClipIds={selectedClipIds}
        onSelect={handleSelectClip}
        onRemoveMarker={handleRemoveMarker}
        busy={busy}
        onOp={runOp}
      />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Barra de selección múltiple (FEAT-004)
// --------------------------------------------------------------------------- //

// Acciones sobre la selección: cada una es UN gesto = UNA operación batch = UNA Version.
// - Eliminar: op batch_delete (atómico; rechaza pistas locked).
// - Mover ◀/▶: op batch_move por un delta fijo (ajustado por snap si está activo).
// - Copiar atributos: op copy_attributes del clip activo a los demás (por tipo compatible).
function SelectionBar({
  selectedClips,
  activeClipId,
  snapEnabled,
  busy,
  onClear,
  onBatchDelete,
  onBatchMove,
  onCopyAttributes,
}: {
  selectedClips: { track: EditTrack; clip: EditClip }[];
  activeClipId: string | null;
  snapEnabled: boolean;
  busy: boolean;
  onClear: () => void;
  onBatchDelete: () => void | Promise<void>;
  onBatchMove: (deltaS: number) => void | Promise<void>;
  onCopyAttributes: (attrs: CopyableAttr[]) => void | Promise<void>;
}) {
  const count = selectedClips.length;
  const active = selectedClips.find((s) => s.clip.id === activeClipId) ?? null;
  // Atributos copiables aplicables al TIPO del clip activo (lo que el usuario puede pegar
  // al resto de la selección). El backend igualmente filtra por el tipo de cada destino.
  const attrsForKind: Record<string, CopyableAttr[]> = {
    video: ["transform", "transition"],
    image: ["transform", "transition"],
    voice: ["gain", "fade"],
    music: ["gain", "fade"],
    sfx: ["gain", "fade"],
    subtitle: ["style"],
    title: ["title"],
    overlay: ["overlay"],
  };
  const copyable = active ? attrsForKind[active.clip.kind] ?? [] : [];
  const hasTargets = count > 1 && activeClipId != null;

  return (
    <Card className="flex flex-wrap items-center gap-3">
      <span className="text-sm font-medium text-neutral-200">
        {count} clip{count === 1 ? "" : "s"} seleccionado{count === 1 ? "" : "s"}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="secondary"
          onClick={() => void onBatchMove(-0.5)}
          loading={busy}
          title="Mover selección 0,5 s a la izquierda"
        >
          ◀ 0,5 s
        </Button>
        <Button
          variant="secondary"
          onClick={() => void onBatchMove(0.5)}
          loading={busy}
          title="Mover selección 0,5 s a la derecha"
        >
          0,5 s ▶
        </Button>
        <span className="text-xs text-neutral-500">
          {snapEnabled ? "(con snap)" : ""}
        </span>
      </div>
      {hasTargets && copyable.length > 0 && (
        <div className="flex items-center gap-1">
          <span className="text-xs text-neutral-500">
            Copiar del activo a la selección:
          </span>
          {copyable.map((attr) => (
            <Button
              key={attr}
              variant="secondary"
              onClick={() => void onCopyAttributes([attr])}
              loading={busy}
              title={`Pegar ${attr} del clip activo al resto de la selección`}
            >
              {attr}
            </Button>
          ))}
        </div>
      )}
      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="danger"
          onClick={() => void onBatchDelete()}
          loading={busy}
        >
          Eliminar selección
        </Button>
        <Button variant="secondary" onClick={onClear} disabled={busy}>
          Deseleccionar
        </Button>
      </div>
    </Card>
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

// Controles por pista (candado / ojo / altavoz) — FEAT-002. Cada botón alterna una
// bandera de la pista con la operación `set_track_flags` (una sola op cubre las tres):
// - locked (candado): protege la edición; las operaciones que mutan clips la rechazan.
// - hidden (ojo): oculta una pista VISUAL en preview/export (no en el timeline).
// - muted (altavoz): silencia una pista de AUDIO.
// El ojo solo se muestra en pistas visuales y el altavoz solo en pistas de audio (cada
// bandera solo tiene efecto en su dominio); el candado está en todas.
function TrackFlagControls({
  track,
  busy,
  onOp,
}: {
  track: EditTrack;
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const isVisual = (VISUAL_HIDE_KINDS as readonly string[]).includes(track.kind);
  const isAudio = isAudioKind(track.kind);
  const toggle = (flag: "locked" | "hidden" | "muted", value: boolean) =>
    void onOp("set_track_flags", { track_id: track.id, [flag]: value });
  const btn =
    "rounded px-1.5 py-0.5 text-[11px] leading-none transition-colors disabled:opacity-40";

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        disabled={busy}
        aria-pressed={track.locked}
        title={track.locked ? "Pista bloqueada: desbloquear" : "Bloquear pista"}
        onClick={() => toggle("locked", !track.locked)}
        className={`${btn} ${
          track.locked
            ? "bg-amber-500/20 text-amber-300"
            : "bg-neutral-800 text-neutral-400 hover:text-neutral-200"
        }`}
      >
        {track.locked ? "🔒" : "🔓"}
      </button>
      {isVisual && (
        <button
          type="button"
          disabled={busy}
          aria-pressed={track.hidden}
          title={track.hidden ? "Pista oculta: mostrar" : "Ocultar pista"}
          onClick={() => toggle("hidden", !track.hidden)}
          className={`${btn} ${
            track.hidden
              ? "bg-sky-500/20 text-sky-300"
              : "bg-neutral-800 text-neutral-400 hover:text-neutral-200"
          }`}
        >
          {track.hidden ? "🚫" : "👁"}
        </button>
      )}
      {isAudio && (
        <button
          type="button"
          disabled={busy}
          aria-pressed={track.muted}
          title={track.muted ? "Pista silenciada: activar" : "Silenciar pista"}
          onClick={() => toggle("muted", !track.muted)}
          className={`${btn} ${
            track.muted
              ? "bg-rose-500/20 text-rose-300"
              : "bg-neutral-800 text-neutral-400 hover:text-neutral-200"
          }`}
        >
          {track.muted ? "🔇" : "🔊"}
        </button>
      )}
    </div>
  );
}

function Timeline({
  project,
  durationS,
  selectedClipId,
  selectedClipIds,
  onSelect,
  onRemoveMarker,
  busy,
  onOp,
}: {
  project: EditProject;
  durationS: number;
  selectedClipId: string | null;
  selectedClipIds: string[];
  onSelect: (id: string, additive?: boolean) => void;
  onRemoveMarker: (markerId: string) => void | Promise<void>;
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const scale = durationS > 0 ? 100 / durationS : 0; // % por segundo
  const tracks = [...project.tracks].sort((a, b) => a.order - b.order);
  const markers: EditMarker[] = project.markers ?? [];
  const selectedSet = new Set(selectedClipIds);

  return (
    <Card className="flex flex-col gap-2">
      <h2 className="font-medium text-neutral-100">Línea de tiempo</h2>
      <p className="text-xs text-neutral-500">
        Mantén Mayús o Ctrl/⌘ al hacer clic para seleccionar varios clips.
      </p>
      {/* Regla con marcadores (FEAT-003): puntos etiquetados que el snap usa como anclas. */}
      <div className="flex items-center gap-3">
        <div className="w-32 shrink-0 text-xs text-neutral-500">Marcadores</div>
        <div className="relative h-6 flex-1 rounded-md border border-neutral-800 bg-neutral-950">
          {markers.map((marker) => {
            const left = durationS > 0 ? Math.max(0, Math.min(100, marker.t_s * scale)) : 0;
            return (
              <button
                key={marker.id}
                type="button"
                disabled={busy}
                onClick={() => void onRemoveMarker(marker.id)}
                title={`${marker.label || "marcador"} · ${marker.t_s.toFixed(2)}s (clic para quitar)`}
                className="absolute top-0 bottom-0 w-0.5 -translate-x-1/2 disabled:opacity-40"
                style={{ left: `${left}%`, backgroundColor: marker.color }}
              >
                <span
                  className="absolute -top-0.5 left-1/2 h-2 w-2 -translate-x-1/2 rotate-45"
                  style={{ backgroundColor: marker.color }}
                />
              </button>
            );
          })}
          {markers.length === 0 && (
            <span className="absolute left-2 top-1 text-[10px] text-neutral-600">
              Usa «＋ Marcador» para marcar el instante actual
            </span>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-2">
        {tracks.map((track) => (
          <div key={track.id} className="flex items-center gap-3">
            <div className="flex w-32 shrink-0 flex-col gap-1">
              <div className="truncate text-xs text-neutral-400">
                {track.name || track.kind}
                {track.locked && (
                  <span className="ml-1 text-amber-400">(bloqueada)</span>
                )}
              </div>
              <TrackFlagControls track={track} busy={busy} onOp={onOp} />
            </div>
            <div
              className={`relative h-9 flex-1 overflow-hidden rounded-md border bg-neutral-950 ${
                track.locked ? "border-amber-500/60" : "border-neutral-800"
              } ${track.hidden ? "opacity-50" : ""}`}
            >
              {track.clips.map((clip) => {
                const left = Math.max(0, clip.start_s * scale);
                const width = Math.max(1.5, clip.duration_s * scale);
                const isActive = clip.id === selectedClipId;
                const isSel = selectedSet.has(clip.id);
                return (
                  <button
                    key={clip.id}
                    type="button"
                    onClick={(e) =>
                      onSelect(clip.id, e.shiftKey || e.ctrlKey || e.metaKey)
                    }
                    title={`${clip.kind} · ${clip.start_s.toFixed(2)}s → ${(
                      clip.start_s + clip.duration_s
                    ).toFixed(2)}s`}
                    className={`absolute top-0.5 bottom-0.5 flex items-center overflow-hidden rounded px-1.5 text-[10px] font-medium text-white/90 ${
                      TRACK_COLORS[track.kind] ?? "bg-neutral-600"
                    } ${
                      isActive
                        ? "ring-2 ring-white"
                        : isSel
                          ? "ring-2 ring-sky-300"
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
// Selector de tipografía del catálogo cerrado (FEAT-007)
// --------------------------------------------------------------------------- //

// <select> de familias del FONT_CATALOG (NUNCA texto libre): garantiza que font_family
// sea una familia del catálogo, cargable por el loader de Remotion, de modo que preview y
// export muestren la MISMA tipografía. El valor que se guarda y viaja a inputProps es el
// `css` de la entrada (p. ej. "Inter, sans-serif"); el <select> casa el valor actual con
// su familia canónica aunque el documento guarde el CSS.
function FontSelect({
  value,
  busy,
  onChange,
}: {
  value: string | undefined;
  busy: boolean;
  onChange: (css: string) => void;
}) {
  const current = findFont(value ?? "") ?? FONT_CATALOG[0];
  return (
    <Select
      value={current.family}
      onChange={(e) => {
        const entry = findFont(e.target.value);
        if (entry) onChange(entry.css);
      }}
      disabled={busy}
    >
      {FONT_CATALOG.map((f) => (
        <option key={f.family} value={f.family}>
          {f.family}
        </option>
      ))}
    </Select>
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

      {/* Velocidad / congelar fotograma / desvincular audio (FEAT-006 / FEAT-005). */}
      <SpeedFreezeControls clip={clip} busy={busy} onOp={onOp} />
    </div>
  );
}

// Controles de velocidad (rate + invertir), congelar fotograma (at_s/hold_s) y, para
// clips de vídeo, desvincular el audio de origen a una pista voice. Cada control llama a la
// operación pura correspondiente (set_speed/set_freeze/detach_audio), que versiona y se
// propaga a preview/export por igual.
function SpeedFreezeControls({
  clip,
  busy,
  onOp,
}: {
  clip: EditClip;
  busy: boolean;
  onOp: (op: string, params: Record<string, unknown>) => void | Promise<void>;
}) {
  const rate = clip.speed?.rate ?? 1;
  const reverse = clip.speed?.reverse ?? false;
  const freeze = clip.freeze;
  const isVideo = clip.kind === "video";

  return (
    <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3">
      <h4 className="text-xs font-semibold uppercase text-neutral-500">
        Velocidad y congelación
      </h4>
      <div className="grid grid-cols-2 gap-3">
        <Field label={`Velocidad (${rate.toFixed(2)}×)`}>
          <TextInput
            type="number"
            step="0.25"
            min="0.1"
            defaultValue={rate}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v > 0 && v !== rate)
                void onOp("set_speed", { clip_id: clip.id, rate: v });
            }}
            disabled={busy}
          />
        </Field>
        {isVideo && (
          <Field label="Invertir (export)">
            <Button
              variant={reverse ? "primary" : "secondary"}
              loading={busy}
              onClick={() =>
                void onOp("set_speed", { clip_id: clip.id, reverse: !reverse })
              }
            >
              {reverse ? "Invertido ✓" : "Invertir"}
            </Button>
          </Field>
        )}
      </div>

      {isVideo && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Congelar en (s del origen)">
            <TextInput
              type="number"
              step="0.1"
              min="0"
              defaultValue={freeze?.at_s ?? clip.source_in_s}
              onBlur={(e) => {
                const atS = Number(e.target.value);
                const holdS = freeze?.hold_s ?? 1;
                if (Number.isFinite(atS))
                  void onOp("set_freeze", {
                    clip_id: clip.id,
                    at_s: atS,
                    hold_s: holdS,
                  });
              }}
              disabled={busy}
            />
          </Field>
          <Field label="Mantener (s); 0 = quitar">
            <TextInput
              type="number"
              step="0.1"
              min="0"
              defaultValue={freeze?.hold_s ?? 0}
              onBlur={(e) => {
                const holdS = Number(e.target.value);
                const atS = freeze?.at_s ?? clip.source_in_s;
                if (Number.isFinite(holdS))
                  void onOp("set_freeze", {
                    clip_id: clip.id,
                    at_s: atS,
                    hold_s: holdS,
                  });
              }}
              disabled={busy}
            />
          </Field>
        </div>
      )}

      {isVideo && (
        <Button
          variant="secondary"
          loading={busy}
          disabled={clip.mute_source_audio}
          onClick={() => void onOp("detach_audio", { clip_id: clip.id })}
        >
          {clip.mute_source_audio ? "Audio desvinculado ✓" : "Desvincular audio"}
        </Button>
      )}
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
        <Field label="Offset X (px)" hint="Arrastra el texto en el preview o ajústalo aquí">
          <TextInput
            type="number"
            step="10"
            defaultValue={style?.x ?? 0}
            onBlur={(e) =>
              void onOp("set_position", {
                clip_id: clip.id,
                x: Number(e.target.value) || 0,
              })
            }
            disabled={busy}
          />
        </Field>
        <Field label="Offset Y (px)">
          <TextInput
            type="number"
            step="10"
            defaultValue={style?.y ?? 0}
            onBlur={(e) =>
              void onOp("set_position", {
                clip_id: clip.id,
                y: Number(e.target.value) || 0,
              })
            }
            disabled={busy}
          />
        </Field>
        <Field label="Tipografía" hint="Catálogo de fuentes con licencia libre">
          <FontSelect
            value={style?.font_family}
            busy={busy}
            onChange={(css) => setStyle({ font_family: css })}
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
        <Field label="Tipografía" hint="Catálogo de fuentes con licencia libre">
          <FontSelect
            value={title?.font_family}
            busy={busy}
            onChange={(css) => setStyle({ font_family: css })}
          />
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

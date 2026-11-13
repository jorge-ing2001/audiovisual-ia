"use client";

// Tarjeta de escena del storyboard (FEAT-007 + FEAT-B).
//
// Edición inline (PATCH /scenes/{id}) y las CINCO opciones de imagen por escena, cada
// una conectada a una llamada REAL a la API (sin botones decorativos):
//   1. Generar imagen    -> POST /scenes/{id}/image  (force=false, usa cache del motor)
//   2. Regenerar imagen  -> POST /scenes/{id}/image  (force=true, alternativa nueva)
//   3. Subir imagen      -> POST /scenes/{id}/image/upload  (multipart, imagen propia)
//   4. Reemplazar imagen -> menú que ofrece Regenerar o Subir para ESA escena (aislado)
//   5. Previsualizar     -> modal con la imagen asociada o con la preview local del File
//      elegido (URL.createObjectURL) antes de confirmar la subida.

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  assetDownloadUrl,
  generateSceneImage,
  regenerateSceneImage,
  updateScene,
  uploadSceneImage,
  type Asset,
  type Scene,
} from "@/lib/api";
import {
  Button,
  ErrorBanner,
  Field,
  Modal,
  StatusBadge,
  TextArea,
  TextInput,
  statusTone,
} from "@/components/ui";

interface SceneCardProps {
  scene: Scene;
  image: Asset | null;
  onSceneUpdated: (scene: Scene) => void;
  onImageRegenerated: (asset: Asset) => void;
}

export default function SceneCard({
  scene,
  image,
  onSceneUpdated,
  onImageRegenerated,
}: SceneCardProps) {
  const [narration, setNarration] = useState(scene.narration_text);
  const [visualPrompt, setVisualPrompt] = useState(scene.visual_prompt);
  const [duration, setDuration] = useState(String(scene.duration_s));

  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [replaceOpen, setReplaceOpen] = useState(false);

  // Previsualización: imagen ya asociada (por id) o preview local del File elegido.
  const [previewOpen, setPreviewOpen] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const dirty =
    narration !== scene.narration_text ||
    visualPrompt !== scene.visual_prompt ||
    duration !== String(scene.duration_s);

  const busy = generating || regenerating || uploading;

  // Libera la object URL de la preview local cuando cambia o se desmonta.
  useEffect(() => {
    return () => {
      if (pendingUrl) URL.revokeObjectURL(pendingUrl);
    };
  }, [pendingUrl]);

  async function handleSave() {
    const parsedDuration = Number(duration);
    if (Number.isNaN(parsedDuration) || parsedDuration <= 0) {
      setError("La duración debe ser un número mayor que 0.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateScene(scene.id, {
        narration_text: narration,
        visual_prompt: visualPrompt,
        duration_s: parsedDuration,
      });
      onSceneUpdated(updated);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar la escena");
    } finally {
      setSaving(false);
    }
  }

  // 1. Generar imagen (primera vez; reutiliza cache del motor con force=false).
  async function handleGenerate() {
    setGenerating(true);
    setError(null);
    try {
      const asset = await generateSceneImage(scene.id, { force: false });
      onImageRegenerated(asset);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo generar la imagen");
    } finally {
      setGenerating(false);
    }
  }

  // 2. Regenerar imagen (alternativa nueva; force=true). También usada por «Reemplazar».
  async function handleRegenerate() {
    setRegenerating(true);
    setError(null);
    try {
      const asset = await regenerateSceneImage(scene.id, { force: true });
      onImageRegenerated(asset);
      setSavedAt(Date.now());
      setReplaceOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo regenerar la imagen");
    } finally {
      setRegenerating(false);
    }
  }

  // 3/5. El usuario elige un fichero: NO se sube todavía; se muestra preview + confirmar.
  function handleFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    // Permite volver a elegir el mismo fichero más tarde.
    e.target.value = "";
    if (!file) return;
    if (pendingUrl) URL.revokeObjectURL(pendingUrl);
    setError(null);
    setPendingFile(file);
    setPendingUrl(URL.createObjectURL(file));
    setPreviewOpen(true);
    setReplaceOpen(false);
  }

  // 3/4. Confirmar la subida de la imagen propia (reemplazo aislado de ESTA escena).
  async function handleConfirmUpload() {
    if (!pendingFile) return;
    setUploading(true);
    setError(null);
    try {
      const asset = await uploadSceneImage(scene.id, pendingFile);
      onImageRegenerated(asset);
      setSavedAt(Date.now());
      closePreview();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo subir la imagen");
    } finally {
      setUploading(false);
    }
  }

  function closePreview() {
    setPreviewOpen(false);
    if (pendingUrl) URL.revokeObjectURL(pendingUrl);
    setPendingUrl(null);
    setPendingFile(null);
  }

  function openUploadPicker() {
    fileInputRef.current?.click();
  }

  // Cache-buster para refrescar la <img> tras generar/subir (el path puede repetirse).
  const imageSrc = image ? `${assetDownloadUrl(image.id)}?v=${image.id}-${savedAt ?? ""}` : null;
  const hasImage = Boolean(image);

  return (
    <div className="grid gap-4 rounded-xl border border-neutral-800 bg-neutral-900/60 p-5 md:grid-cols-[220px_1fr]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-neutral-200">
            Escena {scene.order + 1}
          </span>
          <StatusBadge tone={statusTone(scene.status)}>{scene.status}</StatusBadge>
        </div>
        <button
          type="button"
          onClick={() => hasImage && setPreviewOpen(true)}
          disabled={!hasImage}
          className="relative aspect-video w-full overflow-hidden rounded-lg border border-neutral-800 bg-neutral-950 enabled:cursor-zoom-in"
          title={hasImage ? "Previsualizar imagen" : undefined}
        >
          {imageSrc ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageSrc}
              alt={`Imagen de la escena ${scene.order + 1}`}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full items-center justify-center p-3 text-center text-xs text-neutral-500">
              Sin imagen. Pulsa «Generar imagen» o «Subir imagen».
            </div>
          )}
          {image?.provider === "upload" && (
            <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
              Propia
            </span>
          )}
        </button>

        {/* Entrada de fichero oculta compartida por «Subir» y «Reemplazar». */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          className="hidden"
          onChange={handleFilePicked}
        />

        <div className="grid grid-cols-2 gap-2">
          {/* 1. Generar imagen */}
          <Button
            variant="primary"
            onClick={() => void handleGenerate()}
            loading={generating}
            disabled={busy}
          >
            Generar
          </Button>
          {/* 2. Regenerar imagen */}
          <Button
            variant="secondary"
            onClick={() => void handleRegenerate()}
            loading={regenerating}
            disabled={busy}
          >
            Regenerar
          </Button>
          {/* 3. Subir imagen (abre selector; luego previsualiza antes de confirmar) */}
          <Button
            variant="secondary"
            onClick={openUploadPicker}
            loading={uploading}
            disabled={busy}
          >
            Subir
          </Button>
          {/* 4. Reemplazar imagen (ofrece regenerar o subir, solo esta escena) */}
          <Button
            variant="secondary"
            onClick={() => setReplaceOpen(true)}
            disabled={busy || !hasImage}
            title={hasImage ? undefined : "Primero genera o sube una imagen"}
          >
            Reemplazar
          </Button>
        </div>
        {/* 5. Previsualizar imagen asociada */}
        <Button
          variant="ghost"
          onClick={() => setPreviewOpen(true)}
          disabled={!hasImage || busy}
        >
          Previsualizar
        </Button>
      </div>

      <div className="flex flex-col gap-3">
        <Field label="Narración" htmlFor={`narration-${scene.id}`}>
          <TextArea
            id={`narration-${scene.id}`}
            rows={2}
            value={narration}
            onChange={(e) => setNarration(e.target.value)}
          />
        </Field>
        <Field label="Prompt visual" htmlFor={`visual-${scene.id}`}>
          <TextArea
            id={`visual-${scene.id}`}
            rows={2}
            value={visualPrompt}
            onChange={(e) => setVisualPrompt(e.target.value)}
          />
        </Field>
        <div className="flex items-end gap-3">
          <Field label="Duración (s)" htmlFor={`duration-${scene.id}`}>
            <TextInput
              id={`duration-${scene.id}`}
              type="number"
              min="0.1"
              step="0.1"
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
              className="w-28"
            />
          </Field>
          <Button
            onClick={() => void handleSave()}
            loading={saving}
            disabled={!dirty}
            className="mb-[1px]"
          >
            Guardar cambios
          </Button>
          {!dirty && savedAt && (
            <span className="mb-2 text-xs text-green-400">Guardado</span>
          )}
        </div>
        {error && <ErrorBanner message={error} />}
      </div>

      {/* Modal de «Reemplazar»: elegir entre regenerar o subir (solo esta escena). */}
      <Modal
        open={replaceOpen}
        onClose={() => setReplaceOpen(false)}
        title={`Reemplazar imagen de la escena ${scene.order + 1}`}
        footer={
          <Button variant="ghost" onClick={() => setReplaceOpen(false)}>
            Cancelar
          </Button>
        }
      >
        <p className="mb-4 text-sm text-neutral-400">
          Cambia el recurso visual de esta escena sin afectar al resto del proyecto. Elige
          cómo quieres reemplazarlo:
        </p>
        <div className="flex flex-col gap-2">
          <Button
            variant="secondary"
            onClick={() => void handleRegenerate()}
            loading={regenerating}
            disabled={busy}
          >
            Regenerar con el motor
          </Button>
          <Button variant="secondary" onClick={openUploadPicker} disabled={busy}>
            Subir una imagen propia
          </Button>
        </div>
      </Modal>

      {/* Modal de «Previsualizar»: preview local del File elegido (con confirmar/cancelar)
          o visualización en grande de la imagen ya asociada. */}
      <Modal
        open={previewOpen}
        onClose={pendingFile ? closePreview : () => setPreviewOpen(false)}
        title={
          pendingFile
            ? `Previsualizar «${pendingFile.name}»`
            : `Imagen de la escena ${scene.order + 1}`
        }
        footer={
          pendingFile ? (
            <>
              <Button variant="ghost" onClick={closePreview} disabled={uploading}>
                Cancelar
              </Button>
              <Button onClick={() => void handleConfirmUpload()} loading={uploading}>
                Confirmar y usar
              </Button>
            </>
          ) : (
            <Button variant="ghost" onClick={() => setPreviewOpen(false)}>
              Cerrar
            </Button>
          )
        }
      >
        <div className="flex items-center justify-center bg-neutral-950">
          {pendingUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={pendingUrl}
              alt={`Previsualización de ${pendingFile?.name ?? "imagen"}`}
              className="max-h-[70vh] w-auto object-contain"
            />
          ) : imageSrc ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageSrc}
              alt={`Imagen de la escena ${scene.order + 1}`}
              className="max-h-[70vh] w-auto object-contain"
            />
          ) : (
            <p className="p-6 text-sm text-neutral-500">No hay imagen para previsualizar.</p>
          )}
        </div>
        {error && (
          <div className="mt-3">
            <ErrorBanner message={error} />
          </div>
        )}
      </Modal>
    </div>
  );
}

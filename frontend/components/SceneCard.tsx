"use client";

// Tarjeta de escena del storyboard: muestra la imagen (si existe), permite editar
// narración/prompt visual/duración (PATCH /scenes/{id}) y regenerar la imagen
// (POST /scenes/{id}/image). Cada botón dispara una llamada real a la API.

import { useState } from "react";

import {
  ApiError,
  assetDownloadUrl,
  regenerateSceneImage,
  updateScene,
  type Asset,
  type Scene,
} from "@/lib/api";
import {
  Button,
  ErrorBanner,
  Field,
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
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const dirty =
    narration !== scene.narration_text ||
    visualPrompt !== scene.visual_prompt ||
    duration !== String(scene.duration_s);

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

  async function handleRegenerate() {
    setRegenerating(true);
    setError(null);
    try {
      const asset = await regenerateSceneImage(scene.id, { force: true });
      onImageRegenerated(asset);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo regenerar la imagen");
    } finally {
      setRegenerating(false);
    }
  }

  // Cache-buster para refrescar la <img> tras regenerar (mismo path en disco).
  const imageSrc = image ? `${assetDownloadUrl(image.id)}?v=${image.id}-${savedAt ?? ""}` : null;

  return (
    <div className="grid gap-4 rounded-xl border border-neutral-800 bg-neutral-900/60 p-5 md:grid-cols-[220px_1fr]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-neutral-200">
            Escena {scene.order + 1}
          </span>
          <StatusBadge tone={statusTone(scene.status)}>{scene.status}</StatusBadge>
        </div>
        <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-neutral-800 bg-neutral-950">
          {imageSrc ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageSrc}
              alt={`Imagen de la escena ${scene.order + 1}`}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full items-center justify-center p-3 text-center text-xs text-neutral-500">
              Sin imagen. Pulsa «Regenerar imagen».
            </div>
          )}
        </div>
        <Button
          variant="secondary"
          onClick={() => void handleRegenerate()}
          loading={regenerating}
        >
          Regenerar imagen
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
    </div>
  );
}

"use client";

// Storyboard: lista las escenas del proyecto (GET /projects/{id}/scenes) junto con
// sus imágenes (GET /projects/{id}/assets?kind=image) y delega la edición/regeneración
// en SceneCard. Expone un `refresh` para recargar tras generar el vídeo.

import { useCallback, useEffect, useImperativeHandle, useState, forwardRef } from "react";

import {
  ApiError,
  listAssets,
  listScenes,
  type Asset,
  type Scene,
} from "@/lib/api";
import { ErrorBanner, Spinner } from "@/components/ui";
import SceneCard from "@/components/SceneCard";

export interface StoryboardHandle {
  refresh: () => void;
}

interface StoryboardProps {
  projectId: number;
}

const Storyboard = forwardRef<StoryboardHandle, StoryboardProps>(
  function Storyboard({ projectId }, ref) {
    const [scenes, setScenes] = useState<Scene[] | null>(null);
    const [images, setImages] = useState<Record<number, Asset>>({});
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
      try {
        setError(null);
        const [sceneList, imageAssets] = await Promise.all([
          listScenes(projectId),
          listAssets(projectId, "image"),
        ]);
        setScenes(sceneList);
        // Nos quedamos con el asset de imagen más reciente por escena.
        const byScene: Record<number, Asset> = {};
        for (const asset of imageAssets) {
          if (asset.scene_id === null) continue;
          const current = byScene[asset.scene_id];
          if (!current || asset.id > current.id) byScene[asset.scene_id] = asset;
        }
        setImages(byScene);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Error cargando el storyboard");
        setScenes([]);
      }
    }, [projectId]);

    useEffect(() => {
      void load();
    }, [load]);

    useImperativeHandle(ref, () => ({ refresh: () => void load() }), [load]);

    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-neutral-100">Storyboard</h2>
          {scenes && scenes.length > 0 && (
            <span className="text-sm text-neutral-500">{scenes.length} escenas</span>
          )}
        </div>

        {error && <ErrorBanner message={error} />}

        {scenes === null && (
          <div className="flex items-center gap-2 text-neutral-400">
            <Spinner /> Cargando escenas…
          </div>
        )}

        {scenes !== null && scenes.length === 0 && !error && (
          <p className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-5 text-sm text-neutral-400">
            Todavía no hay escenas. Genera el vídeo desde el prompt para crear el guion y
            las escenas.
          </p>
        )}

        <div className="grid gap-4">
          {scenes?.map((scene) => (
            <SceneCard
              key={scene.id}
              scene={scene}
              image={images[scene.id] ?? null}
              onSceneUpdated={(updated) =>
                setScenes(
                  (prev) =>
                    prev?.map((s) => (s.id === updated.id ? updated : s)) ?? prev,
                )
              }
              onImageRegenerated={(asset) =>
                asset.scene_id !== null &&
                setImages((prev) => ({ ...prev, [asset.scene_id as number]: asset }))
              }
            />
          ))}
        </div>
      </div>
    );
  },
);

export default Storyboard;

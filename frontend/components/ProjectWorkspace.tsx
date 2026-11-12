"use client";

// Espacio de trabajo de un proyecto: compone el editor de prompt/generación, el
// storyboard de escenas y el panel de render. Carga el proyecto por id
// (GET /projects/{id}) y coordina el refresco del storyboard tras generar.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ApiError, getProject, type Project } from "@/lib/api";
import { ErrorBanner, Spinner, StatusBadge, statusTone } from "@/components/ui";
import GeneratePanel from "@/components/GeneratePanel";
import RenderPanel from "@/components/RenderPanel";
import Storyboard, { type StoryboardHandle } from "@/components/Storyboard";

export default function ProjectWorkspace({ projectId }: { projectId: number }) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const storyboardRef = useRef<StoryboardHandle>(null);

  useEffect(() => {
    let cancelled = false;
    getProject(projectId)
      .then((p) => {
        if (!cancelled) setProject(p);
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof ApiError ? err.message : "No se pudo cargar el proyecto");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (error) {
    return (
      <div className="flex flex-col gap-4">
        <ErrorBanner message={error} />
        <Link href="/" className="text-sm text-neutral-400 hover:text-neutral-200">
          ← Volver al dashboard
        </Link>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex items-center gap-2 text-neutral-400">
        <Spinner /> Cargando proyecto…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <Link href="/" className="text-sm text-neutral-400 hover:text-neutral-200">
          ← Dashboard
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-neutral-100">{project.name}</h1>
          <StatusBadge tone={statusTone(project.status)}>{project.status}</StatusBadge>
          <span className="text-sm text-neutral-500">
            {project.aspect_ratio} · {project.resolution} · {project.production_type}
          </span>
        </div>
      </header>

      <GeneratePanel
        project={project}
        onProjectUpdated={setProject}
        onGenerationFinished={() => storyboardRef.current?.refresh()}
      />

      <Storyboard ref={storyboardRef} projectId={projectId} />

      <RenderPanel projectId={projectId} />
    </div>
  );
}

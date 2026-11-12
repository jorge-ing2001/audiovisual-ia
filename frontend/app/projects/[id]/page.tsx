import { notFound } from "next/navigation";

import ProjectWorkspace from "@/components/ProjectWorkspace";

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const projectId = Number(id);
  if (!Number.isInteger(projectId) || projectId <= 0) {
    notFound();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-8 p-6 md:p-10">
      <ProjectWorkspace projectId={projectId} />
    </main>
  );
}

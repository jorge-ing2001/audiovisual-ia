import { notFound } from "next/navigation";

import EditorWorkspace from "@/components/EditorWorkspace";

export default async function EditorPage({
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
    <main className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 p-4 md:p-8">
      <EditorWorkspace projectId={projectId} />
    </main>
  );
}

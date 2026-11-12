import HealthStatus from "@/components/HealthStatus";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 p-8">
      <header>
        <h1 className="text-3xl font-bold">audiovisual-ia</h1>
        <p className="mt-2 text-neutral-400">
          Plataforma prompt-to-video 2D — Fase 1 (MVP), modo local/gratuito.
        </p>
      </header>
      <HealthStatus />
    </main>
  );
}

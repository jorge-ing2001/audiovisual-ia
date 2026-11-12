import Dashboard from "@/components/Dashboard";
import HealthStatus from "@/components/HealthStatus";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-8 p-6 md:p-10">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold">audiovisual-ia</h1>
        <p className="text-neutral-400">
          Plataforma prompt-to-video 2D — Fase 1 (MVP), modo local/gratuito.
        </p>
      </header>
      <HealthStatus />
      <Dashboard />
    </main>
  );
}

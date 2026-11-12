import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "audiovisual-ia",
  description: "Plataforma prompt-to-video 2D (Fase 1 / MVP) — modo local/gratuito.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}

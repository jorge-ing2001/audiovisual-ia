// Render de la composición "Timeline" a partir de un timeline JSON (FEAT-005).
//
// Uso:  node scripts/render.mjs --timeline <ruta/timeline.json> --out <ruta/out.mp4>
//
// Lee el timeline JSON (producido por el backend), lo empaqueta como inputProps,
// selecciona la composición "Timeline" (su duración/fps/resolución se derivan del
// propio timeline vía calculateMetadata) y renderiza el vídeo. El render real usa
// @remotion/renderer, que descarga/usa un Chromium headless y exige glibc 2.35+:
// en entornos sin navegador este script falla con un mensaje claro y el backend
// propaga el error (el test de integración se salta con razón).
//
// Este script NO mezcla ni muxea el audio: eso lo hace el backend (AudioMixer + FFmpeg)
// sobre el vídeo que aquí se produce.

import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");

const parseArgs = (argv) => {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "--timeline") args.timeline = argv[++i];
    else if (token === "--out") args.out = argv[++i];
    else if (token === "--codec") args.codec = argv[++i];
    else if (token === "--public-dir") args.publicDir = argv[++i];
    else if (token === "--composition") args.composition = argv[++i];
  }
  return args;
};

const run = async () => {
  const args = parseArgs(process.argv.slice(2));
  if (!args.timeline || !args.out) {
    throw new Error(
      "uso: node scripts/render.mjs --timeline <timeline.json> --out <out.mp4>",
    );
  }

  const timelinePath = path.resolve(args.timeline);
  const outPath = path.resolve(args.out);
  const codec = args.codec ?? "h264";
  // Composición a renderizar: "Timeline" (generación) por defecto; el editor pasa "Edit".
  const compositionId = args.composition ?? "Timeline";

  const inputProps = JSON.parse(fs.readFileSync(timelinePath, "utf-8"));
  fs.mkdirSync(path.dirname(outPath), { recursive: true });

  // publicDir: raíz desde la que `staticFile(...)` resuelve las imágenes locales de las
  // escenas (el backend pasa AIV_MEDIA_ROOT y emite rutas relativas a esa raíz). El
  // navegador headless no puede cargar file:// ni rutas absolutas de disco.
  const bundleOptions = { entryPoint: path.join(projectRoot, "src", "index.ts") };
  if (args.publicDir) {
    bundleOptions.publicDir = path.resolve(args.publicDir);
  }
  const serveUrl = await bundle(bundleOptions);

  const composition = await selectComposition({
    serveUrl,
    id: compositionId,
    inputProps,
  });

  await renderMedia({
    composition,
    serveUrl,
    codec,
    outputLocation: outPath,
    inputProps,
  });

  process.stdout.write(`${outPath}\n`);
};

run().catch((err) => {
  console.error(err?.stack || String(err));
  process.exit(1);
});

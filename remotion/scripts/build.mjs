// Build del proyecto Remotion (FEAT-005): empaqueta el bundle de la composición.
//
// Este paso NO necesita navegador headless ni glibc 2.35+ (eso solo lo exige el
// render real con @remotion/renderer), por lo que sirve como verificación de que
// el proyecto compila y las composiciones son descubribles. Produce `build/bundle`.
//
// Uso:  node scripts/build.mjs

import { bundle } from "@remotion/bundler";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");

const run = async () => {
  const entry = path.join(projectRoot, "src", "index.ts");
  const outDir = path.join(projectRoot, "build", "bundle");

  const bundleLocation = await bundle({
    entryPoint: entry,
    outDir,
    // El timeline se pasa como inputProps en tiempo de render; aquí solo compilamos.
    onProgress: (progress) => {
      if (progress % 25 === 0) {
        process.stdout.write(`bundling ${progress}%\n`);
      }
    },
  });

  process.stdout.write(`bundle listo en ${bundleLocation}\n`);
};

run().catch((err) => {
  console.error(err);
  process.exit(1);
});

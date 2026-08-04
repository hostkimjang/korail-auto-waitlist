import { build } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";

const outputDirectory = new URL("../dist/", import.meta.url);
await rm(outputDirectory, { recursive: true, force: true });
await mkdir(outputDirectory, { recursive: true });
await build({
  entryPoints: [
    "src/content.ts",
    "src/app-bridge.ts",
    "src/background.ts",
    "src/popup.ts",
    "src/options.ts",
  ],
  outdir: "dist",
  bundle: true,
  format: "esm",
  target: "chrome120",
  sourcemap: true,
  legalComments: "none",
});
for (const filename of ["manifest.json", "popup.html", "options.html", "ui.css"]) {
  await cp(filename, new URL(filename, outputDirectory));
}

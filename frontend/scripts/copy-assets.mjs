// Before each build: pdf.js into public/pdfjs (lib/api.ts pdfFacts: page count and password check in
// the browser before upload) and the full-screen comparison / redaction editor (redaction-review.*).
import { cpSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "node_modules", "pdfjs-dist");
const target = join(root, "public", "pdfjs");
mkdirSync(target, { recursive: true });
for (const name of ["pdf.mjs", "pdf.worker.mjs"]) cpSync(join(source, "build", name), join(target, name));
for (const name of ["cmaps", "standard_fonts", "wasm", "iccs"]) cpSync(join(source, name), join(target, name), { recursive: true });
cpSync(join(source, "LICENSE"), join(target, "LICENSE"));
const editor = join(root, "..", "cloud", "ui");
for (const name of ["redaction-review.html", "redaction-review.css", "redaction-review.mjs"])
  cpSync(join(editor, name), join(root, "public", name));
console.log("pdf.js and the review editor copied to public/");

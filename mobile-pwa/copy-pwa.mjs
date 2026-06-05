#!/usr/bin/env node
// Copy pwa/dist/ → mobile-pwa/www/ for Capacitor sync.
// Run after `cd pwa && npm run build`.
import { existsSync, cpSync, rmSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(__dirname, "..", "pwa", "dist");
const DST = resolve(__dirname, "www");

if (!existsSync(SRC)) {
  console.error(`✗ pwa/dist not found at ${SRC}`);
  console.error("  Run: cd pwa && npm run build");
  process.exit(1);
}

if (existsSync(DST)) {
  rmSync(DST, { recursive: true, force: true });
}
mkdirSync(DST, { recursive: true });
cpSync(SRC, DST, { recursive: true });
console.log(`✓ Copied pwa/dist → mobile-pwa/www`);

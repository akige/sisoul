/**
 * PWA manifest.json 静态检查
 */
import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PWA_DIR = resolve(__dirname, "../..");
const MANIFEST = resolve(PWA_DIR, "public/manifest.json");

describe("manifest.json", () => {
  it("exists", () => {
    expect(existsSync(MANIFEST)).toBe(true);
  });

  it("is valid JSON", () => {
    const content = readFileSync(MANIFEST, "utf-8");
    expect(() => JSON.parse(content)).not.toThrow();
  });

  it("has required PWA fields", () => {
    const manifest = JSON.parse(readFileSync(MANIFEST, "utf-8"));
    expect(manifest.name).toBeTruthy();
    expect(manifest.short_name).toBeTruthy();
    expect(manifest.start_url).toBeTruthy();
    expect(manifest.display).toBe("standalone");
    expect(manifest.icons).toBeInstanceOf(Array);
    expect(manifest.icons.length).toBeGreaterThanOrEqual(2);
  });

  it("has theme_color", () => {
    const manifest = JSON.parse(readFileSync(MANIFEST, "utf-8"));
    expect(manifest.theme_color).toBe("#0b0d12");
  });

  it("has icons with 192 and 512", () => {
    const manifest = JSON.parse(readFileSync(MANIFEST, "utf-8"));
    const sizes = manifest.icons.map((i: any) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
  });
});

describe("service worker (public/sw.js)", () => {
  const SW = resolve(PWA_DIR, "public/sw.js");

  it("exists", () => {
    expect(existsSync(SW)).toBe(true);
  });

  it("has cache strategy logic", () => {
    const content = readFileSync(SW, "utf-8");
    expect(content).toContain("fetch");
    expect(content).toContain("caches");
  });

  it("handles /sisoul/ API endpoints", () => {
    const content = readFileSync(SW, "utf-8");
    expect(content).toContain("/sisoul/");
  });
});

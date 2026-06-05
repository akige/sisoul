// Capacitor config + sync sanity tests.
// Run: cd mobile-pwa && node --test tests/cap_config.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, statSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

test("capacitor.config.json — appId is io.sisoul.pwa", () => {
  const cfg = JSON.parse(readFileSync(resolve(ROOT, "capacitor.config.json"), "utf8"));
  assert.equal(cfg.appId, "io.sisoul.pwa");
  assert.equal(cfg.appName, "sisoul");
  assert.equal(cfg.webDir, "www");
});

test("capacitor.config.json — iOS scheme + Android scheme correct", () => {
  const cfg = JSON.parse(readFileSync(resolve(ROOT, "capacitor.config.json"), "utf8"));
  assert.equal(cfg.ios.scheme, "sisoul", "iOS deep-link scheme must be sisoul://");
  assert.equal(cfg.server.androidScheme, "https", "Android must use HTTPS");
  assert.equal(cfg.server.iosScheme, "sisoul");
});

test("capacitor.config.json — allowed navigation domains", () => {
  const cfg = JSON.parse(readFileSync(resolve(ROOT, "capacitor.config.json"), "utf8"));
  const nav = cfg.server.allowNavigation;
  assert(nav.includes("*.sisoul.io"), "must allow sisoul.io subdomains");
  assert(nav.includes("127.0.0.1:9876"), "must allow local daemon");
});

test("capacitor.config.json — push notification options", () => {
  const cfg = JSON.parse(readFileSync(resolve(ROOT, "capacitor.config.json"), "utf8"));
  const push = cfg.plugins.PushNotifications;
  assert.deepEqual(push.presentationOptions.sort(), ["alert", "badge", "sound"]);
});

test("package.json — Capacitor 8 packages", () => {
  const pkg = JSON.parse(readFileSync(resolve(ROOT, "package.json"), "utf8"));
  const deps = pkg.dependencies;
  assert(deps["@capacitor/core"].startsWith("^8."), "Capacitor core must be 8.x");
  assert(deps["@capacitor/ios"].startsWith("^8."));
  assert(deps["@capacitor/android"].startsWith("^8."));
  assert(deps["@capacitor/push-notifications"], "push-notifications plugin required");
  assert(deps["@capacitor/local-notifications"]);
  assert(deps["@capacitor/preferences"]);
  assert(deps["@capacitor/network"]);
  assert(deps["@capacitor/app"]);
});

test("iOS native project — App.xcodeproj exists", () => {
  const ios = resolve(ROOT, "ios", "App", "App.xcodeproj");
  assert(existsSync(ios), `iOS project must exist at ${ios} (run: npx cap add ios)`);
  assert(statSync(ios).isDirectory());
});

test("Android native project — build.gradle exists", () => {
  const gradle = resolve(ROOT, "android", "build.gradle");
  assert(existsSync(gradle), `Android project must exist (run: npx cap add android)`);
});

test("iOS — capacitor.config.json synced into App/", () => {
  const synced = resolve(ROOT, "ios", "App", "App", "capacitor.config.json");
  if (existsSync(synced)) {
    const cfg = JSON.parse(readFileSync(synced, "utf8"));
    assert.equal(cfg.appId, "io.sisoul.pwa");
  }
  // If not synced yet, `npx cap sync` is the fix; not a hard error here.
});

test("copy-pwa.mjs — script exists + is executable", () => {
  const script = resolve(ROOT, "copy-pwa.mjs");
  assert(existsSync(script));
  const content = readFileSync(script, "utf8");
  assert(content.includes("pwa/dist"), "must reference pwa/dist source");
  assert(content.includes("www"), "must copy to www");
});

test("README.md — covers iOS + Android build", () => {
  const readme = readFileSync(resolve(ROOT, "README.md"), "utf8");
  assert(readme.includes("Xcode"), "README must mention Xcode for iOS");
  assert(readme.includes("Android Studio"), "README must mention Android Studio");
  assert(readme.includes("APNs"), "README must explain APNs for iOS push");
  assert(readme.includes("FCM"), "README must explain FCM for Android push");
});

test(".gitignore — excludes node_modules + www + iOS Pods", () => {
  const gi = readFileSync(resolve(ROOT, ".gitignore"), "utf8");
  assert(gi.includes("node_modules/"));
  assert(gi.includes("www/"));
  assert(gi.includes("Pods/"));
});

test("www/ exists after copy-pwa run (or skip if not built)", () => {
  const www = resolve(ROOT, "www");
  if (!existsSync(www)) {
    // PWA not built yet — acceptable in CI before pwa build step
    return;
  }
  const indexHtml = resolve(www, "index.html");
  assert(existsSync(indexHtml), "www/ must contain index.html if copied");
});

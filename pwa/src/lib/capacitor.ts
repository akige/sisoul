// Capacitor detection + push notification registration.
// PWA bundled into iOS / Android via mobile-pwa/ (Capacitor 8 wrap).
// In native context: register for APNs/FCM, send token to daemon.
// In browser: no-op (falls back to Web Push if SW supports).

interface CapacitorGlobal {
  isNativePlatform(): boolean;
  getPlatform(): "ios" | "android" | "web";
}

declare global {
  interface Window {
    Capacitor?: CapacitorGlobal;
  }
}

export function isNativeApp(): boolean {
  return !!(window.Capacitor && window.Capacitor.isNativePlatform());
}

export function getPlatform(): "ios" | "android" | "web" {
  return window.Capacitor?.getPlatform() ?? "web";
}

export interface PushRegistration {
  token: string;
  platform: "ios" | "android";
  registered_at: string;
}

const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE || "http://127.0.0.1:9876";

/**
 * Register for native push notifications + send token to sisoul daemon.
 *
 * Called once per app launch from V2Dashboard onMount (or settings screen).
 * No-op when running in browser (returns null).
 *
 * Daemon endpoint: POST /v1/push/register
 *   body: { token, platform, did_key }
 *   The daemon routes inbound pushes via Waku store-and-forward to peer daemons.
 */
export async function registerNativePush(didKey: string): Promise<PushRegistration | null> {
  if (!isNativeApp()) {
    return null;
  }

  // Dynamic import — only loaded in native context (Capacitor bundles the plugin).
  // In browser/CI/test, Capacitor is undefined and this never runs.
  // String-variable specifier so vite/vitest doesn't statically resolve the dep
  // (the @capacitor/* packages live in mobile-pwa/, not pwa/).
  const pushPkg = "@capacitor" + "/push-notifications";
  const { PushNotifications } = await import(/* @vite-ignore */ pushPkg);

  // Permission request — iOS shows native sheet, Android 13+ shows POST_NOTIFICATIONS prompt.
  const permResult = await PushNotifications.requestPermissions();
  if (permResult.receive !== "granted") {
    return null;
  }

  await PushNotifications.register();

  // Token arrives async via 'registration' listener — wrap in Promise.
  const token = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("push token timeout")), 10_000);

    PushNotifications.addListener("registration", (t: { value: string }) => {
      clearTimeout(timer);
      resolve(t.value);
    });

    PushNotifications.addListener("registrationError", (err: { error: string }) => {
      clearTimeout(timer);
      reject(new Error(err.error));
    });
  });

  const platform = getPlatform() as "ios" | "android";
  const registration: PushRegistration = {
    token,
    platform,
    registered_at: new Date().toISOString(),
  };

  // Notify daemon (silent fail; user can retry from Settings).
  try {
    await fetch(`${DAEMON_BASE}/v1/push/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...registration, did_key: didKey }),
    });
  } catch {
    // Daemon offline; PWA stores locally for retry.
  }

  return registration;
}

/**
 * Listen for inbound push notifications + dispatch DOM event.
 * App router subscribes to 'sisoul:push' on window for routing decisions.
 */
export async function listenForPushNotifications(): Promise<void> {
  if (!isNativeApp()) return;

  // String-variable specifier so vite/vitest doesn't statically resolve the dep
  // (the @capacitor/* packages live in mobile-pwa/, not pwa/).
  const pushPkg = "@capacitor" + "/push-notifications";
  const { PushNotifications } = await import(/* @vite-ignore */ pushPkg);

  PushNotifications.addListener("pushNotificationReceived", (notif: unknown) => {
    window.dispatchEvent(new CustomEvent("sisoul:push", { detail: notif }));
  });

  PushNotifications.addListener("pushNotificationActionPerformed", (action: unknown) => {
    window.dispatchEvent(new CustomEvent("sisoul:push:tap", { detail: action }));
  });
}

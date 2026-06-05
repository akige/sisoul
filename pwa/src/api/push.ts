// PWA push device registration client (real /v1/push/* endpoints).
const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE || "http://127.0.0.1:9876";

export interface PushDevice {
  token: string;
  platform: "ios" | "android";
  did_key: string | null;
  registered_at: string;
  last_seen_at: string;
}

export interface PushRegisterResponse {
  success: boolean;
  device: PushDevice;
  is_new: boolean;
}

export interface PushDevicesResponse {
  devices: PushDevice[];
  count: number;
}

export interface PushTestResponse {
  sent: number;
  devices_targeted: string[];
  note: string;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${DAEMON_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!resp.ok) {
    throw new Error(`${path} → ${resp.status} ${resp.statusText}`);
  }
  return (await resp.json()) as T;
}

export const registerPushDevice = (
  token: string,
  platform: "ios" | "android",
  didKey?: string,
) =>
  api<PushRegisterResponse>("/v1/push/register", {
    method: "POST",
    body: JSON.stringify({ token, platform, did_key: didKey }),
  });

export const listPushDevices = (didKey?: string) => {
  const q = didKey ? `?did_key=${encodeURIComponent(didKey)}` : "";
  return api<PushDevicesResponse>(`/v1/push/devices${q}`);
};

export const unregisterPushDevice = (token: string) =>
  api<{ success: boolean; removed: number }>(
    `/v1/push/devices/${encodeURIComponent(token)}`,
    { method: "DELETE" },
  );

export const sendTestPush = (title: string, body: string, targetDid?: string) =>
  api<PushTestResponse>("/v1/push/test", {
    method: "POST",
    body: JSON.stringify({ title, body, target_did: targetDid }),
  });

// PWA v3 daemon API client. 真接 /v3/* endpoints (RSI Recursive Self-Improvement).
const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE ||
  (typeof window !== "undefined" && window.location.pathname.startsWith("/app")
    ? window.location.origin // daemon 托管 (任意端口) → 同源
    : "http://127.0.0.1:9876");

export interface RSIStatus {
  framework: string;
  version: string;
  components: Record<string, string>;
  safety_boundary_active: boolean;
}

export interface RSIIterateRequest {
  mode: "godel" | "alpha_evolve" | "dspy";
  target_module?: string;
  dry_run?: boolean;
}

export interface RSIIterateResponse {
  iteration_id: string;
  mode: string;
  started_at: string;
  accepted: boolean;
  fitness: number | null;
  candidate_count: number;
  reason: string;
}

export interface RSIHistoryEntry {
  iteration_id: string;
  mode: string;
  started_at: string;
  accepted: boolean;
  fitness: number | null;
}

export interface RSIHistory {
  iterations: RSIHistoryEntry[];
  count: number;
}

export interface RSIGossipResponse {
  broadcast: boolean;
  envelope: Record<string, unknown> | null;
  error: string;
}

export interface RSIPeersResponse {
  peer_mutations: Record<string, unknown>[];
  count: number;
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

export const getRSIStatus = () => api<RSIStatus>("/v3/rsi/status");

export const runRSIIterate = (req: RSIIterateRequest) =>
  api<RSIIterateResponse>("/v3/rsi/iterate", {
    method: "POST",
    body: JSON.stringify(req),
  });

export const getRSIHistory = () => api<RSIHistory>("/v3/rsi/history");

export const gossipRSIMutation = (mutation: Record<string, unknown>) =>
  api<RSIGossipResponse>("/v3/rsi/gossip", {
    method: "POST",
    body: JSON.stringify({ mutation }),
  });

export const getRSIPeers = () => api<RSIPeersResponse>("/v3/rsi/peers");

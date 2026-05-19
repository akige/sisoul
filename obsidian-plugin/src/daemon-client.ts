import { Notice, requestUrl, RequestUrlParam } from "obsidian";
import {
  DaemonError,
  DaemonHealth,
  FriendsResponse,
  GoalsResponse,
  PreferencesResponse,
  SkillsResponse,
  SyncStatus,
  SyncTriggerResponse,
} from "./types";

export interface DaemonClientOptions {
  baseUrl: string;
  token?: string;
  timeoutMs?: number;
  notifyUser?: boolean;
}

/**
 * Thin HTTP wrapper over the local sisoul daemon. Uses Obsidian's `requestUrl`
 * to bypass CORS. Surfaces network / 401 / 5xx as `DaemonError` and renders
 * a `Notice` toast when `notifyUser` is on (default true).
 */
export class DaemonClient {
  private baseUrl: string;
  private token: string;
  private timeoutMs: number;
  private notifyUser: boolean;
  private lastNoticeAt = 0;

  constructor(opts: DaemonClientOptions) {
    this.baseUrl = this.normalizeBase(opts.baseUrl);
    this.token = opts.token ?? "";
    this.timeoutMs = opts.timeoutMs ?? 8000;
    this.notifyUser = opts.notifyUser ?? true;
  }

  setBaseUrl(url: string): void {
    this.baseUrl = this.normalizeBase(url);
  }

  setToken(token: string): void {
    this.token = token;
  }

  // --- public endpoint methods (5 categories) -----------------------------

  health(): Promise<DaemonHealth> {
    return this.get<DaemonHealth>("/sisoul/health");
  }

  preferences(): Promise<PreferencesResponse> {
    return this.get<PreferencesResponse>("/sisoul/preferences");
  }

  goals(): Promise<GoalsResponse> {
    return this.get<GoalsResponse>("/sisoul/goals");
  }

  friends(): Promise<FriendsResponse> {
    return this.get<FriendsResponse>("/sisoul/friends");
  }

  skills(): Promise<SkillsResponse> {
    return this.get<SkillsResponse>("/sisoul/skills");
  }

  syncStatus(): Promise<SyncStatus> {
    return this.get<SyncStatus>("/sisoul/sync/status");
  }

  triggerSync(): Promise<SyncTriggerResponse> {
    return this.post<SyncTriggerResponse>("/sisoul/sync", {});
  }

  // --- HTTP plumbing ------------------------------------------------------

  private normalizeBase(url: string): string {
    let v = (url || "http://127.0.0.1:9876").trim();
    if (v.endsWith("/")) v = v.slice(0, -1);
    return v;
  }

  private async get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  private async request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (method === "POST") headers["Content-Type"] = "application/json";

    const params: RequestUrlParam = {
      url,
      method,
      headers,
      throw: false,
      body: method === "POST" ? JSON.stringify(body ?? {}) : undefined,
    };

    let resp;
    try {
      resp = await this.withTimeout(requestUrl(params), this.timeoutMs);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      const lower = msg.toLowerCase();
      const kind =
        lower.includes("timeout") || lower.includes("aborted")
          ? "timeout"
          : "network";
      const e = new DaemonError(kind, null, `sisoul daemon unreachable: ${msg}`);
      this.toast(`Sisoul daemon unreachable (${kind}). Is sisoul-daemon running on ${this.baseUrl}?`);
      throw e;
    }

    const status = resp.status;
    if (status === 401 || status === 403) {
      this.toast("Sisoul daemon rejected token (401/403). Check API token in settings.");
      throw new DaemonError("auth", status, `auth failed: ${status}`);
    }
    if (status >= 500) {
      this.toast(`Sisoul daemon 5xx (${status}). Check daemon logs.`);
      throw new DaemonError("server", status, `server error: ${status}`);
    }
    if (status >= 400) {
      throw new DaemonError("client", status, `client error: ${status} ${resp.text?.slice(0, 200) ?? ""}`);
    }

    try {
      return resp.json as T;
    } catch (err) {
      throw new DaemonError("server", status, `invalid JSON: ${String(err)}`);
    }
  }

  private withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`timeout after ${ms}ms`)), ms);
      p.then(
        (v) => {
          clearTimeout(t);
          resolve(v);
        },
        (e) => {
          clearTimeout(t);
          reject(e);
        },
      );
    });
  }

  private toast(msg: string): void {
    if (!this.notifyUser) return;
    // throttle to once per 10s so repeated polls don't spam user
    const now = Date.now();
    if (now - this.lastNoticeAt < 10_000) return;
    this.lastNoticeAt = now;
    new Notice(msg, 6000);
  }
}

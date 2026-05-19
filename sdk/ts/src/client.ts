// SisoulClient - 顶层 daemon 客户端
// 用法:
//   const c = new SisoulClient({ baseUrl: "http://localhost:8088/sisoul" });
//   const prefs = await c.vault.list();
//   const skills = await c.skills.list();

import type { ClientConfig } from "./types.js";
import { NetworkError, TimeoutError, classifyHttpError } from "./errors.js";
import { VaultAPI } from "./vault.js";
import { GoalsAPI } from "./goals.js";
import { FriendsAPI } from "./friends.js";
import { SkillsAPI } from "./skills.js";
import { AttestAPI } from "./attest.js";

export const DEFAULT_BASE_URL = "/sisoul";
export const DEFAULT_TIMEOUT_MS = 30_000;

export class SisoulClient {
  public readonly baseUrl: string;
  public readonly timeout: number;
  private readonly fetchImpl: typeof fetch;
  private readonly defaultHeaders: Record<string, string>;

  public readonly vault: VaultAPI;
  public readonly goals: GoalsAPI;
  public readonly friends: FriendsAPI;
  public readonly skills: SkillsAPI;
  public readonly attest: AttestAPI;

  constructor(config: ClientConfig = {}) {
    this.baseUrl = (config.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, "");
    this.timeout = config.timeout ?? DEFAULT_TIMEOUT_MS;
    this.fetchImpl = config.fetchImpl ?? (globalThis.fetch?.bind(globalThis));
    if (!this.fetchImpl) {
      throw new Error("no fetch impl available - pass config.fetchImpl");
    }
    this.defaultHeaders = {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(config.headers ?? {}),
    };

    this.vault = new VaultAPI(this);
    this.goals = new GoalsAPI(this);
    this.friends = new FriendsAPI(this);
    this.skills = new SkillsAPI(this);
    this.attest = new AttestAPI(this);
  }

  /**
   * Low-level request. 路径必须以 "/" 开头, 会拼到 baseUrl 后面.
   * absolute=true 时直接拿 path 当 URL 用 (适配 /sisoul/skill/* 已含前缀的路由).
   */
  async request<T>(
    path: string,
    options: RequestInit & { absolute?: boolean } = {}
  ): Promise<T> {
    const { absolute = false, ...init } = options;
    const url = absolute ? path : `${this.baseUrl}${path}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    let resp: Response;
    try {
      resp = await this.fetchImpl(url, {
        ...init,
        headers: { ...this.defaultHeaders, ...(init.headers ?? {}) },
        signal: init.signal ?? controller.signal,
      });
    } catch (err: unknown) {
      clearTimeout(timer);
      const e = err as { name?: string; message?: string };
      if (e?.name === "AbortError") {
        throw new TimeoutError(this.timeout);
      }
      throw new NetworkError(`fetch ${url} failed: ${e?.message ?? String(err)}`, err);
    }
    clearTimeout(timer);

    if (!resp.ok) {
      let body: string | undefined;
      try {
        body = await resp.text();
      } catch {
        // 忽略
      }
      throw classifyHttpError(resp.status, path, body);
    }

    if (resp.status === 204) return undefined as T;

    const ct = resp.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) {
      // 容忍 daemon 偶尔返 text
      const text = await resp.text();
      return text as unknown as T;
    }
    return (await resp.json()) as T;
  }

  async get<T>(path: string, opts?: { absolute?: boolean }): Promise<T> {
    return this.request<T>(path, { method: "GET", absolute: opts?.absolute });
  }

  async post<T>(path: string, body?: unknown, opts?: { absolute?: boolean }): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      absolute: opts?.absolute,
    });
  }

  async delete<T>(path: string, opts?: { absolute?: boolean }): Promise<T> {
    return this.request<T>(path, { method: "DELETE", absolute: opts?.absolute });
  }
}

// Vault API - daemon preferences endpoints
// list  - GET /preferences/list
// get   - GET /preferences/get?key=...
// set   - POST /preferences/set { key, value }
// delete - POST /preferences/delete { key }

import type { SisoulClient } from "./client.js";
import type {
  ListPreferencesResponse,
  Preference,
  VaultGetResponse,
} from "./types.js";

export class VaultAPI {
  constructor(private readonly client: SisoulClient) {}

  async list(): Promise<Preference[]> {
    const resp = await this.client.get<ListPreferencesResponse>("/preferences/list");
    return resp.items ?? [];
  }

  async get(key: string): Promise<string | null> {
    if (!key) throw new Error("vault.get: key required");
    const q = encodeURIComponent(key);
    const resp = await this.client.get<VaultGetResponse>(`/preferences/get?key=${q}`);
    return resp.value;
  }

  async set(key: string, value: string): Promise<void> {
    if (!key) throw new Error("vault.set: key required");
    await this.client.post<{ ok: boolean }>("/preferences/set", { key, value });
  }

  async delete(key: string): Promise<void> {
    if (!key) throw new Error("vault.delete: key required");
    await this.client.post<{ ok: boolean }>("/preferences/delete", { key });
  }

  /** 批量读 - daemon 没真 batch endpoint, 这里串行兜底, 后续 daemon 加 /preferences/multi-get 可换. */
  async multiGet(keys: string[]): Promise<Record<string, string | null>> {
    const out: Record<string, string | null> = {};
    for (const k of keys) {
      out[k] = await this.get(k);
    }
    return out;
  }
}

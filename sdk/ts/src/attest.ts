// Attest API - daemon /attest/* endpoints
// EAS schema attest 历史 + 创建

import type { SisoulClient } from "./client.js";
import type {
  AttestCreateRequest,
  AttestCreateResponse,
  AttestEntry,
  AttestHistoryResponse,
} from "./types.js";

export class AttestAPI {
  constructor(private readonly client: SisoulClient) {}

  async history(): Promise<AttestEntry[]> {
    const r = await this.client.get<AttestHistoryResponse>("/attest/history");
    return r.history ?? [];
  }

  async create(req: AttestCreateRequest): Promise<AttestCreateResponse> {
    if (!req.schema) throw new Error("attest.create: schema required");
    if (!req.subject_did) throw new Error("attest.create: subject_did required");
    return this.client.post<AttestCreateResponse>("/attest/create", req);
  }

  async bySchema(schema: string): Promise<AttestEntry[]> {
    const all = await this.history();
    return all.filter((e) => e.schema === schema);
  }

  async since(timestampSec: number): Promise<AttestEntry[]> {
    const all = await this.history();
    return all.filter((e) => e.timestamp >= timestampSec);
  }
}

// Skills API - §28 §3.6 packaging spec
// 注意: /sisoul/skill/* 路径已含 /sisoul 前缀, 走 absolute=true.

import type { SisoulClient } from "./client.js";
import type {
  SkillBorrowRequest,
  SkillBorrowResponse,
  SkillCreateRequest,
  SkillCreateResponse,
  SkillLendRequest,
  SkillLendResponse,
  SkillListResponse,
  SkillSessionsResponse,
  SkillItem,
  SkillSessionItem,
} from "./types.js";

export interface EndSessionResponse {
  session_id: string;
  status: string;
  destroy_reason?: string;
  destroyed_at?: number;
  ledger_entry_id?: string;
}

export class SkillsAPI {
  constructor(private readonly client: SisoulClient) {}

  async list(): Promise<SkillListResponse> {
    return this.client.get<SkillListResponse>("/sisoul/skill/list", { absolute: true });
  }

  async owned(): Promise<SkillItem[]> {
    return (await this.list()).owned;
  }

  async available(): Promise<SkillItem[]> {
    return (await this.list()).available_to_borrow;
  }

  async create(req: SkillCreateRequest): Promise<SkillCreateResponse> {
    if (!req.name) throw new Error("skills.create: name required");
    if (!req.system_prompt) throw new Error("skills.create: system_prompt required");
    return this.client.post<SkillCreateResponse>("/sisoul/skill/create", req, {
      absolute: true,
    });
  }

  async lend(req: SkillLendRequest): Promise<SkillLendResponse> {
    if (!req.skill_id) throw new Error("skills.lend: skill_id required");
    if (!req.permissions?.mode) throw new Error("skills.lend: permissions.mode required");
    return this.client.post<SkillLendResponse>("/sisoul/skill/lend", req, {
      absolute: true,
    });
  }

  async borrow(req: SkillBorrowRequest): Promise<SkillBorrowResponse> {
    if (!req.owner_did) throw new Error("skills.borrow: owner_did required");
    if (!req.qualified_name) throw new Error("skills.borrow: qualified_name required");
    return this.client.post<SkillBorrowResponse>("/sisoul/skill/borrow", req, {
      absolute: true,
    });
  }

  async sessions(): Promise<SkillSessionItem[]> {
    const r = await this.client.get<SkillSessionsResponse>("/sisoul/skill/sessions", {
      absolute: true,
    });
    return r.sessions ?? [];
  }

  async activeSessions(): Promise<SkillSessionItem[]> {
    return (await this.sessions()).filter((s) => s.status === "active");
  }

  async endSession(session_id: string, reason?: string): Promise<EndSessionResponse> {
    if (!session_id) throw new Error("skills.endSession: session_id required");
    return this.client.post<EndSessionResponse>(
      "/sisoul/skill/end-session",
      { session_id, reason },
      { absolute: true }
    );
  }
}

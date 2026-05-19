// sisoul daemon fetch wrapper · 类型 + 错误处理
export const DAEMON_BASE = "/sisoul";

export class DaemonError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "DaemonError";
  }
}

async function daemonFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${DAEMON_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    throw new DaemonError(resp.status, `daemon ${path} → ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

// skill API 路径已含 /sisoul 前缀 (跟 dev-A skill_router prefix 完全对齐),
// 不能再被 DAEMON_BASE 拼一次. 用 absoluteFetch 走绝对路径.
async function absoluteFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    throw new DaemonError(resp.status, `daemon ${path} → ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

// ── Preferences (Vault) ────────────────────────────────────────────────────
export interface Preference {
  key: string;
  value: string;
  updated_at: string;
}

export function listPreferences(): Promise<{ items: Preference[] }> {
  return daemonFetch("/preferences/list");
}

// ── Goals ─────────────────────────────────────────────────────────────────
export interface Goal {
  id: string;
  title: string;
  progress: number;
  deadline?: string;
  notes?: string;
}

export function listGoals(): Promise<{ goals: Goal[] }> {
  return daemonFetch("/goals/list");
}

// ── Chat History ──────────────────────────────────────────────────────────
export interface ChatSession {
  id: string;
  title: string;
  started_at: string;
  message_count: number;
}

export function listChatHistory(): Promise<{ sessions: ChatSession[] }> {
  return daemonFetch("/chat-history/list");
}

// ── Identity / Settings ────────────────────────────────────────────────────
export interface IdentityInfo {
  did: string;
  handle?: string;
  mnemonic_hint?: string;
  provider?: string;
}

export function getIdentity(): Promise<IdentityInfo> {
  return daemonFetch("/identity");
}

// ── Attestation / Advanced ─────────────────────────────────────────────────
export interface AttestEntry {
  uid: string;
  schema: string;
  timestamp: number;
  chain: string;
}

export function getAttestHistory(): Promise<{ history: AttestEntry[] }> {
  return daemonFetch("/attest/history");
}

// ── Friends ────────────────────────────────────────────────────────────────
export interface Friend {
  did: string;
  handle?: string;
  trust_level: number;
  connected_at: string;
}

export function listFriends(): Promise<{ friends: Friend[] }> {
  return daemonFetch("/friend/list");
}

export function listPerms(): Promise<{ perms: unknown[] }> {
  return daemonFetch("/perms/list");
}

// ── Skills (§28 §3.6 packaging spec · 跟 dev-A skill_router 对齐) ───────────
//
// daemon endpoints:
//   POST /sisoul/skill/create
//   GET  /sisoul/skill/list
//   POST /sisoul/skill/lend
//   POST /sisoul/skill/borrow
//   GET  /sisoul/skill/sessions
//   POST /sisoul/skill/end-session
//   POST /sisoul/skill/proxy-chat
//
// 字段命名跟 dev-A pydantic 模型完全对齐 (skill_id / qualified_name / owner_did /
// personality_traits / recommended_models / few_shot_examples / session_id /
// duration_minutes / proxy_endpoint / wiped / etc).

// §28 §3.6 packaging spec — 单个技能完整描述
export interface SkillItem {
  skill_id: string;
  qualified_name: string;
  name: string;
  version: string;
  owner_did: string;
  description: string;
  source: "owned" | "borrowed" | "available";
  fingerprint: string;
  examples_count: number;
  personality_traits: string[];
  recommended_models: string[];
  installed?: boolean;
  borrowed?: boolean;
}

// §28 §3.3 — 3 档授权 mode
export interface SkillLendPermissions {
  // "strong-tie-auto" | "per-request" | "emergency-only"
  mode: "strong-tie-auto" | "per-request" | "emergency-only";
  max_duration_minutes: number;
  pin_to_ipfs?: boolean;
  recipient_pubkey_b64?: string;
}

// §28 §3.6 — createSkill request body
export interface SkillCreateRequest {
  name: string;
  description: string;
  system_prompt: string;
  version?: string;
  few_shot_examples?: Array<{ user: string; assistant: string }>;
  preference_overlay?: Record<string, unknown>;
  tool_call_templates?: Array<Record<string, unknown>>;
  personality_traits?: string[];
  recommended_models?: string[];
  expiry_hours?: number;
  owner_did?: string;
}

export interface SkillCreateResponse {
  skill_id: string;
  qualified_name: string;
  owner_did: string;
  version: string;
  fingerprint: string;
  examples_count: number;
}

export interface SkillListResponse {
  own_did: string;
  owned: SkillItem[];
  available_to_borrow: SkillItem[];
}

export interface SkillLendRequest {
  skill_id: string;
  permissions: SkillLendPermissions;
  expiry_hours?: number;
}

export interface SkillLendResponse {
  skill_id: string;
  qualified_name: string;
  max_duration_minutes: number;
  ipfs_cid?: string;
  encrypted_b64?: string;
  sender_pubkey_b64?: string;
}

export interface SkillBorrowRequest {
  owner_did: string;
  skill_name: string;
  qualified_name: string;
  duration_minutes: number;
  per_request_approved?: boolean;
  emergency_flag?: boolean;
  borrower_did?: string;
}

export interface SkillBorrowResponse {
  session_id: string;
  qualified_name: string;
  owner_did: string;
  borrower_did: string;
  skill_id: string;
  started_at: number;
  expires_at: number;
  duration_minutes: number;
  ipfs_cid?: string;
  skill_package_fingerprint: string;
  permission_reason: string;
  used_fallback: boolean;
}

// §28 §3.6 lifecycle — session 30/60/120min + wipe
export interface SkillSessionItem {
  session_id: string;
  skill_id: string;
  skill_name: string;
  qualified_name: string;
  owner_did: string;
  borrower_did: string;
  status: "active" | "expired" | "ended" | "wiped";
  started_at: number;
  expires_at: number;
  proxy_endpoint: string;
  wiped: boolean;
}

export interface SkillSessionsResponse {
  own_did: string;
  sessions: SkillSessionItem[];
}

export interface SkillEndSessionRequest {
  session_id: string;
  reason?: string;
}

export interface SkillEndSessionResponse {
  session_id: string;
  status: string;
  destroy_reason?: string;
  destroyed_at?: number;
  ledger_entry_id?: string;
}

export interface SkillProxyChatRequest {
  session_id: string;
  messages: Array<{ role: "user" | "assistant" | "system"; content: string }>;
  prompt?: string;
  model?: string;
  provider?: string;
  llm_api_key?: string;
}

export interface SkillProxyChatResponse {
  text: string;
  tokens_used: number;
  prompt_tokens: number;
  response_tokens: number;
  model_used: string;
  session_id: string;
  session_remaining_sec: number;
}

// Legacy alias — 维持 Skills.tsx 旧 import 不破坏
export type Skill = SkillItem;

// §28 §3.6 — 7 skill API methods · 对象字面量形式 (跟 contract test 对齐)
export const skillApi = {
  createSkill: (body: SkillCreateRequest): Promise<SkillCreateResponse> =>
    absoluteFetch("/sisoul/skill/create", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listSkills: (): Promise<SkillListResponse> =>
    absoluteFetch("/sisoul/skill/list"),

  lendSkill: (body: SkillLendRequest): Promise<SkillLendResponse> =>
    absoluteFetch("/sisoul/skill/lend", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  borrowSkill: (body: SkillBorrowRequest): Promise<SkillBorrowResponse> =>
    absoluteFetch("/sisoul/skill/borrow", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listSkillSessions: (): Promise<SkillSessionsResponse> =>
    absoluteFetch("/sisoul/skill/sessions"),

  endSkillSession: (body: SkillEndSessionRequest): Promise<SkillEndSessionResponse> =>
    absoluteFetch("/sisoul/skill/end-session", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  proxyChatWithSkill: (body: SkillProxyChatRequest): Promise<SkillProxyChatResponse> =>
    absoluteFetch("/sisoul/skill/proxy-chat", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// 兼容旧 import (Skills.tsx 用 listSkills/borrowSkill 直接命名)
export const createSkill = skillApi.createSkill;
export const listSkills = skillApi.listSkills;
export const lendSkill = skillApi.lendSkill;
export const borrowSkill = skillApi.borrowSkill;
export const listSkillSessions = skillApi.listSkillSessions;
export const endSkillSession = skillApi.endSkillSession;
export const proxyChatWithSkill = skillApi.proxyChatWithSkill;

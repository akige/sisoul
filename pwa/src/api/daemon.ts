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
  // daemon 真返 array of {id, path, title, tags, updated} —— PWA 期望
  // {items: [{key, value, updated_at}, ...]}. Adapter 把 array→object 同时
  // 把 field rename (title→key, ?→value, updated→updated_at). 防 undefined
  // 全程 ?? "" fallback, 不让 caller `.length` 炸.
  return daemonFetch<any>("/preferences/list").then((d) => {
    const arr: any[] = Array.isArray(d) ? d : Array.isArray(d?.items) ? d.items : [];
    return {
      items: arr.map((r) => ({
        key: r.key ?? r.title ?? r.id ?? "(no key)",
        value: r.value ?? r.summary ?? r.body ?? r.path ?? "",
        updated_at: r.updated_at ?? r.updated ?? "",
      })),
    };
  });
}

// ── Goals ─────────────────────────────────────────────────────────────────
export interface Goal {
  id: string;
  title: string;
  progress: number;
  deadline?: string;
  notes?: string;
  status?: string;
}

export function listGoals(): Promise<{ goals: Goal[] }> {
  // daemon 真返 array of {id, path, title, progress, status, target_date,
  // updated} —— PWA 期望 {goals: [{id, title, progress, deadline?, notes?}]}.
  // Adapter: array → object + target_date → deadline.
  return daemonFetch<any>("/goals/list").then((d) => {
    const arr: any[] = Array.isArray(d) ? d : Array.isArray(d?.goals) ? d.goals : [];
    return {
      goals: arr.map((r) => ({
        id: r.id ?? "",
        title: r.title ?? "(no title)",
        progress: typeof r.progress === "number" ? r.progress : 0,
        deadline: r.deadline ?? r.target_date ?? undefined,
        notes: r.notes ?? r.summary ?? undefined,
        status: r.status ?? "active",
      })),
    };
  });
}

// ── Chat History ──────────────────────────────────────────────────────────
export interface ChatSession {
  id: string;
  title: string;
  started_at: string;
  message_count: number;
}

export function listChatHistory(): Promise<{ sessions: ChatSession[] }> {
  // daemon 真返 array (类似 goals/preferences), PWA 期望 {sessions: []}
  return daemonFetch<any>("/chat-history/list").then((d) => {
    const arr: any[] = Array.isArray(d) ? d : Array.isArray(d?.sessions) ? d.sessions : [];
    return {
      sessions: arr.map((r) => ({
        id: r.id ?? r.session_id ?? "",
        title: r.title ?? "(untitled)",
        started_at: r.started_at ?? r.created_at ?? r.updated ?? "",
        message_count: r.message_count ?? r.messages?.length ?? 0,
      })),
    };
  });
}

// ── Identity / Settings ────────────────────────────────────────────────────
export interface IdentityInfo {
  did: string;
  handle?: string;
  mnemonic_hint?: string;
  provider?: string;
}

export function getIdentity(): Promise<IdentityInfo> {
  // daemon /identity 真返 {has_seed, seed_path, master_key_fingerprint, ...}
  // 不含 did. PWA Settings.tsx access id().did → undefined → .length crash.
  // 兜底: 先调 /identity 拿 seed info, 同步调 /did 拿 default did, 合并.
  return Promise.all([
    daemonFetch<any>("/identity").catch(() => ({})),
    daemonFetch<any>("/did").catch(() => ({})),
  ]).then(([identityResp, didResp]) => {
    const def = didResp?.default || didResp || {};
    return {
      did: def.did ?? identityResp?.master_key_fingerprint ?? "did:key:unknown",
      handle: def.handle ?? identityResp?.handle ?? undefined,
      mnemonic_hint: identityResp?.seed_path
        ? `seed @ ${identityResp.seed_path} (${identityResp.seed_word_count || 12} 词)`
        : undefined,
      provider: def.network ?? identityResp?.provider ?? undefined,
    };
  });
}

// ── Attestation / Advanced ─────────────────────────────────────────────────
export interface AttestEntry {
  uid: string;
  schema: string;
  timestamp: number;
  chain: string;
}

export function getAttestHistory(): Promise<{ history: AttestEntry[] }> {
  // daemon 真返 {source, items: [...]} — PWA 期望 {history: [...]}
  return daemonFetch<any>("/attest/history").then((d) => {
    const arr: any[] = Array.isArray(d?.items) ? d.items
      : Array.isArray(d?.history) ? d.history
      : Array.isArray(d) ? d : [];
    return {
      history: arr.map((r) => ({
        uid: r.uid ?? r.batch_uid ?? r.tx_hash ?? "",
        schema: r.schema ?? r.method ?? r.network ?? "",
        timestamp: r.timestamp ?? (r.confirmed_at ? Date.parse(r.confirmed_at)/1000 : 0),
        chain: r.chain ?? r.network ?? "",
      })),
    };
  });
}

// ── Friends ────────────────────────────────────────────────────────────────
export interface Friend {
  did: string;
  handle?: string;
  trust_level: number;
  connected_at: string;
  // P1-1 push heartbeat → last_seen_at (Unix epoch ms); 不在线 = null
  last_seen_at?: number | null;
  online?: boolean;
  // 待 Lend approve 数 (走 lend pending count cache)
  pending_lend_count?: number;
}

export function listFriends(): Promise<{ friends: Friend[] }> {
  // adapter: daemon 返 array, 兜底 {friends: []} 不让 caller `.length` 炸
  return daemonFetch<any>("/friend/list").then((d) => {
    const arr: any[] = Array.isArray(d) ? d : Array.isArray(d?.friends) ? d.friends : [];
    return { friends: arr };
  }) as Promise<{ friends: Friend[] }>;
  // legacy direct (kept for ref): return daemonFetch("/friend/list");
}

export interface AddFriendRequest {
  did: string;
  handle?: string;
  trust_level?: number;
}

export interface AddFriendResponse {
  did: string;
  handle?: string;
  trust_level: number;
  added_at: string;
  verified: boolean;
}

export function addFriend(body: AddFriendRequest): Promise<AddFriendResponse> {
  return daemonFetch("/friend/add", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listPerms(): Promise<{ perms: unknown[] }> {
  return daemonFetch("/perms/list");
}

// ── Borrow (主动借) ──────────────────────────────────────────────────────────
// daemon endpoints (Wave B P1-2):
//   POST /sisoul/borrow/run         触发主动 borrow (Waku → 加密 → 等批 → LLM)
//   GET  /sisoul/borrow/proxy-list  当前活跃 proxy session
//   POST /sisoul/borrow/proxy-stop  停某 proxy session
//   GET  /sisoul/ledger/<friend_did>?direction=borrow|lend
//
// 字段命名跟 dev-A pydantic 模型对齐 (provider/model/token_count/emergency_flag/
// session_id/proxy_endpoint/etc)。

export interface BorrowRunRequest {
  friend_did: string;
  provider: string;
  model: string;
  token_count: number;
  emergency_flag?: boolean;
  reason?: string;
}

export type BorrowStage =
  | "queued"
  | "waku-discover"
  | "encrypting"
  | "awaiting-approval"
  | "llm-streaming"
  | "completed"
  | "denied"
  | "error";

export interface BorrowRunResponse {
  request_id?: string;
  session_id?: string;
  stage?: BorrowStage;
  proxy_endpoint?: string;
  approved_at?: string;
  error?: string;
  // daemon /sisoul/borrow/run alias 真返 {session: {session_id, status,
  // lend_request_id, error, ...}}. PWA submit handler 兜底两种 shape。
  session?: {
    session_id?: string;
    lend_request_id?: string;
    status?: string;
    error?: string | null;
    [k: string]: unknown;
  };
}

export function borrowRun(body: BorrowRunRequest): Promise<BorrowRunResponse> {
  return daemonFetch("/borrow/run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface ProxySessionItem {
  session_id: string;
  request_id: string;
  friend_did: string;
  friend_handle?: string;
  provider: string;
  model: string;
  token_count: number;
  tokens_used: number;
  started_at: string;
  expires_at: string;
  stage: BorrowStage;
  proxy_endpoint?: string;
}

export interface ProxyListResponse {
  sessions: ProxySessionItem[];
}

export function borrowProxyList(): Promise<ProxyListResponse> {
  return daemonFetch("/borrow/proxy-list");
}

export interface ProxyStopRequest {
  session_id: string;
  reason?: string;
}

export interface ProxyStopResponse {
  session_id: string;
  stopped_at: string;
  tokens_used: number;
}

export function borrowProxyStop(
  body: ProxyStopRequest
): Promise<ProxyStopResponse> {
  return daemonFetch("/borrow/proxy-stop", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Lend (被动接) ────────────────────────────────────────────────────────────
// daemon endpoints:
//   GET  /sisoul/lend/list          pending lend request 列表
//   POST /sisoul/lend/approve       批准一条 request
//   POST /sisoul/lend/deny          拒绝一条 request
//
// Pending request 通常走 WebSocket /sisoul/notify/stream 实时推 (P1-1).

export interface LendRequestItem {
  request_id: string;
  borrower_did: string;
  borrower_handle?: string;
  provider: string;
  model: string;
  token_count: number;
  reason?: string;
  emergency_flag: boolean;
  created_at: string;
  expires_at: string;
}

export interface LendListResponse {
  requests: LendRequestItem[];
}

export function lendList(): Promise<LendListResponse> {
  return daemonFetch("/lend/list");
}

export interface LendApproveRequest {
  request_id: string;
  duration_minutes?: number;
  max_tokens?: number;
}

export interface LendApproveResponse {
  request_id: string;
  session_id: string;
  approved_at: string;
  expires_at: string;
  proxy_endpoint?: string;
}

export function lendApprove(
  body: LendApproveRequest
): Promise<LendApproveResponse> {
  return daemonFetch("/lend/approve", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface LendDenyRequest {
  request_id: string;
  reason?: string;
}

export interface LendDenyResponse {
  request_id: string;
  denied_at: string;
}

export function lendDeny(body: LendDenyRequest): Promise<LendDenyResponse> {
  return daemonFetch("/lend/deny", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Ledger (双向账本) ────────────────────────────────────────────────────────
export interface LedgerEntry {
  entry_id: string;
  request_id?: string;
  session_id?: string;
  direction: "borrow" | "lend";
  counterparty_did: string;
  counterparty_handle?: string;
  provider: string;
  model: string;
  tokens_used: number;
  cost_usd?: number;
  started_at: string;
  ended_at?: string;
  status: "active" | "completed" | "denied" | "error";
}

export interface LedgerResponse {
  friend_did?: string;
  direction?: "borrow" | "lend";
  entries: LedgerEntry[];
  total_tokens: number;
  total_cost_usd: number;
}

export function getLedger(
  friendDid: string,
  direction?: "borrow" | "lend"
): Promise<LedgerResponse> {
  const q = direction ? `?direction=${encodeURIComponent(direction)}` : "";
  return daemonFetch(
    `/ledger/${encodeURIComponent(friendDid)}${q}`
  );
}

export function getLedgerAll(
  direction?: "borrow" | "lend"
): Promise<LedgerResponse> {
  const q = direction ? `?direction=${encodeURIComponent(direction)}` : "";
  return daemonFetch(`/ledger/all${q}`);
}

// ── Notify Stream (SSE/WebSocket 实时推) ────────────────────────────────────
//
// daemon endpoint: GET /sisoul/notify/stream (Server-Sent Events)
//
// 消息格式:
//   event: lend.request   data: <LendRequestItem JSON>
//   event: ledger.entry   data: <LedgerEntry JSON>
//   event: friend.online  data: { did, online, last_seen_at }
//   event: borrow.update  data: { request_id, stage, proxy_endpoint? }
//   event: heartbeat      data: {}
//
// 浏览器原生 EventSource 已足够; 不需 WebSocket. 但开放 WS fallback,
// 走 /sisoul/notify/ws (subprotocol "sisoul.notify.v1").

export type NotifyEvent =
  | { type: "lend.request"; data: LendRequestItem }
  | { type: "ledger.entry"; data: LedgerEntry }
  | { type: "friend.online"; data: { did: string; online: boolean; last_seen_at?: number } }
  | { type: "borrow.update"; data: { request_id: string; stage: BorrowStage; proxy_endpoint?: string } }
  | { type: "heartbeat"; data: Record<string, never> };

export interface NotifyStreamHandle {
  close(): void;
  readyState(): number;
}

export function notifyStream(
  onEvent: (ev: NotifyEvent) => void,
  onError?: (err: Event) => void
): NotifyStreamHandle {
  // 跨浏览器 SSE; 测试环境 (jsdom) 没原生 EventSource → fallback no-op
  const G = globalThis as unknown as { EventSource?: typeof EventSource };
  if (typeof G.EventSource === "undefined") {
    return { close: () => {}, readyState: () => 2 };
  }
  const es = new G.EventSource(`${DAEMON_BASE}/notify/stream`);

  const types: NotifyEvent["type"][] = [
    "lend.request",
    "ledger.entry",
    "friend.online",
    "borrow.update",
    "heartbeat",
  ];
  for (const t of types) {
    es.addEventListener(t, (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        onEvent({ type: t, data } as NotifyEvent);
      } catch {
        // ignore malformed payload
      }
    });
  }
  if (onError) es.onerror = onError;
  return {
    close: () => es.close(),
    readyState: () => es.readyState,
  };
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

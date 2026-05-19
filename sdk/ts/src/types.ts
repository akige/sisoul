// sisoul daemon shared types - 跟 PWA daemon.ts 对齐
// 任何新字段在这里加, 不要散落到 client/vault/goals 里.

export interface Preference {
  key: string;
  value: string;
  updated_at: string;
}

export interface ListPreferencesResponse {
  items: Preference[];
}

export interface Goal {
  id: string;
  title: string;
  progress: number;
  deadline?: string;
  notes?: string;
}

export interface ListGoalsResponse {
  goals: Goal[];
}

export interface GoalCreateRequest {
  title: string;
  progress?: number;
  deadline?: string;
  notes?: string;
}

export interface GoalUpdateRequest {
  id: string;
  title?: string;
  progress?: number;
  deadline?: string;
  notes?: string;
}

export interface Friend {
  did: string;
  handle?: string;
  trust_level: number;
  connected_at: string;
}

export interface ListFriendsResponse {
  friends: Friend[];
}

export interface FriendAddRequest {
  did: string;
  handle?: string;
  trust_level?: number;
}

export interface FriendLendRequest {
  friend_did: string;
  resource_type: "skill" | "data" | "compute";
  resource_id: string;
  duration_hours?: number;
}

export interface FriendBorrowRequest {
  owner_did: string;
  resource_type: "skill" | "data" | "compute";
  resource_id: string;
  duration_hours?: number;
}

// Skills
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

export interface SkillLendPermissions {
  mode: "strong-tie-auto" | "per-request" | "emergency-only";
  max_duration_minutes: number;
  pin_to_ipfs?: boolean;
  recipient_pubkey_b64?: string;
}

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

// Attest
export interface AttestEntry {
  uid: string;
  schema: string;
  timestamp: number;
  chain: string;
}

export interface AttestHistoryResponse {
  history: AttestEntry[];
}

export interface AttestCreateRequest {
  schema: string;
  subject_did: string;
  payload: Record<string, unknown>;
  chain?: string;
}

export interface AttestCreateResponse {
  uid: string;
  tx_hash?: string;
  chain: string;
  timestamp: number;
}

// Vault generic
export interface VaultSetRequest {
  key: string;
  value: string;
}

export interface VaultGetResponse {
  key: string;
  value: string | null;
  updated_at?: string;
}

export interface ClientConfig {
  baseUrl?: string;
  timeout?: number;
  fetchImpl?: typeof fetch;
  headers?: Record<string, string>;
}

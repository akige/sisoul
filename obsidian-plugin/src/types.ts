// daemon HTTP response 类型

export interface DaemonHealth {
  status: "ok" | "degraded" | "down";
  version: string;
  uptime_sec: number;
  vault_encrypted: boolean;
  did: string;
}

export interface PreferenceItem {
  key: string;
  value: string;
  category?: string;
  updated_at: string;
}

export interface PreferencesResponse {
  items: PreferenceItem[];
  total: number;
}

export interface GoalItem {
  id: string;
  title: string;
  status: "active" | "paused" | "done" | "abandoned";
  priority: number;
  created_at: string;
  updated_at: string;
  description?: string;
}

export interface GoalsResponse {
  items: GoalItem[];
  total: number;
}

export interface FriendItem {
  did: string;
  alias: string;
  trust: number;
  last_seen?: string;
  tags?: string[];
}

export interface FriendsResponse {
  items: FriendItem[];
  total: number;
}

export interface SkillItem {
  id: string;
  name: string;
  level: number;
  category: string;
  evidence_count: number;
  updated_at: string;
}

export interface SkillsResponse {
  items: SkillItem[];
  total: number;
}

export interface SyncStatus {
  last_sync_at: string | null;
  in_progress: boolean;
  next_scheduled_at: string | null;
  peers_synced: number;
  pending_objects: number;
  last_error: string | null;
}

export interface SyncTriggerResponse {
  accepted: boolean;
  sync_id: string;
  started_at: string;
}

export class DaemonError extends Error {
  constructor(
    public readonly kind: "network" | "auth" | "server" | "client" | "timeout",
    public readonly status: number | null,
    message: string,
  ) {
    super(message);
    this.name = "DaemonError";
  }
}

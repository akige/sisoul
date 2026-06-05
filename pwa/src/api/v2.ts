// PWA v2 daemon API client. 真接 /v2/* endpoints (foundation skeleton).
const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE || "http://127.0.0.1:9876";

export interface Case {
  id: string;
  question: string;
  answer: string;
  did_author: string;
  created_at: string;
  tags: string[];
}

export interface CaseList {
  cases: Case[];
  count: number;
}

export interface SkillList {
  skills: string[];
  count: number;
}

export interface GrowthSnapshot {
  date: string;
  cases_added: number;
  skills_installed: number;
  skills_used: number;
  chats_sent: number;
  borrowed_llm_calls: number;
  new_friends: number;
}

export interface GrowthTrend {
  window_days: number;
  snapshots: GrowthSnapshot[];
  total_cases: number;
  total_skills_used: number;
  avg_chats_per_day: number;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${DAEMON_BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`${path} ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function listCases(limit = 100): Promise<CaseList> {
  return fetchJson(`/v2/case?limit=${limit}`);
}

export async function searchCases(query: string, top_k = 5): Promise<{
  query: string;
  cases: Case[];
  is_hit: boolean;
}> {
  return fetchJson(`/v2/case/search/?q=${encodeURIComponent(query)}&top_k=${top_k}`);
}

export async function listSkillsInstalled(): Promise<SkillList> {
  return fetchJson(`/v2/skill/list`);
}

export async function getGrowthLast(n = 7): Promise<GrowthTrend> {
  return fetchJson(`/v2/growth/last?n=${n}`);
}

export interface DebateAgentSpec {
  did: string;
  petname?: string;
  topic_reputation: number;
}

export async function runDebate(query: string, agents: DebateAgentSpec[], n_rounds = 3) {
  return fetchJson<{
    query: string;
    final_answer: string;
    final_confidence: number;
    n_rounds: number;
    agents: { did: string; petname: string | null; is_synthesizer: boolean }[];
  }>(`/v2/debate/run`, {
    method: "POST",
    body: JSON.stringify({ query, agents, n_rounds }),
  });
}

export async function attestProvenance(
  response_id: string,
  query: string,
  answer: string,
  did_answerer: string,
  cited_cases: { source_id: string; did_author: string }[],
  network = "mock",
) {
  return fetchJson<{
    attestation_uid: string;
    network: string;
    total_micropay_sis: number;
    citation_count: number;
  }>(`/v2/provenance/attest`, {
    method: "POST",
    body: JSON.stringify({ response_id, query, answer, did_answerer, cited_cases, network }),
  });
}

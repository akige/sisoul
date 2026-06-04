// v2.0 智能体网络 Dashboard (Phase 3 foundation, SolidJS).
//
// Foundation skeleton: full data from daemon /v2/* in v2.0 ship (T+8-12m).
import { createSignal, onMount, For } from "solid-js";

interface GrowthSnapshot {
  date: string;
  cases_added: number;
  skills_used: number;
  chats_sent: number;
}

interface CaseRetrievalStats {
  total_cases: number;
  hit_rate_30d: number;
  avg_top_k_score: number;
}

interface SkillStats {
  installed_count: number;
  total_calls: number;
  total_sis_paid: number;
}

export default function V2Dashboard() {
  const [growth, setGrowth] = createSignal<GrowthSnapshot[]>([]);
  const [caseStats, setCaseStats] = createSignal<CaseRetrievalStats | null>(null);
  const [skillStats, setSkillStats] = createSignal<SkillStats | null>(null);
  const [loading, setLoading] = createSignal(true);

  onMount(() => {
    // Foundation: stub data. Full impl: fetch from daemon /v2/* + /v2/case/search
    setTimeout(() => {
      setGrowth([
        { date: "2026-05-29", cases_added: 3, skills_used: 1, chats_sent: 5 },
        { date: "2026-05-30", cases_added: 5, skills_used: 2, chats_sent: 8 },
        { date: "2026-05-31", cases_added: 2, skills_used: 1, chats_sent: 4 },
        { date: "2026-06-01", cases_added: 7, skills_used: 3, chats_sent: 12 },
        { date: "2026-06-02", cases_added: 4, skills_used: 2, chats_sent: 9 },
        { date: "2026-06-03", cases_added: 8, skills_used: 4, chats_sent: 15 },
        { date: "2026-06-04", cases_added: 6, skills_used: 3, chats_sent: 11 },
      ]);
      setCaseStats({
        total_cases: 47,
        hit_rate_30d: 0.34,
        avg_top_k_score: 0.71,
      });
      setSkillStats({
        installed_count: 5,
        total_calls: 142,
        total_sis_paid: 1.23,
      });
      setLoading(false);
    }, 200);
  });

  return (
    <div class="space-y-6 p-2">
      <header>
        <h1 class="text-2xl font-bold text-sisoul-text">v2.0 智能体网络 Dashboard</h1>
        <p class="text-sm text-sisoul-muted">
          Foundation skeleton — full data from daemon /v2/* endpoints in v2.0 ship (T+8-12m).
        </p>
      </header>

      {loading() ? (
        <div class="text-sisoul-muted p-4">Loading…</div>
      ) : (
        <>
          {/* Growth Curve */}
          <section>
            <h2 class="text-lg font-semibold mb-2">本周进化 (7-day growth)</h2>
            <div class="flex gap-2 items-end h-32 border-b border-sisoul-border">
              <For each={growth()}>
                {(s) => (
                  <div class="flex-1 text-center">
                    <div
                      class="bg-sisoul-accent rounded-t mx-auto transition-all"
                      style={{
                        height: `${s.cases_added * 12}px`,
                        width: "70%",
                      }}
                      title={`${s.date}: ${s.cases_added} cases`}
                    />
                    <div class="text-xs text-sisoul-muted mt-1">{s.date.slice(-2)}</div>
                  </div>
                )}
              </For>
            </div>
            <p class="text-xs text-sisoul-muted mt-2">
              Total this week: {growth().reduce((a, s) => a + s.cases_added, 0)} cases
            </p>
          </section>

          {/* Case Retrieval Stats */}
          <section>
            <h2 class="text-lg font-semibold mb-2">Case Retrieval</h2>
            <div class="grid grid-cols-3 gap-3">
              <div class="border border-sisoul-border rounded-lg p-3">
                <div class="text-2xl font-bold">{caseStats()?.total_cases}</div>
                <div class="text-xs text-sisoul-muted">Total Cases</div>
              </div>
              <div class="border border-sisoul-border rounded-lg p-3">
                <div class="text-2xl font-bold">
                  {((caseStats()?.hit_rate_30d ?? 0) * 100).toFixed(0)}%
                </div>
                <div class="text-xs text-sisoul-muted">30-day Hit Rate</div>
              </div>
              <div class="border border-sisoul-border rounded-lg p-3">
                <div class="text-2xl font-bold">{caseStats()?.avg_top_k_score.toFixed(2)}</div>
                <div class="text-xs text-sisoul-muted">Avg top-k score</div>
              </div>
            </div>
            <p class="text-xs text-sisoul-muted mt-2">
              v2.0 KPI target: hit rate ≥ 30%. Current: {((caseStats()?.hit_rate_30d ?? 0) * 100).toFixed(0)}%
              {(caseStats()?.hit_rate_30d ?? 0) >= 0.3 ? " ✅" : " ⚠️"}
            </p>
          </section>

          {/* Skill Stats */}
          <section>
            <h2 class="text-lg font-semibold mb-2">Skill Ecosystem</h2>
            <div class="grid grid-cols-3 gap-3">
              <div class="border border-sisoul-border rounded-lg p-3 bg-yellow-50/5">
                <div class="text-2xl font-bold">{skillStats()?.installed_count}</div>
                <div class="text-xs text-sisoul-muted">Installed</div>
              </div>
              <div class="border border-sisoul-border rounded-lg p-3 bg-yellow-50/5">
                <div class="text-2xl font-bold">{skillStats()?.total_calls}</div>
                <div class="text-xs text-sisoul-muted">Total Calls</div>
              </div>
              <div class="border border-sisoul-border rounded-lg p-3 bg-yellow-50/5">
                <div class="text-2xl font-bold">{skillStats()?.total_sis_paid.toFixed(2)} SIS</div>
                <div class="text-xs text-sisoul-muted">Paid to authors</div>
              </div>
            </div>
          </section>

          {/* Roadmap */}
          <section>
            <h2 class="text-lg font-semibold mb-2">Roadmap to 涌现</h2>
            <ul class="space-y-1 text-sm">
              <li><strong>alpha v1.0 (NOW)</strong>: 5 核心 + Signal chat (Double Ratchet + PQXDH)</li>
              <li><strong>v1.0 stable (T+6m)</strong>: Optimism mainnet + SIS Airdrop</li>
              <li><strong>v2.0 智能体网络 (T+12m)</strong>: Case retrieval + Personal LoRA + Provenance + Skill marketplace</li>
              <li><strong>v3.0 超级智能体 (T+18m)</strong>: Multi-Agent Debate + Federated LoRA + SIS micropay</li>
              <li><strong>集体智能涌现 (T+36m)</strong>: 10K+ MAU + 1M+ cases + 70%+ recall (bonus, 25-35% prob)</li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

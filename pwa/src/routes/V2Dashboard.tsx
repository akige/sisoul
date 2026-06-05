// v2.0 智能体网络 Dashboard · 真 fetch daemon /v2/* endpoints (replaces stub).
import { createSignal, onMount, For, Show } from "solid-js";
import { listCases, listSkillsInstalled, getGrowthLast } from "../api/v2";
import type { Case, GrowthTrend } from "../api/v2";

export default function V2Dashboard() {
  const [growth, setGrowth] = createSignal<GrowthTrend | null>(null);
  const [cases, setCases] = createSignal<Case[]>([]);
  const [caseTotal, setCaseTotal] = createSignal(0);
  const [skillCount, setSkillCount] = createSignal(0);
  const [skills, setSkills] = createSignal<string[]>([]);
  const [error, setError] = createSignal<string | null>(null);
  const [loading, setLoading] = createSignal(true);

  onMount(async () => {
    try {
      // parallel fetch
      const [caseList, skillList, growthTrend] = await Promise.all([
        listCases(50).catch(() => ({ cases: [], count: 0 })),
        listSkillsInstalled().catch(() => ({ skills: [], count: 0 })),
        getGrowthLast(7).catch(() => ({
          window_days: 7,
          snapshots: [],
          total_cases: 0,
          total_skills_used: 0,
          avg_chats_per_day: 0,
        })),
      ]);
      setCases(caseList.cases);
      setCaseTotal(caseList.count);
      setSkillCount(skillList.count);
      setSkills(skillList.skills);
      setGrowth(growthTrend);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  });

  const hitRate = () => {
    const t = caseTotal();
    if (t === 0) return 0;
    // foundation: simple est. as cases/100 (proxy). Full impl uses real retrieval stats.
    return Math.min(1, t / 100);
  };

  return (
    <div class="space-y-6 p-2">
      <header>
        <h1 class="text-2xl font-bold text-sisoul-text">v2.0 智能体网络 Dashboard</h1>
        <p class="text-sm text-sisoul-muted">
          真 fetch sisoul daemon /v2/* endpoints. Daemon 没起 / 没 case 时显 0.
        </p>
      </header>

      <Show when={error()}>
        <div class="border border-red-500/40 bg-red-500/10 rounded-lg p-3 text-sm">
          <strong>Daemon 未连接</strong>: {error()}
          <p class="text-xs text-sisoul-muted mt-1">
            启动 daemon: <code>sisoul daemon start</code>
          </p>
        </div>
      </Show>

      <Show when={loading()}>
        <div class="text-sisoul-muted p-4">Loading from daemon…</div>
      </Show>

      <Show when={!loading()}>
        {/* Growth Curve */}
        <section>
          <h2 class="text-lg font-semibold mb-2">本周进化 (7-day growth)</h2>
          <Show
            when={growth() && growth()!.snapshots.length > 0}
            fallback={
              <div class="text-sm text-sisoul-muted">
                尚无 growth 数据. daemon 每日自动 snapshot 后这里会显 7-day curve.
              </div>
            }
          >
            <div class="flex gap-2 items-end h-32 border-b border-sisoul-border">
              <For each={growth()!.snapshots}>
                {(s) => (
                  <div class="flex-1 text-center">
                    <div
                      class="bg-sisoul-accent rounded-t mx-auto transition-all"
                      style={{
                        height: `${Math.max(4, s.cases_added * 12)}px`,
                        width: "70%",
                      }}
                      title={`${s.date}: ${s.cases_added} cases / ${s.chats_sent} chats`}
                    />
                    <div class="text-xs text-sisoul-muted mt-1">{s.date.slice(-2)}</div>
                  </div>
                )}
              </For>
            </div>
            <p class="text-xs text-sisoul-muted mt-2">
              Total: {growth()!.total_cases} cases · avg chats/day {growth()!.avg_chats_per_day.toFixed(1)}
            </p>
          </Show>
        </section>

        {/* Case Retrieval Stats */}
        <section>
          <h2 class="text-lg font-semibold mb-2">Case Retrieval</h2>
          <div class="grid grid-cols-3 gap-3">
            <div class="border border-sisoul-border rounded-lg p-3">
              <div class="text-2xl font-bold">{caseTotal()}</div>
              <div class="text-xs text-sisoul-muted">Total Cases</div>
            </div>
            <div class="border border-sisoul-border rounded-lg p-3">
              <div class="text-2xl font-bold">{(hitRate() * 100).toFixed(0)}%</div>
              <div class="text-xs text-sisoul-muted">Est. Hit Rate</div>
            </div>
            <div class="border border-sisoul-border rounded-lg p-3">
              <div class="text-2xl font-bold">v2.0 KPI</div>
              <div class="text-xs text-sisoul-muted">target ≥ 30% (T+12m)</div>
            </div>
          </div>
        </section>

        {/* Recent cases */}
        <section>
          <h2 class="text-lg font-semibold mb-2">Recent Cases (top 5)</h2>
          <Show
            when={cases().length > 0}
            fallback={<div class="text-sm text-sisoul-muted">尚无 case. 通过 sisoul ask 自动写入.</div>}
          >
            <ul class="space-y-1 text-sm">
              <For each={cases().slice(0, 5)}>
                {(c) => (
                  <li class="border border-sisoul-border rounded p-2">
                    <div class="font-semibold">{c.question}</div>
                    <div class="text-xs text-sisoul-muted">
                      {c.did_author.slice(0, 16)}… · {c.created_at}
                    </div>
                  </li>
                )}
              </For>
            </ul>
          </Show>
        </section>

        {/* Skill Stats */}
        <section>
          <h2 class="text-lg font-semibold mb-2">Skill Ecosystem</h2>
          <div class="grid grid-cols-3 gap-3 mb-2">
            <div class="border border-sisoul-border rounded-lg p-3">
              <div class="text-2xl font-bold">{skillCount()}</div>
              <div class="text-xs text-sisoul-muted">Installed</div>
            </div>
            <div class="border border-sisoul-border rounded-lg p-3">
              <div class="text-2xl font-bold">v2.0</div>
              <div class="text-xs text-sisoul-muted">marketplace ship T+11m</div>
            </div>
            <div class="border border-sisoul-border rounded-lg p-3">
              <div class="text-2xl font-bold">SIS</div>
              <div class="text-xs text-sisoul-muted">micropay v3.0 T+15m</div>
            </div>
          </div>
          <Show when={skills().length > 0}>
            <div class="text-sm text-sisoul-muted">
              Installed: <For each={skills()}>{(s) => <code class="mr-2">{s}</code>}</For>
            </div>
          </Show>
        </section>

        {/* Roadmap */}
        <section>
          <h2 class="text-lg font-semibold mb-2">Roadmap to 涌现</h2>
          <ul class="space-y-1 text-sm">
            <li><strong>alpha v1.0 (NOW)</strong>: 5 核心 + Signal chat real PQXDH</li>
            <li><strong>v1.0 stable (T+6m)</strong>: Optimism mainnet + SIS Airdrop</li>
            <li><strong>v2.0 智能体网络 (T+12m)</strong>: Case + LoRA + Provenance + Skill marketplace</li>
            <li><strong>v3.0 超级智能体 (T+18m)</strong>: Multi-Agent Debate + Federated LoRA + SIS micropay</li>
            <li><strong>集体智能涌现 (T+36m)</strong>: 10K+ MAU + 1M+ cases (bonus, 25-35% prob)</li>
          </ul>
        </section>
      </Show>
    </div>
  );
}

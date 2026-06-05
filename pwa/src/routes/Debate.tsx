// PWA /debate · Multi-Agent Debate UI · 真接 /v2/debate/run.
import { createSignal, For, Show } from "solid-js";
import { runDebate } from "../api/v2";
import type { DebateAgentSpec } from "../api/v2";

interface DebateResult {
  query: string;
  final_answer: string;
  final_confidence: number;
  n_rounds: number;
  agents: { did: string; petname: string | null; is_synthesizer: boolean }[];
}

const DEFAULT_AGENTS: DebateAgentSpec[] = [
  { did: "did:key:z6MkAlice", petname: "Alice", topic_reputation: 0.6 },
  { did: "did:key:z6MkBob", petname: "Bob", topic_reputation: 0.85 },
  { did: "did:key:z6MkCarol", petname: "Carol", topic_reputation: 0.72 },
];

export default function Debate() {
  const [query, setQuery] = createSignal("");
  const [agents, setAgents] = createSignal<DebateAgentSpec[]>(DEFAULT_AGENTS);
  const [nRounds, setNRounds] = createSignal(3);
  const [result, setResult] = createSignal<DebateResult | null>(null);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);

  const handleRun = async () => {
    const q = query().trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const r = await runDebate(q, agents(), nRounds());
      setResult(r as DebateResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const updateAgentRep = (i: number, rep: number) => {
    setAgents(agents().map((a, idx) => (idx === i ? { ...a, topic_reputation: rep } : a)));
  };

  return (
    <div class="space-y-4 p-2">
      <header>
        <h1 class="text-2xl font-bold text-sisoul-text">Multi-Agent Debate (v3.0 preview)</h1>
        <p class="text-sm text-sisoul-muted">
          真接 daemon /v2/debate/run. Foundation: 3-round mock synthesize. Full impl (v3.0 ship T+15m): 真 LLM per agent + GossipSub fanout + 88-92% 解题率.
        </p>
      </header>

      <div class="border border-sisoul-border rounded-lg p-4 space-y-3">
        <div>
          <label class="block text-xs text-sisoul-muted mb-1">Difficult Question</label>
          <textarea
            class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1 text-sm"
            rows="2"
            value={query()}
            onInput={(e) => setQuery(e.currentTarget.value)}
            placeholder="PostgreSQL + pgbouncer + sqlx 'prepared statement does not exist' 怎么修?"
          />
        </div>
        <div>
          <label class="block text-xs text-sisoul-muted mb-1">Rounds (3-round protocol: initial → critique → synthesize)</label>
          <input
            type="number"
            min="2"
            max="5"
            class="bg-sisoul-bg border border-sisoul-border rounded px-2 py-1 w-20 text-sm"
            value={nRounds()}
            onInput={(e) => setNRounds(parseInt(e.currentTarget.value || "3"))}
          />
        </div>
        <div>
          <label class="block text-xs text-sisoul-muted mb-2">Agents (highest rep = synthesizer)</label>
          <div class="space-y-2">
            <For each={agents()}>
              {(a, i) => (
                <div class="flex gap-2 items-center text-sm">
                  <code class="text-xs flex-shrink-0">{a.petname || a.did.slice(0, 16)}</code>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={a.topic_reputation}
                    onInput={(e) => updateAgentRep(i(), parseFloat(e.currentTarget.value))}
                    class="flex-1"
                  />
                  <span class="text-xs w-12 text-right">{a.topic_reputation.toFixed(2)}</span>
                </div>
              )}
            </For>
          </div>
        </div>
        <button
          class="bg-sisoul-accent text-sisoul-bg px-4 py-2 rounded font-semibold hover:bg-sisoul-accentDim disabled:opacity-50"
          disabled={loading()}
          onClick={handleRun}
        >
          {loading() ? "Debating…" : "Run Debate"}
        </button>
      </div>

      <Show when={error()}>
        <div class="border border-red-500/40 bg-red-500/10 rounded-lg p-3 text-sm">
          <strong>Error</strong>: {error()}
        </div>
      </Show>

      <Show when={result() && !loading() && !error()}>
        <div class="border border-sisoul-border rounded-lg p-4 space-y-3">
          <h2 class="text-lg font-semibold">Synthesized Answer</h2>
          <pre class="text-sm whitespace-pre-wrap bg-sisoul-bg p-3 rounded border border-sisoul-border">{result()!.final_answer}</pre>
          <div class="grid grid-cols-3 gap-3 text-sm">
            <div class="border border-sisoul-border rounded p-2">
              <div class="text-lg font-bold">{(result()!.final_confidence * 100).toFixed(0)}%</div>
              <div class="text-xs text-sisoul-muted">Confidence</div>
            </div>
            <div class="border border-sisoul-border rounded p-2">
              <div class="text-lg font-bold">{result()!.n_rounds}</div>
              <div class="text-xs text-sisoul-muted">Total rounds</div>
            </div>
            <div class="border border-sisoul-border rounded p-2">
              <div class="text-lg font-bold">{result()!.agents.length}</div>
              <div class="text-xs text-sisoul-muted">Agents</div>
            </div>
          </div>
          <div>
            <h3 class="text-sm font-semibold text-sisoul-muted mb-2">Participants</h3>
            <ul class="space-y-1 text-sm">
              <For each={result()!.agents}>
                {(a) => (
                  <li class="flex justify-between border border-sisoul-border rounded p-2">
                    <span class="font-mono">{a.petname || a.did.slice(0, 16) + "…"}</span>
                    {a.is_synthesizer && (
                      <span class="text-xs bg-sisoul-accent/20 text-sisoul-accent px-2 rounded">synthesizer</span>
                    )}
                  </li>
                )}
              </For>
            </ul>
          </div>
        </div>
      </Show>
    </div>
  );
}

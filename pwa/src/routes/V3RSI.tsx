// v3 RSI (Recursive Self-Improvement) Dashboard · 真接 daemon /v3/rsi/* endpoints.
import { createSignal, onMount, For, Show } from "solid-js";
import {
  getRSIStatus,
  getRSIHistory,
  runRSIIterate,
  getRSIPeers,
} from "../api/v3";
import type { RSIStatus, RSIHistoryEntry, RSIIterateResponse } from "../api/v3";

export default function V3RSI() {
  const [status, setStatus] = createSignal<RSIStatus | null>(null);
  const [history, setHistory] = createSignal<RSIHistoryEntry[]>([]);
  const [peerCount, setPeerCount] = createSignal(0);
  const [lastResult, setLastResult] = createSignal<RSIIterateResponse | null>(null);
  const [error, setError] = createSignal<string | null>(null);
  const [loading, setLoading] = createSignal(true);
  const [iterating, setIterating] = createSignal(false);

  const refresh = async () => {
    try {
      const [st, hist, peers] = await Promise.all([
        getRSIStatus().catch(() => null),
        getRSIHistory().catch(() => ({ iterations: [], count: 0 })),
        getRSIPeers().catch(() => ({ peer_mutations: [], count: 0 })),
      ]);
      setStatus(st);
      setHistory(hist.iterations);
      setPeerCount(peers.count);
      setError(null);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  };

  onMount(refresh);

  const handleIterate = async (mode: "godel" | "alpha_evolve" | "dspy") => {
    setIterating(true);
    try {
      const result = await runRSIIterate({ mode, dry_run: true });
      setLastResult(result);
      await refresh();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setIterating(false);
    }
  };

  return (
    <div class="route">
      <h1>v3 RSI · Recursive Self-Improvement</h1>
      <p class="subtitle">
        L2 prompt 自优化 (Gödel Agent + DSPy) → L3 code 自演化 (AlphaEvolve) → L4 跨 daemon 集体演化 (FederatedRSI).
        基于 2025-2026 真研究 (AlphaEvolve DeepMind / Gödel Agent / DSPy Stanford / ICLR 2026 RSI Workshop).
      </p>

      <Show when={error()}>
        <div class="error">⚠ {error()}</div>
      </Show>

      <Show when={loading()}>
        <div class="loading">Loading RSI status…</div>
      </Show>

      <Show when={!loading() && status()}>
        <section class="rsi-status">
          <h2>Framework</h2>
          <table>
            <tbody>
              <tr><td>name</td><td>{status()!.framework}</td></tr>
              <tr><td>version</td><td>{status()!.version}</td></tr>
              <tr>
                <td>safety_boundary</td>
                <td>{status()!.safety_boundary_active ? "✓ ENFORCED" : "✗ disabled"}</td>
              </tr>
            </tbody>
          </table>
          <h3>Components</h3>
          <ul>
            <For each={Object.entries(status()!.components)}>
              {([name, state]) => (
                <li>
                  <code>{name}</code>: <span class={state === "loaded" ? "ok" : "warn"}>{state}</span>
                </li>
              )}
            </For>
          </ul>
        </section>

        <section class="rsi-controls">
          <h2>Iterate (dry-run)</h2>
          <p class="hint">
            Safety: dry_run=true 默认。真 mutation 需要 LLM adapter wire + EAS provenance attestation 后开启.
          </p>
          <div class="rsi-buttons">
            <button onClick={() => handleIterate("godel")} disabled={iterating()}>
              Gödel Agent (prompt 自优化)
            </button>
            <button onClick={() => handleIterate("alpha_evolve")} disabled={iterating()}>
              AlphaEvolve (code 进化)
            </button>
            <button onClick={() => handleIterate("dspy")} disabled={iterating()}>
              DSPy (declarative optimize)
            </button>
          </div>
          <Show when={lastResult()}>
            <pre class="rsi-result">{JSON.stringify(lastResult(), null, 2)}</pre>
          </Show>
        </section>

        <section class="rsi-history">
          <h2>History ({history().length})</h2>
          <Show when={history().length === 0} fallback={
            <table>
              <thead>
                <tr><th>id</th><th>mode</th><th>at</th><th>accepted</th><th>fitness</th></tr>
              </thead>
              <tbody>
                <For each={history()}>
                  {(it) => (
                    <tr>
                      <td><code>{it.iteration_id}</code></td>
                      <td>{it.mode}</td>
                      <td>{it.started_at}</td>
                      <td>{it.accepted ? "✓" : "✗"}</td>
                      <td>{it.fitness ?? "—"}</td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
          }>
            <p class="empty">No iterations yet. Click a button above.</p>
          </Show>
        </section>

        <section class="rsi-peers">
          <h2>Federated peers</h2>
          <p>
            Received peer mutations: <strong>{peerCount()}</strong>
          </p>
          <p class="hint">
            L4 涌现: 朋友圈 sisoul daemon 互相 gossip RSI 变异. 需 kubo + GossipSub transport wired (skeleton 阶段无 peer).
          </p>
        </section>
      </Show>
    </div>
  );
}

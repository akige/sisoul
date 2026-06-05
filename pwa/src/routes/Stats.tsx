// PWA /stats · daemon metrics dashboard, parses /sisoul/metrics Prometheus format.
import { createSignal, onMount, For, Show } from "solid-js";

const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE || "http://127.0.0.1:9876";

interface Metric {
  name: string;
  value: number;
  help: string;
  type: string;
  labels: Record<string, string>;
}

function parsePrometheus(text: string): Metric[] {
  const lines = text.split("\n");
  const metrics: Metric[] = [];
  let curHelp = "";
  let curType = "";
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("# HELP ")) {
      const rest = trimmed.slice(7);
      const idx = rest.indexOf(" ");
      curHelp = idx >= 0 ? rest.slice(idx + 1) : "";
      continue;
    }
    if (trimmed.startsWith("# TYPE ")) {
      const rest = trimmed.slice(7);
      const idx = rest.indexOf(" ");
      curType = idx >= 0 ? rest.slice(idx + 1) : "";
      continue;
    }
    if (trimmed.startsWith("#")) continue;
    // metric line: name{labels} value
    const m = trimmed.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(.+)$/);
    if (!m) continue;
    const name = m[1];
    const labelStr = m[2] || "";
    const valStr = m[3];
    const labels: Record<string, string> = {};
    if (labelStr) {
      const lm = labelStr.slice(1, -1).matchAll(/(\w+)="([^"]*)"/g);
      for (const lm_ of lm) labels[lm_[1]] = lm_[2];
    }
    metrics.push({
      name, value: parseFloat(valStr) || 0,
      help: curHelp, type: curType, labels,
    });
  }
  return metrics;
}

export default function Stats() {
  const [metrics, setMetrics] = createSignal<Metric[]>([]);
  const [raw, setRaw] = createSignal("");
  const [loading, setLoading] = createSignal(true);
  const [error, setError] = createSignal<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${DAEMON_BASE}/sisoul/metrics`);
      if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
      const text = await res.text();
      setRaw(text);
      setMetrics(parsePrometheus(text));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  onMount(() => {
    refresh();
    const handle = setInterval(refresh, 30_000); // 30s auto-refresh
    return () => clearInterval(handle);
  });

  const info = () => metrics().find((m) => m.name === "sisoul_info");
  const gauges = () => metrics().filter((m) => m.name !== "sisoul_info");

  return (
    <div class="space-y-4 p-2">
      <header class="flex items-start justify-between">
        <div>
          <h1 class="text-2xl font-bold text-sisoul-text">Stats (Prometheus)</h1>
          <p class="text-sm text-sisoul-muted">
            Live metrics from daemon /sisoul/metrics · auto-refresh 30s
          </p>
        </div>
        <button
          class="text-sm text-sisoul-accent hover:underline"
          onClick={refresh}
        >
          ↻ refresh now
        </button>
      </header>

      <Show when={error()}>
        <div class="border border-red-500/40 bg-red-500/10 rounded-lg p-3 text-sm">
          <strong>Daemon error</strong>: {error()}
          <p class="text-xs text-sisoul-muted mt-1">
            Start daemon: <code>sisoul daemon start --background</code>
          </p>
        </div>
      </Show>

      <Show when={!loading() && !error() && info()}>
        <section class="border border-sisoul-border rounded-lg p-4">
          <h2 class="text-sm text-sisoul-muted mb-2">Daemon Info</h2>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <div>
              <span class="text-sisoul-muted">version: </span>
              <code class="font-mono">{info()?.labels.version || "?"}</code>
            </div>
            <div>
              <span class="text-sisoul-muted">phase: </span>
              <code class="font-mono">{info()?.labels.phase || "?"}</code>
            </div>
          </div>
        </section>
      </Show>

      <Show when={!loading() && gauges().length > 0}>
        <section>
          <h2 class="text-lg font-semibold mb-2">Gauges</h2>
          <div class="grid grid-cols-2 gap-3">
            <For each={gauges()}>
              {(m) => (
                <div class="border border-sisoul-border rounded-lg p-3">
                  <div class="text-2xl font-bold">{m.value}</div>
                  <code class="text-xs text-sisoul-muted">{m.name}</code>
                  <Show when={m.help}>
                    <p class="text-xs text-sisoul-muted mt-1">{m.help}</p>
                  </Show>
                </div>
              )}
            </For>
          </div>
        </section>
      </Show>

      <Show when={raw()}>
        <details>
          <summary class="cursor-pointer text-sm text-sisoul-muted">Raw exposition</summary>
          <pre class="text-xs bg-sisoul-bg p-3 rounded border border-sisoul-border mt-2 overflow-x-auto">{raw()}</pre>
        </details>
      </Show>

      <section class="text-xs text-sisoul-muted">
        <p><strong>Integration</strong>: this is the same endpoint Grafana/Prometheus scrape. Example scrape config:</p>
        <pre class="bg-sisoul-bg p-2 rounded mt-1 overflow-x-auto">scrape_configs:
  - job_name: sisoul
    static_configs:
      - targets: ['127.0.0.1:9876']
    metrics_path: /sisoul/metrics</pre>
      </section>
    </div>
  );
}

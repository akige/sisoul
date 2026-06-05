// PWA /skills/v2 · v2 Skill Marketplace UI · 真接 /v2/skill/install + /v2/skill/list.
import { createSignal, onMount, For, Show } from "solid-js";
import { listSkillsInstalled } from "../api/v2";

interface InstallForm {
  name: string;
  version: string;
  entry: string;
  runtime: string;
  ipfs_cid: string;
  author_did: string;
}

const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE || "http://127.0.0.1:9876";

const EXAMPLE_SKILLS = [
  {
    name: "rust-async-expert",
    version: "0.1.0",
    entry: "main.py",
    runtime: "python",
    ipfs_cid: "bafyreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zi",
    author_did: "did:key:z6MkExpertAlice",
    description: "Rust async patterns + tokio + select! anti-patterns",
  },
  {
    name: "pr-auto-review",
    version: "0.2.0",
    entry: "main.py",
    runtime: "python",
    ipfs_cid: "bafyreierlucqf4qyq3xpzz2dcz5gpsq6gz",
    author_did: "did:key:z6MkPRBobReviewer",
    description: "Automated PR review with style guide enforcement",
  },
];

export default function SkillsV2() {
  const [installed, setInstalled] = createSignal<string[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [error, setError] = createSignal<string | null>(null);
  const [form, setForm] = createSignal<InstallForm>({
    name: "", version: "0.1.0", entry: "main.py", runtime: "python",
    ipfs_cid: "", author_did: "",
  });
  const [installResult, setInstallResult] = createSignal<string | null>(null);

  const refresh = async () => {
    try {
      const r = await listSkillsInstalled();
      setInstalled(r.skills);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  onMount(refresh);

  const handleInstall = async () => {
    const f = form();
    if (!f.name || !f.ipfs_cid || !f.author_did) {
      setInstallResult("ERROR: name + ipfs_cid + author_did required");
      return;
    }
    try {
      const res = await fetch(`${DAEMON_BASE}/v2/skill/install`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...f,
          sigstore_sig: "mock-sig-foundation",
          skip_sigstore: true,
        }),
      });
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
      const data = await res.json();
      setInstallResult(`OK installed ${data.skill_name} → ${data.install_path}`);
      await refresh();
    } catch (e) {
      setInstallResult(`ERROR: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleUninstall = async (name: string) => {
    try {
      const res = await fetch(`${DAEMON_BASE}/v2/skill/${name}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`${res.status}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const pickExample = (i: number) => {
    const s = EXAMPLE_SKILLS[i];
    setForm({
      name: s.name, version: s.version, entry: s.entry, runtime: s.runtime,
      ipfs_cid: s.ipfs_cid, author_did: s.author_did,
    });
  };

  return (
    <div class="space-y-4 p-2">
      <header>
        <h1 class="text-2xl font-bold text-sisoul-text">Skill Marketplace (v2.0)</h1>
        <p class="text-sm text-sisoul-muted">
          真接 daemon /v2/skill/*. Foundation: mock IPFS CID + mock sigstore. v2.0 ship (T+11m): 真 IPFS pull + cosign verify + hot-load.
        </p>
      </header>

      <Show when={error()}>
        <div class="border border-red-500/40 bg-red-500/10 rounded-lg p-3 text-sm">
          <strong>Daemon error</strong>: {error()}
        </div>
      </Show>

      {/* Installed list */}
      <section>
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-lg font-semibold">Installed ({installed().length})</h2>
          <button
            class="text-xs text-sisoul-accent hover:underline"
            onClick={refresh}
          >
            ↻ refresh
          </button>
        </div>
        <Show
          when={installed().length > 0}
          fallback={
            <div class="text-sm text-sisoul-muted border border-sisoul-border rounded p-3">
              No skills installed. Use form below or pick an example.
            </div>
          }
        >
          <ul class="space-y-1">
            <For each={installed()}>
              {(s) => (
                <li class="flex items-center justify-between border border-sisoul-border rounded p-2 text-sm">
                  <code>{s}</code>
                  <button
                    class="text-xs text-red-400 hover:underline"
                    onClick={() => handleUninstall(s)}
                  >
                    uninstall
                  </button>
                </li>
              )}
            </For>
          </ul>
        </Show>
      </section>

      {/* Install form */}
      <section class="border border-sisoul-border rounded-lg p-4 space-y-3">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-semibold">Install Skill</h2>
          <div class="flex gap-2">
            <For each={EXAMPLE_SKILLS}>
              {(s, i) => (
                <button
                  class="text-xs px-2 py-1 border border-sisoul-border rounded hover:bg-sisoul-bg/50"
                  onClick={() => pickExample(i())}
                >
                  {s.name}
                </button>
              )}
            </For>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2 text-sm">
          <div>
            <label class="block text-xs text-sisoul-muted">Name</label>
            <input
              class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1"
              value={form().name}
              onInput={(e) => setForm({ ...form(), name: e.currentTarget.value })}
            />
          </div>
          <div>
            <label class="block text-xs text-sisoul-muted">Version</label>
            <input
              class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1"
              value={form().version}
              onInput={(e) => setForm({ ...form(), version: e.currentTarget.value })}
            />
          </div>
          <div>
            <label class="block text-xs text-sisoul-muted">Entry</label>
            <input
              class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1"
              value={form().entry}
              onInput={(e) => setForm({ ...form(), entry: e.currentTarget.value })}
            />
          </div>
          <div>
            <label class="block text-xs text-sisoul-muted">Runtime</label>
            <select
              class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1"
              value={form().runtime}
              onChange={(e) => setForm({ ...form(), runtime: e.currentTarget.value })}
            >
              <option>python</option>
              <option>node</option>
              <option>rust</option>
              <option>wasm</option>
            </select>
          </div>
          <div class="col-span-2">
            <label class="block text-xs text-sisoul-muted">IPFS CID</label>
            <input
              class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1 font-mono"
              value={form().ipfs_cid}
              onInput={(e) => setForm({ ...form(), ipfs_cid: e.currentTarget.value })}
              placeholder="bafyreig..."
            />
          </div>
          <div class="col-span-2">
            <label class="block text-xs text-sisoul-muted">Author DID</label>
            <input
              class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1 font-mono"
              value={form().author_did}
              onInput={(e) => setForm({ ...form(), author_did: e.currentTarget.value })}
              placeholder="did:key:z6Mk..."
            />
          </div>
        </div>

        <div class="flex items-center gap-3">
          <button
            class="bg-sisoul-accent text-sisoul-bg px-4 py-2 rounded font-semibold hover:bg-sisoul-accentDim"
            onClick={handleInstall}
          >
            Install (skip sigstore for foundation)
          </button>
          <Show when={installResult()}>
            <span class={installResult()!.startsWith("OK") ? "text-sisoul-accent text-sm" : "text-red-400 text-sm"}>
              {installResult()}
            </span>
          </Show>
        </div>
      </section>

      <section class="text-xs text-sisoul-muted space-y-1">
        <p><strong>Foundation notes</strong>:</p>
        <ul class="ml-4 list-disc">
          <li>IPFS pull 是 mock (file 不真下载, 只验 CID format)</li>
          <li>sigstore verify skip (foundation 无真 cosign sig)</li>
          <li>hot-load 推后 (v2.0 ship 时 importlib + Wasm sandbox)</li>
          <li>SIS pricing UI 待 v3.0 ship (现 micropayment 仅 schema)</li>
        </ul>
      </section>
    </div>
  );
}

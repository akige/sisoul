// PWA /ask 路由 · 真接 sisoul daemon V2AskPipeline (foundation route, 待 daemon endpoint 接通后真用).
import { createSignal, Show, For } from "solid-js";
import { searchCases, attestProvenance } from "../api/v2";
import type { Case } from "../api/v2";

interface AskState {
  query: string;
  did_asker: string;
  answer: string;
  citedCases: Case[];
  attestationUid: string;
  loading: boolean;
  error: string | null;
}

export default function Ask() {
  const [query, setQuery] = createSignal("");
  const [didAsker, setDidAsker] = createSignal("did:key:z6MkAlice");
  const [state, setState] = createSignal<AskState | null>(null);

  const handleAsk = async () => {
    const q = query().trim();
    if (!q) return;
    setState({
      query: q, did_asker: didAsker(), answer: "", citedCases: [],
      attestationUid: "", loading: true, error: null,
    });
    try {
      // 1. 检索 cases
      const retrieval = await searchCases(q, 3);
      // 2. mock answer (v2.0 ship 后接真 LLM)
      const mockAnswer = retrieval.is_hit
        ? `[v2 mock answer for '${q}' using ${retrieval.cases.length} cases. Top: ${retrieval.cases[0]?.id}]`
        : `[v2 mock answer for '${q}' (no cases hit)]`;
      // 3. attest provenance
      const att = await attestProvenance(
        `resp-${Date.now()}`, q, mockAnswer, didAsker(),
        retrieval.cases.map((c) => ({ source_id: c.id, did_author: c.did_author })),
        "mock"
      );
      setState({
        query: q, did_asker: didAsker(), answer: mockAnswer,
        citedCases: retrieval.cases, attestationUid: att.attestation_uid,
        loading: false, error: null,
      });
    } catch (e) {
      setState({
        query: q, did_asker: didAsker(), answer: "", citedCases: [],
        attestationUid: "", loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  };

  return (
    <div class="space-y-4 p-2">
      <header>
        <h1 class="text-2xl font-bold text-sisoul-text">Ask (v2 Pipeline)</h1>
        <p class="text-sm text-sisoul-muted">
          真接 daemon /v2/case/search + /v2/provenance/attest. Foundation: mock answer + mock EAS.
          Full impl (v2.0 ship T+8-12m): 真 LLM call + 真 Optimism EAS.
        </p>
      </header>

      <div class="border border-sisoul-border rounded-lg p-4 space-y-3">
        <div>
          <label class="block text-xs text-sisoul-muted mb-1">DID (asker)</label>
          <input
            class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1 font-mono text-sm"
            value={didAsker()}
            onInput={(e) => setDidAsker(e.currentTarget.value)}
            placeholder="did:key:z6Mk..."
          />
        </div>
        <div>
          <label class="block text-xs text-sisoul-muted mb-1">Question</label>
          <textarea
            class="w-full bg-sisoul-bg border border-sisoul-border rounded px-2 py-1 text-sm"
            rows="3"
            value={query()}
            onInput={(e) => setQuery(e.currentTarget.value)}
            placeholder="How to fix Rust async tokio::select deadlock?"
          />
        </div>
        <button
          class="bg-sisoul-accent text-sisoul-bg px-4 py-2 rounded font-semibold hover:bg-sisoul-accentDim disabled:opacity-50"
          disabled={state()?.loading}
          onClick={handleAsk}
        >
          {state()?.loading ? "Asking..." : "Ask"}
        </button>
      </div>

      <Show when={state()?.error}>
        <div class="border border-red-500/40 bg-red-500/10 rounded-lg p-3 text-sm">
          <strong>Error</strong>: {state()!.error}
          <p class="text-xs text-sisoul-muted mt-1">
            启动 daemon: <code>sisoul daemon start</code>
          </p>
        </div>
      </Show>

      <Show when={state() && !state()!.loading && !state()!.error && state()!.answer}>
        <div class="border border-sisoul-border rounded-lg p-4 space-y-3">
          <h2 class="text-lg font-semibold">Answer</h2>
          <pre class="text-sm whitespace-pre-wrap bg-sisoul-bg p-3 rounded border border-sisoul-border">{state()!.answer}</pre>

          <Show when={state()!.citedCases.length > 0}>
            <div>
              <h3 class="text-sm font-semibold text-sisoul-muted mb-2">
                Citations ({state()!.citedCases.length})
              </h3>
              <ul class="space-y-1 text-sm">
                <For each={state()!.citedCases}>
                  {(c) => (
                    <li class="border border-sisoul-border rounded p-2">
                      <div class="font-semibold">{c.question}</div>
                      <div class="text-xs text-sisoul-muted font-mono">
                        {c.id} · {c.did_author.slice(0, 20)}…
                      </div>
                    </li>
                  )}
                </For>
              </ul>
            </div>
          </Show>

          <div class="text-xs text-sisoul-muted font-mono">
            <strong>Provenance UID</strong>: {state()!.attestationUid}
          </div>
        </div>
      </Show>

      <section class="text-xs text-sisoul-muted space-y-1">
        <p><strong>Note</strong> (foundation):</p>
        <ul class="ml-4 list-disc">
          <li>Answer 是 mock (foundation impl). v2.0 ship 接真 LLM provider adapter</li>
          <li>Attestation 是 mock SHA256. v2.0 ship 走真 Optimism EAS</li>
          <li>Case retrieval 走 TfIdf foundation. v2.0 ship 切 ChromaDB embed</li>
        </ul>
      </section>
    </div>
  );
}

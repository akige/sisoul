// Advanced 路由 · Phase 3 链上 attestation + 链上历史
// daemon endpoint: GET /sisoul/attest/history
import { createResource, For, Show } from "solid-js";
import { getAttestHistory, type AttestEntry } from "../api/daemon";
import { formatDate } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";

function AttestRow(props: { entry: AttestEntry }) {
  const e = () => props.entry;
  return (
    <div
      class="border border-sisoul-border rounded p-3 space-y-1 hover:border-sisoul-accentDim transition-colors"
      data-testid="attest-row"
    >
      <div class="flex items-center justify-between gap-2">
        <span class="text-xs font-mono text-sisoul-accent truncate">{e().uid}</span>
        <span class="text-xs text-sisoul-muted shrink-0">{e().chain}</span>
      </div>
      <div class="flex gap-4 text-xs text-sisoul-muted">
        <span>schema: <span class="font-mono text-sisoul-text">{e().schema}</span></span>
        <span>时间: <span class="font-mono">{formatDate(new Date(e().timestamp * 1000).toISOString())}</span></span>
      </div>
    </div>
  );
}

function AttestHistory() {
  const [data] = createResource(() => getAttestHistory());

  return (
    <Show
      when={data()}
      fallback={<div class="text-sisoul-muted text-sm">加载 attestation 历史...</div>}
    >
      {(d) => (
        <Show
          when={d().history.length > 0}
          fallback={
            <div class="text-sisoul-muted text-sm py-8 text-center">
              暂无链上 attestation 记录 · Phase 3 功能
            </div>
          }
        >
          <div class="space-y-2">
            <For each={d().history}>{(entry) => <AttestRow entry={entry} />}</For>
          </div>
        </Show>
      )}
    </Show>
  );
}

export default function Advanced() {
  return (
    <div class="space-y-8 max-w-3xl" data-route="advanced">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Advanced</h1>
        <p class="text-sm text-sisoul-muted mt-1">
          链上 attestation (EAS Sepolia) · Arweave 快照 · Phase 3/4 功能
        </p>
      </div>

      {/* Attestation history */}
      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          Attestation 历史
        </h2>
        <AsyncBoundary>
          <AttestHistory />
        </AsyncBoundary>
      </section>

      {/* Phase placeholders */}
      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          功能路线图
        </h2>
        <div class="space-y-2 text-sm">
          {[
            { phase: "Phase 3", status: "live", label: "EAS Sepolia attestation" },
            { phase: "Phase 3", status: "live", label: "Arweave vault 快照" },
            { phase: "Phase 4", status: "planned", label: "IPFS 技能分发" },
            { phase: "Phase 5", status: "planned", label: "ENS / .sisoul.eth.limo 部署" },
          ].map((item) => (
            <div class="flex items-center gap-3 py-2 border-b border-sisoul-border/50">
              <span class="text-xs font-mono text-sisoul-muted w-16 shrink-0">
                {item.phase}
              </span>
              <span
                class="text-xs px-2 py-0.5 rounded font-mono shrink-0"
                classList={{
                  "bg-sisoul-success/20 text-sisoul-success": item.status === "live",
                  "bg-sisoul-border text-sisoul-muted": item.status === "planned",
                }}
              >
                {item.status}
              </span>
              <span class="text-sisoul-text">{item.label}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

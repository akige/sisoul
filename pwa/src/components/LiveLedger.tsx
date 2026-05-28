// LiveLedger · 实时账本组件
//
// 走 SSE /sisoul/notify/stream 订阅 ledger.entry, 每条立刷.
// 支持 friend_did 过滤 + direction 过滤 (borrow / lend / both).
//
// 用法:
//   <LiveLedger friendDid="did:key:z6Mk..." direction="borrow" pageSize={50} />
//   <LiveLedger direction="lend" />  // 全朋友 lend ledger

import {
  createSignal,
  createResource,
  onCleanup,
  onMount,
  For,
  Show,
  createMemo,
} from "solid-js";
import {
  getLedger,
  getLedgerAll,
  notifyStream,
  type LedgerEntry,
  type NotifyEvent,
  type NotifyStreamHandle,
} from "../api/daemon";
import { formatDate, truncateDid } from "../utils/format";

interface Props {
  // 过滤特定朋友; 不填 = 全部
  friendDid?: string;
  // 过滤方向; 不填 = 全部
  direction?: "borrow" | "lend";
  // 显示多少条 (按 ended_at desc); 默认 50
  pageSize?: number;
  // 隐藏标题 (嵌入其他组件时)
  hideTitle?: boolean;
  // 由父级指定的额外 class
  class?: string;
}

const STATUS_COLOR: Record<LedgerEntry["status"], string> = {
  active: "bg-sisoul-accentDim text-sisoul-accent",
  completed: "bg-sisoul-success/20 text-sisoul-success",
  denied: "bg-sisoul-border text-sisoul-muted",
  error: "bg-sisoul-danger/20 text-sisoul-danger",
};

const DIRECTION_LABEL: Record<LedgerEntry["direction"], string> = {
  borrow: "借入",
  lend: "借出",
};

const DIRECTION_COLOR: Record<LedgerEntry["direction"], string> = {
  borrow: "text-sisoul-accent",
  lend: "text-sisoul-success",
};

function entryMatches(
  e: LedgerEntry,
  friendDid?: string,
  direction?: "borrow" | "lend"
): boolean {
  if (friendDid && e.counterparty_did !== friendDid) return false;
  if (direction && e.direction !== direction) return false;
  return true;
}

function sortDesc(entries: LedgerEntry[]): LedgerEntry[] {
  return [...entries].sort((a, b) => {
    const ax = a.ended_at ?? a.started_at;
    const bx = b.ended_at ?? b.started_at;
    return bx.localeCompare(ax);
  });
}

export default function LiveLedger(props: Props) {
  const pageSize = () => props.pageSize ?? 50;

  // 初始 snapshot fetch
  const [initial] = createResource(
    () => ({ fid: props.friendDid, dir: props.direction }),
    async (k) => {
      if (k.fid) return getLedger(k.fid, k.dir);
      return getLedgerAll(k.dir);
    }
  );

  // 实时增量 (SSE pushed entries) — 跟 snapshot 合并去重
  const [liveEntries, setLiveEntries] = createSignal<LedgerEntry[]>([]);
  const [streamState, setStreamState] = createSignal<"connecting" | "open" | "closed" | "error">(
    "connecting"
  );

  let handle: NotifyStreamHandle | null = null;

  onMount(() => {
    handle = notifyStream(
      (ev: NotifyEvent) => {
        if (ev.type === "ledger.entry") {
          const entry = ev.data;
          if (!entryMatches(entry, props.friendDid, props.direction)) return;
          setLiveEntries((prev) => {
            // 去重 by entry_id (replace if exists)
            const filtered = prev.filter((p) => p.entry_id !== entry.entry_id);
            return [entry, ...filtered].slice(0, pageSize() * 2);
          });
          setStreamState("open");
        } else if (ev.type === "heartbeat") {
          setStreamState("open");
        }
      },
      () => setStreamState("error")
    );
    // 检查 readyState
    setTimeout(() => {
      if (handle && handle.readyState() === 1) setStreamState("open");
    }, 100);
  });

  onCleanup(() => {
    if (handle) handle.close();
  });

  const merged = createMemo(() => {
    const base = initial()?.entries ?? [];
    const live = liveEntries();
    // 合并去重 (live entry 优先, 因为更新)
    const seen = new Set<string>();
    const out: LedgerEntry[] = [];
    for (const e of live) {
      if (seen.has(e.entry_id)) continue;
      seen.add(e.entry_id);
      out.push(e);
    }
    for (const e of base) {
      if (seen.has(e.entry_id)) continue;
      seen.add(e.entry_id);
      out.push(e);
    }
    return sortDesc(out).slice(0, pageSize());
  });

  const totalTokens = createMemo(() =>
    merged().reduce((s, e) => s + (e.tokens_used ?? 0), 0)
  );

  const totalCost = createMemo(() =>
    merged().reduce((s, e) => s + (e.cost_usd ?? 0), 0)
  );

  return (
    <div
      class={`space-y-3 ${props.class ?? ""}`}
      data-testid="live-ledger"
      data-friend-did={props.friendDid ?? ""}
      data-direction={props.direction ?? "all"}
    >
      <Show when={!props.hideTitle}>
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-semibold text-sisoul-text">
            实时账本
            <Show when={props.direction}>
              <span class="ml-2 text-xs text-sisoul-muted">
                · {DIRECTION_LABEL[props.direction!]}
              </span>
            </Show>
          </h3>
          <span
            class="text-xs font-mono"
            classList={{
              "text-sisoul-success": streamState() === "open",
              "text-sisoul-muted": streamState() === "connecting",
              "text-sisoul-danger": streamState() === "error" || streamState() === "closed",
            }}
            data-testid="ledger-stream-state"
          >
            {streamState() === "open" && "● live"}
            {streamState() === "connecting" && "○ 连接中"}
            {streamState() === "error" && "✕ 断开"}
            {streamState() === "closed" && "○ 已关"}
          </span>
        </div>
      </Show>

      <div class="flex gap-4 text-xs text-sisoul-muted font-mono">
        <span data-testid="ledger-total-tokens">
          tokens: <span class="text-sisoul-text">{totalTokens().toLocaleString()}</span>
        </span>
        <span data-testid="ledger-total-cost">
          cost: <span class="text-sisoul-text">${totalCost().toFixed(4)}</span>
        </span>
        <span class="ml-auto" data-testid="ledger-row-count">
          {merged().length} 行
        </span>
      </div>

      <Show
        when={merged().length > 0}
        fallback={
          <div class="text-xs text-sisoul-muted py-6 text-center">暂无账本记录</div>
        }
      >
        <ul class="space-y-2" data-testid="ledger-list">
          <For each={merged()}>
            {(e) => (
              <li
                class="border border-sisoul-border rounded-md p-3 text-xs font-mono space-y-1"
                data-testid="ledger-entry"
                data-entry-id={e.entry_id}
              >
                <div class="flex items-center justify-between gap-2">
                  <span class={DIRECTION_COLOR[e.direction]}>
                    {DIRECTION_LABEL[e.direction]}
                  </span>
                  <span class={`px-2 py-0.5 rounded ${STATUS_COLOR[e.status]}`}>
                    {e.status}
                  </span>
                </div>
                <div class="text-sisoul-muted">
                  <span class="text-sisoul-text">
                    {e.counterparty_handle ?? truncateDid(e.counterparty_did)}
                  </span>
                  <span class="mx-1">·</span>
                  <span>{e.provider}/{e.model}</span>
                </div>
                <div class="flex gap-3 text-sisoul-muted">
                  <span>tokens: <span class="text-sisoul-text">{e.tokens_used.toLocaleString()}</span></span>
                  <Show when={e.cost_usd != null}>
                    <span>cost: <span class="text-sisoul-text">${e.cost_usd!.toFixed(4)}</span></span>
                  </Show>
                  <span class="ml-auto">{formatDate(e.ended_at ?? e.started_at)}</span>
                </div>
              </li>
            )}
          </For>
        </ul>
      </Show>
    </div>
  );
}

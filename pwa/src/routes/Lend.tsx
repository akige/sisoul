// Lend 路由 · 被动接朋友 borrow 请求
//
// daemon endpoints:
//   GET  /sisoul/lend/list
//   POST /sisoul/lend/approve
//   POST /sisoul/lend/deny
//
// 实时:
//   SSE lend.request → 新 pending 入列 (含 emergency 弹窗高亮)

import {
  createSignal,
  createResource,
  createMemo,
  onMount,
  onCleanup,
  For,
  Show,
} from "solid-js";
import { A, useSearchParams } from "@solidjs/router";
import {
  lendList,
  lendApprove,
  lendDeny,
  notifyStream,
  type LendRequestItem,
  type NotifyEvent,
  type NotifyStreamHandle,
  DaemonError,
} from "../api/daemon";
import { formatDate, truncateDid, relativeTime } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";
import LiveLedger from "../components/LiveLedger";

interface ToastMsg {
  id: number;
  kind: "info" | "warning" | "danger" | "success";
  text: string;
}

let toastCounter = 0;

interface LendCardProps {
  req: LendRequestItem;
  highlight: boolean;
  onApprove: (req: LendRequestItem, durationMin: number, maxTokens?: number) => void;
  onDeny: (req: LendRequestItem, reason?: string) => void;
}

function LendRequestCard(props: LendCardProps) {
  const r = () => props.req;
  const [expanded, setExpanded] = createSignal(false);
  const [duration, setDuration] = createSignal(30);
  const [maxTokens, setMaxTokens] = createSignal<number | "">("");
  const [denyReason, setDenyReason] = createSignal("");
  const [busy, setBusy] = createSignal(false);

  const expiresIn = createMemo(() => {
    const left = new Date(r().expires_at).getTime() - Date.now();
    if (left <= 0) return "已过期";
    const min = Math.round(left / 60_000);
    if (min < 60) return `${min} 分钟内`;
    return `${Math.round(min / 60)} 小时内`;
  });

  const doApprove = async () => {
    setBusy(true);
    try {
      await props.onApprove(
        r(),
        duration(),
        maxTokens() === "" ? undefined : Number(maxTokens())
      );
    } finally {
      setBusy(false);
    }
  };

  const doDeny = async () => {
    setBusy(true);
    try {
      await props.onDeny(r(), denyReason() || undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <li
      class="border rounded-lg p-4 space-y-3 transition-colors"
      classList={{
        "border-sisoul-danger": r().emergency_flag,
        "border-sisoul-accent": props.highlight && !r().emergency_flag,
        "border-sisoul-border": !props.highlight && !r().emergency_flag,
        "bg-sisoul-danger/5": r().emergency_flag,
      }}
      data-testid="lend-request-card"
      data-request-id={r().request_id}
      data-emergency={r().emergency_flag ? "true" : "false"}
    >
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <Show when={r().emergency_flag}>
            <span
              class="text-xs px-2 py-0.5 rounded font-mono bg-sisoul-danger text-white shrink-0"
              data-testid="lend-emergency-badge"
            >
              EMERGENCY
            </span>
          </Show>
          <span class="font-mono text-sm text-sisoul-text truncate">
            {r().borrower_handle ?? truncateDid(r().borrower_did)}
          </span>
        </div>
        <span class="text-xs font-mono text-sisoul-muted shrink-0">
          {relativeTime(r().created_at)}
        </span>
      </div>

      <div class="text-xs font-mono text-sisoul-muted space-y-1">
        <p>
          provider/model:{" "}
          <span class="text-sisoul-text">
            {r().provider}/{r().model}
          </span>
        </p>
        <p>
          token_count: <span class="text-sisoul-text">{r().token_count.toLocaleString()}</span>
        </p>
        <p>
          DID: <span class="text-sisoul-text">{truncateDid(r().borrower_did)}</span>
        </p>
        <p>
          过期: <span class="text-sisoul-text">{expiresIn()}</span>
        </p>
        <Show when={r().reason}>
          <p>
            理由: <span class="text-sisoul-text">{r().reason}</span>
          </p>
        </Show>
      </div>

      <div class="flex gap-2">
        <button
          type="button"
          class="flex-1 px-3 py-1.5 text-xs font-mono rounded bg-sisoul-success/20 text-sisoul-success hover:bg-sisoul-success/30 disabled:opacity-50"
          onClick={doApprove}
          disabled={busy()}
          data-testid="lend-approve-btn"
        >
          {busy() ? "处理中..." : "Approve"}
        </button>
        <button
          type="button"
          class="flex-1 px-3 py-1.5 text-xs font-mono rounded bg-sisoul-danger/20 text-sisoul-danger hover:bg-sisoul-danger/30 disabled:opacity-50"
          onClick={doDeny}
          disabled={busy()}
          data-testid="lend-deny-btn"
        >
          Deny
        </button>
        <button
          type="button"
          class="px-3 py-1.5 text-xs font-mono rounded border border-sisoul-border text-sisoul-muted hover:text-sisoul-text"
          onClick={() => setExpanded((x) => !x)}
          data-testid="lend-toggle-advanced"
        >
          {expanded() ? "▴" : "▾"}
        </button>
      </div>

      <Show when={expanded()}>
        <div
          class="space-y-2 pt-2 border-t border-sisoul-border"
          data-testid="lend-advanced"
        >
          <div class="grid grid-cols-2 gap-2">
            <label class="block space-y-1">
              <span class="text-[10px] text-sisoul-muted font-mono">
                批准时长 (分钟)
              </span>
              <input
                type="number"
                min="1"
                class="w-full px-2 py-1 bg-sisoul-bg border border-sisoul-border rounded font-mono text-xs"
                value={duration()}
                onInput={(e) => setDuration(Number(e.currentTarget.value))}
                data-testid="lend-duration-input"
              />
            </label>
            <label class="block space-y-1">
              <span class="text-[10px] text-sisoul-muted font-mono">
                最大 tokens (可选)
              </span>
              <input
                type="number"
                min="0"
                class="w-full px-2 py-1 bg-sisoul-bg border border-sisoul-border rounded font-mono text-xs"
                value={maxTokens()}
                onInput={(e) => {
                  const v = e.currentTarget.value;
                  setMaxTokens(v === "" ? "" : Number(v));
                }}
                data-testid="lend-max-tokens-input"
              />
            </label>
          </div>
          <label class="block space-y-1">
            <span class="text-[10px] text-sisoul-muted font-mono">
              拒绝理由 (可选, deny 时发给对方)
            </span>
            <input
              type="text"
              class="w-full px-2 py-1 bg-sisoul-bg border border-sisoul-border rounded font-mono text-xs"
              value={denyReason()}
              onInput={(e) => setDenyReason(e.currentTarget.value)}
              data-testid="lend-deny-reason-input"
              placeholder="quota exhausted / not now / etc."
            />
          </label>
          <A
            href="/friends"
            class="block text-center text-xs font-mono text-sisoul-accent hover:underline"
            data-testid="lend-set-perms-link"
          >
            → 设置 per-friend perms (Friends 页)
          </A>
        </div>
      </Show>
    </li>
  );
}

function Toast(props: { toasts: ToastMsg[]; onDismiss: (id: number) => void }) {
  return (
    <div class="fixed top-4 right-4 z-50 space-y-2 max-w-sm" data-testid="lend-toasts">
      <For each={props.toasts}>
        {(t) => (
          <div
            class="px-4 py-3 rounded-lg shadow-lg border text-sm font-mono cursor-pointer"
            classList={{
              "bg-sisoul-success/20 border-sisoul-success text-sisoul-success":
                t.kind === "success",
              "bg-sisoul-accentDim border-sisoul-accent text-sisoul-accent": t.kind === "info",
              "bg-sisoul-danger/20 border-sisoul-danger text-sisoul-danger": t.kind === "danger",
              "bg-sisoul-warn/20 border-sisoul-warn text-sisoul-warn":
                t.kind === "warning",
            }}
            onClick={() => props.onDismiss(t.id)}
            data-testid="lend-toast"
            data-toast-kind={t.kind}
          >
            {t.text}
          </div>
        )}
      </For>
    </div>
  );
}

function LendContent() {
  const [searchParams] = useSearchParams();
  const [data, { refetch }] = createResource(() => lendList());
  const [localRequests, setLocalRequests] = createSignal<LendRequestItem[]>([]);
  const [toasts, setToasts] = createSignal<ToastMsg[]>([]);
  const [highlightId, setHighlightId] = createSignal<string | null>(null);

  // sync resource → local
  createMemo(() => {
    const d = data();
    if (d) setLocalRequests(d.requests);
  });

  const pushToast = (kind: ToastMsg["kind"], text: string) => {
    const id = ++toastCounter;
    setToasts((prev) => [...prev, { id, kind, text }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  };

  const dismissToast = (id: number) =>
    setToasts((prev) => prev.filter((t) => t.id !== id));

  let handle: NotifyStreamHandle | null = null;
  onMount(() => {
    handle = notifyStream((ev: NotifyEvent) => {
      if (ev.type === "lend.request") {
        const r = ev.data;
        setLocalRequests((prev) => {
          // 去重
          if (prev.some((p) => p.request_id === r.request_id)) return prev;
          return [r, ...prev];
        });
        setHighlightId(r.request_id);
        setTimeout(() => {
          if (highlightId() === r.request_id) setHighlightId(null);
        }, 5000);
        pushToast(
          r.emergency_flag ? "danger" : "info",
          `${r.emergency_flag ? "[EMERGENCY] " : ""}新 borrow 请求 from ${
            r.borrower_handle ?? truncateDid(r.borrower_did)
          } · ${r.token_count} tokens`
        );
      }
    });
  });
  onCleanup(() => {
    if (handle) handle.close();
  });

  const onApprove = async (
    req: LendRequestItem,
    durationMin: number,
    maxTokens?: number
  ) => {
    try {
      const resp = await lendApprove({
        request_id: req.request_id,
        duration_minutes: durationMin,
        max_tokens: maxTokens,
      });
      setLocalRequests((prev) => prev.filter((p) => p.request_id !== req.request_id));
      pushToast(
        "success",
        `已批准 → session ${resp.session_id.slice(0, 8)}... 至 ${formatDate(
          resp.expires_at
        )}`
      );
      refetch();
    } catch (e) {
      if (e instanceof DaemonError) {
        pushToast("danger", `批准失败 ${e.status}: ${e.message}`);
      } else {
        pushToast("danger", `批准失败 ${String(e)}`);
      }
    }
  };

  const onDeny = async (req: LendRequestItem, reason?: string) => {
    try {
      await lendDeny({ request_id: req.request_id, reason });
      setLocalRequests((prev) => prev.filter((p) => p.request_id !== req.request_id));
      pushToast("info", `已拒绝 ${req.request_id.slice(0, 12)}`);
      refetch();
    } catch (e) {
      if (e instanceof DaemonError) {
        pushToast("danger", `拒绝失败 ${e.status}: ${e.message}`);
      } else {
        pushToast("danger", `拒绝失败 ${String(e)}`);
      }
    }
  };

  const filtered = createMemo(() => {
    const friendFilter = searchParams.friend as string | undefined;
    let xs = localRequests();
    if (friendFilter) xs = xs.filter((r) => r.borrower_did === friendFilter);
    // emergency 排前
    return [...xs].sort((a, b) => {
      if (a.emergency_flag && !b.emergency_flag) return -1;
      if (!a.emergency_flag && b.emergency_flag) return 1;
      return b.created_at.localeCompare(a.created_at);
    });
  });

  return (
    <div class="space-y-6">
      <Toast toasts={toasts()} onDismiss={dismissToast} />

      <section class="space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-semibold text-sisoul-text">
            Pending requests ({filtered().length})
            <Show when={searchParams.friend}>
              <span class="ml-2 text-xs text-sisoul-muted">
                · 过滤 {truncateDid(searchParams.friend as string)}
              </span>
            </Show>
          </h3>
          <button
            type="button"
            class="text-xs font-mono text-sisoul-muted hover:text-sisoul-text"
            onClick={() => refetch()}
            data-testid="lend-refresh-btn"
          >
            ↻ 刷新
          </button>
        </div>

        <Show
          when={filtered().length > 0}
          fallback={
            <div
              class="text-sm text-sisoul-muted py-8 text-center border border-dashed border-sisoul-border rounded-lg"
              data-testid="lend-empty"
            >
              暂无 pending 请求 · 等朋友发起 borrow
            </div>
          }
        >
          <ul class="grid gap-3 sm:grid-cols-2" data-testid="lend-request-list">
            <For each={filtered()}>
              {(r) => (
                <LendRequestCard
                  req={r}
                  highlight={highlightId() === r.request_id}
                  onApprove={onApprove}
                  onDeny={onDeny}
                />
              )}
            </For>
          </ul>
        </Show>
      </section>

      <section class="space-y-3">
        <h3 class="text-sm font-semibold text-sisoul-text">历史 lend 账本</h3>
        <LiveLedger direction="lend" pageSize={30} hideTitle={true} />
      </section>
    </div>
  );
}

export default function Lend() {
  return (
    <div class="space-y-6 max-w-4xl" data-route="lend">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Lend</h1>
        <p class="text-sm text-sisoul-muted mt-1">
          被动接朋友 borrow 请求 · 实时弹窗 + emergency 高亮 + Approve / Deny
        </p>
      </div>

      <AsyncBoundary>
        <LendContent />
      </AsyncBoundary>
    </div>
  );
}

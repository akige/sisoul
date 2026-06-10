// Borrow 路由 · 主动借朋友配额
//
// daemon endpoints:
//   GET  /sisoul/friend/list
//   POST /sisoul/borrow/run
//   GET  /sisoul/borrow/proxy-list
//   POST /sisoul/borrow/proxy-stop
//   GET  /sisoul/ledger/<did>?direction=borrow
//
// 实时:
//   SSE borrow.update → 更新 progress stages

import {
  createSignal,
  createResource,
  createEffect,
  createMemo,
  onMount,
  onCleanup,
  For,
  Show,
} from "solid-js";
import { useSearchParams } from "@solidjs/router";
import {
  listFriends,
  borrowRun,
  borrowProxyList,
  borrowProxyStop,
  notifyStream,
  type Friend,
  type BorrowStage,
  type ProxySessionItem,
  type NotifyEvent,
  type NotifyStreamHandle,
  DaemonError,
} from "../api/daemon";
import { formatDate, truncateDid, relativeTime } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";
import LiveLedger from "../components/LiveLedger";

// stage 排序 (用于 progress bar)
const STAGES_ORDER: BorrowStage[] = [
  "queued",
  "waku-discover",
  "encrypting",
  "awaiting-approval",
  "llm-streaming",
  "completed",
];

const STAGE_LABEL: Record<BorrowStage, string> = {
  queued: "排队",
  "waku-discover": "Waku 找 peer",
  encrypting: "加密 payload",
  "awaiting-approval": "等 Bob 批准",
  "llm-streaming": "LLM 流式响应",
  completed: "完成",
  denied: "被拒",
  error: "错误",
};

const STAGE_COLOR: Record<BorrowStage, string> = {
  queued: "text-sisoul-muted",
  "waku-discover": "text-sisoul-accent",
  encrypting: "text-sisoul-accent",
  "awaiting-approval": "text-sisoul-warn",
  "llm-streaming": "text-sisoul-success",
  completed: "text-sisoul-success",
  denied: "text-sisoul-danger",
  error: "text-sisoul-danger",
};

const PROVIDERS = [
  "anthropic",
  "openai",
  "google",
  "deepseek",
  "mistral",
  "litellm-proxy",
];

const MODELS_BY_PROVIDER: Record<string, string[]> = {
  anthropic: ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
  openai: ["gpt-5", "gpt-5-mini", "gpt-4o"],
  google: ["gemini-2.5-pro", "gemini-2.5-flash"],
  deepseek: ["deepseek-v3", "deepseek-r1"],
  mistral: ["mistral-large", "codestral"],
  "litellm-proxy": ["auto"],
};

function stageProgressPct(stage: BorrowStage): number {
  if (stage === "denied" || stage === "error") return 100;
  const i = STAGES_ORDER.indexOf(stage);
  if (i < 0) return 0;
  return Math.round(((i + 1) / STAGES_ORDER.length) * 100);
}

interface BorrowInflight {
  request_id: string;
  friend_did: string;
  friend_handle?: string;
  provider: string;
  model: string;
  token_count: number;
  stage: BorrowStage;
  error?: string;
  started_at: string;
}

function BorrowForm(props: {
  friends: Friend[];
  initialFriend?: string;
  onSubmitted: (inflight: BorrowInflight) => void;
}) {
  const [friendDid, setFriendDid] = createSignal(
    props.initialFriend ?? props.friends[0]?.did ?? ""
  );
  const [provider, setProvider] = createSignal("anthropic");
  const [model, setModel] = createSignal("claude-sonnet-4-6");
  const [tokenCount, setTokenCount] = createSignal(2000);
  const [emergency, setEmergency] = createSignal(false);
  const [reason, setReason] = createSignal("");
  const [submitting, setSubmitting] = createSignal(false);
  const [err, setErr] = createSignal<string | null>(null);

  // provider 切换时 reset model
  createEffect(() => {
    const list = MODELS_BY_PROVIDER[provider()] ?? [];
    if (list.length > 0 && !list.includes(model())) {
      setModel(list[0]);
    }
  });

  const submit = async (e: Event) => {
    e.preventDefault();
    setErr(null);
    if (!friendDid()) {
      setErr("请选朋友");
      return;
    }
    if (tokenCount() <= 0) {
      setErr("token_count 必须 > 0");
      return;
    }
    setSubmitting(true);
    const friend = props.friends.find((f) => f.did === friendDid());
    try {
      const resp = await borrowRun({
        friend_did: friendDid(),
        provider: provider(),
        model: model(),
        token_count: tokenCount(),
        emergency_flag: emergency(),
        reason: reason() || undefined,
      });
      // daemon /borrow/run 真返 {session: {session_id, status,
      // lend_request_id, error, ...}}, 不是 {request_id, stage}. 兜底两种 shape
      // 避免 BorrowProgress 渲染 `.slice(0,12)` TypeError on undefined.
      const sess: any =
        (resp as any).session ?? (resp as any);
      const status: string | undefined =
        sess.status ?? (resp as any).stage;
      let stage: BorrowStage = "queued";
      if (status === "completed" || status === "active") {
        stage = "completed";
      } else if (
        status === "lender-timeout" ||
        status === "denied" ||
        status === "rejected"
      ) {
        stage = "denied";
      } else if (status === "error" || status === "failed") {
        stage = "error";
      } else if (typeof status === "string" && status in STAGE_LABEL) {
        stage = status as BorrowStage;
      }
      const reqId: string =
        sess.session_id ??
        sess.lend_request_id ??
        sess.request_id ??
        (resp as any).request_id ??
        `bs_${Date.now()}`;
      props.onSubmitted({
        request_id: reqId,
        friend_did: friendDid(),
        friend_handle: friend?.handle,
        provider: provider(),
        model: model(),
        token_count: tokenCount(),
        stage,
        error: sess.error ?? (resp as any).error ?? undefined,
        started_at: new Date().toISOString(),
      });
    } catch (ex) {
      if (ex instanceof DaemonError) {
        setErr(`daemon ${ex.status}: ${ex.message}`);
      } else {
        setErr(String(ex));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form
      class="space-y-3 border border-sisoul-border rounded-lg p-4"
      onSubmit={submit}
      data-testid="borrow-form"
    >
      <h3 class="text-sm font-semibold text-sisoul-text">主动借</h3>

      <label class="block space-y-1">
        <span class="text-xs text-sisoul-muted font-mono">朋友</span>
        <select
          class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text"
          value={friendDid()}
          onChange={(e) => setFriendDid(e.currentTarget.value)}
          data-testid="borrow-friend-select"
        >
          <For each={props.friends}>
            {(f) => (
              <option value={f.did}>
                {f.handle ?? truncateDid(f.did)} (L{f.trust_level})
              </option>
            )}
          </For>
        </select>
      </label>

      <div class="grid grid-cols-2 gap-3">
        <label class="block space-y-1">
          <span class="text-xs text-sisoul-muted font-mono">Provider</span>
          <select
            class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text"
            value={provider()}
            onChange={(e) => setProvider(e.currentTarget.value)}
            data-testid="borrow-provider-select"
          >
            <For each={PROVIDERS}>{(p) => <option value={p}>{p}</option>}</For>
          </select>
        </label>

        <label class="block space-y-1">
          <span class="text-xs text-sisoul-muted font-mono">Model</span>
          <select
            class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text"
            value={model()}
            onChange={(e) => setModel(e.currentTarget.value)}
            data-testid="borrow-model-select"
          >
            <For each={MODELS_BY_PROVIDER[provider()] ?? []}>
              {(m) => <option value={m}>{m}</option>}
            </For>
          </select>
        </label>
      </div>

      <label class="block space-y-1">
        <span class="text-xs text-sisoul-muted font-mono">
          Token count (估算 prompt+completion)
        </span>
        <input
          type="number"
          min="1"
          step="1"
          class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text"
          value={tokenCount()}
          onInput={(e) => setTokenCount(Number(e.currentTarget.value))}
          data-testid="borrow-token-input"
        />
      </label>

      <label class="block space-y-1">
        <span class="text-xs text-sisoul-muted font-mono">理由 (可选)</span>
        <input
          type="text"
          class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text"
          value={reason()}
          onInput={(e) => setReason(e.currentTarget.value)}
          placeholder="给 Bob 看的 1 句话"
          data-testid="borrow-reason-input"
        />
      </label>

      <label class="flex items-center gap-2 text-xs text-sisoul-muted">
        <input
          type="checkbox"
          checked={emergency()}
          onChange={(e) => setEmergency(e.currentTarget.checked)}
          data-testid="borrow-emergency-input"
        />
        <span>emergency (突破 per-request 限制, 走 emergency-only 通道)</span>
      </label>

      <Show when={err()}>
        <p
          class="text-xs text-sisoul-danger font-mono"
          data-testid="borrow-error"
        >
          {err()}
        </p>
      </Show>

      <button
        type="submit"
        class="w-full px-3 py-2 text-sm font-mono rounded bg-sisoul-accent text-sisoul-bg hover:bg-sisoul-accent/80 disabled:opacity-50"
        disabled={submitting() || props.friends.length === 0}
        data-testid="borrow-submit"
      >
        {submitting() ? "提交中..." : "发起 borrow"}
      </button>
    </form>
  );
}

function BorrowProgress(props: { inflight: BorrowInflight }) {
  const it = () => props.inflight;
  const pct = createMemo(() => stageProgressPct(it().stage));

  return (
    <div
      class="border border-sisoul-border rounded-lg p-4 space-y-3"
      data-testid="borrow-progress"
      data-request-id={it().request_id}
    >
      <div class="flex items-center justify-between">
        <span class="text-sm font-mono text-sisoul-text">
          {it().provider}/{it().model}
        </span>
        <span class={`text-xs font-mono ${STAGE_COLOR[it().stage]}`} data-testid="borrow-stage">
          {STAGE_LABEL[it().stage] ?? it().stage}
        </span>
      </div>
      <p class="text-xs text-sisoul-muted font-mono">
        → {it().friend_handle ?? truncateDid(it().friend_did)} · {it().token_count} tokens
      </p>
      <div class="h-1.5 bg-sisoul-border rounded overflow-hidden">
        <div
          class="h-full transition-all"
          classList={{
            "bg-sisoul-danger": it().stage === "denied" || it().stage === "error",
            "bg-sisoul-accent": it().stage !== "denied" && it().stage !== "error",
          }}
          style={{ width: `${pct()}%` }}
          data-testid="borrow-progress-bar"
        />
      </div>
      <Show when={it().error}>
        <p class="text-xs text-sisoul-danger font-mono">{it().error}</p>
      </Show>
      <p class="text-[10px] text-sisoul-muted font-mono">
        发起 {relativeTime(it().started_at)} · req={it().request_id.slice(0, 12)}
      </p>
    </div>
  );
}

function ProxySessionRow(props: {
  session: ProxySessionItem;
  onStop: (sid: string) => void;
}) {
  const s = () => props.session;
  return (
    <li
      class="border border-sisoul-border rounded-md p-3 text-xs font-mono space-y-1"
      data-testid="proxy-session-row"
      data-session-id={s().session_id}
    >
      <div class="flex items-center justify-between">
        <span class="text-sisoul-text">
          {s().provider}/{s().model}
        </span>
        <span class={STAGE_COLOR[s().stage]}>{STAGE_LABEL[s().stage]}</span>
      </div>
      <p class="text-sisoul-muted">
        → {s().friend_handle ?? truncateDid(s().friend_did)} · tokens{" "}
        {s().tokens_used}/{s().token_count}
      </p>
      <div class="flex items-center justify-between">
        <span class="text-sisoul-muted">
          到期 {formatDate(s().expires_at)}
        </span>
        <button
          type="button"
          class="px-2 py-0.5 rounded border border-sisoul-danger/50 text-sisoul-danger hover:bg-sisoul-danger/10"
          onClick={() => props.onStop(s().session_id)}
          data-testid="proxy-stop-btn"
        >
          停止
        </button>
      </div>
    </li>
  );
}

function BorrowContent() {
  const [searchParams] = useSearchParams();
  const [friendsRes] = createResource(() => listFriends());
  const [proxyRes, { refetch: refetchProxy }] = createResource(() =>
    borrowProxyList()
  );
  const [inflight, setInflight] = createSignal<BorrowInflight[]>([]);

  // SSE: borrow.update → 更新 stage; ledger.entry → refetch proxy list
  let handle: NotifyStreamHandle | null = null;
  onMount(() => {
    handle = notifyStream((ev: NotifyEvent) => {
      if (ev.type === "borrow.update") {
        const { request_id, stage } = ev.data;
        setInflight((prev) =>
          prev.map((b) => (b.request_id === request_id ? { ...b, stage } : b))
        );
        // 完成 / 错误 → 30s 后移除卡片
        if (stage === "completed" || stage === "denied" || stage === "error") {
          setTimeout(() => {
            setInflight((prev) => prev.filter((b) => b.request_id !== request_id));
            refetchProxy();
          }, 30_000);
        } else {
          refetchProxy();
        }
      } else if (ev.type === "ledger.entry") {
        if (ev.data.direction === "borrow") refetchProxy();
      }
    });
  });
  onCleanup(() => {
    if (handle) handle.close();
  });

  const onSubmitted = (b: BorrowInflight) => {
    setInflight((prev) => [b, ...prev]);
    refetchProxy();
  };

  const onStop = async (sid: string) => {
    try {
      await borrowProxyStop({ session_id: sid });
      refetchProxy();
    } catch (e) {
      console.error("stop failed", e);
    }
  };

  const friends = createMemo(() => friendsRes()?.friends ?? []);
  const proxySessions = createMemo(() => proxyRes()?.sessions ?? []);
  const initialFriend = () => searchParams.friend as string | undefined;

  return (
    <div class="space-y-6">
      <Show
        when={!friendsRes.loading}
        fallback={<p class="text-sm text-sisoul-muted">加载朋友...</p>}
      >
        <BorrowForm
          friends={friends()}
          initialFriend={initialFriend()}
          onSubmitted={onSubmitted}
        />
      </Show>

      <Show when={inflight().length > 0}>
        <section class="space-y-3">
          <h3 class="text-sm font-semibold text-sisoul-text">
            进行中 borrow ({inflight().length})
          </h3>
          <div class="grid gap-3 sm:grid-cols-2" data-testid="borrow-inflight-list">
            <For each={inflight()}>{(b) => <BorrowProgress inflight={b} />}</For>
          </div>
        </section>
      </Show>

      <section class="space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-semibold text-sisoul-text">
            活跃 proxy session ({proxySessions().length})
          </h3>
          <button
            type="button"
            class="text-xs font-mono text-sisoul-muted hover:text-sisoul-text"
            onClick={() => refetchProxy()}
          >
            ↻ 刷新
          </button>
        </div>
        <Show
          when={proxySessions().length > 0}
          fallback={
            <p class="text-xs text-sisoul-muted py-4 text-center">
              无活跃 proxy session
            </p>
          }
        >
          <ul class="space-y-2" data-testid="proxy-sessions-list">
            <For each={proxySessions()}>
              {(s) => <ProxySessionRow session={s} onStop={onStop} />}
            </For>
          </ul>
        </Show>
      </section>

      <section class="space-y-3">
        <h3 class="text-sm font-semibold text-sisoul-text">历史 borrow 账本</h3>
        <LiveLedger direction="borrow" pageSize={30} hideTitle={true} />
      </section>
    </div>
  );
}

export default function Borrow() {
  return (
    <div class="space-y-6 max-w-4xl" data-route="borrow">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Borrow</h1>
        <p class="text-sm text-sisoul-muted mt-1">
          主动借朋友 LLM 配额 · Waku P2P 找 peer + 加密 payload + 等批 + LLM
          stream
        </p>
      </div>

      <AsyncBoundary>
        <BorrowContent />
      </AsyncBoundary>
    </div>
  );
}

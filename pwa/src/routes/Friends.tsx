// Friends 路由 · 朋友关系 + Add modal + last-seen + Borrow/Lend 跳转
//
// daemon endpoints:
//   GET  /sisoul/friend/list
//   POST /sisoul/friend/add
//   GET  /sisoul/perms/list
//
// 实时:
//   SSE friend.online → 刷新对应 friend.online + last_seen_at
//   SSE lend.request  → pending_lend_count++ (对应 borrower friend)

import {
  createResource,
  createSignal,
  createEffect,
  onMount,
  onCleanup,
  For,
  Show,
} from "solid-js";
import { useNavigate } from "@solidjs/router";
import {
  listFriends,
  addFriend,
  notifyStream,
  type Friend,
  type NotifyEvent,
  type NotifyStreamHandle,
  DaemonError,
} from "../api/daemon";
import { formatDate, relativeTime, truncateDid } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";
import LiveLedger from "../components/LiveLedger";

const TRUST_LABELS: Record<number, { label: string; class: string }> = {
  1: { label: "Read", class: "bg-sisoul-border text-sisoul-muted" },
  2: { label: "Query", class: "bg-sisoul-accentDim text-sisoul-accent" },
  3: { label: "Exec", class: "bg-sisoul-success/20 text-sisoul-success" },
};

// DID 校验: did:key:z6Mk... or did:sisoul:...
const DID_PATTERN = /^did:(key|sisoul):[A-Za-z0-9._-]{6,}$/;

function isValidDid(s: string): boolean {
  return DID_PATTERN.test(s.trim());
}

// 在线判定: last_seen_at < 5min
function isOnline(f: Friend): boolean {
  if (typeof f.online === "boolean") return f.online;
  if (!f.last_seen_at) return false;
  return Date.now() - f.last_seen_at < 5 * 60_000;
}

function lastSeenText(f: Friend): string {
  if (isOnline(f)) return "在线";
  if (!f.last_seen_at) return "从未在线";
  return relativeTime(new Date(f.last_seen_at).toISOString());
}

interface FriendCardProps {
  friend: Friend;
  onBorrow: (did: string) => void;
  onLend: (did: string) => void;
}

function FriendCard(props: FriendCardProps) {
  const f = () => props.friend;
  const trust = () =>
    TRUST_LABELS[f().trust_level] ?? {
      label: `L${f().trust_level}`,
      class: "bg-sisoul-border text-sisoul-muted",
    };
  const online = () => isOnline(f());
  const pending = () => f().pending_lend_count ?? 0;

  return (
    <div
      class="border border-sisoul-border rounded-lg p-4 space-y-3 hover:border-sisoul-accentDim transition-colors"
      data-testid="friend-card"
      data-friend-did={f().did}
      data-online={online() ? "true" : "false"}
    >
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span
            class="w-2 h-2 rounded-full shrink-0"
            classList={{
              "bg-sisoul-success": online(),
              "bg-sisoul-muted": !online(),
            }}
            data-testid="friend-online-dot"
          />
          <span class="font-medium text-sisoul-text truncate">
            {f().handle ?? truncateDid(f().did)}
          </span>
        </div>
        <span
          class={`text-xs px-2 py-0.5 rounded font-mono shrink-0 ${trust().class}`}
        >
          {trust().label}
        </span>
      </div>

      <div class="text-xs space-y-1">
        <p class="font-mono text-sisoul-muted">{truncateDid(f().did)}</p>
        <p class="text-sisoul-muted">
          状态:{" "}
          <span
            classList={{
              "text-sisoul-success": online(),
              "text-sisoul-muted": !online(),
            }}
            data-testid="friend-last-seen"
          >
            {lastSeenText(f())}
          </span>
        </p>
        <p class="text-sisoul-muted">
          建立: <span class="font-mono">{formatDate(f().connected_at)}</span>
        </p>
      </div>

      <div class="flex gap-2 pt-1">
        <button
          type="button"
          class="flex-1 px-3 py-1.5 text-xs font-mono rounded border border-sisoul-accentDim text-sisoul-accent hover:bg-sisoul-accentDim transition-colors"
          onClick={() => props.onBorrow(f().did)}
          data-testid="friend-borrow-btn"
        >
          Borrow
        </button>
        <button
          type="button"
          class="flex-1 px-3 py-1.5 text-xs font-mono rounded border border-sisoul-border text-sisoul-text hover:border-sisoul-success hover:text-sisoul-success transition-colors relative"
          onClick={() => props.onLend(f().did)}
          data-testid="friend-lend-btn"
        >
          Lend
          <Show when={pending() > 0}>
            <span
              class="absolute -top-1 -right-1 bg-sisoul-danger text-white text-[10px] font-mono rounded-full px-1.5 py-0.5"
              data-testid="friend-lend-pending"
            >
              {pending()}
            </span>
          </Show>
        </button>
      </div>
    </div>
  );
}

interface AddFriendModalProps {
  open: boolean;
  onClose: () => void;
  onAdded: (f: Friend) => void;
}

function AddFriendModal(props: AddFriendModalProps) {
  const [did, setDid] = createSignal("");
  const [handle, setHandle] = createSignal("");
  const [trust, setTrust] = createSignal(1);
  const [submitting, setSubmitting] = createSignal(false);
  const [err, setErr] = createSignal<string | null>(null);

  const didValid = () => isValidDid(did());

  const submit = async (e: Event) => {
    e.preventDefault();
    setErr(null);
    if (!didValid()) {
      setErr("DID 格式不对 (期望 did:key:... or did:sisoul:...)");
      return;
    }
    setSubmitting(true);
    try {
      const resp = await addFriend({
        did: did().trim(),
        handle: handle().trim() || undefined,
        trust_level: trust(),
      });
      if (!resp.verified) {
        setErr("daemon 返回 verified=false; 该 DID 公钥校验失败");
        setSubmitting(false);
        return;
      }
      const f: Friend = {
        did: resp.did,
        handle: resp.handle,
        trust_level: resp.trust_level,
        connected_at: resp.added_at,
      };
      props.onAdded(f);
      setDid("");
      setHandle("");
      setTrust(1);
      props.onClose();
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
    <Show when={props.open}>
      <div
        class="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
        data-testid="add-friend-modal"
        onClick={(e) => {
          if (e.target === e.currentTarget) props.onClose();
        }}
      >
        <form
          class="bg-sisoul-panel border border-sisoul-border rounded-lg p-6 space-y-4 w-full max-w-md"
          onSubmit={submit}
        >
          <h2 class="text-lg font-semibold text-sisoul-text">添加朋友</h2>

          <label class="block space-y-1">
            <span class="text-xs text-sisoul-muted font-mono">DID *</span>
            <input
              type="text"
              class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text focus:border-sisoul-accent outline-none"
              placeholder="did:key:z6Mk... or did:sisoul:..."
              value={did()}
              onInput={(e) => setDid(e.currentTarget.value)}
              data-testid="add-friend-did-input"
              required
            />
            <Show when={did() !== "" && !didValid()}>
              <span class="text-xs text-sisoul-danger">DID 格式不对</span>
            </Show>
          </label>

          <label class="block space-y-1">
            <span class="text-xs text-sisoul-muted font-mono">Handle (可选)</span>
            <input
              type="text"
              class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text focus:border-sisoul-accent outline-none"
              placeholder="alice"
              value={handle()}
              onInput={(e) => setHandle(e.currentTarget.value)}
              data-testid="add-friend-handle-input"
            />
          </label>

          <label class="block space-y-1">
            <span class="text-xs text-sisoul-muted font-mono">信任等级</span>
            <select
              class="w-full px-3 py-2 bg-sisoul-bg border border-sisoul-border rounded font-mono text-sm text-sisoul-text focus:border-sisoul-accent outline-none"
              value={trust()}
              onChange={(e) => setTrust(Number(e.currentTarget.value))}
              data-testid="add-friend-trust-input"
            >
              <option value="1">L1 Read</option>
              <option value="2">L2 Query</option>
              <option value="3">L3 Exec</option>
            </select>
          </label>

          <Show when={err()}>
            <p
              class="text-xs text-sisoul-danger font-mono"
              data-testid="add-friend-error"
            >
              {err()}
            </p>
          </Show>

          <div class="flex gap-2 pt-2">
            <button
              type="button"
              class="flex-1 px-3 py-2 text-sm font-mono rounded border border-sisoul-border text-sisoul-muted hover:text-sisoul-text"
              onClick={props.onClose}
              disabled={submitting()}
              data-testid="add-friend-cancel"
            >
              取消
            </button>
            <button
              type="submit"
              class="flex-1 px-3 py-2 text-sm font-mono rounded bg-sisoul-accent text-sisoul-bg hover:bg-sisoul-accent/80 disabled:opacity-50"
              disabled={submitting() || !didValid()}
              data-testid="add-friend-submit"
            >
              {submitting() ? "添加中..." : "添加"}
            </button>
          </div>
        </form>
      </div>
    </Show>
  );
}

function FriendsContent() {
  const navigate = useNavigate();
  const [data, { refetch }] = createResource(() => listFriends());
  // 本地 friends 列表 (SSE 增量 patch)
  const [localFriends, setLocalFriends] = createSignal<Friend[]>([]);
  const [modalOpen, setModalOpen] = createSignal(false);

  createEffect(() => {
    const d = data();
    if (d) setLocalFriends(d.friends);
  });

  // SSE: friend.online + lend.request → 更新 friend 状态
  let handle: NotifyStreamHandle | null = null;
  onMount(() => {
    handle = notifyStream((ev: NotifyEvent) => {
      if (ev.type === "friend.online") {
        const { did, online, last_seen_at } = ev.data;
        setLocalFriends((prev) =>
          prev.map((f) =>
            f.did === did ? { ...f, online, last_seen_at } : f
          )
        );
      } else if (ev.type === "lend.request") {
        const borrowerDid = ev.data.borrower_did;
        setLocalFriends((prev) =>
          prev.map((f) =>
            f.did === borrowerDid
              ? { ...f, pending_lend_count: (f.pending_lend_count ?? 0) + 1 }
              : f
          )
        );
      }
    });
  });

  onCleanup(() => {
    if (handle) handle.close();
  });

  const onAdded = (f: Friend) => {
    setLocalFriends((prev) => [f, ...prev]);
    refetch();
  };

  const goBorrow = (did: string) =>
    navigate(`/borrow?friend=${encodeURIComponent(did)}`);
  const goLend = (did: string) =>
    navigate(`/lend?friend=${encodeURIComponent(did)}`);

  return (
    <div class="space-y-4">
      <div class="flex items-center justify-between">
        <p class="text-xs text-sisoul-muted">
          共 <span class="font-mono text-sisoul-text">{localFriends().length}</span>{" "}
          位朋友
        </p>
        <button
          type="button"
          class="px-3 py-1.5 text-xs font-mono rounded bg-sisoul-accent text-sisoul-bg hover:bg-sisoul-accent/80"
          onClick={() => setModalOpen(true)}
          data-testid="open-add-friend-modal"
        >
          + Add Friend
        </button>
      </div>

      <Show
        when={localFriends().length > 0}
        fallback={
          <div class="text-sisoul-muted text-sm py-8 text-center">
            暂无朋友 · 用上方 Add Friend 添加, 或{" "}
            <code class="font-mono text-sisoul-accent">
              sisoul friend add &lt;did&gt;
            </code>
          </div>
        }
      >
        <div class="grid gap-3 sm:grid-cols-2" data-testid="friends-grid">
          <For each={localFriends()}>
            {(f) => (
              <FriendCard friend={f} onBorrow={goBorrow} onLend={goLend} />
            )}
          </For>
        </div>
      </Show>

      <AddFriendModal
        open={modalOpen()}
        onClose={() => setModalOpen(false)}
        onAdded={onAdded}
      />
    </div>
  );
}

export default function Friends() {
  return (
    <div class="space-y-8 max-w-4xl" data-route="friends">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Friends</h1>
        <p class="text-sm text-sisoul-muted mt-1">
          P2P 朋友网络 · 3 档信任 (Read / Query / Exec) · 实时在线状态
        </p>
      </div>

      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          朋友列表
        </h2>
        <AsyncBoundary>
          <FriendsContent />
        </AsyncBoundary>
      </section>

      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          全账本 · 实时
        </h2>
        <LiveLedger pageSize={20} hideTitle={false} />
      </section>

      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          信任等级说明
        </h2>
        <div class="space-y-2 text-sm">
          {[
            { level: "L1 Read", desc: "只读 vault + preferences · 不可执行命令" },
            { level: "L2 Query", desc: "可查询 goals / chat history + 调 LLM" },
            { level: "L3 Exec", desc: "可借用技能 + 触发 session · 最高信任" },
          ].map((item) => (
            <div class="flex gap-3 py-2 border-b border-sisoul-border/50 last:border-0">
              <span class="font-mono text-sisoul-accent text-xs w-20 shrink-0 pt-0.5">
                {item.level}
              </span>
              <span class="text-sisoul-muted">{item.desc}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

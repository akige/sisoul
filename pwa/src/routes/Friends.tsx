// Friends 路由 · 朋友关系 + 共享配额管理
// daemon endpoints: GET /sisoul/friend/list, GET /sisoul/perms/list
import { createResource, For, Show } from "solid-js";
import { listFriends, type Friend } from "../api/daemon";
import { formatDate, truncateDid } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";

const TRUST_LABELS: Record<number, { label: string; class: string }> = {
  1: { label: "Read", class: "bg-sisoul-border text-sisoul-muted" },
  2: { label: "Query", class: "bg-sisoul-accentDim text-sisoul-accent" },
  3: { label: "Exec", class: "bg-sisoul-success/20 text-sisoul-success" },
};

function FriendCard(props: { friend: Friend }) {
  const f = () => props.friend;
  const trust = () => TRUST_LABELS[f().trust_level] ?? { label: `L${f().trust_level}`, class: "bg-sisoul-border text-sisoul-muted" };

  return (
    <div
      class="border border-sisoul-border rounded-lg p-4 space-y-2 hover:border-sisoul-accentDim transition-colors"
      data-testid="friend-card"
    >
      <div class="flex items-center justify-between gap-2">
        <span class="font-medium text-sisoul-text truncate">
          {f().handle ?? truncateDid(f().did)}
        </span>
        <span
          class={`text-xs px-2 py-0.5 rounded font-mono shrink-0 ${trust().class}`}
        >
          {trust().label}
        </span>
      </div>
      <p class="text-xs font-mono text-sisoul-muted">{truncateDid(f().did)}</p>
      <p class="text-xs text-sisoul-muted">
        连接时间: <span class="font-mono">{formatDate(f().connected_at)}</span>
      </p>
    </div>
  );
}

function FriendsContent() {
  const [data] = createResource(() => listFriends());

  return (
    <Show
      when={data()}
      fallback={<div class="text-sisoul-muted text-sm">加载朋友列表...</div>}
    >
      {(d) => (
        <Show
          when={d().friends.length > 0}
          fallback={
            <div class="text-sisoul-muted text-sm py-8 text-center">
              暂无朋友 · 用 <code class="font-mono text-sisoul-accent">sisoul friend add &lt;did&gt;</code> 添加
            </div>
          }
        >
          <div class="grid gap-3 sm:grid-cols-2">
            <For each={d().friends}>{(f) => <FriendCard friend={f} />}</For>
          </div>
        </Show>
      )}
    </Show>
  );
}

export default function Friends() {
  return (
    <div class="space-y-8 max-w-3xl" data-route="friends">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Friends</h1>
        <p class="text-sm text-sisoul-muted mt-1">
          P2P 朋友网络 · 3 档信任等级 (Read / Query / Exec)
        </p>
      </div>

      {/* Friends list */}
      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          朋友列表
        </h2>
        <AsyncBoundary>
          <FriendsContent />
        </AsyncBoundary>
      </section>

      {/* Trust level legend */}
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

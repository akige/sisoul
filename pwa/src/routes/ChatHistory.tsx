// ChatHistory 路由 · chat 时间线 + session 详情
// daemon endpoint: GET /sisoul/chat-history/list
import { createResource, createSignal, For, Show } from "solid-js";
import { listChatHistory, type ChatSession } from "../api/daemon";
import { formatDate, relativeTime } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";

function SessionRow(props: {
  session: ChatSession;
  selected: boolean;
  onSelect: () => void;
}) {
  const s = () => props.session;

  return (
    <button
      class="w-full text-left px-4 py-3 border-b border-sisoul-border hover:bg-sisoul-panel/50 transition-colors"
      classList={{ "bg-sisoul-panel": props.selected }}
      onClick={props.onSelect}
      data-testid="session-row"
    >
      <div class="flex items-center justify-between gap-2">
        <span class="font-medium text-sm text-sisoul-text truncate">{s().title}</span>
        <span class="text-xs text-sisoul-muted shrink-0 font-mono">
          {s().message_count} msgs
        </span>
      </div>
      <p class="text-xs text-sisoul-muted mt-0.5">{relativeTime(s().started_at)}</p>
    </button>
  );
}

function ChatHistoryContent() {
  const [data] = createResource(() => listChatHistory());
  const [selected, setSelected] = createSignal<string | null>(null);

  const selectedSession = () =>
    data()?.sessions.find((s) => s.id === selected());

  return (
    <Show
      when={data()}
      fallback={<div class="text-sisoul-muted text-sm">加载中...</div>}
    >
      {(d) => (
        <Show
          when={d().sessions.length > 0}
          fallback={
            <div class="text-sisoul-muted text-sm py-8 text-center">
              暂无会话记录
            </div>
          }
        >
          <div class="flex gap-4 h-[60vh] border border-sisoul-border rounded-lg overflow-hidden">
            {/* Session list */}
            <div class="w-64 shrink-0 overflow-y-auto scrollbar-thin border-r border-sisoul-border">
              <For each={d().sessions}>
                {(session) => (
                  <SessionRow
                    session={session}
                    selected={selected() === session.id}
                    onSelect={() => setSelected(session.id)}
                  />
                )}
              </For>
            </div>

            {/* Session detail */}
            <div class="flex-1 p-4 overflow-y-auto scrollbar-thin">
              <Show
                when={selectedSession()}
                fallback={
                  <p class="text-sisoul-muted text-sm">← 选择一个会话</p>
                }
              >
                {(s) => (
                  <div class="space-y-3">
                    <h3 class="font-semibold text-sisoul-text">{s().title}</h3>
                    <dl class="space-y-1 text-sm">
                      <div class="flex gap-2">
                        <dt class="text-sisoul-muted w-20 shrink-0">开始</dt>
                        <dd class="font-mono text-sisoul-text">
                          {formatDate(s().started_at)}
                        </dd>
                      </div>
                      <div class="flex gap-2">
                        <dt class="text-sisoul-muted w-20 shrink-0">消息数</dt>
                        <dd class="font-mono text-sisoul-text">{s().message_count}</dd>
                      </div>
                      <div class="flex gap-2">
                        <dt class="text-sisoul-muted w-20 shrink-0">Session ID</dt>
                        <dd class="font-mono text-sisoul-text text-xs break-all">
                          {s().id}
                        </dd>
                      </div>
                    </dl>
                    <div class="mt-4 p-3 bg-sisoul-panel/50 rounded text-xs text-sisoul-muted font-mono">
                      [chat 内容渲染 Phase 3 补充 — markdown + code highlight]
                    </div>
                  </div>
                )}
              </Show>
            </div>
          </div>
        </Show>
      )}
    </Show>
  );
}

export default function ChatHistory() {
  return (
    <div class="space-y-6" data-route="chat-history">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Chat History</h1>
        <p class="text-sm text-sisoul-muted mt-1">AI 会话时间线 · 全量 session 记录</p>
      </div>
      <AsyncBoundary>
        <ChatHistoryContent />
      </AsyncBoundary>
    </div>
  );
}

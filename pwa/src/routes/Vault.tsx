// Vault 路由 · 浏览 ~/.sisoul/preferences/*.md
// daemon endpoint: GET /sisoul/preferences/list
import { createResource, For, Show } from "solid-js";
import { listPreferences, type Preference } from "../api/daemon";
import { formatDate } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";

function PreferenceItem(props: { pref: Preference }) {
  return (
    <div class="border border-sisoul-border rounded-lg p-4 space-y-2 hover:border-sisoul-accentDim transition-colors">
      <div class="flex items-center justify-between gap-2">
        <span class="font-mono text-sm text-sisoul-accent font-semibold truncate">
          {props.pref.key}
        </span>
        <span class="text-xs text-sisoul-muted shrink-0">
          {formatDate(props.pref.updated_at)}
        </span>
      </div>
      <p class="text-sm text-sisoul-text leading-relaxed whitespace-pre-wrap break-words">
        {props.pref.value}
      </p>
    </div>
  );
}

function VaultContent() {
  const [prefs] = createResource(() => listPreferences());

  return (
    <Show
      when={prefs()}
      fallback={<div class="text-sisoul-muted text-sm">暂无 preferences 数据</div>}
    >
      {(data) => (
        <Show
          when={data().items.length > 0}
          fallback={
            <div class="text-sisoul-muted text-sm py-8 text-center">
              vault 为空 — 运行 <code class="font-mono text-sisoul-accent">sisoul remember</code> 添加
            </div>
          }
        >
          <div class="space-y-3">
            <For each={data().items}>
              {(pref) => <PreferenceItem pref={pref} />}
            </For>
          </div>
        </Show>
      )}
    </Show>
  );
}

export default function Vault() {
  return (
    <div class="space-y-6 max-w-3xl" data-route="vault">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Vault</h1>
        <p class="text-sm text-sisoul-muted mt-1">
          你的 AI 工作偏好 · <code class="font-mono">~/.sisoul/preferences/</code>
        </p>
      </div>
      <AsyncBoundary>
        <VaultContent />
      </AsyncBoundary>
    </div>
  );
}

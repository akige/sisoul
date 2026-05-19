// Settings 路由 · vault / daemon / DID / LLM provider 配置
// daemon endpoints: GET /sisoul/identity
import { createResource, Show } from "solid-js";
import { getIdentity } from "../api/daemon";
import { truncateDid } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";

function SettingsRow(props: { label: string; value: string; mono?: boolean }) {
  return (
    <div class="flex items-start gap-4 py-3 border-b border-sisoul-border last:border-0">
      <dt class="w-32 shrink-0 text-sm text-sisoul-muted">{props.label}</dt>
      <dd
        class={`flex-1 text-sm text-sisoul-text break-all ${props.mono ? "font-mono" : ""}`}
      >
        {props.value}
      </dd>
    </div>
  );
}

function IdentitySection() {
  const [identity] = createResource(() => getIdentity());

  return (
    <Show
      when={identity()}
      fallback={<div class="text-sisoul-muted text-sm">加载 identity...</div>}
    >
      {(id) => (
        <dl>
          <SettingsRow label="DID" value={truncateDid(id().did)} mono />
          <Show when={id().handle}>
            <SettingsRow label="Handle" value={id().handle!} />
          </Show>
          <Show when={id().provider}>
            <SettingsRow label="LLM Provider" value={id().provider!} />
          </Show>
          <SettingsRow
            label="助记词"
            value={id().mnemonic_hint ?? "已加密 (run `sisoul identity show` 查看)"}
            mono
          />
        </dl>
      )}
    </Show>
  );
}

export default function Settings() {
  return (
    <div class="space-y-8 max-w-2xl" data-route="settings">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Settings</h1>
        <p class="text-sm text-sisoul-muted mt-1">vault · daemon · DID · LLM provider 配置</p>
      </div>

      {/* Identity section */}
      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          Identity (DID)
        </h2>
        <AsyncBoundary>
          <IdentitySection />
        </AsyncBoundary>
      </section>

      {/* Daemon section */}
      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          Daemon
        </h2>
        <dl>
          <SettingsRow label="Host" value="127.0.0.1" mono />
          <SettingsRow label="Port" value="9876" mono />
          <SettingsRow label="Protocol" value="HTTP (local only)" />
          <SettingsRow label="Status" value="running" />
        </dl>
      </section>

      {/* Vault section */}
      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          Vault 路径
        </h2>
        <dl>
          <SettingsRow label="Root" value="~/.sisoul/" mono />
          <SettingsRow label="Preferences" value="~/.sisoul/preferences/" mono />
          <SettingsRow label="Goals" value="~/.sisoul/goals.json" mono />
          <SettingsRow label="Chat History" value="~/.sisoul/chat/" mono />
        </dl>
      </section>
    </div>
  );
}

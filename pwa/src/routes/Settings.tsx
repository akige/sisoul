// Settings 路由 · vault / daemon / DID / LLM provider 配置
// daemon endpoints: GET /sisoul/identity, /v1/push/*
import { createResource, Show, createSignal, For } from "solid-js";
import { getIdentity } from "../api/daemon";
import { truncateDid } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";
import { isNativeApp, getPlatform, registerNativePush } from "../lib/capacitor";
import {
  listPushDevices,
  unregisterPushDevice,
  sendTestPush,
} from "../api/push";
import type { PushDevice } from "../api/push";

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
          <SettingsRow label="Push Devices" value="~/.sisoul/push_devices.json" mono />
        </dl>
      </section>

      {/* Mobile push notifications */}
      <section class="space-y-3">
        <h2 class="text-base font-semibold text-sisoul-text border-b border-sisoul-border pb-2">
          推送通知 (Mobile)
        </h2>
        <PushSection />
      </section>
    </div>
  );
}

function PushSection() {
  const [devices, { refetch }] = createResource(() => listPushDevices());
  const [status, setStatus] = createSignal<string>("");
  const [busy, setBusy] = createSignal(false);

  const nativeMode = isNativeApp();
  const platform = getPlatform();

  const handleRegister = async () => {
    setBusy(true);
    setStatus("正在请求推送权限…");
    try {
      const result = await registerNativePush("did:key:self");
      if (!result) {
        setStatus(
          nativeMode
            ? "用户拒绝推送权限"
            : "当前在浏览器, 不能注册 native push (装 iOS/Android app 后可用)",
        );
        return;
      }
      setStatus(`✓ 已注册 ${platform} 设备 token: ${result.token.slice(0, 16)}…`);
      refetch();
    } catch (e: any) {
      setStatus(`错: ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    setBusy(true);
    try {
      const r = await sendTestPush("sisoul test", "Hello from your sisoul!");
      setStatus(
        `测试推送目标 ${r.devices_targeted.length} 个设备 (实际发出: ${r.sent}, ${r.note})`,
      );
    } catch (e: any) {
      setStatus(`错: ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const handleUnregister = async (token: string) => {
    setBusy(true);
    try {
      await unregisterPushDevice(token);
      setStatus(`✓ 已注销 ${token.slice(0, 16)}…`);
      refetch();
    } catch (e: any) {
      setStatus(`错: ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="space-y-3">
      <p class="text-xs text-sisoul-muted">
        当前环境: <code class="font-mono">{nativeMode ? `native (${platform})` : "browser"}</code>
        {!nativeMode && <span> · 在 sisoul iOS / Android app 内打开本页才能注册推送</span>}
      </p>

      <div class="flex gap-2 flex-wrap">
        <button
          onClick={handleRegister}
          disabled={busy() || !nativeMode}
          class="px-3 py-1.5 text-sm rounded bg-sisoul-accent text-sisoul-bg disabled:opacity-50"
        >
          注册推送 (本设备)
        </button>
        <button
          onClick={handleTest}
          disabled={busy()}
          class="px-3 py-1.5 text-sm rounded border border-sisoul-border text-sisoul-text"
        >
          发测试推送
        </button>
      </div>

      <Show when={status()}>
        <p class="text-xs text-sisoul-muted font-mono">{status()}</p>
      </Show>

      <h3 class="text-sm font-semibold text-sisoul-text mt-4">已注册设备</h3>
      <Show
        when={devices()?.devices?.length}
        fallback={<p class="text-xs text-sisoul-muted">无已注册设备</p>}
      >
        <ul class="space-y-1">
          <For each={devices()!.devices}>
            {(d: PushDevice) => (
              <li class="flex items-center justify-between text-xs font-mono border-b border-sisoul-border pb-1">
                <span>
                  <code>{d.platform}</code> · <code>{d.token.slice(0, 24)}…</code>
                  {d.did_key && <span class="text-sisoul-muted"> · {truncateDid(d.did_key)}</span>}
                </span>
                <button
                  onClick={() => handleUnregister(d.token)}
                  disabled={busy()}
                  class="text-red-400 hover:text-red-300 text-xs"
                >
                  remove
                </button>
              </li>
            )}
          </For>
        </ul>
      </Show>
    </div>
  );
}

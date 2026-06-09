import { createSignal, For } from "solid-js";

// daemon offline 时显示的真内容首屏 — 不再空白
// online 后由 App.tsx 自动切到主 dashboard
// 风格沿用现有 sisoul-* Tailwind class

interface InstallStep {
  label: string;
  cmd: string;
}

const STEPS: InstallStep[] = [
  { label: "1. 安装依赖 (Python 3.12 + IPFS)", cmd: "brew install python@3.12 ipfs" },
  { label: "2. 克隆仓库", cmd: "git clone https://github.com/akige/sisoul && cd sisoul" },
  { label: "3. 建虚拟环境", cmd: "python3.12 -m venv .venv && source .venv/bin/activate" },
  { label: "4. 装包 (含 daemon/crypto/chat/llm extras)", cmd: "pip install -e '.[daemon,crypto,chat,llm]'" },
  { label: "5. 启动 daemon (后台跑)", cmd: "sisoul daemon &" },
];

interface Scenario {
  title: string;
  desc: string;
  emoji: string;
}

const SCENARIOS: Scenario[] = [
  {
    emoji: "🤝",
    title: "借朋友的 LLM",
    desc: "没 OpenAI key? 通过 sisoul P2P 借朋友的 GPT/Claude 额度,不经任何云中转,端到端加密。",
  },
  {
    emoji: "👥",
    title: "加好友 / Friend Discovery",
    desc: "扫 QR 或交换 did:key,基于 libp2p Kademlia DHT 发现,无需服务器。LAN 内自动 mDNS。",
  },
  {
    emoji: "🧠",
    title: "Founder Chat",
    desc: "找不到答案? 召唤 founder-agent 拿 case-graph 检索 + 真人接力。MCP 协议接入。",
  },
];

export default function OnboardingScreen() {
  const [copied, setCopied] = createSignal<number | null>(null);

  const copy = async (idx: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(idx);
      setTimeout(() => setCopied((c) => (c === idx ? null : c)), 1500);
    } catch (e) {
      // fallback: 选中
      console.warn("clipboard 不可用", e);
    }
  };

  return (
    <div class="min-h-full bg-sisoul-bg text-sisoul-text font-sans">
      <div class="max-w-5xl mx-auto px-6 py-12 md:py-16">
        {/* 大标题 */}
        <header class="mb-12 text-center">
          <h1 class="text-3xl md:text-5xl font-bold tracking-tight mb-4">
            <span class="text-sisoul-accent">sisoul</span>
            <span class="text-sisoul-muted"> · </span>
            <span class="text-sisoul-text">Your AI agent. Your data. No cloud.</span>
          </h1>
          <p class="text-sisoul-muted text-base md:text-lg max-w-2xl mx-auto">
            端到端加密 · 本地 vault · P2P 朋友共享 · 无服务器。装完 daemon 这页会自动消失。
          </p>
          <div class="mt-4 inline-flex items-center gap-2 px-3 py-1 rounded-full bg-red-500/10 border border-red-500/30 text-red-400 text-xs font-mono">
            <span class="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            daemon offline — 等待 127.0.0.1:9876 上线
          </div>
        </header>

        {/* 装机 5 步 */}
        <section class="mb-12">
          <h2 class="text-xl md:text-2xl font-bold mb-4 text-sisoul-text">
            5 步装机 (Mac / Linux)
          </h2>
          <div class="space-y-3">
            <For each={STEPS}>
              {(step, idx) => (
                <div class="bg-sisoul-panel border border-sisoul-border rounded-lg p-4">
                  <div class="text-sm text-sisoul-muted mb-2">{step.label}</div>
                  <div class="flex items-center gap-2">
                    <code class="flex-1 font-mono text-sm text-sisoul-text bg-sisoul-bg px-3 py-2 rounded border border-sisoul-border overflow-x-auto whitespace-nowrap">
                      {step.cmd}
                    </code>
                    <button
                      type="button"
                      onClick={() => copy(idx(), step.cmd)}
                      class="shrink-0 px-3 py-2 text-xs font-mono rounded border border-sisoul-border bg-sisoul-bg hover:bg-sisoul-border/30 text-sisoul-accent transition-colors"
                      aria-label={`复制第 ${idx() + 1} 步命令`}
                    >
                      {copied() === idx() ? "✓ 已复制" : "[复制]"}
                    </button>
                  </div>
                </div>
              )}
            </For>
          </div>
          <p class="text-sisoul-muted text-xs mt-4 font-mono">
            装完 daemon 后, 这页会在 ~15 秒内自动跳到主 dashboard (Vault / Goals / Borrow / Lend ...)
          </p>
        </section>

        {/* 核心场景 */}
        <section class="mb-12">
          <h2 class="text-xl md:text-2xl font-bold mb-4 text-sisoul-text">
            装好能干啥
          </h2>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <For each={SCENARIOS}>
              {(s) => (
                <div class="bg-sisoul-panel border border-sisoul-border rounded-lg p-5">
                  <div class="text-3xl mb-3">{s.emoji}</div>
                  <h3 class="font-bold text-sisoul-text mb-2">{s.title}</h3>
                  <p class="text-sm text-sisoul-muted leading-relaxed">{s.desc}</p>
                </div>
              )}
            </For>
          </div>
        </section>

        {/* 底部链接 */}
        <footer class="border-t border-sisoul-border pt-6 flex flex-wrap items-center justify-center gap-4 text-sm font-mono">
          <a
            href="https://github.com/akige/sisoul"
            target="_blank"
            rel="noopener noreferrer"
            class="text-sisoul-accent hover:underline"
          >
            GitHub →
          </a>
          <span class="text-sisoul-border">·</span>
          <a
            href="https://github.com/akige/sisoul/blob/main/INSTALL.md"
            target="_blank"
            rel="noopener noreferrer"
            class="text-sisoul-accent hover:underline"
          >
            INSTALL.md
          </a>
          <span class="text-sisoul-border">·</span>
          <a
            href="https://www.v2ex.com/t/sisoul"
            target="_blank"
            rel="noopener noreferrer"
            class="text-sisoul-accent hover:underline"
          >
            V2EX 讨论帖
          </a>
        </footer>
      </div>
    </div>
  );
}

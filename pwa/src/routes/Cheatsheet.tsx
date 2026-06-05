// PWA /cheatsheet · quick reference for all CLI commands, same as `sisoul cheatsheet`.
import { For } from "solid-js";

interface Group {
  title: string;
  items: { cmd: string; desc: string }[];
}

const GROUPS: Group[] = [
  {
    title: "First Steps",
    items: [
      { cmd: "sisoul init", desc: "5-step wizard (Petname/did/provider/daemon/QR)" },
      { cmd: "sisoul daemon", desc: "start HTTP daemon (foreground)" },
      { cmd: "sisoul health", desc: "verify daemon + v2 endpoints" },
      { cmd: "sisoul stats", desc: "local case/skill/friend counters" },
    ],
  },
  {
    title: "Friends",
    items: [
      { cmd: "sisoul friend list", desc: "show your friends" },
      { cmd: "sisoul friend add <did:key>", desc: "add friend by DID" },
      { cmd: "sisoul friend qr --out friend.png", desc: "generate QR for friends to scan" },
      { cmd: "sisoul friend qr-scan <image>", desc: "decode friend's QR" },
      { cmd: "sisoul friend mdns scan", desc: "find friends on LAN (5s scan)" },
      { cmd: "sisoul friend petname set <did> <name>", desc: "set local nickname" },
      { cmd: "sisoul invite --did <yours> --petname <yours>", desc: "text invite for IM/Slack" },
    ],
  },
  {
    title: "Cases (knowledge sharing)",
    items: [
      { cmd: "sisoul case list", desc: "all cases in vault" },
      { cmd: "sisoul case search \"<query>\"", desc: "search (TF-IDF foundation)" },
      { cmd: "sisoul case show <case-id>", desc: "full case detail" },
      { cmd: "sisoul case add -q <q> -a <a> -d <did>", desc: "add case manually" },
    ],
  },
  {
    title: "Ask / Debate",
    items: [
      { cmd: "sisoul ask \"<question>\"", desc: "single-LLM ask" },
      { cmd: "sisoul debate \"<difficult q>\"", desc: "multi-agent debate (foundation: mock)" },
    ],
  },
  {
    title: "Skills",
    items: [
      { cmd: "sisoul skill list", desc: "installed skills" },
      { cmd: "sisoul skill install <ipfs-cid>", desc: "install from IPFS" },
    ],
  },
  {
    title: "Chat (Signal-grade)",
    items: [
      { cmd: "sisoul chat send <peer-did> \"<msg>\"", desc: "E2E encrypted (Double Ratchet + PQXDH)" },
      { cmd: "sisoul chat recv", desc: "pull messages" },
      { cmd: "sisoul chat sessions list", desc: "active sessions" },
    ],
  },
  {
    title: "Demo / Debug",
    items: [
      { cmd: "sisoul demo", desc: "8-step end-to-end showcase" },
      { cmd: "sisoul --version", desc: "ASCII version + module status" },
      { cmd: "sisoul cheatsheet", desc: "this cheatsheet in terminal" },
      { cmd: "sisoul completion bash --install", desc: "shell autocomplete" },
    ],
  },
];

export default function Cheatsheet() {
  return (
    <div class="space-y-4 p-2">
      <header>
        <h1 class="text-2xl font-bold text-sisoul-text">CLI Cheatsheet</h1>
        <p class="text-sm text-sisoul-muted">
          18 commands grouped. Same content as `sisoul cheatsheet`. Run `sisoul &lt;cmd&gt; --help` for full options.
        </p>
      </header>

      <div class="space-y-4">
        <For each={GROUPS}>
          {(g) => (
            <section>
              <h2 class="text-lg font-semibold mb-2">{g.title}</h2>
              <div class="border border-sisoul-border rounded-lg overflow-hidden">
                <For each={g.items}>
                  {(item, i) => (
                    <div
                      class="grid grid-cols-2 gap-3 p-2 text-sm hover:bg-sisoul-bg/50 border-b border-sisoul-border last:border-b-0"
                      classList={{ "bg-sisoul-bg/30": i() % 2 === 1 }}
                    >
                      <code class="font-mono text-sisoul-accent">{item.cmd}</code>
                      <span class="text-sisoul-muted">{item.desc}</span>
                    </div>
                  )}
                </For>
              </div>
            </section>
          )}
        </For>
      </div>

      <section class="text-xs text-sisoul-muted border-t border-sisoul-border pt-3 mt-4">
        <p>Quick install: <code>curl -sSfL https://github.com/sisoul/sisoul/releases/latest/download/install.sh | bash</code></p>
        <p>Full docs: <a href="https://github.com/sisoul/sisoul" class="text-sisoul-accent hover:underline">github.com/sisoul/sisoul</a></p>
      </section>
    </div>
  );
}

import { createSignal, Show, onMount, onCleanup } from "solid-js";
import { A } from "@solidjs/router";

interface MobileNavItem {
  path: string;
  label: string;
}

const MOBILE_NAV: MobileNavItem[] = [
  { path: "/", label: "Vault" },
  { path: "/goals", label: "Goals" },
  { path: "/friends", label: "Friends" },
  { path: "/skills", label: "Skills" },
  { path: "/dashboard/v2", label: "v2 Dashboard" },
  { path: "/ask", label: "Ask" },
  { path: "/debate", label: "Debate" },
  { path: "/skills/v2", label: "v2 Skills" },
  { path: "/stats", label: "Stats" },
  { path: "/cheatsheet", label: "Cheatsheet" },
  { path: "/settings", label: "Settings" },
];

const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE ||
  (typeof window !== "undefined" && window.location.pathname.startsWith("/app")
    ? window.location.origin // daemon 托管 (任意端口) → 同源
    : "http://127.0.0.1:9876");

interface DaemonHealth {
  online: boolean;
  version?: string;
}

export default function TopBar() {
  const [menuOpen, setMenuOpen] = createSignal(false);
  const [health, setHealth] = createSignal<DaemonHealth>({ online: false });

  const checkHealth = async () => {
    try {
      const r = await fetch(`${DAEMON_BASE}/sisoul/health`, {
        signal: AbortSignal.timeout(2000),
      });
      if (r.ok) {
        const data = await r.json();
        setHealth({ online: true, version: data.version });
      } else {
        setHealth({ online: false });
      }
    } catch {
      setHealth({ online: false });
    }
  };

  let timer: ReturnType<typeof setInterval> | undefined;

  onMount(() => {
    checkHealth();
    timer = setInterval(checkHealth, 15_000); // 15s poll
  });

  onCleanup(() => {
    if (timer) clearInterval(timer);
  });

  return (
    <header class="flex items-center h-14 px-4 border-b border-sisoul-border bg-sisoul-panel shrink-0 relative z-10">
      {/* Mobile hamburger */}
      <button
        class="md:hidden text-sisoul-muted hover:text-sisoul-text mr-3"
        aria-label="menu"
        onClick={() => setMenuOpen((o) => !o)}
      >
        <span class="text-xl">{menuOpen() ? "✕" : "☰"}</span>
      </button>

      <span class="font-mono text-sisoul-accent font-bold md:hidden">sisoul</span>

      <div class="ml-auto flex items-center gap-3 text-sm text-sisoul-muted font-mono">
        <span
          class="w-2 h-2 rounded-full inline-block transition-colors"
          classList={{
            "bg-sisoul-success": health().online,
            "bg-red-500": !health().online,
          }}
          title={health().online ? `Daemon ok · ${health().version}` : "Daemon offline"}
        />
        <span>
          {health().online ? `daemon ${health().version || "ok"}` : "daemon offline"}
        </span>
      </div>

      {/* Mobile dropdown menu */}
      <Show when={menuOpen()}>
        <div class="absolute top-14 left-0 right-0 bg-sisoul-panel border-b border-sisoul-border md:hidden">
          <ul class="py-2" aria-label="mobile-nav">
            {MOBILE_NAV.map((item) => (
              <li>
                <A
                  href={item.path}
                  class="block px-6 py-3 text-sm font-mono text-sisoul-text hover:bg-sisoul-border/30"
                  onClick={() => setMenuOpen(false)}
                  end={item.path === "/"}
                >
                  {item.label}
                </A>
              </li>
            ))}
          </ul>
        </div>
      </Show>
    </header>
  );
}

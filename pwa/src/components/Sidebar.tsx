import { A } from "@solidjs/router";
import { createSignal } from "solid-js";

interface NavItem {
  path: string;
  label: string;
  icon: string;
}

const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "Vault", icon: "◈" },
  { path: "/goals", label: "Goals", icon: "◎" },
  { path: "/chat-history", label: "History", icon: "◷" },
  { path: "/friends", label: "Friends", icon: "◍" },
  { path: "/skills", label: "Skills", icon: "◈" },
  { path: "/borrow", label: "Borrow", icon: "↙" },
  { path: "/lend", label: "Lend", icon: "↗" },
  // v2 智能体网络 routes
  { path: "/dashboard/v2", label: "v2 Dashboard", icon: "▸" },
  { path: "/ask", label: "Ask", icon: "?" },
  { path: "/debate", label: "Debate", icon: "⇄" },
  { path: "/skills/v2", label: "v2 Skills", icon: "▸" },
  { path: "/stats", label: "Stats", icon: "▤" },
  { path: "/cheatsheet", label: "Cheatsheet", icon: "✎" },
  { path: "/settings", label: "Settings", icon: "◉" },
  { path: "/advanced", label: "Advanced", icon: "◆" },
];

export default function Sidebar() {
  const [collapsed, setCollapsed] = createSignal(false);

  return (
    <nav
      class="hidden md:flex flex-col bg-sisoul-panel border-r border-sisoul-border transition-all duration-200"
      classList={{ "w-16": collapsed(), "w-56": !collapsed() }}
      aria-label="sidebar"
    >
      {/* Logo */}
      <div class="flex items-center h-14 px-4 border-b border-sisoul-border">
        <span class="text-sisoul-accent font-mono font-bold text-lg">
          {collapsed() ? "si" : "sisoul"}
        </span>
        <button
          class="ml-auto text-sisoul-muted hover:text-sisoul-text"
          onClick={() => setCollapsed((c) => !c)}
          aria-label="toggle sidebar"
        >
          {collapsed() ? "›" : "‹"}
        </button>
      </div>

      {/* Nav links */}
      <ul class="flex-1 py-3 space-y-1 px-2">
        {NAV_ITEMS.map((item) => (
          <li>
            <A
              href={item.path}
              activeClass="bg-sisoul-accentDim text-sisoul-accent"
              inactiveClass="text-sisoul-muted hover:text-sisoul-text hover:bg-sisoul-border/30"
              class="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-mono transition-colors"
              end={item.path === "/"}
            >
              <span class="text-base shrink-0">{item.icon}</span>
              {!collapsed() && <span>{item.label}</span>}
            </A>
          </li>
        ))}
      </ul>

      {/* Version */}
      {!collapsed() && (
        <div class="px-4 py-3 text-xs text-sisoul-muted border-t border-sisoul-border font-mono">
          v1.0.0-internal
        </div>
      )}
    </nav>
  );
}

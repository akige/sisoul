import { createSignal, Show } from "solid-js";
import { A } from "@solidjs/router";

interface MobileNavItem {
  path: string;
  label: string;
}

const MOBILE_NAV: MobileNavItem[] = [
  { path: "/", label: "Vault" },
  { path: "/goals", label: "Goals" },
  { path: "/chat-history", label: "History" },
  { path: "/settings", label: "Settings" },
  { path: "/advanced", label: "Advanced" },
  { path: "/friends", label: "Friends" },
  { path: "/skills", label: "Skills" },
];

export default function TopBar() {
  const [menuOpen, setMenuOpen] = createSignal(false);

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
        <span class="w-2 h-2 rounded-full bg-sisoul-success inline-block" />
        <span>daemon ok</span>
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

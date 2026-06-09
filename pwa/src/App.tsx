import { lazy, Suspense, createSignal, onMount, onCleanup, Show } from "solid-js";
import { Router, Route } from "@solidjs/router";
import Sidebar from "./components/Sidebar";
import TopBar from "./components/TopBar";
import OnboardingScreen from "./components/OnboardingScreen";

// 7 路由 lazy 加载 (chunk splitting 使初始 payload 最小)
const Vault = lazy(() => import("./routes/Vault"));
const Goals = lazy(() => import("./routes/Goals"));
const ChatHistory = lazy(() => import("./routes/ChatHistory"));
const Settings = lazy(() => import("./routes/Settings"));
const Advanced = lazy(() => import("./routes/Advanced"));
const Friends = lazy(() => import("./routes/Friends"));
const Skills = lazy(() => import("./routes/Skills"));
const Borrow = lazy(() => import("./routes/Borrow"));
const Lend = lazy(() => import("./routes/Lend"));
const V2Dashboard = lazy(() => import("./routes/V2Dashboard"));
const Ask = lazy(() => import("./routes/Ask"));
const Debate = lazy(() => import("./routes/Debate"));
const SkillsV2 = lazy(() => import("./routes/SkillsV2"));
const Stats = lazy(() => import("./routes/Stats"));
const Cheatsheet = lazy(() => import("./routes/Cheatsheet"));
const V3RSI = lazy(() => import("./routes/V3RSI"));

const DAEMON_BASE = import.meta.env.VITE_DAEMON_BASE || "http://127.0.0.1:9876";

// 根路由 daemon 检测 — offline 显示 OnboardingScreen, online 显示 Vault
// 与 TopBar.tsx 的 health() 检测独立, 但用同 /sisoul/health endpoint
function Root() {
  // null = 未知 (首次加载), true = online, false = offline
  const [daemonOnline, setDaemonOnline] = createSignal<boolean | null>(null);
  let timer: ReturnType<typeof setInterval> | undefined;

  const checkHealth = async () => {
    try {
      const r = await fetch(`${DAEMON_BASE}/sisoul/health`, {
        signal: AbortSignal.timeout(2000),
      });
      setDaemonOnline(r.ok);
    } catch {
      setDaemonOnline(false);
    }
  };

  onMount(() => {
    checkHealth();
    // 15s poll — daemon 一上线就自动切到 Vault
    timer = setInterval(checkHealth, 15_000);
  });

  onCleanup(() => {
    if (timer) clearInterval(timer);
  });

  return (
    <Show
      when={daemonOnline() === true}
      fallback={
        // 首次未知或 offline → OnboardingScreen
        // null 时也显示 OnboardingScreen, 比"加载中..."更友好且很快会切
        <OnboardingScreen />
      }
    >
      <Layout>
        <Vault />
      </Layout>
    </Show>
  );
}

function Layout(props: { children?: any }) {
  return (
    <div class="flex h-full w-full bg-sisoul-bg text-sisoul-text">
      <Sidebar />
      <div class="flex flex-col flex-1 min-w-0">
        <TopBar />
        <main class="flex-1 overflow-y-auto p-4 scrollbar-thin">
          <Suspense fallback={<div class="p-8 text-sisoul-muted">加载中...</div>}>
            {props.children}
          </Suspense>
        </main>
      </div>
    </div>
  );
}

// vite base="./" 时 BASE_URL 不可靠, 改运行时 detect.
// 同一 build artifact 部署到 /sisoul/ (GH Pages) / /app/ (daemon) / / (dev)
// 都正确. 取 location.pathname 的第一个 segment 作 Router base.
function detectRouterBase(): string {
  if (typeof window === "undefined") return "";
  const segs = window.location.pathname.split("/").filter(Boolean);
  // /sisoul/ → ["sisoul"]      → "/sisoul"
  // /app/borrow → ["app","borrow"] → "/app"
  // / → []                     → ""
  return segs.length > 0 ? "/" + segs[0] : "";
}
const ROUTER_BASE = detectRouterBase();

export default function App() {
  return (
    <Router base={ROUTER_BASE}>
      <Route path="/" component={Root} />
      <Route
        path="/vault"
        component={() => (
          <Layout>
            <Vault />
          </Layout>
        )}
      />
      <Route
        path="/goals"
        component={() => (
          <Layout>
            <Goals />
          </Layout>
        )}
      />
      <Route
        path="/chat-history"
        component={() => (
          <Layout>
            <ChatHistory />
          </Layout>
        )}
      />
      <Route
        path="/settings"
        component={() => (
          <Layout>
            <Settings />
          </Layout>
        )}
      />
      <Route
        path="/advanced"
        component={() => (
          <Layout>
            <Advanced />
          </Layout>
        )}
      />
      <Route
        path="/friends"
        component={() => (
          <Layout>
            <Friends />
          </Layout>
        )}
      />
      <Route
        path="/skills"
        component={() => (
          <Layout>
            <Skills />
          </Layout>
        )}
      />
      <Route
        path="/borrow"
        component={() => (
          <Layout>
            <Borrow />
          </Layout>
        )}
      />
      <Route
        path="/lend"
        component={() => (
          <Layout>
            <Lend />
          </Layout>
        )}
      />
      <Route
        path="/ask"
        component={() => (
          <Layout>
            <Ask />
          </Layout>
        )}
      />
      <Route
        path="/debate"
        component={() => (
          <Layout>
            <Debate />
          </Layout>
        )}
      />
      <Route
        path="/skills/v2"
        component={() => (
          <Layout>
            <SkillsV2 />
          </Layout>
        )}
      />
      <Route
        path="/stats"
        component={() => (
          <Layout>
            <Stats />
          </Layout>
        )}
      />
      <Route
        path="/cheatsheet"
        component={() => (
          <Layout>
            <Cheatsheet />
          </Layout>
        )}
      />
      <Route
        path="/dashboard/v2"
        component={() => (
          <Layout>
            <V2Dashboard />
          </Layout>
        )}
      />
      <Route
        path="/rsi"
        component={() => (
          <Layout>
            <V3RSI />
          </Layout>
        )}
      />
    </Router>
  );
}

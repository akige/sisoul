import { lazy, Suspense } from "solid-js";
import { Router, Route } from "@solidjs/router";
import Sidebar from "./components/Sidebar";
import TopBar from "./components/TopBar";

// 7 路由 lazy 加载 (chunk splitting 使初始 payload 最小)
const Vault = lazy(() => import("./routes/Vault"));
const Goals = lazy(() => import("./routes/Goals"));
const ChatHistory = lazy(() => import("./routes/ChatHistory"));
const Settings = lazy(() => import("./routes/Settings"));
const Advanced = lazy(() => import("./routes/Advanced"));
const Friends = lazy(() => import("./routes/Friends"));
const Skills = lazy(() => import("./routes/Skills"));

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

export default function App() {
  return (
    <Router>
      <Route
        path="/"
        component={() => (
          <Layout>
            <Vault />
          </Layout>
        )}
      />
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
    </Router>
  );
}

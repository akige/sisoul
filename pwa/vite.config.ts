import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

// base="./" 让 HTML asset 引用走相对路径, 同一 build 既能:
//   - 在 GitHub Pages 部署 akige.github.io/sisoul/  → ./assets/... 解析正确
//   - 在 daemon serve http://127.0.0.1:9876/app/    → ./assets/... 解析正确
// Router base 在运行时从 window.location.pathname 自动 detect (见 App.tsx).
// dev (vite serve) 用 VITE_BASE='/' override.
const BASE = process.env.VITE_BASE ?? "./";

export default defineConfig({
  base: BASE,
  plugins: [solid()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/sisoul": {
        target: "http://127.0.0.1:9876",
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
  build: {
    target: "esnext",
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/routes/Vault")) return "Vault";
          if (id.includes("/routes/Goals")) return "Goals";
          if (id.includes("/routes/ChatHistory")) return "ChatHistory";
          if (id.includes("/routes/Settings")) return "Settings";
          if (id.includes("/routes/Advanced")) return "Advanced";
          if (id.includes("/routes/Friends")) return "Friends";
          if (id.includes("/routes/Skills")) return "Skills";
          if (id.includes("/routes/Borrow")) return "Borrow";
          if (id.includes("/routes/Lend")) return "Lend";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/unit/setup.ts"],
    include: ["tests/unit/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["tests/e2e/**"],
    transformMode: { web: [/\.[jt]sx?$/] },
  },
});

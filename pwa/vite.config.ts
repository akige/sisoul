import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

// base 路径适配 GitHub Pages 部署 akige.github.io/sisoul/ (项目页).
// 本地 dev / preview / vitest 不需要 base, 用 VITE_BASE='/' override.
const BASE = process.env.VITE_BASE ?? "/sisoul/";

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

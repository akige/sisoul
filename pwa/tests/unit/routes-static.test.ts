/**
 * 静态分析: 7 路由文件存在 + App.tsx 路由声明 + export 完整性
 * 不跑 jsdom (import 路由文件可能触发 solid reactive), 只做 fs + regex 检查
 */
import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PWA_DIR = resolve(__dirname, "../..");
const ROUTES_DIR = resolve(PWA_DIR, "src/routes");
const APP_TSX = resolve(PWA_DIR, "src/App.tsx");

const ROUTES = ["Vault", "Goals", "ChatHistory", "Settings", "Advanced", "Friends", "Skills", "Borrow", "Lend"];

describe("route TSX files exist", () => {
  for (const route of ROUTES) {
    it(`${route}.tsx exists`, () => {
      expect(existsSync(resolve(ROUTES_DIR, `${route}.tsx`))).toBe(true);
    });
  }
});

describe("route TSX files have export default", () => {
  for (const route of ROUTES) {
    it(`${route}.tsx has export default`, () => {
      const content = readFileSync(resolve(ROUTES_DIR, `${route}.tsx`), "utf-8");
      expect(content).toMatch(/export default/);
    });
  }
});

describe("route TSX files have data-route attribute", () => {
  for (const route of ROUTES) {
    it(`${route}.tsx has data-route="${route.toLowerCase().replace("chathistory", "chat-history")}"`, () => {
      const content = readFileSync(resolve(ROUTES_DIR, `${route}.tsx`), "utf-8");
      const routeAttr = route === "ChatHistory" ? "chat-history" : route.toLowerCase();
      expect(content).toContain(`data-route="${routeAttr}"`);
    });
  }
});

describe("App.tsx imports all 7 routes", () => {
  const appContent = readFileSync(APP_TSX, "utf-8");

  for (const route of ROUTES) {
    it(`App.tsx references ${route}`, () => {
      expect(appContent).toContain(`./routes/${route}`);
    });
  }

  it("App.tsx uses lazy() for code splitting", () => {
    expect(appContent).toContain("lazy(");
  });

  it("App.tsx uses Router", () => {
    expect(appContent).toContain("Router");
  });
});

describe("App.tsx routes path coverage", () => {
  const appContent = readFileSync(APP_TSX, "utf-8");

  it("has / (Vault) path", () => {
    expect(appContent).toContain('path="/"');
  });

  it("has /goals path", () => {
    expect(appContent).toContain('path="/goals"');
  });

  it("has /chat-history path", () => {
    expect(appContent).toContain('path="/chat-history"');
  });

  it("has /settings path", () => {
    expect(appContent).toContain('path="/settings"');
  });

  it("has /advanced path", () => {
    expect(appContent).toContain('path="/advanced"');
  });

  it("has /friends path", () => {
    expect(appContent).toContain('path="/friends"');
  });

  it("has /skills path", () => {
    expect(appContent).toContain('path="/skills"');
  });
});

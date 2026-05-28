import { test, expect } from "@playwright/test";

const ROUTES = [
  { path: "/", title: "Vault", dataRoute: "vault" },
  { path: "/vault", title: "Vault", dataRoute: "vault" },
  { path: "/goals", title: "Goals", dataRoute: "goals" },
  { path: "/chat-history", title: "ChatHistory", dataRoute: "chat-history" },
  { path: "/settings", title: "Settings", dataRoute: "settings" },
  { path: "/advanced", title: "Advanced", dataRoute: "advanced" },
  { path: "/friends", title: "Friends", dataRoute: "friends" },
  { path: "/skills", title: "Skills", dataRoute: "skills" },
  { path: "/borrow", title: "Borrow", dataRoute: "borrow" },
  { path: "/lend", title: "Lend", dataRoute: "lend" },
];

test.describe("PWA 7 路由 smoke test", () => {
  for (const route of ROUTES) {
    test(`${route.path} 可访问且有 data-route`, async ({ page }) => {
      await page.goto(route.path);
      await expect(page).toHaveTitle(/sisoul/i);

      // 等待路由组件挂载
      const routeEl = page.locator(`[data-route="${route.dataRoute}"]`);
      await expect(routeEl).toBeVisible({ timeout: 5000 });
    });
  }
});

test.describe("移动菜单 (TopBar hamburger)", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("hamburger 按钮可见 + 点击展开菜单", async ({ page }) => {
    await page.goto("/");
    const menuBtn = page.getByRole("button", { name: "menu" });
    await expect(menuBtn).toBeVisible();
    await menuBtn.click();
    // 移动菜单出现
    await expect(page.getByRole("list", { name: "mobile-nav" })).toBeVisible();
  });
});

test.describe("PWA manifest 可达", () => {
  test("GET /manifest.json returns valid JSON", async ({ page }) => {
    const resp = await page.request.get("/manifest.json");
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.name).toBeTruthy();
    expect(body.display).toBe("standalone");
  });
});

test.describe("service worker 文件可达", () => {
  test("GET /sw.js returns 200", async ({ page }) => {
    const resp = await page.request.get("/sw.js");
    expect(resp.status()).toBe(200);
  });
});

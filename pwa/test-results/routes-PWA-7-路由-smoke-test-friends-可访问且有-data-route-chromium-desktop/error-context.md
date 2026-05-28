# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: routes.spec.ts >> PWA 7 路由 smoke test >> /friends 可访问且有 data-route
- Location: tests/e2e/routes.spec.ts:18:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('[data-route="friends"]')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('[data-route="friends"]')

```

```yaml
- navigation "sidebar":
  - text: sisoul
  - button "toggle sidebar": ‹
  - list:
    - listitem:
      - link "◈ Vault":
        - /url: /
    - listitem:
      - link "◎ Goals":
        - /url: /goals
    - listitem:
      - link "◷ History":
        - /url: /chat-history
    - listitem:
      - link "◉ Settings":
        - /url: /settings
    - listitem:
      - link "◆ Advanced":
        - /url: /advanced
    - listitem:
      - link "◍ Friends":
        - /url: /friends
    - listitem:
      - link "◈ Skills":
        - /url: /skills
    - listitem:
      - link "↙ Borrow":
        - /url: /borrow
    - listitem:
      - link "↗ Lend":
        - /url: /lend
  - text: v1.0.0-internal
- banner: daemon ok
- main: 加载中...
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | 
  3  | const ROUTES = [
  4  |   { path: "/", title: "Vault", dataRoute: "vault" },
  5  |   { path: "/vault", title: "Vault", dataRoute: "vault" },
  6  |   { path: "/goals", title: "Goals", dataRoute: "goals" },
  7  |   { path: "/chat-history", title: "ChatHistory", dataRoute: "chat-history" },
  8  |   { path: "/settings", title: "Settings", dataRoute: "settings" },
  9  |   { path: "/advanced", title: "Advanced", dataRoute: "advanced" },
  10 |   { path: "/friends", title: "Friends", dataRoute: "friends" },
  11 |   { path: "/skills", title: "Skills", dataRoute: "skills" },
  12 |   { path: "/borrow", title: "Borrow", dataRoute: "borrow" },
  13 |   { path: "/lend", title: "Lend", dataRoute: "lend" },
  14 | ];
  15 | 
  16 | test.describe("PWA 7 路由 smoke test", () => {
  17 |   for (const route of ROUTES) {
  18 |     test(`${route.path} 可访问且有 data-route`, async ({ page }) => {
  19 |       await page.goto(route.path);
  20 |       await expect(page).toHaveTitle(/sisoul/i);
  21 | 
  22 |       // 等待路由组件挂载
  23 |       const routeEl = page.locator(`[data-route="${route.dataRoute}"]`);
> 24 |       await expect(routeEl).toBeVisible({ timeout: 5000 });
     |                             ^ Error: expect(locator).toBeVisible() failed
  25 |     });
  26 |   }
  27 | });
  28 | 
  29 | test.describe("移动菜单 (TopBar hamburger)", () => {
  30 |   test.use({ viewport: { width: 375, height: 812 } });
  31 | 
  32 |   test("hamburger 按钮可见 + 点击展开菜单", async ({ page }) => {
  33 |     await page.goto("/");
  34 |     const menuBtn = page.getByRole("button", { name: "menu" });
  35 |     await expect(menuBtn).toBeVisible();
  36 |     await menuBtn.click();
  37 |     // 移动菜单出现
  38 |     await expect(page.getByRole("list", { name: "mobile-nav" })).toBeVisible();
  39 |   });
  40 | });
  41 | 
  42 | test.describe("PWA manifest 可达", () => {
  43 |   test("GET /manifest.json returns valid JSON", async ({ page }) => {
  44 |     const resp = await page.request.get("/manifest.json");
  45 |     expect(resp.status()).toBe(200);
  46 |     const body = await resp.json();
  47 |     expect(body.name).toBeTruthy();
  48 |     expect(body.display).toBe("standalone");
  49 |   });
  50 | });
  51 | 
  52 | test.describe("service worker 文件可达", () => {
  53 |   test("GET /sw.js returns 200", async ({ page }) => {
  54 |     const resp = await page.request.get("/sw.js");
  55 |     expect(resp.status()).toBe(200);
  56 |   });
  57 | });
  58 | 
```
import { chromium } from "playwright";
const b = await chromium.launch();
const ctx = await b.newContext();
const alice = await ctx.newPage();
const bob = await ctx.newPage();
bob.on("console", m => { if (m.type() === "error") console.log("BOB console.error:", m.text().slice(0,120)); });
alice.on("response", async r => {
  if (r.url().includes("/borrow/run")) {
    console.log("alice borrow/run:", r.status(), (await r.text()).slice(0, 260));
  }
});

// Bob 先开 Lend 页 (SSE 监听就位)
await bob.goto("http://127.0.0.1:9877/app/lend", { waitUntil: "domcontentloaded" });
await bob.waitForTimeout(2500);

// Alice 开 Borrow, 选 bob + per-request 模式, 提交 (不 await — 它会等批准)
await alice.goto("http://127.0.0.1:9876/app/borrow", { waitUntil: "domcontentloaded" });
await alice.waitForTimeout(2500);
const sel = alice.locator("select").first();
const opts = await sel.locator("option").allTextContents();
await sel.selectOption({ index: opts.findIndex(o => o.includes("z6LSgEv9")) });
await alice.locator('[data-testid="borrow-mode-select"]').selectOption("per-request");
await alice.locator('[data-testid="borrow-token-input"]').fill("2000");
await alice.locator('[data-testid="borrow-submit"]').click();
console.log("alice submitted (per-request), button:", await alice.locator('[data-testid="borrow-submit"]').textContent());

// Bob 侧: 等 SSE toast / pending 行出现
await bob.waitForTimeout(6000);
const bobBody = await bob.textContent("body");
console.log("bob sees 新请求 toast/row:", /新 borrow 请求|pending/.test(bobBody));
await bob.screenshot({ path: "/tmp/bob-lend-pending.png", fullPage: true });

// Bob 点 Approve (先展开 pending 行如果需要)
const approveBtn = bob.locator("button", { hasText: "Approve" }).first();
await approveBtn.waitFor({ timeout: 10000 });
await approveBtn.click();
console.log("bob clicked Approve");

// Alice 侧: 等 borrow 完成 + mock 回复渲染
await alice.waitForSelector('[data-testid="borrow-proxy-text"]', { timeout: 120000 });
const reply = await alice.textContent('[data-testid="borrow-proxy-text"]');
console.log("ALICE GOT REPLY:", reply.slice(0, 90));
const stage = await alice.locator('[data-testid="borrow-stage"]').first().textContent();
console.log("alice stage:", stage);
await alice.screenshot({ path: "/tmp/alice-per-request-done.png", fullPage: true });
await b.close();

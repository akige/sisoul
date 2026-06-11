// M2 市场 UI 真验证 · playwright headless
// 打开 /app/borrow → 切自动模式 → 选 claude-sonnet-4-6 → 验证候选列表 / 空状态 → 0 console error
import { chromium } from "playwright";

const BASE = process.env.BASE_URL || "http://127.0.0.1:9876/app";
const errors = [];

const b = await chromium.launch();
const page = await b.newPage();
page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message));
page.on("console", (m) => {
  if (m.type() === "error") errors.push("CONSOLE.ERROR: " + m.text());
});

await page.goto(`${BASE}/borrow`, { waitUntil: "domcontentloaded" });
await page.waitForSelector('[data-testid="market-mode-toggle"]', { timeout: 10000 });
console.log("toggle rendered: OK");

// 默认应是手动模式 → 友邻下拉可见
const manualVisible = await page.locator('[data-testid="borrow-friend-select"]').count();
console.log("default(manual) friend-select present:", manualVisible > 0);

// 切到自动模式
await page.locator('[data-testid="market-mode-auto"]').click();
await page.waitForTimeout(500);

// 确保 model = claude-sonnet-4-6 (provider anthropic 默认即此)
const modelVal = await page.locator('[data-testid="borrow-model-select"]').inputValue();
console.log("model selected:", modelVal);
if (modelVal !== "claude-sonnet-4-6") {
  await page.locator('[data-testid="borrow-model-select"]').selectOption("claude-sonnet-4-6");
}

// 等市场 fetch 落地: 要么候选行, 要么空状态
await page.waitForTimeout(2500);
const rows = await page.locator('[data-testid="market-offer-row"]').count();
const emptyVisible = await page.locator('[data-testid="market-empty"]').count();
console.log("market-offer-row count:", rows);
console.log("market-empty present:", emptyVisible > 0);

if (rows > 0) {
  // 验证最优默认选中 + 渲染价格/信誉/在线率
  const first = page.locator('[data-testid="market-offer-row"]').first();
  const selected = await first.getAttribute("data-selected");
  const txt = await first.textContent();
  console.log("first offer data-selected:", selected);
  console.log("first offer text:", txt.replace(/\s+/g, " ").trim().slice(0, 120));
  if (selected !== "true") errors.push("ASSERT: offers[0] not default-selected");
  if (!/\$|1k|信誉|在线/.test(txt)) errors.push("ASSERT: offer row missing price/reputation/uptime");
} else if (emptyVisible > 0) {
  const t = await page.locator('[data-testid="market-empty"]').textContent();
  console.log("empty state text:", t.trim());
  if (!/暂无可用 lender/.test(t)) errors.push("ASSERT: empty state wording wrong");
} else {
  errors.push("ASSERT: neither offer rows nor empty state rendered");
}

await page.screenshot({ path: "/tmp/sisoul-market-ui.png", fullPage: true });
await b.close();

console.log("---");
console.log("console/page errors:", errors.length);
for (const e of errors) console.log("  " + e);
console.log(errors.length === 0 ? "RESULT: PASS" : "RESULT: FAIL");
process.exit(errors.length === 0 ? 0 : 1);

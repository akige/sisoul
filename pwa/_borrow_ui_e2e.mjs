import { chromium } from "playwright";
const b = await chromium.launch();
const page = await b.newPage();
page.on("pageerror", e => console.log("PAGEERROR:", e.message));
page.on("response", async r => {
  if (r.url().includes("/borrow/run")) {
    console.log("borrow/run status:", r.status());
    try { console.log("borrow/run body:", (await r.text()).slice(0, 400)); } catch {}
  }
});
await page.goto("http://127.0.0.1:9876/app/borrow", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
const sel = page.locator("select").first();
const opts = await sel.locator("option").allTextContents();
const i = opts.findIndex(o => o.includes("z6LSgEv9"));
await sel.selectOption({ index: i });
await page.locator('input[type="number"]').first().fill("2000");
const ta = page.locator("textarea, input[placeholder*='句话']").first();
if (await ta.count()) await ta.fill("UI e2e: 今天天气如何?");
await page.locator("button", { hasText: /发起/ }).first().click();
await page.waitForTimeout(25000);
const stages = await page.locator('[data-testid="borrow-stage"]').allTextContents();
console.log("stages:", stages);
const body = await page.textContent("body");
console.log("mock LLM reply visible:", body.includes("Hi from Bob's mock LLM"));
await page.screenshot({ path: "/tmp/sisoul-borrow-ui-e2e.png", fullPage: true });
await b.close();

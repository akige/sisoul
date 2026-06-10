#!/usr/bin/env node
/**
 * sisoul PWA full e2e audit
 *
 * Real headless playwright walk through every route and interact with every
 * visible button/form. Captures:
 *   - pageerror (uncaught JS)
 *   - console.error / console.warn
 *   - network 4xx/5xx (with response body preview)
 *   - red error strings on DOM ("加载失败", "TypeError", etc)
 *   - modal/toast containing error/失败 wording
 *
 * Usage:
 *   node pwa/_full_audit.mjs
 *
 * Env:
 *   BASE_URL  default http://127.0.0.1:9876/app
 *   OUT_DIR   default /tmp
 */

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:9876/app";
const OUT_DIR = process.env.OUT_DIR || "/tmp";

const ROUTES = [
  "/",
  "/vault",
  "/goals",
  "/chat-history",
  "/settings",
  "/advanced",
  "/friends",
  "/skills",
  "/borrow",
  "/lend",
  "/ask",
  "/debate",
  "/stats",
  "/cheatsheet",
];

// buttons to skip when blanket-clicking
const SKIP_BUTTON_TEXT_PATTERNS = [
  /quit/i,
  /停止\s*daemon/i,
  /shutdown/i,
  /关闭\s*daemon/i,
  /退出/i,
  /sign\s*out/i,
  /reset/i,
  /factory/i,
  /清空/i,
  /删除\s*account/i,
  /wipe/i,
  /destroy/i,
  /delete\s*vault/i,
  // safe-list: skip nav buttons that just route (avoid stray nav loops)
  // we still navigate to every route via goto, so nav buttons are redundant.
];

// red-flag strings to grep on DOM after each interaction
const ERROR_TEXT_PATTERNS = [
  /TypeError/i,
  /ReferenceError/i,
  /SyntaxError/i,
  /\bundefined is not\b/i,
  /\bcannot read prop/i,
  /加载失败/,
  /请求失败/,
  /网络错误/,
  /出错了/,
  /服务异常/,
  /500\s*internal/i,
  /Stack trace/i,
  /Uncaught/,
];

// modal/toast filter wording
const MODAL_ERROR_PATTERNS = [/error/i, /失败/, /异常/, /错误/];

// ---------------- helpers ----------------

function nowTs() {
  return new Date().toISOString().slice(11, 19);
}

function log(...args) {
  console.log(`[${nowTs()}]`, ...args);
}

async function safeText(locator, ms = 200) {
  try {
    return (await locator.textContent({ timeout: ms })) || "";
  } catch {
    return "";
  }
}

function sluggify(route) {
  if (route === "/") return "root";
  return route.replace(/^\//, "").replace(/\//g, "_");
}

function shouldSkipButton(text, testid) {
  const blob = `${text || ""} ${testid || ""}`;
  return SKIP_BUTTON_TEXT_PATTERNS.some((p) => p.test(blob));
}

// ---------------- main ----------------

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const reportPath = path.join(OUT_DIR, "sisoul-audit-report.md");
  const jsonPath = path.join(OUT_DIR, "sisoul-audit-report.json");

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();

  /** @type {Record<string, {
   *   route: string,
   *   loadOk: boolean,
   *   loadError: string|null,
   *   pageErrors: Array<{msg:string,stack:string}>,
   *   consoleErrors: Array<{type:string,text:string}>,
   *   networkBad: Array<{status:number,url:string,bodyPreview:string,method:string}>,
   *   redTexts: Array<{pattern:string,snippet:string}>,
   *   modalErrors: Array<string>,
   *   interactions: Array<{type:string, target:string, ok:boolean, err?:string}>,
   *   screenshot: string,
   * }>} */
  const buckets = {};
  let currentRoute = "/";

  function bucket(r) {
    if (!buckets[r]) {
      buckets[r] = {
        route: r,
        loadOk: false,
        loadError: null,
        pageErrors: [],
        consoleErrors: [],
        networkBad: [],
        redTexts: [],
        modalErrors: [],
        interactions: [],
        screenshot: "",
      };
    }
    return buckets[r];
  }

  // --- listeners attached once, route by closure var ---
  page.on("pageerror", (err) => {
    bucket(currentRoute).pageErrors.push({
      msg: String(err.message || err),
      stack: String(err.stack || "").slice(0, 800),
    });
  });

  page.on("console", (msg) => {
    const type = msg.type();
    if (type === "error" || type === "warning") {
      const text = msg.text();
      // Filter known dev noise
      if (/Download the React DevTools|DevTools/i.test(text)) return;
      bucket(currentRoute).consoleErrors.push({ type, text: text.slice(0, 500) });
    }
  });

  page.on("response", async (resp) => {
    try {
      const status = resp.status();
      if (status >= 400 && status < 600) {
        const url = resp.url();
        // skip favicons / sourcemaps noise
        if (/\.(map|ico|woff2?|ttf|png|jpe?g|gif|svg)(\?|$)/i.test(url)) return;
        let bodyPreview = "";
        try {
          bodyPreview = (await resp.text()).slice(0, 300);
        } catch {
          bodyPreview = "<unreadable>";
        }
        bucket(currentRoute).networkBad.push({
          status,
          method: resp.request().method(),
          url,
          bodyPreview,
        });
      }
    } catch {}
  });

  page.on("dialog", async (dialog) => {
    bucket(currentRoute).modalErrors.push(`native dialog: ${dialog.type()} → ${dialog.message()}`);
    try {
      await dialog.dismiss();
    } catch {}
  });

  // ------------- per-route logic -------------

  async function scanForRedText(b) {
    let bodyText = "";
    try {
      bodyText = await page.evaluate(() => document.body?.innerText || "");
    } catch {
      return;
    }
    for (const p of ERROR_TEXT_PATTERNS) {
      const m = bodyText.match(p);
      if (m) {
        // grab a 120-char window around match
        const idx = bodyText.indexOf(m[0]);
        const snippet = bodyText.slice(Math.max(0, idx - 60), idx + 120).replace(/\s+/g, " ");
        b.redTexts.push({ pattern: String(p), snippet });
      }
    }
  }

  async function scanModalsAndToasts(b) {
    // common toast / modal containers
    const selectors = [
      '[role="alert"]',
      '[role="dialog"]',
      '[data-testid*="toast"]',
      '[data-testid*="error"]',
      '[data-testid*="modal"]',
      ".toast",
      ".modal",
    ];
    for (const sel of selectors) {
      let loc;
      try {
        loc = page.locator(sel);
      } catch {
        continue;
      }
      let count = 0;
      try {
        count = await loc.count();
      } catch {
        continue;
      }
      for (let i = 0; i < Math.min(count, 5); i++) {
        const txt = (await safeText(loc.nth(i), 200)).trim();
        if (!txt) continue;
        if (MODAL_ERROR_PATTERNS.some((p) => p.test(txt))) {
          b.modalErrors.push(`${sel} → "${txt.slice(0, 200)}"`);
        }
      }
    }
  }

  async function clickAllVisibleButtons(b, route) {
    // Limit how many we click per route (avoid combinatorial explosion)
    const MAX = 25;
    let buttons;
    try {
      buttons = await page.locator("button:visible").all();
    } catch {
      return;
    }
    log(`  route ${route}: found ${buttons.length} visible buttons`);
    let clicked = 0;
    for (let i = 0; i < buttons.length && clicked < MAX; i++) {
      const btn = buttons[i];
      let text = "";
      let testid = "";
      let disabled = false;
      let type = "";
      try {
        text = ((await btn.textContent({ timeout: 200 })) || "").trim();
        testid = (await btn.getAttribute("data-testid")) || "";
        type = (await btn.getAttribute("type")) || "";
        disabled = await btn.isDisabled({ timeout: 200 });
      } catch {
        continue;
      }
      if (disabled) continue;
      if (shouldSkipButton(text, testid)) {
        log(`    skip button "${text}" (testid=${testid})`);
        continue;
      }
      // skip submit buttons here — we drive forms explicitly below
      if (type === "submit") continue;
      const desc = `button[${i}] "${text.slice(0, 40)}" testid=${testid}`;
      try {
        await btn.click({ timeout: 1500, trial: false });
        b.interactions.push({ type: "click", target: desc, ok: true });
        clicked++;
        // give SolidJS time to react
        await page.waitForTimeout(400);
        // dismiss any modal that popped open (Esc) so next button is clickable
        try {
          await page.keyboard.press("Escape");
        } catch {}
      } catch (e) {
        b.interactions.push({
          type: "click",
          target: desc,
          ok: false,
          err: String(e.message || e).slice(0, 200),
        });
      }
    }
  }

  async function fillAndSubmitForms(b, route) {
    let forms;
    try {
      forms = await page.locator("form:visible").all();
    } catch {
      return;
    }
    log(`  route ${route}: found ${forms.length} visible forms`);
    for (let f = 0; f < forms.length; f++) {
      const form = forms[f];
      const testid = (await form.getAttribute("data-testid")) || `form[${f}]`;

      // fill inputs with sensible defaults
      const inputs = await form.locator("input:visible, textarea:visible").all();
      for (const inp of inputs) {
        try {
          const type = (await inp.getAttribute("type")) || "text";
          const itestid = (await inp.getAttribute("data-testid")) || "";
          const placeholder = (await inp.getAttribute("placeholder")) || "";
          if (type === "checkbox" || type === "radio") continue;
          let val = "e2e-audit";
          if (type === "number") val = "500";
          if (/token/i.test(itestid)) val = "500";
          if (/did/i.test(itestid) || /did/i.test(placeholder)) {
            val = "did:key:z6LSgEv9jNR4iZN1w8P9SmMWgodS2UH3YCNzmTQp6R7FbVX2";
          }
          if (/handle/i.test(itestid)) val = "@e2e-audit";
          if (/trust/i.test(itestid)) val = "1";
          if (/reason/i.test(itestid) || /prompt|question/i.test(placeholder)) {
            val = "e2e audit test prompt";
          }
          await inp.fill(val, { timeout: 1000 });
        } catch {}
      }

      // submit
      const submitBtn = form.locator('button[type="submit"], button[data-testid*="submit" i]').first();
      try {
        if ((await submitBtn.count()) > 0 && (await submitBtn.isVisible())) {
          if (!(await submitBtn.isDisabled())) {
            await submitBtn.click({ timeout: 1500 });
            b.interactions.push({
              type: "form-submit",
              target: `form[testid=${testid}]`,
              ok: true,
            });
            await page.waitForTimeout(8000);
          } else {
            b.interactions.push({
              type: "form-submit",
              target: `form[testid=${testid}]`,
              ok: false,
              err: "submit button disabled",
            });
          }
        }
      } catch (e) {
        b.interactions.push({
          type: "form-submit",
          target: `form[testid=${testid}]`,
          ok: false,
          err: String(e.message || e).slice(0, 200),
        });
      }
    }
  }

  // ------------- route-specific deep tests -------------

  async function deepFriends(b) {
    // open Add Friend modal
    try {
      const opener = page.locator('[data-testid="open-add-friend-modal"]');
      if ((await opener.count()) > 0) {
        await opener.first().click({ timeout: 2000 });
        await page.waitForTimeout(800);
        const handle = page.locator('[data-testid="add-friend-handle-input"]');
        if ((await handle.count()) > 0) {
          await handle.first().fill("@e2e-akige");
        }
        const did = page.locator('[data-testid="add-friend-did-input"]');
        if ((await did.count()) > 0) {
          await did.first().fill("did:key:z6LSeGSR6a3GiyFajKGzCBhJQ2ywtxd4ERFJBWvRLw2JpC7n");
        }
        const submit = page.locator('[data-testid="add-friend-submit"]');
        if ((await submit.count()) > 0) {
          await submit.first().click({ timeout: 2000 });
          b.interactions.push({ type: "deep", target: "Friends add modal submit", ok: true });
          await page.waitForTimeout(5000);
        }
      }
    } catch (e) {
      b.interactions.push({
        type: "deep",
        target: "Friends add modal",
        ok: false,
        err: String(e.message || e),
      });
    }
  }

  async function deepBorrow(b) {
    try {
      // pick bob-mock friend
      const select = page.locator('[data-testid="borrow-friend-select"]');
      if ((await select.count()) > 0) {
        const opts = await select.locator("option").allTextContents();
        const target = opts.find((o) => /bob-mock/.test(o)) || opts[0];
        if (target) {
          await select.selectOption({ label: target.trim() }).catch(async () => {
            // fallback: by value
            try {
              const values = await select.locator("option").evaluateAll((els) =>
                els.map((e) => e.value),
              );
              if (values.length > 0) await select.selectOption(values[0]);
            } catch {}
          });
        }
      }
      // tokens=500
      const tk = page.locator('[data-testid="borrow-token-input"]');
      if ((await tk.count()) > 0) await tk.first().fill("500");
      const rs = page.locator('[data-testid="borrow-reason-input"]');
      if ((await rs.count()) > 0) await rs.first().fill("e2e audit");
      const sb = page.locator('[data-testid="borrow-submit"]');
      if ((await sb.count()) > 0) {
        const dis = await sb.isDisabled();
        if (!dis) {
          await sb.click({ timeout: 2000 });
          b.interactions.push({ type: "deep", target: "Borrow submit", ok: true });
          await page.waitForTimeout(10000); // borrow round-trip can take time
        } else {
          b.interactions.push({ type: "deep", target: "Borrow submit", ok: false, err: "disabled" });
        }
      }
    } catch (e) {
      b.interactions.push({ type: "deep", target: "Borrow", ok: false, err: String(e.message || e) });
    }
  }

  async function deepLend(b) {
    // try click any Approve/Deny on pending request
    try {
      const approveBtns = page.locator(
        'button:has-text("Approve"), button:has-text("批准"), button:has-text("approve")',
      );
      if ((await approveBtns.count()) > 0) {
        await approveBtns.first().click({ timeout: 2000 });
        b.interactions.push({ type: "deep", target: "Lend approve", ok: true });
        await page.waitForTimeout(5000);
      } else {
        b.interactions.push({ type: "deep", target: "Lend approve", ok: false, err: "no approve btn" });
      }
    } catch (e) {
      b.interactions.push({ type: "deep", target: "Lend approve", ok: false, err: String(e.message || e) });
    }
  }

  async function deepAsk(b) {
    try {
      // find textarea (Question)
      const ta = page.locator("textarea:visible").first();
      if ((await ta.count()) > 0) {
        await ta.fill("How to fix tokio::select deadlock? e2e audit");
      }
      const askBtn = page.locator('button:has-text("Ask")').first();
      if ((await askBtn.count()) > 0 && !(await askBtn.isDisabled())) {
        await askBtn.click({ timeout: 2000 });
        b.interactions.push({ type: "deep", target: "Ask submit", ok: true });
        await page.waitForTimeout(8000);
      }
    } catch (e) {
      b.interactions.push({ type: "deep", target: "Ask", ok: false, err: String(e.message || e) });
    }
  }

  async function deepGoals(b) {
    try {
      // any button containing 加 / Add / 新建 / new goal
      const addBtn = page.locator(
        'button:has-text("加"), button:has-text("Add"), button:has-text("New"), button:has-text("新建")',
      );
      if ((await addBtn.count()) > 0) {
        await addBtn.first().click({ timeout: 2000 });
        b.interactions.push({ type: "deep", target: "Goals add", ok: true });
        await page.waitForTimeout(3000);
        // if a prompt input appeared, fill + confirm
        const promptInput = page.locator("input:visible, textarea:visible").first();
        if ((await promptInput.count()) > 0) {
          await promptInput.fill("e2e audit goal");
        }
        // try a confirm button
        const confirm = page.locator(
          'button:has-text("确定"), button:has-text("Save"), button:has-text("保存"), button:has-text("提交"), button:has-text("OK")',
        );
        if ((await confirm.count()) > 0) {
          await confirm.first().click({ timeout: 1500 }).catch(() => {});
          await page.waitForTimeout(3000);
        }
      }
    } catch (e) {
      b.interactions.push({ type: "deep", target: "Goals add", ok: false, err: String(e.message || e) });
    }
  }

  // ------------- main loop -------------

  for (const route of ROUTES) {
    currentRoute = route;
    const b = bucket(route);
    const url = BASE_URL + (route === "/" ? "/" : route);
    log(`>>> ${route} (${url})`);

    try {
      await page.goto(url, { waitUntil: "load", timeout: 15000 });
      b.loadOk = true;
    } catch (e) {
      b.loadError = String(e.message || e);
      b.loadOk = false;
      log(`  load failed: ${b.loadError}`);
      continue;
    }

    // wait 5s for createResource etc
    await page.waitForTimeout(5000);

    // route-specific deep flows BEFORE generic clicks (we control the form state)
    if (route === "/friends") await deepFriends(b);
    if (route === "/borrow") await deepBorrow(b);
    if (route === "/lend") await deepLend(b);
    if (route === "/ask") await deepAsk(b);
    if (route === "/goals") await deepGoals(b);

    // settle
    await page.waitForTimeout(2000);

    // generic: click all visible buttons
    await clickAllVisibleButtons(b, route);

    // generic: fill+submit any remaining forms
    await fillAndSubmitForms(b, route);

    // scan DOM for red text and modal errors
    await scanForRedText(b);
    await scanModalsAndToasts(b);

    // screenshot
    const shot = path.join(OUT_DIR, `sisoul-audit-${sluggify(route)}.png`);
    try {
      await page.screenshot({ path: shot, fullPage: true });
      b.screenshot = shot;
    } catch (e) {
      b.screenshot = `<screenshot failed: ${e.message}>`;
    }
  }

  await context.close();
  await browser.close();

  // ------------- write reports -------------

  fs.writeFileSync(jsonPath, JSON.stringify(buckets, null, 2));

  const lines = [];
  const totalBugs = {};
  let grandTotal = 0;
  for (const r of ROUTES) {
    const b = buckets[r];
    const n =
      b.pageErrors.length +
      b.consoleErrors.length +
      b.networkBad.length +
      b.redTexts.length +
      b.modalErrors.length +
      (b.loadOk ? 0 : 1);
    totalBugs[r] = n;
    grandTotal += n;
  }

  const ranked = Object.entries(totalBugs).sort((a, b) => b[1] - a[1]);

  lines.push("# sisoul PWA 全面 e2e Audit 报告");
  lines.push("");
  lines.push(`- BASE_URL: \`${BASE_URL}\``);
  lines.push(`- 路由数: ${ROUTES.length}`);
  lines.push(`- bug 总数 (pageError + console.error/warn + network 4xx/5xx + 红字 + modal 错误 + load 失败): **${grandTotal}**`);
  lines.push("");
  lines.push("## 路由 bug 排行 (从坏到好)");
  lines.push("");
  lines.push("| 路由 | bug 总数 |");
  lines.push("|---|---|");
  for (const [r, n] of ranked) {
    lines.push(`| \`${r}\` | ${n} |`);
  }
  lines.push("");

  lines.push("## 每路由详情");
  for (const r of ROUTES) {
    const b = buckets[r];
    const status = b.loadOk && totalBugs[r] === 0 ? "✓" : "❌";
    lines.push("");
    lines.push(`### ${status} \`${r}\``);
    lines.push(`- screenshot: \`${b.screenshot}\``);
    if (!b.loadOk) {
      lines.push(`- **页面 load 失败**: ${b.loadError}`);
    }
    if (b.pageErrors.length) {
      lines.push(`- pageerror (uncaught JS) ×${b.pageErrors.length}:`);
      for (const e of b.pageErrors) {
        lines.push(`  - \`${e.msg}\``);
        if (e.stack) {
          const firstLine = e.stack.split("\n").slice(0, 3).join(" | ");
          lines.push(`    stack: \`${firstLine}\``);
        }
      }
    }
    if (b.consoleErrors.length) {
      lines.push(`- console.${"error/warn"} ×${b.consoleErrors.length}:`);
      for (const e of b.consoleErrors.slice(0, 20)) {
        lines.push(`  - [${e.type}] \`${e.text.replace(/`/g, "'")}\``);
      }
      if (b.consoleErrors.length > 20) lines.push(`  - ...还有 ${b.consoleErrors.length - 20} 条`);
    }
    if (b.networkBad.length) {
      lines.push(`- network 4xx/5xx ×${b.networkBad.length}:`);
      for (const n of b.networkBad.slice(0, 30)) {
        lines.push(`  - \`${n.status} ${n.method} ${n.url}\` body=\`${n.bodyPreview.replace(/\n/g, " ").replace(/`/g, "'")}\``);
      }
      if (b.networkBad.length > 30) lines.push(`  - ...还有 ${b.networkBad.length - 30} 条`);
    }
    if (b.redTexts.length) {
      lines.push(`- DOM 红字 / 错误词 ×${b.redTexts.length}:`);
      for (const t of b.redTexts.slice(0, 10)) {
        lines.push(`  - pattern=${t.pattern} snippet=\`${t.snippet.replace(/`/g, "'")}\``);
      }
    }
    if (b.modalErrors.length) {
      lines.push(`- Modal/Toast 含 error/失败 ×${b.modalErrors.length}:`);
      for (const m of b.modalErrors.slice(0, 10)) {
        lines.push(`  - \`${m.replace(/`/g, "'")}\``);
      }
    }
    if (b.interactions.length) {
      const okN = b.interactions.filter((i) => i.ok).length;
      const failN = b.interactions.length - okN;
      lines.push(`- 交互: ${b.interactions.length} 次 (${okN} ok / ${failN} 失败)`);
      const fails = b.interactions.filter((i) => !i.ok);
      for (const f of fails.slice(0, 10)) {
        lines.push(`  - 失败 [${f.type}] ${f.target}: ${f.err}`);
      }
    }
    if (
      b.loadOk &&
      b.pageErrors.length === 0 &&
      b.consoleErrors.length === 0 &&
      b.networkBad.length === 0 &&
      b.redTexts.length === 0 &&
      b.modalErrors.length === 0
    ) {
      lines.push("- 无 bug ✓");
    }
  }

  fs.writeFileSync(reportPath, lines.join("\n"));
  log(`report → ${reportPath}`);
  log(`json   → ${jsonPath}`);
  log(`grand total bugs: ${grandTotal}`);
}

main().catch((e) => {
  console.error("AUDIT CRASHED:", e);
  process.exit(1);
});

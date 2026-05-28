# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: borrow_lend_flow.spec.ts >> Wave B-4 P1-2 Friends + Borrow + Lend e2e >> Add Friend modal: 校验 DID + POST + 加入列表
- Location: tests/e2e/borrow_lend_flow.spec.ts:280:3

# Error details

```
Error: expect(locator).not.toBeVisible() failed

Locator:  locator('[data-testid="add-friend-modal"]')
Expected: not visible
Received: visible
Timeout:  5000ms

Call log:
  - Expect "not toBeVisible" with timeout 5000ms
  - waiting for locator('[data-testid="add-friend-modal"]')
    14 × locator resolved to <div data-testid="add-friend-modal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">…</div>
       - unexpected value "visible"

```

```yaml
- heading "添加朋友" [level=2]
- text: DID *
- textbox "DID *":
  - /placeholder: did:key:z6Mk... or did:sisoul:...
  - text: did:key:z6MkCarolCCCCCCCCCCCCCCCCC
- text: Handle (可选)
- textbox "Handle (可选)":
  - /placeholder: alice
  - text: carol
- text: 信任等级
- combobox "信任等级":
  - option "L1 Read"
  - option "L2 Query" [selected]
  - option "L3 Exec"
- paragraph: "daemon 404: daemon /friend/add → 404"
- button "取消"
- button "添加"
```

# Test source

```ts
  207 |     state.deniedRequests.push(body.request_id);
  208 |     await route.fulfill({
  209 |       status: 200,
  210 |       contentType: "application/json",
  211 |       body: JSON.stringify({
  212 |         request_id: body.request_id,
  213 |         denied_at: new Date().toISOString(),
  214 |       }),
  215 |     });
  216 |   });
  217 | 
  218 |   await page.route(/\/sisoul\/ledger\//, async (route) => {
  219 |     const url = new URL(route.request().url());
  220 |     const dir = url.searchParams.get("direction");
  221 |     const entries = dir
  222 |       ? state.ledger.filter((e) => e.direction === dir)
  223 |       : state.ledger;
  224 |     await route.fulfill({
  225 |       status: 200,
  226 |       contentType: "application/json",
  227 |       body: JSON.stringify({
  228 |         entries,
  229 |         total_tokens: entries.reduce((s, e) => s + e.tokens_used, 0),
  230 |         total_cost_usd: entries.reduce((s, e) => s + (e.cost_usd ?? 0), 0),
  231 |       }),
  232 |     });
  233 |   });
  234 | 
  235 |   // SSE stream — fulfill with empty stream (browser will keep open)
  236 |   await page.route(/\/sisoul\/notify\/stream/, async (route) => {
  237 |     await route.fulfill({
  238 |       status: 200,
  239 |       contentType: "text/event-stream",
  240 |       body: ": connected\n\n",
  241 |     });
  242 |   });
  243 | }
  244 | 
  245 | test.describe("Wave B-4 P1-2 Friends + Borrow + Lend e2e", () => {
  246 |   test("Friends 路由: 列出朋友 + 在线状态 + 跳 borrow", async ({ page }) => {
  247 |     test.setTimeout(60_000);
  248 |     const state = makeMockState();
  249 |     await installMocks(page, state);
  250 | 
  251 |     await page.goto("/friends", { waitUntil: "domcontentloaded" });
  252 |     await expect(page.locator('[data-route="friends"]')).toBeVisible({ timeout: 20_000 });
  253 | 
  254 |     const cards = page.locator('[data-testid="friend-card"]');
  255 |     await expect(cards).toHaveCount(2);
  256 | 
  257 |     // alice 在线
  258 |     const aliceCard = page.locator(
  259 |       '[data-friend-did="did:key:z6MkAliceAAAAAAAAAAAAAAAAA"]'
  260 |     );
  261 |     await expect(aliceCard).toHaveAttribute("data-online", "true");
  262 | 
  263 |     // bob 离线
  264 |     const bobCard = page.locator(
  265 |       '[data-friend-did="did:key:z6MkBobBBBBBBBBBBBBBBBBBBBB"]'
  266 |     );
  267 |     await expect(bobCard).toHaveAttribute("data-online", "false");
  268 | 
  269 |     // alice 有 pending_lend_count=1 badge
  270 |     await expect(
  271 |       aliceCard.locator('[data-testid="friend-lend-pending"]')
  272 |     ).toHaveText("1");
  273 | 
  274 |     // 点 Borrow → /borrow?friend=alice_did
  275 |     await aliceCard.locator('[data-testid="friend-borrow-btn"]').click();
  276 |     await expect(page).toHaveURL(/\/borrow\?friend=/);
  277 |     await expect(page.locator('[data-route="borrow"]')).toBeVisible();
  278 |   });
  279 | 
  280 |   test("Add Friend modal: 校验 DID + POST + 加入列表", async ({ page }) => {
  281 |     test.setTimeout(60_000);
  282 |     const state = makeMockState();
  283 |     await installMocks(page, state);
  284 |     await page.goto("/friends", { waitUntil: "domcontentloaded" });
  285 |     await expect(page.locator('[data-route="friends"]')).toBeVisible({ timeout: 20_000 });
  286 | 
  287 |     // 打开 modal
  288 |     await page.locator('[data-testid="open-add-friend-modal"]').click();
  289 |     await expect(page.locator('[data-testid="add-friend-modal"]')).toBeVisible();
  290 | 
  291 |     // 输入非法 DID
  292 |     const didInput = page.locator('[data-testid="add-friend-did-input"]');
  293 |     await didInput.fill("garbage");
  294 |     await expect(page.locator('[data-testid="add-friend-submit"]')).toBeDisabled();
  295 | 
  296 |     // 改为合法 DID
  297 |     await didInput.fill("did:key:z6MkCarolCCCCCCCCCCCCCCCCC");
  298 |     await page.locator('[data-testid="add-friend-handle-input"]').fill("carol");
  299 |     await page.locator('[data-testid="add-friend-trust-input"]').selectOption("2");
  300 | 
  301 |     await expect(page.locator('[data-testid="add-friend-submit"]')).toBeEnabled();
  302 |     await page.locator('[data-testid="add-friend-submit"]').click();
  303 | 
  304 |     // modal 关闭, carol 加入列表
  305 |     await expect(
  306 |       page.locator('[data-testid="add-friend-modal"]')
> 307 |     ).not.toBeVisible();
      |           ^ Error: expect(locator).not.toBeVisible() failed
  308 |     await expect(page.locator('[data-testid="friend-card"]')).toHaveCount(3);
  309 |     expect(state.addedFriends).toHaveLength(1);
  310 |     expect(state.addedFriends[0].handle).toBe("carol");
  311 |   });
  312 | 
  313 |   test("Borrow + Lend 完整流: 提交 borrow / 批准 lend / deny lend / ledger 刷新", async ({
  314 |     page,
  315 |   }) => {
  316 |     test.setTimeout(60_000);
  317 |     const state = makeMockState();
  318 |     await installMocks(page, state);
  319 | 
  320 |     // === Borrow ===
  321 |     await page.goto("/borrow", { waitUntil: "domcontentloaded" });
  322 |     await expect(page.locator('[data-route="borrow"]')).toBeVisible({ timeout: 20_000 });
  323 | 
  324 |     // 等表单可见
  325 |     await expect(page.locator('[data-testid="borrow-form"]')).toBeVisible({ timeout: 15_000 });
  326 | 
  327 |     // 填写
  328 |     await page
  329 |       .locator('[data-testid="borrow-friend-select"]')
  330 |       .selectOption("did:key:z6MkAliceAAAAAAAAAAAAAAAAA");
  331 |     await page
  332 |       .locator('[data-testid="borrow-provider-select"]')
  333 |       .selectOption("openai");
  334 |     // model 切到 openai 后自动 reset → 应该是 gpt-5
  335 |     await expect(page.locator('[data-testid="borrow-model-select"]')).toHaveValue(
  336 |       "gpt-5"
  337 |     );
  338 |     await page.locator('[data-testid="borrow-token-input"]').fill("3000");
  339 |     await page.locator('[data-testid="borrow-reason-input"]').fill("e2e test");
  340 | 
  341 |     await page.locator('[data-testid="borrow-submit"]').click();
  342 | 
  343 |     // inflight 卡片出现
  344 |     await expect(
  345 |       page.locator('[data-testid="borrow-progress"]')
  346 |     ).toBeVisible({ timeout: 5000 });
  347 |     await expect(page.locator('[data-testid="borrow-stage"]')).toContainText("排队");
  348 | 
  349 |     expect(state.borrowRequests).toHaveLength(1);
  350 |     expect(state.borrowRequests[0].friend_did).toBe(
  351 |       "did:key:z6MkAliceAAAAAAAAAAAAAAAAA"
  352 |     );
  353 |     expect(state.borrowRequests[0].token_count).toBe(3000);
  354 |     expect(state.borrowRequests[0].provider).toBe("openai");
  355 | 
  356 |     // === Lend ===
  357 |     await page.goto("/lend", { waitUntil: "domcontentloaded" });
  358 |     await expect(page.locator('[data-route="lend"]')).toBeVisible({ timeout: 20_000 });
  359 | 
  360 |     const cards = page.locator('[data-testid="lend-request-card"]');
  361 |     await expect(cards).toHaveCount(2);
  362 | 
  363 |     // emergency 排前 (bob 是 emergency)
  364 |     const firstCard = cards.first();
  365 |     await expect(firstCard).toHaveAttribute("data-emergency", "true");
  366 |     await expect(
  367 |       firstCard.locator('[data-testid="lend-emergency-badge"]')
  368 |     ).toBeVisible();
  369 | 
  370 |     // 批准 alice 的请求 (第二张, 非 emergency)
  371 |     const aliceCard = page.locator('[data-request-id="lreq-001"]');
  372 |     await aliceCard.locator('[data-testid="lend-approve-btn"]').click();
  373 | 
  374 |     // toast 出现
  375 |     await expect(page.locator('[data-toast-kind="success"]')).toBeVisible({
  376 |       timeout: 5000,
  377 |     });
  378 | 
  379 |     // alice 卡片消失
  380 |     await expect(aliceCard).not.toBeVisible({ timeout: 5000 });
  381 |     expect(state.approvedRequests).toContain("lreq-001");
  382 | 
  383 |     // 拒绝 bob 的 emergency 请求
  384 |     const bobCard = page.locator('[data-request-id="lreq-002-emerg"]');
  385 |     await bobCard.locator('[data-testid="lend-toggle-advanced"]').click();
  386 |     await bobCard
  387 |       .locator('[data-testid="lend-deny-reason-input"]')
  388 |       .fill("quota exhausted");
  389 |     await bobCard.locator('[data-testid="lend-deny-btn"]').click();
  390 | 
  391 |     await expect(bobCard).not.toBeVisible({ timeout: 5000 });
  392 |     expect(state.deniedRequests).toContain("lreq-002-emerg");
  393 | 
  394 |     // empty state
  395 |     await expect(page.locator('[data-testid="lend-empty"]')).toBeVisible();
  396 |   });
  397 | });
  398 | 
```
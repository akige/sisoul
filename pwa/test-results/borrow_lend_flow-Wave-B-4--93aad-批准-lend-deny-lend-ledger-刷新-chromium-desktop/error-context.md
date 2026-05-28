# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: borrow_lend_flow.spec.ts >> Wave B-4 P1-2 Friends + Borrow + Lend e2e >> Borrow + Lend 完整流: 提交 borrow / 批准 lend / deny lend / ledger 刷新
- Location: tests/e2e/borrow_lend_flow.spec.ts:313:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('[data-testid="borrow-progress"]')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('[data-testid="borrow-progress"]')

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
- main:
  - heading "Borrow" [level=1]
  - paragraph: 主动借朋友 LLM 配额 · Waku P2P 找 peer + 加密 payload + 等批 + LLM stream
  - heading "主动借" [level=3]
  - text: 朋友
  - combobox "朋友":
    - option "alice (L3)" [selected]
    - option "bob (L2)"
  - text: Provider
  - combobox "Provider":
    - option "anthropic"
    - option "openai" [selected]
    - option "google"
    - option "deepseek"
    - option "mistral"
    - option "litellm-proxy"
  - text: Model
  - combobox "Model":
    - option "gpt-5" [selected]
    - option "gpt-5-mini"
    - option "gpt-4o"
  - text: Token count (估算 prompt+completion)
  - spinbutton "Token count (估算 prompt+completion)": "3000"
  - text: 理由 (可选)
  - textbox "理由 (可选)":
    - /placeholder: 给 Bob 看的 1 句话
    - text: e2e test
  - checkbox "emergency (突破 per-request 限制, 走 emergency-only 通道)"
  - text: emergency (突破 per-request 限制, 走 emergency-only 通道)
  - button "发起 borrow"
  - heading "活跃 proxy session (0)" [level=3]
  - button "↻ 刷新"
  - paragraph: 无活跃 proxy session
  - heading "历史 borrow 账本" [level=3]
  - text: "tokens: 1,234 cost: $0.0034 1 行"
  - list:
    - listitem: "借入 completed alice·anthropic/claude-sonnet-4-6 tokens: 1,234 cost: $0.0034 2026-05-27 06:05"
```

# Test source

```ts
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
  307 |     ).not.toBeVisible();
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
> 346 |     ).toBeVisible({ timeout: 5000 });
      |       ^ Error: expect(locator).toBeVisible() failed
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
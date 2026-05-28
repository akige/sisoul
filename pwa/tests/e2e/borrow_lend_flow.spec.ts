// Wave B-4 P1-2 e2e · Friends + Borrow + Lend 完整流
//
// 用 Playwright route interception mock daemon (vite preview 是静态站, 无 backend).
// 不依赖真 Python daemon, dev work 可在 mac 跑.

import { test, expect, type Page } from "@playwright/test";

interface MockState {
  friends: any[];
  lendRequests: any[];
  proxySessions: any[];
  ledger: any[];
  addedFriends: any[];
  approvedRequests: string[];
  deniedRequests: string[];
  borrowRequests: any[];
}

function makeMockState(): MockState {
  return {
    friends: [
      {
        did: "did:key:z6MkAliceAAAAAAAAAAAAAAAAA",
        handle: "alice",
        trust_level: 3,
        connected_at: "2026-05-20T00:00:00Z",
        last_seen_at: Date.now() - 60_000,
        online: true,
        pending_lend_count: 1,
      },
      {
        did: "did:key:z6MkBobBBBBBBBBBBBBBBBBBBBB",
        handle: "bob",
        trust_level: 2,
        connected_at: "2026-05-21T00:00:00Z",
        last_seen_at: Date.now() - 10 * 60_000,
        online: false,
      },
    ],
    lendRequests: [
      {
        request_id: "lreq-001",
        borrower_did: "did:key:z6MkAliceAAAAAAAAAAAAAAAAA",
        borrower_handle: "alice",
        provider: "anthropic",
        model: "claude-sonnet-4-6",
        token_count: 2000,
        reason: "code review",
        emergency_flag: false,
        created_at: "2026-05-28T01:00:00Z",
        expires_at: new Date(Date.now() + 60 * 60_000).toISOString(),
      },
      {
        request_id: "lreq-002-emerg",
        borrower_did: "did:key:z6MkBobBBBBBBBBBBBBBBBBBBBB",
        borrower_handle: "bob",
        provider: "openai",
        model: "gpt-5",
        token_count: 5000,
        emergency_flag: true,
        created_at: "2026-05-28T01:05:00Z",
        expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
      },
    ],
    proxySessions: [],
    ledger: [
      {
        entry_id: "led-001",
        request_id: "old-req",
        direction: "borrow",
        counterparty_did: "did:key:z6MkAliceAAAAAAAAAAAAAAAAA",
        counterparty_handle: "alice",
        provider: "anthropic",
        model: "claude-sonnet-4-6",
        tokens_used: 1234,
        cost_usd: 0.0034,
        started_at: "2026-05-27T10:00:00Z",
        ended_at: "2026-05-27T10:05:00Z",
        status: "completed",
      },
    ],
    addedFriends: [],
    approvedRequests: [],
    deniedRequests: [],
    borrowRequests: [],
  };
}

async function installMocks(page: Page, state: MockState): Promise<void> {
  // 关 EventSource (SSE) 防 reconnect loop 干扰 mock 路由
  await page.addInitScript(() => {
    // @ts-expect-error override global EventSource
    Object.defineProperty(window, "EventSource", {
      value: undefined,
      configurable: true,
      writable: true,
    });
  });
  await page.route("**/sisoul/friend/list", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ friends: state.friends }),
    });
  });

  await page.route("**/sisoul/friend/add", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    const added = {
      did: body.did,
      handle: body.handle,
      trust_level: body.trust_level ?? 1,
      added_at: new Date().toISOString(),
      verified: true,
    };
    state.addedFriends.push(added);
    state.friends.push({
      did: added.did,
      handle: added.handle,
      trust_level: added.trust_level,
      connected_at: added.added_at,
      online: false,
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(added),
    });
  });

  await page.route("**/sisoul/perms/list", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ perms: [] }),
    });
  });

  await page.route("**/sisoul/borrow/run", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    const reqId = `breq-${state.borrowRequests.length + 1}`;
    state.borrowRequests.push({ request_id: reqId, ...body });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: reqId,
        stage: "queued",
      }),
    });
  });

  await page.route("**/sisoul/borrow/proxy-list", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ sessions: state.proxySessions }),
    });
  });

  await page.route("**/sisoul/borrow/proxy-stop", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    state.proxySessions = state.proxySessions.filter(
      (s) => s.session_id !== body.session_id
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        stopped_at: new Date().toISOString(),
        tokens_used: 100,
      }),
    });
  });

  await page.route("**/sisoul/lend/list", async (route) => {
    const remaining = state.lendRequests.filter(
      (r) =>
        !state.approvedRequests.includes(r.request_id) &&
        !state.deniedRequests.includes(r.request_id)
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ requests: remaining }),
    });
  });

  await page.route("**/sisoul/lend/approve", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    state.approvedRequests.push(body.request_id);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: body.request_id,
        session_id: `sess-${body.request_id}`,
        approved_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
      }),
    });
  });

  await page.route("**/sisoul/lend/deny", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    state.deniedRequests.push(body.request_id);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: body.request_id,
        denied_at: new Date().toISOString(),
      }),
    });
  });

  await page.route("**/sisoul/ledger/**", async (route) => {
    const url = new URL(route.request().url());
    const dir = url.searchParams.get("direction");
    const entries = dir
      ? state.ledger.filter((e) => e.direction === dir)
      : state.ledger;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        entries,
        total_tokens: entries.reduce((s, e) => s + e.tokens_used, 0),
        total_cost_usd: entries.reduce((s, e) => s + (e.cost_usd ?? 0), 0),
      }),
    });
  });

  // SSE stream — fulfill with empty stream (browser will keep open)
  await page.route("**/sisoul/notify/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: ": connected\n\n",
    });
  });
}

test.describe("Wave B-4 P1-2 Friends + Borrow + Lend e2e", () => {
  test("Friends 路由: 列出朋友 + 在线状态 + 跳 borrow", async ({ page }) => {
    test.setTimeout(60_000);
    const state = makeMockState();
    await installMocks(page, state);

    await page.goto("/friends", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-route="friends"]')).toBeVisible({ timeout: 20_000 });

    const cards = page.locator('[data-testid="friend-card"]');
    await expect(cards).toHaveCount(2);

    // alice 在线
    const aliceCard = page.locator(
      '[data-friend-did="did:key:z6MkAliceAAAAAAAAAAAAAAAAA"]'
    );
    await expect(aliceCard).toHaveAttribute("data-online", "true");

    // bob 离线
    const bobCard = page.locator(
      '[data-friend-did="did:key:z6MkBobBBBBBBBBBBBBBBBBBBBB"]'
    );
    await expect(bobCard).toHaveAttribute("data-online", "false");

    // alice 有 pending_lend_count=1 badge
    await expect(
      aliceCard.locator('[data-testid="friend-lend-pending"]')
    ).toHaveText("1");

    // 点 Borrow → /borrow?friend=alice_did
    await aliceCard.locator('[data-testid="friend-borrow-btn"]').click();
    await expect(page).toHaveURL(/\/borrow\?friend=/);
    await expect(page.locator('[data-route="borrow"]')).toBeVisible();
  });

  test("Add Friend modal: 校验 DID + POST + 加入列表", async ({ page }) => {
    test.setTimeout(60_000);
    const state = makeMockState();
    await installMocks(page, state);
    await page.goto("/friends", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-route="friends"]')).toBeVisible({ timeout: 20_000 });

    // 打开 modal
    await page.locator('[data-testid="open-add-friend-modal"]').click();
    await expect(page.locator('[data-testid="add-friend-modal"]')).toBeVisible();

    // 输入非法 DID
    const didInput = page.locator('[data-testid="add-friend-did-input"]');
    await didInput.fill("garbage");
    await expect(page.locator('[data-testid="add-friend-submit"]')).toBeDisabled();

    // 改为合法 DID
    await didInput.fill("did:key:z6MkCarolCCCCCCCCCCCCCCCCC");
    await page.locator('[data-testid="add-friend-handle-input"]').fill("carol");
    await page.locator('[data-testid="add-friend-trust-input"]').selectOption("2");

    await expect(page.locator('[data-testid="add-friend-submit"]')).toBeEnabled();
    await page.locator('[data-testid="add-friend-submit"]').click();

    // modal 关闭, carol 加入列表
    await expect(
      page.locator('[data-testid="add-friend-modal"]')
    ).not.toBeVisible();
    await expect(page.locator('[data-testid="friend-card"]')).toHaveCount(3);
    expect(state.addedFriends).toHaveLength(1);
    expect(state.addedFriends[0].handle).toBe("carol");
  });

  test("Borrow + Lend 完整流: 提交 borrow / 批准 lend / deny lend / ledger 刷新", async ({
    page,
  }) => {
    test.setTimeout(60_000);
    const state = makeMockState();
    await installMocks(page, state);

    // === Borrow ===
    await page.goto("/borrow", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-route="borrow"]')).toBeVisible({ timeout: 20_000 });

    // 等表单可见
    await expect(page.locator('[data-testid="borrow-form"]')).toBeVisible({ timeout: 15_000 });

    // 填写
    await page
      .locator('[data-testid="borrow-friend-select"]')
      .selectOption("did:key:z6MkAliceAAAAAAAAAAAAAAAAA");
    await page
      .locator('[data-testid="borrow-provider-select"]')
      .selectOption("openai");
    // model 切到 openai 后自动 reset → 应该是 gpt-5
    await expect(page.locator('[data-testid="borrow-model-select"]')).toHaveValue(
      "gpt-5"
    );
    await page.locator('[data-testid="borrow-token-input"]').fill("3000");
    await page.locator('[data-testid="borrow-reason-input"]').fill("e2e test");

    await page.locator('[data-testid="borrow-submit"]').click();

    // inflight 卡片出现
    await expect(
      page.locator('[data-testid="borrow-progress"]')
    ).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-testid="borrow-stage"]')).toContainText("排队");

    expect(state.borrowRequests).toHaveLength(1);
    expect(state.borrowRequests[0].friend_did).toBe(
      "did:key:z6MkAliceAAAAAAAAAAAAAAAAA"
    );
    expect(state.borrowRequests[0].token_count).toBe(3000);
    expect(state.borrowRequests[0].provider).toBe("openai");

    // === Lend ===
    await page.goto("/lend", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-route="lend"]')).toBeVisible({ timeout: 20_000 });

    const cards = page.locator('[data-testid="lend-request-card"]');
    await expect(cards).toHaveCount(2);

    // emergency 排前 (bob 是 emergency)
    const firstCard = cards.first();
    await expect(firstCard).toHaveAttribute("data-emergency", "true");
    await expect(
      firstCard.locator('[data-testid="lend-emergency-badge"]')
    ).toBeVisible();

    // 批准 alice 的请求 (第二张, 非 emergency)
    const aliceCard = page.locator('[data-request-id="lreq-001"]');
    await aliceCard.locator('[data-testid="lend-approve-btn"]').click();

    // toast 出现
    await expect(page.locator('[data-toast-kind="success"]')).toBeVisible({
      timeout: 5000,
    });

    // alice 卡片消失
    await expect(aliceCard).not.toBeVisible({ timeout: 5000 });
    expect(state.approvedRequests).toContain("lreq-001");

    // 拒绝 bob 的 emergency 请求
    const bobCard = page.locator('[data-request-id="lreq-002-emerg"]');
    await bobCard.locator('[data-testid="lend-toggle-advanced"]').click();
    await bobCard
      .locator('[data-testid="lend-deny-reason-input"]')
      .fill("quota exhausted");
    await bobCard.locator('[data-testid="lend-deny-btn"]').click();

    await expect(bobCard).not.toBeVisible({ timeout: 5000 });
    expect(state.deniedRequests).toContain("lreq-002-emerg");

    // empty state
    await expect(page.locator('[data-testid="lend-empty"]')).toBeVisible();
  });
});

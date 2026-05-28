// Borrow unit tests · daemon API + stage progress logic
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DaemonError } from "../../src/api/daemon";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

describe("borrowRun (mocked fetch)", () => {
  it("POST /borrow/run with full payload", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        request_id: "req-001",
        session_id: "sess-001",
        stage: "queued",
      }),
    });
    const { borrowRun } = await import("../../src/api/daemon");
    const resp = await borrowRun({
      friend_did: "did:key:z6MkBob",
      provider: "anthropic",
      model: "claude-sonnet-4-6",
      token_count: 2000,
      emergency_flag: false,
      reason: "code review",
    });
    expect(resp.request_id).toBe("req-001");
    expect(resp.stage).toBe("queued");

    expect(mockFetch).toHaveBeenCalledWith(
      "/sisoul/borrow/run",
      expect.objectContaining({ method: "POST" })
    );
    const call = mockFetch.mock.calls[0][1];
    const body = JSON.parse(call.body);
    expect(body.friend_did).toBe("did:key:z6MkBob");
    expect(body.token_count).toBe(2000);
    expect(body.emergency_flag).toBe(false);
  });

  it("throws DaemonError on 403 (quota exhausted)", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 403 });
    const { borrowRun } = await import("../../src/api/daemon");
    await expect(
      borrowRun({
        friend_did: "did:key:z6MkX",
        provider: "anthropic",
        model: "x",
        token_count: 100,
      })
    ).rejects.toBeInstanceOf(DaemonError);
  });

  it("propagates emergency_flag", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        request_id: "req-emerg",
        stage: "waku-discover",
      }),
    });
    const { borrowRun } = await import("../../src/api/daemon");
    await borrowRun({
      friend_did: "did:key:z6MkY",
      provider: "openai",
      model: "gpt-5",
      token_count: 5000,
      emergency_flag: true,
    });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.emergency_flag).toBe(true);
  });
});

describe("borrowProxyList (mocked fetch)", () => {
  it("returns sessions on 200", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        sessions: [
          {
            session_id: "s1",
            request_id: "r1",
            friend_did: "did:key:z6MkA",
            friend_handle: "alice",
            provider: "anthropic",
            model: "claude-sonnet-4-6",
            token_count: 1000,
            tokens_used: 234,
            started_at: "2026-05-28T01:00:00Z",
            expires_at: "2026-05-28T02:00:00Z",
            stage: "llm-streaming",
          },
        ],
      }),
    });
    const { borrowProxyList } = await import("../../src/api/daemon");
    const resp = await borrowProxyList();
    expect(resp.sessions).toHaveLength(1);
    expect(resp.sessions[0].tokens_used).toBe(234);
    expect(resp.sessions[0].stage).toBe("llm-streaming");
  });

  it("handles empty list", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ sessions: [] }),
    });
    const { borrowProxyList } = await import("../../src/api/daemon");
    const resp = await borrowProxyList();
    expect(resp.sessions).toHaveLength(0);
  });
});

describe("borrowProxyStop (mocked fetch)", () => {
  it("POSTs session_id", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: "sess-001",
        stopped_at: "2026-05-28T02:00:00Z",
        tokens_used: 1234,
      }),
    });
    const { borrowProxyStop } = await import("../../src/api/daemon");
    const resp = await borrowProxyStop({
      session_id: "sess-001",
      reason: "user cancel",
    });
    expect(resp.tokens_used).toBe(1234);
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.session_id).toBe("sess-001");
    expect(body.reason).toBe("user cancel");
  });
});

describe("ledger fetch", () => {
  it("getLedger encodes friend_did + direction", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        friend_did: "did:key:z6MkA",
        direction: "borrow",
        entries: [],
        total_tokens: 0,
        total_cost_usd: 0,
      }),
    });
    const { getLedger } = await import("../../src/api/daemon");
    await getLedger("did:key:z6MkA", "borrow");
    expect(mockFetch).toHaveBeenCalledWith(
      "/sisoul/ledger/did%3Akey%3Az6MkA?direction=borrow",
      expect.any(Object)
    );
  });

  it("getLedgerAll no friend filter", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entries: [],
        total_tokens: 0,
        total_cost_usd: 0,
      }),
    });
    const { getLedgerAll } = await import("../../src/api/daemon");
    await getLedgerAll("lend");
    expect(mockFetch).toHaveBeenCalledWith(
      "/sisoul/ledger/all?direction=lend",
      expect.any(Object)
    );
  });

  it("getLedgerAll without direction", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ entries: [], total_tokens: 0, total_cost_usd: 0 }),
    });
    const { getLedgerAll } = await import("../../src/api/daemon");
    await getLedgerAll();
    expect(mockFetch).toHaveBeenCalledWith(
      "/sisoul/ledger/all",
      expect.any(Object)
    );
  });
});

describe("stage progress helper (re-impl matching Borrow.tsx)", () => {
  const STAGES = [
    "queued",
    "waku-discover",
    "encrypting",
    "awaiting-approval",
    "llm-streaming",
    "completed",
  ];

  function pct(stage: string): number {
    if (stage === "denied" || stage === "error") return 100;
    const i = STAGES.indexOf(stage);
    if (i < 0) return 0;
    return Math.round(((i + 1) / STAGES.length) * 100);
  }

  it("queued = ~17%", () => {
    expect(pct("queued")).toBe(17);
  });

  it("completed = 100%", () => {
    expect(pct("completed")).toBe(100);
  });

  it("denied = 100%", () => {
    expect(pct("denied")).toBe(100);
  });

  it("error = 100%", () => {
    expect(pct("error")).toBe(100);
  });

  it("unknown stage = 0", () => {
    expect(pct("alien")).toBe(0);
  });

  it("llm-streaming > awaiting-approval", () => {
    expect(pct("llm-streaming")).toBeGreaterThan(pct("awaiting-approval"));
  });
});

describe("addFriend (mocked fetch)", () => {
  it("returns verified=true friend", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        did: "did:key:z6MkBob",
        handle: "bob",
        trust_level: 2,
        added_at: "2026-05-28T01:00:00Z",
        verified: true,
      }),
    });
    const { addFriend } = await import("../../src/api/daemon");
    const resp = await addFriend({
      did: "did:key:z6MkBob",
      handle: "bob",
      trust_level: 2,
    });
    expect(resp.verified).toBe(true);
    expect(resp.handle).toBe("bob");
  });

  it("propagates trust_level", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        did: "did:key:z6MkBob",
        trust_level: 3,
        added_at: "2026-05-28T01:00:00Z",
        verified: true,
      }),
    });
    const { addFriend } = await import("../../src/api/daemon");
    await addFriend({ did: "did:key:z6MkBob", trust_level: 3 });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.trust_level).toBe(3);
  });
});

// Lend unit tests · daemon API + sorting + filtering
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DaemonError } from "../../src/api/daemon";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

describe("lendList (mocked fetch)", () => {
  it("returns pending requests", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        requests: [
          {
            request_id: "r1",
            borrower_did: "did:key:z6MkA",
            borrower_handle: "alice",
            provider: "anthropic",
            model: "claude-sonnet-4-6",
            token_count: 1000,
            emergency_flag: false,
            created_at: "2026-05-28T01:00:00Z",
            expires_at: "2026-05-28T02:00:00Z",
          },
        ],
      }),
    });
    const { lendList } = await import("../../src/api/daemon");
    const resp = await lendList();
    expect(resp.requests).toHaveLength(1);
    expect(resp.requests[0].borrower_handle).toBe("alice");
  });

  it("handles empty list", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ requests: [] }),
    });
    const { lendList } = await import("../../src/api/daemon");
    const resp = await lendList();
    expect(resp.requests).toHaveLength(0);
  });

  it("throws DaemonError on 500", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
    const { lendList } = await import("../../src/api/daemon");
    await expect(lendList()).rejects.toBeInstanceOf(DaemonError);
  });
});

describe("lendApprove (mocked fetch)", () => {
  it("POSTs request_id + duration_minutes", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        request_id: "r1",
        session_id: "sess-001",
        approved_at: "2026-05-28T01:01:00Z",
        expires_at: "2026-05-28T01:31:00Z",
      }),
    });
    const { lendApprove } = await import("../../src/api/daemon");
    const resp = await lendApprove({
      request_id: "r1",
      duration_minutes: 30,
      max_tokens: 5000,
    });
    expect(resp.session_id).toBe("sess-001");
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.request_id).toBe("r1");
    expect(body.duration_minutes).toBe(30);
    expect(body.max_tokens).toBe(5000);
  });

  it("works without max_tokens", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        request_id: "r2",
        session_id: "s2",
        approved_at: "2026-05-28T01:01:00Z",
        expires_at: "2026-05-28T01:31:00Z",
      }),
    });
    const { lendApprove } = await import("../../src/api/daemon");
    await lendApprove({ request_id: "r2" });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.request_id).toBe("r2");
    expect(body.max_tokens).toBeUndefined();
  });
});

describe("lendDeny (mocked fetch)", () => {
  it("POSTs request_id + reason", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        request_id: "r1",
        denied_at: "2026-05-28T01:01:00Z",
      }),
    });
    const { lendDeny } = await import("../../src/api/daemon");
    const resp = await lendDeny({ request_id: "r1", reason: "quota exhausted" });
    expect(resp.denied_at).toBe("2026-05-28T01:01:00Z");
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.reason).toBe("quota exhausted");
  });

  it("works without reason", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ request_id: "r1", denied_at: "x" }),
    });
    const { lendDeny } = await import("../../src/api/daemon");
    await lendDeny({ request_id: "r1" });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.reason).toBeUndefined();
  });
});

describe("sorting: emergency_flag first (re-impl matching Lend.tsx)", () => {
  interface Req {
    id: number;
    emergency_flag: boolean;
    created_at: string;
  }
  function sortRequests(xs: Req[]): Req[] {
    return [...xs].sort((a, b) => {
      if (a.emergency_flag && !b.emergency_flag) return -1;
      if (!a.emergency_flag && b.emergency_flag) return 1;
      return b.created_at.localeCompare(a.created_at);
    });
  }

  it("emergency_flag=true 排前", () => {
    const xs: Req[] = [
      { id: 1, emergency_flag: false, created_at: "2026-05-28T01:00:00Z" },
      { id: 2, emergency_flag: true, created_at: "2026-05-28T00:00:00Z" },
      { id: 3, emergency_flag: false, created_at: "2026-05-28T02:00:00Z" },
    ];
    const sorted = sortRequests(xs);
    expect(sorted[0].id).toBe(2);
  });

  it("非 emergency 之间按 created_at desc", () => {
    const xs = [
      { id: 1, emergency_flag: false, created_at: "2026-05-28T01:00:00Z" },
      { id: 2, emergency_flag: false, created_at: "2026-05-28T03:00:00Z" },
      { id: 3, emergency_flag: false, created_at: "2026-05-28T02:00:00Z" },
    ];
    const sorted = sortRequests(xs);
    expect(sorted.map((s) => s.id)).toEqual([2, 3, 1]);
  });

  it("多条 emergency 之间也按 created_at desc", () => {
    const xs: Req[] = [
      { id: 1, emergency_flag: true, created_at: "2026-05-28T01:00:00Z" },
      { id: 2, emergency_flag: true, created_at: "2026-05-28T02:00:00Z" },
    ];
    const sorted = sortRequests(xs);
    expect(sorted[0].id).toBe(2);
  });
});

describe("DID 校验 (re-impl matching Friends.tsx AddFriendModal)", () => {
  const DID_PATTERN = /^did:(key|sisoul):[A-Za-z0-9._-]{6,}$/;

  const isValid = (s: string) => DID_PATTERN.test(s.trim());

  it("did:key:... 合法", () => {
    expect(isValid("did:key:z6MkBobCDEFGHIJKL")).toBe(true);
  });

  it("did:sisoul:... 合法", () => {
    expect(isValid("did:sisoul:abcdef1234567")).toBe(true);
  });

  it("did:web:... 不合法 (P1-2 只支持 key/sisoul)", () => {
    expect(isValid("did:web:example.com")).toBe(false);
  });

  it("空字符串不合法", () => {
    expect(isValid("")).toBe(false);
  });

  it("前后空格自动 trim", () => {
    expect(isValid("  did:key:z6MkABCDEFGHIJ  ")).toBe(true);
  });

  it("太短不合法", () => {
    expect(isValid("did:key:abc")).toBe(false);
  });
});

describe("friend online detection (re-impl matching Friends.tsx)", () => {
  function isOnline(f: {
    online?: boolean;
    last_seen_at?: number | null;
  }): boolean {
    if (typeof f.online === "boolean") return f.online;
    if (!f.last_seen_at) return false;
    return Date.now() - f.last_seen_at < 5 * 60_000;
  }

  it("online=true 直接 true", () => {
    expect(isOnline({ online: true })).toBe(true);
  });

  it("online=false 直接 false (即使 last_seen 新)", () => {
    expect(isOnline({ online: false, last_seen_at: Date.now() })).toBe(false);
  });

  it("无 online, last_seen 1 分钟前 → online", () => {
    expect(isOnline({ last_seen_at: Date.now() - 60_000 })).toBe(true);
  });

  it("无 online, last_seen 10 分钟前 → offline", () => {
    expect(isOnline({ last_seen_at: Date.now() - 10 * 60_000 })).toBe(false);
  });

  it("无 online + 无 last_seen → offline", () => {
    expect(isOnline({})).toBe(false);
  });
});

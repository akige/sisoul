// LiveLedger unit tests · ledger entry merging + SSE handler + notifyStream
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { LedgerEntry, NotifyEvent } from "../../src/api/daemon";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function makeEntry(
  id: string,
  overrides: Partial<LedgerEntry> = {}
): LedgerEntry {
  return {
    entry_id: id,
    direction: "borrow",
    counterparty_did: "did:key:z6MkA",
    counterparty_handle: "alice",
    provider: "anthropic",
    model: "claude-sonnet-4-6",
    tokens_used: 100,
    cost_usd: 0.001,
    started_at: "2026-05-28T01:00:00Z",
    ended_at: "2026-05-28T01:01:00Z",
    status: "completed",
    ...overrides,
  };
}

describe("entry matching filter (re-impl matching LiveLedger.tsx)", () => {
  function entryMatches(
    e: LedgerEntry,
    friendDid?: string,
    direction?: "borrow" | "lend"
  ): boolean {
    if (friendDid && e.counterparty_did !== friendDid) return false;
    if (direction && e.direction !== direction) return false;
    return true;
  }

  it("无 filter 接受所有", () => {
    const e = makeEntry("e1");
    expect(entryMatches(e)).toBe(true);
  });

  it("friendDid 不匹配 → false", () => {
    const e = makeEntry("e1");
    expect(entryMatches(e, "did:key:z6MkB")).toBe(false);
  });

  it("friendDid 匹配 → true", () => {
    const e = makeEntry("e1");
    expect(entryMatches(e, "did:key:z6MkA")).toBe(true);
  });

  it("direction lend 过滤掉 borrow entry", () => {
    const e = makeEntry("e1", { direction: "borrow" });
    expect(entryMatches(e, undefined, "lend")).toBe(false);
  });

  it("direction borrow 接受 borrow entry", () => {
    const e = makeEntry("e1", { direction: "borrow" });
    expect(entryMatches(e, undefined, "borrow")).toBe(true);
  });

  it("两个 filter 同时", () => {
    const e = makeEntry("e1", { direction: "lend" });
    expect(entryMatches(e, "did:key:z6MkA", "lend")).toBe(true);
    expect(entryMatches(e, "did:key:z6MkA", "borrow")).toBe(false);
  });
});

describe("entry sort desc (re-impl)", () => {
  function sortDesc(entries: LedgerEntry[]): LedgerEntry[] {
    return [...entries].sort((a, b) => {
      const ax = a.ended_at ?? a.started_at;
      const bx = b.ended_at ?? b.started_at;
      return bx.localeCompare(ax);
    });
  }

  it("按 ended_at desc", () => {
    const xs = [
      makeEntry("e1", { ended_at: "2026-05-28T01:00:00Z" }),
      makeEntry("e2", { ended_at: "2026-05-28T03:00:00Z" }),
      makeEntry("e3", { ended_at: "2026-05-28T02:00:00Z" }),
    ];
    const sorted = sortDesc(xs);
    expect(sorted.map((e) => e.entry_id)).toEqual(["e2", "e3", "e1"]);
  });

  it("ended_at 缺失 fallback started_at", () => {
    const xs = [
      makeEntry("e1", { ended_at: undefined, started_at: "2026-05-28T05:00:00Z" }),
      makeEntry("e2", { ended_at: "2026-05-28T03:00:00Z" }),
    ];
    const sorted = sortDesc(xs);
    expect(sorted[0].entry_id).toBe("e1");
  });
});

describe("merged dedupe (re-impl)", () => {
  function mergeWithLive(
    base: LedgerEntry[],
    live: LedgerEntry[],
    pageSize: number
  ): LedgerEntry[] {
    const seen = new Set<string>();
    const out: LedgerEntry[] = [];
    for (const e of live) {
      if (seen.has(e.entry_id)) continue;
      seen.add(e.entry_id);
      out.push(e);
    }
    for (const e of base) {
      if (seen.has(e.entry_id)) continue;
      seen.add(e.entry_id);
      out.push(e);
    }
    return out.slice(0, pageSize);
  }

  it("live entry 替换 base 中同 id", () => {
    const base = [
      makeEntry("e1", { tokens_used: 100 }),
      makeEntry("e2", { tokens_used: 200 }),
    ];
    const live = [makeEntry("e1", { tokens_used: 999 })];
    const merged = mergeWithLive(base, live, 10);
    const e1 = merged.find((e) => e.entry_id === "e1")!;
    expect(e1.tokens_used).toBe(999);
  });

  it("pageSize 截断", () => {
    const base = Array.from({ length: 100 }, (_, i) => makeEntry(`b${i}`));
    const live: LedgerEntry[] = [];
    const merged = mergeWithLive(base, live, 5);
    expect(merged.length).toBe(5);
  });

  it("无重复时全保留", () => {
    const base = [makeEntry("e1"), makeEntry("e2")];
    const live = [makeEntry("e3")];
    const merged = mergeWithLive(base, live, 10);
    expect(merged.length).toBe(3);
    expect(merged.map((e) => e.entry_id).sort()).toEqual(["e1", "e2", "e3"]);
  });
});

describe("notifyStream fallback (no EventSource)", () => {
  it("jsdom 无 EventSource → 返回 no-op handle", async () => {
    // jsdom 不内置 EventSource
    expect((globalThis as any).EventSource).toBeUndefined();
    const { notifyStream } = await import("../../src/api/daemon");
    const events: NotifyEvent[] = [];
    const handle = notifyStream((ev) => events.push(ev));
    expect(handle.readyState()).toBe(2);
    // close 不抛
    handle.close();
    expect(events).toHaveLength(0);
  });
});

describe("notifyStream with mock EventSource", () => {
  class MockEventSource {
    static instances: MockEventSource[] = [];
    listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
    onerror: ((e: Event) => void) | null = null;
    readyState = 0;
    url: string;
    constructor(url: string) {
      this.url = url;
      MockEventSource.instances.push(this);
    }
    addEventListener(type: string, fn: (e: MessageEvent) => void) {
      (this.listeners[type] ??= []).push(fn);
    }
    emit(type: string, payload: unknown) {
      const evt = { data: JSON.stringify(payload) } as MessageEvent;
      (this.listeners[type] ?? []).forEach((fn) => fn(evt));
    }
    close() {
      this.readyState = 2;
    }
  }

  beforeEach(() => {
    MockEventSource.instances = [];
    (globalThis as any).EventSource = MockEventSource;
  });

  afterEach(() => {
    delete (globalThis as any).EventSource;
  });

  it("订阅 ledger.entry 事件", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const events: NotifyEvent[] = [];
    notifyStream((ev) => events.push(ev));
    const es = MockEventSource.instances[0];
    expect(es.url).toContain("/sisoul/notify/stream");
    const entry = makeEntry("e1");
    es.emit("ledger.entry", entry);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("ledger.entry");
    expect((events[0].data as LedgerEntry).entry_id).toBe("e1");
  });

  it("订阅 lend.request 事件", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const events: NotifyEvent[] = [];
    notifyStream((ev) => events.push(ev));
    const es = MockEventSource.instances[0];
    const req = {
      request_id: "r1",
      borrower_did: "did:key:z6MkA",
      provider: "anthropic",
      model: "claude-sonnet-4-6",
      token_count: 1000,
      emergency_flag: false,
      created_at: "2026-05-28T01:00:00Z",
      expires_at: "2026-05-28T02:00:00Z",
    };
    es.emit("lend.request", req);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("lend.request");
  });

  it("订阅 friend.online 事件", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const events: NotifyEvent[] = [];
    notifyStream((ev) => events.push(ev));
    const es = MockEventSource.instances[0];
    es.emit("friend.online", { did: "did:key:z6MkA", online: true, last_seen_at: 123 });
    expect(events).toHaveLength(1);
    if (events[0].type === "friend.online") {
      expect(events[0].data.online).toBe(true);
    }
  });

  it("订阅 borrow.update 事件", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const events: NotifyEvent[] = [];
    notifyStream((ev) => events.push(ev));
    const es = MockEventSource.instances[0];
    es.emit("borrow.update", { request_id: "r1", stage: "llm-streaming" });
    expect(events).toHaveLength(1);
    if (events[0].type === "borrow.update") {
      expect(events[0].data.stage).toBe("llm-streaming");
    }
  });

  it("订阅 heartbeat", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const events: NotifyEvent[] = [];
    notifyStream((ev) => events.push(ev));
    const es = MockEventSource.instances[0];
    es.emit("heartbeat", {});
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("heartbeat");
  });

  it("malformed JSON 不抛错", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const events: NotifyEvent[] = [];
    notifyStream((ev) => events.push(ev));
    const es = MockEventSource.instances[0];
    // 自构造非法 data
    const fn = es.listeners["ledger.entry"][0];
    fn({ data: "not json{" } as MessageEvent);
    expect(events).toHaveLength(0);
  });

  it("onerror 触发 onError callback", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const errors: Event[] = [];
    notifyStream(
      () => {},
      (e) => errors.push(e)
    );
    const es = MockEventSource.instances[0];
    es.onerror?.(new Event("error"));
    expect(errors).toHaveLength(1);
  });

  it("close handle 调 es.close()", async () => {
    const { notifyStream } = await import("../../src/api/daemon");
    const handle = notifyStream(() => {});
    const es = MockEventSource.instances[0];
    expect(es.readyState).toBe(0);
    handle.close();
    expect(es.readyState).toBe(2);
  });
});

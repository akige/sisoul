// vitest - SisoulClient 全面用例 (mock fetch)
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  SisoulClient,
  DaemonError,
  AuthError,
  NetworkError,
  TimeoutError,
} from "../src/index.js";

function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  return vi.fn(async (url: string, init: RequestInit) => handler(url, init));
}

function jsonResp(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("SisoulClient construction", () => {
  it("uses default baseUrl /sisoul", () => {
    const f = mockFetch(() => jsonResp({}));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    expect(c.baseUrl).toBe("/sisoul");
  });

  it("strips trailing slash from baseUrl", () => {
    const f = mockFetch(() => jsonResp({}));
    const c = new SisoulClient({
      baseUrl: "http://localhost:8088/sisoul/",
      fetchImpl: f as unknown as typeof fetch,
    });
    expect(c.baseUrl).toBe("http://localhost:8088/sisoul");
  });

  it("exposes all sub-API namespaces", () => {
    const f = mockFetch(() => jsonResp({}));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    expect(c.vault).toBeDefined();
    expect(c.goals).toBeDefined();
    expect(c.friends).toBeDefined();
    expect(c.skills).toBeDefined();
    expect(c.attest).toBeDefined();
  });
});

describe("vault", () => {
  it("list returns items[]", async () => {
    const f = mockFetch(() =>
      jsonResp({ items: [{ key: "theme", value: "dark", updated_at: "2026-01-01" }] })
    );
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    const out = await c.vault.list();
    expect(out).toHaveLength(1);
    expect(out[0].key).toBe("theme");
  });

  it("get encodes key", async () => {
    let captured = "";
    const f = mockFetch((url) => {
      captured = url;
      return jsonResp({ key: "x", value: "v" });
    });
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await c.vault.get("hello world");
    expect(captured).toContain("key=hello%20world");
  });

  it("set posts body", async () => {
    let body = "";
    const f = mockFetch((_url, init) => {
      body = String(init.body);
      return jsonResp({ ok: true });
    });
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await c.vault.set("a", "b");
    expect(JSON.parse(body)).toEqual({ key: "a", value: "b" });
  });

  it("rejects empty key", async () => {
    const f = mockFetch(() => jsonResp({}));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await expect(c.vault.set("", "x")).rejects.toThrow(/key required/);
  });
});

describe("goals", () => {
  it("list returns goals[]", async () => {
    const f = mockFetch(() => jsonResp({ goals: [{ id: "g1", title: "T", progress: 0.5 }] }));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    const g = await c.goals.list();
    expect(g[0].id).toBe("g1");
  });

  it("add requires title", async () => {
    const f = mockFetch(() => jsonResp({}));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await expect(c.goals.add({ title: "" })).rejects.toThrow(/title required/);
  });

  it("bumpProgress clamps to [0,1]", async () => {
    let updateBody = "";
    const f = mockFetch((url, init) => {
      if (url.endsWith("/goals/list"))
        return jsonResp({ goals: [{ id: "g1", title: "T", progress: 0.9 }] });
      if (url.endsWith("/goals/update")) {
        updateBody = String(init.body);
        return jsonResp({ id: "g1", title: "T", progress: 1 });
      }
      return jsonResp({});
    });
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await c.goals.bumpProgress("g1", 0.5);
    expect(JSON.parse(updateBody).progress).toBe(1);
  });
});

describe("friends", () => {
  it("list returns friends[]", async () => {
    const f = mockFetch(() =>
      jsonResp({
        friends: [
          { did: "did:key:1", trust_level: 0.8, connected_at: "x" },
          { did: "did:key:2", trust_level: 0.3, connected_at: "x" },
        ],
      })
    );
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    const strong = await c.friends.strongTies(0.7);
    expect(strong).toHaveLength(1);
    expect(strong[0].did).toBe("did:key:1");
  });

  it("lend requires friend_did + resource_id", async () => {
    const f = mockFetch(() => jsonResp({}));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await expect(
      c.friends.lend({ friend_did: "", resource_type: "skill", resource_id: "x" })
    ).rejects.toThrow(/friend_did required/);
  });
});

describe("skills", () => {
  it("list uses absolute path /sisoul/skill/list", async () => {
    let captured = "";
    const f = mockFetch((url) => {
      captured = url;
      return jsonResp({ own_did: "did:1", owned: [], available_to_borrow: [] });
    });
    const c = new SisoulClient({
      baseUrl: "/should-not-be-used",
      fetchImpl: f as unknown as typeof fetch,
    });
    await c.skills.list();
    expect(captured).toBe("/sisoul/skill/list");
  });

  it("create rejects missing system_prompt", async () => {
    const f = mockFetch(() => jsonResp({}));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await expect(
      c.skills.create({ name: "x", description: "", system_prompt: "" })
    ).rejects.toThrow(/system_prompt required/);
  });

  it("activeSessions filters status", async () => {
    const f = mockFetch(() =>
      jsonResp({
        own_did: "did:1",
        sessions: [
          { session_id: "s1", status: "active" },
          { session_id: "s2", status: "expired" },
        ],
      })
    );
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    const act = await c.skills.activeSessions();
    expect(act).toHaveLength(1);
    expect(act[0].session_id).toBe("s1");
  });
});

describe("attest", () => {
  it("bySchema filters", async () => {
    const f = mockFetch(() =>
      jsonResp({
        history: [
          { uid: "1", schema: "skill-attest", timestamp: 100, chain: "optimism" },
          { uid: "2", schema: "kyc", timestamp: 200, chain: "optimism" },
        ],
      })
    );
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    const out = await c.attest.bySchema("kyc");
    expect(out).toHaveLength(1);
  });

  it("since filters by timestamp", async () => {
    const f = mockFetch(() =>
      jsonResp({
        history: [
          { uid: "1", schema: "x", timestamp: 100, chain: "c" },
          { uid: "2", schema: "x", timestamp: 200, chain: "c" },
        ],
      })
    );
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    const out = await c.attest.since(150);
    expect(out).toHaveLength(1);
    expect(out[0].uid).toBe("2");
  });
});

describe("error handling", () => {
  it("404 throws DaemonError with status", async () => {
    const f = mockFetch(() => new Response("nope", { status: 404 }));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await expect(c.vault.list()).rejects.toBeInstanceOf(DaemonError);
  });

  it("401 throws AuthError", async () => {
    const f = mockFetch(() => new Response("unauth", { status: 401 }));
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await expect(c.vault.list()).rejects.toBeInstanceOf(AuthError);
  });

  it("network failure throws NetworkError", async () => {
    const f = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch });
    await expect(c.vault.list()).rejects.toBeInstanceOf(NetworkError);
  });

  it("AbortError → TimeoutError", async () => {
    const f = vi.fn(async () => {
      const err = new Error("aborted");
      err.name = "AbortError";
      throw err;
    });
    const c = new SisoulClient({ fetchImpl: f as unknown as typeof fetch, timeout: 1 });
    await expect(c.vault.list()).rejects.toBeInstanceOf(TimeoutError);
  });
});

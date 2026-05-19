import { describe, it, expect, vi, beforeEach } from "vitest";
import { DaemonError } from "../../src/api/daemon";

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

describe("DaemonError", () => {
  it("has correct name and status", () => {
    const err = new DaemonError(503, "service unavailable");
    expect(err.name).toBe("DaemonError");
    expect(err.status).toBe(503);
    expect(err.message).toBe("service unavailable");
  });

  it("is an instance of Error", () => {
    const err = new DaemonError(404, "not found");
    expect(err instanceof Error).toBe(true);
  });
});

describe("listPreferences (mocked fetch)", () => {
  it("returns items on 200", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        items: [{ key: "lang", value: "zh", updated_at: "2026-05-17T00:00:00Z" }],
      }),
    });

    const { listPreferences } = await import("../../src/api/daemon");
    const result = await listPreferences();
    expect(result.items).toHaveLength(1);
    expect(result.items[0].key).toBe("lang");
  });

  it("throws DaemonError on 503", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
    });

    const { listPreferences } = await import("../../src/api/daemon");
    await expect(listPreferences()).rejects.toBeInstanceOf(DaemonError);
  });
});

describe("listGoals (mocked fetch)", () => {
  it("returns goals on 200", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        goals: [{ id: "g1", title: "learn rust", progress: 40 }],
      }),
    });

    const { listGoals } = await import("../../src/api/daemon");
    const result = await listGoals();
    expect(result.goals).toHaveLength(1);
    expect(result.goals[0].progress).toBe(40);
  });
});

describe("listFriends (mocked fetch)", () => {
  it("returns friends on 200", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        friends: [
          {
            did: "did:key:z6MkFriend1",
            handle: "alice",
            trust_level: 2,
            connected_at: "2026-05-01T00:00:00Z",
          },
        ],
      }),
    });

    const { listFriends } = await import("../../src/api/daemon");
    const result = await listFriends();
    expect(result.friends[0].handle).toBe("alice");
    expect(result.friends[0].trust_level).toBe(2);
  });
});

describe("listSkills (mocked fetch)", () => {
  it("returns owned + available_to_borrow on 200", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        own_did: "did:key:z6MkSelf",
        owned: [
          {
            skill_id: "sk-001",
            qualified_name: "did:key:z6MkAuthor/python-helper@1.0.0",
            name: "python-helper",
            version: "1.0.0",
            owner_did: "did:key:z6MkAuthor",
            description: "python utility",
            source: "owned",
            fingerprint: "abc123",
            examples_count: 3,
            personality_traits: ["concise"],
            recommended_models: ["claude-sonnet-4"],
          },
        ],
        available_to_borrow: [],
      }),
    });

    const { listSkills } = await import("../../src/api/daemon");
    const result = await listSkills();
    expect(result.owned[0].name).toBe("python-helper");
    expect(result.own_did).toBe("did:key:z6MkSelf");
  });

  it("throws DaemonError on 401", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
    });

    const { listSkills } = await import("../../src/api/daemon");
    await expect(listSkills()).rejects.toBeInstanceOf(DaemonError);
  });
});

describe("borrowSkill (mocked fetch)", () => {
  it("returns session_id on success", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: "sess-abc123",
        qualified_name: "did:key:z6MkLender/python-helper@1.0.0",
        owner_did: "did:key:z6MkLender",
        borrower_did: "did:key:z6MkSelf",
        skill_id: "sk-001",
        started_at: 1700000000,
        expires_at: 1700003600,
        duration_minutes: 60,
        skill_package_fingerprint: "abc",
        permission_reason: "strong-tie-auto",
        used_fallback: false,
      }),
    });

    const { borrowSkill } = await import("../../src/api/daemon");
    const result = await borrowSkill({
      owner_did: "did:key:z6MkLender",
      skill_name: "python-helper",
      qualified_name: "did:key:z6MkLender/python-helper@1.0.0",
      duration_minutes: 60,
    });
    expect(result.session_id).toBe("sess-abc123");
  });

  it("sends POST with correct body", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: "s1",
        qualified_name: "did:key:z6MkFriend/x@1.0.0",
        owner_did: "did:key:z6MkFriend",
        borrower_did: "did:key:z6MkSelf",
        skill_id: "sk-002",
        started_at: 0,
        expires_at: 0,
        duration_minutes: 30,
        skill_package_fingerprint: "f",
        permission_reason: "ok",
        used_fallback: false,
      }),
    });

    const { borrowSkill } = await import("../../src/api/daemon");
    const body = {
      owner_did: "did:key:z6MkFriend",
      skill_name: "python-helper",
      qualified_name: "did:key:z6MkFriend/python-helper@1.0.0",
      duration_minutes: 30,
    };
    await borrowSkill(body);
    expect(mockFetch).toHaveBeenCalledWith(
      "/sisoul/skill/borrow",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(body),
      })
    );
  });
});

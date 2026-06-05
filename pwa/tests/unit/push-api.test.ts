// Tests for pwa/src/api/push.ts (push register client).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  registerPushDevice,
  listPushDevices,
  unregisterPushDevice,
  sendTestPush,
} from "../../src/api/push";

const originalFetch = global.fetch;

beforeEach(() => {
  global.fetch = vi.fn();
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe("push API client", () => {
  it("registerPushDevice POSTs to /v1/push/register with correct body", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        success: true,
        device: {
          token: "abc123",
          platform: "ios",
          did_key: "did:key:z6MkA",
          registered_at: "2026-06-05T00:00:00Z",
          last_seen_at: "2026-06-05T00:00:00Z",
        },
        is_new: true,
      }),
    });

    const result = await registerPushDevice("abc123", "ios", "did:key:z6MkA");
    expect(result.success).toBe(true);
    expect(result.device.platform).toBe("ios");

    const callArgs = (global.fetch as any).mock.calls[0];
    expect(callArgs[0]).toMatch(/\/v1\/push\/register$/);
    expect(callArgs[1].method).toBe("POST");
    const body = JSON.parse(callArgs[1].body);
    expect(body).toEqual({ token: "abc123", platform: "ios", did_key: "did:key:z6MkA" });
  });

  it("listPushDevices GETs /v1/push/devices", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ devices: [], count: 0 }),
    });

    const result = await listPushDevices();
    expect(result.count).toBe(0);
    expect((global.fetch as any).mock.calls[0][0]).toMatch(/\/v1\/push\/devices$/);
  });

  it("listPushDevices with did_key adds query param", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ devices: [], count: 0 }),
    });

    await listPushDevices("did:key:z6MkAlice");
    expect((global.fetch as any).mock.calls[0][0]).toContain(
      "did_key=did%3Akey%3Az6MkAlice",
    );
  });

  it("unregisterPushDevice DELETEs the token", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true, removed: 1 }),
    });

    const result = await unregisterPushDevice("token-to-remove");
    expect(result.success).toBe(true);
    expect((global.fetch as any).mock.calls[0][1].method).toBe("DELETE");
  });

  it("sendTestPush POSTs title/body/target_did", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ sent: 0, devices_targeted: ["abc..."], note: "skeleton" }),
    });

    await sendTestPush("hi", "hello", "did:key:z6MkA");
    const body = JSON.parse((global.fetch as any).mock.calls[0][1].body);
    expect(body.title).toBe("hi");
    expect(body.body).toBe("hello");
    expect(body.target_did).toBe("did:key:z6MkA");
  });

  it("throws on non-OK response", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
    });

    await expect(registerPushDevice("x", "ios")).rejects.toThrow(/500/);
  });
});

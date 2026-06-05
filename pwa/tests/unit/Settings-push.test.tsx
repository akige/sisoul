// Tests for Settings page PushSection (mobile push UI).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@solidjs/testing-library";
import Settings from "../../src/routes/Settings";

const originalFetch = global.fetch;

beforeEach(() => {
  global.fetch = vi.fn((url: string) => {
    // Mock all daemon endpoints
    if (url.includes("/sisoul/identity")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          did: "did:key:z6MkTest",
          handle: "test-user",
          provider: "anthropic",
          mnemonic_hint: "12 words",
        }),
      } as any);
    }
    if (url.includes("/v1/push/devices")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ devices: [], count: 0 }),
      } as any);
    }
    if (url.includes("/v1/push/test")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ sent: 0, devices_targeted: [], note: "skeleton" }),
      } as any);
    }
    return Promise.reject(new Error(`unmocked URL: ${url}`));
  });
});

afterEach(() => {
  global.fetch = originalFetch;
  delete (window as any).Capacitor;
});

describe("Settings · PushSection", () => {
  it("renders push notifications section header", async () => {
    render(() => <Settings />);
    expect(await screen.findByText(/推送通知/)).toBeInTheDocument();
  });

  it("shows browser mode when Capacitor not present", async () => {
    delete (window as any).Capacitor;
    render(() => <Settings />);
    expect(await screen.findByText(/browser/)).toBeInTheDocument();
  });

  it("shows native iOS context when Capacitor.getPlatform=ios", async () => {
    (window as any).Capacitor = {
      isNativePlatform: () => true,
      getPlatform: () => "ios",
    };
    render(() => <Settings />);
    expect(await screen.findByText(/native \(ios\)/)).toBeInTheDocument();
  });

  it("disables 注册推送 button in browser mode", async () => {
    delete (window as any).Capacitor;
    render(() => <Settings />);
    const btn = await screen.findByText("注册推送 (本设备)");
    expect(btn.closest("button")).toBeDisabled();
  });

  it("发测试推送 button is enabled in browser (works without Capacitor)", async () => {
    delete (window as any).Capacitor;
    render(() => <Settings />);
    const btn = await screen.findByText("发测试推送");
    expect(btn.closest("button")).not.toBeDisabled();
  });

  it("clicks 发测试推送 calls /v1/push/test", async () => {
    render(() => <Settings />);
    const btn = await screen.findByText("发测试推送");
    fireEvent.click(btn);
    // wait microtask + setBusy resolution
    await new Promise((r) => setTimeout(r, 30));
    const calls = (global.fetch as any).mock.calls;
    const tested = calls.some((c: any[]) => c[0].includes("/v1/push/test"));
    expect(tested).toBe(true);
  });

  it("shows 无已注册设备 when devices list empty", async () => {
    render(() => <Settings />);
    expect(await screen.findByText("无已注册设备")).toBeInTheDocument();
  });
});

// Tests for PWA Capacitor detection.
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { isNativeApp, getPlatform } from "../../src/lib/capacitor";

describe("Capacitor detection", () => {
  let originalCap: unknown;

  beforeEach(() => {
    originalCap = (window as any).Capacitor;
  });

  afterEach(() => {
    if (originalCap === undefined) {
      delete (window as any).Capacitor;
    } else {
      (window as any).Capacitor = originalCap;
    }
  });

  it("isNativeApp returns false when Capacitor global undefined", () => {
    delete (window as any).Capacitor;
    expect(isNativeApp()).toBe(false);
  });

  it("isNativeApp returns false when Capacitor.isNativePlatform returns false", () => {
    (window as any).Capacitor = { isNativePlatform: () => false, getPlatform: () => "web" };
    expect(isNativeApp()).toBe(false);
  });

  it("isNativeApp returns true when Capacitor in native context", () => {
    (window as any).Capacitor = { isNativePlatform: () => true, getPlatform: () => "ios" };
    expect(isNativeApp()).toBe(true);
  });

  it("getPlatform returns 'web' when Capacitor missing", () => {
    delete (window as any).Capacitor;
    expect(getPlatform()).toBe("web");
  });

  it("getPlatform returns 'ios' on iOS", () => {
    (window as any).Capacitor = { isNativePlatform: () => true, getPlatform: () => "ios" };
    expect(getPlatform()).toBe("ios");
  });

  it("getPlatform returns 'android' on Android", () => {
    (window as any).Capacitor = { isNativePlatform: () => true, getPlatform: () => "android" };
    expect(getPlatform()).toBe("android");
  });
});

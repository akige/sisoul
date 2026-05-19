import { describe, it, expect } from "vitest";
import {
  formatDate,
  truncateDid,
  formatProgress,
  formatBytes,
  normalizeVersion,
  relativeTime,
} from "../../src/utils/format";

describe("formatDate", () => {
  it("formats valid ISO string to date + time", () => {
    // Use a date that doesn't risk crossing day boundaries with timezone
    const result = formatDate("2026-06-15T12:00:00.000Z");
    // Date part should appear (may shift by timezone but year always present)
    expect(result).toMatch(/2026/);
    // Should contain time HH:MM pattern
    expect(result).toMatch(/\d{2}:\d{2}/);
  });

  it("returns original string for invalid input", () => {
    expect(formatDate("not-a-date")).toBe("not-a-date");
  });

  it("handles empty string gracefully", () => {
    const result = formatDate("");
    expect(typeof result).toBe("string");
  });
});

describe("truncateDid", () => {
  it("truncates long DID", () => {
    const did = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK";
    const result = truncateDid(did);
    expect(result).toContain("...");
    expect(result.length).toBeLessThan(did.length);
  });

  it("returns short DID unchanged", () => {
    const short = "did:key:z6Mk";
    expect(truncateDid(short)).toBe(short);
  });

  it("custom headLen/tailLen", () => {
    const did = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK";
    const result = truncateDid(did, 8, 6);
    expect(result).toContain("...");
    expect(result.startsWith(did.slice(0, 8))).toBe(true);
    expect(result.endsWith(did.slice(-6))).toBe(true);
  });
});

describe("formatProgress", () => {
  it("rounds to nearest integer", () => {
    expect(formatProgress(73.4)).toBe("73%");
    expect(formatProgress(50)).toBe("50%");
    expect(formatProgress(99.9)).toBe("100%");
  });

  it("clamps below 0", () => {
    expect(formatProgress(-5)).toBe("0%");
  });

  it("clamps above 100", () => {
    expect(formatProgress(150)).toBe("100%");
  });

  it("exact 0 and 100", () => {
    expect(formatProgress(0)).toBe("0%");
    expect(formatProgress(100)).toBe("100%");
  });
});

describe("formatBytes", () => {
  it("returns B for small values", () => {
    expect(formatBytes(512)).toBe("512 B");
  });

  it("returns KB for kilobytes", () => {
    expect(formatBytes(2048)).toBe("2.0 KB");
  });

  it("returns MB for megabytes", () => {
    expect(formatBytes(1_500_000)).toBe("1.4 MB");
  });

  it("returns B for 0", () => {
    expect(formatBytes(0)).toBe("0 B");
  });
});

describe("normalizeVersion", () => {
  it("adds v prefix when missing", () => {
    expect(normalizeVersion("1.2.3")).toBe("v1.2.3");
  });

  it("keeps v prefix if already present", () => {
    expect(normalizeVersion("v1.2.3")).toBe("v1.2.3");
  });

  it("handles empty string", () => {
    expect(normalizeVersion("")).toBe("v");
  });
});

describe("relativeTime", () => {
  it("returns 刚刚 for < 1 minute", () => {
    const now = new Date().toISOString();
    expect(relativeTime(now)).toBe("刚刚");
  });

  it("handles invalid date", () => {
    const result = relativeTime("invalid");
    expect(typeof result).toBe("string");
  });

  it("returns minutes ago for recent times", () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(relativeTime(fiveMinAgo)).toBe("5 分钟前");
  });

  it("returns hours ago for 2h ago", () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 3600_000).toISOString();
    expect(relativeTime(twoHoursAgo)).toBe("2 小时前");
  });

  it("returns days ago for >24h", () => {
    const twoDaysAgo = new Date(Date.now() - 2 * 86400_000).toISOString();
    expect(relativeTime(twoDaysAgo)).toBe("2 天前");
  });
});

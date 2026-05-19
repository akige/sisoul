// sisoul PWA 工具函数 · format / display helpers

/**
 * 把 ISO 日期字符串格式化为本地友好显示
 * e.g. "2026-05-17T14:30:00Z" → "2026-05-17 14:30"
 */
export function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const pad = (n: number) => String(n).padStart(2, "0");
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}`
    );
  } catch {
    return iso;
  }
}

/**
 * 把 DID 截短显示: "did:key:z6Mk...Ab1c" (首 12 + 尾 4)
 */
export function truncateDid(did: string, headLen = 12, tailLen = 4): string {
  if (did.length <= headLen + tailLen + 3) return did;
  return `${did.slice(0, headLen)}...${did.slice(-tailLen)}`;
}

/**
 * 把进度 (0-100 float) 格式化为百分比字符串: 73.4 → "73%"
 */
export function formatProgress(progress: number): string {
  return `${Math.round(Math.max(0, Math.min(100, progress)))}%`;
}

/**
 * 把字节数格式化为人类可读: 1234567 → "1.2 MB"
 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 把技能版本字符串标准化: "v1.2.3" or "1.2.3" → "v1.2.3"
 */
export function normalizeVersion(version: string): string {
  return version.startsWith("v") ? version : `v${version}`;
}

/**
 * 相对时间显示: "3 分钟前" / "2 小时前" / "3 天前"
 */
export function relativeTime(iso: string): string {
  try {
    const diffMs = Date.now() - new Date(iso).getTime();
    const minutes = Math.floor(diffMs / 60_000);
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    return `${days} 天前`;
  } catch {
    return iso;
  }
}

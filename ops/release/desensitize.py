#!/usr/bin/env python3
"""脱敏 script — 对 clean-room repo 内文件做 regex 替换 + 验收 + 报告.

用法:
    ./desensitize.py --root /tmp/sisoul-release-build/sisoul-cli \
                     --config <repo-root>/ops/release/desensitize-blacklist.yaml \
                     --report /tmp/sisoul-release-build/sisoul-cli/.desensitize-report.txt

退出码:
    0  - 一次扫描完成, 二次验收 0 命中
    1  - 二次验收仍有残留 (说明 pattern 之间互斥 / 未覆盖, 必须修黑名单或手工处理)
    2  - 配置文件 / root 不存在
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml  # PyYAML
except ImportError:  # 给出友好提示, 不 crash
    print("ERROR: PyYAML required. pip install pyyaml", file=sys.stderr)
    sys.exit(2)


SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target"}
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".bz2", ".so", ".dylib", ".dll", ".whl", ".woff", ".woff2", ".ttf",
    ".mp4", ".mov", ".wav",
}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB - 大于这个走二进制跳过


@dataclass
class Pattern:
    match: re.Pattern
    replace: str
    raw_match: str
    hits: int = 0


@dataclass
class FileReport:
    path: str
    hits: int = 0
    pattern_hits: dict[str, int] = field(default_factory=dict)


def load_patterns(cfg_path: Path) -> list[Pattern]:
    with cfg_path.open() as f:
        data = yaml.safe_load(f)
    out: list[Pattern] = []
    for p in data.get("patterns", []):
        out.append(
            Pattern(
                match=re.compile(p["match"]),
                replace=p["replace"],
                raw_match=p["match"],
            )
        )
    return out


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in SKIP_EXTS:
                continue
            try:
                if p.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            yield p


def desensitize_file(path: Path, patterns: list[Pattern]) -> FileReport:
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return FileReport(path=str(path))
    new = original
    rep = FileReport(path=str(path))
    for p in patterns:
        new2, n = p.match.subn(p.replace, new)
        if n > 0:
            rep.pattern_hits[p.raw_match] = n
            rep.hits += n
            p.hits += n
            new = new2
    if new != original:
        path.write_text(new, encoding="utf-8")
    return rep


def verify_clean(root: Path, patterns: list[Pattern]) -> dict[str, list[str]]:
    """二次扫: 对每个 pattern 在 root 下再 grep, 期望 0 命中."""
    residual: dict[str, list[str]] = {}
    for f in iter_files(root):
        try:
            content = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for p in patterns:
            if p.match.search(content):
                residual.setdefault(p.raw_match, []).append(str(f))
    return residual


def write_report(report_path: Path, file_reports: list[FileReport], patterns: list[Pattern], residual: dict[str, list[str]]) -> None:
    lines = []
    lines.append("# Sisoul Desensitization Report")
    lines.append("")
    lines.append("## Summary")
    total_files = sum(1 for r in file_reports if r.hits > 0)
    total_hits = sum(r.hits for r in file_reports)
    lines.append(f"- files modified: {total_files}")
    lines.append(f"- total replacements: {total_hits}")
    lines.append("")
    lines.append("## Per-pattern hits")
    for p in patterns:
        lines.append(f"- `{p.raw_match}` → `{p.replace}` : {p.hits}")
    lines.append("")
    lines.append("## Modified files")
    for r in sorted(file_reports, key=lambda x: -x.hits):
        if r.hits == 0:
            continue
        lines.append(f"- {r.path}: {r.hits} hits")
        for pat, n in r.pattern_hits.items():
            lines.append(f"  - `{pat}`: {n}")
    lines.append("")
    if residual:
        lines.append("## ⚠️ RESIDUAL (verification failed)")
        for pat, files in residual.items():
            lines.append(f"- `{pat}` still present in {len(files)} file(s)")
            for f in files[:10]:
                lines.append(f"  - {f}")
    else:
        lines.append("## Verification: PASS (0 residual hits)")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--report", required=False, type=Path)
    ap.add_argument("--dry-run", action="store_true", help="不写文件, 只报告会替换什么")
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"ERROR: --root {args.root} not a directory", file=sys.stderr)
        return 2
    if not args.config.is_file():
        print(f"ERROR: --config {args.config} not found", file=sys.stderr)
        return 2

    patterns = load_patterns(args.config)
    print(f"[desensitize] {len(patterns)} patterns loaded")
    print(f"[desensitize] root={args.root}")

    file_reports: list[FileReport] = []
    for f in iter_files(args.root):
        if args.dry_run:
            # 仅 count, 不写
            try:
                content = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rep = FileReport(path=str(f))
            for p in patterns:
                n = len(p.match.findall(content))
                if n > 0:
                    rep.pattern_hits[p.raw_match] = n
                    rep.hits += n
                    p.hits += n
            file_reports.append(rep)
        else:
            file_reports.append(desensitize_file(f, patterns))

    # 二次验收
    residual: dict[str, list[str]] = {}
    if not args.dry_run:
        residual = verify_clean(args.root, patterns)

    report_path = args.report or (args.root / ".desensitize-report.txt")
    write_report(report_path, file_reports, patterns, residual)

    total_files = sum(1 for r in file_reports if r.hits > 0)
    total_hits = sum(r.hits for r in file_reports)
    print(f"[desensitize] modified {total_files} files, {total_hits} replacements")
    print(f"[desensitize] report → {report_path}")

    if residual:
        print(f"[desensitize] ⚠️ verification FAILED — {len(residual)} pattern(s) still present", file=sys.stderr)
        return 1
    print("[desensitize] verification PASS (0 residual)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

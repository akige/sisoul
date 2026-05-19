"""7 天模拟 dogfooding (M1 替代 · 不起 docker).

模拟一个用户 7 天 daily 使用 sisoul 的全 flow:
    Day 1: init + login + remember 2 偏好
    Day 2-6: 每天 remember 1 偏好 + sync 1 次
    Day 7: export + restore 验证

不真起 daemon (避免端口冲突), 不动用户真 ~/.claude.
全 tmp dir 隔离.

跑: python qa/simulate_7_days.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


SISOUL_BIN = shutil.which("sisoul") or str(
    Path(__file__).parent.parent / ".venv" / "bin" / "sisoul"
)


def run(cmd, env, timeout=15):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    elapsed = (time.perf_counter() - t0) * 1000
    return r, elapsed


def fmt_ms(ms):
    return f"{ms:.0f}ms"


def main():
    # 全 tmp 隔离
    tmp_root = Path(tempfile.mkdtemp(prefix="sisoul-7day-"))
    home = tmp_root / "home"
    home.mkdir()
    vault = home / ".sisoul"
    proj = tmp_root / "proj"
    proj.mkdir()
    export_zip = tmp_root / "export.zip"
    restore_vault = tmp_root / "restored"

    env = {
        **os.environ,
        "HOME": str(home),
        "ALLOW_CHANGELOG_PENDING": "1",
    }

    print(f"=" * 60)
    print(f"sisoul 7 天 dogfooding 模拟 · tmp={tmp_root}")
    print(f"=" * 60)

    # 跟踪计数
    cmds = {"init": [], "login": [], "remember": [], "sync": [], "export": [], "restore": [], "status": []}
    failures = []

    def track(cmd_name, r, elapsed):
        cmds[cmd_name].append(elapsed)
        if r.returncode != 0:
            failures.append(f"{cmd_name}: rc={r.returncode}, stderr={r.stderr[:200]}")
            print(f"  [FAIL] {cmd_name} rc={r.returncode}: {r.stderr[:100]}")
        return r.returncode == 0

    # =========================================
    # Day 1: init + login + remember 2 偏好
    # =========================================
    print("\n--- Day 1: 装机 ---")
    r, e = run(
        [SISOUL_BIN, "init", "--vault-dir", str(vault), "--goals", "做 $10k MRR,学 Rust,写小说"],
        env,
    )
    track("init", r, e)
    print(f"  init [{fmt_ms(e)}] rc={r.returncode}")

    r, e = run([SISOUL_BIN, "login", "--provider", "ollama", "--skip-verify"], env)
    track("login", r, e)
    print(f"  login [{fmt_ms(e)}] rc={r.returncode}")

    for pref in ["前端用 Tailwind v4", "数据库选 SQLite"]:
        r, e = run([SISOUL_BIN, "remember", pref, "--vault-dir", str(vault)], env)
        track("remember", r, e)
    print(f"  remember 2 偏好 done")

    r, e = run([SISOUL_BIN, "status", "--vault-dir", str(vault)], env)
    track("status", r, e)
    print(f"  status [{fmt_ms(e)}]")

    # =========================================
    # Day 2-6: 每天 1 偏好 + 1 sync
    # =========================================
    daily_prefs = [
        "部署 CF Workers",
        "测试 pytest + cov",
        "linter 用 ruff",
        "API 用 FastAPI",
        "前端组件 SolidJS",
    ]
    for day_idx, pref in enumerate(daily_prefs, start=2):
        print(f"\n--- Day {day_idx} ---")
        r, e = run([SISOUL_BIN, "remember", pref, "--vault-dir", str(vault)], env)
        track("remember", r, e)
        print(f"  remember '{pref[:20]}...' [{fmt_ms(e)}]")

        r, e = run(
            [
                SISOUL_BIN,
                "sync",
                "--apply",
                "--project-root",
                str(proj),
                "--home",
                str(home),
                "--vault-root",
                str(vault),
            ],
            env,
            timeout=30,
        )
        track("sync", r, e)
        print(f"  sync 5 工具 [{fmt_ms(e)}] rc={r.returncode}")

    # =========================================
    # Day 7: export + restore 验证
    # =========================================
    print("\n--- Day 7: export + restore ---")
    r, e = run(
        [SISOUL_BIN, "export", "--output", str(export_zip), "--vault-dir", str(vault)],
        env,
    )
    track("export", r, e)
    print(f"  export [{fmt_ms(e)}] -> {export_zip.stat().st_size if export_zip.exists() else 0} bytes")

    r, e = run(
        [
            SISOUL_BIN,
            "restore",
            "--from-zip",
            str(export_zip),
            "--vault-dir",
            str(restore_vault),
            "--force",
        ],
        env,
    )
    track("restore", r, e)
    print(f"  restore [{fmt_ms(e)}] rc={r.returncode}")

    # =========================================
    # 最终验证
    # =========================================
    print("\n--- 最终验证 ---")
    # 原 vault 状态
    orig_prefs = list((vault / "preferences").glob("*.md"))
    orig_goals = list((vault / "goals").glob("goal-*.md"))
    # 重建 vault 状态
    restored_prefs = list((restore_vault / "preferences").glob("*.md"))
    restored_goals = list((restore_vault / "goals").glob("goal-*.md"))

    # vault 大小
    def dir_size(p):
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    orig_size = dir_size(vault)
    restored_size = dir_size(restore_vault)

    print(f"  orig vault: {len(orig_prefs)} prefs / {len(orig_goals)} goals / {orig_size} bytes")
    print(f"  restored:   {len(restored_prefs)} prefs / {len(restored_goals)} goals / {restored_size} bytes")

    # 5 工具入口
    entries = {
        "claude_code": home / ".claude" / "CLAUDE.md",
        "codex": home / ".codex" / "AGENTS.md",
        "cursor": proj / ".cursorrules",
        "aider": proj / ".aider.conf.yml",
        "opencode": proj / ".opencode" / "config.md",
    }
    for name, p in entries.items():
        exists = p.exists()
        has_marker = exists and "sisoul-managed" in p.read_text(encoding="utf-8", errors="ignore")
        print(f"  entry {name}: exists={exists} sisoul-marker={has_marker}")

    # =========================================
    # 统计
    # =========================================
    print(f"\n{'=' * 60}")
    print("模拟统计")
    print(f"{'=' * 60}")
    total_ops = sum(len(v) for v in cmds.values())
    print(f"总操作数: {total_ops}")
    for cmd_name, times in cmds.items():
        if times:
            avg = sum(times) / len(times)
            print(f"  {cmd_name:10s} 次数={len(times):2d}  avg={avg:6.1f}ms  max={max(times):6.1f}ms")
    print(f"\n失败操作: {len(failures)}")
    for f in failures:
        print(f"  - {f}")

    print(f"\n最终 vault 大小: {orig_size} bytes ({orig_size/1024:.1f} KB)")
    print(f"导出 ZIP 大小: {export_zip.stat().st_size if export_zip.exists() else 0} bytes")
    print(f"恢复后 vault: {restored_size} bytes (一致性 {'OK' if abs(orig_size - restored_size) < 1024 else 'DIFF'})")

    print(f"\n模拟 {'PASS' if not failures else 'FAIL'}: {len(failures)} 失败")

    # 验证业务断言
    assert not failures, f"7 天模拟出现失败: {failures}"
    assert len(restored_prefs) == len(orig_prefs), "preferences count mismatch"
    assert len(restored_goals) == len(orig_goals) == 3, "goals count mismatch"
    assert all(e.exists() for e in entries.values()), "some sync entry missing"

    print("\n[OK] 7 天模拟 dogfooding 全 PASS")

    # cleanup
    shutil.rmtree(tmp_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""sisoul goal · scheduler (Phase 2 P2-3).

daemon 启动时 spawn asyncio task:
- 每小时扫 vault/goals/ frontmatter `next_review_at`
- 到期 goal → 调 notify(channel=...) (默认 daemon-log, optional macos-notify osascript)

公共函数:
- scan_upcoming(vault, within_hours) → list[GoalUpcoming]
- snooze_goal(vault, goal_id, hours) → dict (改 frontmatter next_review_at)
- notify(goal, channel="daemon-log") → None
- register_scheduler_on_app(app) → daemon 启动时挂 startup/shutdown event
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sisoul.vault.frontmatter import dump_frontmatter, load_frontmatter
from sisoul.vault.storage import DEFAULT_VAULT_DIR, VaultPaths

logger = logging.getLogger("sisoul.goal.scheduler")

DEFAULT_SCAN_INTERVAL_S = 3600  # 1h


@dataclass(frozen=True)
class GoalUpcoming:
    """单条到期 goal 信息."""

    id: str
    path: Path
    title: str
    next_review_at: str  # ISO8601 (UTC) 字符串原样
    seconds_until: float  # 负数 = 已过期
    status: str = "active"
    frontmatter: dict[str, Any] = field(default_factory=dict)


def _resolve_vault_root(override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("SISOUL_VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_VAULT_DIR


def _parse_iso(value: Any) -> datetime | None:
    """frontmatter next_review_at 解析. 容错 ISO8601 + 'Z' 后缀.

    返回 aware UTC datetime; 失败 → None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # frontmatter 库可能直接返回 datetime
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        # 兼容 'Z' 后缀
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _now_utc(now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _read_goal_md(path: Path) -> tuple[dict[str, Any], str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        meta, body = load_frontmatter(raw)
        return meta, body
    except Exception:
        return {}, raw


def scan_upcoming(
    vault: str | Path | None = None,
    within_hours: float = 24,
    *,
    now: datetime | None = None,
    include_overdue: bool = True,
) -> list[GoalUpcoming]:
    """扫 vault/goals/ frontmatter next_review_at, 返回未来 within_hours 内到期 + 已 overdue."""
    root = _resolve_vault_root(vault)
    goals_dir = VaultPaths(root).goals_dir
    if not goals_dir.exists():
        return []

    cutoff_now = _now_utc(now)
    horizon = cutoff_now + timedelta(hours=within_hours)
    results: list[GoalUpcoming] = []

    for p in sorted(goals_dir.glob("*.md")):
        loaded = _read_goal_md(p)
        if loaded is None:
            continue
        meta, _body = loaded
        # status=done / paused 跳
        status = str(meta.get("status") or "active")
        if status in ("done", "paused", "snoozed-skip"):
            continue
        next_review = _parse_iso(meta.get("next_review_at"))
        if next_review is None:
            continue
        delta = (next_review - cutoff_now).total_seconds()
        if delta > within_hours * 3600:
            continue  # 还远, 跳
        if delta < 0 and not include_overdue:
            continue
        results.append(
            GoalUpcoming(
                id=p.stem,
                path=p,
                title=str(meta.get("title") or p.stem),
                next_review_at=meta.get("next_review_at") if isinstance(meta.get("next_review_at"), str) else next_review.isoformat(),
                seconds_until=delta,
                status=status,
                frontmatter=meta,
            )
        )
    # 排序: 最先到期的在前 (overdue 最久的最前)
    results.sort(key=lambda g: g.seconds_until)
    _ = horizon  # silence linter if unused
    return results


def snooze_goal(
    goal_id: str,
    hours: float,
    vault: str | Path | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把 goal 的 next_review_at 推后 hours 小时. 修改 frontmatter 文件.

    Returns:
        {"id", "old_next_review_at", "new_next_review_at", "path"}

    Raises:
        FileNotFoundError: goal 不存在
    """
    root = _resolve_vault_root(vault)
    p = VaultPaths(root).goals_dir / f"{goal_id}.md"
    if not p.exists():
        raise FileNotFoundError(f"goal not found: {goal_id}")
    raw = p.read_text(encoding="utf-8")
    meta, body = load_frontmatter(raw)
    old = meta.get("next_review_at")
    base = _parse_iso(old) or _now_utc(now)
    new_dt = base + timedelta(hours=hours)
    # 若 base 已经在过去, 以 now 为基准
    cutoff_now = _now_utc(now)
    if new_dt < cutoff_now:
        new_dt = cutoff_now + timedelta(hours=hours)
    new_iso = new_dt.replace(microsecond=0).isoformat()
    meta["next_review_at"] = new_iso
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return {
        "id": goal_id,
        "old_next_review_at": old if isinstance(old, str) else (str(old) if old else None),
        "new_next_review_at": new_iso,
        "path": str(p),
    }


def notify(goal: GoalUpcoming, channel: str = "daemon-log") -> dict[str, Any]:
    """通知一个到期 goal. channel: daemon-log (默认 logger) / macos-notify (osascript).

    macos-notify 在非 mac 或 osascript 缺失时 fallback daemon-log.
    Returns dict 记录实际走的 channel + 是否成功 (调试 / test 用).
    """
    msg = f"[sisoul goal] '{goal.title}' due (s={int(goal.seconds_until)}, id={goal.id})"
    if channel == "macos-notify":
        osascript = shutil.which("osascript")
        if sys.platform == "darwin" and osascript:
            try:
                script = (
                    f'display notification "{goal.title}" with title "sisoul goal: {goal.id}"'
                )
                subprocess.run(
                    [osascript, "-e", script],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
                return {"channel": "macos-notify", "ok": True}
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning("macos-notify failed, fallback log: %s", e)
                # fallthrough → daemon-log
        else:
            logger.info("macos-notify unavailable, fallback daemon-log")
    # daemon-log (默认)
    logger.info(msg)
    return {"channel": "daemon-log", "ok": True}


class GoalScheduler:
    """asyncio 后台 task: 周期扫 + notify.

    用法:
        sched = GoalScheduler(vault=root, interval_s=3600)
        await sched.start()
        # ... daemon 运行
        await sched.stop()
    """

    def __init__(
        self,
        vault: str | Path | None = None,
        interval_s: float = DEFAULT_SCAN_INTERVAL_S,
        within_hours: float = 1.0,
        channel: str = "daemon-log",
        notify_fn: Callable[[GoalUpcoming, str], Any] | None = None,
    ) -> None:
        self.vault = vault
        self.interval_s = interval_s
        self.within_hours = within_hours
        self.channel = channel
        self._notify_fn = notify_fn or notify
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self.tick_count = 0
        self.notified_ids: set[str] = set()  # 同一 goal 一个 review cycle 只通知一次

    async def _tick(self) -> int:
        """跑一次扫 + notify. 返回本次 notify 条数."""
        self.tick_count += 1
        try:
            upcoming = scan_upcoming(self.vault, within_hours=self.within_hours)
        except Exception as e:
            logger.exception("scan_upcoming failed: %s", e)
            return 0
        n = 0
        for g in upcoming:
            # 同 cycle 同 next_review_at 仅通知一次
            key = f"{g.id}@{g.next_review_at}"
            if key in self.notified_ids:
                continue
            try:
                self._notify_fn(g, self.channel)
                self.notified_ids.add(key)
                n += 1
            except Exception as e:
                logger.warning("notify failed for %s: %s", g.id, e)
        return n

    async def _run(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as e:
                logger.exception("scheduler tick error: %s", e)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="sisoul-goal-scheduler")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = None
        self._stop_event = None


# 全局 scheduler 单例 (daemon startup 时启动)
_GLOBAL_SCHEDULER: GoalScheduler | None = None


def register_scheduler_on_app(app, *, vault: str | Path | None = None) -> None:
    """挂在 FastAPI app startup/shutdown event 上.

    禁用方法: env SISOUL_GOAL_SCHEDULER=0
    """
    if os.environ.get("SISOUL_GOAL_SCHEDULER") == "0":
        logger.info("SISOUL_GOAL_SCHEDULER=0, scheduler disabled")
        return

    interval = float(os.environ.get("SISOUL_GOAL_SCHEDULER_INTERVAL_S", DEFAULT_SCAN_INTERVAL_S))
    within = float(os.environ.get("SISOUL_GOAL_SCHEDULER_WITHIN_H", 1.0))
    channel = os.environ.get("SISOUL_GOAL_SCHEDULER_CHANNEL", "daemon-log")

    @app.on_event("startup")
    async def _start_goal_sched() -> None:
        global _GLOBAL_SCHEDULER
        _GLOBAL_SCHEDULER = GoalScheduler(
            vault=vault, interval_s=interval, within_hours=within, channel=channel,
        )
        await _GLOBAL_SCHEDULER.start()
        logger.info("sisoul goal scheduler started (interval=%ss, within=%sh)", interval, within)

    @app.on_event("shutdown")
    async def _stop_goal_sched() -> None:
        global _GLOBAL_SCHEDULER
        if _GLOBAL_SCHEDULER is not None:
            await _GLOBAL_SCHEDULER.stop()
            _GLOBAL_SCHEDULER = None
            logger.info("sisoul goal scheduler stopped")

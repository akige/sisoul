"""sisoul goal 模块 (Phase 2 P2-3).

Goal-mode v1.1: daemon 后台 cron + reminder.

公共 API:
- scheduler.GoalScheduler 类 (asyncio task 每小时扫 vault/goals/ frontmatter `next_review_at`)
- scheduler.scan_upcoming(vault, within_hours) → list[GoalUpcoming]
- scheduler.snooze_goal(vault, goal_id, hours) → dict
- scheduler.notify(goal, channel) → None
"""

from __future__ import annotations

from sisoul.goal.scheduler import (
    DEFAULT_SCAN_INTERVAL_S,
    GoalScheduler,
    GoalUpcoming,
    notify,
    register_scheduler_on_app,
    scan_upcoming,
    snooze_goal,
)

__all__ = [
    "DEFAULT_SCAN_INTERVAL_S",
    "GoalScheduler",
    "GoalUpcoming",
    "notify",
    "scan_upcoming",
    "snooze_goal",
    "register_scheduler_on_app",
]

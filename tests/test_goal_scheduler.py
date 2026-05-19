"""tests · goal.scheduler (Phase 2 P2-3).

覆盖:
- scan_upcoming: frontmatter next_review_at 不同状态
- snooze_goal: 改 frontmatter + 404
- notify: daemon-log channel + macos-notify mock
- GoalScheduler asyncio tick (frozen time)
- daemon endpoint upcoming + snooze
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.goal import create_router as create_goal_router
from sisoul.goal.scheduler import (
    GoalScheduler,
    GoalUpcoming,
    notify,
    scan_upcoming,
    snooze_goal,
)
from sisoul.vault.frontmatter import dump_frontmatter, load_frontmatter
from sisoul.vault.storage import VaultPaths


# ────────────────────────────────────────────────────────────
# fixture: tmp vault with goals/
# ────────────────────────────────────────────────────────────


@pytest.fixture()
def now_fixed() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def vault(tmp_path: Path, now_fixed) -> Path:
    root = tmp_path / "vault"
    vp = VaultPaths(root)
    vp.ensure_dirs()
    # 已过期 1h
    (vp.goals_dir / "overdue.md").write_text(
        dump_frontmatter(
            {
                "title": "Overdue goal",
                "next_review_at": (now_fixed - timedelta(hours=1)).isoformat(),
                "status": "active",
            },
            "body",
        ),
        encoding="utf-8",
    )
    # 12h 后到期
    (vp.goals_dir / "soon.md").write_text(
        dump_frontmatter(
            {
                "title": "Soon goal",
                "next_review_at": (now_fixed + timedelta(hours=12)).isoformat(),
                "status": "active",
            },
            "body",
        ),
        encoding="utf-8",
    )
    # 100h 后 (within=24 时不应纳入)
    (vp.goals_dir / "far.md").write_text(
        dump_frontmatter(
            {
                "title": "Far goal",
                "next_review_at": (now_fixed + timedelta(hours=100)).isoformat(),
                "status": "active",
            },
            "body",
        ),
        encoding="utf-8",
    )
    # done 状态 (即使到期也不通知)
    (vp.goals_dir / "done.md").write_text(
        dump_frontmatter(
            {
                "title": "Done goal",
                "next_review_at": (now_fixed - timedelta(hours=2)).isoformat(),
                "status": "done",
            },
            "body",
        ),
        encoding="utf-8",
    )
    # 无 next_review_at field
    (vp.goals_dir / "no-review.md").write_text(
        dump_frontmatter({"title": "No review", "status": "active"}, "body"),
        encoding="utf-8",
    )
    return root


# ────────────────────────────────────────────────────────────
# 1. scan_upcoming
# ────────────────────────────────────────────────────────────


class TestScanUpcoming:
    def test_overdue_included(self, vault, now_fixed):
        items = scan_upcoming(vault, within_hours=24, now=now_fixed)
        ids = {g.id for g in items}
        assert "overdue" in ids

    def test_soon_included(self, vault, now_fixed):
        items = scan_upcoming(vault, within_hours=24, now=now_fixed)
        ids = {g.id for g in items}
        assert "soon" in ids

    def test_far_excluded(self, vault, now_fixed):
        items = scan_upcoming(vault, within_hours=24, now=now_fixed)
        ids = {g.id for g in items}
        assert "far" not in ids

    def test_done_excluded(self, vault, now_fixed):
        items = scan_upcoming(vault, within_hours=24, now=now_fixed)
        ids = {g.id for g in items}
        assert "done" not in ids

    def test_no_review_field_excluded(self, vault, now_fixed):
        items = scan_upcoming(vault, within_hours=24, now=now_fixed)
        ids = {g.id for g in items}
        assert "no-review" not in ids

    def test_overdue_first_in_sort(self, vault, now_fixed):
        items = scan_upcoming(vault, within_hours=24, now=now_fixed)
        assert items[0].id == "overdue"

    def test_empty_vault_no_goals_dir(self, tmp_path, now_fixed):
        items = scan_upcoming(tmp_path / "empty-vault", within_hours=24, now=now_fixed)
        assert items == []

    def test_include_overdue_false(self, vault, now_fixed):
        items = scan_upcoming(vault, within_hours=24, now=now_fixed, include_overdue=False)
        ids = {g.id for g in items}
        assert "overdue" not in ids
        assert "soon" in ids


# ────────────────────────────────────────────────────────────
# 2. snooze_goal
# ────────────────────────────────────────────────────────────


class TestSnoozeGoal:
    def test_snooze_pushes_future(self, vault, now_fixed):
        r = snooze_goal("soon", 48, vault=vault, now=now_fixed)
        assert r["id"] == "soon"
        # new 时间 > old 时间
        new_dt = datetime.fromisoformat(r["new_next_review_at"])
        assert new_dt > now_fixed

    def test_snooze_updates_file(self, vault, now_fixed):
        snooze_goal("soon", 48, vault=vault, now=now_fixed)
        p = VaultPaths(vault).goals_dir / "soon.md"
        meta, _ = load_frontmatter(p.read_text(encoding="utf-8"))
        new_dt_raw = meta["next_review_at"]
        new_dt = datetime.fromisoformat(new_dt_raw)
        if new_dt.tzinfo is None:
            new_dt = new_dt.replace(tzinfo=timezone.utc)
        assert new_dt > now_fixed + timedelta(hours=24)

    def test_snooze_overdue_uses_now_as_base(self, vault, now_fixed):
        """overdue.md 已 1h 过期, snooze 24h → 应基于 now 而非 old."""
        r = snooze_goal("overdue", 24, vault=vault, now=now_fixed)
        new_dt = datetime.fromisoformat(r["new_next_review_at"])
        # 应至少 23h 后
        assert new_dt >= now_fixed + timedelta(hours=23)

    def test_snooze_nonexistent_raises(self, vault, now_fixed):
        with pytest.raises(FileNotFoundError):
            snooze_goal("nonexistent", 24, vault=vault, now=now_fixed)


# ────────────────────────────────────────────────────────────
# 3. notify
# ────────────────────────────────────────────────────────────


class TestNotify:
    def _mk_goal(self) -> GoalUpcoming:
        return GoalUpcoming(
            id="g1",
            path=Path("/tmp/g1.md"),
            title="Test goal",
            next_review_at="2026-05-18T12:00:00+00:00",
            seconds_until=-3600,
        )

    def test_notify_daemon_log_default(self, caplog):
        with caplog.at_level(logging.INFO, logger="sisoul.goal.scheduler"):
            r = notify(self._mk_goal(), channel="daemon-log")
        assert r["channel"] == "daemon-log"
        assert r["ok"] is True

    def test_notify_macos_invokes_osascript(self):
        goal = self._mk_goal()
        with patch("sisoul.goal.scheduler.sys.platform", "darwin"), \
             patch("sisoul.goal.scheduler.shutil.which", return_value="/usr/bin/osascript"), \
             patch("sisoul.goal.scheduler.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            r = notify(goal, channel="macos-notify")
        assert r["channel"] == "macos-notify"
        assert mock_run.called

    def test_notify_macos_fallback_on_non_darwin(self):
        goal = self._mk_goal()
        with patch("sisoul.goal.scheduler.sys.platform", "linux"), \
             patch("sisoul.goal.scheduler.shutil.which", return_value=None):
            r = notify(goal, channel="macos-notify")
        # fallback log
        assert r["channel"] == "daemon-log"


# ────────────────────────────────────────────────────────────
# 4. GoalScheduler asyncio
# ────────────────────────────────────────────────────────────


class TestGoalScheduler:
    @pytest.mark.asyncio
    async def test_scheduler_tick_calls_notify(self, vault, now_fixed):
        """tick → notify_fn 被叫."""
        calls: list[GoalUpcoming] = []

        def fake_notify(g: GoalUpcoming, ch: str) -> None:
            calls.append(g)

        sched = GoalScheduler(
            vault=vault, interval_s=0.1, within_hours=24, notify_fn=fake_notify,
        )
        # 直接同步 patch scan_upcoming 用 frozen now
        with patch(
            "sisoul.goal.scheduler.scan_upcoming",
            side_effect=lambda v, within_hours, **kw: scan_upcoming(v, within_hours=within_hours, now=now_fixed),
        ):
            n = await sched._tick()
        assert n >= 2  # overdue + soon
        assert {g.id for g in calls} >= {"overdue", "soon"}

    @pytest.mark.asyncio
    async def test_scheduler_does_not_double_notify_same_cycle(self, vault, now_fixed):
        calls: list[GoalUpcoming] = []
        sched = GoalScheduler(
            vault=vault, interval_s=0.1, notify_fn=lambda g, c: calls.append(g),
        )
        with patch(
            "sisoul.goal.scheduler.scan_upcoming",
            side_effect=lambda v, within_hours, **kw: scan_upcoming(v, within_hours=within_hours, now=now_fixed),
        ):
            await sched._tick()
            n1 = len(calls)
            await sched._tick()
            n2 = len(calls)
        # 同 next_review_at → 第二次不重复 notify
        assert n2 == n1

    @pytest.mark.asyncio
    async def test_scheduler_start_stop(self, vault, now_fixed):
        sched = GoalScheduler(vault=vault, interval_s=10, notify_fn=lambda g, c: None)
        await sched.start()
        assert sched._task is not None
        await sched.stop()
        assert sched._task is None


# ────────────────────────────────────────────────────────────
# 5. Daemon endpoint
# ────────────────────────────────────────────────────────────


@pytest.fixture()
def goal_client(vault, monkeypatch) -> TestClient:
    monkeypatch.setenv("SISOUL_VAULT_ROOT", str(vault))
    app = FastAPI()
    app.include_router(create_goal_router())
    return TestClient(app)


class TestGoalEndpoint:
    def test_upcoming_basic(self, goal_client):
        r = goal_client.get("/sisoul/goal/upcoming", params={"within_hours": 24})
        assert r.status_code == 200
        data = r.json()
        ids = {it["id"] for it in data["items"]}
        # 不 mock now, 实际 now 跟 fixture 2026-05-18 可能差距大,
        # 但 overdue.md 是 now_fixed-1h, 真 now 是未来 → 仍 overdue
        assert "overdue" in ids

    def test_snooze_success(self, goal_client):
        r = goal_client.post("/sisoul/goal/soon/snooze", json={"hours": 24})
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "soon"
        assert data["new_next_review_at"]

    def test_snooze_not_found(self, goal_client):
        r = goal_client.post("/sisoul/goal/nope/snooze", json={"hours": 24})
        assert r.status_code == 404

    def test_snooze_bad_id_traversal(self, goal_client):
        r = goal_client.post("/sisoul/goal/..%2Fetc/snooze", json={"hours": 24})
        # path traversal blocked
        assert r.status_code in (400, 404)

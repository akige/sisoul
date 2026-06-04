"""Self-Improvement Logging (§59 §4.6, v2.0 ship T+11m).

每日 daemon cron 写 vault/growth/<date>.json, PWA dashboard 显 7-day curve.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional
import json


@dataclass
class DailyGrowthSnapshot:
    """One day's growth metrics."""

    date: str  # YYYY-MM-DD
    cases_added: int = 0
    skills_installed: int = 0
    skills_used: int = 0
    chats_sent: int = 0
    borrowed_llm_calls: int = 0
    new_friends: int = 0
    reputation_topics: dict[str, float] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


@dataclass
class GrowthTrend:
    """N-day trend from N daily snapshots."""

    window_days: int
    snapshots: list[DailyGrowthSnapshot]

    def total_cases(self) -> int:
        return sum(s.cases_added for s in self.snapshots)

    def total_skills_used(self) -> int:
        return sum(s.skills_used for s in self.snapshots)

    def avg_chats_per_day(self) -> float:
        if not self.snapshots:
            return 0.0
        return sum(s.chats_sent for s in self.snapshots) / len(self.snapshots)


class GrowthLogger:
    """Persists daily growth snapshots to vault/growth/."""

    def __init__(self, vault_dir: Path):
        self.vault_dir = Path(vault_dir).expanduser()
        self.growth_dir = self.vault_dir / "growth"
        self.growth_dir.mkdir(parents=True, exist_ok=True)

    def write(self, snapshot: DailyGrowthSnapshot) -> Path:
        path = self.growth_dir / f"{snapshot.date}.json"
        path.write_text(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2))
        return path

    def read(self, date_str: str) -> Optional[DailyGrowthSnapshot]:
        path = self.growth_dir / f"{date_str}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return DailyGrowthSnapshot(**data)

    def last_n_days(self, n: int = 7) -> GrowthTrend:
        """Return last N days of snapshots (oldest first)."""
        snapshots = []
        for path in sorted(self.growth_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                snapshots.append(DailyGrowthSnapshot(**data))
            except Exception:
                continue
        snapshots = snapshots[-n:]
        return GrowthTrend(window_days=n, snapshots=snapshots)


__all__ = ["DailyGrowthSnapshot", "GrowthTrend", "GrowthLogger"]

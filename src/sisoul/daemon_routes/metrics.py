"""sisoul daemon /sisoul/metrics — Prometheus format export.

For monitoring stack (Grafana / Prometheus / alertmanager) to scrape daemon health.
Endpoint returns text/plain Prometheus exposition format.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from sisoul import __version__, __phase__


metrics_router = APIRouter(tags=["metrics"])


@metrics_router.get("/sisoul/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    """Prometheus exposition format."""
    vault = Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    skills_dir = Path(os.environ.get("SISOUL_SKILLS_DIR", "~/.sisoul/skills")).expanduser()

    cases = 0
    skills = 0
    friends = 0
    petnames = 0
    lessons = 0
    growth_days = 0

    if vault.exists():
        cases_dir = vault / "cases"
        if cases_dir.exists():
            cases = len(list(cases_dir.glob("*.json")))
        lessons_dir = vault / "lessons"
        if lessons_dir.exists():
            lessons = len(list(lessons_dir.glob("*.json")))
        growth_dir = vault / "growth"
        if growth_dir.exists():
            growth_days = len(list(growth_dir.glob("*.json")))
        friends_dir = vault / "friends"
        if friends_dir.exists():
            friends = len(list(friends_dir.glob("*.json")))
        pet_file = vault / "petnames.json"
        if pet_file.exists():
            try:
                petnames = len(json.loads(pet_file.read_text()))
            except Exception:
                pass

    if skills_dir.exists():
        skills = len(
            [d for d in skills_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
        )

    # Prometheus exposition format
    lines = [
        "# HELP sisoul_info sisoul daemon info (version + phase).",
        "# TYPE sisoul_info gauge",
        f'sisoul_info{{version="{__version__}",phase="{__phase__}"}} 1',
        "",
        "# HELP sisoul_cases_total Number of cases in local vault.",
        "# TYPE sisoul_cases_total gauge",
        f"sisoul_cases_total {cases}",
        "",
        "# HELP sisoul_skills_installed Number of installed skills.",
        "# TYPE sisoul_skills_installed gauge",
        f"sisoul_skills_installed {skills}",
        "",
        "# HELP sisoul_friends_total Number of friend records.",
        "# TYPE sisoul_friends_total gauge",
        f"sisoul_friends_total {friends}",
        "",
        "# HELP sisoul_petnames_total Number of local petname mappings.",
        "# TYPE sisoul_petnames_total gauge",
        f"sisoul_petnames_total {petnames}",
        "",
        "# HELP sisoul_lessons_total Number of distilled lessons.",
        "# TYPE sisoul_lessons_total gauge",
        f"sisoul_lessons_total {lessons}",
        "",
        "# HELP sisoul_growth_snapshot_days Number of daily growth snapshots.",
        "# TYPE sisoul_growth_snapshot_days gauge",
        f"sisoul_growth_snapshot_days {growth_days}",
        "",
    ]
    return "\n".join(lines)

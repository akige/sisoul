"""sisoul founder-agent — the protocol's first user.

Loads `vault/founder/` (persona + cases + lessons + eval_prompts), routes chat
to any of 9 LLM adapters via the team's newapi free-pool gateway, runs RSI on
its own system_prompt daily.

See docs/FOUNDER-AGENT.md for the full spec.
"""
from sisoul.founder.agent import FounderAgent, FounderConfig
from sisoul.founder.vault import FounderVault, CaseEntry, LessonEntry

__all__ = ["FounderAgent", "FounderConfig", "FounderVault", "CaseEntry", "LessonEntry"]

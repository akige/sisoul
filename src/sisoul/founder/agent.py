"""FounderAgent — orchestrate vault + LLM adapter to produce founder-style answers."""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sisoul.founder.vault import FounderVault, founder_dir

log = logging.getLogger(__name__)

# Module-level so it survives the per-request FounderAgent instances created by
# daemon_routes/founder.py — /v1/founder/status can surface the most recent
# LLM failure even though each request builds a fresh agent.
_LAST_LLM_ERROR: Optional[dict] = None


def _record_llm_error(err: str) -> None:
    global _LAST_LLM_ERROR
    _LAST_LLM_ERROR = {
        "error": err,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def get_last_llm_error() -> Optional[dict]:
    """Most recent LLM-call failure ({"error", "timestamp"}) or None."""
    return _LAST_LLM_ERROR


@dataclass
class FounderConfig:
    provider: str = "newapi-freepool"  # or anthropic/openai/gemini etc.
    max_recall: int = 3
    max_response_tokens: int = 1024
    rsi_enabled: bool = False  # dry_run safety default
    chat_log_dir: Path = field(default_factory=lambda: founder_dir() / "chat")


@dataclass
class ChatTurn:
    role: str  # "user" / "assistant"
    content: str
    timestamp: str
    provider_used: Optional[str] = None
    cases_recalled: list[str] = field(default_factory=list)


class FounderAgent:
    """Sisoul founder-agent — composes prompts and routes to LLM.

    Heavy LLM client construction is deferred until actually needed so the
    agent can be instantiated in tests without provider env vars set.
    """

    def __init__(
        self,
        config: Optional[FounderConfig] = None,
        vault: Optional[FounderVault] = None,
    ):
        self.config = config or FounderConfig()
        self.vault = vault or FounderVault()
        self._llm_client = None  # lazy init

    def _default_adapter(self):
        """Try to resolve a default LLM adapter from env. Returns None on failure."""
        provider = (self.config.provider or "").lower()
        try:
            if provider in ("newapi-freepool", "newapi", "free-pool"):
                from sisoul.llm.newapi import NewapiAdapter
                return NewapiAdapter()
            if provider == "anthropic":
                from sisoul.llm.anthropic import AnthropicAdapter
                return AnthropicAdapter()
            if provider == "openai":
                from sisoul.llm.openai import OpenAIAdapter
                return OpenAIAdapter()
            if provider == "openrouter":
                from sisoul.llm.openrouter import OpenRouterAdapter
                return OpenRouterAdapter()
            if provider == "gemini":
                from sisoul.llm.gemini import GeminiAdapter
                return GeminiAdapter()
        except Exception as e:
            err = f"adapter init failed (provider={provider}): {type(e).__name__}: {e}"
            log.warning("founder-agent %s", err)
            _record_llm_error(err)
            return None
        return None

    # ── prompt assembly ──────────────────────────────────────────────────────

    def build_prompt(self, user_question: str) -> dict:
        """Return {"system": ..., "context_cases": [...], "user": user_question}."""
        system = self.vault.system_prompt or (
            "You are sisoul's founder-agent. Answer honestly; cite cases."
        )
        recalled = self.vault.recall(user_question, top_k=self.config.max_recall)
        context_blocks = []
        for case, score in recalled:
            context_blocks.append(
                f"--- recalled case {case.id} (relevance {score:.2f}) ---\n"
                f"Q: {case.question}\nA: {case.answer}"
            )
        return {
            "system": system,
            "context": "\n\n".join(context_blocks) if context_blocks else "",
            "user": user_question,
            "cases_recalled": [c.id for c, _ in recalled],
        }

    # ── chat (mock by default; real LLM call when adapter wired) ──────────────

    def chat(
        self,
        user_question: str,
        adapter=None,
        record: bool = True,
    ) -> dict:
        """Produce a founder-agent reply.

        If `adapter` is None and no env-configured adapter resolves, returns a
        retrieval-only response (the closest case's answer text) tagged
        ``[retrieval-only]``.

        Returns {"answer": str, "provider": str, "cases_recalled": [...], "mode": "llm"|"retrieval-only"}.
        """
        prompt = self.build_prompt(user_question)
        recalled_ids = prompt["cases_recalled"]
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        llm_error: Optional[str] = None

        # If caller didn't pass adapter, try to auto-resolve from config.provider
        if adapter is None:
            adapter = self._default_adapter()

        # Try real LLM
        if adapter is not None:
            try:
                full_user = (
                    (prompt["context"] + "\n\n" if prompt["context"] else "")
                    + "User: "
                    + user_question
                )
                messages = [
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": full_user},
                ]
                response_text = adapter.chat(messages)
                result = {
                    "answer": response_text,
                    "provider": getattr(adapter, "provider_name", self.config.provider),
                    "cases_recalled": recalled_ids,
                    "mode": "llm",
                    "timestamp": timestamp,
                    "llm_error": None,
                }
                if record:
                    self._record_turn(user_question, result)
                return result
            except Exception as e:
                # Fall through to retrieval, but keep the failure visible
                llm_error = f"{type(e).__name__}: {e}"
                log.warning(
                    "founder-agent LLM call failed (provider=%s), "
                    "falling back to retrieval-only: %s",
                    getattr(adapter, "provider_name", self.config.provider),
                    llm_error,
                )
                _record_llm_error(llm_error)

        # Retrieval-only fallback
        recalled = self.vault.recall(user_question, top_k=1)
        if recalled:
            top, _ = recalled[0]
            answer = f"[retrieval-only · LLM unavailable] {top.answer}"
            cases_recalled = [top.id]
        else:
            answer = "[retrieval-only · LLM unavailable] No matching case in vault."
            cases_recalled = []

        result = {
            "answer": answer,
            "provider": "retrieval-only",
            "cases_recalled": cases_recalled,
            "mode": "retrieval-only",
            "timestamp": timestamp,
            "llm_error": llm_error,
        }
        if record:
            self._record_turn(user_question, result)
        return result

    def _record_turn(self, user_question: str, result: dict) -> None:
        try:
            self.config.chat_log_dir.mkdir(parents=True, exist_ok=True)
            log_file = self.config.chat_log_dir / "log.jsonl"
            with log_file.open("a") as fp:
                fp.write(
                    json.dumps(
                        {
                            "ts": result["timestamp"],
                            "user": user_question,
                            "answer": result["answer"],
                            "provider": result["provider"],
                            "cases_recalled": result["cases_recalled"],
                            "mode": result["mode"],
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass

    # ── status / introspection ──────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "vault_root": str(self.vault.root),
            "vault_size": self.vault.size(),
            "config": {
                "provider": self.config.provider,
                "max_recall": self.config.max_recall,
                "rsi_enabled": self.config.rsi_enabled,
            },
            "last_llm_error": get_last_llm_error(),
        }


__all__ = ["FounderAgent", "FounderConfig", "ChatTurn", "get_last_llm_error"]

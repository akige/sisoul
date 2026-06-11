"""产品引导补强测试 (2026-06-11):
- lend watch: SSE 事件解析 + lend.request 格式化 (lender CLI 实时通知)
- borrow run: 借成功后打印"下一步怎么用 LLM"引导
"""

from __future__ import annotations

from typer.testing import CliRunner

from sisoul.cli_commands.lend import _parse_sse_event, _format_lend_request_event


class TestSSEParse:
    def test_parse_lend_request_event(self) -> None:
        block = 'event: lend.request\ndata: {"lend_request_id": "req_abc123def456", "borrower_did": "did:key:z6Mkxxx", "amount": 1000, "model": "claude-opus-4-7", "mode": "per-request", "resource_type": "llm_quota"}\n'
        etype, data = _parse_sse_event(block)
        assert etype == "lend.request"
        assert data["amount"] == 1000
        assert data["model"] == "claude-opus-4-7"

    def test_parse_heartbeat(self) -> None:
        etype, data = _parse_sse_event("event: heartbeat\ndata: {}\n")
        assert etype == "heartbeat"
        assert data == {}

    def test_parse_no_event_line_defaults_message(self) -> None:
        etype, data = _parse_sse_event('data: {"x": 1}\n')
        assert etype == "message"
        assert data == {"x": 1}

    def test_parse_bad_json_returns_empty(self) -> None:
        etype, data = _parse_sse_event("event: lend.request\ndata: not-json\n")
        assert etype == "lend.request"
        assert data == {}

    def test_format_lend_request_human_readable(self) -> None:
        line = _format_lend_request_event({
            "lend_request_id": "req_abc123def456ghi",
            "borrower_did": "did:key:z6MkborrowerlongdidstringXXXX",
            "amount": 500, "model": "gpt-4o", "mode": "per-request",
            "resource_type": "llm_quota",
        })
        assert "有人来借" in line
        assert "req_abc123de" in line  # 截断到 12 字符
        assert "gpt-4o" in line
        assert "500" in line

    def test_format_emergency_flagged(self) -> None:
        line = _format_lend_request_event({
            "lend_request_id": "r", "borrower_did": "d", "amount": 1,
            "model": "m", "mode": "emergency-only", "emergency_flag": True,
            "resource_type": "llm_quota",
        })
        assert "EMERGENCY" in line


class TestBorrowRunGuidance:
    def test_completed_borrow_prints_llm_usage_guidance(self) -> None:
        """borrow run 借成功 → 打印持续用 LLM 的 proxy / async 引导。"""
        from sisoul.cli_commands import borrow as borrow_cli

        runner = CliRunner()
        # force_mode=strong-tie-auto + 无 mock proxy → stub-passthrough, status=completed
        result = runner.invoke(
            borrow_cli.borrow_app,
            ["run", "bob.eth", "llm_quota", "1000", "-m", "claude-opus-4-7",
             "--force-mode", "strong-tie-auto", "--no-onchain"],
        )
        assert result.exit_code == 0, result.output
        assert "借到了" in result.output
        assert "sisoul borrow proxy bob.eth" in result.output
        assert "async-submit" in result.output

    def test_denied_borrow_no_guidance(self) -> None:
        """借失败 (emergency-only 无 flag) → 不打印用 LLM 引导。"""
        from sisoul.cli_commands import borrow as borrow_cli

        runner = CliRunner()
        result = runner.invoke(
            borrow_cli.borrow_app,
            ["run", "bob.eth", "llm_quota", "100", "-m", "x",
             "--force-mode", "emergency-only", "--no-onchain"],
        )
        assert result.exit_code == 0, result.output
        assert "借到了" not in result.output

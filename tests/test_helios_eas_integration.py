"""sisoul · Helios + EAS / Arweave 集成测试 (Wave A agent-1 · #4 + #5 + #7).

验:
1. eas._verify_testnet_rpc 在 SISOUL_HELIOS_DISABLE=1 时走 legacy 公共 RPC (向后兼容)
2. eas._verify_testnet_rpc 在 helios singleton 已 in_sync 时走 trustless path
3. arweave.ensure_eth_payment_via_helios 接 helios singleton, receipt 校验逻辑正确
4. 不破坏现有 EAS / Arweave behaviour
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from sisoul.onchain import arweave as arweave_mod
from sisoul.onchain import eas as eas_mod
from sisoul.rpc.helios_client import ChainStatus, set_default_client


# ─────────────────────────────────────────────────────────────────────────────
# fixture: 重置 helios singleton (防 tests 间互相污染)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_helios_singleton() -> Any:
    set_default_client(None)
    yield
    set_default_client(None)


# ─────────────────────────────────────────────────────────────────────────────
# Fake helios client (mock 出 in_sync + call_sync)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeHelios:
    """模拟 HeliosClient (只暴露 status/call_sync 接口给 eas/arweave 用)."""

    def __init__(
        self,
        chain: str,
        chain_id_hex: str = "0x14a34",  # 84532 = base-sepolia
        in_sync: bool = True,
        raise_on_call: Exception | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        self.chain = chain
        self.chain_id_hex = chain_id_hex
        self._in_sync = in_sync
        self._raise = raise_on_call
        self._receipt = receipt
        self.call_log: list[tuple[str, str, list]] = []

    def status(self, chain: str | None = None) -> Any:
        if chain is None:
            return {self.chain: ChainStatus(
                chain=self.chain, mode="helios", in_sync=self._in_sync, head=1234,
            )}
        if chain != self.chain:
            return ChainStatus(chain=chain, mode="stopped")
        return ChainStatus(
            chain=self.chain, mode="helios", in_sync=self._in_sync, head=1234,
            rpc_port=18545, rpc_url="http://127.0.0.1:18545",
        )

    def call_sync(self, chain: str, method: str, params: list, *, timeout: float = 15.0) -> Any:
        self.call_log.append((chain, method, list(params)))
        if self._raise:
            raise self._raise
        if method == "eth_chainId":
            return self.chain_id_hex
        if method == "eth_getTransactionReceipt":
            return self._receipt
        raise NotImplementedError(method)


# ─────────────────────────────────────────────────────────────────────────────
# EAS 集成测试
# ─────────────────────────────────────────────────────────────────────────────


def test_eas_helios_disabled_env_falls_back_to_public_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SISOUL_HELIOS_DISABLE=1 → 跳过 helios, 走 httpx 公共 RPC (legacy behaviour)."""
    monkeypatch.setenv("SISOUL_HELIOS_DISABLE", "1")
    fake = _FakeHelios("base-sepolia", chain_id_hex="0x14a34")
    set_default_client(fake)  # type: ignore[arg-type]

    import httpx

    def fake_post(url: str, json: dict, timeout: float) -> Any:
        r = mock.Mock()
        r.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x14a34"}
        r.raise_for_status = lambda: None
        return r

    with mock.patch.object(httpx, "post", side_effect=fake_post):
        eas_mod._verify_testnet_rpc("https://sepolia.base.org", "base-sepolia")

    # helios 应**没**被调用 (env disabled)
    assert fake.call_log == []


def test_eas_helios_native_chain_uses_trustless_path() -> None:
    """base-sepolia + helios in_sync → 走 helios.call_sync, 不打公共 RPC."""
    fake = _FakeHelios("base-sepolia", chain_id_hex="0x14a34", in_sync=True)
    set_default_client(fake)  # type: ignore[arg-type]
    eas_mod._verify_testnet_rpc("https://sepolia.base.org", "base-sepolia")
    assert len(fake.call_log) == 1
    assert fake.call_log[0] == ("base-sepolia", "eth_chainId", [])


def test_eas_helios_chain_not_in_native_table_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """optimism-sepolia 不在 helios 原生表 → 自动公共 RPC."""
    fake = _FakeHelios("op-mainnet")
    set_default_client(fake)  # type: ignore[arg-type]
    import httpx
    posted: list[str] = []

    def fake_post(url: str, json: dict, timeout: float) -> Any:
        posted.append(url)
        r = mock.Mock()
        r.json.return_value = {"result": "0xaa37dc"}  # 11155420 = op-sepolia
        r.raise_for_status = lambda: None
        return r

    with mock.patch.object(httpx, "post", side_effect=fake_post):
        eas_mod._verify_testnet_rpc("https://sepolia.optimism.io", "optimism-sepolia")
    assert len(posted) == 1
    assert "optimism.io" in posted[0]
    assert fake.call_log == []


def test_eas_helios_not_in_sync_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """helios singleton 在但未 in_sync → fallback 公共 RPC."""
    fake = _FakeHelios("base-sepolia", in_sync=False)
    set_default_client(fake)  # type: ignore[arg-type]
    import httpx
    posted: list[str] = []

    def fake_post(url: str, json: dict, timeout: float) -> Any:
        posted.append(url)
        r = mock.Mock()
        r.json.return_value = {"result": "0x14a34"}
        r.raise_for_status = lambda: None
        return r

    with mock.patch.object(httpx, "post", side_effect=fake_post):
        eas_mod._verify_testnet_rpc("https://sepolia.base.org", "base-sepolia")
    assert posted == ["https://sepolia.base.org"]
    assert fake.call_log == []


def test_eas_helios_call_failed_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """helios in_sync 但 call 抛 → fallback 公共 RPC + warn."""
    fake = _FakeHelios(
        "base-sepolia",
        in_sync=True,
        raise_on_call=RuntimeError("simulated helios HTTP down"),
    )
    set_default_client(fake)  # type: ignore[arg-type]
    import httpx
    posted: list[str] = []

    def fake_post(url: str, json: dict, timeout: float) -> Any:
        posted.append(url)
        r = mock.Mock()
        r.json.return_value = {"result": "0x14a34"}
        r.raise_for_status = lambda: None
        return r

    with mock.patch.object(httpx, "post", side_effect=fake_post):
        eas_mod._verify_testnet_rpc("https://sepolia.base.org", "base-sepolia")
    assert len(fake.call_log) == 1
    assert len(posted) == 1


def test_eas_helios_chain_id_mismatch_raises() -> None:
    """helios 返错 chain_id → EASError."""
    fake = _FakeHelios("base-sepolia", chain_id_hex="0x1")  # 1 != 84532
    set_default_client(fake)  # type: ignore[arg-type]
    with pytest.raises(eas_mod.EASError, match="chain_id 不匹配"):
        eas_mod._verify_testnet_rpc("https://sepolia.base.org", "base-sepolia")


def test_eas_mainnet_still_blocked() -> None:
    """mainnet 仍然 hard gate (即使 helios 接进来也不能上)."""
    fake = _FakeHelios("op-mainnet", in_sync=True)
    set_default_client(fake)  # type: ignore[arg-type]
    with pytest.raises(eas_mod.NetworkNotSupportedError):
        eas_mod._verify_testnet_rpc("https://mainnet.optimism.io", "optimism-mainnet")


# ─────────────────────────────────────────────────────────────────────────────
# Arweave hook 测试
# ─────────────────────────────────────────────────────────────────────────────


def test_arweave_eth_payment_no_helios_returns_false() -> None:
    """无 helios singleton → 返 False (不抛)."""
    set_default_client(None)
    assert arweave_mod.ensure_eth_payment_via_helios("base-sepolia", "0xdead") is False


def test_arweave_eth_payment_helios_not_synced_returns_false() -> None:
    fake = _FakeHelios("base-sepolia", in_sync=False)
    set_default_client(fake)  # type: ignore[arg-type]
    assert arweave_mod.ensure_eth_payment_via_helios("base-sepolia", "0xdead") is False


def test_arweave_eth_payment_receipt_none_returns_false() -> None:
    fake = _FakeHelios("base-sepolia", in_sync=True, receipt=None)
    set_default_client(fake)  # type: ignore[arg-type]
    assert arweave_mod.ensure_eth_payment_via_helios("base-sepolia", "0xdead") is False


def test_arweave_eth_payment_receipt_failed_status_returns_false() -> None:
    fake = _FakeHelios(
        "base-sepolia", in_sync=True,
        receipt={"status": "0x0", "to": "0xabc"},
    )
    set_default_client(fake)  # type: ignore[arg-type]
    assert arweave_mod.ensure_eth_payment_via_helios("base-sepolia", "0xdead") is False


def test_arweave_eth_payment_receipt_success_returns_true() -> None:
    fake = _FakeHelios(
        "base-sepolia", in_sync=True,
        receipt={"status": "0x1", "to": "0xRECIPIENT"},
    )
    set_default_client(fake)  # type: ignore[arg-type]
    assert arweave_mod.ensure_eth_payment_via_helios(
        "base-sepolia", "0xdead",
        required_recipient="0xRECIPIENT",
    ) is True


def test_arweave_eth_payment_wrong_recipient_returns_false() -> None:
    fake = _FakeHelios(
        "base-sepolia", in_sync=True,
        receipt={"status": "0x1", "to": "0xWRONG"},
    )
    set_default_client(fake)  # type: ignore[arg-type]
    assert arweave_mod.ensure_eth_payment_via_helios(
        "base-sepolia", "0xdead",
        required_recipient="0xRECIPIENT",
    ) is False


def test_arweave_eth_payment_recipient_case_insensitive() -> None:
    fake = _FakeHelios(
        "base-sepolia", in_sync=True,
        receipt={"status": "0x1", "to": "0xABCDEF1234567890"},
    )
    set_default_client(fake)  # type: ignore[arg-type]
    assert arweave_mod.ensure_eth_payment_via_helios(
        "base-sepolia", "0xdead",
        required_recipient="0xabcdef1234567890",
    ) is True


def test_arweave_eth_payment_helios_call_raises_returns_false() -> None:
    fake = _FakeHelios(
        "base-sepolia", in_sync=True,
        raise_on_call=RuntimeError("rpc down"),
    )
    set_default_client(fake)  # type: ignore[arg-type]
    assert arweave_mod.ensure_eth_payment_via_helios("base-sepolia", "0xdead") is False


# ─────────────────────────────────────────────────────────────────────────────
# 不破坏现 EAS / Arweave 的回归测试
# ─────────────────────────────────────────────────────────────────────────────


def test_eas_module_imports_unchanged() -> None:
    """改 eas.py 后 __all__ 里关键符号仍 export."""
    from sisoul.onchain import eas
    expected = {"AttestQueue", "AuditAttestation", "AttestConfig", "upload_batch",
                "verify_attestation_local", "_verify_testnet_rpc"}
    actual = set(dir(eas))
    for sym in expected:
        assert sym in actual, f"eas.{sym} 丢失"


def test_arweave_module_imports_unchanged() -> None:
    """改 arweave.py 后 __all__ 里关键符号仍 export."""
    from sisoul.onchain import arweave
    for sym in (
        "ArweaveSnapshot", "SnapshotRecord", "SnapshotHistory",
        "schedule_monthly_snapshot", "ensure_eth_payment_via_helios",
    ):
        assert hasattr(arweave, sym), f"arweave.{sym} 丢失"

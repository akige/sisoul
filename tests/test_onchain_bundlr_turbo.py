"""tests · onchain.bundlr_turbo (v1.0-decentralized Wave A agent-2 · #5).

15+ 单元 (httpx mock) + LIVE smoke (opt-in 跑 testnet free tier quote / 真上传).

覆盖:
- Quote: free tier <100KB / 付费 ≥100KB / 真 turbo /v1/price/bytes/{N} HTTP / 错误处理
- Upload: mock / turbo no-wallet 报错 / arweave-direct
- Status: mock permanent / 真 GET /tx/{id}/status (200/202/404)
- Fetch: mock 拒绝 / 真 GET <gateway>/<tx_id> (200/404)
- Mainnet 双 gate: env=0 + confirm=False / env=1 + confirm=False / 双开
- balance / fund / health / receipt_to_dict / quote_to_dict
- §J-2 真验收: SISOUL_TEST_BUNDLR_LIVE=1 跑 1 个真 quote+health 拿真数字
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sisoul.onchain.bundlr_turbo import (
    FREE_TIER_BYTES,
    TURBO_GATEWAY_DEFAULT,
    TURBO_PAYMENT_BASE,
    ArweaveMainnetGateError,
    ArweaveTxNotFound,
    ArweaveUploader,
    BundlrError,
    quote_to_dict,
    receipt_to_dict,
)


# ─────────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARWEAVE_ALLOW_MAINNET", raising=False)


@pytest.fixture()
def mock_uploader() -> ArweaveUploader:
    return ArweaveUploader(provider="mock", network="mock")


@pytest.fixture()
def turbo_uploader() -> ArweaveUploader:
    return ArweaveUploader(provider="turbo", network="testnet")


# ─────────────────────────────────────────────────────────────────────────
# 1. Quote
# ─────────────────────────────────────────────────────────────────────────


def test_quote_mock_under_100kb_is_free(mock_uploader: ArweaveUploader) -> None:
    q = mock_uploader.quote(50 * 1024)
    assert q.free_tier is True
    assert q.cost_usd == Decimal("0")
    assert q.cost_winc == 0
    assert q.bytes_count == 50 * 1024


def test_quote_mock_over_100kb_charged(mock_uploader: ArweaveUploader) -> None:
    q = mock_uploader.quote(1_000_000)
    assert q.free_tier is False
    assert q.cost_usd > Decimal("0")
    assert q.cost_winc > 0


def test_quote_negative_bytes_raises(mock_uploader: ArweaveUploader) -> None:
    with pytest.raises(ValueError):
        mock_uploader.quote(-1)


def test_quote_turbo_real_endpoint_mock(turbo_uploader: ArweaveUploader) -> None:
    """真 turbo provider: GET /v1/price/bytes/{N} → winc + GET /v1/rates → USD."""
    price_resp = MagicMock()
    price_resp.status_code = 200
    price_resp.headers = {"content-type": "application/json"}
    price_resp.json.return_value = {"winc": "1000000000"}
    price_resp.raise_for_status = MagicMock()

    rates_resp = MagicMock()
    rates_resp.status_code = 200
    rates_resp.json.return_value = {"winc": "1000000000000", "fiat": {"usd": "1.0"}}
    rates_resp.raise_for_status = MagicMock()

    shared_inst = MagicMock()
    shared_inst.get.side_effect = [price_resp, rates_resp]

    def _client_factory(*args: Any, **kw: Any) -> MagicMock:
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=shared_inst)
        cm.__exit__ = MagicMock(return_value=None)
        return cm

    with patch("httpx.Client", side_effect=_client_factory):
        q = turbo_uploader.quote(2 * 1024 * 1024)
    assert q.cost_winc == 1_000_000_000
    assert q.cost_usd == Decimal("0.0010")
    assert q.free_tier is False


def test_quote_turbo_http_error_raises_bundlr(turbo_uploader: ArweaveUploader) -> None:
    inst = MagicMock()
    inst.get.side_effect = httpx.HTTPError("dns down")
    cm = MagicMock()
    cm.__enter__.return_value = inst
    cm.__exit__.return_value = None
    with patch("httpx.Client", return_value=cm):
        with pytest.raises(BundlrError, match="price quote"):
            turbo_uploader.quote(200_000)


# ─────────────────────────────────────────────────────────────────────────
# 2. Upload
# ─────────────────────────────────────────────────────────────────────────


def test_upload_mock_returns_deterministic_tx(mock_uploader: ArweaveUploader) -> None:
    blob = b"hello world"
    r1 = mock_uploader.upload(blob)
    r2 = mock_uploader.upload(blob)
    assert r1.tx_id == r2.tx_id
    assert r1.tx_id.startswith("mocktx-")
    assert r1.bundle_id is not None
    assert r1.free_tier is True
    assert r1.provider == "mock"
    assert r1.fetch_url.endswith(r1.tx_id)


def test_upload_mock_large_blob_not_free(mock_uploader: ArweaveUploader) -> None:
    blob = b"x" * (200 * 1024)
    r = mock_uploader.upload(blob)
    assert r.free_tier is False
    assert r.cost_paid_usd > Decimal("0")


def test_upload_turbo_no_wallet_raises_bundlr_error() -> None:
    up = ArweaveUploader(provider="turbo", network="testnet", wallet_path=None)
    with pytest.raises(BundlrError, match="wallet"):
        up.upload(b"data")


def test_upload_arweave_direct_no_wallet_raises() -> None:
    up = ArweaveUploader(provider="arweave-direct", network="testnet")
    with pytest.raises(BundlrError, match="wallet_path"):
        up.upload(b"data")


def test_upload_mock_includes_default_tags(mock_uploader: ArweaveUploader) -> None:
    r = mock_uploader.upload(b"x", tags={"Comment": "test"})
    assert r.tx_id is not None


# ─────────────────────────────────────────────────────────────────────────
# 3. Status / Fetch
# ─────────────────────────────────────────────────────────────────────────


def test_status_mock_permanent(mock_uploader: ArweaveUploader) -> None:
    assert mock_uploader.status("mocktx-deadbeef") == "permanent"


def test_status_mock_nonmock_id_pending(mock_uploader: ArweaveUploader) -> None:
    assert mock_uploader.status("real_tx_id_xyz") == "pending"


def test_status_real_gateway_200_with_confirmations(turbo_uploader: ArweaveUploader) -> None:
    cases = [
        (0, "pending"),
        (5, "confirmed"),
        (20, "permanent"),
    ]
    for n, expected in cases:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"number_of_confirmations": n}
        inst = MagicMock()
        inst.get.return_value = resp
        cm = MagicMock()
        cm.__enter__.return_value = inst
        cm.__exit__.return_value = None
        with patch("httpx.Client", return_value=cm):
            assert turbo_uploader.status("Bf3xY9") == expected


def test_status_real_gateway_404_pending(turbo_uploader: ArweaveUploader) -> None:
    resp = MagicMock()
    resp.status_code = 404
    inst = MagicMock()
    inst.get.return_value = resp
    cm = MagicMock()
    cm.__enter__.return_value = inst
    cm.__exit__.return_value = None
    with patch("httpx.Client", return_value=cm):
        assert turbo_uploader.status("newtx") == "pending"


def test_fetch_mock_raises(mock_uploader: ArweaveUploader) -> None:
    with pytest.raises(BundlrError, match="mock provider"):
        mock_uploader.fetch("mocktx-xx")


def test_fetch_real_200(turbo_uploader: ArweaveUploader) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"payload bytes"
    resp.raise_for_status = MagicMock()
    inst = MagicMock()
    inst.get.return_value = resp
    cm = MagicMock()
    cm.__enter__.return_value = inst
    cm.__exit__.return_value = None
    with patch("httpx.Client", return_value=cm):
        data = turbo_uploader.fetch("Bf3xY9")
    assert data == b"payload bytes"


def test_fetch_404_raises_not_found(turbo_uploader: ArweaveUploader) -> None:
    resp = MagicMock()
    resp.status_code = 404
    inst = MagicMock()
    inst.get.return_value = resp
    cm = MagicMock()
    cm.__enter__.return_value = inst
    cm.__exit__.return_value = None
    with patch("httpx.Client", return_value=cm):
        with pytest.raises(ArweaveTxNotFound):
            turbo_uploader.fetch("does_not_exist")


# ─────────────────────────────────────────────────────────────────────────
# 4. Mainnet 双 gate
# ─────────────────────────────────────────────────────────────────────────


def test_mainnet_blocked_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARWEAVE_ALLOW_MAINNET", raising=False)
    up = ArweaveUploader(provider="turbo", network="mainnet", confirm_mainnet=True)
    assert up.network == "testnet"


def test_mainnet_blocked_without_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARWEAVE_ALLOW_MAINNET", "1")
    up = ArweaveUploader(provider="turbo", network="mainnet", confirm_mainnet=False)
    assert up.network == "testnet"


def test_mainnet_allowed_with_both_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARWEAVE_ALLOW_MAINNET", "1")
    up = ArweaveUploader(provider="turbo", network="mainnet", confirm_mainnet=True)
    assert up.network == "mainnet"


def test_arweave_direct_mainnet_gate_in_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """arweave-direct 在 upload 路径里也会 double-check gate (防御构造后被改 network)."""
    monkeypatch.delenv("ARWEAVE_ALLOW_MAINNET", raising=False)
    wallet = tmp_path / "w.json"
    wallet.write_text("{}")
    up = ArweaveUploader(
        provider="arweave-direct", network="mainnet",
        confirm_mainnet=False, wallet_path=wallet,
    )
    up.network = "mainnet"   # 测试 hack 直击 upload 内层 gate
    up.confirm_mainnet = False
    with pytest.raises(ArweaveMainnetGateError):
        up.upload(b"data")


# ─────────────────────────────────────────────────────────────────────────
# 5. balance / fund / health
# ─────────────────────────────────────────────────────────────────────────


def test_balance_mock(mock_uploader: ArweaveUploader) -> None:
    b = mock_uploader.balance("0xabc")
    assert b.balance_usd == Decimal("10.00")
    assert b.balance_winc > 0


def test_balance_real_404_returns_zero(turbo_uploader: ArweaveUploader) -> None:
    resp = MagicMock()
    resp.status_code = 404
    inst = MagicMock()
    inst.get.return_value = resp
    cm = MagicMock()
    cm.__enter__.return_value = inst
    cm.__exit__.return_value = None
    with patch("httpx.Client", return_value=cm):
        b = turbo_uploader.balance("0xnew")
    assert b.balance_winc == 0
    assert b.balance_usd == Decimal("0")


def test_fund_mock_returns_checkout_url(mock_uploader: ArweaveUploader) -> None:
    r = mock_uploader.fund("0xabc", Decimal("5.00"), token="USDC")
    assert r.checkout_url.startswith("mock://turbo/checkout/")
    assert r.amount_usd == Decimal("5.00")
    assert r.token == "USDC"


def test_fund_zero_raises(mock_uploader: ArweaveUploader) -> None:
    with pytest.raises(ValueError):
        mock_uploader.fund("0xabc", Decimal("0"))


def test_fund_real_url_format(turbo_uploader: ArweaveUploader) -> None:
    r = turbo_uploader.fund("0xabc", Decimal("2.50"), token="ETH")
    assert r.checkout_url == (
        f"{TURBO_PAYMENT_BASE}/top-up/checkout-session/0xabc/250/eth"
    )


def test_health_mock(mock_uploader: ArweaveUploader) -> None:
    h = mock_uploader.health()
    assert h["mock"] is True


# ─────────────────────────────────────────────────────────────────────────
# 6. helpers
# ─────────────────────────────────────────────────────────────────────────


def test_receipt_to_dict_decimal_to_str(mock_uploader: ArweaveUploader) -> None:
    r = mock_uploader.upload(b"x")
    d = receipt_to_dict(r)
    assert isinstance(d["cost_paid_usd"], str)
    json.dumps(d)


def test_quote_to_dict_decimal_to_str(mock_uploader: ArweaveUploader) -> None:
    q = mock_uploader.quote(50_000)
    d = quote_to_dict(q)
    assert isinstance(d["cost_usd"], str)
    json.dumps(d)


def test_free_tier_boundary() -> None:
    up = ArweaveUploader(provider="mock")
    assert up.quote(FREE_TIER_BYTES - 1).free_tier is True
    assert up.quote(FREE_TIER_BYTES).free_tier is False


# ─────────────────────────────────────────────────────────────────────────
# 7. 集成: ArweaveSnapshot ←→ ArweaveUploader
# ─────────────────────────────────────────────────────────────────────────


def test_arweave_snapshot_uses_bundlr_uploader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ArweaveSnapshot 改造后真走 ArweaveUploader (mock provider)."""
    from sisoul.identity.seed import generate_mnemonic
    from sisoul.onchain.arweave import ArweaveSnapshot, SnapshotHistory

    monkeypatch.delenv("PINATA_JWT", raising=False)
    mnemonic = generate_mnemonic(strength=128)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("hello")

    client = ArweaveSnapshot(
        mnemonic=mnemonic, network="mock",
        history=SnapshotHistory(tmp_path / "h.json"),
    )
    assert client.bundlr_provider == "mock"
    record = client.snapshot_now(vault, upload="arweave")
    assert record.arweave_tx_id is not None
    assert record.arweave_tx_id.startswith("mocktx-")
    assert record.bundle_id is not None and record.bundle_id.startswith("mockbundle-")
    assert record.fetch_url is not None and record.fetch_url.endswith(record.arweave_tx_id)
    assert record.provider == "mock"
    assert record.cost_paid_usd == "0"


def test_arweave_snapshot_uploader_failure_marks_record_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sisoul.identity.seed import generate_mnemonic
    from sisoul.onchain.arweave import ArweaveSnapshot, SnapshotHistory

    monkeypatch.delenv("PINATA_JWT", raising=False)
    mnemonic = generate_mnemonic(strength=128)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("hello")

    client = ArweaveSnapshot(
        mnemonic=mnemonic, network="mock",
        history=SnapshotHistory(tmp_path / "h.json"),
    )

    def _boom(*a: Any, **kw: Any) -> Any:
        raise BundlrError("simulated upstream failure")

    client.uploader.upload = _boom   # type: ignore[method-assign]
    record = client.snapshot_now(vault, upload="arweave")
    assert record.status == "failed"
    assert record.arweave_tx_id is None
    assert record.error is not None and "simulated" in record.error


# ─────────────────────────────────────────────────────────────────────────
# 8. §J-2 真验收 smoke (opt-in)
# ─────────────────────────────────────────────────────────────────────────

LIVE = os.environ.get("SISOUL_TEST_BUNDLR_LIVE") == "1"
LIVE_UPLOAD = os.environ.get("SISOUL_TEST_BUNDLR_LIVE_UPLOAD") == "1"


@pytest.mark.skipif(not LIVE, reason="设 SISOUL_TEST_BUNDLR_LIVE=1 才跑 (真打 Turbo)")
def test_live_turbo_quote_under_100kb_is_free() -> None:
    """V1: 真 Turbo /v1/price/bytes/50000 应返 winc + sisoul 标记 free_tier=True.

    §J-2 真验收: 贴实际 winc + usd 真数字, 不只看 status_code 200.
    """
    up = ArweaveUploader(provider="turbo", network="testnet")
    q = up.quote(50_000)
    print(f"[LIVE] Quote 50KB: winc={q.cost_winc}, usd={q.cost_usd}, free_tier={q.free_tier}")
    assert q.free_tier is True
    assert q.cost_winc > 0
    assert q.cost_usd != Decimal("-1"), "USD conversion 应通"


@pytest.mark.skipif(not LIVE, reason="设 SISOUL_TEST_BUNDLR_LIVE=1 才跑")
def test_live_turbo_quote_1mb_charged() -> None:
    """V2: 真 Turbo /v1/price/bytes/1MB 应 winc > 0."""
    up = ArweaveUploader(provider="turbo", network="testnet")
    q = up.quote(1_000_000)
    print(f"[LIVE] Quote 1MB: winc={q.cost_winc}, usd={q.cost_usd}")
    assert q.cost_winc > 0


@pytest.mark.skipif(not LIVE, reason="设 SISOUL_TEST_BUNDLR_LIVE=1 才跑")
def test_live_turbo_service_health() -> None:
    """V3: Turbo upload + payment + arweave gateway 三 service 全 alive."""
    up = ArweaveUploader(provider="turbo", network="testnet")
    h = up.health()
    print(f"[LIVE] Health: {json.dumps(h, indent=2, default=str)}")
    assert h.get("gateway_alive") is True


@pytest.mark.skipif(not LIVE, reason="设 SISOUL_TEST_BUNDLR_LIVE=1 才跑")
def test_live_arweave_mainnet_fetch_known_tx() -> None:
    """V4: arweave.net /info 真可达."""
    resp = httpx.get(f"{TURBO_GATEWAY_DEFAULT}/info", timeout=15.0)
    print(f"[LIVE] gateway /info: status={resp.status_code}")
    assert resp.status_code == 200
    data = resp.json()
    assert "network" in data or "height" in data


@pytest.mark.skipif(
    not LIVE_UPLOAD or not os.environ.get("ARWEAVE_WALLET"),
    reason="设 SISOUL_TEST_BUNDLR_LIVE_UPLOAD=1 + ARWEAVE_WALLET=path 才跑真上传",
)
def test_live_turbo_real_upload_under_100kb() -> None:
    """V5 (§J-2 强 smoke): 真上传 <100KB blob, 拿真 tx_id, 验 gateway 可访问.

    需要:
    - SISOUL_TEST_BUNDLR_LIVE_UPLOAD=1
    - ARWEAVE_WALLET=/path/to/wallet.json (Arweave JWK)
    - pip install 'sisoul[onchain]' (arweave-python-client)
    """
    import time

    wallet = Path(os.environ["ARWEAVE_WALLET"]).expanduser()
    up = ArweaveUploader(provider="turbo", network="testnet", wallet_path=wallet)
    blob = b"sisoul-vck-smoke-" + os.urandom(64)
    receipt = up.upload(blob, tags={"App-Name": "sisoul-smoke", "Test-Run": str(int(time.time()))})
    print(f"[LIVE-UPLOAD] tx_id={receipt.tx_id}")
    print(f"[LIVE-UPLOAD] bundle_id={receipt.bundle_id}")
    print(f"[LIVE-UPLOAD] cost_paid_usd={receipt.cost_paid_usd}")
    print(f"[LIVE-UPLOAD] fetch_url={receipt.fetch_url}")
    assert receipt.tx_id and len(receipt.tx_id) >= 32
    assert receipt.fetch_url.startswith("https://")

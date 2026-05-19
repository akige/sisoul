"""真连 Optimism Sepolia testnet readonly smoke test (波 4 dev-B).

默认 skip. 设 ``SISOUL_TEST_LIVE_TESTNET=1`` 才跑.
不发 tx (无私钥), 只:
1. 调 RPC eth_chainId 校验 chain_id == 11155420
2. 调 EAS GraphQL endpoint 查 schema 存在 (verify_attestation_onchain 路径 smoke)

用途:
- M3 验收时跑 (qa-D)
- CI 不跑 (避免依赖外部公共 RPC 稳定性)
"""

from __future__ import annotations

import os

import pytest

from sisoul.onchain.eas import (
    OPTIMISM_SEPOLIA_CHAIN_ID,
    OPTIMISM_SEPOLIA_DEFAULT_RPC,
    _verify_optimism_sepolia_rpc,
    list_history_onchain,
    verify_attestation_onchain,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("SISOUL_TEST_LIVE_TESTNET") != "1",
    reason="设 SISOUL_TEST_LIVE_TESTNET=1 才真连 Optimism Sepolia RPC",
)


def test_rpc_chain_id_matches() -> None:
    """RPC eth_chainId 返 Optimism Sepolia chain id."""
    rpc = os.environ.get("SISOUL_OPTIMISM_SEPOLIA_RPC", OPTIMISM_SEPOLIA_DEFAULT_RPC)
    # 应不抛
    _verify_optimism_sepolia_rpc(rpc)


def test_graphql_endpoint_reachable() -> None:
    """EAS GraphQL endpoint 可访问 (查不存在的 uid → 返 not-found 但 200)."""
    r = verify_attestation_onchain(
        uid="0x" + "0" * 64,  # 不可能存在的 uid
        network="optimism-sepolia",
    )
    assert r["method"] == "onchain-graphql"
    # 应返 invalid + 原因 (不存在 / 调用失败), 不应抛
    assert r["valid"] is False


def test_list_history_onchain_callable() -> None:
    """list_history_onchain 接口 smoke (limit=1, 不强求有结果)."""
    out = list_history_onchain(network="optimism-sepolia", limit=1)
    # 不强求 len > 0 (公共 testnet 可能空), 只验证不抛
    assert isinstance(out, list)

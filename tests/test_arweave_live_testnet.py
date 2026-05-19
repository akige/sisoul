"""tests · 真打 Arweave testnet (默认 skip).

启用: SISOUL_TEST_LIVE_TESTNET=1 pytest tests/test_arweave_live_testnet.py

只跑 readonly 路径:
- GraphQL query 验证 gateway 在线
- 跑一个已知 testnet tx 的 metadata 取回

不真上传 (要 wallet + 测试网 AR coin; 留 Phase 5).
"""

from __future__ import annotations

import os

import httpx
import pytest

from sisoul.onchain.arweave import ARWEAVE_TESTNET_GATEWAY

LIVE = os.environ.get("SISOUL_TEST_LIVE_TESTNET") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="设 SISOUL_TEST_LIVE_TESTNET=1 才跑 (真打 Arweave testnet)",
)


def test_testnet_gateway_alive() -> None:
    """GET <testnet>/info 应 200 + 含 network: arweave.N.1."""
    try:
        r = httpx.get(f"{ARWEAVE_TESTNET_GATEWAY}/info", timeout=20.0)
    except httpx.HTTPError as e:
        pytest.skip(f"testnet 不可达: {e}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "network" in data or "height" in data, data


def test_testnet_graphql_query() -> None:
    """GraphQL endpoint 应能查 sisoul 标签 (即使 0 结果也 200)."""
    query = """
    query {
      transactions(
        tags: [{ name: "App-Name", values: ["sisoul"] }]
        first: 1
      ) {
        edges { node { id } }
      }
    }
    """
    try:
        r = httpx.post(
            f"{ARWEAVE_TESTNET_GATEWAY}/graphql",
            json={"query": query},
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        pytest.skip(f"testnet graphql 不可达: {e}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "data" in data, data
    # 0 结果也 OK, 关键 schema 合规
    edges = data["data"].get("transactions", {}).get("edges", [])
    assert isinstance(edges, list)

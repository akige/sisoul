"""tests for sisoul.dao.governance — Phase 3 P3-4 DAO governance.

覆盖:
- DAOConfig load/save 默认值 + roundtrip
- GovernorClient mock propose / cast_vote / state / proposal_votes
- propose_pip_promotion calldata 拼接 + status 校验
- 错误路径: ProposalNotFound / 长度不一致 / 非法 support / 非法 next_status
- daemon route: POST /sisoul/dao/propose + /vote + GET /proposals/{id} + /proposals
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


from sisoul.dao.governance import (
    DAOConfig,
    DAOError,
    GovernorClient,
    PROPOSAL_STATE_NAMES,
    ProposalNotFoundError,
    ProposalState,
    SUPPORT_FOR,
    SUPPORT_AGAINST,
    SUPPORT_ABSTAIN,
    load_dao_config,
    propose_pip_promotion,
    save_dao_config,
)


# ── 1. config ────────────────────────────────────────────────────────────────


def test_default_config_is_mock():
    cfg = DAOConfig()
    assert cfg.mode == "mock"
    assert cfg.chain_id == 11155420


def test_config_save_load_roundtrip(tmp_path: Path):
    cfg = DAOConfig(
        mode="live",
        rpc_url="https://example.org",
        chain_id=42161,
        governor_address="0xabc",
        pip_registry_address="0xpipdef0123456789012345678901234567890000",
    )
    p = tmp_path / "dao_cfg.json"
    save_dao_config(cfg, p)
    loaded = load_dao_config(p)
    assert loaded.mode == "live"
    assert loaded.chain_id == 42161
    assert loaded.governor_address == "0xabc"


def test_config_missing_file_returns_defaults(tmp_path: Path):
    cfg = load_dao_config(tmp_path / "no-such.json")
    assert cfg.mode == "mock"


# ── 2. GovernorClient mock propose ───────────────────────────────────────────


def test_mock_propose_deterministic_pid():
    cfg = DAOConfig(mode="mock")
    c1 = GovernorClient(cfg)
    c2 = GovernorClient(cfg)
    targets = ["0x0000000000000000000000000000000000000001"]
    values = [0]
    calldatas = ["0xdeadbeef"]
    desc = "same"
    s1 = c1.propose(targets, values, calldatas, desc)
    s2 = c2.propose(targets, values, calldatas, desc)
    assert s1.proposal_id == s2.proposal_id
    assert s1.state == int(ProposalState.Pending)
    assert s1.tx_hash is not None


def test_mock_propose_length_mismatch():
    c = GovernorClient(DAOConfig(mode="mock"))
    with pytest.raises(DAOError):
        c.propose(["0x1", "0x2"], [0], ["0x"], "x")


# ── 3. cast_vote + state advance ─────────────────────────────────────────────


def test_mock_cast_vote_for_advances_to_active():
    c = GovernorClient(DAOConfig(mode="mock"))
    s = c.propose(["0x1"], [0], ["0x"], "d")
    pid = s.proposal_id
    assert c.state(pid) == ProposalState.Pending
    tx = c.cast_vote(pid, "for")
    assert tx.startswith("0x")
    assert c.state(pid) == ProposalState.Active
    against, for_v, abstain = c.proposal_votes(pid)
    assert for_v > 0
    assert against == 0
    assert abstain == 0


def test_mock_cast_vote_against_and_abstain():
    c = GovernorClient(DAOConfig(mode="mock"))
    s = c.propose(["0x1"], [0], ["0x"], "d")
    pid = s.proposal_id
    c.cast_vote(pid, "against")
    c.cast_vote(pid, "abstain")
    against, for_v, abstain = c.proposal_votes(pid)
    assert against > 0
    assert abstain > 0
    assert for_v == 0


def test_mock_cast_vote_int_support():
    c = GovernorClient(DAOConfig(mode="mock"))
    s = c.propose(["0x1"], [0], ["0x"], "d")
    c.cast_vote(s.proposal_id, SUPPORT_FOR)
    c.cast_vote(s.proposal_id, SUPPORT_AGAINST)
    against, for_v, _ = c.proposal_votes(s.proposal_id)
    assert against > 0 and for_v > 0


def test_mock_cast_vote_invalid_support_str():
    c = GovernorClient(DAOConfig(mode="mock"))
    s = c.propose(["0x1"], [0], ["0x"], "d")
    with pytest.raises(DAOError):
        c.cast_vote(s.proposal_id, "yes")


def test_mock_cast_vote_invalid_support_int():
    c = GovernorClient(DAOConfig(mode="mock"))
    s = c.propose(["0x1"], [0], ["0x"], "d")
    with pytest.raises(DAOError):
        c.cast_vote(s.proposal_id, 7)


def test_mock_state_unknown_proposal():
    c = GovernorClient(DAOConfig(mode="mock"))
    with pytest.raises(ProposalNotFoundError):
        c.state(999)


# ── 4. propose_pip_promotion ─────────────────────────────────────────────────


def test_propose_pip_promotion_calldata_shape():
    cfg = DAOConfig(
        mode="mock",
        pip_registry_address="0x1234567890123456789012345678901234567890",
    )
    c = GovernorClient(cfg)
    s = propose_pip_promotion(3, "review", c)
    assert len(s.calldatas) == 1
    cd = s.calldatas[0]
    # 4-byte selector + 32-byte pip_id (0x03 padded) + 32-byte status (0x02)
    assert cd.startswith("0x")
    assert len(cd) == 2 + 8 + 64 + 64
    # 末尾 32 字节 = status enum 2 (Review)
    assert cd.endswith("0" * 63 + "2")


def test_propose_pip_promotion_invalid_status():
    cfg = DAOConfig(
        mode="mock",
        pip_registry_address="0x1234567890123456789012345678901234567890",
    )
    c = GovernorClient(cfg)
    with pytest.raises(DAOError):
        propose_pip_promotion(3, "nope", c)


def test_propose_pip_promotion_no_registry_address():
    c = GovernorClient(DAOConfig(mode="mock"))  # 默认 0x0...0
    with pytest.raises(DAOError):
        propose_pip_promotion(3, "review", c)


def test_propose_pip_promotion_status_aliases():
    cfg = DAOConfig(
        mode="mock",
        pip_registry_address="0x1234567890123456789012345678901234567890",
    )
    c = GovernorClient(cfg)
    # final-call / final_call / FinalCall 都接受
    s1 = propose_pip_promotion(4, "final-call", c)
    s2 = propose_pip_promotion(5, "FinalCall", c)
    assert s1.calldatas[0].endswith("0" * 63 + "3")
    assert s2.calldatas[0].endswith("0" * 63 + "3")


# ── 5. proposal_state names ──────────────────────────────────────────────────


def test_proposal_state_names_full():
    assert PROPOSAL_STATE_NAMES[0] == "Pending"
    assert PROPOSAL_STATE_NAMES[7] == "Executed"
    assert len(PROPOSAL_STATE_NAMES) == 8


# ── 6. summary roundtrip ─────────────────────────────────────────────────────


def test_summary_reflects_latest_votes():
    c = GovernorClient(DAOConfig(mode="mock"))
    s = c.propose(["0x1"], [0], ["0x"], "d")
    c.cast_vote(s.proposal_id, "for")
    c.cast_vote(s.proposal_id, "for")
    summary = c.summary(s.proposal_id)
    assert summary.state == int(ProposalState.Active)
    assert summary.votes_for == 2 * (10**18)


# ── 7. daemon route smoke (mock) ─────────────────────────────────────────────

# 提交一个 propose → vote → list/get, 看 mock store 端到端通.


def test_daemon_dao_full_flow():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    # 导入 dao_router 单独挂载 (避开 daemon 全 router 加载副作用)
    from fastapi import FastAPI
    from sisoul.daemon_routes.dao import dao_router, reset_dao_client_for_test

    reset_dao_client_for_test()

    app = FastAPI()
    app.include_router(dao_router)
    client = TestClient(app)

    # 1. 先把 pip_registry 地址设到默认 mock client (通过 ENV / share 配置)
    #    本测试用 share client + monkey-patch config.
    from sisoul.daemon_routes import dao as dao_mod

    # 触发懒加载
    c = dao_mod._client(None)
    c.config.pip_registry_address = "0x1234567890123456789012345678901234567890"

    # propose
    r = client.post("/sisoul/dao/propose", json={"pip_id": 3, "next_status": "review"})
    assert r.status_code == 200, r.text
    body = r.json()
    pid = body["proposal_id"]
    assert body["state_name"] == "Pending"

    # vote
    r2 = client.post(
        "/sisoul/dao/vote", json={"proposal_id": pid, "support": "for"}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["tx_hash"].startswith("0x")

    # get one
    r3 = client.get(f"/sisoul/dao/proposals/{pid}")
    assert r3.status_code == 200
    assert r3.json()["state_name"] == "Active"
    assert r3.json()["votes_for"] > 0

    # list
    r4 = client.get("/sisoul/dao/proposals")
    assert r4.status_code == 200
    j = r4.json()
    assert j["mode"] == "mock"
    assert j["count"] >= 1
    assert any(p["proposal_id"] == pid for p in j["proposals"])


def test_daemon_dao_vote_unknown_proposal():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sisoul.daemon_routes.dao import dao_router, reset_dao_client_for_test

    reset_dao_client_for_test()
    app = FastAPI()
    app.include_router(dao_router)
    client = TestClient(app)

    r = client.post("/sisoul/dao/vote", json={"proposal_id": 12345, "support": "for"})
    assert r.status_code == 404


def test_daemon_dao_propose_bad_status():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sisoul.daemon_routes.dao import dao_router, reset_dao_client_for_test

    reset_dao_client_for_test()
    app = FastAPI()
    app.include_router(dao_router)
    client = TestClient(app)

    # registry address not set in this fresh client → DAOError → 400
    r = client.post(
        "/sisoul/dao/propose", json={"pip_id": 3, "next_status": "review"}
    )
    assert r.status_code == 400

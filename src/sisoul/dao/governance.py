"""sisoul DAO governance Python 客户端 (Phase 3 P3-4).

封装 SisoulGov / SisoulToken / PIPRegistry web3.py 调用. 默认 mock 模式 (无 RPC),
便于在没装 web3 / 没真链时仍能跑测试 + CLI dry-run.

模式:
- mock: 不连链, 返本地确定性 stub (proposalId = keccak256(targets+values+...))
- live: 连真 RPC, 走 web3.py

PIP-id → proposal 模式:
    `sisoul dao propose PIP-003` →
    1. 读 PIP-003 的 spec CID (从本地 obs 或 PIPRegistry on-chain)
    2. propose 主体: PIPRegistry.setStatus(3, Review) 或 (3, Final) (取决于当前 status)
    3. description = "Promote PIP-003 to next stage: <new_status>\\nCID: <cid>"
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Literal, Optional

# ── 公开常量 ─────────────────────────────────────────────────────────────────

DEFAULT_DAO_CONFIG = Path.home() / ".sisoul" / "dao_config.json"

DAOMode = Literal["mock", "live"]


class ProposalState(IntEnum):
    """对齐 OZ Governor v5 IGovernor.ProposalState."""

    Pending = 0
    Active = 1
    Canceled = 2
    Defeated = 3
    Succeeded = 4
    Queued = 5
    Expired = 6
    Executed = 7


PROPOSAL_STATE_NAMES = {s.value: s.name for s in ProposalState}


# OZ Governor castVote support values
SUPPORT_AGAINST = 0
SUPPORT_FOR = 1
SUPPORT_ABSTAIN = 2

SUPPORT_MAP = {
    "against": SUPPORT_AGAINST,
    "for": SUPPORT_FOR,
    "abstain": SUPPORT_ABSTAIN,
}


# ── 异常 ─────────────────────────────────────────────────────────────────────


class DAOError(Exception):
    """DAO 通用异常."""


class ProposalNotFoundError(DAOError):
    """proposal_id 链上找不到."""


class Web3NotInstalledError(DAOError):
    """live 模式但 web3 没装."""


# ── Config ───────────────────────────────────────────────────────────────────


@dataclass
class DAOConfig:
    """DAO config (~/.sisoul/dao_config.json)."""

    mode: DAOMode = "mock"
    rpc_url: str = "https://sepolia.optimism.io"
    chain_id: int = 11155420
    governor_address: str = "0x0000000000000000000000000000000000000000"
    token_address: str = "0x0000000000000000000000000000000000000000"
    pip_registry_address: str = "0x0000000000000000000000000000000000000000"
    skill_registry_address: str = "0x0000000000000000000000000000000000000000"
    timelock_address: str = "0x0000000000000000000000000000000000000000"
    private_key_path: Optional[str] = None  # hex key file
    sender_address: Optional[str] = None  # 显式 sender (mock 用)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DAOConfig":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


def load_dao_config(path: Path | str | None = None) -> DAOConfig:
    p = Path(path) if path else DEFAULT_DAO_CONFIG
    if not p.exists():
        return DAOConfig()
    try:
        return DAOConfig.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        raise DAOError(f"读 dao_config 失败 ({p}): {e}") from e


def save_dao_config(cfg: DAOConfig, path: Path | str | None = None) -> Path:
    p = Path(path) if path else DEFAULT_DAO_CONFIG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ── 数据 ─────────────────────────────────────────────────────────────────────


@dataclass
class ProposalSummary:
    """提案概览 (mock + live 通用)."""

    proposal_id: int
    description: str
    state: int  # ProposalState
    state_name: str
    proposer: str
    targets: list[str] = field(default_factory=list)
    values: list[int] = field(default_factory=list)
    calldatas: list[str] = field(default_factory=list)
    description_hash: str = ""
    votes_for: int = 0
    votes_against: int = 0
    votes_abstain: int = 0
    tx_hash: Optional[str] = None  # propose tx hash (live)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── ABI (最小集) ─────────────────────────────────────────────────────────────

# 只包含本模块用到的方法. 真合约 ABI artifact 由 forge build 后从 out/ 加载.
GOVERNOR_ABI_MIN = [
    {
        "name": "propose",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "targets", "type": "address[]"},
            {"name": "values", "type": "uint256[]"},
            {"name": "calldatas", "type": "bytes[]"},
            {"name": "description", "type": "string"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "castVote",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "support", "type": "uint8"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "state",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "proposalId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name": "proposalVotes",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "proposalId", "type": "uint256"}],
        "outputs": [
            {"name": "againstVotes", "type": "uint256"},
            {"name": "forVotes", "type": "uint256"},
            {"name": "abstainVotes", "type": "uint256"},
        ],
    },
    {
        "name": "hashProposal",
        "type": "function",
        "stateMutability": "pure",
        "inputs": [
            {"name": "targets", "type": "address[]"},
            {"name": "values", "type": "uint256[]"},
            {"name": "calldatas", "type": "bytes[]"},
            {"name": "descriptionHash", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


# ── 客户端 ────────────────────────────────────────────────────────────────────


class GovernorClient:
    """Governor 客户端封装. 默认 mock; live 走 web3.py.

    mock 模式下:
    - propose 返 deterministic id (keccak256 of canonical) + 本地内存 dict 记录
    - cast_vote 累加 mock 内存
    - state 返 Pending (除非 mock 内 vote 模拟 advance 状态)
    """

    def __init__(self, config: DAOConfig | None = None):
        self.config = config or DAOConfig()
        self._mock_store: dict[int, ProposalSummary] = {}
        self._w3 = None
        self._contract = None

    # ── live 初始化 ────────────────────────────────────────────────────────

    def _ensure_live(self) -> None:
        if self.config.mode != "live":
            return
        if self._w3 is not None:
            return
        try:
            from web3 import Web3  # type: ignore[import-not-found]
        except ImportError as e:
            raise Web3NotInstalledError(
                "web3 未装. pip install 'sisoul[onchain]'."
            ) from e
        self._w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))
        if not self._w3.is_connected():
            raise DAOError(f"web3 connect 失败: {self.config.rpc_url}")
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(self.config.governor_address),
            abi=GOVERNOR_ABI_MIN,
        )

    # ── propose ────────────────────────────────────────────────────────────

    def propose(
        self,
        targets: list[str],
        values: list[int],
        calldatas: list[str],
        description: str,
    ) -> ProposalSummary:
        if len(targets) != len(values) or len(values) != len(calldatas):
            raise DAOError("targets / values / calldatas 长度必须一致")

        desc_hash = "0x" + hashlib.sha3_256(description.encode("utf-8")).hexdigest()

        if self.config.mode == "mock":
            pid = self._mock_proposal_id(targets, values, calldatas, desc_hash)
            summary = ProposalSummary(
                proposal_id=pid,
                description=description,
                state=int(ProposalState.Pending),
                state_name=ProposalState.Pending.name,
                proposer=self.config.sender_address or "0xMOCK",
                targets=targets,
                values=values,
                calldatas=calldatas,
                description_hash=desc_hash,
                tx_hash="0x" + "ab" * 32,
            )
            self._mock_store[pid] = summary
            return summary

        # live
        self._ensure_live()
        assert self._contract is not None and self._w3 is not None

        # 真发 tx: 需 private key
        if not self.config.private_key_path:
            raise DAOError("live propose 需 private_key_path 配置")
        pk = Path(self.config.private_key_path).read_text(encoding="utf-8").strip()
        if not pk.startswith("0x"):
            pk = "0x" + pk

        from web3 import Web3  # type: ignore[import-not-found]
        from eth_account import Account  # type: ignore[import-not-found]

        acct = Account.from_key(pk)
        fn = self._contract.functions.propose(
            [Web3.to_checksum_address(t) for t in targets],
            values,
            [bytes.fromhex(cd[2:] if cd.startswith("0x") else cd) for cd in calldatas],
            description,
        )
        tx = fn.build_transaction(
            {
                "from": acct.address,
                "nonce": self._w3.eth.get_transaction_count(acct.address),
                "chainId": self.config.chain_id,
                "gas": 500_000,
                "gasPrice": self._w3.eth.gas_price,
            }
        )
        signed = acct.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash)

        # 从 receipt logs 解 ProposalCreated event (省略, 用 hashProposal 视图)
        pid = self._contract.functions.hashProposal(
            [Web3.to_checksum_address(t) for t in targets],
            values,
            [bytes.fromhex(cd[2:] if cd.startswith("0x") else cd) for cd in calldatas],
            bytes.fromhex(desc_hash[2:]),
        ).call()

        return ProposalSummary(
            proposal_id=int(pid),
            description=description,
            state=int(ProposalState.Pending),
            state_name=ProposalState.Pending.name,
            proposer=acct.address,
            targets=targets,
            values=values,
            calldatas=calldatas,
            description_hash=desc_hash,
            tx_hash=tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
        )

    # ── cast_vote ──────────────────────────────────────────────────────────

    def cast_vote(self, proposal_id: int, support: int | str) -> str:
        """投票. support: 0/1/2 或 'against'/'for'/'abstain'. 返 tx_hash."""
        if isinstance(support, str):
            key = support.lower()
            if key not in SUPPORT_MAP:
                raise DAOError(f"support 必须是 for/against/abstain, 拿到 '{support}'")
            support_int = SUPPORT_MAP[key]
        else:
            support_int = int(support)
            if support_int not in (0, 1, 2):
                raise DAOError(f"support 必须是 0/1/2, 拿到 {support_int}")

        if self.config.mode == "mock":
            if proposal_id not in self._mock_store:
                raise ProposalNotFoundError(f"mock store 无 proposal {proposal_id}")
            summary = self._mock_store[proposal_id]
            weight = 10**18  # 1 SIS mock
            if support_int == SUPPORT_FOR:
                summary.votes_for += weight
            elif support_int == SUPPORT_AGAINST:
                summary.votes_against += weight
            else:
                summary.votes_abstain += weight
            # mock advance state to Active 让 status 不全停 Pending
            if summary.state == int(ProposalState.Pending):
                summary.state = int(ProposalState.Active)
                summary.state_name = ProposalState.Active.name
            return "0x" + hashlib.sha256(
                f"mock-vote:{proposal_id}:{support_int}".encode()
            ).hexdigest()

        self._ensure_live()
        assert self._contract is not None and self._w3 is not None
        if not self.config.private_key_path:
            raise DAOError("live cast_vote 需 private_key_path")
        pk = Path(self.config.private_key_path).read_text(encoding="utf-8").strip()
        if not pk.startswith("0x"):
            pk = "0x" + pk
        from eth_account import Account  # type: ignore[import-not-found]

        acct = Account.from_key(pk)
        fn = self._contract.functions.castVote(proposal_id, support_int)
        tx = fn.build_transaction(
            {
                "from": acct.address,
                "nonce": self._w3.eth.get_transaction_count(acct.address),
                "chainId": self.config.chain_id,
                "gas": 200_000,
                "gasPrice": self._w3.eth.gas_price,
            }
        )
        signed = acct.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.rawTransaction)
        self._w3.eth.wait_for_transaction_receipt(tx_hash)
        return tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)

    # ── state ──────────────────────────────────────────────────────────────

    def state(self, proposal_id: int) -> ProposalState:
        if self.config.mode == "mock":
            if proposal_id not in self._mock_store:
                raise ProposalNotFoundError(f"mock store 无 proposal {proposal_id}")
            return ProposalState(self._mock_store[proposal_id].state)
        self._ensure_live()
        assert self._contract is not None
        try:
            raw = self._contract.functions.state(proposal_id).call()
            return ProposalState(int(raw))
        except Exception as e:
            raise ProposalNotFoundError(
                f"链上 state({proposal_id}) 失败: {e}"
            ) from e

    # ── proposal_votes ──────────────────────────────────────────────────────

    def proposal_votes(self, proposal_id: int) -> tuple[int, int, int]:
        """返 (against, for, abstain)."""
        if self.config.mode == "mock":
            if proposal_id not in self._mock_store:
                raise ProposalNotFoundError(f"mock store 无 proposal {proposal_id}")
            s = self._mock_store[proposal_id]
            return (s.votes_against, s.votes_for, s.votes_abstain)
        self._ensure_live()
        assert self._contract is not None
        try:
            raw = self._contract.functions.proposalVotes(proposal_id).call()
            return (int(raw[0]), int(raw[1]), int(raw[2]))
        except Exception as e:
            raise ProposalNotFoundError(
                f"链上 proposalVotes({proposal_id}) 失败: {e}"
            ) from e

    def summary(self, proposal_id: int) -> ProposalSummary:
        st = self.state(proposal_id)
        against, for_v, abstain = self.proposal_votes(proposal_id)
        if self.config.mode == "mock" and proposal_id in self._mock_store:
            s = self._mock_store[proposal_id]
            s.state = int(st)
            s.state_name = st.name
            s.votes_against = against
            s.votes_for = for_v
            s.votes_abstain = abstain
            return s
        # live: 链上没存 description/targets, 只能返最小集
        return ProposalSummary(
            proposal_id=proposal_id,
            description="(live: description not stored on-chain after propose)",
            state=int(st),
            state_name=st.name,
            proposer="(unknown — query events)",
            votes_for=for_v,
            votes_against=against,
            votes_abstain=abstain,
        )

    # ── mock 内部 ──────────────────────────────────────────────────────────

    @staticmethod
    def _mock_proposal_id(
        targets: list[str], values: list[int], calldatas: list[str], desc_hash: str
    ) -> int:
        canonical = json.dumps(
            {
                "targets": [t.lower() for t in targets],
                "values": values,
                "calldatas": [c.lower() for c in calldatas],
                "desc_hash": desc_hash.lower(),
            },
            sort_keys=True,
        )
        return int(hashlib.sha256(canonical.encode()).hexdigest(), 16) % (2**256)


# ── PIP-id → proposal helper ─────────────────────────────────────────────────


def propose_pip_promotion(
    pip_id: int,
    next_status: str,
    client: GovernorClient,
    pip_registry_address: str | None = None,
) -> ProposalSummary:
    """快捷封装: 给 PIPRegistry.setStatus(pip_id, next_status) 发 propose.

    next_status: 'review' / 'finalcall' / 'final' / 'withdrawn'.
    """
    status_map = {
        "draft": 1,
        "review": 2,
        "finalcall": 3,
        "final": 4,
        "withdrawn": 5,
        "superseded": 6,
    }
    key = next_status.lower().replace("-", "").replace("_", "")
    if key not in status_map:
        raise DAOError(
            f"next_status 必须 ∈ {list(status_map.keys())}, 拿到 '{next_status}'"
        )
    new_status_int = status_map[key]

    addr = pip_registry_address or client.config.pip_registry_address
    if addr == "0x0000000000000000000000000000000000000000":
        raise DAOError(
            "pip_registry_address 未配置. 先 `sisoul dao config --set-pip-registry 0x...`"
        )

    # calldata: setStatus(uint256, uint8) selector = keccak256("setStatus(uint256,uint8)")[:4]
    # 这里不用 web3 ABI encode (mock 友好), 手动拼.
    selector = hashlib.sha3_256(b"setStatus(uint256,uint8)").hexdigest()[:8]
    pid_hex = f"{pip_id:064x}"
    status_hex = f"{new_status_int:064x}"
    calldata = "0x" + selector + pid_hex + status_hex

    description = (
        f"sisoul DAO proposal: promote PIP-{pip_id:03d} to status={next_status}\n"
        f"target=PIPRegistry({addr})\n"
        f"new_status_enum={new_status_int}"
    )
    return client.propose(
        targets=[addr],
        values=[0],
        calldatas=[calldata],
        description=description,
    )


# ── 模块函数式 API (CLI / daemon 简洁调用) ───────────────────────────────────


def propose(
    targets: list[str],
    values: list[int],
    calldatas: list[str],
    description: str,
    config: DAOConfig | None = None,
) -> ProposalSummary:
    return GovernorClient(config).propose(targets, values, calldatas, description)


def cast_vote(
    proposal_id: int, support: int | str, config: DAOConfig | None = None
) -> str:
    return GovernorClient(config).cast_vote(proposal_id, support)


def proposal_state(proposal_id: int, config: DAOConfig | None = None) -> ProposalState:
    return GovernorClient(config).state(proposal_id)


def proposal_votes(
    proposal_id: int, config: DAOConfig | None = None
) -> tuple[int, int, int]:
    return GovernorClient(config).proposal_votes(proposal_id)


__all__ = [
    "DEFAULT_DAO_CONFIG",
    "DAOMode",
    "DAOConfig",
    "DAOError",
    "ProposalNotFoundError",
    "Web3NotInstalledError",
    "ProposalState",
    "PROPOSAL_STATE_NAMES",
    "SUPPORT_AGAINST",
    "SUPPORT_FOR",
    "SUPPORT_ABSTAIN",
    "SUPPORT_MAP",
    "ProposalSummary",
    "GovernorClient",
    "load_dao_config",
    "save_dao_config",
    "propose",
    "cast_vote",
    "proposal_state",
    "proposal_votes",
    "propose_pip_promotion",
]

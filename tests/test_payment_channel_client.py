"""M4 支付通道客户端测试 (2026-06-11).

两层:
1. 纯单元 (无链): EIP-712 收据 sign → 本地 recover 回签名地址, 证明结构自洽。
2. anvil 端到端 (有 anvil/forge 才跑): 真部署 MockUSDC + PaymentChannel,
   borrower open+sign (Python eth_account), payee close (传 Python 签名) →
   **证明 Python 签的收据被 Solidity 合约 verify 接受** + 97/3 分账数值正确。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from sisoul.friend.payment_channel import (
    EIP712_DOMAIN_NAME,
    build_receipt_message,
    recover_receipt_signer,
    sign_receipt,
)

# anvil 内置确定性账户 (公开测试私钥, 非任何真实凭据)。
ANVIL_KEYS = {
    "deployer": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "borrower": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "payee": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
}

CONTRACTS_OUT = Path(__file__).resolve().parent.parent / "contracts" / "out"


# ── 1. 纯单元: 签名结构自洽 (无链) ───────────────────────────────────────────


class TestReceiptSignatureUnit:
    def test_sign_then_recover_roundtrip(self) -> None:
        """Python 签的收据, 本地 recover 出来 == 签名者地址。"""
        from eth_account import Account

        acct = Account.from_key(ANVIL_KEYS["borrower"])
        channel_id = bytes.fromhex("11" * 32)
        sig = sign_receipt(
            private_key=ANVIL_KEYS["borrower"],
            channel_id=channel_id, cumulative_amount=600_000_000,
            chain_id=10, verifying_contract="0x" + "ab" * 20,
        )
        assert len(sig) == 65
        recovered = recover_receipt_signer(
            signature=sig, channel_id=channel_id, cumulative_amount=600_000_000,
            chain_id=10, verifying_contract="0x" + "ab" * 20,
        )
        assert recovered.lower() == acct.address.lower()

    def test_amount_change_changes_signature(self) -> None:
        """cumulativeAmount 变 → 签名变 (递增收据每张不同)。"""
        ch = bytes.fromhex("22" * 32)
        common = dict(channel_id=ch, chain_id=10, verifying_contract="0x" + "cd" * 20)
        s1 = sign_receipt(private_key=ANVIL_KEYS["borrower"], cumulative_amount=100, **common)
        s2 = sign_receipt(private_key=ANVIL_KEYS["borrower"], cumulative_amount=200, **common)
        assert s1 != s2

    def test_domain_name_locked(self) -> None:
        msg = build_receipt_message(
            channel_id=bytes(32), cumulative_amount=1, chain_id=10,
            verifying_contract="0x" + "00" * 20,
        )
        assert msg["domain"]["name"] == EIP712_DOMAIN_NAME == "SisoulPaymentChannel"
        assert msg["primaryType"] == "Receipt"

    def test_bad_channel_id_length_rejected(self) -> None:
        with pytest.raises(Exception):
            build_receipt_message(
                channel_id=b"\x00" * 31, cumulative_amount=1, chain_id=10,
                verifying_contract="0x" + "00" * 20,
            )


# ── 2. anvil 端到端 (skip 无 anvil) ──────────────────────────────────────────


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _load_artifact(name: str, sol_file: str) -> tuple[list, str]:
    p = CONTRACTS_OUT / sol_file / f"{name}.json"
    d = json.loads(p.read_text())
    return d["abi"], d["bytecode"]["object"]


anvil_required = pytest.mark.skipif(
    shutil.which("anvil") is None
    or not (CONTRACTS_OUT / "PaymentChannel.sol" / "PaymentChannel.json").exists(),
    reason="anvil 或 forge out/ 产物不可用 (CI 无 foundry 时跳过)",
)


@pytest.fixture()
def anvil_chain():
    port = _free_port()
    proc = subprocess.Popen(
        ["anvil", "--port", str(port), "--silent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    rpc = f"http://127.0.0.1:{port}"
    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(rpc))
        for _ in range(50):
            try:
                if w3.is_connected():
                    break
            except Exception:
                pass
            time.sleep(0.1)
        assert w3.is_connected(), "anvil 没起来"
        yield rpc, w3
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _deploy(w3, deployer_key, abi, bytecode, *args):
    from eth_account import Account

    acct = Account.from_key(deployer_key)
    c = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = c.constructor(*args).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": w3.eth.chain_id,
    })
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    return w3.eth.contract(address=rcpt["contractAddress"], abi=abi)


@anvil_required
class TestPaymentChannelOnAnvil:
    def test_python_signed_receipt_accepted_by_solidity_and_splits_97_3(
        self, anvil_chain
    ) -> None:
        """核心: Python eth_account 签的收据被 Solidity close 接受 + 97/3 分账。"""
        from eth_account import Account
        from sisoul.friend.payment_channel import PaymentChannelClient

        rpc, w3 = anvil_chain
        deployer = Account.from_key(ANVIL_KEYS["deployer"])
        borrower = Account.from_key(ANVIL_KEYS["borrower"])
        payee = Account.from_key(ANVIL_KEYS["payee"])

        usdc_abi, usdc_bc = _load_artifact("MockUSDC", "PaymentChannel.t.sol")
        pc_abi, pc_bc = _load_artifact("PaymentChannel", "PaymentChannel.sol")

        usdc = _deploy(w3, ANVIL_KEYS["deployer"], usdc_abi, usdc_bc)
        # fee 收款 = deployer; feeBps=300
        pc = _deploy(w3, ANVIL_KEYS["deployer"], pc_abi, pc_bc, deployer.address, 300)

        deposit = 1_000_000_000  # 1000 USDC (6 decimals)
        cumulative = 600_000_000  # 600 USDC 已授权付给 payee

        # mint USDC 给 borrower + borrower approve PaymentChannel
        def _send(acct, fn):
            tx = fn.build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": w3.eth.chain_id,
            })
            s = acct.sign_transaction(tx)
            return w3.eth.wait_for_transaction_receipt(
                w3.eth.send_raw_transaction(s.raw_transaction), timeout=60
            )

        _send(deployer, usdc.functions.mint(borrower.address, deposit))
        _send(borrower, usdc.functions.approve(pc.address, deposit))

        fee_before = usdc.functions.balanceOf(deployer.address).call()
        payee_before = usdc.functions.balanceOf(payee.address).call()
        borrower_before = usdc.functions.balanceOf(borrower.address).call()

        # borrower 用客户端 open 通道
        client = PaymentChannelClient(
            rpc_url=rpc, contract_address=pc.address,
            private_key=ANVIL_KEYS["borrower"], abi=pc_abi,
        )
        expiry = w3.eth.get_block("latest")["timestamp"] + 3600
        channel_id = client.open_channel(
            payee=payee.address, token=usdc.address, deposit=deposit, expiry=expiry
        )
        assert len(channel_id) == 32
        st = client.read_channel(channel_id=channel_id)
        assert st.state == 1 and st.deposit == deposit  # Open

        # borrower 签递增收据 (Python eth_account EIP-712)
        payer_sig = client.sign_receipt(channel_id=channel_id, cumulative_amount=cumulative)

        # payee 拿收据 close 结算 — 若 Python 签名 Solidity 不认会 revert InvalidSignature
        payee_client = PaymentChannelClient(
            rpc_url=rpc, contract_address=pc.address,
            private_key=ANVIL_KEYS["payee"], abi=pc_abi,
        )
        rcpt = payee_client.close_channel(
            channel_id=channel_id, cumulative_amount=cumulative, payer_sig=payer_sig
        )
        assert rcpt["status"] == 1  # close 没 revert = Python 签名被合约接受 ✅

        # 分账: payee 97% / fee 3% / borrower 退回 deposit-cumulative
        fee_after = usdc.functions.balanceOf(deployer.address).call()
        payee_after = usdc.functions.balanceOf(payee.address).call()
        borrower_after = usdc.functions.balanceOf(borrower.address).call()

        assert payee_after - payee_before == 582_000_000   # 600 × 97%
        assert fee_after - fee_before == 18_000_000        # 600 × 3%
        # borrower 快照在 open 前 (满额 1000); 锁 1000 → 退 400, 净付 = cumulative 600
        assert borrower_before - borrower_after == 600_000_000
        assert borrower_after == 400_000_000
        # 合约里 USDC 清零
        assert usdc.functions.balanceOf(pc.address).call() == 0
        # 通道状态 = Closed
        assert client.read_channel(channel_id=channel_id).state == 2

    def test_cancel_after_expiry_refunds_payer(self, anvil_chain) -> None:
        """payee 不结算 → expiry 后 borrower cancel 拿回全额。"""
        from eth_account import Account
        from sisoul.friend.payment_channel import PaymentChannelClient

        rpc, w3 = anvil_chain
        deployer = Account.from_key(ANVIL_KEYS["deployer"])
        borrower = Account.from_key(ANVIL_KEYS["borrower"])
        payee = Account.from_key(ANVIL_KEYS["payee"])

        usdc_abi, usdc_bc = _load_artifact("MockUSDC", "PaymentChannel.t.sol")
        pc_abi, pc_bc = _load_artifact("PaymentChannel", "PaymentChannel.sol")
        usdc = _deploy(w3, ANVIL_KEYS["deployer"], usdc_abi, usdc_bc)
        pc = _deploy(w3, ANVIL_KEYS["deployer"], pc_abi, pc_bc, deployer.address, 300)

        deposit = 500_000_000

        def _send(acct, fn):
            tx = fn.build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": w3.eth.chain_id,
            })
            s = acct.sign_transaction(tx)
            return w3.eth.wait_for_transaction_receipt(
                w3.eth.send_raw_transaction(s.raw_transaction), timeout=60
            )

        _send(deployer, usdc.functions.mint(borrower.address, deposit))
        _send(borrower, usdc.functions.approve(pc.address, deposit))

        client = PaymentChannelClient(
            rpc_url=rpc, contract_address=pc.address,
            private_key=ANVIL_KEYS["borrower"], abi=pc_abi,
        )
        expiry = w3.eth.get_block("latest")["timestamp"] + 100
        channel_id = client.open_channel(
            payee=payee.address, token=usdc.address, deposit=deposit, expiry=expiry
        )
        before = usdc.functions.balanceOf(borrower.address).call()
        # 推进时间过 expiry
        w3.provider.make_request("evm_increaseTime", [200])
        w3.provider.make_request("evm_mine", [])
        client.cancel_channel(channel_id=channel_id)
        after = usdc.functions.balanceOf(borrower.address).call()
        assert after - before == deposit  # 全额退回
        assert client.read_channel(channel_id=channel_id).state == 3  # Cancelled

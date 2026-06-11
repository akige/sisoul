"""sisoul · M4 支付通道客户端 (2026-06-11).

把 borrow 的"陌生人付费怕跑路"接进链上 PaymentChannel 状态通道:

    借入方 open 通道锁 USDC → 借出方流式回 token, 借入方签一张递增金额的
    EIP-712 收据 → 任一方拿最后一张双签状态上链 close 结算, 合约自动 97/3 分账
    (97% 给借出方, 3% protocol fee)。双方最大损失钳死在 1 个 chunk。

合约 (OP 主网真部署): 0x3aa396d31872f87cf6269c65cf59bc00d820a19f
EIP-712 domain: name="SisoulPaymentChannel" version="1" (chainId + verifyingContract 运行时填)
Receipt type: Receipt(bytes32 channelId,uint256 cumulativeAmount)

本模块**只在真要链上结算时**才 import web3/eth_account (borrow 核心不硬依赖链)。
纯本地 borrow (gift/kudos) 完全不碰本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# OP 主网真部署地址 (2026-06-11, 见 contracts/DEPLOYMENTS.md)。
MAINNET_PAYMENT_CHANNEL = "0x3aa396d31872f87cf6269c65cf59bc00d820a19f"
OP_MAINNET_CHAIN_ID = 10

# EIP-712 收据类型 (必须跟合约 RECEIPT_TYPEHASH 逐字节一致, 否则 close revert)。
EIP712_DOMAIN_NAME = "SisoulPaymentChannel"
EIP712_DOMAIN_VERSION = "1"
RECEIPT_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Receipt": [
        {"name": "channelId", "type": "bytes32"},
        {"name": "cumulativeAmount", "type": "uint256"},
    ],
}

# 最小 ABI: 只含客户端要调的函数/事件 (够用, 不依赖 forge out/ 存在)。
PAYMENT_CHANNEL_ABI: list[dict] = [
    {
        "type": "function", "name": "open", "stateMutability": "nonpayable",
        "inputs": [
            {"name": "payee", "type": "address"},
            {"name": "token", "type": "address"},
            {"name": "deposit", "type": "uint256"},
            {"name": "expiry", "type": "uint64"},
        ],
        "outputs": [{"name": "channelId", "type": "bytes32"}],
    },
    {
        "type": "function", "name": "close", "stateMutability": "nonpayable",
        "inputs": [
            {"name": "channelId", "type": "bytes32"},
            {"name": "cumulativeAmount", "type": "uint256"},
            {"name": "payerSig", "type": "bytes"},
        ],
        "outputs": [],
    },
    {
        "type": "function", "name": "cancel", "stateMutability": "nonpayable",
        "inputs": [{"name": "channelId", "type": "bytes32"}],
        "outputs": [],
    },
    {
        "type": "function", "name": "getChannel", "stateMutability": "view",
        "inputs": [{"name": "channelId", "type": "bytes32"}],
        "outputs": [{
            "name": "", "type": "tuple",
            "components": [
                {"name": "payer", "type": "address"},
                {"name": "payee", "type": "address"},
                {"name": "token", "type": "address"},
                {"name": "deposit", "type": "uint256"},
                {"name": "expiry", "type": "uint64"},
                {"name": "status", "type": "uint8"},
            ],
        }],
    },
    {
        "type": "function", "name": "feeBps", "stateMutability": "view",
        "inputs": [], "outputs": [{"name": "", "type": "uint16"}],
    },
    {
        "type": "event", "name": "ChannelOpened", "anonymous": False,
        "inputs": [
            {"name": "channelId", "type": "bytes32", "indexed": True},
            {"name": "payer", "type": "address", "indexed": True},
            {"name": "payee", "type": "address", "indexed": True},
            {"name": "token", "type": "address", "indexed": False},
            {"name": "deposit", "type": "uint256", "indexed": False},
            {"name": "expiry", "type": "uint64", "indexed": False},
        ],
    },
    {
        "type": "event", "name": "ChannelClosed", "anonymous": False,
        "inputs": [
            {"name": "channelId", "type": "bytes32", "indexed": True},
            {"name": "cumulativeAmount", "type": "uint256", "indexed": False},
            {"name": "paidToPayee", "type": "uint256", "indexed": False},
            {"name": "feeAmount", "type": "uint256", "indexed": False},
            {"name": "refundToPayer", "type": "uint256", "indexed": False},
        ],
    },
]


class PaymentChannelError(Exception):
    """支付通道客户端错误。"""


@dataclass
class ChannelState:
    payer: str
    payee: str
    token: str
    deposit: int
    expiry: int
    state: int  # 0=None 1=Open 2=Closed 3=Cancelled


def build_receipt_message(
    *, channel_id: bytes, cumulative_amount: int, chain_id: int, verifying_contract: str
) -> dict:
    """构造 EIP-712 Receipt 全消息 (eth_account.sign_typed_data 的 full_message)。

    channel_id 必须是 32 字节 bytes; cumulative_amount 是 uint256 int。
    这份结构跟合约 _hashTypedDataV4(abi.encode(RECEIPT_TYPEHASH, channelId, amount))
    必须等价, 否则合约 close 时 ECDSA.recover 出来的地址 ≠ payer → InvalidSignature。
    """
    if len(channel_id) != 32:
        raise PaymentChannelError(f"channel_id 必须 32 字节, 得到 {len(channel_id)}")
    return {
        "types": RECEIPT_TYPES,
        "primaryType": "Receipt",
        "domain": {
            "name": EIP712_DOMAIN_NAME,
            "version": EIP712_DOMAIN_VERSION,
            "chainId": chain_id,
            "verifyingContract": verifying_contract,
        },
        "message": {
            "channelId": channel_id,
            "cumulativeAmount": cumulative_amount,
        },
    }


def sign_receipt(
    *, private_key: str, channel_id: bytes, cumulative_amount: int,
    chain_id: int, verifying_contract: str,
) -> bytes:
    """payer 用私钥对 (channelId, cumulativeAmount) 签 EIP-712 收据, 返 65 字节签名。

    这是借入方流式付费的核心: 每收一个 chunk, cumulative_amount 递增, 重签一张。
    借出方拿最后一张去 close 结算。
    """
    from eth_account import Account

    msg = build_receipt_message(
        channel_id=channel_id, cumulative_amount=cumulative_amount,
        chain_id=chain_id, verifying_contract=verifying_contract,
    )
    signed = Account.sign_typed_data(private_key, full_message=msg)
    return bytes(signed.signature)


def recover_receipt_signer(
    *, signature: bytes, channel_id: bytes, cumulative_amount: int,
    chain_id: int, verifying_contract: str,
) -> str:
    """本地从收据签名恢复 payer 地址 (不上链就能验签名结构, 测试/防伪用)。"""
    from eth_account import Account

    msg = build_receipt_message(
        channel_id=channel_id, cumulative_amount=cumulative_amount,
        chain_id=chain_id, verifying_contract=verifying_contract,
    )
    return Account.recover_message(_encode(msg), signature=signature)


def _encode(full_message: dict) -> Any:
    from eth_account.messages import encode_typed_data

    return encode_typed_data(full_message=full_message)


class PaymentChannelClient:
    """连已部署 PaymentChannel 合约的链上客户端 (borrower 或 payee 端)。

    生产用 OP 主网 (MAINNET_PAYMENT_CHANNEL); 测试用 anvil 部署的实例。
    """

    def __init__(
        self, *, rpc_url: str, contract_address: str, private_key: str,
        abi: Optional[list] = None,
    ) -> None:
        from web3 import Web3
        from eth_account import Account

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise PaymentChannelError(f"RPC 连不上: {rpc_url}")
        self.acct = Account.from_key(private_key)
        self.address = self.acct.address
        self.chain_id = self.w3.eth.chain_id
        self.contract_address = self.w3.to_checksum_address(contract_address)
        self.contract = self.w3.eth.contract(
            address=self.contract_address, abi=abi or PAYMENT_CHANNEL_ABI
        )

    def _send(self, fn_call: Any) -> Any:
        """build+sign+send 一笔交易, 等 receipt。"""
        tx = fn_call.build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": self.chain_id,
        })
        signed = self.acct.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

    def open_channel(
        self, *, payee: str, token: str, deposit: int, expiry: int
    ) -> bytes:
        """开通道锁 deposit (调用方需先对 token approve 本合约)。返 channelId。"""
        rcpt = self._send(self.contract.functions.open(
            self.w3.to_checksum_address(payee),
            self.w3.to_checksum_address(token),
            int(deposit), int(expiry),
        ))
        logs = self.contract.events.ChannelOpened().process_receipt(rcpt)
        if not logs:
            raise PaymentChannelError("open 成功但没解析到 ChannelOpened 事件")
        return bytes(logs[0]["args"]["channelId"])

    def sign_receipt(self, *, channel_id: bytes, cumulative_amount: int) -> bytes:
        """对本通道签一张递增收据 (借入方每收一个 chunk 重签)。"""
        return sign_receipt(
            private_key=self.acct.key.hex(),
            channel_id=channel_id, cumulative_amount=cumulative_amount,
            chain_id=self.chain_id, verifying_contract=self.contract_address,
        )

    def close_channel(
        self, *, channel_id: bytes, cumulative_amount: int, payer_sig: bytes
    ) -> Any:
        """payee 拿最后一张双签收据 close 结算, 合约自动 97/3 分账。返 tx receipt。"""
        return self._send(self.contract.functions.close(
            channel_id, int(cumulative_amount), payer_sig,
        ))

    def cancel_channel(self, *, channel_id: bytes) -> Any:
        """expiry 后 payer 单方拿回全部 deposit (payee 没结算 = 没服务)。"""
        return self._send(self.contract.functions.cancel(channel_id))

    def read_channel(self, *, channel_id: bytes) -> ChannelState:
        c = self.contract.functions.getChannel(channel_id).call()
        # getChannel 返 Channel struct tuple (payer,payee,token,deposit,expiry,status)
        return ChannelState(
            payer=c[0], payee=c[1], token=c[2], deposit=c[3], expiry=c[4], state=c[5]
        )


__all__ = [
    "MAINNET_PAYMENT_CHANNEL",
    "OP_MAINNET_CHAIN_ID",
    "PAYMENT_CHANNEL_ABI",
    "PaymentChannelClient",
    "PaymentChannelError",
    "ChannelState",
    "build_receipt_message",
    "sign_receipt",
    "recover_receipt_signer",
]

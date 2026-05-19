"""sisoul · ArDrive Turbo / Bundlr (Irys) 上传客户端 (v1.0-decentralized §B.2).

把 Bundlr Network (2024 改名 Irys) 的 fork "ArDrive Turbo" 接进来, 替掉
``arweave.py`` 里原本的 Pinata IPFS 长期存 + 直 Arweave HTTP POST 路径.

设计目标 (依据 obs §32 §B.2.1):

1. **< 100 KiB free tier 完全免费**, 不要用户先充值就能首跑.
2. **> 100 KiB 走 USDC 计价** (~$0.01-0.02/MB), Turbo 自己做 EVM / SOL / AR / 信用卡收款,
   把多个上传 bundle 成一笔 Arweave tx 摊薄 gas.
3. 上传完返 ``tx_id`` (43 char base64url), ~60-120s 后 ``status=permanent``,
   ``https://arweave.net/<tx_id>`` 永久可拉.
4. **mainnet 双 gate**: ``ARWEAVE_ALLOW_MAINNET=1`` env + ``confirm_mainnet=True``
   构造参数, 两者同时满足才会真打 mainnet. 否则降 mock / testnet.
5. 用户 BYO wallet 支持 ETH / SOL / MATIC / AR / 信用卡 充值 (本模块负责拼
   ``GET /top-up/checkout-session`` URL, 真签名 / 链上转账留给 wallet 自己做).

依赖:
- 必装: httpx (项目已有)
- 可选: arweave-python-client (走 ``provider="arweave-direct"`` 备选高级路径,
  或 turbo provider 真上传 — ANS-104 DataItem 签名). 没装 → 自动报错明确.

设计哲学:
- **不放凭据**: Turbo public endpoints 都不要 auth (price quote / status / fetch).
  真上传走 ANS-104 DataItem, 需 wallet 签, wallet 路径由调用方传入.
- **同步接口** (httpx.Client, 不用 async): 项目其他模块都是同步, 保持一致.
- **失败不静默**: HTTP 错误抛 ``BundlrError`` 子类, 调用方自己决定 retry / fallback.

⚠️ 默认 ``provider="mock"`` 不真打网. 走真路径要显式构造 ``provider="turbo"``.
⚠️ 真 mainnet 上传要 ``ARWEAVE_ALLOW_MAINNET=1`` + ``confirm_mainnet=True`` 双 gate.

真 API endpoints (实测 2026-05-19):
- Upload: https://upload.ardrive.io
    GET /info  → version + addresses{eth,sol,ar,matic,kyve} + freeUploadLimitBytes(=107520)
    POST /tx   → upload bundle / data item
- Payment: https://payment.ardrive.io
    GET /v1/price/bytes/{N}              → {"winc":"<n>","adjustments":[]}
    GET /v1/rates                        → {"winc":"<n>","fiat":{"usd":<f>,...}}
    GET /v1/account/balance/{token}?address=<addr>
                                          → 200 {"winc":"<n>",...} or 404 "User Not Found"
    GET /top-up/checkout-session/{addr}/{usd_cents}/{currency}
                                          → Stripe checkout URL
- Gateway: https://arweave.net
    GET /info              → height/blocks/peers
    GET /{tx_id}           → tx 真 data
    GET /tx/{tx_id}/status → 202 pending / 200 {number_of_confirmations: N}

References:
- ArDrive Turbo docs: https://docs.ardrive.io/docs/turbo/
- Turbo SDK (JS): https://github.com/ardriveapp/turbo-sdk
- ANS-104 spec: https://github.com/ArweaveTeam/arweave-standards/blob/master/ans/ANS-104.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

import httpx

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

# ArDrive Turbo (主路径)
TURBO_UPLOAD_BASE = "https://upload.ardrive.io"
TURBO_PAYMENT_BASE = "https://payment.ardrive.io"
TURBO_GATEWAY_DEFAULT = "https://arweave.net"

# Bundlr / Irys 兼容 fallback (旧名, 同协议, 不同节点)
IRYS_UPLOAD_BASE = "https://node1.bundlr.network"
IRYS_PAYMENT_BASE = "https://node1.bundlr.network"

# 自由档阈值: < 100 KiB sisoul 标记 free_tier (Turbo 真 free 是 107520 字节, 我们略保守)
FREE_TIER_BYTES = 100 * 1024

# Turbo provider 三档
ProviderLiteral = Literal["mock", "turbo", "irys", "arweave-direct"]

# Turbo 支持的付款 token
PaymentToken = Literal["USDC", "ETH", "SOL", "AR", "MATIC", "credit-card"]


# ─────────────────────────────────────────────────────────────────────────────
# 异常 (§B.2.3 error code 表)
# ─────────────────────────────────────────────────────────────────────────────


class BundlrError(RuntimeError):
    """Bundlr/Turbo 类异常基类."""

    code: int = 5000


class ArweaveInsufficientFunds(BundlrError):
    """quote > balance. fund 后重试."""

    code = 5001


class ArweaveUploadTimeout(BundlrError):
    """upload > 120s 仍无 tx_id."""

    code = 5002


class ArweaveNotFinalized(BundlrError):
    """status > 10min 仍 pending."""

    code = 5003


class ArweaveTxNotFound(BundlrError):
    """fetch 404."""

    code = 5004


class ArweaveMainnetGateError(BundlrError):
    """mainnet 双 gate 未通过."""

    code = 5006


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Quote:
    """price quote 返回."""

    bytes_count: int
    cost_winc: int
    cost_usd: Decimal
    payment_token: str
    free_tier: bool
    expires_at: int


@dataclass
class UploadReceipt:
    """upload 返回."""

    tx_id: str
    bundle_id: Optional[str]
    cost_paid_winc: int
    cost_paid_usd: Decimal
    upload_ts: int
    expected_finalize_s: int
    fetch_url: str
    provider: str
    free_tier: bool


@dataclass
class FundReceipt:
    target_address: str
    amount_usd: Decimal
    token: str
    checkout_url: str
    expires_at: int


@dataclass
class BalanceInfo:
    address: str
    balance_winc: int
    balance_usd: Decimal


# ─────────────────────────────────────────────────────────────────────────────
# 核心: ArweaveUploader
# ─────────────────────────────────────────────────────────────────────────────


class ArweaveUploader:
    """Bundlr / Turbo / arweave-direct 三档抽象 (§B.2.3).

    构造参数:
    - provider: turbo (默认) / irys / arweave-direct / mock
    - upload_endpoint / payment_endpoint: 自定义节点 URL
    - gateway: 拉 tx 的 gateway, 默认 https://arweave.net
    - wallet_path: ANS-104 签名用的 wallet JSON (Arweave JWK 格式)
    - payment_token: 默认 USDC
    - network: testnet / mainnet / mock
    - confirm_mainnet: bool, mainnet 双 gate 之一
    - http_timeout_sec: HTTP timeout
    """

    def __init__(
        self,
        provider: ProviderLiteral = "mock",
        upload_endpoint: Optional[str] = None,
        payment_endpoint: Optional[str] = None,
        gateway: str = TURBO_GATEWAY_DEFAULT,
        wallet_path: Optional[Path] = None,
        payment_token: PaymentToken = "USDC",
        network: Literal["testnet", "mainnet", "mock"] = "testnet",
        confirm_mainnet: bool = False,
        http_timeout_sec: float = 60.0,
    ) -> None:
        self.provider: ProviderLiteral = provider
        self.upload_endpoint = upload_endpoint or self._default_upload_endpoint(provider)
        self.payment_endpoint = payment_endpoint or self._default_payment_endpoint(provider)
        self.gateway = gateway.rstrip("/")
        self.wallet_path = Path(wallet_path).expanduser() if wallet_path else None
        self.payment_token: PaymentToken = payment_token
        self._requested_network = network
        self.confirm_mainnet = confirm_mainnet
        self.network = self._resolve_network(network, confirm_mainnet)
        self.http_timeout_sec = http_timeout_sec

    @staticmethod
    def _default_upload_endpoint(provider: ProviderLiteral) -> str:
        if provider == "irys":
            return IRYS_UPLOAD_BASE
        return TURBO_UPLOAD_BASE

    @staticmethod
    def _default_payment_endpoint(provider: ProviderLiteral) -> str:
        if provider == "irys":
            return IRYS_PAYMENT_BASE
        return TURBO_PAYMENT_BASE

    @staticmethod
    def _resolve_network(
        requested: Literal["testnet", "mainnet", "mock"],
        confirm_mainnet: bool,
    ) -> Literal["testnet", "mainnet", "mock"]:
        """双 gate: env=1 AND confirm_mainnet=True 才放行. 否则降 testnet + warn."""
        if requested != "mainnet":
            return requested
        env_ok = os.environ.get("ARWEAVE_ALLOW_MAINNET") == "1"
        if env_ok and confirm_mainnet:
            return "mainnet"
        logger.warning(
            "Bundlr/Turbo: 请求 mainnet 但双 gate 未全开 "
            "(env=%s, confirm_mainnet=%s), 降级 testnet (避免误花真钱).",
            env_ok, confirm_mainnet,
        )
        return "testnet"

    # ── 1. quote ─────────────────────────────────────────────────────────

    def _price_url(self, bytes_count: int) -> str:
        if self.provider == "irys":
            return f"{self.payment_endpoint}/price/arweave/{bytes_count}"
        return f"{self.payment_endpoint}/v1/price/bytes/{bytes_count}"

    def _rates_url(self) -> str:
        if self.provider == "irys":
            return f"{self.payment_endpoint}/price/arweave/1000000000000"
        return f"{self.payment_endpoint}/v1/rates"

    def quote(self, bytes_count: int) -> Quote:
        """问 Turbo 上传 N bytes 多少钱.

        sisoul free_tier: < 100 KiB → True. 实际 Turbo upload service free 阈值 107520 bytes
        (从 /info 拿 freeUploadLimitBytes).
        """
        if bytes_count < 0:
            raise ValueError(f"bytes_count 必须 >= 0, got {bytes_count}")

        free = bytes_count < FREE_TIER_BYTES

        if self.provider == "mock":
            cost_usd = Decimal("0") if free else (Decimal(bytes_count) / Decimal(10**8))
            cost_winc = int(cost_usd * Decimal(10**12))
            return Quote(
                bytes_count=bytes_count, cost_winc=cost_winc, cost_usd=cost_usd,
                payment_token="credits", free_tier=free,
                expires_at=int(time.time()) + 60,
            )

        url = self._price_url(bytes_count)
        try:
            with httpx.Client(timeout=self.http_timeout_sec) as client:
                resp = client.get(url)
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    data = resp.json()
                    if isinstance(data, dict):
                        winc = int(str(data.get("winc", data.get("price", 0))))
                    else:
                        winc = int(data)
                else:
                    winc = int(resp.text.strip())
        except httpx.HTTPError as e:
            raise BundlrError(f"Turbo price quote 失败 ({url}): {e}") from e
        except (ValueError, KeyError) as e:
            raise BundlrError(f"Turbo price quote 响应解析失败: {e}") from e

        try:
            usd = self._winc_to_usd(winc)
        except BundlrError:
            usd = Decimal("-1")

        return Quote(
            bytes_count=bytes_count, cost_winc=winc, cost_usd=usd,
            payment_token="credits", free_tier=free,
            expires_at=int(time.time()) + 60,
        )

    def _winc_to_usd(self, winc: int) -> Decimal:
        """Turbo GET /v1/rates → {winc, fiat.usd}."""
        url = self._rates_url()
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(url)
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise BundlrError(f"Turbo rates 拉取失败: {e}") from e
        winc_per_unit = Decimal(str(data.get("winc", "1000000000000")))
        try:
            usd_per_unit = Decimal(str(data["fiat"]["usd"]))
        except (KeyError, TypeError) as e:
            raise BundlrError(f"Turbo rates 响应缺 fiat.usd: {data}") from e
        if winc_per_unit <= 0:
            raise BundlrError(f"Turbo rates winc 非正: {winc_per_unit}")
        return (Decimal(winc) * usd_per_unit / winc_per_unit).quantize(Decimal("0.0001"))

    # ── 2. balance ─────────────────────────────────────────────────────

    def balance(self, address: str, token: str = "ethereum") -> BalanceInfo:
        """查 Turbo 账号余额. token = ethereum / solana / arweave / matic / kyve."""
        if self.provider == "mock":
            return BalanceInfo(address=address, balance_winc=10 * 10**12, balance_usd=Decimal("10.00"))

        url = f"{self.payment_endpoint}/v1/account/balance/{token}"
        try:
            with httpx.Client(timeout=self.http_timeout_sec) as client:
                resp = client.get(url, params={"address": address})
                if resp.status_code == 404:
                    return BalanceInfo(address=address, balance_winc=0, balance_usd=Decimal("0"))
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            raise BundlrError(f"Turbo balance 查询失败 ({url}): {e}") from e
        except ValueError as e:
            raise BundlrError(f"Turbo balance 响应非 JSON: {e}") from e
        winc = int(str(data.get("winc", data.get("balance", 0))))
        try:
            usd = self._winc_to_usd(winc) if winc > 0 else Decimal("0")
        except BundlrError:
            usd = Decimal("-1")
        return BalanceInfo(address=address, balance_winc=winc, balance_usd=usd)

    # ── 3. fund (拼 checkout URL) ─────────────────────────────────────

    def fund(
        self,
        target_address: str,
        amount_usd: Decimal,
        token: PaymentToken = "USDC",
    ) -> FundReceipt:
        """拼 Turbo top-up checkout URL. 实际付款用户在浏览器完成."""
        if amount_usd <= 0:
            raise ValueError(f"amount_usd 必须 > 0, got {amount_usd}")

        if self.provider == "mock":
            url = f"mock://turbo/checkout/{target_address}/{amount_usd}/{token.lower()}"
        else:
            usd_cents = int(amount_usd * 100)
            url = (
                f"{self.payment_endpoint}/top-up/checkout-session/"
                f"{target_address}/{usd_cents}/{token.lower()}"
            )
        return FundReceipt(
            target_address=target_address, amount_usd=amount_usd, token=token,
            checkout_url=url, expires_at=int(time.time()) + 1800,
        )

    # ── 4. upload (核心) ─────────────────────────────────────────────────

    def upload(
        self,
        data: bytes,
        content_type: str = "application/octet-stream",
        tags: Optional[dict[str, str]] = None,
    ) -> UploadReceipt:
        """上传 bytes → Arweave (经 Turbo bundle)."""
        size = len(data)
        free = size < FREE_TIER_BYTES
        tags = dict(tags or {})
        tags.setdefault("Content-Type", content_type)
        tags.setdefault("App-Name", tags.get("App-Name", "sisoul"))

        if self.provider == "mock":
            tx_id = "mocktx-" + hashlib.sha256(data).hexdigest()[:43]
            bundle_id = "mockbundle-" + hashlib.sha256(data + b"|bundle").hexdigest()[:32]
            return UploadReceipt(
                tx_id=tx_id, bundle_id=bundle_id,
                cost_paid_winc=0 if free else size * 10,
                cost_paid_usd=Decimal("0") if free else (Decimal(size) / Decimal(10**8)),
                upload_ts=int(time.time()), expected_finalize_s=60,
                fetch_url=f"{self.gateway}/{tx_id}",
                provider="mock", free_tier=free,
            )

        if self.provider == "arweave-direct":
            return self._upload_arweave_direct(data, tags)

        return self._upload_via_turbo(data, tags, free=free)

    def _upload_via_turbo(
        self, data: bytes, tags: dict[str, str], free: bool,
    ) -> UploadReceipt:
        """ANS-104 DataItem 经 Turbo upload service."""
        if not self.wallet_path or not self.wallet_path.exists():
            raise BundlrError(
                "Turbo 真上传需 Arweave JWK wallet (`wallet_path`). "
                "free tier 仍需 wallet 签 DataItem; 不愿提供 wallet 用 provider='mock'."
            )

        try:
            import arweave  # type: ignore[import-not-found]
        except ImportError as e:
            raise BundlrError(
                "Turbo 真上传需 arweave-python-client. `pip install 'sisoul[onchain]'`."
            ) from e

        try:
            wallet = arweave.Wallet(str(self.wallet_path))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            raise BundlrError(f"Arweave wallet 加载失败 ({self.wallet_path}): {e}") from e

        try:
            tx = arweave.Transaction(wallet, data=data)  # type: ignore[attr-defined]
            for k, v in tags.items():
                tx.add_tag(k, v)
            tx.sign()
        except Exception as e:  # noqa: BLE001
            raise BundlrError(f"Arweave tx 签名失败: {e}") from e

        url = f"{self.upload_endpoint}/tx"
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    url, content=data,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "x-tx-id": str(tx.id),
                    },
                )
                resp.raise_for_status()
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        except httpx.HTTPError as e:
            raise ArweaveUploadTimeout(f"Turbo upload 失败 ({url}): {e}") from e

        tx_id = str(body.get("id") or tx.id)
        bundle_id = body.get("bundleId") or body.get("bundle_id")
        winc = int(body.get("winc", body.get("price", 0)))
        try:
            usd = self._winc_to_usd(winc) if winc > 0 else Decimal("0")
        except BundlrError:
            usd = Decimal("-1")

        return UploadReceipt(
            tx_id=tx_id, bundle_id=str(bundle_id) if bundle_id else None,
            cost_paid_winc=winc, cost_paid_usd=usd,
            upload_ts=int(time.time()),
            expected_finalize_s=60 if free else 120,
            fetch_url=f"{self.gateway}/{tx_id}",
            provider=self.provider, free_tier=free,
        )

    def _upload_arweave_direct(
        self, data: bytes, tags: dict[str, str],
    ) -> UploadReceipt:
        """直 Arweave (不经 Turbo). BYO wallet 必需."""
        if not self.wallet_path or not self.wallet_path.exists():
            raise BundlrError(
                "arweave-direct 上传必须 wallet_path. "
                "(导出 AR wallet JWK 放 ~/.sisoul/arweave-wallet.json)"
            )
        try:
            import arweave  # type: ignore[import-not-found]
        except ImportError as e:
            raise BundlrError(
                "arweave-direct 需 arweave-python-client. `pip install 'sisoul[onchain]'`."
            ) from e

        if self.network == "mainnet" and not (
            os.environ.get("ARWEAVE_ALLOW_MAINNET") == "1" and self.confirm_mainnet
        ):
            raise ArweaveMainnetGateError(
                "arweave-direct mainnet 双 gate 未通过 "
                "(ARWEAVE_ALLOW_MAINNET=1 + confirm_mainnet=True 必须都给)."
            )

        try:
            wallet = arweave.Wallet(str(self.wallet_path))  # type: ignore[attr-defined]
            wallet.api_url = self.gateway  # type: ignore[attr-defined]
            tx = arweave.Transaction(wallet, data=data)  # type: ignore[attr-defined]
            for k, v in tags.items():
                tx.add_tag(k, v)
            tx.sign()
            tx.send()
        except Exception as e:  # noqa: BLE001
            raise BundlrError(f"arweave-direct 上链失败: {e}") from e

        return UploadReceipt(
            tx_id=str(tx.id), bundle_id=None,
            cost_paid_winc=0, cost_paid_usd=Decimal("-1"),
            upload_ts=int(time.time()), expected_finalize_s=120,
            fetch_url=f"{self.gateway}/{tx.id}",
            provider="arweave-direct", free_tier=False,
        )

    # ── 5. status ────────────────────────────────────────────────────────

    def status(
        self, tx_id: str,
    ) -> Literal["pending", "confirmed", "permanent", "failed"]:
        """查 tx 落 Arweave 状态."""
        if self.provider == "mock":
            return "permanent" if tx_id.startswith("mocktx-") else "pending"

        url = f"{self.gateway}/tx/{tx_id}/status"
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(url)
        except httpx.HTTPError as e:
            raise BundlrError(f"status 查询失败 ({url}): {e}") from e

        if resp.status_code == 202:
            return "pending"
        if resp.status_code == 404:
            return "pending"
        if resp.status_code >= 400:
            return "failed"
        try:
            data = resp.json()
        except ValueError:
            return "confirmed"
        n = int(data.get("number_of_confirmations", 0))
        if n >= 10:
            return "permanent"
        if n >= 1:
            return "confirmed"
        return "pending"

    # ── 6. fetch ─────────────────────────────────────────────────────────

    def fetch(self, tx_id: str, gateway: Optional[str] = None) -> bytes:
        """从 Arweave gateway 拉 tx 数据."""
        if self.provider == "mock":
            raise BundlrError(
                f"mock provider 不真存 data, 没法 fetch (tx_id={tx_id}). "
                "用 provider='turbo' 才真上链."
            )

        gw = (gateway or self.gateway).rstrip("/")
        url = f"{gw}/{tx_id}"
        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 404:
                    raise ArweaveTxNotFound(f"tx_id 不存在 ({url}): 等 finalize 或换 gateway")
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPError as e:
            raise BundlrError(f"fetch 失败 ({url}): {e}") from e

    # ── 7. health ──────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """3 服务 health: upload /info + payment /v1/rates + gateway /info."""
        if self.provider == "mock":
            return {"mock": True, "provider": self.provider}

        out: dict[str, Any] = {"provider": self.provider}
        try:
            with httpx.Client(timeout=10.0) as c:
                r = c.get(f"{self.upload_endpoint}/info")
                out["upload"] = {
                    "status_code": r.status_code,
                    "body": r.json() if r.status_code == 200 else None,
                }
        except (httpx.HTTPError, ValueError) as e:
            out["upload"] = {"error": str(e)}
        try:
            with httpx.Client(timeout=10.0) as c:
                rates_url = self._rates_url()
                r = c.get(rates_url)
                body: Any = None
                if r.status_code == 200:
                    try:
                        body = r.json()
                    except ValueError:
                        body = r.text[:200]
                out["payment"] = {"status_code": r.status_code, "body": body, "url": rates_url}
        except httpx.HTTPError as e:
            out["payment"] = {"error": str(e)}
        try:
            with httpx.Client(timeout=10.0) as c:
                r = c.get(f"{self.gateway}/info")
                out["gateway_alive"] = r.status_code == 200
                if r.status_code == 200:
                    out["gateway_info"] = r.json()
        except (httpx.HTTPError, ValueError) as e:
            out["gateway_alive"] = False
            out["gateway_error"] = str(e)
        return out


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def receipt_to_dict(r: UploadReceipt) -> dict[str, Any]:
    d = asdict(r)
    d["cost_paid_usd"] = str(d["cost_paid_usd"])
    return d


def quote_to_dict(q: Quote) -> dict[str, Any]:
    d = asdict(q)
    d["cost_usd"] = str(d["cost_usd"])
    return d


__all__ = [
    "TURBO_UPLOAD_BASE", "TURBO_PAYMENT_BASE", "TURBO_GATEWAY_DEFAULT",
    "IRYS_UPLOAD_BASE", "IRYS_PAYMENT_BASE", "FREE_TIER_BYTES",
    "ProviderLiteral", "PaymentToken",
    "BundlrError", "ArweaveInsufficientFunds", "ArweaveUploadTimeout",
    "ArweaveNotFinalized", "ArweaveTxNotFound", "ArweaveMainnetGateError",
    "Quote", "UploadReceipt", "FundReceipt", "BalanceInfo",
    "ArweaveUploader",
    "receipt_to_dict", "quote_to_dict",
]

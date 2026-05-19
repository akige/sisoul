"""sisoul · Arweave 月度加密 snapshot (Phase 3 W41-W43 · v1.0-decentralized Wave A #5).

§28 §1.1 模块 12 · §29 §5.1 W41-W43.
v1.0-decentralized §B.2 (obs 32) Wave A agent-2: **替 Pinata pin 长期存为 Bundlr/Turbo 直传 Arweave**.

设计:
公司倒闭 / Mac 硬盘坏 → 用户拿 BIP-39 seed + Arweave tx_id 还原 vault.

流程:
1. 拉 vault dir → 加密 (BIP-39 派生 subkey "arweave") → ZIP
2. 计算 sha256 hash (作 vault_master_key_fingerprint + 内容指纹)
3. IPFS pin (Pinata HTTP API, ~1-5s, 给用户即时反馈) — **保留作 #6 helia hot 路径**
4. **Arweave 上链经 Bundlr/Turbo** (bundlr_turbo.ArweaveUploader): < 100 KiB free tier,
   > 100 KiB ~$0.01-0.02/MB. tx_id 60-120s 内 permanent. 200+ 年存活承诺.
5. 历史 → ~/.sisoul/snapshot_history.json

复活流程:
- restore_from_arweave(tx_id, mnemonic, target):
    - 用 tx_id GET arweave.net (mainnet gateway 永远可读, free) → 拿密文 ZIP
    - mnemonic → derive_subkey("arweave") → 解密 → 解 ZIP → 写 target

依赖:
- 必装: 项目原有 (httpx / pynacl / mnemonic)
- 可选: arweave-python-client (真 mainnet/testnet 上传; 没装 → mock fallback)

⚠️ **mainnet 双 gate**: ``ARWEAVE_ALLOW_MAINNET=1`` env + ``confirm_mainnet=True``
   构造参数, 两个都开才真打 mainnet. 否则降 testnet.

v1.0-decentralized Wave A 改动 (本文件):
- 砍掉旧的 ``upload_to_arweave`` 里 "无 wallet → fake tx" / "无 lib → fake tx" / Pinata 上链段,
  全部改走 ``bundlr_turbo.ArweaveUploader``.
- 保留 ``pin_to_ipfs`` (Pinata IPFS pin, #6 helia hot 路径需要它兜底).
- ``SnapshotRecord`` 加 ``bundle_id`` / ``cost_paid_usd`` / ``fetch_url`` / ``provider`` 字段
  (default None, 向后兼容旧 history.json).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import platform
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import httpx

from sisoul.identity.seed import (
    derive_subkey,
    load_mnemonic_from_file,
    mnemonic_to_master_key,
    verify_mnemonic,
)
from sisoul.onchain.bundlr_turbo import (
    ArweaveUploader,
    BundlrError,
    ProviderLiteral,
    UploadReceipt,
)
from sisoul.vault.encryption import decrypt_bytes, encrypt_bytes

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

# Arweave 派生 subkey purpose tag (跟 vault.encryption "vault" 隔离)
_SNAPSHOT_PURPOSE = "arweave"

# Arweave gateway (默认 testnet)
ARWEAVE_TESTNET_GATEWAY = "https://test.arweave.net"
ARWEAVE_MAINNET_GATEWAY = "https://arweave.net"

# Pinata HTTP API (默认 IPFS pin 选择, 详 dev-C 报告)
PINATA_API_BASE = "https://api.pinata.cloud"

# 历史文件
DEFAULT_HISTORY_PATH = Path.home() / ".sisoul" / "snapshot_history.json"

# 排除路径 (跟 export.py 一致, 不打进 ZIP)
_EXCLUDE_PATTERNS = {
    ".venv",
    "__pycache__",
    ".git",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "Thumbs.db",
}

# 上链网络
NetworkLiteral = Literal["testnet", "mainnet", "mock"]


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SnapshotRecord:
    """单次 snapshot 元数据 (写 snapshot_history.json 一条 entry).

    v1.0-decentralized Wave A 新增 (default None 向后兼容旧 history.json):
    - bundle_id: Bundlr/Turbo bundle id (Turbo upload service 内部 id)
    - cost_paid_usd: 实际扣的 USD (str 保 Decimal 精度; "0" = free tier; "-1" = unknown)
    - fetch_url: https://arweave.net/<tx_id> (cache 起来, 复活时直拉)
    - provider: turbo / irys / arweave-direct / mock
    """

    timestamp: str
    size_bytes: int
    sha256: str
    ipfs_cid: Optional[str] = None
    arweave_tx_id: Optional[str] = None
    vault_master_key_fingerprint: str = ""
    network: str = "testnet"
    status: str = "ok"
    error: Optional[str] = None
    # v1.0-decentralized Wave A 新增 (向后兼容)
    bundle_id: Optional[str] = None
    cost_paid_usd: Optional[str] = None
    fetch_url: Optional[str] = None
    provider: Optional[str] = None


class SnapshotHistory:
    """~/.sisoul/snapshot_history.json 读写 wrapper.

    线程不安全 (单机单 daemon 足够; 多进程并发请加 fcntl).
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or DEFAULT_HISTORY_PATH).expanduser()

    def load(self) -> list[SnapshotRecord]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("snapshot_history.json 读失败 (%s), 当空", e)
            return []
        if not isinstance(data, list):
            return []
        out: list[SnapshotRecord] = []
        for d in data:
            if isinstance(d, dict):
                try:
                    out.append(SnapshotRecord(**{k: d.get(k) for k in SnapshotRecord.__dataclass_fields__}))  # type: ignore[arg-type]
                except TypeError:
                    continue
        return out

    def append(self, record: SnapshotRecord) -> None:
        records = self.load()
        records.append(record)
        self.save(records)

    def save(self, records: list[SnapshotRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False)
        self.path.write_text(payload, encoding="utf-8")

    def find(self, tx_or_cid_or_hash: str) -> Optional[SnapshotRecord]:
        """按 tx_id / IPFS CID / sha256 任意一个查."""
        for r in self.load():
            if r.arweave_tx_id == tx_or_cid_or_hash:
                return r
            if r.ipfs_cid == tx_or_cid_or_hash:
                return r
            if r.sha256 == tx_or_cid_or_hash:
                return r
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 核心: ArweaveSnapshot
# ─────────────────────────────────────────────────────────────────────────────


class ArweaveSnapshot:
    """Arweave + IPFS snapshot client.

    构造参数:
    - mnemonic: BIP-39 12-24 词 seed (用来派生 snapshot 加密 subkey)
                None = 自动从 ~/.sisoul/seed.txt 加载
    - pinata_jwt: Pinata API JWT token (None = 从 env PINATA_JWT 读; 都没有走 mock)
    - arweave_wallet_path: Arweave wallet JSON path (None = 从 env ARWEAVE_WALLET 读;
                          都没有 + 非 mock 网络 → 上链时报错)
    - network: "testnet" / "mainnet" / "mock"
               mainnet 需 env ARWEAVE_ALLOW_MAINNET=1 否则降 testnet + warn
    - history: SnapshotHistory 实例 (None = DEFAULT)
    """

    def __init__(
        self,
        mnemonic: Optional[str] = None,
        pinata_jwt: Optional[str] = None,
        arweave_wallet_path: Optional[Path] = None,
        network: NetworkLiteral = "testnet",
        history: Optional[SnapshotHistory] = None,
        *,
        bundlr_provider: ProviderLiteral = "turbo",
        confirm_mainnet: bool = False,
        uploader: Optional[ArweaveUploader] = None,
    ) -> None:
        self._mnemonic = mnemonic
        self.pinata_jwt = pinata_jwt or os.environ.get("PINATA_JWT")
        self.arweave_wallet_path = (
            Path(arweave_wallet_path).expanduser()
            if arweave_wallet_path
            else (Path(os.environ["ARWEAVE_WALLET"]).expanduser() if os.environ.get("ARWEAVE_WALLET") else None)
        )
        self.network = self._resolve_network(network)
        self.confirm_mainnet = confirm_mainnet
        self.history = history or SnapshotHistory()

        # v1.0-decentralized Wave A: 接 Bundlr/Turbo uploader
        # 网络 = mock → 强制 provider=mock (避免误打 Turbo)
        effective_provider: ProviderLiteral = "mock" if self.network == "mock" else bundlr_provider
        self.bundlr_provider: ProviderLiteral = effective_provider

        if uploader is not None:
            self.uploader = uploader
        else:
            self.uploader = ArweaveUploader(
                provider=effective_provider,
                wallet_path=self.arweave_wallet_path,
                network=self.network,
                confirm_mainnet=confirm_mainnet,
                gateway=ARWEAVE_MAINNET_GATEWAY,
            )

    # ── network gate ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_network(requested: NetworkLiteral) -> NetworkLiteral:
        """mainnet 双重 gate: env ARWEAVE_ALLOW_MAINNET=1 才放行, 否则降 testnet."""
        if requested == "mainnet":
            if os.environ.get("ARWEAVE_ALLOW_MAINNET") == "1":
                return "mainnet"
            logger.warning(
                "请求 mainnet 但 ARWEAVE_ALLOW_MAINNET != '1', 降级 testnet "
                "(避免误花真 AR)."
            )
            return "testnet"
        return requested

    @property
    def gateway(self) -> str:
        if self.network == "mainnet":
            return ARWEAVE_MAINNET_GATEWAY
        # mock 也用 testnet gateway URL (mock 模式不真发请求)
        return ARWEAVE_TESTNET_GATEWAY

    # ── key 派生 ─────────────────────────────────────────────────────────

    def _derive_encryption_key(self) -> bytes:
        """派生 snapshot 加密用 32B subkey.

        优先级: 构造传 mnemonic > env SISOUL_MNEMONIC > ~/.sisoul/seed.txt > 抛错.
        """
        mnemonic = self._mnemonic or os.environ.get("SISOUL_MNEMONIC")
        if not mnemonic:
            try:
                mnemonic = load_mnemonic_from_file()
            except FileNotFoundError as e:
                raise RuntimeError(
                    "无 seed 可派生 snapshot key. 提供 mnemonic 参数 / env "
                    "SISOUL_MNEMONIC / 跑 `sisoul init` 生成 ~/.sisoul/seed.txt"
                ) from e
        if not verify_mnemonic(mnemonic):
            raise ValueError("mnemonic 非合法 BIP-39")
        master = mnemonic_to_master_key(mnemonic)
        return derive_subkey(master, _SNAPSHOT_PURPOSE, index=0)

    @staticmethod
    def _key_fingerprint(key: bytes) -> str:
        return hashlib.sha256(key).hexdigest()[:16]

    # ── 1. snapshot vault: ZIP + 加密 ──────────────────────────────────

    def snapshot_vault(
        self,
        vault_dir: Path,
        encryption_key: Optional[bytes] = None,
    ) -> tuple[bytes, str, str]:
        """加密 + ZIP vault.

        Args:
            vault_dir: vault root.
            encryption_key: 32B key. None = 自动派生.

        Returns:
            (encrypted_blob, sha256_hex, key_fingerprint)

        Raises:
            FileNotFoundError: vault_dir 不存在.
        """
        vault_dir = Path(vault_dir).expanduser()
        if not vault_dir.exists():
            raise FileNotFoundError(f"vault dir 不存在: {vault_dir}")

        key = encryption_key if encryption_key is not None else self._derive_encryption_key()

        # 1. ZIP
        buf = io.BytesIO()
        file_count = 0
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for src in sorted(vault_dir.rglob("*")):
                if not src.is_file():
                    continue
                rel = str(src.relative_to(vault_dir))
                if _should_exclude(rel):
                    continue
                zf.write(src, arcname=f"vault/{rel}")
                file_count += 1
            # 元数据 stub
            zf.writestr(
                "snapshot-meta.json",
                json.dumps(
                    {
                        "schema": "sisoul-snapshot-v1",
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                        "file_count": file_count,
                        "vault_root": str(vault_dir),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        plain_zip = buf.getvalue()

        # 2. 加密
        encrypted = encrypt_bytes(plain_zip, key)

        sha = hashlib.sha256(encrypted).hexdigest()
        return encrypted, sha, self._key_fingerprint(key)

    # ── 2. IPFS pin ────────────────────────────────────────────────────

    def pin_to_ipfs(
        self,
        blob: bytes,
        filename: str = "sisoul-snapshot.enc",
    ) -> Optional[str]:
        """上 Pinata. 返回 IPFS CID. 失败 → None + 日志.

        无 jwt → mock CID (本地 sha256 → fake CID, 仅 dev).
        """
        if not self.pinata_jwt:
            cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
            logger.warning("PINATA_JWT 未设, 返回 mock CID: %s", cid)
            return cid

        url = f"{PINATA_API_BASE}/pinning/pinFileToIPFS"
        headers = {"Authorization": f"Bearer {self.pinata_jwt}"}
        files = {"file": (filename, blob, "application/octet-stream")}
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, files=files)
                resp.raise_for_status()
                data = resp.json()
                cid = data.get("IpfsHash") or data.get("cid")
                if not cid:
                    logger.warning("Pinata 响应缺 IpfsHash: %s", data)
                    return None
                return str(cid)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Pinata pin 失败: %s", e)
            return None

    # ── 3. Arweave 上传 (v1.0-decentralized: 走 Bundlr/Turbo) ──────────

    def upload_to_arweave(self, blob: bytes) -> Optional[str]:
        """上 Arweave (经 Bundlr/Turbo bundle service). 返 tx_id. 失败 → None.

        v1.0-decentralized Wave A 改造: 砍掉旧的 "无 wallet fake / 无 lib fake / direct httpx
        POST" 三档 fallback, 全部走 ``ArweaveUploader``:
        - mock provider: 决定性 fake tx_id ("mocktx-" + sha256[:43])
        - turbo / irys: < 100 KiB free tier 无需 USDC; >= 100 KiB 走付费
        - arweave-direct: BYO wallet 直发, 用户付 AR token
        """
        try:
            receipt = self.upload_with_receipt(blob)
        except BundlrError as e:
            logger.warning("Bundlr/Turbo 上传失败 (%s)", e)
            return None
        return receipt.tx_id

    def upload_with_receipt(self, blob: bytes) -> UploadReceipt:
        """上传并返完整 ``UploadReceipt`` (含 bundle_id / cost / fetch_url)."""
        tags = {
            "App-Name": "sisoul",
            "App-Version": "1.0.0-decentralized",
            "Content-Type": "application/octet-stream",
            "Snapshot-Schema": "sisoul-snapshot-v1",
        }
        return self.uploader.upload(blob, content_type="application/octet-stream", tags=tags)

    # ── 4. 还原 ──────────────────────────────────────────────────────

    def restore_from_arweave(
        self,
        tx_id_or_cid: str,
        target_vault_dir: Path,
        decryption_key: Optional[bytes] = None,
        source: Literal["arweave", "ipfs", "auto"] = "auto",
    ) -> Path:
        """从 Arweave tx_id 或 IPFS CID 还原 vault.

        Args:
            tx_id_or_cid: Arweave tx_id (~43 char base64url) 或 IPFS CID (Qm.../bafy...)
            target_vault_dir: 还原目标. 已存在则 raise.
            decryption_key: 32B key. None = 自动派生 (跟 snapshot 时同 seed).
            source: "auto" 自动判 (CID 以 Qm/bafy 开头 → ipfs; 否则 arweave)

        Returns:
            target_vault_dir (绝对路径).

        Raises:
            FileExistsError: target 存在.
            RuntimeError: 下载失败 / 解密失败.
        """
        target = Path(target_vault_dir).expanduser().resolve()
        if target.exists() and any(target.iterdir()):
            raise FileExistsError(f"target_vault_dir 非空: {target}")

        # 1. 取密文 blob
        if source == "auto":
            if tx_id_or_cid.startswith(("Qm", "bafy", "mockcid-")):
                source = "ipfs"
            else:
                source = "arweave"

        if source == "ipfs":
            blob = self._fetch_ipfs(tx_id_or_cid)
        else:
            blob = self._fetch_arweave(tx_id_or_cid)

        # 2. 解密
        key = decryption_key if decryption_key is not None else self._derive_encryption_key()
        try:
            plain_zip = decrypt_bytes(blob, key)
        except Exception as e:  # nacl.CryptoError
            raise RuntimeError(f"snapshot 解密失败 (key 错? blob 篡改?): {e}") from e

        # 3. 解 ZIP
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(plain_zip), "r") as zf:
            for member in zf.namelist():
                if member.startswith("vault/"):
                    # 去掉前缀 vault/
                    rel = member[len("vault/"):]
                    if not rel:
                        continue
                    dest = target / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(dest, "wb") as out:
                        out.write(src.read())
        return target

    def _fetch_ipfs(self, cid: str) -> bytes:
        """从 IPFS gateway 拉 (Pinata public gateway / ipfs.io)."""
        if cid.startswith("mockcid-"):
            raise RuntimeError(
                f"mockcid 无法真拉; mock 模式仅本地测试用. cid={cid}"
            )
        gateways = [
            f"https://gateway.pinata.cloud/ipfs/{cid}",
            f"https://ipfs.io/ipfs/{cid}",
        ]
        last_err: Exception = RuntimeError("no gateway tried")
        for url in gateways:
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    return resp.content
            except httpx.HTTPError as e:
                last_err = e
                continue
        raise RuntimeError(f"IPFS 拉取失败 (所有 gateway 都失败): {last_err}")

    def _fetch_arweave(self, tx_id: str) -> bytes:
        """从 Arweave gateway 拉 tx data."""
        if tx_id.startswith((
            "mocktx-", "testnet-fake-", "no-lib-fake-", "upload-err-", "mockbundle-",
        )):
            raise RuntimeError(
                f"fake/mock tx_id 无法真拉; 仅本地测试用. tx_id={tx_id}"
            )
        url = f"{self.gateway}/{tx_id}"
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPError as e:
            raise RuntimeError(f"Arweave 拉取失败 ({url}): {e}") from e

    # ── 5. 主流程: now() ─────────────────────────────────────────────

    def snapshot_now(
        self,
        vault_dir: Path,
        upload: Literal["ipfs", "arweave", "both", "none"] = "both",
    ) -> SnapshotRecord:
        """一键 snapshot · 加密 + 上传 + 写 history.

        Args:
            vault_dir: vault root.
            upload: 上传策略.

        Returns:
            写入 history 的 SnapshotRecord.
        """
        encrypted, sha, key_fp = self.snapshot_vault(vault_dir)
        record = SnapshotRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            size_bytes=len(encrypted),
            sha256=sha,
            vault_master_key_fingerprint=key_fp,
            network=self.network,
            status="ok",
        )

        if upload in ("ipfs", "both"):
            cid = self.pin_to_ipfs(encrypted)
            record.ipfs_cid = cid
            if cid is None:
                record.status = "failed"
                record.error = "ipfs pin 失败"

        if upload in ("arweave", "both"):
            try:
                receipt = self.upload_with_receipt(encrypted)
                record.arweave_tx_id = receipt.tx_id
                record.bundle_id = receipt.bundle_id
                record.cost_paid_usd = str(receipt.cost_paid_usd)
                record.fetch_url = receipt.fetch_url
                record.provider = receipt.provider
            except BundlrError as e:
                logger.warning("Bundlr/Turbo 上传失败: %s", e)
                record.status = "failed"
                record.error = (record.error + "; " if record.error else "") + f"arweave 上链失败: {e}"

        self.history.append(record)
        return record

    # ── 波 7 dev-A bug-6: snapshot_now + retry wrapper ──────────────────────

    def snapshot_now_with_retry(
        self,
        vault_dir: Path,
        upload: Literal["ipfs", "arweave", "both", "none"] = "both",
        *,
        max_retries: int = 3,
        base_delay_sec: float = 1.0,
    ) -> SnapshotRecord:
        """snapshot_now + 3 次 exponential backoff retry.

        波 7 dev-A bug-6 修复 (qa-D P2-5): EAS/Arweave 失败 audit 静默丢失风险.
        snapshot_now 自身 catch 上传失败标 status=failed; 此 wrapper 进一步在
        status=failed 时重试整个 snapshot+上传链路.

        重试条件: status='failed' (IPFS/Arweave 上传失败).
        跳过: status='ok' (首次成功).
        退避: base * 2^attempt 加 jitter ±20% (P1-6 #5 edge case).
        """
        import random as _random
        import time as _time

        record: SnapshotRecord | None = None
        for attempt in range(max_retries):
            record = self.snapshot_now(vault_dir, upload=upload)
            if record.status == "ok":
                return record
            if attempt == max_retries - 1:
                break
            base = base_delay_sec * (2 ** attempt)
            delay = base * (1.0 + _random.uniform(-0.2, 0.2))
            _time.sleep(max(0.05, delay))
        # 全失败 — record 含 error, 调用方按 record.status 处理
        assert record is not None
        return record


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _should_exclude(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    for part in parts:
        if part in _EXCLUDE_PATTERNS:
            return True
        for pat in _EXCLUDE_PATTERNS:
            if pat.startswith("*") and part.endswith(pat[1:]):
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# scheduler: launchd / systemd
# ─────────────────────────────────────────────────────────────────────────────


_LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTD/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.sisoul.snapshot.{cadence}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sisoul_bin}</string>
        <string>snapshot</string>
        <string>now</string>
        <string>--upload</string>
        <string>{upload}</string>
    </array>
    <key>StartCalendarInterval</key>
    {calendar_block}
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{log_dir}/sisoul-snapshot.out.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/sisoul-snapshot.err.log</string>
</dict>
</plist>
"""

_SYSTEMD_SERVICE_TEMPLATE = """\
[Unit]
Description=sisoul vault snapshot (encrypted ZIP → IPFS pin + Arweave upload)

[Service]
Type=oneshot
ExecStart={sisoul_bin} snapshot now --upload {upload}
StandardOutput=journal
StandardError=journal
"""

_SYSTEMD_TIMER_TEMPLATE = """\
[Unit]
Description=sisoul snapshot timer ({cadence})

[Timer]
OnCalendar={on_calendar}
Persistent=true

[Install]
WantedBy=timers.target
"""


def _systemd_oncalendar(cadence: str) -> str:
    return {
        "monthly": "monthly",
        "weekly": "weekly",
        "daily": "daily",
    }.get(cadence, "monthly")


def _launchd_calendar_block(cadence: str) -> str:
    # macOS launchd: monthly = Day=1 Hour=3; weekly = Weekday=1 Hour=3
    if cadence == "weekly":
        return (
            "<dict>\n"
            "        <key>Weekday</key><integer>1</integer>\n"
            "        <key>Hour</key><integer>3</integer>\n"
            "        <key>Minute</key><integer>0</integer>\n"
            "    </dict>"
        )
    if cadence == "daily":
        return (
            "<dict>\n"
            "        <key>Hour</key><integer>3</integer>\n"
            "        <key>Minute</key><integer>0</integer>\n"
            "    </dict>"
        )
    # monthly (default)
    return (
        "<dict>\n"
        "        <key>Day</key><integer>1</integer>\n"
        "        <key>Hour</key><integer>3</integer>\n"
        "        <key>Minute</key><integer>0</integer>\n"
        "    </dict>"
    )


def schedule_monthly_snapshot(
    cadence: Literal["monthly", "weekly", "daily", "never"] = "monthly",
    upload: Literal["ipfs", "arweave", "both"] = "both",
    sisoul_bin: Optional[str] = None,
    install: bool = False,
    target_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """生成 launchd plist (macOS) 或 systemd unit (Linux), 可选 install.

    Args:
        cadence: monthly / weekly / daily / never (never = 删现有 unit)
        upload: 上传模式
        sisoul_bin: sisoul 可执行路径, None = 自动取 sys.argv[0] 同级 venv bin
        install: True = 真写到 ~/Library/LaunchAgents/ 或 ~/.config/systemd/user/
                 (不真 launchctl load / systemctl enable, 让用户手动确认)
        target_dir: install=True 时, 自定义安装目标 dir (覆盖默认; 测试用)

    Returns:
        dict {"system": "darwin"/"linux"/"unsupported", "unit_text": str,
              "install_path": Path or None, "installed": bool, "cadence": ...}

    注: 不真 launchctl load / systemctl enable, 避免 subagent 改 Mac 真用户 launchd
        (CLAUDE.md mac-protect 红线). 用户拿 plist 文本自己 launchctl load.
    """
    sisoul_bin = sisoul_bin or "sisoul"
    system = platform.system().lower()

    out: dict[str, Any] = {
        "system": system,
        "cadence": cadence,
        "upload": upload,
        "unit_text": "",
        "install_path": None,
        "installed": False,
    }

    if cadence == "never":
        out["unit_text"] = "# never: 不生成 schedule unit"
        return out

    if system == "darwin":
        log_dir = str((Path.home() / "Library" / "Logs" / "sisoul").resolve())
        unit = _LAUNCHD_PLIST_TEMPLATE.format(
            cadence=cadence,
            sisoul_bin=sisoul_bin,
            upload=upload,
            calendar_block=_launchd_calendar_block(cadence),
            log_dir=log_dir,
        )
        out["unit_text"] = unit
        if install:
            install_dir = Path(target_dir) if target_dir else Path.home() / "Library" / "LaunchAgents"
            install_dir.mkdir(parents=True, exist_ok=True)
            plist_path = install_dir / f"io.sisoul.snapshot.{cadence}.plist"
            plist_path.write_text(unit, encoding="utf-8")
            out["install_path"] = plist_path
            out["installed"] = True
        return out

    if system == "linux":
        service = _SYSTEMD_SERVICE_TEMPLATE.format(sisoul_bin=sisoul_bin, upload=upload)
        timer = _SYSTEMD_TIMER_TEMPLATE.format(
            cadence=cadence,
            on_calendar=_systemd_oncalendar(cadence),
        )
        out["unit_text"] = service + "\n# ── timer ──\n" + timer
        if install:
            install_dir = Path(target_dir) if target_dir else Path.home() / ".config" / "systemd" / "user"
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "sisoul-snapshot.service").write_text(service, encoding="utf-8")
            (install_dir / "sisoul-snapshot.timer").write_text(timer, encoding="utf-8")
            out["install_path"] = install_dir
            out["installed"] = True
        return out

    out["system"] = "unsupported"
    out["unit_text"] = f"# 不支持的系统: {system}"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# v1.0-decentralized #4 (Helios light client) hook · Wave A agent-1 · 2026-05-19
# ─────────────────────────────────────────────────────────────────────────────
# Arweave gateway 本身是 web2 (arweave.net HTTP GET / Bundlr Turbo REST), 跟 EVM
# Helios light client 关系弱 — Arweave 共识层不在 helios 0.11.1 支持矩阵. 但本 wave
# 加 hook 给未来 (v1.1+) Bundlr/Turbo 收 USDC on Optimism Sepolia 走 helios verify
# tx receipt 用: 替代信任 Turbo API 自报 "已收到".


def ensure_eth_payment_via_helios(
    chain: str,
    tx_hash: str,
    *,
    required_recipient: str | None = None,
) -> bool:
    """通过 helios trustless verify EVM 支付 tx receipt (v1.1+ Bundlr/Turbo USDC 付费).

    本 wave (Wave A): 接口骨架. 当 helios 已 in_sync 且 receipt 校验通过 → 返 True;
    helios 缺失 / chain 不支持 / receipt 不匹配 → False (不抛, 让调用方决定退化策略).
    """
    try:
        from sisoul.rpc.helios_client import get_default_client
    except ImportError:
        logger.debug("sisoul.rpc 不可用, ensure_eth_payment_via_helios 返 False")
        return False
    client = get_default_client()
    if client is None:
        return False
    status = client.status(chain)
    if isinstance(status, dict) or not status.in_sync:
        logger.warning("helios %s 未 in_sync / 未注册, eth_payment verify 跳过", chain)
        return False
    try:
        receipt = client.call_sync(chain, "eth_getTransactionReceipt", [tx_hash])
    except Exception as e:  # noqa: BLE001
        logger.warning("helios eth_getTransactionReceipt(%s) 失败: %s", tx_hash, e)
        return False
    if not receipt:
        return False
    if receipt.get("status") != "0x1":
        return False
    if required_recipient and (receipt.get("to") or "").lower() != required_recipient.lower():
        return False
    return True


__all__ = [
    "ArweaveSnapshot",
    "SnapshotRecord",
    "SnapshotHistory",
    "schedule_monthly_snapshot",
    "ARWEAVE_TESTNET_GATEWAY",
    "ARWEAVE_MAINNET_GATEWAY",
    "PINATA_API_BASE",
    "DEFAULT_HISTORY_PATH",
    # v1.0-decentralized #4 Helios hook (Wave A agent-1)
    "ensure_eth_payment_via_helios",
]

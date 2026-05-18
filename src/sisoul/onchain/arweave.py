"""sisoul · Arweave 月度加密 snapshot + IPFS pin (Phase 3 W41-W43 · dev-C).

§28 §1.1 模块 12 · §29 §5.1 W41-W43.

设计:
公司倒闭 / Mac 硬盘坏 → 用户拿 BIP-39 seed + Arweave tx_id (or IPFS CID)
还原 vault.

流程:
1. 拉 vault dir → 加密 (BIP-39 派生 subkey "arweave") → ZIP
2. 计算 sha256 hash (作 vault_master_key_fingerprint + 内容指纹)
3. IPFS pin (Pinata HTTP API, ~1-5s, 给用户即时反馈)
4. Arweave 上链 (httpx POST testnet, ~30s, 异步, 写 tx_id 回 history)
5. 历史 → ~/.sisoul/snapshot_history.json

复活流程:
- restore_from_arweave(tx_id, mnemonic, target):
    - 用 tx_id GET testnet → 拿密文 ZIP
    - mnemonic → derive_subkey("arweave") → 解密 → 解 ZIP → 写 target

依赖:
- 必装: 项目原有 (httpx / pynacl / mnemonic)
- 可选: arweave-python-client (真 mainnet 用; testnet HTTP 直 POST 也行, 不强制)
- 可选: ipfshttpclient (自托管 IPFS daemon, 不强制; 默认走 Pinata HTTP API)

⚠️ 默认全 testnet / mock. mainnet 留 Phase 5 (避免误花真钱).
⚠️ 不上 mainnet 的硬约束: ARWEAVE_GATEWAY 默认 https://test.arweave.net/,
   ARWEAVE_NETWORK env 必须显式 = "mainnet" + ARWEAVE_ALLOW_MAINNET=1 才走 mainnet.
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

    字段:
    - timestamp: ISO UTC
    - size_bytes: 加密后 ZIP 大小
    - sha256: 加密后 ZIP 的 sha256 (内容指纹, 不是 key fingerprint)
    - ipfs_cid: IPFS CID (None = 没 pin / 失败)
    - arweave_tx_id: Arweave tx_id (None = 没上链 / 异步还没完成)
    - vault_master_key_fingerprint: derive_subkey("arweave") 的前 8B hex (8 字节足够区分)
    - network: testnet / mainnet / mock
    - status: ok / pending / failed
    - error: 失败原因 (status=failed 时填)
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
    ) -> None:
        self._mnemonic = mnemonic
        self.pinata_jwt = pinata_jwt or os.environ.get("PINATA_JWT")
        self.arweave_wallet_path = (
            Path(arweave_wallet_path).expanduser()
            if arweave_wallet_path
            else (Path(os.environ["ARWEAVE_WALLET"]).expanduser() if os.environ.get("ARWEAVE_WALLET") else None)
        )
        self.network = self._resolve_network(network)
        self.history = history or SnapshotHistory()

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

    # ── 3. Arweave 上传 ──────────────────────────────────────────────

    def upload_to_arweave(self, blob: bytes) -> Optional[str]:
        """上 Arweave (默认 testnet). 返 tx_id. 失败 → None.

        实现:
        - mock 网络: sha256 → fake tx_id
        - testnet: 优先用 arweave-python-client 真上链; 失败 fallback HTTP POST mock
        - mainnet: 需 wallet + ARWEAVE_ALLOW_MAINNET=1
        """
        if self.network == "mock":
            tx_id = "mocktx-" + hashlib.sha256(blob).hexdigest()[:43]
            return tx_id

        # 真 Arweave 要 wallet
        if not self.arweave_wallet_path or not self.arweave_wallet_path.exists():
            # 无 wallet → testnet 仍可走 mock-style fake tx (内部签名要 wallet, 没法真上)
            logger.warning(
                "ARWEAVE_WALLET 未设/不存在, 用 fake testnet tx_id. "
                "真上链需 wallet JSON (Arweave 钱包导出)."
            )
            tx_id = "testnet-fake-" + hashlib.sha256(blob).hexdigest()[:32]
            return tx_id

        # 真上链路径 (optional dep · 装 arweave-python-client 才走)
        try:
            import arweave  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "arweave-python-client 未装 (pip install 'sisoul[onchain]'), "
                "返回 fake tx_id"
            )
            tx_id = "no-lib-fake-" + hashlib.sha256(blob).hexdigest()[:32]
            return tx_id

        try:
            wallet = arweave.Wallet(str(self.arweave_wallet_path))  # type: ignore[attr-defined]
            wallet.api_url = self.gateway  # type: ignore[attr-defined]
            tx = arweave.Transaction(wallet, data=blob)  # type: ignore[attr-defined]
            tx.add_tag("App-Name", "sisoul")
            tx.add_tag("App-Version", "0.1.0-dev")
            tx.add_tag("Content-Type", "application/octet-stream")
            tx.add_tag("Snapshot-Schema", "sisoul-snapshot-v1")
            tx.sign()
            tx.send()
            return str(tx.id)
        except Exception as e:  # noqa: BLE001 · 第三方库异常面宽
            logger.warning("Arweave 真上链失败 (%s), 返回 fake tx_id", e)
            return "upload-err-" + hashlib.sha256(blob).hexdigest()[:32]

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
        if tx_id.startswith(("mocktx-", "testnet-fake-", "no-lib-fake-", "upload-err-")):
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
            tx_id = self.upload_to_arweave(encrypted)
            record.arweave_tx_id = tx_id
            if tx_id is None:
                record.status = "failed"
                record.error = (record.error + "; " if record.error else "") + "arweave 上链失败"

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
        """
        import time as _time

        record: SnapshotRecord | None = None
        for attempt in range(max_retries):
            record = self.snapshot_now(vault_dir, upload=upload)
            if record.status == "ok":
                return record
            if attempt == max_retries - 1:
                break
            delay = base_delay_sec * (2 ** attempt)
            _time.sleep(delay)
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


__all__ = [
    "ArweaveSnapshot",
    "SnapshotRecord",
    "SnapshotHistory",
    "schedule_monthly_snapshot",
    "ARWEAVE_TESTNET_GATEWAY",
    "ARWEAVE_MAINNET_GATEWAY",
    "PINATA_API_BASE",
    "DEFAULT_HISTORY_PATH",
]

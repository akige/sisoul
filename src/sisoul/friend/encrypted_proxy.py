"""sisoul friend · 加密 proxy daemon (Phase 4 W54-W58 · 波 5 dev-B).

§28 §3.2 加密 proxy 机制 (核心隐私保证) · §29 §6.1 W54-W58.

# 问题

Alice 借 Bob 的 Claude API quota, 但 **不想让 Bob 看 prompt 内容** (商业机密).

# 解决 (本模块实现)

端到端加密 proxy. prompt 仅在 Bob daemon **内存可见**, 不写盘, 不显示给 Bob 用户.

```
Alice daemon                          Bob daemon                       Anthropic API
    │                                       │                                │
    │ 1. encrypt(prompt, Bob_pub)           │                                │
    │  (libsodium Box, curve25519+xcha)     │                                │
    │ ─────────────────────────────────────▶│                                │
    │                                       │ 2. decrypt(prompt, Bob_priv)   │
    │                                       │  prompt 仅内存, 不 log/写盘     │
    │                                       │ 3. call LLM (Bob 自己 key)     │
    │                                       │ ──────────────────────────────▶│
    │                                       │ 4. response (plaintext)        │
    │                                       │ ◀──────────────────────────────│
    │                                       │ 5. encrypt(response, Alice_pub)│
    │ 6. decrypt(response, Alice_priv)      │                                │
    │ ◀─────────────────────────────────────│                                │
```

# 密码学决策

- **选 libsodium Box** (curve25519 + xchacha20poly1305-poly1305)
  - 标准 NaCl/libsodium 原语, Soatok / cryptography 社区共识"绿框"安全级
  - 双方独立 keypair (Alice/Bob 不同 seed) → Box 模式而非 SecretBox
  - 复用 `pynacl.public.Box` (已在 W31 装) · 不再引第三方依赖
- **不选 Noise Protocol Framework** 理由:
  - Noise 主要价值在 1-RTT handshake + 多种 pattern (IK / XK / etc.) → 适合 Wireguard/Lightning
  - 本场景双方公钥**预先**通过 EAS attestation / friend ledger 互验 (波 5 dev-A 的
    `relationship.py` `verify_mutual_attestation`) → 无需 handshake 协商
  - Noise 状态机复杂 · 攻击面比 Box 大 · Python 实现 (noiseprotocol 包) 维护停滞 2 年
- **per-friend session key 派生**:
  - BIP-39 master seed → `derive_subkey(master, "proxy", index=friend_id)` →
    32B seed (Curve25519 PrivateKey 接受 32B clamped seed)
  - 每朋友独立 keypair, 跨设备同 BIP-39 seed → 同 keypair (无需在线交换)

# Forward Secrecy 注

当前 v1.0: per-friend long-term keypair (无 ephemeral handshake) → **不具备** forward secrecy
(Bob priv 泄露 → 历史 ciphertext 可解). 已知 TODO, Phase 5 上 X3DH-like ephemeral 握手补.

# 隐私护栏 (本模块铁律)

1. 解密后 plaintext **仅活在** `proxy_chat_request` 局部变量
2. 调 LLM adapter 后立即用 `_zeroize` 覆盖局部 bytes (Python 限制下 best-effort)
3. **绝不** log / print / write_file / 加入任何持久化结构
4. metadata (token count / model / ts) **不含** 任何 prompt 字串 / response 字串
5. `enforce_no_disk_write()` runtime sanity check, audit 工具静态扫描双保险

# 协调 (波 5 边界)

- dev-A: `relationship.py` 提供 `Friend` (含 pubkey) / `verify_mutual_attestation`
- dev-B (本模块): 加密 proxy class + cli/daemon route
- dev-C: `permissions.py` 3 档授权 (本模块 hook `_check_permission` 占位, dev-C 接)
- dev-D: `ledger.py` 互惠 ledger (本模块 emit metadata event, dev-D 写 EAS)

dev-A/C/D 模块未 ship 时本模块用 stub fallback, 单测可独立通过.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.utils import random as nacl_random

from sisoul.identity import derive_subkey


# ── 常量 ──────────────────────────────────────────────────────────────────────


# Box (curve25519+xchacha+poly1305) nonce 长度 24B
BOX_NONCE_SIZE = Box.NONCE_SIZE  # 24
PUBKEY_SIZE = 32
PRIVKEY_SEED_SIZE = 32

# per-friend proxy key purpose tag (跟 vault/did/p2p 隔离)
_PROXY_PURPOSE = "proxy"

# session metadata 字段白名单 (绝不含 prompt/response 内容)
_METADATA_WHITELIST = frozenset(
    {
        "session_id",
        "borrower_did",
        "lender_did",
        "target_model",
        "started_ts",
        "ended_ts",
        "prompt_token_count",
        "response_token_count",
        "status",  # pending | completed | failed
        "provider",
        "error_class",  # 错误类型名, 不含 prompt 内容
    }
)


# ── 异常 ──────────────────────────────────────────────────────────────────────


class ProxyError(Exception):
    """加密 proxy root error."""


class ProxyPermissionError(ProxyError):
    """3 档授权拒绝 / quota 超限 / model 未授权 (dev-C 接)."""


class ProxyDecryptError(ProxyError):
    """密文校验失败 (MAC 错 / pubkey 不匹配 / 篡改)."""


class ProxyDiskWriteViolation(ProxyError):
    """sanity check 抓到 prompt/response 字串进了持久化 sink."""


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProxySessionMetadata:
    """对 Bob 用户可见的 session metadata (绝不含 prompt/response 内容).

    §28 §3.2 "Bob 用户能看到的 metadata":
    - 借了多少 token / 用了什么 model / 何时 / 借贷状态
    """

    session_id: str
    borrower_did: str
    lender_did: str
    target_model: str
    provider: str
    started_ts: float
    ended_ts: Optional[float] = None
    prompt_token_count: int = 0
    response_token_count: int = 0
    status: str = "pending"  # pending | completed | failed
    error_class: Optional[str] = None

    def to_safe_dict(self) -> dict[str, Any]:
        """转 dict 用于 PWA / API. 白名单过滤, 防误带 prompt 字串."""
        raw = {
            "session_id": self.session_id,
            "borrower_did": self.borrower_did,
            "lender_did": self.lender_did,
            "target_model": self.target_model,
            "provider": self.provider,
            "started_ts": self.started_ts,
            "ended_ts": self.ended_ts,
            "prompt_token_count": self.prompt_token_count,
            "response_token_count": self.response_token_count,
            "status": self.status,
            "error_class": self.error_class,
        }
        # 双保险白名单过滤
        return {k: v for k, v in raw.items() if k in _METADATA_WHITELIST}


@dataclass
class ProxySession:
    """活动 proxy session (仅 metadata 持久化, prompt/response 严禁存)."""

    metadata: ProxySessionMetadata
    # 注: 不存 prompt / response / 解密后明文. 故意.

    def end(self, status: str = "completed", error_class: Optional[str] = None,
            prompt_tokens: int = 0, response_tokens: int = 0) -> ProxySessionMetadata:
        self.metadata = ProxySessionMetadata(
            session_id=self.metadata.session_id,
            borrower_did=self.metadata.borrower_did,
            lender_did=self.metadata.lender_did,
            target_model=self.metadata.target_model,
            provider=self.metadata.provider,
            started_ts=self.metadata.started_ts,
            ended_ts=time.time(),
            prompt_token_count=prompt_tokens,
            response_token_count=response_tokens,
            status=status,
            error_class=error_class,
        )
        return self.metadata


# ── per-friend keypair 派生 ───────────────────────────────────────────────────


def derive_friend_session_keypair(
    master_seed: bytes, friend_index: int = 0
) -> tuple[PrivateKey, PublicKey]:
    """从 BIP-39 master seed 派生本端 (Alice 或 Bob) 在某朋友通道的 long-term keypair.

    Args:
        master_seed: 64B BIP-39 master seed (identity.seed.mnemonic_to_master_key).
        friend_index: friend 整数索引 (在 friend DB 里的固定编号, 同朋友跨设备一致).

    Returns:
        (PrivateKey, PublicKey) Curve25519 keypair.
        同 seed + friend_index ⇒ 同 keypair (决定性, 跨设备恢复).

    Raises:
        ValueError: master_seed 非 bytes / 空 / friend_index 负.
    """
    if not isinstance(master_seed, (bytes, bytearray)) or len(master_seed) == 0:
        raise ValueError("master_seed 必须非空 bytes")
    if not isinstance(friend_index, int) or friend_index < 0:
        raise ValueError(f"friend_index 必须 >= 0 int, 实际 {friend_index}")

    seed_32 = derive_subkey(bytes(master_seed), _PROXY_PURPOSE, index=friend_index)
    assert len(seed_32) == PRIVKEY_SEED_SIZE, "subkey 应 32B"
    priv = PrivateKey(seed_32)
    return priv, priv.public_key


# ── 内存擦除 best-effort ──────────────────────────────────────────────────────


def _zeroize(buf: Any) -> None:
    """best-effort 覆盖 bytearray. Python str/bytes 不可变, 无法真擦.

    本函数主要意义: 标记意图 + 对 bytearray 真擦, 减少 GC 前内存暴露窗口.
    生产中 plaintext 字符串到 GC 才回收 (Python GIL 下风险可控, 但仍是已知限制).
    """
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0
    # str / bytes immutable, 只能 del 让 refcount 归 0
    del buf


# ── 类型: forward_to_provider hook ────────────────────────────────────────────


# 接口: (prompt: str, model: str, provider: str, **kwargs) -> tuple[response_text, prompt_tokens, response_tokens]
ForwarderHook = Callable[..., tuple[str, int, int]]


class ForwarderNotInjectedError(RuntimeError):
    """无显式 forwarder 注入 + 未启用 SISOUL_DEFAULT_FORWARDER_REAL=1 → raise.

    波 7 dev-A bug-4 修复: 默认 forwarder 之前会真打 anthropic/openai API, 测试 / dev
    忘传 forwarder 易出 spurious network call / quota 消耗 / 暴露 prod LLM key 风险.

    解禁: 显式设 ENV `SISOUL_DEFAULT_FORWARDER_REAL=1` (生产 daemon 启动时设).
    """


def _default_forwarder(
    prompt: str,
    model: str,
    provider: str = "anthropic",
    api_key: Optional[str] = None,
    **kwargs: Any,
) -> tuple[str, int, int]:
    """默认 forwarder.

    波 7 dev-A bug-4 修复 (硬规):
      - 默认 raise ForwarderNotInjectedError (强制调用方显式注入 forwarder).
      - 仅 ENV `SISOUL_DEFAULT_FORWARDER_REAL=1` 时才真调 `sisoul.llm.get_adapter`
        (生产 daemon 启动时设, 单元测试默认 raise 拦截).

    生产 daemon (`sisoul daemon`) 启动 wrapper 应设 SISOUL_DEFAULT_FORWARDER_REAL=1.
    测试若需 mock LLM, 显式传 `forwarder=lambda p, m, **kw: ('mock-resp', 10, 10)`.

    注: prompt token 估算用 len(prompt)//4 粗算 (避免依赖 tiktoken).
    """
    import os

    if os.environ.get("SISOUL_DEFAULT_FORWARDER_REAL") != "1":
        raise ForwarderNotInjectedError(
            "EncryptedProxy._default_forwarder 默认禁用真 LLM 调用. "
            "测试请显式传 forwarder=mock_fn; 生产请设 ENV SISOUL_DEFAULT_FORWARDER_REAL=1. "
            "(波 7 dev-A bug-4 防 spurious API call)"
        )

    from sisoul.llm import get_adapter  # 延迟 import 避免环依赖

    adapter = get_adapter(provider, api_key=api_key, model=model)
    messages = [{"role": "user", "content": prompt}]
    # Wave B' P0-1: chat_with_usage 取真 token 计数 (Anthropic SDK usage).
    response_text, prompt_tokens, response_tokens = adapter.chat_with_usage(
        messages, **kwargs
    )
    return response_text, prompt_tokens, response_tokens


# ── 核心 class ────────────────────────────────────────────────────────────────


class EncryptedProxy:
    """端到端加密 proxy daemon (Bob 端 + Alice 端通用类, 角色由参数定).

    用法 (Bob 端 — 接 Alice 加密 prompt 调 LLM 返加密 response):

        bob_master = mnemonic_to_master_key("...")
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, friend_index=0)
        proxy = EncryptedProxy(
            self_priv=bob_priv,
            self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            llm_api_key="sk-ant-...",  # Bob 自己的 LLM key
        )
        # Alice 发 encrypted_prompt + alice_pubkey
        encrypted_response = proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_pub.encode(),
            encrypted_prompt=blob,
            target_model="claude-opus-4-7",
        )

    用法 (Alice 端 — 加密 prompt 发给 Bob, 解密 response):

        alice_master = mnemonic_to_master_key("...")
        alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
        proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub,
            self_did="alice.sisoul.eth",
        )
        encrypted_prompt = proxy.encrypt_for(bob_pubkey.encode(), "my secret prompt")
        # ... 发 Bob, 收 encrypted_response ...
        plaintext_response = proxy.decrypt_from(bob_pubkey.encode(), encrypted_response)
    """

    def __init__(
        self,
        self_priv: PrivateKey,
        self_pub: PublicKey,
        self_did: str,
        llm_api_key: Optional[str] = None,
        forwarder: Optional[ForwarderHook] = None,
        permission_checker: Optional[Callable[..., None]] = None,
        ledger_writer: Optional[Callable[[ProxySessionMetadata], None]] = None,
    ) -> None:
        """初始化 proxy.

        Args:
            self_priv: 本端 Curve25519 PrivateKey.
            self_pub: 本端 PublicKey (= self_priv.public_key, 显式传防 mismatch).
            self_did: 本端 DID (例 'bob.sisoul.eth').
            llm_api_key: Bob 端调真 LLM 时的 API key. Alice 端可不传.
            forwarder: LLM 转发钩子 (test 时 patch mock). None 用 _default_forwarder.
            permission_checker: dev-C 接 3 档授权检查. None 放行 (开发期 fallback).
            ledger_writer: dev-D 接互惠 ledger 写入. None no-op.
        """
        if not isinstance(self_priv, PrivateKey):
            raise ValueError("self_priv 必须 nacl.public.PrivateKey")
        if not isinstance(self_pub, PublicKey):
            raise ValueError("self_pub 必须 nacl.public.PublicKey")
        if self_priv.public_key.encode() != self_pub.encode():
            raise ValueError("self_pub 与 self_priv.public_key 不匹配")
        if not isinstance(self_did, str) or not self_did:
            raise ValueError("self_did 必须非空 str")

        self.self_priv = self_priv
        self.self_pub = self_pub
        self.self_did = self_did
        self.llm_api_key = llm_api_key
        self._forwarder: ForwarderHook = forwarder or _default_forwarder
        self._permission_checker = permission_checker
        self._ledger_writer = ledger_writer

        # active sessions (仅 metadata, 绝无 prompt/response)
        self._sessions: dict[str, ProxySession] = {}

    # ── 低层: Box 加解密 ──────────────────────────────────────────────────────

    def _box_for(self, peer_pubkey_bytes: bytes) -> Box:
        if not isinstance(peer_pubkey_bytes, (bytes, bytearray)):
            raise ValueError("peer_pubkey 必须 bytes")
        if len(peer_pubkey_bytes) != PUBKEY_SIZE:
            raise ValueError(f"peer_pubkey 必须 {PUBKEY_SIZE}B, 实际 {len(peer_pubkey_bytes)}")
        peer_pub = PublicKey(bytes(peer_pubkey_bytes))
        return Box(self.self_priv, peer_pub)

    def encrypt_for(self, peer_pubkey_bytes: bytes, plaintext: str | bytes) -> bytes:
        """用对端 pubkey 加密. 返回 nonce(24)||ciphertext+mac.

        Args:
            peer_pubkey_bytes: 32B 对端 Curve25519 public key bytes.
            plaintext: str 或 bytes.

        Returns:
            blob (decrypt_from 的输入).
        """
        if isinstance(plaintext, str):
            data = plaintext.encode("utf-8")
        elif isinstance(plaintext, (bytes, bytearray)):
            data = bytes(plaintext)
        else:
            raise ValueError("plaintext 必须 str 或 bytes")

        box = self._box_for(peer_pubkey_bytes)
        nonce = nacl_random(BOX_NONCE_SIZE)
        encrypted = box.encrypt(data, nonce)
        # pynacl EncryptedMessage 已含 nonce 前缀
        return bytes(encrypted)

    def decrypt_from(self, peer_pubkey_bytes: bytes, blob: bytes) -> bytes:
        """用对端 pubkey 解密.

        Returns:
            明文 bytes (调用方按需 decode utf-8).

        Raises:
            ProxyDecryptError: MAC 错 / pubkey 不匹配 / 数据损坏.
        """
        if not isinstance(blob, (bytes, bytearray)) or len(blob) < BOX_NONCE_SIZE + 16:
            raise ValueError(f"blob 太短 (>= {BOX_NONCE_SIZE + 16}B), 实际 {len(blob) if hasattr(blob, '__len__') else '?'}B")
        box = self._box_for(peer_pubkey_bytes)
        try:
            return box.decrypt(bytes(blob))
        except CryptoError as e:
            raise ProxyDecryptError(f"Box 解密失败 (MAC 错 / key 不匹配 / 损坏): {e}") from e

    # ── 高层: Bob 端 proxy ────────────────────────────────────────────────────

    def proxy_chat_request(
        self,
        borrower_did: str,
        borrower_pubkey: bytes,
        encrypted_prompt: bytes,
        target_model: str,
        provider: str = "anthropic",
        **llm_kwargs: Any,
    ) -> tuple[bytes, ProxySessionMetadata]:
        """Bob 端入口: 接 Alice 加密 prompt → 解密 → 调 LLM → 加密 response 返回.

        隐私铁律:
        - plaintext prompt **仅活在** 本函数局部 `prompt_text` 变量
        - 不 log / 不 print / 不 write_file / 不存入 self._sessions
        - 函数末尾 best-effort zeroize

        Args:
            borrower_did: Alice DID (metadata 用).
            borrower_pubkey: Alice 32B Curve25519 pubkey bytes (用于双向加密).
            encrypted_prompt: encrypt_for 输出 (Alice 用 Bob pub 加密).
            target_model: 'claude-opus-4-7' / etc.
            provider: 'anthropic' / 'openai' / 'gemini' / etc. (传给 sisoul.llm.get_adapter).
            **llm_kwargs: 透传 LLMAdapter.chat (max_tokens / temperature).

        Returns:
            (encrypted_response, metadata) — encrypted_response 用 borrower_pubkey 加密.

        Raises:
            ProxyDecryptError / ProxyPermissionError / ProxyError.
        """
        # 1. 创 session (metadata-only)
        session = self._create_session(
            borrower_did=borrower_did,
            target_model=target_model,
            provider=provider,
        )

        # 2. 权限检查 (dev-C hook)
        try:
            self._check_permission(borrower_did, target_model, provider)
        except ProxyPermissionError as e:
            meta = session.end(status="failed", error_class="ProxyPermissionError")
            self._maybe_write_ledger(meta)
            raise

        # 3. 解密 prompt (仅内存)
        try:
            prompt_bytes = self.decrypt_from(borrower_pubkey, encrypted_prompt)
            prompt_text = prompt_bytes.decode("utf-8")
        except (ProxyDecryptError, UnicodeDecodeError) as e:
            meta = session.end(status="failed", error_class=type(e).__name__)
            self._maybe_write_ledger(meta)
            raise ProxyDecryptError(f"prompt 解密/解码失败: {type(e).__name__}") from e

        # 4. 调 LLM (Bob 自己 key, 不 leak prompt)
        # ⚠️ 严禁: log/print prompt_text. 严禁存入 self._sessions. 严禁写文件.
        try:
            response_text, p_tok, r_tok = self._forwarder(
                prompt=prompt_text,
                model=target_model,
                provider=provider,
                api_key=self.llm_api_key,
                **llm_kwargs,
            )
        except Exception as e:  # noqa: BLE001 (任何 forwarder 异常都不让 prompt 泄漏)
            # 不把 e 信息 chain 出去 (e.args 可能含 prompt 片段如 422 错误回显)
            err_class = type(e).__name__
            meta = session.end(status="failed", error_class=err_class)
            self._maybe_write_ledger(meta)
            # ⚠️ 故意不 raise from e (链式异常可能携带 prompt 字符串)
            raise ProxyError(f"forwarder 调用失败 ({err_class})") from None
        finally:
            # best-effort 清 plaintext (Python str 不可变限制, 但 del 让 GC 候选)
            _zeroize(prompt_bytes if isinstance(prompt_bytes, (bytes, bytearray)) else b"")

        # 5. 加密 response 给 Alice
        try:
            encrypted_response = self.encrypt_for(borrower_pubkey, response_text)
        finally:
            # 同 prompt 清理
            del response_text

        # 6. 结束 session metadata
        meta = session.end(
            status="completed",
            prompt_tokens=p_tok,
            response_tokens=r_tok,
        )
        self._maybe_write_ledger(meta)

        return encrypted_response, meta

    # ── session 管理 ──────────────────────────────────────────────────────────

    def _create_session(
        self,
        borrower_did: str,
        target_model: str,
        provider: str,
    ) -> ProxySession:
        sid = uuid.uuid4().hex[:16]
        meta = ProxySessionMetadata(
            session_id=sid,
            borrower_did=borrower_did,
            lender_did=self.self_did,
            target_model=target_model,
            provider=provider,
            started_ts=time.time(),
        )
        session = ProxySession(metadata=meta)
        self._sessions[sid] = session
        return session

    def list_sessions(self) -> list[ProxySessionMetadata]:
        """列活动 session metadata (Bob PWA 看). 绝不含 prompt/response."""
        return [s.metadata for s in self._sessions.values()]

    def get_session(self, session_id: str) -> Optional[ProxySessionMetadata]:
        s = self._sessions.get(session_id)
        return s.metadata if s else None

    def end_session(self, session_id: str) -> Optional[ProxySessionMetadata]:
        s = self._sessions.pop(session_id, None)
        if s is None:
            return None
        return s.end(status="completed")

    # ── hook 接口 (dev-C / dev-D 接) ─────────────────────────────────────────

    def _check_permission(
        self, borrower_did: str, target_model: str, provider: str
    ) -> None:
        """dev-C 3 档授权 hook. dev-C 未 ship 时放行 (开发期 fallback)."""
        if self._permission_checker is None:
            return
        try:
            self._permission_checker(
                borrower_did=borrower_did,
                target_model=target_model,
                provider=provider,
                lender_did=self.self_did,
            )
        except ProxyPermissionError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProxyPermissionError(f"permission_checker 异常: {type(e).__name__}") from e

    def _maybe_write_ledger(self, meta: ProxySessionMetadata) -> None:
        """dev-D 互惠 ledger hook. None no-op."""
        if self._ledger_writer is None:
            return
        try:
            self._ledger_writer(meta)
        except Exception:  # noqa: BLE001 (ledger 故障不阻塞 proxy)
            pass

    # ── 隐私 sanity check ────────────────────────────────────────────────────

    @staticmethod
    def enforce_no_disk_write(
        prompt_substring: str,
        response_substring: str,
        check_paths: Optional[list[str]] = None,
    ) -> None:
        """runtime sanity: 验证 prompt/response 字串没出现在常见持久化路径.

        典型用法: 跑一遍 proxy_chat_request, 然后调本函数确认 vault / ~/.sisoul/ /tmp
        / 当前 logfile 都没含 prompt/response 子串.

        Args:
            prompt_substring: prompt 的一段独特字符 (例 random uuid token).
            response_substring: response 的独特字符.
            check_paths: 要扫的路径列表. None = 默认扫 ~/.sisoul + /tmp + cwd.

        Raises:
            ProxyDiskWriteViolation: 任何路径含 prompt/response 子串.
        """
        from pathlib import Path

        if check_paths is None:
            check_paths = [
                str(Path.home() / ".sisoul"),
                "/tmp",
                str(Path.cwd()),
            ]

        for p_str in check_paths:
            p = Path(p_str).expanduser()
            if not p.exists():
                continue
            if p.is_file():
                _scan_file_for_leak(p, prompt_substring, response_substring)
            else:
                # 只扫一层 (避免递归大目录如 ~/.sisoul/p2p/cache 太慢)
                for child in p.iterdir():
                    if child.is_file() and child.stat().st_size < 10 * 1024 * 1024:
                        _scan_file_for_leak(child, prompt_substring, response_substring)


def _scan_file_for_leak(path, prompt_sub: str, response_sub: str) -> None:
    """读文件检查是否含 prompt/response 子串. 抛 ProxyDiskWriteViolation."""
    try:
        content = path.read_bytes()
    except (OSError, PermissionError):
        return
    if prompt_sub and prompt_sub.encode("utf-8") in content:
        raise ProxyDiskWriteViolation(
            f"LEAK: prompt 字串 '{prompt_sub[:20]}...' 出现在 {path}"
        )
    if response_sub and response_sub.encode("utf-8") in content:
        raise ProxyDiskWriteViolation(
            f"LEAK: response 字串 '{response_sub[:20]}...' 出现在 {path}"
        )


# ── async 包装 (daemon FastAPI 用) ────────────────────────────────────────────


async def proxy_chat_request_async(
    proxy: EncryptedProxy,
    borrower_did: str,
    borrower_pubkey: bytes,
    encrypted_prompt: bytes,
    target_model: str,
    provider: str = "anthropic",
    **llm_kwargs: Any,
) -> tuple[bytes, ProxySessionMetadata]:
    """async 包装. 当前内部仍同步 (LLM adapter 是 sync), 走 to_thread 不阻 event loop."""
    return await asyncio.to_thread(
        proxy.proxy_chat_request,
        borrower_did=borrower_did,
        borrower_pubkey=borrower_pubkey,
        encrypted_prompt=encrypted_prompt,
        target_model=target_model,
        provider=provider,
        **llm_kwargs,
    )


# ── borrow-path module entry (P0 2026-06-10) ─────────────────────────────────
#
# borrow._proxy_call 的 import 目标. 之前不存在 → import 永远失败 → borrow 永远
# 走 stub-passthrough. 现在真走 proxy_p2p.borrower_roundtrip: Box 加密 prompt
# 经 GossipSub 发 lender daemon, lender 解密调自己的 LLM endpoint, 加密回传.


def proxy_chat_request(
    *,
    borrower_did: str,
    lender_did: str,
    model: str,
    prompt: str,
    amount: int = 0,
    provider: str = "openai",
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Borrow 真路径 (Alice 端): 加密 prompt → GossipSub → lender → 解密 response.

    Returns:
        {"text", "tokens_used", "model_used", "request_id"} — borrow._proxy_call
        映射成 ProxyResult(method="dev-b-encrypted-proxy").

    Raises:
        proxy_p2p.ProxyP2PTimeout / ProxyP2PError — borrow 端可 fallback stub.
    """
    from sisoul.friend.proxy_p2p import (
        DEFAULT_ROUNDTRIP_TIMEOUT,
        borrower_roundtrip,
    )

    r = borrower_roundtrip(
        borrower_did=borrower_did,
        lender_did=lender_did,
        model=model,
        prompt=prompt,
        provider=provider,
        timeout=timeout or DEFAULT_ROUNDTRIP_TIMEOUT,
    )
    return {
        "text": r["text"],
        "tokens_used": int(r["prompt_tokens"]) + int(r["response_tokens"]),
        "model_used": r["model_used"],
        "request_id": r["request_id"],
    }


# ── module singleton (daemon route 用) ────────────────────────────────────────

_GLOBAL_PROXY: Optional[EncryptedProxy] = None


def get_global_proxy() -> Optional[EncryptedProxy]:
    return _GLOBAL_PROXY


def set_global_proxy(proxy: Optional[EncryptedProxy]) -> None:
    global _GLOBAL_PROXY
    _GLOBAL_PROXY = proxy


__all__ = [
    "BOX_NONCE_SIZE",
    "PUBKEY_SIZE",
    "EncryptedProxy",
    "ProxySession",
    "ProxySessionMetadata",
    "ProxyError",
    "ProxyPermissionError",
    "ProxyDecryptError",
    "ProxyDiskWriteViolation",
    "derive_friend_session_keypair",
    "proxy_chat_request",
    "proxy_chat_request_async",
    "get_global_proxy",
    "set_global_proxy",
]

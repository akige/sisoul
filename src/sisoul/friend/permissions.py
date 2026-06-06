"""sisoul friend permissions · 3 档授权模式 (Phase 4 W59-W62, 波 5 dev-C).

§28 §3.3 3 档授权模式 spec:
- strong-tie-auto  : 任何 request 自动通过 (限月度配额 + rate limit). 不弹窗
- per-request      : 每次 request, Bob PWA 弹窗批准. 30 秒不响应自动 deny
- emergency-only   : 仅 Alice 触发 emergency flag (例 quota 已耗 + 关键 deadline) 才可借

yaml 配置 schema (Bob 给 Alice 的授权, 存 ~/.sisoul/friends/<did>-permissions.yaml):

    friend: alice.sisoul.eth
    permissions:
      llm_quota_share:
        enabled: true
        mode: strong-tie-auto      # or per-request / emergency-only
        monthly_token_cap: 500000
        rate_limit: 10             # N requests / min
        models:                    # allow list (空 = 全部允许)
          - claude-opus-4-7
          - claude-sonnet-4-6
        emergency_reserve_tokens: 200000

      ai_skill_share:
        enabled: true
        mode: per-request
        skills:                    # allow list
          - solidity-expert
          - chinese-novel-writer
        per_session_max_minutes: 30

      compute_share:               # v2/v3 schema 占位, 当前不实现
        enabled: false

模块边界 (波 5 dev-C 严格约束):
- 不动 src/sisoul/{vault,llm,sync,identity,p2p,onchain,daemon.py} 主
- 不动 friend/{__init__.py(dev-A), relationship.py(dev-A), encrypted_proxy.py(dev-B),
       proxy_audit.py(dev-B), lend.py(dev-D), borrow.py(dev-D), ledger.py(dev-D)}
- 只动 friend/{permissions.py, anti_abuse.py}

count_monthly_usage 当前 stub (dev-D 出 ledger 接口后接). check_permission 收 callback 参数,
方便集成测试注入实际 usage 函数.

测试见 tests/test_friend_permissions.py.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import yaml

# ── 常量 ─────────────────────────────────────────────────────────────────────

PermissionMode = Literal["strong-tie-auto", "per-request", "emergency-only"]
ResourceType = Literal["llm_quota", "ai_skill", "compute"]

VALID_MODES: tuple[PermissionMode, ...] = (
    "strong-tie-auto",
    "per-request",
    "emergency-only",
)
VALID_RESOURCES: tuple[ResourceType, ...] = ("llm_quota", "ai_skill", "compute")

# 默认 permissions 路径.
DEFAULT_PERMISSIONS_DIR = Path.home() / ".sisoul" / "friends"


# ── 异常 ─────────────────────────────────────────────────────────────────────


class PermissionError(Exception):
    """permissions 通用异常."""


class PermissionNotFoundError(PermissionError):
    """未找到 friend permissions (yaml 不存在)."""


class InvalidPermissionConfigError(PermissionError):
    """yaml schema 不合法 (mode / resource 等枚举值非法)."""


class FriendRevokedError(PermissionError):
    """该 friend 已被 revoke (即时拒)."""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class LLMQuotaShare:
    """LLM quota share 配置.

    Incentive modes (per INCENTIVE-DESIGN.md, §4.10-compatible — sisoul takes 0%):

    - "gift": no cost to borrower (default, between friends)
    - "kudos": borrower deducts kudos counter; kudos non-transferable, decays 5%/mo
    - "micropay": borrower pays USDT-TRC20 directly to lender's payout address;
      sisoul does not route or hold funds
    """

    enabled: bool = False
    mode: PermissionMode = "per-request"
    monthly_token_cap: int = 0
    rate_limit: int = 0  # N requests / min (滑动窗口); 0 = 不限
    models: list[str] = field(default_factory=list)  # 空 list = 全允许
    emergency_reserve_tokens: int = 0
    # incentive (added 2026-06-06 per docs/INCENTIVE-DESIGN.md)
    incentive_mode: str = "gift"  # gift | kudos | micropay
    kudos_required_per_1k_tokens: float = 0.0  # only used when incentive_mode == "kudos"
    usdt_per_1k_tokens: float = 0.0  # only used when incentive_mode == "micropay"
    usdt_payout_address: str = ""  # TRC20 T-address; required when incentive_mode == "micropay"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMQuotaShare:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    def validate_incentive(self) -> None:
        """Raise InvalidPermissionConfigError if incentive fields are inconsistent."""
        if self.incentive_mode not in ("gift", "kudos", "micropay"):
            raise InvalidPermissionConfigError(
                f"incentive_mode must be gift/kudos/micropay, got: {self.incentive_mode!r}"
            )
        if self.incentive_mode == "kudos" and self.kudos_required_per_1k_tokens < 0:
            raise InvalidPermissionConfigError("kudos_required_per_1k_tokens must be ≥ 0")
        if self.incentive_mode == "micropay":
            if self.usdt_per_1k_tokens <= 0:
                raise InvalidPermissionConfigError("micropay requires usdt_per_1k_tokens > 0")
            if not self.usdt_payout_address or not self.usdt_payout_address.startswith("T"):
                raise InvalidPermissionConfigError(
                    "micropay requires usdt_payout_address (TRC20 T-address)"
                )


@dataclass
class AISkillShare:
    """AI 技能 share 配置."""

    enabled: bool = False
    mode: PermissionMode = "per-request"
    skills: list[str] = field(default_factory=list)
    per_session_max_minutes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AISkillShare:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class ComputeShare:
    """compute share schema 占位 (v2/v3, 当前不实现)."""

    enabled: bool = False
    # 未来字段 (v2/v3 实现时 append, 当前只 schema 占位)
    cpu_cores: int = 0
    ram_mb: int = 0
    mode: PermissionMode = "per-request"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ComputeShare:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class FriendPermission:
    """Bob 给 Alice 的完整授权 (一个 friend → 一个 FriendPermission)."""

    friend_did: str
    llm_quota_share: LLMQuotaShare = field(default_factory=LLMQuotaShare)
    ai_skill_share: AISkillShare = field(default_factory=AISkillShare)
    compute_share: ComputeShare = field(default_factory=ComputeShare)
    # revoke 即时生效字段 (L3 anti_abuse 写, check_permission 读)
    revoked: bool = False
    revoked_at: Optional[str] = None
    revoked_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "friend": self.friend_did,
            "permissions": {
                "llm_quota_share": self.llm_quota_share.to_dict(),
                "ai_skill_share": self.ai_skill_share.to_dict(),
                "compute_share": self.compute_share.to_dict(),
            },
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
            "revoked_reason": self.revoked_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FriendPermission:
        """兼容两种结构: 平铺 (顶层 friend_did + llm_quota_share) 或嵌套 (friend + permissions{}).

        yaml 文件用嵌套结构 (§28 §3.3 spec). HTTP/CLI 偶尔用平铺.
        """
        # 嵌套 yaml 形 (spec 标准)
        if "permissions" in d:
            friend_did = d.get("friend") or d.get("friend_did") or ""
            perms = d.get("permissions") or {}
            return cls(
                friend_did=friend_did,
                llm_quota_share=LLMQuotaShare.from_dict(perms.get("llm_quota_share", {})),
                ai_skill_share=AISkillShare.from_dict(perms.get("ai_skill_share", {})),
                compute_share=ComputeShare.from_dict(perms.get("compute_share", {})),
                revoked=bool(d.get("revoked", False)),
                revoked_at=d.get("revoked_at"),
                revoked_reason=d.get("revoked_reason"),
            )
        # 平铺
        return cls(
            friend_did=d.get("friend_did") or d.get("friend") or "",
            llm_quota_share=LLMQuotaShare.from_dict(d.get("llm_quota_share", {})),
            ai_skill_share=AISkillShare.from_dict(d.get("ai_skill_share", {})),
            compute_share=ComputeShare.from_dict(d.get("compute_share", {})),
            revoked=bool(d.get("revoked", False)),
            revoked_at=d.get("revoked_at"),
            revoked_reason=d.get("revoked_reason"),
        )


# ── yaml schema 校验 ─────────────────────────────────────────────────────────


def validate_permission(perm: FriendPermission) -> None:
    """校验 perm schema (mode / resource 枚举合法 / cap >=0).

    抛 InvalidPermissionConfigError on bad.
    """
    if not perm.friend_did:
        raise InvalidPermissionConfigError("friend_did 必填")

    for resource, sub in [
        ("llm_quota_share", perm.llm_quota_share),
        ("ai_skill_share", perm.ai_skill_share),
        ("compute_share", perm.compute_share),
    ]:
        if sub.mode not in VALID_MODES:
            raise InvalidPermissionConfigError(
                f"{resource}.mode={sub.mode!r} 非法; 必须 ∈ {VALID_MODES}"
            )

    if perm.llm_quota_share.monthly_token_cap < 0:
        raise InvalidPermissionConfigError("monthly_token_cap 必须 >= 0")
    if perm.llm_quota_share.rate_limit < 0:
        raise InvalidPermissionConfigError("rate_limit 必须 >= 0")
    if perm.llm_quota_share.emergency_reserve_tokens < 0:
        raise InvalidPermissionConfigError("emergency_reserve_tokens 必须 >= 0")
    if perm.ai_skill_share.per_session_max_minutes < 0:
        raise InvalidPermissionConfigError("per_session_max_minutes 必须 >= 0")


# ── 持久化 (yaml) ────────────────────────────────────────────────────────────


def _sanitize_did_for_filename(did: str) -> str:
    """DID → 安全 filename. 替换 / : 等不安全字符."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", did)


def _perm_path(friend_did: str, perms_dir: Optional[Path] = None) -> Path:
    base = Path(perms_dir) if perms_dir else DEFAULT_PERMISSIONS_DIR
    return base / f"{_sanitize_did_for_filename(friend_did)}-permissions.yaml"


def load_permissions(
    friend_did: str, perms_dir: Optional[Path] = None
) -> FriendPermission:
    """从 ~/.sisoul/friends/<did>-permissions.yaml 读 perm.

    文件不存在 → 抛 PermissionNotFoundError (调用方可决定回退默认 deny).
    """
    path = _perm_path(friend_did, perms_dir)
    if not path.exists():
        raise PermissionNotFoundError(
            f"friend permissions 不存在: {path} (friend_did={friend_did})"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as e:
        raise InvalidPermissionConfigError(
            f"yaml 解析失败 ({path}): {e}"
        ) from e
    if not isinstance(data, dict):
        raise InvalidPermissionConfigError(
            f"yaml 顶层必须是 mapping, 拿到 {type(data).__name__}"
        )
    perm = FriendPermission.from_dict(data)
    # 防 yaml 里 friend 字段缺失 → 用参数补.
    if not perm.friend_did:
        perm.friend_did = friend_did
    validate_permission(perm)
    return perm


def save_permissions(
    friend_did: str,
    perm: FriendPermission,
    perms_dir: Optional[Path] = None,
) -> Path:
    """写 perm 到 yaml. 写前 validate, 不合法抛 InvalidPermissionConfigError."""
    if perm.friend_did and perm.friend_did != friend_did:
        raise InvalidPermissionConfigError(
            f"perm.friend_did={perm.friend_did!r} 与参数 friend_did={friend_did!r} 不一致"
        )
    perm.friend_did = friend_did
    validate_permission(perm)
    path = _perm_path(friend_did, perms_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(perm.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def list_all_friends(perms_dir: Optional[Path] = None) -> list[str]:
    """列出所有有 perm 文件的 friend (从 filename 反推).

    note: filename sanitize 不可逆, 故返 yaml 内 friend 字段(精确).
    """
    base = Path(perms_dir) if perms_dir else DEFAULT_PERMISSIONS_DIR
    if not base.exists():
        return []
    out: list[str] = []
    for f in sorted(base.glob("*-permissions.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                continue
            did = data.get("friend") or data.get("friend_did")
            if did:
                out.append(did)
        except (yaml.YAMLError, OSError):
            # 跳过 broken yaml, 不阻断 list.
            continue
    return out


# ── usage 计数 (stub, dev-D ledger 后接) ─────────────────────────────────────


# 注入点: dev-D ledger ship 后, 在 daemon 启动时调用 register_usage_provider 注入实际函数.
# 当前默认 stub 返 0.
_usage_provider: Optional[Callable[[str, str], int]] = None


def register_usage_provider(
    provider: Optional[Callable[[str, str], int]],
) -> None:
    """注入 monthly usage 查询函数. 签名: (friend_did, resource_type) -> int.

    传 None 重置回默认 stub (测试用).
    dev-D ship ledger.py 后在 daemon 启动 hook 调用.
    """
    global _usage_provider
    _usage_provider = provider


def count_monthly_usage(friend_did: str, resource_type: str) -> int:
    """当月 friend 借量 (resource_type 内). dev-D ledger 接入后真实数据.

    当前 stub: 注入了 provider 用 provider, 否则返 0.
    """
    if _usage_provider is not None:
        try:
            return int(_usage_provider(friend_did, resource_type))
        except Exception:
            # provider broken 时 fail-open: 视为 0, 不阻断 check_permission.
            # (真生产应 log warning, 这里波 5 范围简化.)
            return 0
    return 0


# ── 核心: check_permission ───────────────────────────────────────────────────


def check_permission(
    friend_did: str,
    resource_type: str,
    amount: int,
    model: Optional[str] = None,
    *,
    perm: Optional[FriendPermission] = None,
    perms_dir: Optional[Path] = None,
    emergency_flag: bool = False,
    per_request_approved: bool = False,
    current_usage: Optional[int] = None,
) -> tuple[bool, str]:
    """核心: 判 Alice 借 (resource_type, amount, model) 是否准.

    参数:
      - friend_did: Alice DID (Bob 看角度: "对方")
      - resource_type: 'llm_quota' / 'ai_skill' / 'compute'
      - amount: tokens (llm_quota) / minutes (ai_skill) / cores (compute, v2/v3)
      - model: model name (llm_quota) 或 skill_id (ai_skill); compute 可空
      - perm: 已加载 perm; 省略则 load_permissions(friend_did)
      - emergency_flag: Alice 触发 emergency (mode=emergency-only 时 only-flag-true 才准)
      - per_request_approved: Bob 通过 PWA 已 approve (mode=per-request 时必须)
      - current_usage: 注入当月使用量; 省略时调 count_monthly_usage

    返: (allowed: bool, reason: str)
      - allowed=True  → reason 是"已批准, mode=X, usage=Y/Z"
      - allowed=False → reason 是具体拒因 (revoked / disabled / model_not_allowed / cap_exceeded /
                       per_request_pending / emergency_only_no_flag / unknown_resource)
    """
    if resource_type not in VALID_RESOURCES:
        return False, f"unknown_resource:{resource_type!r}"
    if amount < 0:
        return False, "invalid_amount:negative"

    if perm is None:
        try:
            perm = load_permissions(friend_did, perms_dir)
        except PermissionNotFoundError:
            return False, "no_permission_config"
        except InvalidPermissionConfigError as e:
            return False, f"invalid_permission_config:{e}"

    if perm.revoked:
        return False, f"revoked:{perm.revoked_reason or 'no_reason'}"

    # 选择子配置
    sub: Any
    if resource_type == "llm_quota":
        sub = perm.llm_quota_share
    elif resource_type == "ai_skill":
        sub = perm.ai_skill_share
    else:  # compute
        sub = perm.compute_share

    if not sub.enabled:
        return False, f"resource_disabled:{resource_type}"

    # model / skill allow list check (llm_quota & ai_skill 用)
    if resource_type == "llm_quota":
        if sub.models and model is not None and model not in sub.models:
            return False, f"model_not_allowed:{model}"
        if model is None and sub.models:
            return False, "model_required"
    elif resource_type == "ai_skill":
        if sub.skills and model is not None and model not in sub.skills:
            return False, f"skill_not_allowed:{model}"
        if model is None and sub.skills:
            return False, "skill_required"
        if sub.per_session_max_minutes and amount > sub.per_session_max_minutes:
            return False, (
                f"session_too_long:{amount}min > "
                f"{sub.per_session_max_minutes}min"
            )

    # 月度 cap (L1 anti_abuse, 但 check_permission 也做基础检查, anti_abuse 再细)
    if resource_type == "llm_quota" and sub.monthly_token_cap > 0:
        usage = (
            current_usage
            if current_usage is not None
            else count_monthly_usage(friend_did, resource_type)
        )
        # 普通 cap check
        if usage + amount > sub.monthly_token_cap:
            # 若 emergency-only 模式 + flag 触发 → 检查 reserve
            if sub.mode == "emergency-only" and emergency_flag:
                if amount <= sub.emergency_reserve_tokens:
                    return True, (
                        f"approved:emergency-reserve "
                        f"({amount} <= reserve {sub.emergency_reserve_tokens})"
                    )
                return False, (
                    f"emergency_reserve_exceeded:{amount} > "
                    f"{sub.emergency_reserve_tokens}"
                )
            return False, (
                f"monthly_cap_exceeded:{usage}+{amount} > "
                f"{sub.monthly_token_cap}"
            )

    # 3 档 mode
    if sub.mode == "strong-tie-auto":
        return True, "approved:strong-tie-auto"

    if sub.mode == "per-request":
        if per_request_approved:
            return True, "approved:per-request"
        return False, "per_request_pending"

    if sub.mode == "emergency-only":
        if emergency_flag:
            # 若没触发 cap 路径 (例: 没设 cap), 看是否在 reserve 内
            reserve = sub.emergency_reserve_tokens if resource_type == "llm_quota" else 0
            if resource_type == "llm_quota" and reserve > 0 and amount > reserve:
                return False, f"emergency_reserve_exceeded:{amount} > {reserve}"
            return True, "approved:emergency-only"
        return False, "emergency_only_no_flag"

    return False, f"unknown_mode:{sub.mode}"


# ── revoke 状态 helper (anti_abuse L3 用) ────────────────────────────────────


def mark_revoked(
    friend_did: str,
    reason: str = "",
    perms_dir: Optional[Path] = None,
) -> FriendPermission:
    """设 perm.revoked=True 并落盘. 若 yaml 不存在 → 创建一个 revoked-only 的占位.

    anti_abuse.revoke_friend_permission 调这做即时禁用.
    """
    from datetime import datetime, timezone

    try:
        perm = load_permissions(friend_did, perms_dir)
    except PermissionNotFoundError:
        perm = FriendPermission(friend_did=friend_did)
    perm.revoked = True
    perm.revoked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    perm.revoked_reason = reason
    save_permissions(friend_did, perm, perms_dir)
    return perm


def unmark_revoked(
    friend_did: str, perms_dir: Optional[Path] = None
) -> FriendPermission:
    """撤销 revoke (Bob 改主意). 仅清 revoked 标志, 不动其他 perm."""
    perm = load_permissions(friend_did, perms_dir)
    perm.revoked = False
    perm.revoked_at = None
    perm.revoked_reason = None
    save_permissions(friend_did, perm, perms_dir)
    return perm


__all__ = [
    # 类型
    "FriendPermission",
    "LLMQuotaShare",
    "AISkillShare",
    "ComputeShare",
    "PermissionMode",
    "ResourceType",
    # 异常
    "PermissionError",
    "PermissionNotFoundError",
    "InvalidPermissionConfigError",
    "FriendRevokedError",
    # 常量
    "VALID_MODES",
    "VALID_RESOURCES",
    "DEFAULT_PERMISSIONS_DIR",
    # 持久化
    "load_permissions",
    "save_permissions",
    "list_all_friends",
    # 校验
    "validate_permission",
    # 核心
    "check_permission",
    # usage
    "count_monthly_usage",
    "register_usage_provider",
    # revoke helper
    "mark_revoked",
    "unmark_revoked",
]

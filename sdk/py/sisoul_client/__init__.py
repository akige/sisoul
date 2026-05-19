"""sisoul-client - Python SDK for Sisoul daemon."""

from .client import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, SisoulClient
from .errors import (
    AuthError,
    DaemonError,
    NetworkError,
    SisoulError,
    TimeoutError,
)
from .types import (
    AttestCreateRequest,
    AttestCreateResponse,
    AttestEntry,
    Friend,
    FriendAddRequest,
    FriendBorrowRequest,
    FriendLendRequest,
    Goal,
    GoalCreateRequest,
    GoalUpdateRequest,
    Preference,
    SkillBorrowRequest,
    SkillBorrowResponse,
    SkillCreateRequest,
    SkillCreateResponse,
    SkillItem,
    SkillLendPermissions,
    SkillLendRequest,
    SkillLendResponse,
    SkillSessionItem,
)

__version__ = "0.1.0"

__all__ = [
    "SisoulClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    # errors
    "SisoulError",
    "DaemonError",
    "AuthError",
    "NetworkError",
    "TimeoutError",
    # types
    "Preference",
    "Goal",
    "GoalCreateRequest",
    "GoalUpdateRequest",
    "Friend",
    "FriendAddRequest",
    "FriendLendRequest",
    "FriendBorrowRequest",
    "SkillItem",
    "SkillLendPermissions",
    "SkillCreateRequest",
    "SkillCreateResponse",
    "SkillLendRequest",
    "SkillLendResponse",
    "SkillBorrowRequest",
    "SkillBorrowResponse",
    "SkillSessionItem",
    "AttestEntry",
    "AttestCreateRequest",
    "AttestCreateResponse",
]

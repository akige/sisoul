"""sisoul daemon shared pydantic models - 跟 PWA daemon.ts 对齐."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SkillSource = Literal["owned", "borrowed", "available"]
SkillLendMode = Literal["strong-tie-auto", "per-request", "emergency-only"]
SkillSessionStatus = Literal["active", "expired", "ended", "wiped"]
ResourceType = Literal["skill", "data", "compute"]


class Preference(BaseModel):
    key: str
    value: str
    updated_at: str


class ListPreferencesResponse(BaseModel):
    items: list[Preference] = Field(default_factory=list)


class Goal(BaseModel):
    id: str
    title: str
    progress: float = 0.0
    deadline: str | None = None
    notes: str | None = None


class ListGoalsResponse(BaseModel):
    goals: list[Goal] = Field(default_factory=list)


class GoalCreateRequest(BaseModel):
    title: str
    progress: float = 0.0
    deadline: str | None = None
    notes: str | None = None


class GoalUpdateRequest(BaseModel):
    id: str
    title: str | None = None
    progress: float | None = None
    deadline: str | None = None
    notes: str | None = None


class Friend(BaseModel):
    did: str
    handle: str | None = None
    trust_level: float = 0.0
    connected_at: str


class ListFriendsResponse(BaseModel):
    friends: list[Friend] = Field(default_factory=list)


class FriendAddRequest(BaseModel):
    did: str
    handle: str | None = None
    trust_level: float | None = None


class FriendLendRequest(BaseModel):
    friend_did: str
    resource_type: ResourceType
    resource_id: str
    duration_hours: int | None = None


class FriendBorrowRequest(BaseModel):
    owner_did: str
    resource_type: ResourceType
    resource_id: str
    duration_hours: int | None = None


# Skills (§28 §3.6 packaging spec)
class SkillItem(BaseModel):
    skill_id: str
    qualified_name: str
    name: str
    version: str
    owner_did: str
    description: str
    source: SkillSource
    fingerprint: str
    examples_count: int = 0
    personality_traits: list[str] = Field(default_factory=list)
    recommended_models: list[str] = Field(default_factory=list)
    installed: bool | None = None
    borrowed: bool | None = None


class SkillLendPermissions(BaseModel):
    mode: SkillLendMode
    max_duration_minutes: int
    pin_to_ipfs: bool | None = None
    recipient_pubkey_b64: str | None = None


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    system_prompt: str
    version: str | None = None
    few_shot_examples: list[dict[str, str]] | None = None
    preference_overlay: dict[str, Any] | None = None
    tool_call_templates: list[dict[str, Any]] | None = None
    personality_traits: list[str] | None = None
    recommended_models: list[str] | None = None
    expiry_hours: int | None = None
    owner_did: str | None = None


class SkillCreateResponse(BaseModel):
    skill_id: str
    qualified_name: str
    owner_did: str
    version: str
    fingerprint: str
    examples_count: int


class SkillListResponse(BaseModel):
    own_did: str
    owned: list[SkillItem] = Field(default_factory=list)
    available_to_borrow: list[SkillItem] = Field(default_factory=list)


class SkillLendRequest(BaseModel):
    skill_id: str
    permissions: SkillLendPermissions
    expiry_hours: int | None = None


class SkillLendResponse(BaseModel):
    skill_id: str
    qualified_name: str
    max_duration_minutes: int
    ipfs_cid: str | None = None
    encrypted_b64: str | None = None
    sender_pubkey_b64: str | None = None


class SkillBorrowRequest(BaseModel):
    owner_did: str
    skill_name: str
    qualified_name: str
    duration_minutes: int
    per_request_approved: bool | None = None
    emergency_flag: bool | None = None
    borrower_did: str | None = None


class SkillBorrowResponse(BaseModel):
    session_id: str
    qualified_name: str
    owner_did: str
    borrower_did: str
    skill_id: str
    started_at: int
    expires_at: int
    duration_minutes: int
    ipfs_cid: str | None = None
    skill_package_fingerprint: str
    permission_reason: str
    used_fallback: bool


class SkillSessionItem(BaseModel):
    session_id: str
    skill_id: str
    skill_name: str
    qualified_name: str
    owner_did: str
    borrower_did: str
    status: SkillSessionStatus
    started_at: int
    expires_at: int
    proxy_endpoint: str
    wiped: bool


class SkillSessionsResponse(BaseModel):
    own_did: str
    sessions: list[SkillSessionItem] = Field(default_factory=list)


class EndSessionRequest(BaseModel):
    session_id: str
    reason: str | None = None


class EndSessionResponse(BaseModel):
    session_id: str
    status: str
    destroy_reason: str | None = None
    destroyed_at: int | None = None
    ledger_entry_id: str | None = None


# Attest
class AttestEntry(BaseModel):
    uid: str
    schema_: str = Field(alias="schema")
    timestamp: int
    chain: str

    model_config = {"populate_by_name": True}


class AttestHistoryResponse(BaseModel):
    history: list[AttestEntry] = Field(default_factory=list)


class AttestCreateRequest(BaseModel):
    schema_: str = Field(alias="schema")
    subject_did: str
    payload: dict[str, Any]
    chain: str | None = None

    model_config = {"populate_by_name": True}


class AttestCreateResponse(BaseModel):
    uid: str
    tx_hash: str | None = None
    chain: str
    timestamp: int


class VaultGetResponse(BaseModel):
    key: str
    value: str | None = None
    updated_at: str | None = None

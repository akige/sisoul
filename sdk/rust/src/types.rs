//! sisoul daemon shared types — serde-driven, 跟 PWA daemon.ts 对齐.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Preference {
    pub key: String,
    pub value: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ListPreferencesResponse {
    #[serde(default)]
    pub items: Vec<Preference>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Goal {
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub progress: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deadline: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ListGoalsResponse {
    #[serde(default)]
    pub goals: Vec<Goal>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct GoalCreateRequest {
    pub title: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub progress: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deadline: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct GoalUpdateRequest {
    pub id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub progress: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deadline: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Friend {
    pub did: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub handle: Option<String>,
    #[serde(default)]
    pub trust_level: f64,
    pub connected_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ListFriendsResponse {
    #[serde(default)]
    pub friends: Vec<Friend>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FriendAddRequest {
    pub did: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub handle: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trust_level: Option<f64>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ResourceType {
    Skill,
    Data,
    Compute,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FriendLendRequest {
    pub friend_did: String,
    pub resource_type: ResourceType,
    pub resource_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_hours: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FriendBorrowRequest {
    pub owner_did: String,
    pub resource_type: ResourceType,
    pub resource_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_hours: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LeaseResponse {
    pub lease_id: String,
    pub expires_at: u64,
}

// ─── Skills ─────────────────────────────────────────────────────────────────
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SkillSource {
    Owned,
    Borrowed,
    Available,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillItem {
    pub skill_id: String,
    pub qualified_name: String,
    pub name: String,
    pub version: String,
    pub owner_did: String,
    pub description: String,
    pub source: SkillSource,
    pub fingerprint: String,
    #[serde(default)]
    pub examples_count: u32,
    #[serde(default)]
    pub personality_traits: Vec<String>,
    #[serde(default)]
    pub recommended_models: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub installed: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub borrowed: Option<bool>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum SkillLendMode {
    StrongTieAuto,
    PerRequest,
    EmergencyOnly,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillLendPermissions {
    pub mode: SkillLendMode,
    pub max_duration_minutes: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pin_to_ipfs: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recipient_pubkey_b64: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FewShotExample {
    pub user: String,
    pub assistant: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SkillCreateRequest {
    pub name: String,
    pub description: String,
    pub system_prompt: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub few_shot_examples: Option<Vec<FewShotExample>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preference_overlay: Option<HashMap<String, serde_json::Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub personality_traits: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_models: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expiry_hours: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub owner_did: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillCreateResponse {
    pub skill_id: String,
    pub qualified_name: String,
    pub owner_did: String,
    pub version: String,
    pub fingerprint: String,
    pub examples_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SkillListResponse {
    pub own_did: String,
    #[serde(default)]
    pub owned: Vec<SkillItem>,
    #[serde(default)]
    pub available_to_borrow: Vec<SkillItem>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillLendRequest {
    pub skill_id: String,
    pub permissions: SkillLendPermissions,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expiry_hours: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillLendResponse {
    pub skill_id: String,
    pub qualified_name: String,
    pub max_duration_minutes: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ipfs_cid: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub encrypted_b64: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sender_pubkey_b64: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillBorrowRequest {
    pub owner_did: String,
    pub skill_name: String,
    pub qualified_name: String,
    pub duration_minutes: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub per_request_approved: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub emergency_flag: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub borrower_did: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillBorrowResponse {
    pub session_id: String,
    pub qualified_name: String,
    pub owner_did: String,
    pub borrower_did: String,
    pub skill_id: String,
    pub started_at: u64,
    pub expires_at: u64,
    pub duration_minutes: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ipfs_cid: Option<String>,
    pub skill_package_fingerprint: String,
    pub permission_reason: String,
    pub used_fallback: bool,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SkillSessionStatus {
    Active,
    Expired,
    Ended,
    Wiped,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillSessionItem {
    pub session_id: String,
    pub skill_id: String,
    pub skill_name: String,
    pub qualified_name: String,
    pub owner_did: String,
    pub borrower_did: String,
    pub status: SkillSessionStatus,
    pub started_at: u64,
    pub expires_at: u64,
    pub proxy_endpoint: String,
    pub wiped: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SkillSessionsResponse {
    pub own_did: String,
    #[serde(default)]
    pub sessions: Vec<SkillSessionItem>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EndSessionRequest {
    pub session_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EndSessionResponse {
    pub session_id: String,
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub destroy_reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub destroyed_at: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ledger_entry_id: Option<String>,
}

// ─── Attest ─────────────────────────────────────────────────────────────────
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttestEntry {
    pub uid: String,
    pub schema: String,
    pub timestamp: u64,
    pub chain: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AttestHistoryResponse {
    #[serde(default)]
    pub history: Vec<AttestEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttestCreateRequest {
    pub schema: String,
    pub subject_did: String,
    pub payload: HashMap<String, serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chain: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttestCreateResponse {
    pub uid: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tx_hash: Option<String>,
    pub chain: String,
    pub timestamp: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct VaultGetResponse {
    pub key: String,
    #[serde(default)]
    pub value: Option<String>,
    #[serde(default)]
    pub updated_at: Option<String>,
}

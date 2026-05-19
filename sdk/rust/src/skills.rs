//! Skills API — §28 §3.6 packaging spec.
//!
//! 注意: /sisoul/skill/* 路径已含 /sisoul 前缀, 用 absolute=true.

use crate::client::SisoulClient;
use crate::errors::{Result, SisoulError};
use crate::types::{
    EndSessionRequest, EndSessionResponse, SkillBorrowRequest, SkillBorrowResponse,
    SkillCreateRequest, SkillCreateResponse, SkillItem, SkillLendRequest, SkillLendResponse,
    SkillListResponse, SkillSessionItem, SkillSessionStatus, SkillSessionsResponse,
};

pub struct SkillsAPI<'a> {
    c: &'a SisoulClient,
}

impl<'a> SkillsAPI<'a> {
    pub fn new(c: &'a SisoulClient) -> Self {
        Self { c }
    }

    pub fn list(&self) -> Result<SkillListResponse> {
        self.c.get_json("/sisoul/skill/list", &[], true)
    }

    pub fn owned(&self) -> Result<Vec<SkillItem>> {
        Ok(self.list()?.owned)
    }

    pub fn available(&self) -> Result<Vec<SkillItem>> {
        Ok(self.list()?.available_to_borrow)
    }

    pub fn create(&self, req: &SkillCreateRequest) -> Result<SkillCreateResponse> {
        if req.name.is_empty() {
            return Err(SisoulError::InvalidArgument("skills.create: name required".into()));
        }
        if req.system_prompt.is_empty() {
            return Err(SisoulError::InvalidArgument(
                "skills.create: system_prompt required".into(),
            ));
        }
        self.c.post_json("/sisoul/skill/create", req, true)
    }

    pub fn lend(&self, req: &SkillLendRequest) -> Result<SkillLendResponse> {
        if req.skill_id.is_empty() {
            return Err(SisoulError::InvalidArgument("skills.lend: skill_id required".into()));
        }
        self.c.post_json("/sisoul/skill/lend", req, true)
    }

    pub fn borrow(&self, req: &SkillBorrowRequest) -> Result<SkillBorrowResponse> {
        if req.owner_did.is_empty() {
            return Err(SisoulError::InvalidArgument("skills.borrow: owner_did required".into()));
        }
        if req.qualified_name.is_empty() {
            return Err(SisoulError::InvalidArgument(
                "skills.borrow: qualified_name required".into(),
            ));
        }
        self.c.post_json("/sisoul/skill/borrow", req, true)
    }

    pub fn sessions(&self) -> Result<Vec<SkillSessionItem>> {
        let r: SkillSessionsResponse = self.c.get_json("/sisoul/skill/sessions", &[], true)?;
        Ok(r.sessions)
    }

    pub fn active_sessions(&self) -> Result<Vec<SkillSessionItem>> {
        Ok(self
            .sessions()?
            .into_iter()
            .filter(|s| s.status == SkillSessionStatus::Active)
            .collect())
    }

    pub fn end_session(&self, session_id: &str, reason: Option<&str>) -> Result<EndSessionResponse> {
        if session_id.is_empty() {
            return Err(SisoulError::InvalidArgument(
                "skills.end_session: session_id required".into(),
            ));
        }
        let body = EndSessionRequest {
            session_id: session_id.to_string(),
            reason: reason.map(String::from),
        };
        self.c.post_json("/sisoul/skill/end-session", &body, true)
    }
}

//! Attest API.

use crate::client::SisoulClient;
use crate::errors::{Result, SisoulError};
use crate::types::{
    AttestCreateRequest, AttestCreateResponse, AttestEntry, AttestHistoryResponse,
};

pub struct AttestAPI<'a> {
    c: &'a SisoulClient,
}

impl<'a> AttestAPI<'a> {
    pub fn new(c: &'a SisoulClient) -> Self {
        Self { c }
    }

    pub fn history(&self) -> Result<Vec<AttestEntry>> {
        let r: AttestHistoryResponse = self.c.get_json("/attest/history", &[], false)?;
        Ok(r.history)
    }

    pub fn create(&self, req: &AttestCreateRequest) -> Result<AttestCreateResponse> {
        if req.schema.is_empty() {
            return Err(SisoulError::InvalidArgument("attest.create: schema required".into()));
        }
        if req.subject_did.is_empty() {
            return Err(SisoulError::InvalidArgument("attest.create: subject_did required".into()));
        }
        self.c.post_json("/attest/create", req, false)
    }

    pub fn by_schema(&self, schema: &str) -> Result<Vec<AttestEntry>> {
        Ok(self.history()?.into_iter().filter(|e| e.schema == schema).collect())
    }

    pub fn since(&self, timestamp_sec: u64) -> Result<Vec<AttestEntry>> {
        Ok(self.history()?.into_iter().filter(|e| e.timestamp >= timestamp_sec).collect())
    }
}

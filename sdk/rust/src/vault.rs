//! Vault API.

use crate::client::SisoulClient;
use crate::errors::{Result, SisoulError};
use crate::types::{ListPreferencesResponse, Preference, VaultGetResponse};

pub struct VaultAPI<'a> {
    c: &'a SisoulClient,
}

impl<'a> VaultAPI<'a> {
    pub fn new(c: &'a SisoulClient) -> Self {
        Self { c }
    }

    pub fn list(&self) -> Result<Vec<Preference>> {
        let r: ListPreferencesResponse = self.c.get_json("/preferences/list", &[], false)?;
        Ok(r.items)
    }

    pub fn get(&self, key: &str) -> Result<Option<String>> {
        if key.is_empty() {
            return Err(SisoulError::InvalidArgument("vault.get: key required".into()));
        }
        let r: VaultGetResponse = self.c.get_json("/preferences/get", &[("key", key)], false)?;
        Ok(r.value)
    }

    pub fn set(&self, key: &str, value: &str) -> Result<()> {
        if key.is_empty() {
            return Err(SisoulError::InvalidArgument("vault.set: key required".into()));
        }
        let body = serde_json::json!({ "key": key, "value": value });
        self.c.post_unit("/preferences/set", &body, false)
    }

    pub fn delete(&self, key: &str) -> Result<()> {
        if key.is_empty() {
            return Err(SisoulError::InvalidArgument("vault.delete: key required".into()));
        }
        let body = serde_json::json!({ "key": key });
        self.c.post_unit("/preferences/delete", &body, false)
    }
}

//! Top-level Sisoul daemon client (blocking reqwest).

use std::time::Duration;

use reqwest::blocking::Client;
use serde::de::DeserializeOwned;
use serde::Serialize;
use url::Url;

use crate::attest::AttestAPI;
use crate::errors::{classify_http, Result, SisoulError};
use crate::friends::FriendsAPI;
use crate::goals::GoalsAPI;
use crate::skills::SkillsAPI;
use crate::vault::VaultAPI;

pub const DEFAULT_BASE_URL: &str = "http://localhost:8088/sisoul";
pub const DEFAULT_TIMEOUT_SECS: u64 = 30;

#[derive(Clone)]
pub struct SisoulClient {
    pub(crate) base_url: String,
    pub(crate) http: Client,
    pub(crate) timeout: Duration,
}

impl SisoulClient {
    pub fn new(base_url: impl Into<String>) -> Result<Self> {
        Self::with_timeout(base_url, Duration::from_secs(DEFAULT_TIMEOUT_SECS))
    }

    pub fn with_timeout(base_url: impl Into<String>, timeout: Duration) -> Result<Self> {
        let mut base = base_url.into();
        while base.ends_with('/') {
            base.pop();
        }
        // 验证 url 合法
        let _ = Url::parse(&base)?;
        let http = Client::builder()
            .timeout(timeout)
            .build()
            .map_err(SisoulError::from)?;
        Ok(Self {
            base_url: base,
            http,
            timeout,
        })
    }

    /// 仅 unit-test 用: 指定一个 reqwest::blocking::Client (适配 mockito server URL).
    pub fn with_http_client(base_url: impl Into<String>, http: Client) -> Result<Self> {
        let mut base = base_url.into();
        while base.ends_with('/') {
            base.pop();
        }
        let _ = Url::parse(&base)?;
        Ok(Self {
            base_url: base,
            http,
            timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
        })
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    // ─── sub-API factories ────────────────────────────────────────────────
    pub fn vault(&self) -> VaultAPI<'_> {
        VaultAPI::new(self)
    }
    pub fn goals(&self) -> GoalsAPI<'_> {
        GoalsAPI::new(self)
    }
    pub fn friends(&self) -> FriendsAPI<'_> {
        FriendsAPI::new(self)
    }
    pub fn skills(&self) -> SkillsAPI<'_> {
        SkillsAPI::new(self)
    }
    pub fn attest(&self) -> AttestAPI<'_> {
        AttestAPI::new(self)
    }

    // ─── low-level ────────────────────────────────────────────────────────
    pub(crate) fn url_for(&self, path: &str, absolute: bool) -> Result<String> {
        if absolute {
            // path 形如 /sisoul/skill/list — 用 base_url 的 origin 拼.
            let parsed = Url::parse(&self.base_url)?;
            let scheme = parsed.scheme();
            let host = parsed.host_str().ok_or_else(|| {
                SisoulError::InvalidArgument("base_url missing host".to_string())
            })?;
            let port_part = parsed
                .port()
                .map(|p| format!(":{}", p))
                .unwrap_or_default();
            Ok(format!("{}://{}{}{}", scheme, host, port_part, path))
        } else {
            Ok(format!("{}{}", self.base_url, path))
        }
    }

    pub(crate) fn get_json<T: DeserializeOwned>(
        &self,
        path: &str,
        params: &[(&str, &str)],
        absolute: bool,
    ) -> Result<T> {
        let url = self.url_for(path, absolute)?;
        let resp = self
            .http
            .get(&url)
            .query(params)
            .header("Accept", "application/json")
            .send()
            .map_err(handle_reqwest_err)?;
        process_response(resp, path)
    }

    pub(crate) fn post_json<B: Serialize, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
        absolute: bool,
    ) -> Result<T> {
        let url = self.url_for(path, absolute)?;
        let resp = self
            .http
            .post(&url)
            .header("Content-Type", "application/json")
            .header("Accept", "application/json")
            .json(body)
            .send()
            .map_err(handle_reqwest_err)?;
        process_response(resp, path)
    }

    pub(crate) fn post_unit<B: Serialize>(&self, path: &str, body: &B, absolute: bool) -> Result<()> {
        let url = self.url_for(path, absolute)?;
        let resp = self
            .http
            .post(&url)
            .header("Content-Type", "application/json")
            .json(body)
            .send()
            .map_err(handle_reqwest_err)?;
        process_response::<serde_json::Value>(resp, path).map(|_| ())
    }
}

fn handle_reqwest_err(e: reqwest::Error) -> SisoulError {
    if e.is_timeout() {
        SisoulError::Timeout { timeout_ms: 30_000 }
    } else {
        SisoulError::Network(e.to_string())
    }
}

fn process_response<T: DeserializeOwned>(
    resp: reqwest::blocking::Response,
    path: &str,
) -> Result<T> {
    let status = resp.status().as_u16();
    if !resp.status().is_success() {
        let body = resp.text().ok();
        return Err(classify_http(status, path, body));
    }
    let bytes = resp.bytes().map_err(SisoulError::from)?;
    if bytes.is_empty() {
        // 试图返 () via serde_json::Value::Null
        return serde_json::from_str("null").map_err(SisoulError::from);
    }
    serde_json::from_slice(&bytes).map_err(SisoulError::from)
}

//! sisoul SDK error hierarchy.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum SisoulError {
    #[error("daemon {path} → {status}{}", body.as_deref().map(|b| format!(": {}", &b[..b.len().min(200)])).unwrap_or_default())]
    Daemon {
        status: u16,
        path: String,
        body: Option<String>,
    },

    #[error("auth failed at {path} → {status}")]
    Auth { status: u16, path: String },

    #[error("network error: {0}")]
    Network(String),

    #[error("timeout after {timeout_ms}ms")]
    Timeout { timeout_ms: u64 },

    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    #[error("decode error: {0}")]
    Decode(String),

    #[error("invalid url: {0}")]
    Url(#[from] url::ParseError),
}

pub type Result<T> = std::result::Result<T, SisoulError>;

impl From<reqwest::Error> for SisoulError {
    fn from(e: reqwest::Error) -> Self {
        if e.is_timeout() {
            SisoulError::Timeout { timeout_ms: 0 }
        } else if e.is_decode() {
            SisoulError::Decode(e.to_string())
        } else {
            SisoulError::Network(e.to_string())
        }
    }
}

impl From<serde_json::Error> for SisoulError {
    fn from(e: serde_json::Error) -> Self {
        SisoulError::Decode(e.to_string())
    }
}

pub(crate) fn classify_http(status: u16, path: &str, body: Option<String>) -> SisoulError {
    if status == 401 || status == 403 {
        SisoulError::Auth {
            status,
            path: path.to_string(),
        }
    } else {
        SisoulError::Daemon {
            status,
            path: path.to_string(),
            body,
        }
    }
}

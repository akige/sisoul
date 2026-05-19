//! sisoul-client — Rust SDK for the Sisoul daemon.
//!
//! ```no_run
//! use sisoul_client::SisoulClient;
//!
//! let c = SisoulClient::new("http://localhost:8088/sisoul").unwrap();
//! let prefs = c.vault().list().unwrap();
//! let skills = c.skills().owned().unwrap();
//! ```

pub mod attest;
pub mod client;
pub mod errors;
pub mod friends;
pub mod goals;
pub mod skills;
pub mod types;
pub mod vault;

pub use client::SisoulClient;
pub use errors::{SisoulError, Result};
pub use types::*;

# sisoul-client (Rust)

Rust SDK for the Sisoul daemon. Blocking by default, async feature optional.

## Install

```toml
[dependencies]
sisoul-client = "0.1"
```

## Quick Start

```rust
use sisoul_client::SisoulClient;

let c = SisoulClient::new("http://localhost:8088/sisoul").unwrap();

let prefs = c.vault().list().unwrap();
let goals = c.goals().list().unwrap();
let friends = c.friends().strong_ties(0.7).unwrap();
let owned = c.skills().owned().unwrap();
let attests = c.attest().history().unwrap();
```

## Modules

- `client.vault()` — preferences read/write
- `client.goals()` — list/add/update/delete/bump_progress
- `client.friends()` — list/add/remove/lend/borrow/strong_ties
- `client.skills()` — owned/create/lend/borrow/sessions/end_session
- `client.attest()` — history/create/by_schema/since

## Errors (`SisoulError` enum, thiserror)

- `Daemon { status, path, body }` — non-2xx
- `Auth { status, path }` — 401/403
- `Network(String)` — transport failure
- `Timeout { timeout_ms }` — exceeded configured timeout
- `InvalidArgument(String)` — local validation
- `Decode(String)` — serde failure

## Test

```bash
cargo test
```

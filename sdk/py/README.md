# sisoul-client (Python)

Python SDK for the Sisoul daemon.

## Install

```bash
pip install sisoul-client
```

## Quick Start

```python
from sisoul_client import SisoulClient

with SisoulClient(base_url="http://localhost:8088/sisoul") as c:
    prefs = c.vault.list()
    goals = c.goals.list()
    friends = c.friends.strong_ties(0.7)
    owned = c.skills.owned()
    attests = c.attest.history()
```

## Modules

- `client.vault` — preferences read/write
- `client.goals` — list/add/update/delete/bump_progress
- `client.friends` — list/add/remove/lend/borrow/strong_ties
- `client.skills` — owned/create/lend/borrow/sessions/end_session
- `client.attest` — history/create/by_schema/since

## Errors

- `DaemonError` — non-2xx
- `AuthError(DaemonError)` — 401/403
- `NetworkError` — transport-level failure
- `TimeoutError(NetworkError)` — exceeded `timeout`

## Test

```bash
pip install -e ".[dev]"
pytest
```

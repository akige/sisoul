# @sisoul/client

TypeScript SDK for the Sisoul daemon.

## Install

```bash
npm i @sisoul/client
```

## Quick Start

```ts
import { SisoulClient } from "@sisoul/client";

const c = new SisoulClient({ baseUrl: "http://localhost:8088/sisoul" });

const prefs = await c.vault.list();
const goals = await c.goals.list();
const friends = await c.friends.strongTies(0.7);
const owned = await c.skills.owned();
const attests = await c.attest.history();
```

## Modules

- `client.vault` — preferences read/write
- `client.goals` — list/add/update/delete/bumpProgress
- `client.friends` — list/add/remove/lend/borrow/strongTies
- `client.skills` — owned/create/lend/borrow/sessions/endSession
- `client.attest` — history/create/bySchema/since

## Errors

- `DaemonError` — non-2xx response
- `AuthError extends DaemonError` — 401/403
- `NetworkError` — fetch failed
- `TimeoutError extends NetworkError` — request exceeded `timeout`

## Test

```bash
npm test
```

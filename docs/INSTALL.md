# Installing sisoul (alpha v1.0)

> Tested on macOS 14+, Ubuntu 22.04+, WSL2. Windows native is `beta v1.2 (T+2m)`.
> See [Roadmap](../README.md#roadmap) for what each milestone ships.

## Requirements

- **Python 3.11+** (macOS default `3.9` does not work — use `brew install python@3.12` or `pyenv install 3.12.x`)
- `git`
- ~600 MB disk for venv + kubo IPFS node
- Optional: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` if you want LLM (else retrieval-only mode works without)

## 4 steps (verified end-to-end 2026-06-06)

```bash
# 1. clone the repo + create a Python 3.11+ venv
git clone https://github.com/akige/sisoul
cd sisoul
python3.12 -m venv .venv
source .venv/bin/activate

# 2. editable install with the 4 alpha feature groups
pip install --upgrade pip
pip install -e '.[daemon,crypto,chat,llm]'

# 3. (recommended) install a wrapper so `sisoul` works from any shell
#    without needing to activate the venv first
mkdir -p ~/.local/bin
cat > ~/.local/bin/sisoul <<EOF
#!/usr/bin/env bash
exec $PWD/.venv/bin/sisoul "\$@"
EOF
chmod +x ~/.local/bin/sisoul
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.zshrc   # or ~/.bashrc
exec $SHELL                                              # reload PATH

# 4. verify
sisoul --version           # → sisoul 1.0.0-alpha
sisoul self-check          # → 8 checks all green
```

## Initialise your vault + founder agent

```bash
# generate your BIP-39 seed (write the 12 words down on paper — losing them
# means losing your vault, there is no backdoor)
sisoul init --goals "试试 sisoul,玩 P2P AI,看 @founder"

# install the founder-agent persona into your vault
# (this copies vault-template/founder/{system_prompt, cases, lessons} into
#  ~/.sisoul/founder/ — all public, no PII, you can edit any of it)
sisoul founder init --from vault-template/founder

# chat — retrieval-only mode (no API key needed, reads vault cases directly)
sisoul founder chat "为什么 sisoul 不发币?"

# chat — real LLM mode (any of 9 providers; bring your own key)
export ANTHROPIC_API_KEY=sk-ant-...
SISOUL_RSI_PROVIDER=anthropic sisoul founder chat "比较 sisoul 跟 ChatGPT"
```

## Borrow / lend LLM between two daemons (A3 · v1.0-stable)

Lender Alice runs a daemon with her own API key; borrower Bob (no key) sends
a borrow request to Alice's `did:key`. The request travels on a per-DID
GossipSub topic over kubo IPFS — no central directory, no public relay.

```bash
# Both sides — start the daemon (it auto-embeds kubo on mac/wsl/win)
sisoul daemon &
sisoul net status        # should show ≥3 swarm peers within ~60s

# Alice (lender) — register a permission for Bob
sisoul friend add @bob   # resolves the EAS username → bob's did:key
sisoul lend perm set @bob --mode per-request --quota-1k 5

# Bob (borrower) — send a borrow request
sisoul borrow @alice --amount 1000 --model claude-haiku
# → publishes on /sisoul/lend/v1/<sha256(alice_did):16> over GossipSub
# Alice's daemon ingests it into LendStore; her PWA / CLI shows pending.

# Alice — approve / deny manually
sisoul lend list                  # shows pending
sisoul lend approve <request-id>  # publishes ack on /sisoul/lend-ack/v1/<bob>

# v1.1 — optional USDT auto-approve for micropay mode
sisoul lend perm set @bob --mode micropay --usdt-per-1k 0.05 --usdt-payout TQ...
sisoul lend auto-approve enable       # opt-in, persisted to vault
sisoul lend auto-approve status       # confirms ENABLED
# Restart Alice's daemon; her LendAutoApprover polls TronGrid every 30s.
# Once Bob pays 0.05 USDT to TQ..., Alice's daemon auto-approves + acks.
```

The lend transport never touches a server we run. Topics are derived from a
SHA-256 of the lender/borrower DID; GossipSub routes via kubo peers; the
USDT chain-watcher uses the public TronGrid HTTP API (read-only, no key).

## Troubleshooting

### `sisoul: command not found`

Either:
- you ran `pip install -e .` but didn't activate the venv → run `source .venv/bin/activate`, or
- you skipped step 3 (the wrapper) → install the wrapper now and `exec $SHELL`

Verify with `which sisoul` → should print `~/.local/bin/sisoul`.

### `ImportError: No module named 'fastapi'` / `'mnemonic'` / `'click'`

You missed the extras groups. Re-run:

```bash
pip install -e '.[daemon,crypto,chat,llm]'
```

### `Python 3.9.6 not in '>=3.11'`

macOS ships Python 3.9. Install Python 3.11+:

```bash
brew install python@3.12       # macOS
sudo apt install python3.12     # Ubuntu 22.04+
```

Then re-create the venv: `python3.12 -m venv .venv`.

### Daemon won't start

```bash
sisoul daemon &                # start in background
sleep 2
curl http://127.0.0.1:9876/v1/founder/status   # should return 200
```

If port `9876` is taken: `SISOUL_DAEMON_PORT=9877 sisoul daemon &`.

### Founder chat returns the wrong case in Chinese

Fixed in commit `3dec811` (CJK tokenizer + 3 Chinese seed cases). Pull latest
`git pull github main` and re-run `sisoul founder init --from vault-template/founder --force`.

## Uninstall

```bash
# remove the wrapper
rm ~/.local/bin/sisoul

# remove the vault (CAUTION: this deletes your did:key identity)
rm -rf ~/.sisoul

# remove the source clone
cd .. && rm -rf sisoul
```

There is no server-side cleanup because there is no server.

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

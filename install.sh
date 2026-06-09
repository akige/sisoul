#!/usr/bin/env bash
#
# sisoul one-line installer
#   curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash
#
# What it does (BTC-mode, no sudo, no telemetry):
#   1. detect OS (darwin / linux) + shell (zsh / bash)
#   2. ensure Python 3.11+ (mac: brew install python@3.12; linux: hint apt)
#   3. ensure kubo / ipfs (mac: brew install ipfs; linux: hint manual)
#   4. git clone github.com/akige/sisoul → ${SISOUL_HOME:-~/sisoul-app}
#   5. create venv + `pip install -e '.[daemon,crypto,chat,llm]'`
#   6. write wrapper to ~/.local/bin/sisoul + ensure PATH
#   7. verify: `sisoul --version` + `sisoul self-check`
#
# Override target dir:   SISOUL_HOME=/tmp/sisoul-app-test bash install.sh
# Skip prompts (CI):     SISOUL_ASSUME_YES=1 bash install.sh
# Skip kubo install:     SISOUL_SKIP_KUBO=1 bash install.sh
#
set -euo pipefail

# -------- colors --------
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'
  C_CYAN=$'\033[36m'
  C_DIM=$'\033[2m'
else
  C_RESET=''; C_BOLD=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_CYAN=''; C_DIM=''
fi

info()  { printf '%b\n' "${C_CYAN}[*]${C_RESET} $*"; }
ok()    { printf '%b\n' "${C_GREEN}[OK]${C_RESET} $*"; }
warn()  { printf '%b\n' "${C_YELLOW}[!]${C_RESET} $*" >&2; }
err()   { printf '%b\n' "${C_RED}[ERR]${C_RESET} $*" >&2; }
step()  { printf '\n%b\n' "${C_BOLD}${C_BLUE}==>${C_RESET}${C_BOLD} $*${C_RESET}"; }

die() {
  err "$1"
  echo
  err "Install failed. For manual 4-step install see:"
  err "  https://github.com/akige/sisoul/blob/main/docs/INSTALL.md"
  exit 1
}

confirm() {
  # confirm "prompt" -> default YES (Enter = yes)
  local prompt="$1"
  if [[ "${SISOUL_ASSUME_YES:-}" == "1" ]] || ! [[ -t 0 ]]; then
    return 0
  fi
  local reply
  printf '%b ' "${C_YELLOW}?${C_RESET} ${prompt} ${C_DIM}[Y/n]${C_RESET}"
  read -r reply || reply=""
  case "${reply,,}" in
    ""|y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

# bash 3.2 (mac default) has no `${reply,,}`; redefine via tr
confirm() {
  local prompt="$1"
  if [[ "${SISOUL_ASSUME_YES:-}" == "1" ]] || ! [[ -t 0 ]]; then
    return 0
  fi
  local reply
  printf '%b ' "${C_YELLOW}?${C_RESET} ${prompt} ${C_DIM}[Y/n]${C_RESET}"
  read -r reply || reply=""
  reply=$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')
  case "$reply" in
    ""|y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

# -------- 0. banner --------
cat <<'BANNER'

   ___  _                  _
  / __|(_) ___  ___  _  _ | |
  \__ \| |(_-< / _ \| || || |
  |___/|_|/__/ \___/ \_,_||_|

  Decentralized P2P AI agent · Your AI, your data, no cloud.

BANNER

# -------- 1. detect OS + shell --------
step "Step 1/7 — Detect platform"

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="darwin" ;;
  Linux)  PLATFORM="linux" ;;
  *)
    die "Unsupported OS: $OS (sisoul alpha supports macOS / Linux / WSL2)"
    ;;
esac

# detect login shell rc file (we honor $SHELL, fall back to bash)
USER_SHELL="${SHELL:-/bin/bash}"
case "$(basename "$USER_SHELL")" in
  zsh)  SHELL_RC="$HOME/.zshrc"; SHELL_NAME="zsh" ;;
  bash) SHELL_RC="$HOME/.bashrc"; SHELL_NAME="bash" ;;
  fish) SHELL_RC="$HOME/.config/fish/config.fish"; SHELL_NAME="fish" ;;
  *)    SHELL_RC="$HOME/.profile"; SHELL_NAME="sh" ;;
esac

ok "platform=$PLATFORM  shell=$SHELL_NAME  rc=$SHELL_RC"

# -------- 2. ensure Python 3.11+ --------
step "Step 2/7 — Ensure Python 3.11+"

find_python() {
  local candidates=(python3.12 python3.13 python3.11 python3)
  for p in "${candidates[@]}"; do
    if command -v "$p" >/dev/null 2>&1; then
      local ver
      ver=$("$p" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")
      local major minor
      major="${ver%%.*}"
      minor="${ver##*.}"
      if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
        echo "$p"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON=""
if PYTHON=$(find_python); then
  ok "found $PYTHON ($("$PYTHON" --version 2>&1))"
else
  warn "no Python 3.11+ found in PATH"
  if [[ "$PLATFORM" == "darwin" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
      die "Homebrew not installed. Install from https://brew.sh first, then re-run."
    fi
    if confirm "Install python@3.12 via Homebrew (no sudo)?"; then
      info "running: brew install python@3.12"
      brew install python@3.12
      PYTHON=$(find_python) || die "Python 3.11+ still not found after brew install."
      ok "installed $PYTHON"
    else
      die "Python 3.11+ required. Aborting."
    fi
  else
    err "On Debian/Ubuntu run (needs root):"
    err "    sudo apt update && sudo apt install -y python3.12 python3.12-venv"
    err "On Fedora:  sudo dnf install -y python3.12"
    err "On Arch:    sudo pacman -S python"
    die "Re-run install.sh after Python 3.11+ is available."
  fi
fi

# -------- 3. ensure kubo (ipfs) --------
step "Step 3/7 — Ensure kubo (IPFS)"

if [[ "${SISOUL_SKIP_KUBO:-}" == "1" ]]; then
  warn "SISOUL_SKIP_KUBO=1, skipping kubo install"
elif command -v ipfs >/dev/null 2>&1; then
  ok "found ipfs ($(ipfs --version 2>&1 | head -1))"
else
  warn "kubo (ipfs) not installed — sisoul daemon needs it for P2P swarm"
  if [[ "$PLATFORM" == "darwin" ]]; then
    if command -v brew >/dev/null 2>&1 && confirm "Install kubo via 'brew install ipfs'?"; then
      info "running: brew install ipfs"
      brew install ipfs
      ok "installed $(ipfs --version 2>&1 | head -1)"
    else
      warn "skipping kubo install. Install later with: brew install ipfs"
    fi
  else
    warn "On Linux install kubo manually:"
    warn "    https://docs.ipfs.tech/install/command-line/#install-official-binary-distributions"
    warn "    or: snap install ipfs"
    warn "Continuing without kubo (daemon mode will require it later)."
  fi
fi

# -------- 4. clone or update repo --------
step "Step 4/7 — Clone github.com/akige/sisoul"

SISOUL_HOME="${SISOUL_HOME:-$HOME/sisoul-app}"
REPO_URL="https://github.com/akige/sisoul.git"

if [[ -d "$SISOUL_HOME/.git" ]]; then
  info "$SISOUL_HOME already a git clone, running 'git pull'"
  (cd "$SISOUL_HOME" && git pull --ff-only) || warn "git pull failed (continuing with current checkout)"
elif [[ -e "$SISOUL_HOME" ]]; then
  die "$SISOUL_HOME exists and is not a git clone. Move it away and re-run."
else
  if confirm "Clone $REPO_URL into $SISOUL_HOME?"; then
    git clone --depth 1 "$REPO_URL" "$SISOUL_HOME"
  else
    die "User declined clone. Aborting."
  fi
fi
ok "repo at $SISOUL_HOME"

# -------- 5. venv + editable install --------
step "Step 5/7 — Create venv + pip install -e '.[daemon,crypto,chat,llm]'"

VENV="$SISOUL_HOME/.venv"
if [[ ! -d "$VENV" ]]; then
  info "creating venv at $VENV using $PYTHON"
  "$PYTHON" -m venv "$VENV"
else
  ok "venv already exists at $VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

info "upgrading pip"
python -m pip install --quiet --upgrade pip

info "pip install -e '.[daemon,crypto,chat,llm]' (this takes 1-3 min)"
(
  cd "$SISOUL_HOME"
  pip install --quiet -e '.[daemon,crypto,chat,llm]'
)
ok "pip install done"

# -------- 6. wrapper + PATH --------
step "Step 6/7 — Install wrapper + ensure PATH"

mkdir -p "$HOME/.local/bin"
WRAPPER="$HOME/.local/bin/sisoul"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
# sisoul wrapper — auto-generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
exec "$VENV/bin/sisoul" "\$@"
EOF
chmod +x "$WRAPPER"
ok "wrapper → $WRAPPER"

# ensure ~/.local/bin in PATH for this shell rc
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [[ -f "$SHELL_RC" ]] && grep -Fq "$PATH_LINE" "$SHELL_RC"; then
  ok "$SHELL_RC already exports ~/.local/bin"
else
  printf '\n# Added by sisoul install.sh\n%s\n' "$PATH_LINE" >> "$SHELL_RC"
  ok "appended PATH export to $SHELL_RC"
fi

# also export for the rest of THIS script
export PATH="$HOME/.local/bin:$PATH"

# -------- 7. verify --------
step "Step 7/7 — Verify"

if ! command -v sisoul >/dev/null 2>&1; then
  die "sisoul wrapper not in PATH after install. Check $SHELL_RC and 'exec \$SHELL'."
fi

info "running: sisoul --version"
SISOUL_VERSION_OUT=$(sisoul --version 2>&1 || true)
echo "    $SISOUL_VERSION_OUT"
if ! printf '%s' "$SISOUL_VERSION_OUT" | grep -Eq '1\.0(\.|-)'; then
  die "sisoul --version did not report a 1.0.x build (got: $SISOUL_VERSION_OUT)"
fi
ok "sisoul --version ok"

info "running: sisoul self-check"
if sisoul self-check; then
  ok "self-check passed"
else
  warn "self-check returned non-zero. This is often expected on a fresh install"
  warn "(no vault yet, no kubo running). Run 'sisoul init' to bootstrap your vault."
fi

# -------- done --------
cat <<EOF

${C_GREEN}${C_BOLD}sisoul installed.${C_RESET}

  source code      ${C_CYAN}$SISOUL_HOME${C_RESET}
  venv             ${C_CYAN}$VENV${C_RESET}
  wrapper          ${C_CYAN}$WRAPPER${C_RESET}

Next steps (in a fresh shell, or run ${C_BOLD}exec \$SHELL${C_RESET}):

  ${C_CYAN}sisoul init --goals "试试 sisoul"${C_RESET}      generate your did:key + 12-word seed
  ${C_CYAN}sisoul founder init --from vault-template/founder${C_RESET}
  ${C_CYAN}sisoul founder chat "为什么 sisoul 不发币?"${C_RESET}
  ${C_CYAN}sisoul daemon &${C_RESET}                          start the P2P daemon

Docs: ${C_BLUE}https://github.com/akige/sisoul/blob/main/docs/INSTALL.md${C_RESET}

EOF

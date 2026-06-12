#!/usr/bin/env bash
# Wave T3 dev · 真跨 OS e2e runner (主会话 mac 跑).
#
# 编排:
# 1. rsync mac → WSL (Bob) → 拉 wave-T3-openai-compat 分支代码.
# 2. rsync mac → Win11 (Alice) (经 tailscale).
# 3. 重启 WSL Bob systemd 服务 (sisoul-bob-test).
# 4. 重启 Win11 Alice daemon (powershell exec).
# 5. 在 Win11 跑 openai_compat_e2e.py.
#
# 前置 (一次性):
# - WSL 上有 systemd unit sisoul-bob-test.service (从 ops/systemd/).
# - Win11 上有 sisoul-alice-test.ps1 启动脚本 (从 ops/win11/).
# - tailnet 互通 (Mac ↔ WSL ↔ Win11).
#
# 用:
#   bash qa/wsl2_p2p_e2e/openai_compat_test_runner.sh
#
# ⚠️ 本脚本不在 mac 上跑测试 (规则: mac 不部署 sisoul daemon).
# ⚠️ subagent 不许跑这个 (会真起远程进程). 主会话手动跑.

set -euo pipefail

MAC_REPO="${HOME}/sisoul-dev"
WSL_HOST="${SISOUL_T3_WSL_HOST:-192.0.2.15}"
WSL_USER="${SISOUL_T3_WSL_USER:-wsl}"
WSL_PORT="${SISOUL_T3_WSL_PORT:-22}"
WSL_REPO="${SISOUL_T3_WSL_REPO:-/home/wsl/sisoul-dev}"

WIN11_HOST="${SISOUL_T3_WIN11_HOST:-192.0.2.16}"
WIN11_USER="${SISOUL_T3_WIN11_USER:-win}"
WIN11_PORT="${SISOUL_T3_WIN11_PORT:-22}"
WIN11_REPO="${SISOUL_T3_WIN11_REPO:-C:/Users/win/sisoul-dev}"

BOB_URL="http://${WSL_HOST}:9877"
ALICE_URL="http://${WIN11_HOST}:9876"

echo "=== Wave T3 OpenAI-compat e2e runner ==="
echo "Mac repo  : ${MAC_REPO}"
echo "WSL Bob   : ${WSL_USER}@${WSL_HOST}:${WSL_PORT}:${WSL_REPO}"
echo "Win Alice : ${WIN11_USER}@${WIN11_HOST}:${WIN11_PORT}:${WIN11_REPO}"

# 1. mac → WSL rsync (源码)
echo ""
echo "--- [1/5] rsync mac → WSL Bob ---"
rsync -avz --delete \
    --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
    --exclude='*.egg-info' --exclude='.pytest_cache' \
    -e "ssh -p ${WSL_PORT}" \
    "${MAC_REPO}/src/" "${WSL_USER}@${WSL_HOST}:${WSL_REPO}/src/"

# 2. mac → Win11 rsync (源码)
echo ""
echo "--- [2/5] rsync mac → Win11 Alice ---"
rsync -avz --delete \
    --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
    --exclude='*.egg-info' --exclude='.pytest_cache' \
    -e "ssh -p ${WIN11_PORT}" \
    "${MAC_REPO}/src/" "${WIN11_USER}@${WIN11_HOST}:${WIN11_REPO}/src/"

# e2e script 也推过去
rsync -avz \
    -e "ssh -p ${WIN11_PORT}" \
    "${MAC_REPO}/qa/wsl2_p2p_e2e/openai_compat_e2e.py" \
    "${WIN11_USER}@${WIN11_HOST}:${WIN11_REPO}/qa/wsl2_p2p_e2e/openai_compat_e2e.py"

# 3. 重启 WSL Bob daemon
echo ""
echo "--- [3/5] restart Bob daemon (WSL) ---"
ssh -p "${WSL_PORT}" "${WSL_USER}@${WSL_HOST}" \
    "sudo systemctl restart sisoul-bob-test && sleep 2 && systemctl is-active sisoul-bob-test"

# 4. 重启 Win11 Alice daemon
echo ""
echo "--- [4/5] restart Alice daemon (Win11) ---"
ssh -p "${WIN11_PORT}" "${WIN11_USER}@${WIN11_HOST}" \
    'powershell -ExecutionPolicy Bypass -File C:/Users/win/sisoul-dev/ops/win11/restart-alice-test.ps1'

# Bob/Alice health check
echo ""
echo "--- health check ---"
curl -sf "${BOB_URL}/sisoul/health" | head -c 200 && echo ""
curl -sf "${ALICE_URL}/sisoul/health" | head -c 200 && echo ""

# 5. Win11 跑 e2e
echo ""
echo "--- [5/5] run openai_compat_e2e.py on Win11 ---"
ssh -p "${WIN11_PORT}" "${WIN11_USER}@${WIN11_HOST}" \
    "powershell -Command \"\$env:OPENAI_BASE_URL='${ALICE_URL}/v1'; \$env:OPENAI_API_KEY='sk-fake'; \$env:SISOUL_BORROW_BOB_URL='${BOB_URL}'; \$env:SISOUL_OPENAI_COMPAT_MOCK='1'; cd ${WIN11_REPO}; .\\.venv\\Scripts\\python.exe qa\\wsl2_p2p_e2e\\openai_compat_e2e.py\""

echo ""
echo "=== Wave T3 e2e DONE ==="

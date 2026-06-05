#!/usr/bin/env bash
# sisoul bash smoke test - curl every v2 endpoint, verify OK.
# Use: bash bash_smoke_test.sh
set -euo pipefail

BASE="${SISOUL_DAEMON_BASE:-http://127.0.0.1:9876}"
FAIL=0

check() {
    local name="$1"
    local code="$2"
    if [[ "$code" =~ ^2 ]]; then
        echo "  ✓ $name (HTTP $code)"
    else
        echo "  ✗ $name (HTTP $code)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== sisoul daemon smoke test @ $BASE ==="
echo ""

# Health
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/sisoul/health")
check "/sisoul/health" "$code"

# Metrics
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/sisoul/metrics")
check "/sisoul/metrics" "$code"

# v2 case
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/v2/case")
check "GET /v2/case (list)" "$code"

code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/v2/case" \
    -H "content-type: application/json" \
    -d '{"question":"smoke test","answer":"smoke","did_author":"did:key:z6MkSmoke"}')
check "POST /v2/case (add)" "$code"

code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/v2/case/search/?q=smoke")
check "GET /v2/case/search" "$code"

# v2 skill
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/v2/skill/list")
check "GET /v2/skill/list" "$code"

# v2 provenance
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/v2/provenance/attest" \
    -H "content-type: application/json" \
    -d '{"response_id":"smoke","query":"q","answer":"a","did_answerer":"did:key:z6MkSmoke","cited_cases":[],"network":"mock"}')
check "POST /v2/provenance/attest" "$code"

# v2 debate
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/v2/debate/run" \
    -H "content-type: application/json" \
    -d '{"query":"q","agents":[{"did":"did:key:z6MkA","topic_reputation":0.5},{"did":"did:key:z6MkB","topic_reputation":0.8}],"n_rounds":2}')
check "POST /v2/debate/run" "$code"

# v2 reputation
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/v2/reputation/update" \
    -H "content-type: application/json" \
    -d '{"did":"did:key:z6MkA","topic":"smoke","score_delta":0.1}')
check "POST /v2/reputation/update" "$code"

# v2 growth
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/v2/growth/write" \
    -H "content-type: application/json" \
    -d '{"date":"2026-06-04","cases_added":1}')
check "POST /v2/growth/write" "$code"

code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/v2/growth/last?n=7")
check "GET /v2/growth/last" "$code"

# v2 lesson
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/v2/lesson/distill" \
    -H "content-type: application/json" \
    -d '{"did_owner":"did:key:z6MkA","source_case_ids":["c1","c2"]}')
check "POST /v2/lesson/distill" "$code"

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "✓ ALL SMOKE TESTS PASS"
else
    echo "✗ $FAIL test(s) FAILED"
    exit 1
fi

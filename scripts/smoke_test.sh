#!/usr/bin/env bash
# End-to-end smoke test against a running stack (docker compose up).
# Requires: curl, jq.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000/api/v1}"
SAMPLES="$(cd "$(dirname "$0")/../samples" && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }
expect() { [[ "$1" == "$2" ]] || fail "expected '$2' but got '$1' ($3)"; }

post_json() { # url body -> prints "<http_code> <body>"
  curl -sS -o /tmp/smoke_body -w '%{http_code}' -H 'Content-Type: application/json' -d "$2" "$1"
}

echo "==> health"
expect "$(curl -fsS "$BASE_URL/health/" | jq -r .status)" "ok" "health"

echo "==> upload clean batch"
batch_id=$(curl -fsS -F reference=smoke-clean -F submitted_by=alice \
  -F file=@"$SAMPLES/clean_batch.csv" "$BASE_URL/batches/" | jq -r .id)
echo "    batch $batch_id"

echo "==> process -> PENDING_APPROVAL (outlier reported as warning)"
processed=$(curl -fsS -X POST "$BASE_URL/batches/$batch_id/process/")
expect "$(jq -r .status <<<"$processed")" "PENDING_APPROVAL" "process clean"
expect "$(jq -r '.analysis.issues[0].code' <<<"$processed")" "AMOUNT_OUTLIER" "outlier warning"

echo "==> self-approval is forbidden (400)"
expect "$(post_json "$BASE_URL/batches/$batch_id/approve/" '{"approver":"alice"}')" "400" "self approval"
expect "$(jq -r .code /tmp/smoke_body)" "SELF_APPROVAL_FORBIDDEN" "self approval code"

echo "==> bob approves (200)"
expect "$(post_json "$BASE_URL/batches/$batch_id/approve/" '{"approver":"bob"}')" "200" "approve"
expect "$(jq -r .status /tmp/smoke_body)" "APPROVED" "approved status"

echo "==> carol approves too late (409)"
expect "$(post_json "$BASE_URL/batches/$batch_id/approve/" '{"approver":"carol"}')" "409" "double approval"
expect "$(jq -r .code /tmp/smoke_body)" "INVALID_STATE_TRANSITION" "conflict code"

echo "==> dirty batch is rejected automatically"
dirty_id=$(curl -fsS -F reference=smoke-dirty -F submitted_by=alice \
  -F file=@"$SAMPLES/dirty_batch.csv" "$BASE_URL/batches/" | jq -r .id)
dirty=$(curl -fsS -X POST "$BASE_URL/batches/$dirty_id/process/")
expect "$(jq -r .status <<<"$dirty")" "REJECTED" "dirty batch"
jq '.analysis.issues[] | "\(.row_number) \(.code)"' <<<"$dirty"

echo "==> analytics: overview, timeline and AML over approved batches"
overview=$(curl -fsS "$BASE_URL/analytics/overview/?granularity=DAY")
[[ "$(jq -r .transaction_count <<<"$overview")" -ge 1 ]] || fail "overview has no transactions"
curl -fsS "$BASE_URL/analytics/timeline/" | jq -e '.days | length >= 1' >/dev/null || fail "timeline"
curl -fsS "$BASE_URL/analytics/aml/" | jq -e '.alerts' >/dev/null || fail "aml"

echo "==> analytics: reconciliation with an ERP ledger"
recon=$(curl -fsS -F file=@"$SAMPLES/analitica/erp_mayor_bancos_2026_09.csv" \
  -F default_currency=EUR "$BASE_URL/analytics/reconciliation/")
jq -r '"    \(.ledger_entries) entries, \(.result.matches | length) matched"' <<<"$recon"

echo "==> unknown batch (404)"
code=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/batches/00000000-0000-0000-0000-000000000000/")
expect "$code" "404" "not found"

echo "All smoke checks passed."

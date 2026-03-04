#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
  echo "[FAIL] $1" >&2
  exit 1
}

check_probe() {
  local path="$1"
  local expect_route="$2"
  local expect_upstream="$3"
  local hdr="$TMP_DIR/hdr_probe_$(echo "$expect_route" | tr -c 'a-zA-Z0-9' '_').txt"
  local body="$TMP_DIR/body_probe_$(echo "$expect_route" | tr -c 'a-zA-Z0-9' '_').txt"

  local code
  code="$(curl -sS -m 5 -D "$hdr" -o "$body" -w '%{http_code}' "$BASE_URL$path")"
  [[ "$code" == "200" ]] || fail "probe $path http_code=$code"

  grep -qi "^X-Channel-Route: $expect_route" "$hdr" || fail "probe $path missing X-Channel-Route=$expect_route"
  grep -qi "^X-Route-Upstream: $expect_upstream" "$hdr" || fail "probe $path missing X-Route-Upstream=$expect_upstream"
}

check_proxy_header() {
  local path="$1"
  local expect_route="$2"
  local expect_upstream="$3"
  local hdr="$TMP_DIR/hdr_proxy_$(echo "$expect_route" | tr -c 'a-zA-Z0-9' '_').txt"
  local body="$TMP_DIR/body_proxy_$(echo "$expect_route" | tr -c 'a-zA-Z0-9' '_').txt"

  # backend may return 2xx/4xx/5xx depending on auth/config, we only verify route headers
  curl -sS -m 5 -D "$hdr" -o "$body" "$BASE_URL$path" >/dev/null || true

  grep -qi "^X-Channel-Route: $expect_route" "$hdr" || fail "proxy $path missing X-Channel-Route=$expect_route"
  grep -qi "^X-Route-Upstream: $expect_upstream" "$hdr" || fail "proxy $path missing X-Route-Upstream=$expect_upstream"
}

check_probe "/_channel_probe/qq" "qq" "127.0.0.1:18790"
check_probe "/_channel_probe/feishu" "feishu" "127.0.0.1:28789"
check_proxy_header "/webhook/qq/v1/models" "qq" "127.0.0.1:18790"
check_proxy_header "/webhook/feishu/v1/models" "feishu" "127.0.0.1:28789"

echo "[OK] channel routing isolation check passed"

#!/usr/bin/env bash
set -euo pipefail

base_url="${ARENA_LOCAL_URL:-http://127.0.0.1:8765}"
status="$(curl --fail --silent --show-error --max-time 5 "$base_url/health")"
python3 - <<'PY' "$status"
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("status") not in {"ok", "healthy"}:
    raise SystemExit(f"unexpected health response: {payload!r}")
print("center health: ok")
PY

if command -v systemctl >/dev/null 2>&1; then
  systemctl is-active --quiet agent-cooperation
  echo "systemd service: active"
fi

if command -v caddy >/dev/null 2>&1; then
  caddy validate --config /etc/caddy/Caddyfile >/dev/null
  echo "caddy config: valid"
fi

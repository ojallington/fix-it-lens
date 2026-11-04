#!/usr/bin/env bash
# The first-day HTTPS check: serve the probe on https://<host>.ts.net:8443 until the phone posts one result, then stop.
# The tailnet port is open only while this runs.
set -euo pipefail
cd "$(dirname "$0")/../.."
out="${FIXIT_PRIVATE:-$HOME/Projects/hackathons/fix-it-lens-private}/probe/https.jsonl"
count() { if [ -f "$out" ]; then wc -l <"$out"; else echo 0; fi; }
before=$(count)
uv run --quiet --with websockets python deploy/probe/probe.py --port 8790 --out "$out" &
pid=$!
trap 'tailscale serve --https=8443 off >/dev/null 2>&1 || true; kill "$pid" 2>/dev/null || true' EXIT
sleep 2
tailscale serve --bg --https=8443 http://127.0.0.1:8790 >/dev/null
host=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
echo "Open https://$host:8443/ on the phone and tap Run."
for _ in $(seq 1 900); do
  if [ "$(count)" -gt "$before" ]; then tail -n 1 "$out"; exit 0; fi
  sleep 1
done
echo "no result in 15 min" >&2
exit 1

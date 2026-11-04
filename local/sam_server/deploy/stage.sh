#!/usr/bin/env bash
# Bring up local/sam_server on the GPU host, one step at a time, once you have access to facebook/sam3.1.
# Run from the repo root on the app host, with FIXIT_LOCAL_SAM_URL=http://<gpu-host>:8088 set; each step says what
# it did and stops on the first error.
#
#   local/sam_server/deploy/stage.sh env       # code to /opt/fixit-sam, its own uv env (torch cu128, sam3); no GPU
#   local/sam_server/deploy/stage.sh fetch     # token over scp (never argv), weights + sha256 check, local marker
#   local/sam_server/deploy/stage.sh service   # install + start the unit; /health must say weights
#   local/sam_server/deploy/stage.sh measure   # one segment on a synthetic frame: load time and the MiB it holds
#
# The model loads on the first request and unloads after SAM_IDLE_S (900 s) idle. `measure` says how much of the GPU
# it holds, so you can check it fits beside whatever else runs there.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# The GPU host and port: FIXIT_LOCAL_SAM_URL, as calls/config.py reads it (config/models.toml's default is
# localhost, for a GPU on the app host itself). SAM_HOST overrides the ssh target.
SAM_URL=${FIXIT_LOCAL_SAM_URL:-$(python3 -c 'import tomllib
print(tomllib.load(open("config/models.toml", "rb"))["backend"]["local"]["sam_url"])')}
SAM_HOSTNAME=$(python3 -c 'import sys, urllib.parse as u; print(u.urlsplit(sys.argv[1]).hostname)' "$SAM_URL")
HOST=${SAM_HOST:-root@$SAM_HOSTNAME}
DEST=/opt/fixit-sam
TOKEN="$HOME/.config/fleet/hf-read.token"
MARKER_DIR="${SAM_WEIGHTS:-$HOME/.cache/fixit/sam3.1}"   # calls/backends/local.health stats this on the app host

say() { printf 'stage %s: %s\n' "$1" "$2"; }

case "${1:-}" in
env)
  ssh "$HOST" "mkdir -p $DEST/local $DEST/weights $DEST/hf"
  rsync -a --delete --exclude __pycache__ --exclude .venv local/sam_server/ "$HOST:$DEST/local/sam_server/"
  rsync -a local/__init__.py "$HOST:$DEST/local/__init__.py"
  ssh "$HOST" "test -x /usr/local/bin/uv" || { scp -q "$(readlink -f "$(command -v uv)")" "$HOST:/usr/local/bin/uv"; ssh "$HOST" "chmod 755 /usr/local/bin/uv"; }
  ssh "$HOST" "cd $DEST && UV_PYTHON=3.12 uv sync --project local/sam_server --python-preference managed"
  ssh "$HOST" "cd $DEST && local/sam_server/.venv/bin/python -c 'import torch, sam3; print(\"torch\", torch.__version__, \"cuda\", torch.cuda.is_available())'"
  say env "ok: $DEST on $HOST"
  ;;
fetch)
  test -f "$TOKEN" || { say fetch "no token at $TOKEN yet"; exit 2; }
  [ "$(stat -c %a "$TOKEN")" = 600 ] || { say fetch "$TOKEN must be 0600"; exit 2; }
  ssh "$HOST" "install -d -m 700 /root/.config/fleet"
  scp -q "$TOKEN" "$HOST:/root/.config/fleet/hf-read.token"
  ssh "$HOST" "chmod 600 /root/.config/fleet/hf-read.token"
  ssh "$HOST" "cd $DEST && SAM_WEIGHTS=$DEST/weights local/sam_server/.venv/bin/python -m local.sam_server.fetch" \
    | tee /dev/stderr | grep -q '^verified ' || { say fetch "weights did not verify"; exit 3; }
  mkdir -p "$MARKER_DIR"
  ssh "$HOST" "cat $DEST/weights/verified.json" > "$MARKER_DIR/fetched.json"
  say fetch "ok: weights verified on $HOST; marker $MARKER_DIR/fetched.json"
  ;;
service)
  scp -q local/sam_server/deploy/fixit-sam.service "$HOST:/etc/systemd/system/fixit-sam.service"
  ssh "$HOST" "systemctl daemon-reload && systemctl enable --now fixit-sam"
  for _ in $(seq 30); do curl -sf -m 2 $SAM_URL/health >/dev/null && break; sleep 2; done
  curl -sf -m 2 "$SAM_URL"/health | tee /dev/stderr | grep -q '"weights":true' \
    || { say service "/health does not report weights"; exit 4; }
  say service "ok: fixit-sam up at $SAM_URL"
  ;;
measure)
  FIXIT_LOCAL_SAM_URL=$SAM_URL SAM_HOST=$HOST uv run python -m local.sam_server.measure
  ;;
*)
  sed -n '2,12p' "$0"; exit 64
  ;;
esac

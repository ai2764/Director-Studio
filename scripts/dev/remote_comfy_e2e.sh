#!/usr/bin/env bash
# Dev e2e for DS remote-ComfyUI mode against a *running* DS backend (default :8890).
# Starts a temporary CPU ComfyUI (listening on all interfaces) and an auth gate, then runs
# scripts/dev/e2e_remote_check.py twice:
#   1) auth gate: http://127.0.0.1:<gate> requiring "Authorization: Bearer <token>"
#   2) plain http on this machine's non-loopback IP (no auth), like a LAN/Tailscale host
# Both temporary services are stopped on exit and DS is restored to --restore-mode.
#
#   scripts/dev/remote_comfy_e2e.sh [--ds URL] [--comfy-dir DIR] [--python PY] [--restore-mode managed]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DS=http://127.0.0.1:8890
COMFY_DIR=${DS_E2E_COMFY_DIR:-$HERE/../../../runtime/ComfyUI}
PY=${DS_E2E_PYTHON:-python3}
RESTORE=managed
COMFY_PORT=8199 GATE_PORT=18190 TOKEN=e2e-$RANDOM-$RANDOM
while [[ $# -gt 0 ]]; do
  case $1 in
    --ds) DS=$2; shift 2 ;;
    --comfy-dir) COMFY_DIR=$2; shift 2 ;;
    --python) PY=$2; shift 2 ;;
    --restore-mode) RESTORE=$2; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
COMFY_PY=$COMFY_DIR/venv/bin/python
[[ -x $COMFY_PY ]] || COMFY_PY=$COMFY_DIR/../comfy-python/bin/python3
LOGS=${DS_E2E_LOGS:-/tmp/ds-remote-e2e}; mkdir -p "$LOGS"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -n $IP && $IP != 127.* ]] || { echo "no non-loopback IPv4 address found" >&2; exit 1; }
pids=()
cleanup() {
  for p in "${pids[@]}"; do pkill -TERM -s "$p" 2>/dev/null || kill "$p" 2>/dev/null || true; done
  for _ in $(seq 1 40); do
    alive=0; for p in "${pids[@]}"; do pgrep -s "$p" >/dev/null && alive=1; done
    [[ $alive == 0 ]] && break; sleep 0.25
  done
  for p in "${pids[@]}"; do pkill -KILL -s "$p" 2>/dev/null || true; done
  echo "[e2e] temporary services stopped"
}
trap cleanup EXIT
wait_http() { for _ in $(seq 1 $(($2*2))); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 0.5; done; return 1; }

echo "[e2e] ComfyUI $COMFY_DIR on 0.0.0.0:$COMFY_PORT (cpu), log $LOGS/comfy.log"
# each service runs in its own session; the pid file holds the session leader (= session id)
spawn() { local pidf=$1; shift; setsid bash -c 'echo $$ >"$0"; exec "$@"' "$pidf" "$@" & for _ in $(seq 1 40); do [[ -s $pidf ]] && break; sleep 0.05; done; }
rm -f "$LOGS"/*.pid
(cd "$COMFY_DIR" && spawn "$LOGS/comfy.pid" "$COMFY_PY" -u main.py --listen 0.0.0.0 --port $COMFY_PORT --cpu \
   --disable-auto-launch --output-directory "$LOGS/output" >"$LOGS/comfy.log" 2>&1)
pids+=("$(cat "$LOGS/comfy.pid")")
wait_http "http://127.0.0.1:$COMFY_PORT/system_stats" 180 || { echo "ComfyUI did not start" >&2; tail -20 "$LOGS/comfy.log"; exit 1; }

echo "[e2e] auth gate :$GATE_PORT -> :$COMFY_PORT (bearer)"
spawn "$LOGS/gate.pid" "$PY" "$HERE/auth_gate.py" --listen-port $GATE_PORT --upstream "http://127.0.0.1:$COMFY_PORT" \
   --require "Authorization: Bearer $TOKEN" >"$LOGS/auth-gate.log" 2>&1
pids+=("$(cat "$LOGS/gate.pid")")
for _ in $(seq 1 40); do grep -q "auth gate on" "$LOGS/auth-gate.log" 2>/dev/null && break; sleep 0.25; done
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$GATE_PORT/system_stats")
echo "[e2e] gate without token -> HTTP $code (expect 401)"; [[ $code == 401 ]]

echo "== scenario 1: auth gate (bearer) =="
"$PY" "$HERE/e2e_remote_check.py" --ds "$DS" --remote "http://127.0.0.1:$GATE_PORT" --auth bearer --token "$TOKEN" --restore-mode "$RESTORE"
echo "== scenario 2: plain http, non-loopback IP $IP =="
"$PY" "$HERE/e2e_remote_check.py" --ds "$DS" --remote "http://$IP:$COMFY_PORT" --auth none --restore-mode "$RESTORE"
echo "[e2e] both scenarios passed"

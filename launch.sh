#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
executable="$root_dir/DirectorStudio"
if [[ ! -x "$executable" ]]; then
  echo "DirectorStudio is missing or is not executable: $executable" >&2
  exit 1
fi

port="${DS_PORT:-}"
if [[ -z "$port" && -f "$root_dir/.env" ]]; then
  port="$(sed -nE 's/^[[:space:]]*DS_PORT[[:space:]]*=[[:space:]]*"?([0-9]+)"?[[:space:]]*$/\1/p' "$root_dir/.env" | tail -n 1)"
fi
port="${port:-8790}"
export DS_PORT="$port"
timeout_sec="${DS_STARTUP_TIMEOUT_SEC:-60}"
url="http://127.0.0.1:$port"

child_pid=""
stop_child() {
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "$child_pid" 2>/dev/null; then
        wait "$child_pid" 2>/dev/null || true
        return
      fi
      sleep 0.1
    done
    kill -KILL "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
}
trap 'stop_child; exit 143' INT TERM

(cd "$root_dir" && exec "$executable") &
child_pid=$!
deadline=$((SECONDS + timeout_sec))
while (( SECONDS < deadline )); do
  if ! kill -0 "$child_pid" 2>/dev/null; then
    if wait "$child_pid"; then
      exit 1
    else
      child_status=$?
      exit "$child_status"
    fi
  fi
  if curl --fail --silent --show-error "$url/api/health" >/dev/null 2>&1; then
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$url" >/dev/null 2>&1 || echo "Open $url in your browser."
    else
      echo "Open $url in your browser."
    fi
    wait "$child_pid"
    exit $?
  fi
  sleep 0.25
done

echo "Director Studio did not become healthy within $timeout_sec seconds." >&2
stop_child
exit 1

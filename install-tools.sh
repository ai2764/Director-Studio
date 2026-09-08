#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python_bin="${DS_PYTHON_EXE:-}"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "Python 3.11 or newer was not found. Install python3 and python3-venv, then run this script again." >&2
  exit 1
fi
if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Director Studio tools require Python 3.11 or newer." >&2
  exit 1
fi

exec "$python_bin" "$root_dir/Install-Tools.py"

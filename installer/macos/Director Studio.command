#!/bin/sh
# Double-click in Finder to start Director Studio (opens the browser).
# First run on a downloaded copy: if macOS blocks it, right-click -> Open, or run
#   xattr -dr com.apple.quarantine "<this folder>"
set -eu
root="$(cd "$(dirname "$0")" && pwd -P)"
if command -v xattr >/dev/null 2>&1 && xattr -p com.apple.quarantine "$root/runtime/python/bin/python3" >/dev/null 2>&1; then
  echo "Removing the download quarantine flag from $root (unsigned, not notarized build)..."
  xattr -dr com.apple.quarantine "$root" || true
fi
exec "$root/director-studio" "$@"

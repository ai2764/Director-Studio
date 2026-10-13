#!/bin/sh
# Add (or with --remove, delete) a desktop menu entry pointing at this extracted package.
set -eu
root="$(cd "$(dirname "$0")" && pwd -P)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
target="$apps/director-studio.desktop"
if [ "${1:-}" = "--remove" ]; then rm -f "$target"; echo "removed $target"; exit 0; fi
mkdir -p "$apps"
sed "s#@ROOT@#$root#g" "$root/director-studio.desktop.in" > "$target"
chmod 0644 "$target"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1 || true
echo "installed $target"

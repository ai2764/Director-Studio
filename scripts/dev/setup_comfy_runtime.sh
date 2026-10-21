#!/usr/bin/env bash
# Dev helper: install a ComfyUI runtime for DS "managed" mode (Linux).
# ComfyUI (GPL-3.0) and KJNodes (GPL-3.0) are cloned and run as separate
# processes; no code from them is copied into Director Studio.
# Usage: scripts/dev/setup_comfy_runtime.sh <target-dir> [cpu|cu128]
set -euo pipefail
TARGET=${1:?target dir, e.g. ../runtime}
FLAVOR=${2:-cpu}
REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$TARGET" && cd "$TARGET"
[[ -d ComfyUI ]] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
[[ -d custom_nodes/ComfyUI-KJNodes ]] || git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes.git custom_nodes/ComfyUI-KJNodes
[[ -e custom_nodes/ds_nodes ]] || ln -s "$REPO_ROOT/comfy_nodes/ds_nodes" custom_nodes/ds_nodes
INDEX=https://download.pytorch.org/whl/$FLAVOR
uv venv --python "${PYTHON:-python3}" venv
uv pip install --python venv/bin/python torch torchvision torchaudio --index-url "$INDEX"
uv pip install --python venv/bin/python -r requirements.txt --extra-index-url "$INDEX" --index-strategy unsafe-best-match
uv pip install --python venv/bin/python -r custom_nodes/ComfyUI-KJNodes/requirements.txt --extra-index-url "$INDEX" --index-strategy unsafe-best-match
echo "Done. Set DS_COMFY_MODE=managed and DS_COMFY_DIR=$PWD (GPU-less dev boxes: DS_COMFY_DEV_CPU=true)."

#!/usr/bin/env bash
set -euo pipefail

# Find status.py relative to script directory (resolving symlinks) or fallback paths
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

if [ -f "$SCRIPT_DIR/status.py" ]; then
    exec python3 "$SCRIPT_DIR/status.py" "$@"
elif [ -f "$HOME/.antigravity/status.py" ]; then
    exec python3 "$HOME/.antigravity/status.py" "$@"
elif [ -f "$HOME/.gemini/antigravity-cli/status.py" ]; then
    exec python3 "$HOME/.gemini/antigravity-cli/status.py" "$@"
elif [ -f "$HOME/configs/agents-config/harnesses/antigravity-cli/status.py" ]; then
    exec python3 "$HOME/configs/agents-config/harnesses/antigravity-cli/status.py" "$@"
else
    echo "Error: status.py not found in SCRIPT_DIR or HOME paths" >&2
    exit 1
fi

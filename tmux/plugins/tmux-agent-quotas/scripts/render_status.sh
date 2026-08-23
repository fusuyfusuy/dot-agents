#!/usr/bin/env bash
set -euo pipefail

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-combined}"
CACHE_DIR="$HOME/.cache/agent-quotas"
MAX_AGE=30

case "$TARGET" in
  claude)
    CACHE_FILE="$CACHE_DIR/claude.txt"
    DEFAULT_TEXT="#[fg=colour244]CC --#[default]"
    ;;
  ocgo)
    CACHE_FILE="$CACHE_DIR/opencode_go.txt"
    DEFAULT_TEXT="#[fg=colour244]OCGO --#[default]"
    ;;
  *)
    CACHE_FILE="$CACHE_DIR/combined.txt"
    DEFAULT_TEXT="#[fg=colour244]CC -- │ OCGO --#[default]"
    ;;
esac

# Check cache freshness & trigger background refresh if needed
NOW=$(date +%s)
NEED_REFRESH=0

if [ ! -f "$CACHE_FILE" ]; then
  NEED_REFRESH=1
else
  # Linux stat -c %Y
  MTIME=$(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)
  AGE=$((NOW - MTIME))
  if [ "$AGE" -gt "$MAX_AGE" ]; then
    NEED_REFRESH=1
  fi
fi

if [ "$NEED_REFRESH" -eq 1 ]; then
  # Spawn python fetcher in background detached to prevent freezing tmux
  (python3 "$CURRENT_DIR/fetch_quotas.py" >/dev/null 2>&1 &)
fi

# Print cached output immediately
if [ -f "$CACHE_FILE" ]; then
  cat "$CACHE_FILE"
else
  printf "%s" "$DEFAULT_TEXT"
fi

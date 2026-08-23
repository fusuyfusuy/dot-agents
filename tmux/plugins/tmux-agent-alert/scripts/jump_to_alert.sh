#!/usr/bin/env bash
set -euo pipefail

ALERTS_DIR="/tmp/tmux-agent-alerts"
[ ! -d "$ALERTS_DIR" ] && exit 0

for f in "$ALERTS_DIR"/pane_*; do
  [ -f "$f" ] || continue
  pane_id=$(jq -r '.pane_id // empty' "$f" 2>/dev/null || true)
  win_id=$(jq -r '.window_id // empty' "$f" 2>/dev/null || true)
  if [ -n "$pane_id" ]; then
    [ -n "$win_id" ] && tmux select-window -t "$win_id" 2>/dev/null || true
    tmux select-pane -t "$pane_id" 2>/dev/null || true
    rm -f "$f" 2>/dev/null || true
    tmux refresh-client -S 2>/dev/null || true
    exit 0
  fi
done

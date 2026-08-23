#!/usr/bin/env bash
set -euo pipefail

target_pane="${1:-}"

if [ -z "$target_pane" ]; then
  target_pane="$(tmux display-message -p '#{pane_id}' 2>/dev/null || true)"
fi
[ -z "$target_pane" ] && exit 0

clean_pane_id="${target_pane//%/pane_}"
ALERTS_DIR="/tmp/tmux-agent-alerts"

if [ -f "$ALERTS_DIR/$clean_pane_id" ]; then
  rm -f "$ALERTS_DIR/$clean_pane_id" 2>/dev/null || true
  # Refresh status bar to remove indicator immediately
  tmux refresh-client -S 2>/dev/null || true
fi

exit 0

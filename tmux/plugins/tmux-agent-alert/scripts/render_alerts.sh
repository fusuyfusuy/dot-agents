#!/usr/bin/env bash
set -euo pipefail

ALERTS_DIR="/tmp/tmux-agent-alerts"
[ ! -d "$ALERTS_DIR" ] && exit 0

alerts=()
for f in "$ALERTS_DIR"/pane_*; do
  [ -f "$f" ] || continue
  # Extract window info if available
  win_info=$(jq -r '.window_info // empty' "$f" 2>/dev/null || true)
  if [ -n "$win_info" ]; then
    alerts+=("$win_info")
  fi
done

if [ ${#alerts[@]} -gt 0 ]; then
  joined=$(IFS=,; echo "${alerts[*]}")
  # Bold alert badge in statusline
  printf "#[fg=colour255,bg=colour160,bold] 🔔 WAITING [%s] #[default]" "$joined"
fi

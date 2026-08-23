#!/usr/bin/env bash
set -euo pipefail

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "$CURRENT_DIR/helpers.sh"

target_pane="${1:-}"
reason="${2:-input_needed}"
target_window="${3:-}"
agent_name="${4:-Agent}"

# Ensure tmux server is alive
if ! tmux info >/dev/null 2>&1; then
  exit 0
fi

# Deduce target pane if not provided
if [ -z "$target_pane" ]; then
  target_pane="$(tmux display-message -p '#{pane_id}' 2>/dev/null || true)"
fi
[ -z "$target_pane" ] && exit 0

clean_pane_id="${target_pane//%/pane_}"

# ─── Debouncing / Rate Limiting (2 seconds cooldown per pane) ────────────────
DEBOUNCE_FILE="/tmp/tmux-agent-alert-${clean_pane_id}.last"
NOW=$(date +%s)
if [ -f "$DEBOUNCE_FILE" ]; then
  LAST_TIME=$(cat "$DEBOUNCE_FILE" 2>/dev/null || echo 0)
  if [ $((NOW - LAST_TIME)) -lt 2 ]; then
    exit 0
  fi
fi
echo "$NOW" > "$DEBOUNCE_FILE" 2>/dev/null || true

# ─── Query Pane & Focus State ────────────────────────────────────────────────
active_pane="$(tmux display-message -p '#{pane_id}' 2>/dev/null || true)"
active_window="$(tmux display-message -p '#{window_id}' 2>/dev/null || true)"
target_win_id="$(tmux display-message -t "$target_pane" -p '#{window_id}' 2>/dev/null || true)"
target_win_info="$(tmux display-message -t "$target_pane" -p '#{window_index}:#{window_name}' 2>/dev/null || echo "$target_pane")"

suppress_active="$(get_tmux_option "@agent_alert_suppress_active" "on")"
always_alert="$(get_tmux_option "@agent_alert_always" "off")"

# If the user is currently focused on the pane, skip unless always_alert is enabled
if [ "$always_alert" != "on" ] && [ "$suppress_active" = "on" ]; then
  if [ "$target_pane" = "$active_pane" ] && [ "$target_win_id" = "$active_window" ]; then
    # Already focused on this pane, no alert needed
    exit 0
  fi
fi

# ─── Record Alert for Status Bar Display ────────────────────────────────────
ALERTS_DIR="/tmp/tmux-agent-alerts"
mkdir -p "$ALERTS_DIR"
cat <<EOF > "$ALERTS_DIR/$clean_pane_id"
{
  "pane_id": "$target_pane",
  "window_id": "$target_win_id",
  "window_info": "$target_win_info",
  "agent": "$agent_name",
  "reason": "$reason",
  "timestamp": $NOW
}
EOF

# ─── Send Terminal Bell over Mosh ───────────────────────────────────────────
# Write ASCII BEL directly to all attached tmux client TTYs
bell_enabled="$(get_tmux_option "@agent_alert_bell" "on")"
if [ "$bell_enabled" = "on" ]; then
  tmux list-clients -F '#{client_tty}' 2>/dev/null | while read -r client_tty; do
    if [ -n "$client_tty" ] && [ -w "$client_tty" ]; then
      printf '\a' > "$client_tty" 2>/dev/null || true
    fi
  done
fi

# ─── Display Tmux Flash Message ─────────────────────────────────────────────
display_msg_enabled="$(get_tmux_option "@agent_alert_display_message" "on")"
if [ "$display_msg_enabled" = "on" ]; then
  tmux display-message -d 3000 "🔔 $agent_name waiting in [$target_win_info]" 2>/dev/null || true
fi

# ─── Out-of-Band Push Notifications (ntfy / webhook) ────────────────────────
ntfy_topic="$(get_tmux_option "@agent_alert_ntfy_topic" "")"
if [ -n "$ntfy_topic" ]; then
  (curl -s -m 3 \
    -H "Title: $agent_name Input Needed" \
    -H "Tags: bell,robot" \
    -H "Priority: high" \
    -d "$agent_name in [$target_win_info] is waiting for your input." \
    "https://ntfy.sh/$ntfy_topic" >/dev/null 2>&1 &)
fi

webhook_url="$(get_tmux_option "@agent_alert_webhook" "")"
if [ -n "$webhook_url" ]; then
  (curl -s -m 3 -X POST \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"🔔 $agent_name in [$target_win_info] is waiting for user input.\"}" \
    "$webhook_url" >/dev/null 2>&1 &)
fi

exit 0

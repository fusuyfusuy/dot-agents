#!/usr/bin/env bash
set -euo pipefail

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/helpers.sh
source "$CURRENT_DIR/scripts/helpers.sh"

agent_alerts_interpolation=(
  "\#{agent_alerts}"
  "#($CURRENT_DIR/scripts/render_alerts.sh)"
)

update_tmux_option() {
  local option="$1"
  local option_value
  option_value="$(get_tmux_option "$option" "")"
  if [ -n "$option_value" ]; then
    local new_option_value="$option_value"
    new_option_value="${new_option_value//${agent_alerts_interpolation[0]}/${agent_alerts_interpolation[1]}}"
    set_tmux_option "$option" "$new_option_value"
  fi
}

setup_bell_defaults() {
  set_tmux_option "monitor-bell" "on"
  set_tmux_option "bell-action" "other"
  set_tmux_option "visual-bell" "off"
  tmux set-window-option -gq window-status-bell-style "fg=colour255,bg=colour160,bold"
}

setup_hooks() {
  tmux set-hook -g alert-bell "run-shell '$CURRENT_DIR/scripts/alert_handler.sh \"#{hook_pane}\" \"bell\" \"#{hook_window}\"'"
  tmux set-hook -g alert-silence "run-shell '$CURRENT_DIR/scripts/alert_handler.sh \"#{hook_pane}\" \"silence\" \"#{hook_window}\"'"
  tmux set-hook -g pane-focus-in "run-shell '$CURRENT_DIR/scripts/clear_alert.sh \"#{pane_id}\"'"
  tmux set-hook -g after-select-pane "run-shell '$CURRENT_DIR/scripts/clear_alert.sh \"#{pane_id}\"'"
  tmux set-hook -g after-select-window "run-shell '$CURRENT_DIR/scripts/clear_alert.sh \"#{pane_id}\"'"
}

setup_keybindings() {
  local jump_key
  jump_key="$(get_tmux_option "@agent_alert_jump_key" "A")"
  if [ -n "$jump_key" ]; then
    tmux bind-key "$jump_key" run-shell "$CURRENT_DIR/scripts/jump_to_alert.sh"
  fi
}

main() {
  chmod +x "$CURRENT_DIR/scripts/alert_handler.sh" \
           "$CURRENT_DIR/scripts/render_alerts.sh" \
           "$CURRENT_DIR/scripts/clear_alert.sh" \
           "$CURRENT_DIR/scripts/jump_to_alert.sh" \
           "$CURRENT_DIR/scripts/helpers.sh"

  setup_bell_defaults
  setup_hooks
  setup_keybindings

  update_tmux_option "status-right"
  update_tmux_option "status-left"
}

main

#!/usr/bin/env bash
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/helpers.sh
source "$CURRENT_DIR/scripts/helpers.sh"

claude_quota_interpolation=(
  "\#{claude_quota}"
  "#($CURRENT_DIR/scripts/render_status.sh claude)"
)

agent_quotas_interpolation=(
  "\#{agent_quotas}"
  "#($CURRENT_DIR/scripts/render_status.sh combined)"
)

opencode_go_interpolation=(
  "\#{opencode_go}"
  "#($CURRENT_DIR/scripts/render_status.sh ocgo)"
)

update_tmux_option() {
  local option="$1"
  local option_value
  option_value="$(get_tmux_option "$option" "")"
  if [ -n "$option_value" ]; then
    local new_option_value="$option_value"
    new_option_value="${new_option_value//${claude_quota_interpolation[0]}/${claude_quota_interpolation[1]}}"
    new_option_value="${new_option_value//${agent_quotas_interpolation[0]}/${agent_quotas_interpolation[1]}}"
    new_option_value="${new_option_value//${opencode_go_interpolation[0]}/${opencode_go_interpolation[1]}}"
    set_tmux_option "$option" "$new_option_value"
  fi
}

main() {
  chmod +x "$CURRENT_DIR/scripts/render_status.sh" \
           "$CURRENT_DIR/scripts/fetch_quotas.py" \
           "$CURRENT_DIR/scripts/helpers.sh"

  update_tmux_option "status-right"
  update_tmux_option "status-left"

  # Initial async warmup
  (python3 "$CURRENT_DIR/scripts/fetch_quotas.py" >/dev/null 2>&1 &)
}

main

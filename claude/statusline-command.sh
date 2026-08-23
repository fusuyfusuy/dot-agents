#!/bin/bash
# Claude Code statusline: cwd + git, context usage, rate-limit quota/reset, model.

input=$(cat)

cwd=$(echo "$input" | jq -r '.workspace.current_dir // .cwd')
model=$(echo "$input" | jq -r '.model.display_name // .model.id // "unknown"')

ctx_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
ctx_part=""
if [ -n "$ctx_pct" ]; then
  ctx_part=$(printf "ctx %.0f%%" "$ctx_pct")
fi

five_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
five_reset=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
week_pct=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
week_reset=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

quota_part=""
if [ -n "$five_pct" ]; then
  five_reset_fmt=$(date -d "@${five_reset}" "+%H:%M" 2>/dev/null)
  quota_part="5h ${five_pct%.*}%→${five_reset_fmt}"
fi
if [ -n "$week_pct" ]; then
  week_reset_fmt=$(date -d "@${week_reset}" "+%a %H:%M" 2>/dev/null)
  if [ -n "$quota_part" ]; then
    quota_part="${quota_part} | 7d ${week_pct%.*}%→${week_reset_fmt}"
  else
    quota_part="7d ${week_pct%.*}%→${week_reset_fmt}"
  fi
fi

# Save rate-limit snapshot to cache for tmux / tools
if [ -n "$five_pct" ] || [ -n "$week_pct" ]; then
  mkdir -p "$HOME/.cache/agent-quotas"
  cat <<EOF > "$HOME/.cache/agent-quotas/claude.json.tmp"
{
  "timestamp": $(date +%s),
  "model": "$model",
  "five_hour_used_pct": ${five_pct:-null},
  "five_hour_resets_at": ${five_reset:-null},
  "seven_day_used_pct": ${week_pct:-null},
  "seven_day_resets_at": ${week_reset:-null}
}
EOF
  mv -f "$HOME/.cache/agent-quotas/claude.json.tmp" "$HOME/.cache/agent-quotas/claude.json" 2>/dev/null || true
fi

# Git info, computed from cwd (not provided by the statusline JSON).
git_part=""
if git -C "$cwd" rev-parse --git-dir >/dev/null 2>&1; then
  branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
  if [ -z "$branch" ]; then
    branch=$(git -C "$cwd" rev-parse --short HEAD 2>/dev/null)
  fi
  dirty=""
  if [ -n "$(git -C "$cwd" status --porcelain 2>/dev/null)" ]; then
    dirty="*"
  fi
  if [ -n "$branch" ]; then
    git_part=" \033[33m(${branch}${dirty})\033[0m"
  fi
fi

dir_display=$(basename "$cwd")

parts=()
[ -n "$ctx_part" ] && parts+=("$ctx_part")
[ -n "$quota_part" ] && parts+=("$quota_part")
info_part=""
if [ ${#parts[@]} -gt 0 ]; then
  joined="${parts[0]}"
  for p in "${parts[@]:1}"; do
    joined="${joined} | ${p}"
  done
  info_part=" \033[2m[${joined}]\033[0m"
fi

printf '\033[34m%s\033[0m%b \033[35m%s\033[0m%b' "$dir_display" "$git_part" "$model" "$info_part"

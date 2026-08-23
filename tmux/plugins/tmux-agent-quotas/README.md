# tmux-agent-quotas

A lightweight, non-blocking tmux plugin that displays real-time remaining subscription quotas for **Antigravity (`agy`)**, **Claude Code (`claude`)**, **OpenCode Go (`ocgo`)** and today's **pi spend ($)** in your tmux status bar.

---

## Features

- **Non-blocking Status Updates**: Tmux status rendering never waits for network or IPC calls (sub-millisecond `cat` from atomic cache).
- **Triple Support**:
  - **Antigravity (`agy`)**: Probes active Language Server or cached `/usage` model quotas. Surfaces the **active gating constraint** (the currently active session model's quota, or the lowest remaining quota bucket across models) with live reset timers.
  - **Claude Code**: Surfaces the 5-hour rolling sprint quota, falling back to the 7-day weekly quota only when 5-hour data isn't available.
  - **Pi (`pi`)**: Today's total spend across all pi sessions, summed from per-message costs persisted in `~/.pi/agent/sessions/**.jsonl` (as computed by pi's own usage totals); resets at local midnight.
  - **OpenCode Go (`ocgo`)**: Tracks rolling (5h), weekly, and monthly quota windows via the official `/zen/go/v1/usage` endpoint. Surfaces whichever constraint is tighter (lowest remaining % first, soonest reset on ties).
- **Adaptive Color Indicators**:
  - High quota remaining (> 50%): `#[fg=green]`
  - Medium quota remaining (20% - 50%): `#[fg=yellow]`
  - Low quota remaining (< 20%): `#[fg=red]`

---

## Installation

### With TPM (Tmux Plugin Manager)

Add the plugin to your `~/.tmux.conf`:

```tmux
set -g @plugin 'tmux-plugins/tpm'
# Local symlinked plugin path:
run-shell ~/.tmux/plugins/tmux-agent-quotas/tmux-agent-quotas.tmux
```

### Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `OPENCODE_GO_API_KEY` | OpenCode Go API key (starts with `sk-opencode-`) | *(none)* |
| `OPENCODE_GO_USAGE_URL` | Override usage endpoint URL | `https://opencode.ai/zen/go/v1/usage` |

### Manual Installation (without TPM)

Clone or link the plugin directory, and add to your `~/.tmux.conf`:

```tmux
# Use #{agy_quota}, #{claude_quota}, #{pi_spend}, or #{agent_quotas}
set -g status-right "#{agent_quotas} | %H:%M"

# Run plugin
run-shell ~/.tmux/plugins/tmux-agent-quotas/tmux-agent-quotas.tmux
```

---

## Status Placeholders

| Placeholder | Output Example | Description |
| :--- | :--- | :--- |
| `#{agy_quota}` | `AGY 12% 2d22h` | Antigravity active gating quota (active model / lowest remaining) |
| `#{claude_quota}` | `CC 38% 28h34m` | Claude Code 5h rolling quota (7d fallback if 5h unavailable) |
| `#{opencode_go}` | `OCGO 45% 3h20m` | OpenCode Go active gating (5h / weekly / monthly whichever is tighter) |
| `#{pi_spend}` | `PI $1.87` | Pi today's total spend across all sessions |
| `#{agent_quotas}` | `AGY 12% │ CC 38% │ OCGO 45% │ PI $1.87` | Combined active gating status segment + pi spend |

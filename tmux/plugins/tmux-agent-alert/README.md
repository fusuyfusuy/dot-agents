# tmux-agent-alert

A lightweight, zero-daemon tmux plugin to alert you when an AI agent (Antigravity, Claude Code, Aider, etc.) is waiting for user input, specifically optimized for remote development over **mosh** and **ssh**.

## Features

- **Terminal Bell Forwarding**: Forwards standard ASCII `\a` (BEL) to attached client TTYs so Mosh delivers audio/visual alerts to your local terminal emulator (Ghostty, iTerm2, Kitty, WezTerm, Alacritty).
- **Status Flash**: Displays a momentary tmux message (`🔔 Agent waiting in [1:agy]`) when input is needed.
- **Statusline Badge**: Provides `#{agent_alerts}` interpolation showing active alerts (e.g. `🔔 WAITING [1:agy]`).
- **Instant Focus Jump (`prefix + A`)**: Press `<prefix> A` to jump immediately to the pane/window awaiting user input.
- **Auto-Clearing**: Alerts clear automatically as soon as you focus the waiting pane or window.
- **Optional Push Notifications**: Native support for [ntfy.sh](https://ntfy.sh) or custom webhook alerts if your laptop is sleeping.

## Installation

Add to your `~/.tmux.conf`:

```tmux
# Status bar integration (optional)
set -g status-right "#{agent_alerts} #{agent_quotas} #[bold]#[fg=colour255]│ #(date +%H:%M) #[default]"

# Load plugin
run-shell ~/.tmux/plugins/tmux-agent-alert/tmux-agent-alert.tmux
```

## Options

| Option | Default | Description |
| :--- | :--- | :--- |
| `@agent_alert_bell` | `"on"` | Ring client terminal bell over mosh |
| `@agent_alert_display_message` | `"on"` | Show tmux flash message |
| `@agent_alert_suppress_active` | `"on"` | Don't alert if you are already looking at the pane |
| `@agent_alert_always` | `"off"` | Alert even if currently focused |
| `@agent_alert_jump_key` | `"A"` | Key to jump to next alerting pane (`prefix + A`) |
| `@agent_alert_ntfy_topic` | `""` | ntfy.sh topic for mobile/desktop push |
| `@agent_alert_webhook` | `""` | Webhook URL for incoming JSON payload |

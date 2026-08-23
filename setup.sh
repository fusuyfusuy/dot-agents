#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --------------------------------------------------------------------------
# agent detection & selection
# --------------------------------------------------------------------------

detect_agents() {
    HAVE_CLAUDE=false
    HAVE_AGY=false
    HAVE_PI=false
    HAVE_OPENCODE=false

    if command -v claude >/dev/null 2>&1 || [ -d "$HOME/.claude" ]; then HAVE_CLAUDE=true; fi
    if command -v agy >/dev/null 2>&1 || [ -d "$HOME/.gemini" ]; then HAVE_AGY=true; fi
    if command -v pi >/dev/null 2>&1 || [ -d "$HOME/.pi" ]; then HAVE_PI=true; fi
    if command -v opencode >/dev/null 2>&1 || [ -d "$HOME/.config/opencode" ]; then HAVE_OPENCODE=true; fi
}

state() { [ "$1" = true ] && echo "installed" || echo "not found"; }

# Map a selection string ('cap', 'all', 'none', ...) onto the install flags.
# 'all' installs whatever was detected; an explicit letter set installs exactly those.
resolve_selection() {
    local sel
    sel=$(echo "${1:-all}" | tr '[:upper:]' '[:lower:]')

    INSTALL_CLAUDE=false
    INSTALL_AGY=false
    INSTALL_PI=false
    INSTALL_OPENCODE=false
    case "$sel" in
    all)
        INSTALL_CLAUDE=$HAVE_CLAUDE
        INSTALL_AGY=$HAVE_AGY
        INSTALL_PI=$HAVE_PI
        INSTALL_OPENCODE=$HAVE_OPENCODE
        ;;
    none) : ;;
    *)
        [[ "$sel" == *c* ]] && INSTALL_CLAUDE=true
        [[ "$sel" == *a* ]] && INSTALL_AGY=true
        [[ "$sel" == *p* ]] && INSTALL_PI=true
        [[ "$sel" == *o* ]] && INSTALL_OPENCODE=true
        ;;
    esac
}

choose_agents() {
    echo "==> Detected agents:"
    printf "    [c] Claude Code              : %s\n" "$(state "$HAVE_CLAUDE")"
    printf "    [a] AGY (Antigravity/Gemini)  : %s\n" "$(state "$HAVE_AGY")"
    printf "    [p] pi                       : %s\n" "$(state "$HAVE_PI")"
    printf "    [o] opencode                 : %s\n" "$(state "$HAVE_OPENCODE")"
    echo

    # Non-interactive override for automation/CI: AGENTS='cap' | 'all' | 'none' | letters.
    if [ -n "${AGENTS:-}" ]; then
        echo "==> AGENTS override: installing for '$AGENTS'."
        resolve_selection "$AGENTS"
        return
    fi

    # Non-interactive (piped/CI): configure everything that is installed, never hang on read.
    if [ ! -t 0 ]; then
        INSTALL_CLAUDE=$HAVE_CLAUDE
        INSTALL_AGY=$HAVE_AGY
        INSTALL_PI=$HAVE_PI
        INSTALL_OPENCODE=$HAVE_OPENCODE
        echo "==> Non-interactive shell: installing for all detected agents."
        return
    fi

    echo "Which agents should I configure here?"
    echo "  Enter letters to select (e.g. 'capo'), 'all', 'none', or leave blank for all detected:"
    read -r sel
    resolve_selection "${sel:-all}"
}

# --------------------------------------------------------------------------
# tmux selection
# --------------------------------------------------------------------------

# Map a tmux selection string ('ps', 'all', 'none', ...) onto the install flags.
# 'p' = plugins (symlink into ~/.tmux/plugins), 's' = statusline (~/.tmux.conf).
resolve_tmux() {
    local sel
    sel=$(echo "${1:-all}" | tr '[:upper:]' '[:lower:]')

    INSTALL_TMUX_PLUGINS=false
    INSTALL_TMUX_STATUSLINE=false
    case "$sel" in
    all)
        INSTALL_TMUX_PLUGINS=true
        INSTALL_TMUX_STATUSLINE=true
        ;;
    none) : ;;
    *)
        [[ "$sel" == *p* ]] && INSTALL_TMUX_PLUGINS=true
        [[ "$sel" == *s* ]] && INSTALL_TMUX_STATUSLINE=true
        ;;
    esac
}

choose_tmux() {
    # Non-interactive override for automation/CI: TMUX_SETUP='ps' | 'all' | 'none' | letters.
    if [ -n "${TMUX_SETUP:-}" ]; then
        echo "==> TMUX_SETUP override: installing '$TMUX_SETUP'."
        resolve_tmux "$TMUX_SETUP"
        return
    fi

    # Non-interactive (piped/CI): install everything, never hang on read.
    if [ ! -t 0 ]; then
        INSTALL_TMUX_PLUGINS=true
        INSTALL_TMUX_STATUSLINE=true
        echo "==> Non-interactive shell: installing tmux plugins + statusline."
        return
    fi

    echo
    echo "Optional tmux integration:"
    echo "  [p] plugins    : symlink tmux-agent-quotas + tmux-agent-alert into ~/.tmux/plugins"
    echo "  [s] statusline : append the agent status bar + plugin hooks to ~/.tmux.conf"
    echo "  Enter letters (e.g. 'ps'), 'all', 'none', or leave blank for all:"
    read -r sel
    resolve_tmux "${sel:-all}"
}

# --------------------------------------------------------------------------
# linking
# --------------------------------------------------------------------------

link_file() {
    local src="$1"
    local dest="$2"

    mkdir -p "$(dirname "$dest")"

    if [ -L "$dest" ] && [ "$(readlink -f "$dest")" = "$(readlink -f "$src")" ]; then
        echo "  [OK] Already symlinked: $dest -> $src"
        return 0
    fi

    if [ -e "$dest" ] && [ ! -L "$dest" ]; then
        local backup="${dest}.bak.$(date +%Y%m%d%H%M%S)"
        echo "  [BACKUP] Existing file backed up to $backup"
        mv "$dest" "$backup"
    fi

    ln -sfn "$src" "$dest"
    echo "  [LINK] Created symlink: $dest -> $src"
}

# Fan out the shared skills (canonical source: skills) into
# an agent's global skill directory. Same source, several targets. Each skill is
# symlinked as a whole directory so it tracks the repo and never drifts; a
# skill's backing script (e.g. mimori) rides along inside the
# symlink and is additionally installed once on PATH by install_shared.
link_skills() {
    local dest_root="$1"
    mkdir -p "$dest_root"
    if [ -d "$SCRIPT_DIR/skills" ]; then
        for skill_dir in "$SCRIPT_DIR"/skills/*/; do
            [ -f "${skill_dir}SKILL.md" ] || continue
            link_file "$skill_dir" "$dest_root/$(basename "$skill_dir")"
        done
    fi
}

# --------------------------------------------------------------------------
# per-agent install
# --------------------------------------------------------------------------

install_shared() {
    # Set executable permissions on scripts
    chmod +x "$SCRIPT_DIR/setup.sh" \
        "$SCRIPT_DIR/skills/mimori/mimori" \
        "$SCRIPT_DIR/antigravity-cli/status.py" \
        "$SCRIPT_DIR/antigravity-cli/statusline.sh" \
        "$SCRIPT_DIR/antigravity-cli/agy-quota-cache.py" \
        "$SCRIPT_DIR/antigravity-cli/agy-sidebar.py" \
        "$SCRIPT_DIR/antigravity-cli/agy-artifacts.py" \
        "$SCRIPT_DIR/antigravity-cli/hooks/guard-destructive.sh" \
        "$SCRIPT_DIR/antigravity-cli/hooks/git-checkpoint.sh" \
        "$SCRIPT_DIR/mcp-servers/mcp-ast/server.js" \
        "$SCRIPT_DIR/claude/statusline-command.sh" \
        "$SCRIPT_DIR/scripts/opencode_quota_manager.py" \
        "$SCRIPT_DIR/scripts/llm_benchmark_aggregator.py" \
        "$SCRIPT_DIR/scripts/opencode_cost_benefit_analyzer.py" \
        "$SCRIPT_DIR/scripts/free_model_ranker.py" \
        "$SCRIPT_DIR/scripts/stealth_model_detector.py"

    mkdir -p "$HOME/.local/bin"

    # CLI Binaries
    link_file "$SCRIPT_DIR/skills/mimori/mimori" "$HOME/.local/bin/mimori"
    link_file "$SCRIPT_DIR/scripts/opencode_quota_manager.py" "$HOME/.local/bin/ocgo"
    link_file "$SCRIPT_DIR/scripts/opencode_cost_benefit_analyzer.py" "$HOME/.local/bin/ocheck"
    link_file "$SCRIPT_DIR/scripts/free_model_ranker.py" "$HOME/.local/bin/fcheck"
    link_file "$SCRIPT_DIR/scripts/stealth_model_detector.py" "$HOME/.local/bin/scheck"
    link_file "$SCRIPT_DIR/scripts/llm_benchmark_aggregator.py" "$HOME/.local/bin/bcheck"
    link_file "$SCRIPT_DIR/antigravity-cli/agy-sidebar.py" "$HOME/.local/bin/agy-sidebar"
    link_file "$SCRIPT_DIR/antigravity-cli/agy-artifacts.py" "$HOME/.local/bin/agy-artifacts"
    link_file "$SCRIPT_DIR/antigravity-cli/agy-artifacts.py" "$HOME/.local/bin/agy-art"
    link_file "$SCRIPT_DIR/antigravity-cli/agy-artifacts.py" "$HOME/.local/bin/art"

    # Herdr Config
    if [ -d "$HOME/.config/herdr" ] || command -v herdr >/dev/null 2>&1; then
        link_file "$SCRIPT_DIR/herdr/config.toml" "$HOME/.config/herdr/config.toml"
    fi
}

install_tmux() {
    local TMUX_DIR="$SCRIPT_DIR/tmux"
    chmod +x "$TMUX_DIR/plugins/tmux-agent-quotas/tmux-agent-quotas.tmux" \
        "$TMUX_DIR/plugins/tmux-agent-quotas/scripts/render_status.sh" \
        "$TMUX_DIR/plugins/tmux-agent-quotas/scripts/fetch_quotas.py" \
        "$TMUX_DIR/plugins/tmux-agent-quotas/scripts/helpers.sh" \
        "$TMUX_DIR/plugins/tmux-agent-alert/tmux-agent-alert.tmux" \
        "$TMUX_DIR/plugins/tmux-agent-alert/scripts/alert_handler.sh" \
        "$TMUX_DIR/plugins/tmux-agent-alert/scripts/render_alerts.sh" \
        "$TMUX_DIR/plugins/tmux-agent-alert/scripts/clear_alert.sh" \
        "$TMUX_DIR/plugins/tmux-agent-alert/scripts/jump_to_alert.sh" \
        "$TMUX_DIR/plugins/tmux-agent-alert/scripts/helpers.sh"

    mkdir -p "$HOME/.tmux/plugins"
    if [ -d "$TMUX_DIR/plugins/tmux-agent-quotas" ]; then
        ln -sfn "$TMUX_DIR/plugins/tmux-agent-quotas" "$HOME/.tmux/plugins/tmux-agent-quotas"
        echo "  [LINK] Linked tmux plugin: $HOME/.tmux/plugins/tmux-agent-quotas"
    fi
    if [ -d "$TMUX_DIR/plugins/tmux-agent-alert" ]; then
        ln -sfn "$TMUX_DIR/plugins/tmux-agent-alert" "$HOME/.tmux/plugins/tmux-agent-alert"
        echo "  [LINK] Linked tmux plugin: $HOME/.tmux/plugins/tmux-agent-alert"
    fi
}

# Append the agent statusline block to ~/.tmux.conf (idempotent; never destroys
# an existing tmux.conf). Requires the plugins symlinked by install_tmux.
install_tmux_statusline() {
    local tmux_conf="$HOME/.tmux.conf"
    local marker="# --- agents-config: agent quotas + alert statusline ---"
    if [ -f "$tmux_conf" ] && grep -qF "$marker" "$tmux_conf"; then
        echo "  [OK] Already configured: $tmux_conf (agent statusline present)"
        return 0
    fi
    {
        echo
        echo "$marker"
        echo 'set -g status-right "#{agent_alerts}#{agent_quotas} #[bold]#[fg=colour255]│ #(date +%H:%M) #[default]"'
        echo 'run-shell ~/.tmux/plugins/tmux-agent-quotas/tmux-agent-quotas.tmux'
        echo 'run-shell ~/.tmux/plugins/tmux-agent-alert/tmux-agent-alert.tmux'
    } >>"$tmux_conf"
    echo "  [APPEND] Configured $tmux_conf (agent statusline + plugin hooks)"
}

install_claude() {
    echo "==> Configuring Claude Code..."
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.claude/CLAUDE.md"
    link_file "$SCRIPT_DIR/claude/settings.json" "$HOME/.claude/settings.json"
    link_file "$SCRIPT_DIR/claude/statusline-command.sh" "$HOME/.claude/statusline-command.sh"
    link_skills "$HOME/.claude/skills"
}

install_agy() {
    echo "==> Configuring AGY (Antigravity / Gemini)..."
    local AGY_DIR="$SCRIPT_DIR/antigravity-cli"
    mkdir -p "$HOME/.gemini/antigravity-cli" "$HOME/.gemini/config/skills" "$HOME/.antigravity"

    # Shared rules
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.gemini/AGENTS.md"

    # Antigravity & Gemini CLI
    link_file "$AGY_DIR/settings.json" "$HOME/.gemini/antigravity-cli/settings.json"
    link_file "$AGY_DIR/keybindings.json" "$HOME/.gemini/antigravity-cli/keybindings.json"
    link_file "$AGY_DIR/config.json" "$HOME/.gemini/config/config.json"
    
    # Resolve MCP server paths dynamically
    link_file "$SCRIPT_DIR/mcp-servers" "$HOME/.gemini/config/mcp-servers"
    mkdir -p "$HOME/.gemini/config"
    sed "s|__MCP_AST_SERVER_PATH__|$HOME/.gemini/config/mcp-servers/mcp-ast/server.js|g" "$AGY_DIR/mcp_config.json" > "$HOME/.gemini/config/mcp_config.json"

    link_file "$AGY_DIR/hooks/hooks.json" "$HOME/.gemini/config/hooks.json"
    link_file "$AGY_DIR/statusline.sh" "$HOME/.gemini/antigravity-cli/statusline.sh"
    link_file "$AGY_DIR/status.py" "$HOME/.antigravity/status.py"
    link_file "$AGY_DIR/agy-quota-cache.py" "$HOME/.antigravity/agy-quota-cache.py"
    link_file "$AGY_DIR/agy-proxy.py" "$HOME/.antigravity/agy-proxy.py"
    chmod +x "$AGY_DIR/agy-proxy.py"

    # User systemd service for persistent proxy bridge
    mkdir -p "$HOME/.config/systemd/user"
    link_file "$AGY_DIR/agy-proxy.service" "$HOME/.config/systemd/user/agy-proxy.service"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    systemctl --user enable --now agy-proxy.service >/dev/null 2>&1 || true

    link_skills "$HOME/.gemini/config/skills"
}

install_pi() {
    echo "==> Configuring pi..."
    mkdir -p "$HOME/.pi/agent/skills"

    # Global instructions
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.pi/agent/AGENTS.md"

    # Skills (directories with SKILL.md) are discovered from ~/.pi/agent/skills/
    link_skills "$HOME/.pi/agent/skills"

    # Pi extensions (global) — pi/extensions/*.ts -> ~/.pi/agent/extensions/
    mkdir -p "$HOME/.pi/agent/extensions"
    if [ -d "$SCRIPT_DIR/pi/extensions" ]; then
        for ext in "$SCRIPT_DIR"/pi/extensions/*.ts; do
            [ -e "$ext" ] || continue
            link_file "$ext" "$HOME/.pi/agent/extensions/$(basename "$ext")"
        done
    fi
}

install_opencode() {
    echo "==> Configuring opencode..."
    local cfg_dir="$HOME/.config/opencode"
    local cfg="$cfg_dir/opencode.jsonc"
    mkdir -p "$cfg_dir"

    # Preserve any user-installed plugins; default to the suite's known pair.
    local plugins='["opencode-gemini-auth@latest", "opencode-statusline@latest"]'
    if [ -f "$cfg" ]; then
        local existing
        existing=$(grep -o '"plugin"[[:space:]]*:[[:space:]]*\[[^]]*\]' "$cfg" || true)
        [ -n "$existing" ] && plugins=$(printf '%s' "$existing" | sed 's/"plugin"[[:space:]]*:[[:space:]]*//')
    fi

    cat >"$cfg" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "plugin": $plugins,
  "instructions": [
    "$SCRIPT_DIR/prompts/AGENTS.md"
  ]
}
EOF
    echo "  [WRITE] Wrote $cfg (instructions -> AGENTS.md)"

    # opencode discovers skills from ~/.claude/skills; fan them out there.
    link_skills "$HOME/.claude/skills"
}

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

detect_agents
choose_agents
choose_tmux

install_shared
if [ "$INSTALL_CLAUDE" = true ]; then install_claude; fi
if [ "$INSTALL_AGY" = true ]; then install_agy; fi
if [ "$INSTALL_PI" = true ]; then install_pi; fi
if [ "$INSTALL_OPENCODE" = true ]; then install_opencode; fi
if [ "$INSTALL_TMUX_STATUSLINE" = true ]; then
    [ "$INSTALL_TMUX_PLUGINS" = false ] && INSTALL_TMUX_PLUGINS=true &&
        echo "==> Statusline requires the plugins; installing them too."
fi
if [ "$INSTALL_TMUX_PLUGINS" = true ]; then install_tmux; fi
if [ "$INSTALL_TMUX_STATUSLINE" = true ]; then install_tmux_statusline; fi

echo "==> Setup completed successfully!"

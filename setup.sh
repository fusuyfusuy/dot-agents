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
    HAVE_OMP=false
    HAVE_CLINE=false
    HAVE_MUSE=false

    if command -v claude >/dev/null 2>&1 || [ -d "$HOME/.claude" ]; then HAVE_CLAUDE=true; fi
    if command -v agy >/dev/null 2>&1 || [ -d "$HOME/.gemini" ]; then HAVE_AGY=true; fi
    if command -v pi >/dev/null 2>&1 || [ -d "$HOME/.pi" ]; then HAVE_PI=true; fi
    if command -v opencode >/dev/null 2>&1 || [ -d "$HOME/.config/opencode" ]; then HAVE_OPENCODE=true; fi
    if command -v omp >/dev/null 2>&1 || [ -d "$HOME/.omp" ]; then HAVE_OMP=true; fi
    if command -v cline >/dev/null 2>&1 || [ -d "$HOME/.cline" ]; then HAVE_CLINE=true; fi
    if command -v muse >/dev/null 2>&1 || [ -d "$HOME/.config/muse" ]; then HAVE_MUSE=true; fi
}

state() { [ "$1" = true ] && echo "installed" || echo "not found"; }

resolve_selection() {
    local sel
    sel=$(echo "${1:-all}" | tr '[:upper:]' '[:lower:]')

    INSTALL_CLAUDE=false
    INSTALL_AGY=false
    INSTALL_PI=false
    INSTALL_OPENCODE=false
    INSTALL_OMP=false
    INSTALL_CLINE=false
    INSTALL_MUSE=false
    case "$sel" in
    all)
        INSTALL_CLAUDE=$HAVE_CLAUDE
        INSTALL_AGY=$HAVE_AGY
        INSTALL_PI=$HAVE_PI
        INSTALL_OPENCODE=$HAVE_OPENCODE
        INSTALL_OMP=$HAVE_OMP
        INSTALL_CLINE=$HAVE_CLINE
        INSTALL_MUSE=$HAVE_MUSE
        ;;
    none) : ;;
    *)
        [[ "$sel" == *c* ]] && INSTALL_CLAUDE=true
        [[ "$sel" == *a* ]] && INSTALL_AGY=true
        [[ "$sel" == *p* ]] && INSTALL_PI=true
        [[ "$sel" == *o* ]] && INSTALL_OPENCODE=true
        [[ "$sel" == *m* ]] && INSTALL_OMP=true
        [[ "$sel" == *l* ]] && INSTALL_CLINE=true
        [[ "$sel" == *u* ]] && INSTALL_MUSE=true
        true
        ;;
    esac
}
choose_agents() {
    echo "==> Detected agents:"
    printf "    [c] Claude Code              : %s\n" "$(state "$HAVE_CLAUDE")"
    printf "    [a] AGY (Antigravity/Gemini)  : %s\n" "$(state "$HAVE_AGY")"
    printf "    [p] pi                       : %s\n" "$(state "$HAVE_PI")"
    printf "    [o] opencode                 : %s\n" "$(state "$HAVE_OPENCODE")"
    printf "    [m] omp (Oh My Pi)           : %s\n" "$(state "$HAVE_OMP")"
    printf "    [l] Cline                    : %s\n" "$(state "$HAVE_CLINE")"
    printf "    [u] Muse Code                : %s\n" "$(state "$HAVE_MUSE")"
    echo

    # Non-interactive override for automation/CI: AGENTS='capomlu' | 'all' | 'none' | letters.
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
        INSTALL_OMP=$HAVE_OMP
        INSTALL_CLINE=$HAVE_CLINE
        INSTALL_MUSE=$HAVE_MUSE
        echo "==> Non-interactive shell: installing for all detected agents."
        return
    fi

    echo "Which agents should I configure here?"
    echo "  Enter letters to select (e.g. 'capomlu', 'all', 'none', or leave blank for all detected):"
    read -r sel
    resolve_selection "${sel:-all}"
}

# ------------------------------------------
# linking
# ------------------------------------------

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

# A skill owned by skills-library is opt-in: it is activated per project via
# scripts/enable-skills.sh, never globally. Fanning it out to a global harness
# root would defeat that and silently re-pollute every session's prompt, so the
# name is refused loudly here instead.
library_owns_skill() {
    local name="$1" grp
    [ -d "$SCRIPT_DIR/skills-library" ] || return 1
    for grp in "$SCRIPT_DIR"/skills-library/*/; do
        [ -d "$grp" ] || continue
        [ -f "${grp}${name}/SKILL.md" ] && return 0
        if [ -f "${grp}SKILL.md" ] && [ "$(basename "$grp")" = "$name" ]; then
            return 0
        fi
    done
    return 1
}

# Global MCP config fan-out.
#
# NOT a symlink, deliberately. ~/.pi/agent/mcp.json is also written at runtime
# by pi's MCP tooling, so a symlink would push those writes back into the repo
# (dirtying it) and an MCP setup panel could rewrite our source of truth. The
# repo file wins on every install instead: it is copied over the target unless
# the target is already byte-identical.
#
# The set is closed on purpose — akatsuki, context-mode, mimori. Every global
# server's tools are advertised into every session's context, so adding a fourth
# server is a deliberate edit to mcp/global.json, never a
# side effect of enabling a server for one project (scripts/enable-mcp.sh).
link_global_mcp() {
    local dest="$1"
    local src="$SCRIPT_DIR/mcp/global.json"

    if [ ! -f "$src" ]; then
        echo "  [SKIP] No global MCP config at $src"
        return 0
    fi

    mkdir -p "$(dirname "$dest")"

    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        echo "  [OK] Global MCP config already current: $dest"
        return 0
    fi

    cp "$src" "$dest"
    echo "  [WRITE] Global MCP config: $dest <- $src"
}

# Fan out shared skills (canonical source: skills) into an agent's global
# skill directory. Each skill directory is symlinked directly so it tracks
# updates. Skills owned by skills-library are skipped (opt-in per project).
# Native MCP servers (e.g. mimori) are skipped since they are exposed via MCP.
link_skills() {
    local dest_root="$1" name
    mkdir -p "$dest_root"
    if [ -d "$SCRIPT_DIR/skills" ]; then
        for skill_dir in "$SCRIPT_DIR"/skills/*/; do
            [ -f "${skill_dir}SKILL.md" ] || continue
            name="$(basename "$skill_dir")"
            if [ "$name" = "mimori" ]; then
                continue # Exposed natively via Model Context Protocol (MCP)
            fi
            if library_owns_skill "$name"; then
                echo "  [SKIP] $name is opt-in (skills-library) — activate with scripts/enable-skills.sh"
                continue
            fi
            link_file "$skill_dir" "$dest_root/$name"
        done
    fi
}

# --------------------------------------------------------------------------
# per-agent install
# --------------------------------------------------------------------------

install_shared() {
    # Set executable permissions on scripts
    chmod +x "$SCRIPT_DIR/setup.sh" \
        "$SCRIPT_DIR/artifacts/art.py" \
        "$SCRIPT_DIR/antigravity-cli/status.py" \
        "$SCRIPT_DIR/antigravity-cli/statusline.sh" \
        "$SCRIPT_DIR/antigravity-cli/agy-quota-cache.py" \
        "$SCRIPT_DIR/antigravity-cli/agy-artifacts.py" \
        "$SCRIPT_DIR/antigravity-cli/agy-auth.py" \
        "$SCRIPT_DIR/antigravity-cli/test_agy_auth.py" \
        "$SCRIPT_DIR/antigravity-cli/hooks/guard-destructive.sh" \
        "$SCRIPT_DIR/antigravity-cli/hooks/git-checkpoint.sh" \
        "$SCRIPT_DIR/claude/statusline-command.sh" \
        "$SCRIPT_DIR/skills/viblog-writer/scripts/publish-post.mjs" \
        "$SCRIPT_DIR/skills/akatsuki/scripts/akatsuki.py" \
        "$SCRIPT_DIR/scripts/enable-mcp.sh" \
        "$SCRIPT_DIR/scripts/enable-skills.sh" \
        "$SCRIPT_DIR/mcp-library/ast/server.js" \
        "$SCRIPT_DIR/mcp-library/ast/test_mcpast.sh"

    mkdir -p "$HOME/.local/bin"

    # CLI Binaries
    if command -v cargo >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/skills/mimori/Cargo.toml" ]; then
        if ! command -v mimori >/dev/null 2>&1; then
            echo "==> Installing mimori via cargo..."
            cargo install --path "$SCRIPT_DIR/skills/mimori" --quiet
        fi
    fi
    if [ -f "$HOME/.cargo/bin/mimori" ]; then
        link_file "$HOME/.cargo/bin/mimori" "$HOME/.local/bin/mimori"
    fi
    link_file "$SCRIPT_DIR/antigravity-cli/agy-auth.py" "$HOME/.local/bin/agy-auth"
    link_file "$SCRIPT_DIR/antigravity-cli/agy-auth.py" "$HOME/.local/bin/agy-switch"
    link_file "$SCRIPT_DIR/antigravity-cli/agy-auth.py" "$HOME/.local/bin/agy-profile"
    link_file "$SCRIPT_DIR/artifacts/art.py" "$HOME/.local/bin/art"
    link_file "$SCRIPT_DIR/artifacts/art.py" "$HOME/.local/bin/agy-artifacts"
    link_file "$SCRIPT_DIR/artifacts/art.py" "$HOME/.local/bin/agy-art"
    if [ -f "$SCRIPT_DIR/skills/viblog-writer/scripts/publish-post.mjs" ]; then
        link_file "$SCRIPT_DIR/skills/viblog-writer/scripts/publish-post.mjs" "$HOME/.local/bin/viblog-publish"
    fi
    if [ -f "$SCRIPT_DIR/skills/akatsuki/scripts/akatsuki.py" ]; then
        link_file "$SCRIPT_DIR/skills/akatsuki/scripts/akatsuki.py" "$HOME/.local/bin/akatsuki"
    fi
    if [ -f "$SCRIPT_DIR/mcp-library/ast/server.js" ]; then
        link_file "$SCRIPT_DIR/mcp-library/ast/server.js" "$HOME/.local/bin/mcp-ast"
    fi
    if [ -f "$SCRIPT_DIR/scripts/enable-mcp.sh" ]; then
        link_file "$SCRIPT_DIR/scripts/enable-mcp.sh" "$HOME/.local/bin/enable-mcp"
    fi
    if [ -f "$SCRIPT_DIR/scripts/enable-skills.sh" ]; then
        link_file "$SCRIPT_DIR/scripts/enable-skills.sh" "$HOME/.local/bin/enable-skill"
        link_file "$SCRIPT_DIR/scripts/enable-skills.sh" "$HOME/.local/bin/enable-skills"
    fi

    # Herdr Config
    if [ -d "$HOME/.config/herdr" ] || command -v herdr >/dev/null 2>&1; then
        link_file "$SCRIPT_DIR/herdr/config.toml" "$HOME/.config/herdr/config.toml"
    fi

    # Secure master secret keys
    if [ -f "$HOME/.secret.keys" ]; then
        chmod 0600 "$HOME/.secret.keys"
    fi
}

install_claude() {
    echo "==> Configuring Claude Code..."
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.claude/CLAUDE.md"
    link_file "$SCRIPT_DIR/claude/settings.json" "$HOME/.claude/settings.json"
    link_file "$SCRIPT_DIR/claude/statusline-command.sh" "$HOME/.claude/statusline-command.sh"
    # No skills: global skill fan-out is AGY + pi only for now.
    # opencode (below) has no skill root of its own and read ~/.claude/skills, so
    # it loses skills too until claude is re-added here.
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

    mkdir -p "$HOME/.gemini/config"
    # AGY reads the same {mcpServers:{name:{command,args}}} shape as pi/omp, so it
    # gets the identical closed set rather than a third hand-maintained file.
    link_global_mcp "$HOME/.gemini/config/mcp_config.json"

    link_file "$AGY_DIR/hooks/hooks.json" "$HOME/.gemini/config/hooks.json"
    link_file "$AGY_DIR/statusline.sh" "$HOME/.gemini/antigravity-cli/statusline.sh"
    link_file "$AGY_DIR/status.py" "$HOME/.gemini/antigravity-cli/status.py"
    link_file "$AGY_DIR/status.py" "$HOME/.antigravity/status.py"
    link_file "$AGY_DIR/agy-quota-cache.py" "$HOME/.antigravity/agy-quota-cache.py"
    link_file "$AGY_DIR/agy-proxy.py" "$HOME/.antigravity/agy-proxy.py"
    link_file "$AGY_DIR/agy-auth.py" "$HOME/.antigravity/agy-auth.py"
    chmod +x "$AGY_DIR/agy-proxy.py" "$AGY_DIR/agy-auth.py"

    # User systemd service for persistent proxy bridge
    mkdir -p "$HOME/.config/systemd/user"
    link_file "$AGY_DIR/agy-proxy.service" "$HOME/.config/systemd/user/agy-proxy.service"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    systemctl --user enable --now agy-proxy.service >/dev/null 2>&1 || true

    # User systemd service & timer for daily token keepalive
    link_file "$AGY_DIR/agy-auth-keepalive.service" "$HOME/.config/systemd/user/agy-auth-keepalive.service"
    link_file "$AGY_DIR/agy-auth-keepalive.timer" "$HOME/.config/systemd/user/agy-auth-keepalive.timer"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    systemctl --user enable --now agy-auth-keepalive.timer >/dev/null 2>&1 || true

    link_skills "$HOME/.gemini/config/skills"
}

install_pi() {
    echo "==> Configuring pi..."
    mkdir -p "$HOME/.pi/agent/skills"

    # Global instructions
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.pi/agent/AGENTS.md"

    # Global MCP servers
    link_global_mcp "$HOME/.pi/agent/mcp.json"

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

    # Pi custom models configuration
    if [ -f "$SCRIPT_DIR/pi/models.json" ]; then
        link_file "$SCRIPT_DIR/pi/models.json" "$HOME/.pi/agent/models.json"
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

    # No skills: opencode discovers skills from ~/.claude/skills, which is no
    # longer fanned out (global skill fan-out is AGY + pi only for now).
}

install_omp() {
    echo "==> Configuring omp (Oh My Pi)..."

    # Global instructions — omp discovers AGENTS.md via native context files.
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.omp/agent/AGENTS.md"

    # Global MCP servers (same closed set as pi — akatsuki + context-mode + mimori)
    link_global_mcp "$HOME/.omp/agent/mcp.json"

    # No skills: global skill fan-out is AGY + pi only for now. omp reads pi's
    # skills via skills.enablePiUser, so it still sees them through pi.

    # Extensions — same jiti API as pi; fan out pi extensions to omp.
    # agy-thinking and timestamps monkey-patch pi's bundled TUI modules under
    # dist/modes/interactive/; on omp's runtime those imports cannot resolve.
    # commandcode-discovery calls pi.registerProvider(), which omp's extension
    # API does not expose (omp discovers that provider natively via
    # models.yml `discovery:`).
    mkdir -p "$HOME/.omp/agent/extensions"
    if [ -d "$SCRIPT_DIR/pi/extensions" ]; then
        for ext in "$SCRIPT_DIR"/pi/extensions/*.ts; do
            [ -e "$ext" ] || continue
            case "$(basename "$ext")" in
            agy-thinking.ts | timestamps.ts | commandcode-discovery.ts)
                continue
                ;;
            esac
            link_file "$ext" "$HOME/.omp/agent/extensions/$(basename "$ext")"
        done
    fi

    # Omp-specific overrides (if any) live in omp/.
    # Do NOT overwrite ~/.omp/agent/config.yml unconditionally — it holds
    # user modelRoles + auth state. Only seed if missing.
    if [ ! -f "$HOME/.omp/agent/config.yml" ] && [ -f "$SCRIPT_DIR/omp/config.yml" ]; then
        link_file "$SCRIPT_DIR/omp/config.yml" "$HOME/.omp/agent/config.yml"
    fi
}

install_cline() {
    echo "==> Configuring Cline..."
    mkdir -p "$HOME/.cline/rules"

    # Global rules & instructions (Cline discovers ~/.cline/rules/, ~/.cline/AGENTS.md, ~/.clinerules)
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.cline/rules/AGENTS.md"
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.cline/AGENTS.md"
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.clinerules"

    # No skills: global skill fan-out is AGY + pi only for now.
}

install_muse() {
    echo "==> Configuring Muse Code..."

    # Global instructions (user scope root; workspace AGENTS.md still wins per-project)
    link_file "$SCRIPT_DIR/prompts/AGENTS.md" "$HOME/.config/muse/AGENTS.md"

    # No skills: global skill fan-out is AGY + pi only for now.
}

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

# Ensure git submodules (e.g. mimori) are initialized and synced
if [ -d "$SCRIPT_DIR/.git" ] && command -v git >/dev/null 2>&1; then
    echo "==> Syncing git submodules..."
    git -C "$SCRIPT_DIR" submodule update --init --recursive --quiet || true
fi

detect_agents
choose_agents

install_shared
if [ "$INSTALL_CLAUDE" = true ]; then install_claude; fi
if [ "$INSTALL_AGY" = true ]; then install_agy; fi
if [ "$INSTALL_PI" = true ]; then install_pi; fi
if [ "$INSTALL_OPENCODE" = true ]; then install_opencode; fi
if [ "$INSTALL_OMP" = true ]; then install_omp; fi
if [ "$INSTALL_CLINE" = true ]; then install_cline; fi
if [ "$INSTALL_MUSE" = true ]; then install_muse; fi

echo "==> Setup completed successfully!"

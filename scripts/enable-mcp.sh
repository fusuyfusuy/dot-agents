#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# enable-mcp.sh — Per-project agent MCP server activator
# ==============================================================================
# Configures opt-in MCP servers for a target project across supported harnesses:
#   1. Antigravity (agy): symlinks into <project>/.agents/plugins/<name>
#   2. Pi agent (pi):     merges into <project>/.pi/mcp.json
#
# Available servers:
#   - ast:      AST pattern rewriting (ast-grep), LSP diagnostics, blast radius
#   - browser:  Playwright headless browser automation
#   - context7: Up-to-date documentation search (@upstash/context7-mcp)
#   - context:  Dynamic context management & compaction resilience (context-mode)
#
# Usage:
#   ./scripts/enable-mcp.sh list
#   ./scripts/enable-mcp.sh <server|all> [project-dir]
#   ./scripts/enable-mcp.sh disable <server|all> [project-dir]
#   ./scripts/enable-mcp.sh status [project-dir]
#   ./scripts/enable-mcp.sh doctor
# ==============================================================================

REAL_SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$REAL_SCRIPT")" && pwd)"
LIBRARY_ROOT="$(cd "$SCRIPT_DIR/../mcp-library" && pwd)"

KNOWN_SERVERS=(ast browser context7 context akatsuki)

usage() {
    cat <<USAGE
Usage:
  $(basename "$0") list
  $(basename "$0") <server|all> [project-dir]
  $(basename "$0") disable <server|all> [project-dir]
  $(basename "$0") status [project-dir]
  $(basename "$0") doctor

Servers:
  akatsuki  Bidirectional second brain knowledge base access (search, read, services, log)
  ast       AST pattern rewriting via ast-grep, LSP diagnostics, blast radius
  browser   Headless browser automation via Playwright MCP
  context7  Framework and library docs retrieval (@upstash/context7-mcp)
  context   Compaction resilience and dynamic context (context-mode)
  all       Enable/disable all of the above

Harness integrations:
  - Antigravity: <project>/.agents/plugins/<name>
  - Pi agent:    <project>/.pi/mcp.json
USAGE
}

list_servers() {
    echo "Available MCP servers in library ($LIBRARY_ROOT):"
    echo
    for s in "${KNOWN_SERVERS[@]}"; do
        local pjson="$LIBRARY_ROOT/$s/plugin.json"
        local desc=""
        if [ -f "$pjson" ]; then
            desc="$(jq -r '.description // ""' "$pjson" 2>/dev/null || echo "")"
        fi
        printf "  %-12s %s\n" "$s" "$desc"
    done
    echo
}

server_exists() {
    local s="$1"
    [ -d "$LIBRARY_ROOT/$s" ] && [ -f "$LIBRARY_ROOT/$s/mcp_config.json" ]
}

enable_single() {
    local s="$1"
    local target="$2"

    if ! server_exists "$s"; then
        echo "Error: MCP server '$s' not found in library: $LIBRARY_ROOT" >&2
        return 1
    fi

    local src_dir="$LIBRARY_ROOT/$s"
    local agy_dest_dir="$target/.agents/plugins"
    # pi's project-scope config is <project>/.pi/mcp.json. The adapter resolves it
    # as join(configDirName, "mcp.json") from cwd — there is no .pi/agent layer at
    # project scope, so ~/.pi/agent/mcp.json is the GLOBAL file, not this one.
    local pi_agent_dir="$target/.pi"
    local pi_mcp_file="$pi_agent_dir/mcp.json"

    # 1. Antigravity plugin symlink
    mkdir -p "$agy_dest_dir"
    ln -sfn "$src_dir" "$agy_dest_dir/$s"
    echo "  [Antigravity] Linked plugin: $agy_dest_dir/$s -> $src_dir"

    # 2. Pi agent mcp.json merge
    mkdir -p "$pi_agent_dir"
    local srv_json="$src_dir/mcp_config.json"
    if [ -f "$pi_mcp_file" ] && [ -s "$pi_mcp_file" ]; then
        # Merge .mcpServers from library into existing file
        jq --slurpfile extra "$srv_json" '
            .mcpServers = ((.mcpServers // {}) * ($extra[0].mcpServers // {}))
        ' "$pi_mcp_file" >"${pi_mcp_file}.tmp" && mv "${pi_mcp_file}.tmp" "$pi_mcp_file"
    else
        # Initialize with server definitions
        jq '{mcpServers: .mcpServers}' "$srv_json" >"$pi_mcp_file"
    fi
    echo "  [Pi Agent]    Registered in: $pi_mcp_file"
}

disable_single() {
    local s="$1"
    local target="$2"

    local agy_link="$target/.agents/plugins/$s"
    local pi_mcp_file="$target/.pi/mcp.json"

    # 1. Remove Antigravity symlink
    if [ -L "$agy_link" ] || [ -e "$agy_link" ]; then
        rm -rf "$agy_link"
        echo "  [Antigravity] Removed plugin link: $agy_link"
    fi

    # 2. Unregister from Pi agent mcp.json
    local srv_json="$LIBRARY_ROOT/$s/mcp_config.json"
    if [ -f "$pi_mcp_file" ] && [ -f "$srv_json" ]; then
        local keys
        keys="$(jq -r '.mcpServers | keys[]' "$srv_json" 2>/dev/null || true)"
        for k in $keys; do
            jq --arg k "$k" 'del(.mcpServers[$k])' "$pi_mcp_file" >"${pi_mcp_file}.tmp" && mv "${pi_mcp_file}.tmp" "$pi_mcp_file"
        done
        echo "  [Pi Agent]    Unregistered from: $pi_mcp_file"
    fi
}

show_status() {
    local target="$1"
    echo "MCP status for target: $target"
    echo "--- Antigravity (.agents/plugins/) ---"
    if [ -d "$target/.agents/plugins" ]; then
        for s in "${KNOWN_SERVERS[@]}"; do
            if [ -L "$target/.agents/plugins/$s" ]; then
                echo "  [ENABLED]  $s -> $(readlink -f "$target/.agents/plugins/$s")"
            else
                echo "  [DISABLED] $s"
            fi
        done
    else
        echo "  No .agents/plugins directory found."
    fi

    echo "--- Pi Agent (.pi/mcp.json) ---"
    if [ -f "$target/.pi/mcp.json" ]; then
        jq -r '.mcpServers | keys[]' "$target/.pi/mcp.json" 2>/dev/null | while read -r k; do
            echo "  [CONFIGURED] $k"
        done
    else
        echo "  No .pi/mcp.json found."
    fi
}

run_doctor() {
    echo "Running MCP doctor checks..."
    local ok=true

    for s in "${KNOWN_SERVERS[@]}"; do
        local dir="$LIBRARY_ROOT/$s"
        if [ ! -d "$dir" ]; then
            echo "  [FAIL] Missing directory: $dir"
            ok=false
            continue
        fi
        if [ ! -f "$dir/plugin.json" ] || ! jq . "$dir/plugin.json" >/dev/null 2>&1; then
            echo "  [FAIL] Invalid/missing plugin.json in $dir"
            ok=false
        fi
        if [ ! -f "$dir/mcp_config.json" ] || ! jq . "$dir/mcp_config.json" >/dev/null 2>&1; then
            echo "  [FAIL] Invalid/missing mcp_config.json in $dir"
            ok=false
        fi
    done

    # Check dependencies
    if command -v node >/dev/null 2>&1; then
        echo "  [OK] node executable found: $(command -v node)"
    else
        echo "  [WARN] node not found on PATH"
        ok=false
    fi

    if command -v npx >/dev/null 2>&1; then
        echo "  [OK] npx executable found: $(command -v npx)"
    else
        echo "  [WARN] npx not found on PATH"
        ok=false
    fi

    if command -v context-mode >/dev/null 2>&1; then
        echo "  [OK] context-mode executable found: $(command -v context-mode)"
    else
        echo "  [WARN] context-mode not found on PATH"
    fi

    if command -v mcp-ast >/dev/null 2>&1; then
        echo "  [OK] mcp-ast executable found: $(command -v mcp-ast)"
    else
        echo "  [WARN] mcp-ast not found on PATH (will use node script directly)"
    fi

    if [ "$ok" = true ]; then
        echo "Doctor check: ALL PASS"
        return 0
    else
        echo "Doctor check: COMPLETED WITH WARNINGS/ERRORS"
        return 1
    fi
}

# ── Main Entry ────────────────────────────────────────────────────────────────

CMD="${1:-help}"

case "$CMD" in
help | -h | --help)
    usage
    exit 0
    ;;
list)
    list_servers
    exit 0
    ;;
doctor)
    run_doctor
    exit 0
    ;;
status)
    TARGET="${2:-$(pwd)}"
    show_status "$TARGET"
    exit 0
    ;;
disable)
    TARGET_SRV="${2:-}"
    TARGET_DIR="${3:-$(pwd)}"
    if [ -z "$TARGET_SRV" ]; then
        echo "Error: server name or 'all' required. Run '$0 list' to see available." >&2
        exit 1
    fi
    if [ "$TARGET_SRV" = "all" ]; then
        for s in "${KNOWN_SERVERS[@]}"; do
            echo "Disabling $s in $TARGET_DIR..."
            disable_single "$s" "$TARGET_DIR"
        done
    else
        echo "Disabling $TARGET_SRV in $TARGET_DIR..."
        disable_single "$TARGET_SRV" "$TARGET_DIR"
    fi
    exit 0
    ;;
all)
    TARGET_DIR="${2:-$(pwd)}"
    for s in "${KNOWN_SERVERS[@]}"; do
        echo "Enabling $s in $TARGET_DIR..."
        enable_single "$s" "$TARGET_DIR"
    done
    exit 0
    ;;
*)
    TARGET_SRV="$CMD"
    TARGET_DIR="${2:-$(pwd)}"
    if server_exists "$TARGET_SRV"; then
        echo "Enabling $TARGET_SRV in $TARGET_DIR..."
        enable_single "$TARGET_SRV" "$TARGET_DIR"
        exit 0
    else
        echo "Error: Unknown command or server '$TARGET_SRV'." >&2
        usage
        exit 1
    fi
    ;;
esac

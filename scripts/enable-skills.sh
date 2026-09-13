#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# enable-skills.sh — Per-project agent skill activator
# ==============================================================================
# Symlinks library skills into a target project's `.agents/skills/` directory so
# they are active strictly for that project.
#
# pi discovers skills at:
#   global  : ~/.agents/skills/  and  ~/.pi/agent/skills/   (scanned recursively)
#   project : <project>/.agents/skills/                     (trusted projects only)
#
# INVARIANT: the library holds the SINGLE physical copy of each suite skill.
# A library skill must never also exist in the global store, or it leaks into
# every session's system prompt regardless of what is enabled per project.
# `doctor` fails loudly when that invariant (or the suite lists) drift.
#
# Usage:
#   ./scripts/enable-skills.sh interfaces [project-dir]
#   ./scripts/enable-skills.sh matt [project-dir]
#   ./scripts/enable-skills.sh specterops [project-dir]           # all 75
#   ./scripts/enable-skills.sh specterops:bloodhound [project-dir]
#   ./scripts/enable-skills.sh <skill-name> [project-dir]
#   ./scripts/enable-skills.sh list
#   ./scripts/enable-skills.sh doctor
# ==============================================================================

REAL_SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$REAL_SCRIPT")" && pwd)"
LIBRARY_ROOT="$SCRIPT_DIR/../skills-library"
GLOBAL_SKILLS_DIR="${HOME}/.agents/skills"
PI_SKILLS_DIR="${HOME}/.pi/agent/skills"

SUITE="${1:-help}"
TARGET="${2:-$(pwd)}"
DEST="$TARGET/.agents/skills"

SUBSUITES=(bloodhound appsec recon c2 re payloads infra)

# Declared always-global exceptions. A library skill listed here is intentionally
# ALSO kept in the global store, so doctor reports it as deliberate instead of
# silently tolerating a leak. Every OTHER library skill must be absent from the
# global store.
GLOBAL_ALLOWED=()

# Source the installer fans out to every harness root. A library skill present
# here would be re-linked globally on the next setup.sh run — the exact bug that
# put 128 skill descriptions into every session prompt.
FANOUT_DIR="$SCRIPT_DIR/../skills"

# ── library access ────────────────────────────────────────────────────────────

group_dir() {
    case "$1" in
    interfaces | ui) echo "$LIBRARY_ROOT/interfaces" ;;
    matt | pocock) echo "$LIBRARY_ROOT/mattpocock" ;;
    specterops | security) echo "$LIBRARY_ROOT/specterops" ;;
    *) echo "" ;;
    esac
}

LIB_SKILLS=()
# A group dir is normally a folder of skill dirs (specterops/, interfaces/,
# mattpocock/). A group dir that IS a skill (archify/) is its own single member.
load_group_skills() {
    LIB_SKILLS=()
    local dir="$1" p
    [ -d "$dir" ] || return 0
    if [ -f "$dir/SKILL.md" ]; then
        LIB_SKILLS+=("$(basename "$dir")")
        return 0
    fi
    for p in "$dir"/*/; do
        [ -f "${p}SKILL.md" ] || continue
        LIB_SKILLS+=("$(basename "$p")")
    done
}

# Absolute path of a skill inside a group dir, tolerating both shapes above.
resolve_skill() {
    local dir="$1" name="$2"
    if [ -f "$dir/$name/SKILL.md" ]; then
        echo "$dir/$name"
        return 0
    fi
    if [ -f "$dir/SKILL.md" ] && [ "$(basename "$dir")" = "$name" ]; then
        echo "$dir"
        return 0
    fi
    return 1
}

# Suite membership is data, not a second hardcoded listing: a skill is in a suite
# iff it is a SKILL.md-bearing directory in that suite's library folder.
subsuite_skills() {
    case "$1" in
    bloodhound) echo "bloodhound-analysis bloodhound-ad-analysis bloodhound-opengraph bloodhound-query azurehound-analysis openhound-development openhound-github openhound-jamf openhound-okta" ;;
    appsec) echo "cwe-code-review owasp-security-code-review openssf-python-review security-code-review security-review secret-scan webapp-review webapp-qa" ;;
    recon) echo "shodan nmap-parse osint-recon" ;;
    c2) echo "cobalt-strike-aggressor-development cobalt-strike-aggressor-reference cobalt-strike-malleable-c2-development beacon-object-file-development c2-bof-development mythic-implant-development mythic-profiles mythic-translation-containers oc2-bof-script-development oc2-bot-development" ;;
    re) echo "ghidra-mcp-analysis binary-ninja-mcp-analysis" ;;
    payloads) echo "electron-app-audit electron-candidate-discovery electron-install-persistence electron-squirrel-repackage linux-process-injection macos-initial-access com-proxy-triage" ;;
    infra) echo "ssh-ops proxychains-tunnel nftables-allow-source iac-attack-surface sccm-recon sccm-takeover-relay sccmhunter-install-local" ;;
    *) echo "" ;;
    esac
}

find_library_skill() {
    local skill="$1" gdir
    for gdir in "$LIBRARY_ROOT"/*/; do
        gdir="${gdir%/}"
        if resolve_skill "$gdir" "$skill" >/dev/null; then
            resolve_skill "$gdir" "$skill"
            return 0
        fi
    done
    return 1
}

# ── linking ───────────────────────────────────────────────────────────────────

refuse_unsafe_target() {
    mkdir -p "$TARGET"
    local resolved agents_root pi_root
    resolved="$(cd "$TARGET" && pwd)"
    agents_root="${GLOBAL_SKILLS_DIR%/*}" # ~/.agents
    pi_root="${PI_SKILLS_DIR%/*}"         # ~/.pi/agent
    case "$resolved" in
    "$LIBRARY_ROOT" | "$LIBRARY_ROOT"/*)
        echo "✗ Refusing to link into the library itself: $resolved" >&2
        exit 1
        ;;
    "$HOME" | "$GLOBAL_SKILLS_DIR" | "$GLOBAL_SKILLS_DIR"/* | "$agents_root" | \
        "$PI_SKILLS_DIR" | "$PI_SKILLS_DIR"/* | "$pi_root")
        echo "✗ Refusing to link into a global skill root ($resolved) — that is global pollution, not opt-in." >&2
        exit 1
        ;;
    esac
    mkdir -p "$DEST"
}

link_skills() {
    local suite_name="$1" dir="$2" skill src count=0
    load_group_skills "$dir"
    if [ "${#LIB_SKILLS[@]}" -eq 0 ]; then
        echo "✗ No skills found in $dir" >&2
        exit 1
    fi
    refuse_unsafe_target
    for skill in "${LIB_SKILLS[@]}"; do
        src="$(resolve_skill "$dir" "$skill")"
        ln -sfn "$src" "$DEST/$skill"
        count=$((count + 1))
    done
    echo "Enabled $suite_name ($count skills) in $DEST"
}

link_named_skills() {
    local suite_name="$1" skill src missing=0 count=0
    shift
    refuse_unsafe_target
    for skill in "$@"; do
        if src="$(find_library_skill "$skill")"; then
            ln -sfn "$src" "$DEST/$skill"
            count=$((count + 1))
        else
            echo "  ✗ $skill not found in $LIBRARY_ROOT" >&2
            missing=$((missing + 1))
        fi
    done
    [ "$missing" -eq 0 ] || {
        echo "✗ Aborted: $missing suite entries missing from the library." >&2
        exit 1
    }
    echo "Enabled $suite_name ($count skills) in $DEST"
}

link_subsuite() {
    local sub="$1" entries
    read -r -a entries <<<"$(subsuite_skills "$sub")"
    if [ "${#entries[@]}" -eq 0 ]; then
        echo "✗ Unknown subsuite: specterops:$sub" >&2
        usage
    fi
    link_named_skills "SpecterOps $sub" "${entries[@]}"
}

# ── maintenance ───────────────────────────────────────────────────────────────

doctor() {
    local problems=0 gdir name sub entry found allowed
    local all=() covered=()

    echo "Library: $LIBRARY_ROOT"
    for gdir in "$LIBRARY_ROOT"/*/; do
        load_group_skills "$gdir"
        printf '  %-14s %d skills\n' "$(basename "$gdir")" "${#LIB_SKILLS[@]}"
        for name in "${LIB_SKILLS[@]}"; do
            [ -e "$GLOBAL_SKILLS_DIR/$name" ] || continue
            allowed=0
            for entry in "${GLOBAL_ALLOWED[@]}"; do
                if [ "$entry" = "$name" ]; then
                    allowed=1
                    break
                fi
            done
            if [ "$allowed" -eq 1 ]; then
                echo "  · '$name' is a declared always-global skill (not a leak)"
            else
                echo "  ✗ LEAK: '$name' is in the library AND in $GLOBAL_SKILLS_DIR"
                problems=$((problems + 1))
            fi
        done
    done

    load_group_skills "$LIBRARY_ROOT/specterops"
    all=("${LIB_SKILLS[@]}")

    # A library skill in the installer's fan-out source would be re-globalized by
    # the next setup.sh run. This is the leak's root cause, so doctor fails on it.
    for gdir in "$LIBRARY_ROOT"/*/; do
        load_group_skills "$gdir"
        for name in "${LIB_SKILLS[@]}"; do
            [ -e "$FANOUT_DIR/$name" ] || continue
            echo "  ✗ FANOUT: '$name' is in the library AND in $FANOUT_DIR (setup.sh would link it globally)"
            problems=$((problems + 1))
        done
    done
    for sub in "${SUBSUITES[@]}"; do
        read -r -a entries <<<"$(subsuite_skills "$sub")"
        for entry in "${entries[@]}"; do
            covered+=("$entry")
            if [ ! -d "$LIBRARY_ROOT/specterops/$entry" ]; then
                echo "  ✗ DRIFT: subsuite 'specterops:$sub' lists '$entry' which is not in the library"
                problems=$((problems + 1))
            fi
        done
    done

    # A name present in BOTH global roots makes pi warn and silently drop one of
    # them (global roots are ~/.pi/agent/skills then ~/.agents/skills). Identical
    # copies are redundant; divergent copies are a real defect.
    if [ -d "$PI_SKILLS_DIR" ]; then
        for name in $(ls -A "$PI_SKILLS_DIR" 2>/dev/null); do
            [ -e "$PI_SKILLS_DIR/$name" ] || continue
            [ -e "$GLOBAL_SKILLS_DIR/$name" ] || continue
            if diff -rq "$PI_SKILLS_DIR/$name" "$GLOBAL_SKILLS_DIR/$name" >/dev/null 2>&1; then
                echo "  · '$name' is duplicated across both global roots (identical copies)"
            else
                echo "  ✗ COLLISION: '$name' differs between $PI_SKILLS_DIR and $GLOBAL_SKILLS_DIR"
                problems=$((problems + 1))
            fi
        done
    fi

    for name in "${all[@]}"; do
        found=0
        for entry in "${covered[@]}"; do
            if [ "$entry" = "$name" ]; then
                found=1
                break
            fi
        done
        if [ "$found" -eq 0 ]; then
            echo "  · '$name' is in no subsuite — reachable only via 'specterops' (all)"
        fi
    done

    if [ "$problems" -gt 0 ]; then
        echo "✗ doctor: $problems problem(s) found."
        exit 1
    fi
    echo "✓ doctor: no leaks, no suite drift."
}
list_library() {
    local gdir name
    for gdir in "$LIBRARY_ROOT"/*/; do
        echo "$(basename "$gdir"):"
        load_group_skills "$gdir"
        for name in "${LIB_SKILLS[@]}"; do echo "  $name"; done
    done
}

usage() {
    local code="${1:-1}"
    local out=2
    [ "$code" -eq 0 ] && out=1
    cat <<EOF >&"$out"
Usage: $(basename "$0") {suite[:subsuite]|<skill-name>|list|doctor} [target-directory]

Suites (library: skills-library/):
  interfaces             : Jakub Krehel design & UI polish (better-ui, better-colors, …)
  matt                   : Matt Pocock engineering & architecture skills
  specterops             : all SpecterOps security skills
  specterops:bloodhound  : BloodHound, AzureHound, OpenHound
  specterops:appsec      : CWE, OWASP, OpenSSF, security review
  specterops:recon       : Shodan, Nmap, OSINT
  specterops:c2          : Cobalt Strike, Mythic, Outflank C2
  specterops:re          : Ghidra & Binary Ninja MCP
  specterops:payloads    : initial access & persistence
  specterops:infra       : operator infrastructure & SCCM
  list                   : list every skill in the library
  doctor                 : verify no library skill leaked into the global store
EOF
    exit "$code"
}

# ── dispatch ──────────────────────────────────────────────────────────────────

case "$SUITE" in
interfaces | ui) link_skills "Jakub Krehel Interfaces" "$(group_dir interfaces)" ;;
matt | pocock) link_skills "Matt Pocock Skills" "$(group_dir matt)" ;;
specterops:*) link_subsuite "${SUITE#specterops:}" ;;
specterops | security) link_skills "SpecterOps (all)" "$(group_dir specterops)" ;;
list) list_library ;;
doctor) doctor ;;
help | -h | --help) usage 0 ;;
*)
    find_library_skill "$SUITE" >/dev/null || usage 1
    link_named_skills "$SUITE" "$SUITE"
    ;;
esac

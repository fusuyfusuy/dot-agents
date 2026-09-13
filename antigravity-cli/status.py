#!/usr/bin/env python3
"""Antigravity IDE / CLI — Status Line Script
Format:
  fusuyfusuy │ Flash 3.8 (H) │ Idle │ ctx 94% │ qt 54% · rst 2h 26m │ ↑58k ↓21k  79k tok · Δ+3k
"""
import sys
import json
import time
import os
import re
import subprocess
from datetime import datetime, timezone

# ── ANSI Colors ────────────────────────────────────────────────────────────────
RESET        = "\033[0m"
BOLD         = "\033[1m"
GRAY         = "\033[90m"
WHITE        = "\033[37m"
GREEN        = "\033[32m"
YELLOW       = "\033[33m"
ORANGE       = "\033[38;5;208m"
LIGHT_ORANGE = "\033[38;5;214m"
RED          = "\033[31m"
MAGENTA      = "\033[35m"
CYAN         = "\033[36m"
BLUE         = "\033[34m"
PURPLE       = "\033[38;5;141m"
SEP          = f" {GRAY}│{RESET} "

STATUS_STATE_FILE = os.environ.get(
    "AGY_STATUS_STATE",
    os.path.expanduser("~/.antigravity/status-state.json"),
)
_TURN_STATE_FILE = "/tmp/agy-turn-velocity.json"


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def context_color(pct: float) -> str:
    if pct >= 50:
        return GREEN
    elif pct >= 20:
        return LIGHT_ORANGE
    return RED


def quota_color(pct: float) -> str:
    if pct >= 50:
        return PURPLE
    elif pct >= 20:
        return LIGHT_ORANGE
    return RED


def shorten_model_name(name: str) -> str:
    if not name:
        return "AGY"

    thinking_suffix = ""
    lower = name.lower()
    if "(thinking)" in lower or "(high)" in lower:
        thinking_suffix = " (H)"
    elif "(medium)" in lower:
        thinking_suffix = " (M)"
    elif "(low)" in lower:
        thinking_suffix = " (L)"

    clean = re.sub(r"\(.*?\)", "", name).strip()

    m = re.search(r"(?:gemini\s+)?(?:(\d+\.\d+)\s+)?(flash|pro|ultra)(?:\s+(\d+\.\d+))?", clean, re.IGNORECASE)
    if m:
        family = m.group(2).capitalize()
        ver = m.group(3) or m.group(1) or ""
        return f"{family} {ver}".strip() + thinking_suffix

    m = re.search(r"(?:claude\s+)?(?:(\d+\.\d+)\s+)?(opus|sonnet|haiku)(?:\s+(\d+\.\d+))?", clean, re.IGNORECASE)
    if m:
        family = m.group(2).capitalize()
        ver = m.group(3) or m.group(1) or ""
        return f"{family} {ver}".strip() + thinking_suffix

    m = re.search(r"gpt[-_ ]?(\d+o?[-_ ]?mini|\d+o|\d+)", clean, re.IGNORECASE)
    if m:
        return f"GPT-{m.group(1)}" + thinking_suffix

    m = re.search(r"(o[1-4](?:[-_ ]mini)?)", clean, re.IGNORECASE)
    if m:
        return m.group(1) + thinking_suffix

    if len(clean) > 16:
        clean = clean[:16].strip()

    return clean + thinking_suffix


def load_toku_quota() -> dict:
    """Reads live quota and refresh window from toku cache without socket scraping."""
    for path in (
        os.path.expanduser("~/.cache/toku/quotas_live_cache.json"),
        os.path.expanduser("~/.cache/toku/quotas.json"),
    ):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            # 1. Look for live windows in quotas_live_cache.json
            for w in d.get("windows", []):
                if w.get("source") in ("antigravity", "gemini"):
                    rem = w.get("remaining_pct")
                    if rem is not None:
                        return {
                            "remaining_percentage": float(rem),
                            "refreshes_in": w.get("resets_in", ""),
                        }
            # 2. Look in harnesses.antigravity of quotas.json
            harness = d.get("harnesses", {}).get("antigravity", {})
            if harness and "week_remaining_pct" in harness:
                return {
                    "remaining_percentage": float(harness.get("week_remaining_pct", 100.0)),
                    "refreshes_in": harness.get("week_resets_in", ""),
                }
        except Exception:
            continue
    return {}


def calculate_turn_velocity(total_tokens: int, session_id: str) -> str:
    """Calculates turn token delta (Δ+Xk) persisted per session."""
    if total_tokens <= 0:
        return ""
    state = {}
    if os.path.isfile(_TURN_STATE_FILE):
        try:
            with open(_TURN_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    prev_session = state.get("session_id", "")
    prev_tokens = state.get("prev_tokens", 0)
    last_delta = state.get("last_delta", 0)

    if session_id and session_id != prev_session:
        delta = 0
        state = {"session_id": session_id, "prev_tokens": total_tokens, "last_delta": 0, "timestamp": time.time()}
    else:
        if total_tokens > prev_tokens:
            delta = total_tokens - prev_tokens
            state = {"session_id": session_id, "prev_tokens": total_tokens, "last_delta": delta, "timestamp": time.time()}
        else:
            delta = last_delta

    try:
        with open(_TURN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass

    if delta > 0:
        return f"{YELLOW}Δ+{format_tokens(delta)}{RESET}"
    return f"{GRAY}Δ+0{RESET}"


def get_vcs_info(data: dict) -> tuple[str, str, bool, str]:
    """Returns (repo_name, branch, dirty, counters_str)."""
    cwd = data.get("cwd", "").strip() or os.getcwd()
    repo_name = os.path.basename(cwd.rstrip("/")) or cwd

    vcs = data.get("vcs")
    branch = ""
    dirty = False
    if isinstance(vcs, dict) and vcs.get("branch"):
        branch = str(vcs.get("branch", "")).strip()
        dirty = bool(vcs.get("dirty", False))

    if not branch:
        try:
            branch = subprocess.check_output(
                ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                text=True, stderr=subprocess.DEVNULL, timeout=0.25
            ).strip()
        except Exception:
            return repo_name, "", False, ""

    if not branch:
        return repo_name, "", False, ""

    if branch == "HEAD":
        try:
            short_sha = subprocess.check_output(
                ["git", "-C", cwd, "rev-parse", "--short", "HEAD"],
                text=True, stderr=subprocess.DEVNULL, timeout=0.25
            ).strip()
            if short_sha:
                branch = f"@{short_sha}"
        except Exception:
            pass

    tokens = []

    # 1. Ahead / behind upstream
    try:
        rev_count = subprocess.check_output(
            ["git", "-C", cwd, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            text=True, stderr=subprocess.DEVNULL, timeout=0.25
        ).strip()
        if rev_count:
            parts = rev_count.split()
            if len(parts) >= 2:
                ahead, behind = int(parts[0]), int(parts[1])
                if ahead > 0 and behind > 0:
                    tokens.append(f"{CYAN}↑{ahead}↓{behind}{RESET}")
                elif ahead > 0:
                    tokens.append(f"{CYAN}↑{ahead}{RESET}")
                elif behind > 0:
                    tokens.append(f"{CYAN}↓{behind}{RESET}")
    except Exception:
        pass

    # 2. Stashes
    try:
        stash_out = subprocess.check_output(
            ["git", "-C", cwd, "rev-list", "--walk-reflogs", "--count", "refs/stash"],
            text=True, stderr=subprocess.DEVNULL, timeout=0.25
        ).strip()
        if stash_out and int(stash_out) > 0:
            tokens.append(f"{CYAN}*{stash_out}{RESET}")
    except Exception:
        pass

    # 3. Working tree status
    try:
        status_out = subprocess.check_output(
            ["git", "-C", cwd, "status", "--no-renames", "--porcelain"],
            text=True, stderr=subprocess.DEVNULL, timeout=0.25
        )
        if status_out:
            staged = 0
            unstaged = 0
            untracked = 0
            conflicted = 0
            for line in status_out.splitlines():
                if len(line) < 3:
                    continue
                x, y = line[0], line[1]
                if x == "?" and y == "?":
                    untracked += 1
                elif x == "U" or y == "U" or line[:2] in ("AA", "DD"):
                    conflicted += 1
                else:
                    if x in "MADRC":
                        staged += 1
                    if y in "MADRC":
                        unstaged += 1

            if conflicted > 0:
                tokens.append(f"{RED}~{conflicted}{RESET}")
            if staged > 0:
                tokens.append(f"{GREEN}+{staged}{RESET}")
            if unstaged > 0:
                tokens.append(f"{YELLOW}!{unstaged}{RESET}")
            if untracked > 0:
                tokens.append(f"{BLUE}?{untracked}{RESET}")

            dirty = dirty or bool(staged or unstaged or untracked or conflicted)
    except Exception:
        pass

    counters = " ".join(tokens)
    return repo_name, branch, dirty, counters


def is_subagent_actively_running(sub: dict) -> bool:
    if not isinstance(sub, dict):
        return False

    state = (sub.get("state") or sub.get("lifecycleState") or sub.get("status") or "").lower()
    if state in ("idle", "done", "completed", "error", "errored", "canceled", "cancelled", "dead", "stopped", "killed", "finished", "success"):
        return False

    if sub.get("active") is False or sub.get("is_active") is False:
        return False

    return state in ("running", "waiting_for_input", "waiting_for_dependents", "working", "busy")


def get_subagents_info(data: dict) -> str:
    subagents = data.get("subagents")
    if isinstance(subagents, list) and subagents:
        active_items = []
        for sub in subagents:
            if not isinstance(sub, dict):
                continue
            if not is_subagent_actively_running(sub):
                continue

            r = sub.get("role") or sub.get("type") or sub.get("typeName") or sub.get("name") or ""
            state = (sub.get("state") or "").lower()
            state_detail = sub.get("stateDetail") or sub.get("toolAction") or ""
            if r:
                lower = r.lower()
                if "research" in lower:
                    short = "Research"
                elif "review" in lower:
                    short = "Review"
                elif "debug" in lower:
                    short = "Debug"
                elif "plan" in lower:
                    short = "Plan"
                elif "audit" in lower:
                    short = "Audit"
                elif "frontend" in lower or "ui" in lower:
                    short = "UI"
                else:
                    cleaned = re.sub(r"(?i)\b(subagent|agent|sub)\b", "", r).strip()
                    short = cleaned.split()[0] if cleaned else "sub"
                if len(short) > 10:
                    short = short[:8].strip()

                action_preview = ""
                if state in ("running", "waiting_for_input", "working") and state_detail:
                    m = re.search(r"(\w+)", state_detail)
                    if m:
                        act = m.group(1).lower()
                        if act in ("view_file", "view"):
                            action_preview = ":view"
                        elif act in ("run_command", "run", "bash"):
                            action_preview = ":run"
                        elif act in ("replace_file_content", "write_to_file", "edit"):
                            action_preview = ":edit"
                        elif act in ("grep_search", "grep"):
                            action_preview = ":grep"
                        elif act in ("find_by_name", "find"):
                            action_preview = ":find"
                        else:
                            action_preview = f":{act[:5]}"
                active_items.append(f"{short}{action_preview}")

        count = len(active_items)
        if count > 0:
            items_str = ", ".join(active_items[:6])
            if len(active_items) > 6:
                items_str += f"+{len(active_items)-6}"
            return f"{MAGENTA}sub: {count} [{items_str}]{RESET}"

    return ""


def get_bg_tasks_info(data: dict) -> str:
    tasks = data.get("tasks") or data.get("bg_tasks") or data.get("background_tasks")
    if isinstance(tasks, list) and tasks:
        active_names = []
        for t in tasks:
            if isinstance(t, dict):
                state = (t.get("state") or t.get("status") or "").lower()
                if state in ("idle", "done", "completed", "error", "errored", "canceled", "cancelled", "dead", "stopped", "killed", "finished", "success"):
                    continue
                cmd = t.get("command") or t.get("cmd") or t.get("name") or t.get("id") or ""
                first = cmd.strip().split()[0] if cmd else "task"
                first = os.path.basename(first)
                active_names.append(first[:12])
            elif isinstance(t, str) and t:
                active_names.append(os.path.basename(t.strip().split()[0])[:12])
        count = len(active_names)
        if count > 0:
            names_str = ", ".join(active_names[:3])
            if len(active_names) > 3:
                names_str += f"+{len(active_names)-3}"
            return f"{CYAN}bg: {count} [{names_str}]{RESET}"
    return ""


def write_status_state(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATUS_STATE_FILE), exist_ok=True)
        state = {
            "email": data.get("email") or "",
            "plan_tier": data.get("plan_tier") or "",
            "session_id": data.get("conversation_id") or data.get("session_id") or "",
            "timestamp": time.time(),
        }
        model_info = data.get("model", {})
        if isinstance(model_info, dict):
            state["model"] = model_info.get("display_name") or model_info.get("id") or ""
        else:
            state["model"] = str(model_info or "")
        with open(STATUS_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    except Exception:
        pass


def render(data: dict) -> str:
    # 1. Directory & Git
    repo_name, branch, dirty, counters = get_vcs_info(data)
    if branch:
        cnt_str = f" {counters}" if counters else ""
        dirty_str = f"{YELLOW}*{RESET}" if dirty and not counters else ""
        branch_color = RED if dirty else BLUE
        git_display = f"{GREEN}{repo_name}{GRAY}:{branch_color}{branch}{dirty_str}{cnt_str}{RESET}"
    else:
        git_display = f"{GREEN}{repo_name}{RESET}"

    sandbox_enabled = data.get("sandbox", {}).get("enabled", False)
    sandbox_str = f" {ORANGE}[sandbox]{RESET}" if sandbox_enabled else ""

    # 2. Model
    model_info = data.get("model", {})
    if isinstance(model_info, dict):
        raw_name = model_info.get("display_name") or model_info.get("id") or "AGY"
    else:
        raw_name = str(model_info) or "AGY"
    short_name = shorten_model_name(raw_name)
    model_display = f"{YELLOW}{BOLD}{short_name}{RESET}"

    # 3. Agent State
    state = str(data.get("agent_state", "idle")).lower()
    state_colors = {
        "working": CYAN,
        "idle":    GRAY,
        "waiting": LIGHT_ORANGE,
        "error":   RED,
        "tool_use": PURPLE,
    }
    sc = state_colors.get(state, WHITE)
    state_display = f"{sc}{state.capitalize()}{RESET}"

    # 4. Context Window
    cw = data.get("context_window", {})
    rem_pct = cw.get("remaining_percentage")
    if rem_pct is None:
        used_pct = float(cw.get("used_percentage", 0.0))
        rem_pct = max(0.0, min(100.0, 100.0 - used_pct))
    else:
        rem_pct = float(rem_pct)
    cc = context_color(rem_pct)
    ctx_display = f"{cc}ctx {rem_pct:.0f}%{RESET}"

    # 5. Quota & Reset
    quota = load_toku_quota()
    if "remaining_percentage" in quota:
        quota_pct = float(quota["remaining_percentage"])
        qc = quota_color(quota_pct)
        reset_in = quota.get("refreshes_in", "")
        rst_str = f" {GRAY}·{RESET} rst {reset_in}" if reset_in else ""
        quota_display = f"{qc}qt {quota_pct:.0f}%{RESET}{rst_str}"
    else:
        quota_display = ""

    # 6. Session Tokens & Turn Velocity
    in_t    = int(cw.get("total_input_tokens",  0))
    out_t   = int(cw.get("total_output_tokens", 0))
    total_t = in_t + out_t

    session_id = data.get("conversation_id") or data.get("session_id") or ""
    velocity_display = calculate_turn_velocity(total_t, session_id)

    tok_elements = [f"{GRAY}↑{format_tokens(in_t)} ↓{format_tokens(out_t)}  {WHITE}{format_tokens(total_t)} tok{RESET}"]
    if velocity_display:
        tok_elements.append(velocity_display)
    tokens_display = f" {GRAY}·{RESET} ".join(tok_elements)

    parts_line1 = [f"{git_display}{sandbox_str}", model_display, state_display, ctx_display]
    if quota_display:
        parts_line1.append(quota_display)
    parts_line1.append(tokens_display)
    line1 = SEP.join(parts_line1)

    # ── LINE 2: Subagents, Background Tasks, Artifacts ─────────────────────────
    sub_display = get_subagents_info(data)
    bg_display = get_bg_tasks_info(data)
    artifact_count = int(data.get("artifact_count", 0) or 0)

    parts_line2 = []
    if sub_display:
        parts_line2.append(sub_display)
    if bg_display:
        parts_line2.append(bg_display)
    if artifact_count > 0:
        parts_line2.append(f"{GRAY}artifacts: {WHITE}{artifact_count}{RESET}")

    if parts_line2:
        line2 = SEP.join(parts_line2)
        return f"{GRAY}╭─{RESET} {line1}\n{GRAY}╰─{RESET} {line2}"

    return line1


def handle_tmux_alert(state: str) -> None:
    tmux_pane = os.environ.get("TMUX_PANE")
    if not tmux_pane or not state:
        return
    try:
        sanitized = tmux_pane.replace("%", "pane_")
        state_file = f"/tmp/agy-state-{sanitized}"
        prev_state = ""
        if os.path.isfile(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                prev_state = f.read().strip()
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(state)
        if state == "idle" and prev_state and prev_state != "idle":
            alert_handler = os.path.expanduser("~/.tmux/plugins/tmux-agent-alert/scripts/alert_handler.sh")
            if os.path.isfile(alert_handler) and os.access(alert_handler, os.X_OK):
                subprocess.Popen(
                    [alert_handler, tmux_pane, "input_needed", "", "Antigravity"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            print(render({}), flush=True)
            return
        data = json.loads(raw)
        if isinstance(data, dict):
            write_status_state(data)
            handle_tmux_alert(str(data.get("agent_state", "idle")).lower())
            print(render(data), flush=True)
        else:
            print(render({}), flush=True)
    except Exception:
        print(render({}), flush=True)


if __name__ == "__main__":
    main()

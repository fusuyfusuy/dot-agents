#!/usr/bin/env python3
"""Antigravity IDE — Status Line Script
Row 1: path · model · state · ctx · quota · session tokens
Row 2: 💡 Tip  (rotates at most once every 3 seconds, persisted across restarts)
"""
import sys
import json
import time
import os
import random
import select
import re
import subprocess
import http.client
import ssl
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

QUOTA_CACHE_FILE = os.environ.get(
    "AGY_QUOTA_CACHE",
    os.path.expanduser("~/.antigravity/quota-cache.json"),
)
STATUS_STATE_FILE = os.environ.get(
    "AGY_STATUS_STATE",
    os.path.expanduser("~/.antigravity/status-state.json"),
)
QUOTA_MAX_AGE_SECONDS = float(os.environ.get("AGY_QUOTA_MAX_AGE_SECONDS", "900"))
QUOTA_REFRESH_INTERVAL_SECONDS = float(os.environ.get("AGY_QUOTA_REFRESH_INTERVAL_SECONDS", "30"))
USER_STATUS_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"

# ── Helpers ────────────────────────────────────────────────────────────────────
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
        return ORANGE
    else:
        return RED


def normalize_model_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def shorten_model_name(name: str) -> str:
    if not name:
        return "AGY"

    thinking_suffix = ""
    lower = name.lower()
    if "(thinking)" in lower:
        thinking_suffix = " (T)"
    elif "(high)" in lower:
        thinking_suffix = " (H)"
    elif "(medium)" in lower:
        thinking_suffix = " (M)"
    elif "(low)" in lower:
        thinking_suffix = " (L)"

    clean = re.sub(r"\(.*?\)", "", name).strip()

    m = re.search(r"(?:claude\s+)?(?:(\d+\.\d+)\s+)?(opus|sonnet|haiku)(?:\s+(\d+\.\d+))?", clean, re.IGNORECASE)
    if m:
        family = m.group(2).capitalize()
        ver = m.group(3) or m.group(1) or ""
        return f"{family} {ver}".strip() + thinking_suffix

    m = re.search(r"(?:gemini\s+)?(?:(\d+\.\d+)\s+)?(flash|pro|ultra)(?:\s+(\d+\.\d+))?", clean, re.IGNORECASE)
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


def reset_epoch(entry: dict):
    reset_time = entry.get("reset_time")
    if not reset_time:
        return None
    try:
        return datetime.fromisoformat(reset_time.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def find_model_by_name(models: dict, model_name: str) -> dict:
    if not model_name or not models:
        return {}
    wanted = normalize_model_name(model_name)
    if wanted in models:
        return models[wanted]
    for key, value in models.items():
        key_norm = normalize_model_name(key)
        if key_norm and (key_norm in wanted or wanted in key_norm):
            return value
    return {}


def select_gating_quota(models: dict, active_model_name: str = "") -> dict:
    """Return the active gating quota constraint:
    1. If an active model is specified and present in models, return its quota.
    2. Otherwise, return the most constrained model: lowest remaining percentage
       first, with ties broken by soonest reset time.
    """
    if active_model_name:
        matched = find_model_by_name(models, active_model_name)
        if matched:
            return matched

    candidates = list(models.values())
    if not candidates:
        return {}

    return min(
        candidates,
        key=lambda m: (
            float(m.get("remaining_percentage", 100.0)),
            reset_epoch(m) if reset_epoch(m) is not None else float("inf"),
        ),
    )


def quota_color(pct: float) -> str:
    if pct >= 50:
        return PURPLE
    if pct >= 20:
        return LIGHT_ORANGE
    return RED


def format_reset_time(reset_time: str) -> str:
    try:
        reset = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
        diff = int((reset - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return ""

    if diff <= 0:
        return "now"
    minutes = (diff + 59) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours >= 24:
        days, rem_hours = divmod(hours, 24)
        return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def extract_arg(command_line: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}(?:=|\s+)([^\s\"']+|\"[^\"]+\"|'[^']+')", command_line)
    if not match:
        return ""
    return match.group(1).strip("\"'")


def find_server_candidates() -> list[dict]:
    try:
        ps = subprocess.check_output(["ps", "auxww"], text=True, stderr=subprocess.DEVNULL, timeout=1.5)
    except Exception:
        return []

    candidates = []
    for line in ps.splitlines():
        lower = line.lower()
        is_cli = re.search(r"\bagy(\s|$)", line) is not None
        is_language_server = "language_server" in lower
        if not is_cli and not is_language_server:
            continue
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        token = extract_arg(parts[10], "--csrf_token")
        score = 10
        if is_cli:
            score += 40
        if is_language_server:
            score += 20
        if token:
            score += 10
        if "/applications/antigravity.app" in lower:
            score -= 10
        candidates.append({
            "pid": pid,
            "csrf_token": token,
            "score": score,
            "kind": "cli" if is_cli else "language_server",
        })

    return sorted(candidates, key=lambda x: x["score"], reverse=True)


def get_listening_ports(pid: int) -> list[int]:
    # 1. Try Linux /proc parsing first (fastest, no lsof/stat/subprocess overhead)
    try:
        socket_inodes = set()
        fd_dir = f"/proc/{pid}/fd"
        if os.path.exists(fd_dir):
            for fd in os.listdir(fd_dir):
                try:
                    link = os.readlink(os.path.join(fd_dir, fd))
                    if link.startswith("socket:[") and link.endswith("]"):
                        socket_inodes.add(link[8:-1])
                except Exception:
                    pass

        if socket_inodes:
            ports = set()
            for net_file in (f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6", "/proc/net/tcp", "/proc/net/tcp6"):
                if not os.path.exists(net_file):
                    continue
                try:
                    with open(net_file, "r") as f:
                        lines = f.readlines()[1:]
                    for line in lines:
                        parts = line.strip().split()
                        if len(parts) >= 10:
                            state = parts[3]
                            inode = parts[9]
                            if state == "0A" and inode in socket_inodes:
                                local_addr = parts[1]
                                port_hex = local_addr.rsplit(":", 1)[-1]
                                ports.add(int(port_hex, 16))
                except Exception:
                    pass
            if ports:
                return sorted(list(ports))
    except Exception:
        pass

    # 2. Try ss command (fast on Linux, avoids lsof stat issues)
    try:
        out = subprocess.check_output(
            ["ss", "-Htlpn"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
        )
        ports = set()
        for line in out.splitlines():
            if re.search(rf"\bpid={pid}\b", line):
                parts = line.split()
                if len(parts) >= 4:
                    local_addr = parts[3]
                    port_str = local_addr.rsplit(":", 1)[-1]
                    if port_str.isdigit():
                        ports.add(int(port_str))
        if ports:
            return sorted(list(ports))
    except Exception:
        pass

    # 3. Fallback to lsof with -X flag and stderr suppressed
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-X", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
        )
        ports = []
        for match in re.finditer(r":(\d+)\s+\(LISTEN\)", out):
            port = int(match.group(1))
            if port not in ports:
                ports.append(port)
        return sorted(ports)
    except Exception:
        return []


def request_user_status(port: int, csrf_token: str, use_https: bool) -> dict:
    body = json.dumps({
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "locale": "en",
        }
    })
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }
    if csrf_token:
        headers["X-Codeium-Csrf-Token"] = csrf_token
    if use_https:
        conn = http.client.HTTPSConnection(
            "127.0.0.1",
            port,
            timeout=2,
            context=ssl._create_unverified_context(),
        )
    else:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("POST", USER_STATUS_PATH, body, headers)
    res = conn.getresponse()
    raw = res.read().decode("utf-8", "replace")
    if res.status < 200 or res.status >= 300:
        raise RuntimeError(f"HTTP {res.status}")
    return json.loads(raw)


def parse_user_status_quota(response: dict) -> dict:
    user_status = response.get("userStatus", {})
    plan_status = user_status.get("planStatus", {})
    plan_info = plan_status.get("planInfo", {})
    cascade = user_status.get("cascadeModelConfigData", {})
    models = {}

    for model in cascade.get("clientModelConfigs", []) or []:
        quota_info = model.get("quotaInfo") or {}
        if "remainingFraction" not in quota_info:
            continue
        label = model.get("label") or model.get("modelOrAlias", {}).get("model") or "Unknown"
        remaining = max(0.0, min(100.0, float(quota_info.get("remainingFraction", 0)) * 100))
        entry = {
            "name": label,
            "remaining_percentage": remaining,
            "source": "local_language_server",
        }
        reset_time = quota_info.get("resetTime")
        if reset_time:
            entry["reset_time"] = reset_time
            entry["refreshes_in"] = format_reset_time(reset_time)
        models[normalize_model_name(label)] = entry

    return {
        "timestamp": time.time(),
        "source": "local_language_server",
        "scope": {
            "email": user_status.get("email") or "",
            "plan_tier": plan_info.get("planName") or "",
        },
        "models": models,
    }


def fetch_live_quota_cache(expected_email: str = "") -> dict:
    fallback = {}
    expected = (expected_email or "").lower()

    for process_info in find_server_candidates():
        ports = get_listening_ports(process_info["pid"])
        for port in ports:
            for use_https in (True, False):
                try:
                    response = request_user_status(
                        port,
                        process_info.get("csrf_token", ""),
                        use_https,
                    )
                    cache = parse_user_status_quota(response)
                    if not cache.get("models"):
                        continue
                    cache["source_process"] = process_info.get("kind", "")
                    cache["source_port"] = port
                    email = str(cache.get("scope", {}).get("email", "")).lower()
                    if expected and email == expected:
                        return cache
                    if not fallback:
                        fallback = cache
                except Exception:
                    continue
    return fallback


def read_json_file(path: str) -> dict:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_quota_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(QUOTA_CACHE_FILE), exist_ok=True)
        with open(QUOTA_CACHE_FILE, "w") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    except Exception:
        pass


def quota_scope(data: dict) -> dict:
    return {
        "email": data.get("email") or "",
        "plan_tier": data.get("plan_tier") or "",
    }


def write_status_state(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATUS_STATE_FILE), exist_ok=True)
        state = quota_scope(data)
        state["session_id"] = data.get("conversation_id") or data.get("session_id") or ""
        model_info = data.get("model", {})
        if isinstance(model_info, dict):
            state["model"] = model_info.get("display_name") or model_info.get("id") or ""
        else:
            state["model"] = str(model_info or "")
        state["timestamp"] = time.time()
        with open(STATUS_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    except Exception:
        pass


def scope_mismatch(cache: dict, data: dict) -> str:
    expected = quota_scope(data)
    actual = cache.get("scope", {})
    if not isinstance(actual, dict):
        return "scope"

    # Only email is a hard account mismatch (switching accounts)
    exp_email = (expected.get("email") or "").strip().lower()
    act_email = (actual.get("email") or "").strip().lower()
    if exp_email and act_email and exp_email != act_email:
        return "account"

    # Plan tier mismatch triggers background refresh via should_refresh_quota
    exp_plan = (expected.get("plan_tier") or "").strip().lower()
    act_plan = (actual.get("plan_tier") or "").strip().lower()
    if exp_plan and act_plan:
        # Normalize: ignore common prefix/suffix like "google", "ai", "tier"
        def norm(p):
            return p.replace("google", "").replace("ai", "").replace("tier", "").strip()
        if norm(exp_plan) != norm(act_plan) and not exp_plan.endswith(act_plan) and not act_plan.endswith(exp_plan):
            return "plan"

    return ""


def should_refresh_quota(data: dict, cache: dict) -> bool:
    if not cache:
        return True
    now = time.time()
    ts = float(cache.get("timestamp", 0) or 0)
    if not ts or now - ts >= QUOTA_REFRESH_INTERVAL_SECONDS:
        return True
    state = read_json_file(STATUS_STATE_FILE)
    current_session = data.get("conversation_id") or data.get("session_id") or ""
    if current_session and state.get("session_id") and state.get("session_id") != current_session:
        return True
    if scope_mismatch(cache, data):
        return True
    return False


def refresh_quota_if_needed(data: dict) -> dict:
    cache = read_json_file(QUOTA_CACHE_FILE)
    if should_refresh_quota(data, cache):
        lock_file = "/tmp/agy_quota_refresh.lock"
        now = time.time()
        should_spawn = True
        try:
            if os.path.exists(lock_file):
                mtime = os.path.getmtime(lock_file)
                if now - mtime < 15.0:
                    should_spawn = False
            if should_spawn:
                with open(lock_file, "w") as f:
                    f.write(str(now))
        except Exception:
            pass

        if should_spawn:
            email = data.get("email") or ""
            try:
                subprocess.Popen(
                    [sys.executable, os.path.abspath(__file__), "--fetch-quota", email],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass
    return cache


def load_quota_for_model(data: dict) -> dict:
    """Read the latest quota cache and return the active gating window."""
    cache = refresh_quota_if_needed(data)
    if not cache:
        return {}

    # Only hard account mismatch (different logged-in account) blocks quota display
    mismatch = scope_mismatch(cache, data)
    if mismatch == "account":
        return {"stale": True, "reason": "account"}

    ts = float(cache.get("timestamp", 0) or 0)
    if ts and time.time() - ts > QUOTA_MAX_AGE_SECONDS:
        return {"stale": True, "reason": "age"}

    models = cache.get("models", {})
    if not isinstance(models, dict) or not models:
        return {}

    model_info = data.get("model", {})
    if isinstance(model_info, dict):
        active_model = model_info.get("display_name") or model_info.get("id") or ""
    else:
        active_model = str(model_info or "")

    return select_gating_quota(models, active_model)


def get_vcs_info(data: dict) -> tuple[str, str, bool]:
    cwd = data.get("cwd", "").strip() or os.getcwd()
    repo_name = os.path.basename(cwd.rstrip("/")) or cwd

    vcs = data.get("vcs")
    if isinstance(vcs, dict) and vcs.get("branch"):
        branch = str(vcs.get("branch", "")).strip()
        dirty = bool(vcs.get("dirty", False))
        return repo_name, branch, dirty

    # Fallback to fast git check if not provided in JSON payload
    try:
        branch = subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=0.3
        ).strip()
        status_out = subprocess.check_output(
            ["git", "-C", cwd, "status", "--porcelain"],
            text=True, stderr=subprocess.DEVNULL, timeout=0.3
        ).strip()
        dirty = bool(status_out)
        return repo_name, branch, dirty
    except Exception:
        return repo_name, "", False


# ── Rendering ──────────────────────────────────────────────────────────────────
SEP = f" {GRAY}│{RESET} "


def render(data: dict) -> str:
    # 1. Git repository & branch
    repo_name, branch, dirty = get_vcs_info(data)
    if branch:
        dirty_str = f"{YELLOW}*{RESET}" if dirty else ""
        branch_color = RED if dirty else BLUE
        git_display = f"{GREEN}{repo_name}{GRAY}:{branch_color}{branch}{dirty_str}{RESET}"
    else:
        git_display = f"{GREEN}{repo_name}{RESET}"

    # 2. Model
    model_info = data.get("model", {})
    if isinstance(model_info, dict):
        raw_name = model_info.get("display_name") or model_info.get("id") or "AGY"
    else:
        raw_name = str(model_info) or "AGY"
    short_name = shorten_model_name(raw_name)
    model_display = f"{YELLOW}{BOLD}{short_name}{RESET}"

    # 3. Agent state
    state = data.get("agent_state", "idle")
    state_colors = {
        "working": CYAN,
        "idle":    GRAY,
        "waiting": LIGHT_ORANGE,
        "error":   RED,
    }
    sc = state_colors.get(state, WHITE)
    state_display = f"{sc}{state.capitalize()}{RESET}"

    # 4. Context window
    cw      = data.get("context_window", {})
    rem_pct = float(cw.get("remaining_percentage", 100.0))
    cc      = context_color(rem_pct)
    ctx_display = f"{cc}ctx {rem_pct:.0f}%{RESET}"

    # 5. Session token totals
    in_t    = int(cw.get("total_input_tokens",  0))
    out_t   = int(cw.get("total_output_tokens", 0))
    total_t = in_t + out_t
    tokens_display = (
        f"{GRAY}↑{format_tokens(in_t)} ↓{format_tokens(out_t)}"
        f"  {WHITE}{format_tokens(total_t)} tok{RESET}"
    )

    # 6. Quota from the cached /usage output — active gating constraint
    quota = load_quota_for_model(data)
    if quota.get("stale"):
        reason = quota.get("reason", "stale")
        quota_display = f"{GRAY}qt: sync /usage ({reason}){RESET}"
    elif "remaining_percentage" in quota:
        quota_pct = float(quota.get("remaining_percentage", 0.0))
        qc = quota_color(quota_pct)
        reset_in = quota.get("refreshes_in") or format_reset_time(quota.get("reset_time", ""))
        reset_display = f"{GRAY} · rst {reset_in}{RESET}" if reset_in else ""
        quota_display = f"{qc}qt {quota_pct:.0f}%{RESET}{reset_display}"
    else:
        quota_display = f"{GRAY}qt: sync /usage{RESET}"

    # 7. Sandbox badge
    sandbox_enabled = data.get("sandbox", {}).get("enabled", False)
    sandbox_str = f" {ORANGE}[sandbox]{RESET}" if sandbox_enabled else ""

    # ── Assemble clean single-line status row ──────────────────────────────────
    return (
        f"{git_display}{sandbox_str}{SEP}"
        f"{model_display}{SEP}"
        f"{state_display}{SEP}"
        f"{ctx_display}{SEP}"
        f"{quota_display}{SEP}"
        f"{tokens_display}"
    )


# ── Main loop ──────────────────────────────────────────────────────────────────
def main():
    decoder = json.JSONDecoder()
    buffer  = ""
    last_data = None

    while True:
        try:
            r, _, _ = select.select([sys.stdin], [], [], 1.0)
            if r:
                chunk = sys.stdin.read(1)
                if not chunk:
                    stripped = buffer.lstrip()
                    if stripped and not last_data:
                        try:
                            data, _ = decoder.raw_decode(stripped)
                            if isinstance(data, dict):
                                write_status_state(data)
                                print(render(data), flush=True)
                        except Exception:
                            pass
                    break
                buffer += chunk

                if chunk == "}":
                    while buffer:
                        stripped = buffer.lstrip()
                        if not stripped:
                            buffer = ""
                            break
                        try:
                            data, idx = decoder.raw_decode(stripped)
                            buffer = stripped[idx:]
                            if isinstance(data, dict):
                                write_status_state(data)
                                last_data = data
                                print(render(last_data), flush=True)
                        except json.JSONDecodeError:
                            break

        except EOFError:
            break
        except Exception:
            time.sleep(0.05)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fetch-quota":
        expected_email = sys.argv[2] if len(sys.argv) > 2 else ""
        cache = fetch_live_quota_cache(expected_email)
        if cache:
            write_quota_cache(cache)
        sys.exit(0)
    main()

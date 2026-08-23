#!/usr/bin/env python3
"""Aggregates Antigravity (agy), Claude Code, and OpenCode Go subscription
quotas, and generates pre-rendered tmux status segments.
"""
import os
import sys
import json
import time
import re
import subprocess
import http.client
import ssl
from datetime import datetime, timezone

CACHE_DIR = os.path.expanduser("~/.cache/agent-quotas")
AGY_CACHE_FILE = os.environ.get(
    "AGY_QUOTA_CACHE",
    os.path.expanduser("~/.antigravity/quota-cache.json"),
)
STATUS_STATE_FILE = os.environ.get(
    "AGY_STATUS_STATE",
    os.path.expanduser("~/.antigravity/status-state.json"),
)
CLAUDE_CACHE_FILE = os.path.join(CACHE_DIR, "claude.json")
OUTPUT_STATUS_JSON = os.path.join(CACHE_DIR, "status.json")
OUTPUT_AGY_TXT = os.path.join(CACHE_DIR, "agy.txt")
OUTPUT_CLAUDE_TXT = os.path.join(CACHE_DIR, "claude.txt")
OUTPUT_PI_TXT = os.path.join(CACHE_DIR, "pi.txt")
OUTPUT_OCGO_TXT = os.path.join(CACHE_DIR, "opencode_go.txt")
OUTPUT_COMBINED_TXT = os.path.join(CACHE_DIR, "combined.txt")

def _resolve_ocgo_key() -> str:
    """Resolve OpenCode Go API key: env var first, then pi auth.json fallback."""
    key = os.environ.get("OPENCODE_GO_API_KEY", "")
    if key:
        return key
    auth_path = os.path.expanduser("~/.pi/agent/auth.json")
    try:
        with open(auth_path, "r") as f:
            data = json.load(f)
        return data.get("opencode-go", {}).get("key", "")
    except Exception:
        return ""


OCGO_API_KEY = _resolve_ocgo_key()
OCGO_USAGE_URL = os.environ.get(
    "OPENCODE_GO_USAGE_URL",
    "https://opencode.ai/zen/go/v1/usage",
)

PI_SESSIONS_DIR = os.environ.get(
    "PI_SESSIONS_DIR",
    os.path.expanduser("~/.pi/agent/sessions"),
)

USER_STATUS_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"


def format_reset_time(reset_time_str: str) -> str:
    if not reset_time_str:
        return ""
    try:
        reset = datetime.fromisoformat(reset_time_str.replace("Z", "+00:00"))
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
        return f"{days}d{rem_hours}h" if rem_hours else f"{days}d"
    return f"{hours}h{mins}m" if mins else f"{hours}h"


def format_epoch_reset(epoch: float) -> str:
    if not epoch:
        return ""
    try:
        diff = int(epoch - time.time())
        if diff <= 0:
            return "now"
        minutes = (diff + 59) // 60
        if minutes < 60:
            return f"{minutes}m"
        hours, mins = divmod(minutes, 60)
        return f"{hours}h{mins}m" if mins else f"{hours}h"
    except Exception:
        return ""


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
        candidates.append({
            "pid": pid,
            "csrf_token": token,
            "score": score,
            "kind": "cli" if is_cli else "language_server",
        })

    return sorted(candidates, key=lambda x: x["score"], reverse=True)


def get_listening_ports(pid: int) -> list[int]:
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

    try:
        out = subprocess.check_output(["ss", "-Htlpn"], text=True, stderr=subprocess.DEVNULL, timeout=1.5)
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


def fetch_live_agy_quota() -> dict:
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
                    user_status = response.get("userStatus", {})
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
                        }
                        reset_time = quota_info.get("resetTime")
                        if reset_time:
                            entry["reset_time"] = reset_time
                            entry["refreshes_in"] = format_reset_time(reset_time)
                        models[label.lower()] = entry
                    if models:
                        return {
                            "timestamp": time.time(),
                            "source": "live",
                            "models": models,
                            "user": user_status.get("email", ""),
                        }
                except Exception:
                    continue
    return {}


def load_cached_agy_quota() -> dict:
    if not os.path.exists(AGY_CACHE_FILE):
        return {}
    try:
        with open(AGY_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception:
        return {}


def load_claude_quota() -> dict:
    if not os.path.exists(CLAUDE_CACHE_FILE):
        return {}
    try:
        with open(CLAUDE_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def reset_epoch(entry: dict) -> float | None:
    reset_time = entry.get("reset_time")
    if not reset_time:
        return None
    try:
        return datetime.fromisoformat(reset_time.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def read_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def normalize_model_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def find_model_by_name(models: dict, model_name: str) -> dict:
    if not model_name or not models:
        return {}
    wanted = normalize_model_name(model_name)
    for key, value in models.items():
        key_norm = normalize_model_name(key)
        if key_norm and (key_norm in wanted or wanted in key_norm):
            return value
        label_norm = normalize_model_name(value.get("name", ""))
        if label_norm and (label_norm in wanted or wanted in label_norm):
            return value
    return {}


def select_gating_quota(models: dict, active_model_name: str = "") -> dict:
    """Return the active gating quota constraint:
    1. If an active model is specified and matches, return its quota.
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


def select_claude_gating_quota(claude_data: dict) -> dict | None:
    """Always surface the 5h rolling window; fall back to 7d only if 5h is absent."""
    if not claude_data:
        return None

    five_h_used = claude_data.get("five_hour_used_pct")
    seven_d_used = claude_data.get("seven_day_used_pct")
    five_h_reset = claude_data.get("five_hour_resets_at")
    seven_d_reset = claude_data.get("seven_day_resets_at")

    if five_h_used is not None:
        gating = {
            "window": "5h",
            "remaining_pct": 100.0 - float(five_h_used),
            "resets_in": format_epoch_reset(five_h_reset) if five_h_reset else "",
            "reset_epoch": float(five_h_reset) if five_h_reset else float("inf"),
        }
    elif seven_d_used is not None:
        gating = {
            "window": "7d",
            "remaining_pct": 100.0 - float(seven_d_used),
            "resets_in": format_epoch_reset(seven_d_reset) if seven_d_reset else "",
            "reset_epoch": float(seven_d_reset) if seven_d_reset else float("inf"),
        }
    else:
        return None

    gating["model"] = claude_data.get("model", "Claude")
    return gating


def entry_cost_total(entry: dict) -> float:
    """Cost persisted by pi for one session entry (message / compaction / summary).

    Mirrors pi's own getUsageCostBreakdown: assistant messages carry
    message.usage.cost.total, toolResult messages may too, and compaction /
    branch_summary entries carry usage.cost.total directly.
    """
    etype = entry.get("type")
    if etype == "message":
        usage = entry.get("message", {}).get("usage") or {}
    elif etype in ("compaction", "branch_summary"):
        usage = entry.get("usage") or {}
    else:
        return 0.0
    cost = usage.get("cost") or {}
    try:
        return float(cost.get("total", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def compute_pi_today_spend(sessions_root: str | None = None) -> float:
    """Total pi spend for the current local calendar day, summed across all
    session files that were modified today under `sessions_root`.

    Returns 0.0 (never an error) when the sessions dir is missing or empty.
    """
    root = sessions_root or PI_SESSIONS_DIR
    if not os.path.isdir(root):
        return 0.0

    now = datetime.now()
    start_of_day = datetime(now.year, now.month, now.day).timestamp()

    total = 0.0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, name)
            # Files split per session; a session touched today lives in the
            # file touched today. Old files are skipped without parsing.
            if os.path.getmtime(path) < start_of_day:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            total += entry_cost_total(json.loads(line))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
            except OSError:
                continue
    return total


def format_pi_spend(cost: float) -> str:
    return f"${cost:.2f}"


def fetch_opencode_go_usage() -> dict:
    """Fetch OpenCode Go usage from the official endpoint.

    Returns dict with rolling/weekly/monthly window data, or empty dict on failure.
    """
    api_key = OCGO_API_KEY
    if not api_key:
        return {}

    try:
        conn = http.client.HTTPSConnection("opencode.ai", timeout=5)
        conn.request(
            "GET",
            "/zen/go/v1/usage",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        res = conn.getresponse()
        raw = res.read().decode("utf-8", "replace")
        if res.status == 401:
            return {}
        if res.status < 200 or res.status >= 300:
            return {}
        data = json.loads(raw)
        return data.get("usage", {})
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def select_ocgo_gating_quota(usage: dict) -> dict | None:
    """Always surface the 5h rolling window; fall back to weekly, then
    monthly, only if rolling data is missing. Mirrors
    select_claude_gating_quota: the 5h bucket is what the user watches, and
    showing whichever window is numerically tighter makes the display flip
    to a longer bucket unpredictably.
    """
    if not usage:
        return None

    for key, label in (("rolling", "5h"), ("weekly", "wk"), ("monthly", "mo")):
        w = usage.get(key)
        if not w or w.get("status") != "ok":
            continue
        pct = float(w.get("percent", 0))
        reset_at = w.get("resetsAt", "")
        return {
            "label": label,
            "remaining_pct": 100.0 - pct,
            "resets_in": format_reset_time(reset_at),
            "reset_epoch": (
                datetime.fromisoformat(reset_at.replace("Z", "+00:00")).timestamp()
                if reset_at else float("inf")
            ),
        }
    return None


def get_color_tag(pct: float, high: str = "#[fg=colour120,bold]", med: str = "#[fg=colour221,bold]", low: str = "#[fg=colour203,bold]") -> str:
    if pct >= 50.0:
        return high
    elif pct >= 20.0:
        return med
    return low


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    # 1. Disabled: Antigravity quota (re-enable by uncommenting)
    # agy_data = fetch_live_agy_quota()
    # if not agy_data:
    #     agy_data = load_cached_agy_quota()
    agy_summary = None

    # 2. Fetch Claude quota
    claude_data = load_claude_quota()
    claude_summary = select_claude_gating_quota(claude_data)

    # 2b. Disabled: Pi today's spend (re-enable by uncommenting)
    # pi_cost = compute_pi_today_spend()
    # pi_txt = f"#[fg=colour214]PI {format_pi_spend(pi_cost)}#[default]"

    # 2c. OpenCode Go usage
    ocgo_usage = fetch_opencode_go_usage()
    ocgo_summary = select_ocgo_gating_quota(ocgo_usage)

    # 3. Build tmux formatted strings
    # AGY Segment (disabled)
    agy_txt = ""

    # Claude Segment (active gating constraint)
    if claude_summary and claude_summary.get("remaining_pct") is not None:
        rem = claude_summary["remaining_pct"]
        col = get_color_tag(rem)
        rst = claude_summary.get("resets_in", "")
        rst_str = f" {rst}" if rst else ""
        claude_txt = f"{col}CC {rem:.0f}%{rst_str}#[default]"
    else:
        claude_txt = "#[fg=colour244]CC --#[default]"

    # OpenCode Go Segment (active gating constraint)
    if ocgo_summary and ocgo_summary.get("remaining_pct") is not None:
        rem = ocgo_summary["remaining_pct"]
        col = get_color_tag(rem)
        rst = ocgo_summary.get("resets_in", "")
        rst_str = f" {rst}" if rst else ""
        ocgo_txt = f"{col}OCGO {rem:.0f}%{rst_str}#[default]"
    else:
        ocgo_txt = "#[fg=colour244]OCGO --#[default]"

    # Combined Segment
    combined_parts = []
    if claude_summary and claude_summary.get("remaining_pct") is not None:
        combined_parts.append(claude_txt)
    if ocgo_summary and ocgo_summary.get("remaining_pct") is not None:
        combined_parts.append(ocgo_txt)

    if combined_parts:
        combined_txt = " #[fg=colour240]│#[default] ".join(combined_parts)
    else:
        combined_txt = f"{claude_txt} #[fg=colour240]│#[default] {ocgo_txt}"

    # Write files atomically
    for target_file, content in [
        (OUTPUT_CLAUDE_TXT, claude_txt),
        (OUTPUT_OCGO_TXT, ocgo_txt),
        (OUTPUT_COMBINED_TXT, combined_txt),
    ]:
        tmp = f"{target_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, target_file)

    # Save full status json
    status_payload = {
        "timestamp": time.time(),
        "claude": claude_summary,
        "opencode_go": ocgo_summary,
    }
    tmp_json = f"{OUTPUT_STATUS_JSON}.tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(status_payload, f, indent=2)
    os.replace(tmp_json, OUTPUT_STATUS_JSON)


if __name__ == "__main__":
    main()

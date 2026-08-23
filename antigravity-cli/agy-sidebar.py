#!/usr/bin/env python3
"""
agy-sidebar: High-performance live multi-agent process & task monitor.
Tracks Antigravity, Pi Agent, OpenCode, and Claude across Herdr panes and workspaces.
"""

import sys
import os
import time
import select
import subprocess
import json
import sqlite3
import socket
import pathlib
import shlex
import threading
import argparse
import glob
import re
from dataclasses import dataclass, field, replace
from typing import Optional, List, Dict, Any, Tuple

try:
    import tty
    import termios
except ImportError:
    tty = None
    termios = None

from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from rich.align import Align


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# A transcript showing WORKING but untouched for longer than this is stale.
STATUS_STALE_AFTER_SEC = 60.0
# Re-read status-state.json at most this often (it is global across panes).
STATE_FILE_TTL_SEC = 2.0
HERDR_SOCK_PATH = os.path.expanduser("~/.config/herdr/herdr.sock")
HERDR_MAX_RESPONSE_BYTES = 1024 * 1024


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class AgentInfo:
    pane_id: str
    agent_type: str  # "agy", "pi", "opencode", "claude", "unknown"
    cwd: str
    status: str  # "WORKING", "IDLE", "DONE", "BLOCKED", "WAITING INPUT", "UNKNOWN"
    task: str
    current_step: str
    model: str
    updated_at: float = field(default_factory=time.time)
    is_focused: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TelemetrySnapshot:
    agents: List[AgentInfo] = field(default_factory=list)
    quotas_ansi: str = ""
    context_ansi: str = ""
    transcript_ansi: str = ""
    last_updated: float = field(default_factory=time.time)


# -----------------------------------------------------------------------------
# Fast Utilities & Formatting Helpers
# -----------------------------------------------------------------------------

def read_jsonl_tail(path: str, max_bytes: int = 65536) -> List[Dict[str, Any]]:
    """Reads only the last few records from a JSONL file without loading it entirely.

    Note: the tail chunk may start mid-line; a partial leading line fails JSON
    parsing and is silently skipped, which is acceptable for tail sampling.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes), os.SEEK_SET)
            chunk = f.read().decode("utf-8", errors="ignore")
            lines = chunk.strip().splitlines()
            records = []
            for l in lines:
                if l.strip():
                    try:
                        records.append(json.loads(l))
                    except Exception:
                        pass
            return records
    except Exception:
        return []


def clean_task_prompt(text: str) -> str:
    """Strips XML/system tags, skills, and compresses whitespace for clean task display."""
    if not text:
        return ""
    # Strip <skill ...>...</skill> blocks completely
    text = re.sub(r"<skill[^>]*>.*?</skill>", "", text, flags=re.DOTALL)
    # Check for explicit <USER_REQUEST> tags
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL | re.IGNORECASE)
    if m:
        cleaned = m.group(1).strip()
        if cleaned:
            return cleaned.splitlines()[0][:140]

    lines = []
    in_tag = False
    for line in text.splitlines():
        l_str = line.strip()
        if not l_str:
            continue
        if l_str.startswith("<ADDITIONAL_METADATA>") or l_str.startswith("<SKILL>") or l_str.startswith("<USER_SETTINGS_CHANGE>"):
            in_tag = True
            continue
        if in_tag:
            if l_str.endswith("</ADDITIONAL_METADATA>") or l_str.endswith("</SKILL>") or l_str.endswith("</USER_SETTINGS_CHANGE>"):
                in_tag = False
            continue
        if l_str.startswith("<") and l_str.endswith(">"):
            continue
        if l_str.startswith("The current local time is:") or l_str.startswith("The user has mentioned") or l_str.startswith("The user changed setting"):
            continue
        lines.append(l_str)

    res = " ".join(lines).strip()
    return res[:140] if res else ""


def shorten_path(path: str) -> str:
    if not path:
        return ""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def normalize_model_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def shorten_model_name(name: str) -> str:
    if not name:
        return "Gemini"

    clean = re.sub(r"\(.*?\)", "", name).strip()
    thinking_suffix = ""
    lower = name.lower()
    if "thinking" in lower:
        thinking_suffix = " (T)"
    elif "high" in lower:
        thinking_suffix = " (H)"
    elif "medium" in lower:
        thinking_suffix = " (M)"
    elif "low" in lower:
        thinking_suffix = " (L)"

    # DeepSeek (accepts "deepseek v4", "deepseek-v4", "deepseek_v4")
    m = re.search(r"(?:deepseek[\s\-_]+)(v\d+|r\d+)?(?:[\s\-_]+)?(flash|chat|coder)?", clean, re.IGNORECASE)
    if m and m.group(1):
        parts = [p for p in m.groups() if p]
        return f"DeepSeek {' '.join(parts)}".strip() + thinking_suffix

    # Claude
    m = re.search(r"(?:claude[\s\-_]+)?(?:(\d+\.\d+)[\s\-_]+)?(opus|sonnet|haiku)(?:[\s\-_]+(\d+\.\d+))?", clean, re.IGNORECASE)
    if m and ("claude" in lower or m.group(2)):
        family = m.group(2).capitalize()
        ver = m.group(3) or m.group(1) or ""
        return f"{family} {ver}".strip() + thinking_suffix

    # Gemini (accepts "gemini 2.5 pro", "gemini-2.5-pro", "gemini_2.5_pro")
    m = re.search(r"(?:gemini[\s\-_]+)(?:(\d+\.\d+)[\s\-_]+)?(flash|pro|ultra)(?:[\s\-_]+(\d+\.\d+))?", clean, re.IGNORECASE)
    if m:
        family = m.group(2).capitalize()
        ver = m.group(3) or m.group(1) or ""
        return f"Gemini {ver} {family}".strip() + thinking_suffix

    # Standalone Flash/Pro
    m = re.search(r"^(?:(\d+\.\d+)\s+)?(flash|pro|ultra)$", clean, re.IGNORECASE)
    if m:
        family = m.group(2).capitalize()
        ver = m.group(1) or ""
        return f"Gemini {ver} {family}".strip() + thinking_suffix

    # OpenCode / GLM / Kimi / GPT
    m = re.search(r"(glm[-_ ]?\d+(?:\.\d+)?|kimi[-_ ]?k\d+|gpt[-_ ]?\d+o?[-_ ]?mini|gpt[-_ ]?\d+o|gpt[-_ ]?oss|mimo[-_ ]?v\d+(?:\.\d+)?)", clean, re.IGNORECASE)
    if m:
        return m.group(1).upper() + thinking_suffix

    if len(clean) > 18:
        clean = clean[:18].strip()

    return clean + thinking_suffix


def format_time_ago(ts: float) -> str:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return "unknown"
    try:
        diff = int(time.time() - ts)
    except Exception:
        return "unknown"
    if diff < 5:
        return "just now"
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        mins = diff // 60
        return f"{mins}m ago"
    hours = diff // 3600
    rem_mins = (diff % 3600) // 60
    return f"{hours}h {rem_mins}m ago"


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def herdr_socket_request(payload: Dict[str, Any], timeout: float = 0.5) -> Optional[Dict[str, Any]]:
    """Sends one JSON request over the Herdr Unix socket and reads one JSON response.

    Reads until the accumulated buffer parses as a JSON object, the peer closes,
    or the byte cap is hit — tolerant of partial frames and of responses that
    omit the trailing newline.
    """
    if not os.path.exists(HERDR_SOCK_PATH):
        return None
    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(HERDR_SOCK_PATH)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = bytearray()
        while len(buf) <= HERDR_MAX_RESPONSE_BYTES:
            if buf:
                try:
                    obj = json.loads(bytes(buf).decode("utf-8", errors="ignore").strip())
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
            chunk = sock.recv(65536)
            if not chunk or not isinstance(chunk, (bytes, bytearray)):
                break
            buf.extend(chunk)
        try:
            obj = json.loads(bytes(buf).decode("utf-8", errors="ignore").strip())
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    except Exception:
        return None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def sqlite_ro_connect(db_path: str, timeout: float = 0.2) -> sqlite3.Connection:
    """Opens a SQLite database read-only via a properly percent-encoded file URI."""
    uri = pathlib.Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout)


def focus_herdr_pane(pane_id: str) -> bool:
    """Focuses a pane in Herdr via socket API or CLI."""
    if not pane_id:
        return False
    resp = herdr_socket_request(
        {"id": "sidebar:focus", "method": "pane.focus", "params": {"pane_id": pane_id}},
        timeout=0.2,
    )
    if resp is not None:
        return True
    try:
        res = subprocess.run(["herdr", "pane", "focus", "--pane", pane_id], capture_output=True, timeout=0.5)
        return res.returncode == 0
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Quota & State Loading
# -----------------------------------------------------------------------------

def load_quota_data(
    quota_path: Optional[str] = None,
    state_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Loads quota-cache.json and status-state.json directly in Python (<0.2ms)."""
    quota_file = quota_path or os.environ.get(
        "AGY_QUOTA_CACHE",
        os.path.expanduser("~/.antigravity/quota-cache.json"),
    )
    state_file = state_path or os.environ.get(
        "AGY_STATUS_STATE",
        os.path.expanduser("~/.antigravity/status-state.json"),
    )

    quota_data = {}
    if os.path.exists(quota_file):
        try:
            with open(quota_file, "r", encoding="utf-8") as f:
                quota_data = json.load(f)
        except Exception:
            pass

    state_data = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
        except Exception:
            pass

    return quota_data, state_data


# -----------------------------------------------------------------------------
# CWD -> Active Session Mapping
# -----------------------------------------------------------------------------

class CwdResolver:
    """Fast indexed resolver mapping workspace CWD paths to Antigravity conversation IDs."""

    def __init__(self, conv_dir: Optional[str] = None):
        self.conv_dir = conv_dir or os.path.expanduser("~/.gemini/antigravity-cli/conversations")
        # Cache key = (dir mtime, max db mtime, db count) so in-place .db rewrites
        # also invalidate the index, not just newly created files.
        self._cache_key: Tuple[float, float, int] = (0.0, 0.0, 0)
        self._cwd_to_cid: Dict[str, str] = {}
        self._cid_to_cwd: Dict[str, str] = {}
        self._latest_cid: Optional[str] = None

    def refresh_index(self, max_dbs: int = 30) -> None:
        if not os.path.isdir(self.conv_dir):
            return
        try:
            dir_mtime = os.path.getmtime(self.conv_dir)
        except Exception:
            dir_mtime = 0.0

        db_files = []
        try:
            with os.scandir(self.conv_dir) as entries:
                for entry in entries:
                    # "x.db-wal"/"x.db-shm" never end with ".db", no extra check needed.
                    if entry.name.endswith(".db"):
                        try:
                            db_files.append((entry.path, entry.stat().st_mtime))
                        except Exception:
                            pass
        except Exception:
            pass

        cache_key = (dir_mtime, max((m for _, m in db_files), default=0.0), len(db_files))
        if cache_key == self._cache_key and self._cwd_to_cid:
            return

        db_files.sort(key=lambda x: x[1], reverse=True)
        top_dbs = db_files[:max_dbs]

        cwd_to_cid: Dict[str, str] = {}
        cid_to_cwd: Dict[str, str] = {}
        latest_cid: Optional[str] = None

        for db_path, _ in top_dbs:
            cid = os.path.splitext(os.path.basename(db_path))[0]
            try:
                conn = sqlite_ro_connect(db_path, timeout=0.1)
                cur = conn.cursor()
                cur.execute("SELECT data FROM trajectory_metadata_blob WHERE id=\"main\"")
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    if latest_cid is None:
                        latest_cid = cid
                    raw = row[0]
                    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
                    uris = re.findall(r"file://([A-Za-z0-9_\-./]+)", text)
                    for u in uris:
                        p = u.rstrip("./")
                        cand = p
                        while cand and not os.path.exists(cand) and len(cand) > 1:
                            cand = os.path.dirname(cand)
                        if not cand and p:
                            cand = p
                        if cand:
                            norm = os.path.abspath(os.path.expanduser(cand))
                            if norm not in cwd_to_cid:
                                cwd_to_cid[norm] = cid
                            if cid not in cid_to_cwd:
                                cid_to_cwd[cid] = norm
            except Exception:
                pass

        self._cwd_to_cid = cwd_to_cid
        self._cid_to_cwd = cid_to_cwd
        self._latest_cid = latest_cid
        self._cache_key = cache_key

    def resolve_cwd(self, cwd: Optional[str]) -> Optional[str]:
        self.refresh_index()
        if not cwd:
            return self._latest_cid
        norm_cwd = os.path.abspath(os.path.expanduser(cwd))
        # 1. Exact match
        if norm_cwd in self._cwd_to_cid:
            return self._cwd_to_cid[norm_cwd]
        # 2. Subdirectory match (cwd is inside a workspace root)
        best_match = None
        best_len = 0
        for ws, cid in self._cwd_to_cid.items():
            if norm_cwd == ws or norm_cwd.startswith(ws.rstrip("/") + "/"):
                if len(ws) > best_len:
                    best_match = cid
                    best_len = len(ws)
        if best_match:
            return best_match
        # 3. Parent directory match (workspace is inside cwd); longest match wins
        best_parent = None
        best_plen = 0
        for ws, cid in self._cwd_to_cid.items():
            if ws.startswith(norm_cwd.rstrip("/") + "/") and len(ws) > best_plen:
                best_parent = cid
                best_plen = len(ws)
        if best_parent:
            return best_parent
        return self._latest_cid


# -----------------------------------------------------------------------------
# Agent Extractors
# -----------------------------------------------------------------------------

class AntigravityExtractor:
    def __init__(self, brain_dir: Optional[str] = None, conv_dir: Optional[str] = None):
        self.brain_dir = brain_dir or os.path.expanduser("~/.gemini/antigravity-cli/brain")
        self.cwd_resolver = CwdResolver(conv_dir)
        self._cache: Dict[str, Tuple[int, AgentInfo]] = {}  # transcript_path -> (mtime_ns, info)
        self._prompt_cache: Dict[str, str] = {}  # conv_id -> prompt
        self._state_cache: Dict[str, Any] = {}
        self._state_ts: float = 0.0

    def _get_state(self) -> Dict[str, Any]:
        """Global status-state.json with a short TTL (it is shared across panes)."""
        if time.time() - self._state_ts > STATE_FILE_TTL_SEC:
            _, state = load_quota_data()
            self._state_cache = state if isinstance(state, dict) else {}
            self._state_ts = time.time()
        return self._state_cache

    def extract(self, session_id: Optional[str], pane_id: str, cwd: str, is_focused: bool) -> AgentInfo:
        target_conv_id = session_id or self.cwd_resolver.resolve_cwd(cwd)
        target_dir = None

        if target_conv_id:
            cand = os.path.join(self.brain_dir, target_conv_id)
            if os.path.isdir(cand):
                target_dir = cand

        if not target_dir:
            # No session resolved for this pane: report unresolved rather than
            # adopting the most recently modified session (which likely belongs
            # to a different pane).
            return AgentInfo(
                pane_id=pane_id,
                agent_type="agy",
                cwd=cwd,
                status="UNKNOWN",
                task="No matching AGY session",
                current_step="Idle",
                model="Gemini 3.7 Flash",
                is_focused=is_focused,
            )

        conv_id = os.path.basename(target_dir)
        transcript_path = os.path.join(target_dir, ".system_generated", "logs", "transcript.jsonl")

        if not os.path.isfile(transcript_path):
            return AgentInfo(
                pane_id=pane_id,
                agent_type="agy",
                cwd=cwd,
                status="IDLE",
                task="Active Antigravity workspace",
                current_step="Ready",
                model="Gemini 3.7 Flash",
                is_focused=is_focused,
                details={"session_id": conv_id},
            )

        try:
            mtime_ns = os.stat(transcript_path).st_mtime_ns
        except Exception:
            mtime_ns = 0

        cached = self._cache.get(transcript_path)
        if cached and cached[0] == mtime_ns:
            # Return a copy: the cached instance must never be mutated per-pane.
            info = replace(cached[1])
            info.pane_id = pane_id
            info.is_focused = is_focused
            return info

        # Extract initial task prompt once per conv_id
        task_prompt = self._prompt_cache.get(conv_id)
        if not task_prompt:
            try:
                with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                    for _ in range(50):
                        line = f.readline()
                        if not line:
                            break
                        d = json.loads(line.strip())
                        if d.get("type") == "USER_INPUT":
                            raw_content = d.get("content", "")
                            cleaned = clean_task_prompt(raw_content)
                            if cleaned:
                                task_prompt = cleaned
                                break
            except Exception:
                pass
            task_prompt = task_prompt or "Antigravity task in flight"
            self._prompt_cache[conv_id] = task_prompt

        # Tail scan for current step and model
        records = read_jsonl_tail(transcript_path, max_bytes=65536)
        current_step = "Idle"
        status = "IDLE"
        model = "Gemini 3.7 Flash"
        active_tools: List[str] = []

        for r in reversed(records):
            t = r.get("type")
            tool_calls = r.get("tool_calls", [])
            content = r.get("content", "")

            if t == "PLANNER_RESPONSE":
                if tool_calls:
                    names = [tc.get("name", "tool") for tc in tool_calls]
                    active_tools = names
                    current_step = f"running: {', '.join(names)}"
                    status = "WORKING"
                else:
                    cleaned_resp = clean_task_prompt(content)
                    current_step = cleaned_resp[:80] if cleaned_resp else "Responded"
                    status = "IDLE"
                break
            elif t == "TOOL_CALL_RESULT":
                current_step = "Processing tool result"
                status = "WORKING"
                break
            elif t == "USER_INPUT":
                current_step = "Received user input"
                status = "WORKING"
                break

        # Check status-state.json for active model if available
        model_state = self._get_state().get("model")
        if isinstance(model_state, str) and model_state:
            model = model_state

        # Staleness guard: a transcript claiming WORKING but untouched for a
        # while means the inference is stale, not that the agent still works.
        if status == "WORKING" and mtime_ns and (time.time() - mtime_ns / 1e9) > STATUS_STALE_AFTER_SEC:
            status = "UNKNOWN"
            current_step = f"{current_step} (stale)"

        info = AgentInfo(
            pane_id=pane_id,
            agent_type="agy",
            cwd=cwd,
            status=status,
            task=task_prompt,
            current_step=current_step,
            model=model,
            updated_at=mtime_ns / 1e9 if mtime_ns else time.time(),
            is_focused=is_focused,
            details={
                "session_id": conv_id,
                "transcript_path": transcript_path,
                "tool_calls": active_tools,
            },
        )
        self._cache[transcript_path] = (mtime_ns, info)
        return info


class PiExtractor:
    def __init__(self, sessions_dir: Optional[str] = None):
        self.sessions_dir = sessions_dir or os.path.expanduser("~/.pi/agent/sessions")
        self._cache: Dict[str, Tuple[int, AgentInfo]] = {}
        self._prompt_cache: Dict[str, str] = {}

    def extract(self, session_path: Optional[str], pane_id: str, cwd: str, is_focused: bool) -> AgentInfo:
        target_file = session_path
        if target_file and not os.path.isfile(target_file):
            return AgentInfo(
                pane_id=pane_id,
                agent_type="pi",
                cwd=cwd,
                status="UNKNOWN",
                task="Pi session file not found",
                current_step="Idle",
                model="pi",
                is_focused=is_focused,
            )

        if not target_file and os.path.isdir(self.sessions_dir):
            pi_sessions = glob.glob(os.path.join(self.sessions_dir, "**/*.jsonl"), recursive=True)
            if pi_sessions:
                norm_cwd = os.path.abspath(os.path.expanduser(cwd))
                matching = [p for p in pi_sessions if os.path.dirname(p) == norm_cwd or norm_cwd in p]
                target_file = max(matching or pi_sessions, key=os.path.getmtime)

        if not target_file or not os.path.isfile(target_file):
            return AgentInfo(
                pane_id=pane_id,
                agent_type="pi",
                cwd=cwd,
                status="UNKNOWN",
                task="No Pi session found",
                current_step="Idle",
                model="pi",
                is_focused=is_focused,
            )

        try:
            mtime_ns = os.stat(target_file).st_mtime_ns
        except Exception:
            mtime_ns = 0

        cached = self._cache.get(target_file)
        if cached and cached[0] == mtime_ns:
            # Return a copy: the cached instance must never be mutated per-pane.
            info = replace(cached[1])
            info.pane_id = pane_id
            info.is_focused = is_focused
            return info

        task_prompt = self._prompt_cache.get(target_file)
        if not task_prompt:
            try:
                with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                    for _ in range(60):
                        line = f.readline()
                        if not line:
                            break
                        d = json.loads(line.strip())
                        if d.get("type") == "message" and d.get("message", {}).get("role") == "user":
                            content = d.get("message", {}).get("content", "")
                            raw_text = ""
                            if isinstance(content, list):
                                for item in content:
                                    if item.get("type") == "text":
                                        raw_text = item.get("text", "")
                                        cleaned = clean_task_prompt(raw_text)
                                        if cleaned:
                                            task_prompt = cleaned
                                            break
                            elif isinstance(content, str):
                                cleaned = clean_task_prompt(content)
                                if cleaned:
                                    task_prompt = cleaned
                            if task_prompt:
                                break
            except Exception:
                pass
            task_prompt = task_prompt or "Pi task in flight"
            self._prompt_cache[target_file] = task_prompt

        records = read_jsonl_tail(target_file, max_bytes=65536)
        current_step = "Idle"
        status = "IDLE"
        model = "pi"
        active_tools: List[str] = []

        # Pass 1 (whole tail): latest model_change wins regardless of where it
        # sits relative to the last informative message.
        for r in records:
            if r.get("type") == "model_change":
                m_info = r.get("model", {})
                name = m_info.get("name") if isinstance(m_info, dict) else None
                model = str(name or r.get("provider") or model)

        # Pass 2 (reverse): infer current step/status from the newest records.
        for r in reversed(records):
            t = r.get("type")
            if t == "message":
                msg = r.get("message", {})
                role = msg.get("role")
                content = msg.get("content", [])
                if role == "assistant":
                    if isinstance(content, list):
                        for item in content:
                            if item.get("type") == "toolCall":
                                name = item.get("name", "tool")
                                args = str(item.get("arguments", ""))[:40]
                                active_tools.append(name)
                                current_step = f"running: {name}({args})"
                                status = "WORKING"
                                break
                            elif item.get("type") == "text":
                                txt = item.get("text", "").strip()
                                if txt:
                                    current_step = txt.split("\n")[0][:80]
                                    status = "IDLE"
                    if current_step != "Idle":
                        break
                elif role == "toolResult":
                    current_step = "Processing tool result"
                    status = "WORKING"
                    break
                elif role == "user":
                    current_step = "Waiting for assistant response"
                    status = "WORKING"
                    break

        # Staleness guard (same policy as the AGY extractor).
        if status == "WORKING" and mtime_ns and (time.time() - mtime_ns / 1e9) > STATUS_STALE_AFTER_SEC:
            status = "UNKNOWN"
            current_step = f"{current_step} (stale)"

        info = AgentInfo(
            pane_id=pane_id,
            agent_type="pi",
            cwd=cwd,
            status=status,
            task=task_prompt,
            current_step=current_step,
            model=model,
            updated_at=mtime_ns / 1e9 if mtime_ns else time.time(),
            is_focused=is_focused,
            details={"session_file": target_file, "tool_calls": active_tools},
        )
        self._cache[target_file] = (mtime_ns, info)
        return info


class OpenCodeExtractor:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.expanduser("~/.local/share/opencode/opencode.db")
        self._last_data_version = None
        self._cached_sessions: List[Dict[str, Any]] = []

    def get_sessions(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.db_path):
            return []
        try:
            conn = sqlite_ro_connect(self.db_path)
            cur = conn.cursor()
            cur.execute("PRAGMA data_version;")
            ver = cur.fetchone()[0]
            if ver == self._last_data_version and self._cached_sessions:
                conn.close()
                return self._cached_sessions

            cur.execute("""
                SELECT s.id, s.title, s.directory, s.agent, s.model, s.time_updated
                FROM session s
                ORDER BY s.time_updated DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
            results = []
            for sid, title, directory, agent, model_json, time_updated in rows:
                model_name = "opencode"
                if model_json:
                    try:
                        m_obj = json.loads(model_json)
                        model_name = m_obj.get("id") or m_obj.get("model") or "opencode"
                    except Exception:
                        model_name = str(model_json)

                # Check active todos
                active_todo = None
                try:
                    cur.execute("SELECT content, status FROM todo WHERE session_id = ? ORDER BY position ASC LIMIT 1", (sid,))
                    todo_row = cur.fetchone()
                    if todo_row:
                        active_todo = todo_row[0]
                except Exception:
                    pass

                results.append({
                    "session_id": sid,
                    "title": title or "Untitled session",
                    "directory": directory or "",
                    "agent": agent or "opencode",
                    "model": model_name,
                    "todo": active_todo,
                    "time_updated": time_updated or 0,
                })
            conn.close()
            self._last_data_version = ver
            self._cached_sessions = results
            return results
        except Exception:
            return self._cached_sessions


# -----------------------------------------------------------------------------
# Unified Discovery Engine
# -----------------------------------------------------------------------------

class MultiAgentDiscovery:
    def __init__(
        self,
        agy_extractor: Optional[AntigravityExtractor] = None,
        pi_extractor: Optional[PiExtractor] = None,
        opencode_extractor: Optional[OpenCodeExtractor] = None,
    ):
        self.agy_extractor = agy_extractor or AntigravityExtractor()
        self.pi_extractor = pi_extractor or PiExtractor()
        self.opencode_extractor = opencode_extractor or OpenCodeExtractor()

    def discover_from_herdr_socket(self) -> Optional[List[AgentInfo]]:
        resp = herdr_socket_request(
            {"id": "sidebar:probe", "method": "pane.list", "params": {"workspace_id": None}},
            timeout=0.3,
        )
        if resp is None:
            return None
        try:
            panes = resp.get("result", {}).get("panes", [])

            agents: List[AgentInfo] = []
            for p in panes:
                agent_tag = p.get("agent")
                sess_info = p.get("agent_session", {})
                pane_id = p.get("pane_id", "")
                cwd = p.get("cwd", "")
                is_focused = bool(p.get("focused", False))
                sess_val = sess_info.get("value")

                if agent_tag == "agy" or (sess_info.get("source") == "herdr:antigravity_cli"):
                    info = self.agy_extractor.extract(sess_val, pane_id, cwd, is_focused)
                    agents.append(info)
                elif agent_tag == "pi" or (sess_info.get("source") == "herdr:pi"):
                    info = self.pi_extractor.extract(sess_val, pane_id, cwd, is_focused)
                    agents.append(info)
                elif agent_tag == "opencode":
                    oc_sessions = self.opencode_extractor.get_sessions()
                    matching = next((s for s in oc_sessions if s["directory"] == cwd), oc_sessions[0] if oc_sessions else None)
                    if matching:
                        agents.append(AgentInfo(
                            pane_id=pane_id,
                            agent_type="opencode",
                            cwd=cwd,
                            status="WORKING" if matching.get("todo") else "IDLE",
                            task=matching["title"],
                            current_step=f"todo: {matching['todo']}" if matching.get("todo") else "Idle",
                            model=matching["model"],
                            is_focused=is_focused,
                            updated_at=safe_float(matching.get("time_updated"), time.time()),
                        ))
                else:
                    pass

            return agents
        except Exception:
            return None

    def discover_fallback(self) -> List[AgentInfo]:
        agents = []
        agy_info = self.agy_extractor.extract(None, "local:agy", os.getcwd(), True)
        if agy_info.status != "UNKNOWN":
            agents.append(agy_info)

        pi_info = self.pi_extractor.extract(None, "local:pi", os.getcwd(), False)
        if pi_info.status != "UNKNOWN":
            agents.append(pi_info)

        oc_sessions = self.opencode_extractor.get_sessions()
        for i, s in enumerate(oc_sessions[:2]):
            agents.append(AgentInfo(
                pane_id=f"local:oc:{i}",
                agent_type="opencode",
                cwd=s["directory"],
                status="WORKING" if s.get("todo") else "IDLE",
                task=s["title"],
                current_step=f"todo: {s['todo']}" if s.get("todo") else "Ready",
                model=s["model"],
                is_focused=False,
                updated_at=safe_float(s.get("time_updated"), time.time()),
            ))
        return agents

    def discover_all(self) -> List[AgentInfo]:
        herdr_agents = self.discover_from_herdr_socket()
        if herdr_agents:
            return herdr_agents
        return self.discover_fallback()


# -----------------------------------------------------------------------------
# Background Telemetry Manager
# -----------------------------------------------------------------------------

class TelemetryManager:
    def __init__(self, discovery: Optional[MultiAgentDiscovery] = None, auto_start: bool = True):
        self.discovery = discovery or MultiAgentDiscovery()
        self.snapshot = TelemetrySnapshot()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cached_context: str = ""
        self._context_loaded: bool = False
        self._toku_running: bool = False
        self._worker: Optional[threading.Thread] = None

        if auto_start:
            self.start()

    def start(self):
        if self._worker is None:
            self._worker = threading.Thread(target=self._poll_loop, daemon=True)
            self._worker.start()

    def get_snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return self.snapshot

    def trigger_toku_probe(self):
        """Asynchronously trigger toku live probe when user requests it with 'r' on Quotas tab."""
        if self._toku_running:
            return
        self._toku_running = True

        def _run():
            try:
                subprocess.run("toku", shell=True, capture_output=True, text=True, timeout=10.0)
            except Exception:
                pass
            finally:
                self._toku_running = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def load_context_on_demand(self, force: bool = False) -> str:
        """Load context only when Tab 3 is active or refreshed."""
        if self._context_loaded and not force:
            return self._cached_context
        try:
            res = subprocess.run("mimori map --stdout", shell=True, capture_output=True, text=True, timeout=3.0)
            if res.returncode == 0 and res.stdout.strip():
                self._cached_context = res.stdout.strip()
            else:
                self._cached_context = res.stderr.strip() or "No symbol map available from mimori."
        except Exception as e:
            self._cached_context = f"mimori unavailable: {e}"
        self._context_loaded = True
        return self._cached_context

    def _poll_loop(self):
        while not self._stop_event.is_set():
            now = time.time()
            agents = self.discovery.discover_all()

            new_snap = TelemetrySnapshot(
                agents=agents,
                last_updated=now,
            )
            with self._lock:
                self.snapshot = new_snap

            # Back off when nothing is live; full speed while agents exist.
            time.sleep(0.5 if agents else 2.0)

    def stop(self):
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)


# -----------------------------------------------------------------------------
# Sidebar TUI Application
# -----------------------------------------------------------------------------

class SidebarApp:
    def __init__(self, telemetry: TelemetryManager):
        self.telemetry = telemetry
        self.current_tab = 1  # 1: Agents, 2: Quotas, 3: Context, 4: Logs
        self.selected_index = 0
        self.selected_pane_id: Optional[str] = None  # stable cursor across reorders
        self.scroll_pause = False
        self.cached_logs = ""
        self.status_msg = ""

    def render_status_pill(self, status: str) -> str:
        st = status.upper().strip()
        if st == "WORKING":
            return "[bold white on green] WORKING [/]"
        elif st == "IDLE":
            return "[bold white on blue] IDLE [/]"
        elif st == "DONE":
            return "[bold white on cyan] DONE [/]"
        elif st in ("WAITING INPUT", "WAITING"):
            return "[bold white on bright_yellow] WAITING [/]"
        elif st == "BLOCKED":
            return "[bold white on bright_yellow] BLOCKED [/]"
        else:
            return f"[dim] {st or 'UNKNOWN'} [/]"

    def render_agent_badge(self, agent_type: str) -> str:
        at = agent_type.lower()
        if at == "agy":
            return "[bold bright_blue]AGY[/]"
        elif at == "pi":
            return "[bold magenta]PI[/]"
        elif at == "opencode":
            return "[bold green]OPENCODE[/]"
        elif at == "claude":
            return "[bold yellow]CLAUDE[/]"
        return f"[bold white]{agent_type.upper()}[/]"

    def render_agents_tab(self, agents: List[AgentInfo]) -> Layout:
        agents_layout = Layout()
        agents_layout.split_column(
            Layout(name="table", ratio=3),
            Layout(name="detail", size=6),
        )

        if not agents:
            empty_table = Panel(Text("No active AI agents detected in Herdr or session stores.", style="dim italic"), title="Active Agents (0)")
            empty_detail = Panel(Text("No agent selected.", style="dim italic"), title="Agent Details")
            agents_layout["table"].update(empty_table)
            agents_layout["detail"].update(empty_detail)
            return agents_layout

        # Resolve selection by pane_id so the cursor stays on the same agent
        # when the pane list reorders between polls.
        ids = [a.pane_id for a in agents]
        if self.selected_pane_id in ids:
            self.selected_index = ids.index(self.selected_pane_id)
        else:
            self.selected_index = max(0, min(len(agents) - 1, self.selected_index))
            self.selected_pane_id = agents[self.selected_index].pane_id

        table = Table(expand=True, box=None, show_header=True, header_style="bold dim")
        table.add_column("", width=2)
        table.add_column("STATUS", width=14)
        table.add_column("AGENT", width=10)
        table.add_column("WORKSPACE", width=20)
        table.add_column("PANE", width=10)
        table.add_column("MODEL", width=18)
        table.add_column("TASK & CURRENT STEP")

        for idx, agent in enumerate(agents):
            is_selected = (idx == self.selected_index)
            cursor = "[bold bright_cyan]▶[/]" if is_selected else " "
            status_pill = self.render_status_pill(agent.status)
            badge = self.render_agent_badge(agent.agent_type)
            ws_name = os.path.basename(agent.cwd) or agent.cwd
            workspace_col = f"[bold magenta]{ws_name[:18]}[/]"
            pane_col = f"[dim]({agent.pane_id})[/]"
            model_col = f"[cyan]{shorten_model_name(agent.model)}[/]"

            task_short = agent.task[:60] if agent.task else "No task"
            step_short = agent.current_step[:50] if agent.current_step else "Idle"
            task_col = f"[bold white]{task_short}[/] [dim]·[/] [italic yellow]{step_short}[/]"

            table.add_row(cursor, status_pill, badge, workspace_col, pane_col, model_col, task_col)

        table_panel = Panel(table, title=f"Active Agents ({len(agents)})")
        agents_layout["table"].update(table_panel)

        # Selected agent details
        sel = agents[self.selected_index]
        detail_text = Text()
        detail_text.append(f"📁 Workspace: ", style="bold white")
        detail_text.append(f"{sel.cwd}  ", style="magenta")
        detail_text.append(f"| ⚡ Model: ", style="dim")
        detail_text.append(f"{sel.model}  ", style="cyan")
        detail_text.append(f"| ⏱️ Active: ", style="dim")
        detail_text.append(f"{format_time_ago(sel.updated_at)}\n", style="dim")

        detail_text.append(f"🎯 Task: ", style="bold white")
        detail_text.append(f"{sel.task}\n", style="white")

        detail_text.append(f"⚙️ Step: ", style="bold yellow")
        detail_text.append(f"{sel.current_step}", style="yellow")

        tool_calls = sel.details.get("tool_calls", [])
        if tool_calls:
            detail_text.append(f"\n🔧 Active Tools: ", style="bold cyan")
            detail_text.append(f"{', '.join(tool_calls)}", style="bright_cyan")

        detail_title = f"Agent Details: {sel.agent_type.upper()} ({sel.pane_id})"
        detail_panel = Panel(detail_text, title=detail_title, border_style="bright_blue" if sel.is_focused else "white")
        agents_layout["detail"].update(detail_panel)

        return agents_layout

    def render_quotas_tab(self) -> Panel:
        quotas, state = load_quota_data()
        models = quotas.get("models", {})

        email = state.get("email") or quotas.get("scope", {}).get("email") or "Local User"
        plan = state.get("plan_tier") or quotas.get("scope", {}).get("plan_tier") or "Google AI Ultra"
        active_model = state.get("model", "")

        table = Table(expand=True, box=None, show_header=True, header_style="bold dim")
        table.add_column("MODEL", ratio=3, style="bold white")
        table.add_column("REMAINING", width=12, justify="right")
        table.add_column("BAR", width=14, justify="left")
        table.add_column("REFRESHES IN", width=16, style="cyan")
        table.add_column("RESET TIME", width=24, style="dim")

        if not models:
            empty_msg = Text("\nNo quota cache found in ~/.antigravity/quota-cache.json\nPress 'r' to run live toku probe.\n", style="dim italic")
            return Panel(empty_msg, title=f"Model Quotas & Limits — {email} ({plan})")

        for k, m in sorted(models.items(), key=lambda x: safe_float(x[1].get("remaining_percentage"), 100.0)):
            name = m.get("name", k)
            pct = safe_float(m.get("remaining_percentage"), 100.0)
            refresh = m.get("refreshes_in", "-")
            reset_time = m.get("reset_time", "-")

            color = "green" if pct >= 50 else ("yellow" if pct >= 20 else "red")
            pct_str = f"[{color}]{pct:.1f}%[/]"

            try:
                filled = max(0, min(10, int(pct / 10)))
            except (TypeError, ValueError, OverflowError):
                filled = 0
            bar_str = f"[{color}]{'█' * filled}{'░' * (10 - filled)}[/]"

            is_active = active_model and (normalize_model_name(name) == normalize_model_name(active_model))
            model_display = f"[bold bright_cyan]▶ {name}[/]" if is_active else name

            table.add_row(model_display, pct_str, bar_str, refresh, reset_time)

        subtitle = f"[dim]Account: [bold white]{email}[/] | Plan: [bold cyan]{plan}[/] | Press [bold white]'r'[/] for toku live probe[/]"
        return Panel(table, title=f"Model Quotas & Limits — {email} ({plan})", subtitle=subtitle)

    def render_context_tab(self) -> Panel:
        ctx_text = self.telemetry.load_context_on_demand()
        content = Text.from_ansi(ctx_text)
        return Panel(content, title="Project Context (mimori)", subtitle="[dim]Press 'r' to reload context map[/]")

    def render_logs_tab(self, agents: List[AgentInfo]) -> Panel:
        if not self.scroll_pause:
            active_cid = None
            if agents and 0 <= self.selected_index < len(agents):
                active_cid = agents[self.selected_index].details.get("session_id")
            self.cached_logs = self._get_transcript_tail(active_cid)

        content = Text.from_ansi(self.cached_logs)
        title = "Streaming Transcripts [PAUSED]" if self.scroll_pause else "Streaming Transcripts"
        return Panel(content, title=title, subtitle="[dim]Space: Pause/Resume | 'r': Refresh[/]")

    def _get_transcript_tail(self, conv_id: Optional[str] = None) -> str:
        brain_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain")
        if not os.path.isdir(brain_dir):
            return "No AGY brain directory found."
        try:
            target_dir = None
            if conv_id:
                cand = os.path.join(brain_dir, conv_id)
                if os.path.isdir(cand):
                    target_dir = cand
            if not target_dir:
                subdirs = [os.path.join(brain_dir, d) for d in os.listdir(brain_dir) if os.path.isdir(os.path.join(brain_dir, d))]
                if not subdirs:
                    return "No conversations found."
                target_dir = max(subdirs, key=os.path.getmtime)

            transcript_path = os.path.join(target_dir, ".system_generated", "logs", "transcript.jsonl")
            if not os.path.isfile(transcript_path):
                return f"No transcript in {os.path.basename(target_dir)}"

            records = read_jsonl_tail(transcript_path, max_bytes=32768)
            lines = []
            for data in records[-20:]:
                t = data.get("type", "")
                content = data.get("content", "").strip()
                if t == "USER_INPUT":
                    lines.append(f"\033[1;34m[USER]\033[0m {content.splitlines()[0][:80]}...")
                elif t == "PLANNER_RESPONSE":
                    tool_calls = data.get("tool_calls", [])
                    if tool_calls:
                        names = [tc.get("name", "unknown") for tc in tool_calls]
                        lines.append(f"\033[1;32m[AGY]\033[0m Used tools: {', '.join(names)}")
                    else:
                        lines.append(f"\033[1;32m[AGY]\033[0m Responded")
                elif t == "TOOL_CALL_RESULT":
                    lines.append(f"\033[1;30m[TOOL]\033[0m {content.splitlines()[0][:80]}")
            return f"\033[1;36mActive Conv:\033[0m {os.path.basename(target_dir)}\n\n" + "\n".join(lines)
        except Exception as e:
            return f"Error reading logs: {e}"

    def render(self) -> Layout:
        snapshot = self.telemetry.get_snapshot()

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=1),
        )

        tab_names = ["1: Agents", "2: Quotas", "3: Context", "4: Logs"]
        tabs = []
        for idx_0, name in enumerate(tab_names):
            idx = idx_0 + 1
            if idx == self.current_tab:
                tabs.append(f"[bold white on blue] {name} [/]")
            else:
                tabs.append(f"[dim white] {name} [/]")

        header_title = f"[bold]AGY Multi-Agent Monitor[/] [dim]({len(snapshot.agents)} live)[/]"
        layout["header"].update(Panel(" ".join(tabs), title=header_title))

        if self.current_tab == 1:
            layout["main"].update(self.render_agents_tab(snapshot.agents))
        elif self.current_tab == 2:
            layout["main"].update(self.render_quotas_tab())
        elif self.current_tab == 3:
            layout["main"].update(self.render_context_tab())
        elif self.current_tab == 4:
            layout["main"].update(self.render_logs_tab(snapshot.agents))

        footer_text = " ↑/↓/j/k: Select | Enter: Focus | 1-4: Tab | r: Refresh | q/Esc: Quit"
        if self.status_msg:
            footer_text = f" {self.status_msg} |" + footer_text
        layout["footer"].update(Text(footer_text, style="dim"))
        return layout

    def focus_selected_agent(self, agents: List[AgentInfo]):
        if not agents or self.selected_index >= len(agents):
            return
        agent = agents[self.selected_index]
        ok = focus_herdr_pane(agent.pane_id)
        if ok:
            self.status_msg = f"Focused {agent.pane_id}"
        else:
            self.status_msg = f"Could not focus {agent.pane_id}"

    def run(self):
        fd = sys.stdin.fileno()
        old_settings = None
        is_tty = False
        tty_mod = tty
        term_mod = termios
        try:
            is_tty = bool(os.isatty(fd)) and term_mod is not None and tty_mod is not None
            if is_tty and term_mod is not None and tty_mod is not None:
                old_settings = term_mod.tcgetattr(fd)
                tty_mod.setcbreak(fd)
        except Exception:
            is_tty = False

        try:
            with Live(self.render(), refresh_per_second=4, screen=True) as live:
                while True:
                    snapshot = self.telemetry.get_snapshot()
                    try:
                        live.update(self.render())
                    except Exception:
                        pass

                    if is_tty:
                        try:
                            r, _, _ = select.select([fd], [], [], 0.25)
                            if not r:
                                continue
                            raw = os.read(fd, 64).decode("utf-8", errors="ignore")
                            if not raw:
                                continue

                            # Normalize key events
                            action = None
                            if raw in ("\x1b[A", "\x1bOA", "\x1b[1;5A", "k", "K"):
                                action = "UP"
                            elif raw in ("\x1b[B", "\x1bOB", "\x1b[1;5B", "j", "J"):
                                action = "DOWN"
                            elif raw in ("\x1b[C", "\x1bOC"):
                                action = "RIGHT"
                            elif raw in ("\x1b[D", "\x1bOD"):
                                action = "LEFT"
                            elif raw in ("\r", "\n"):
                                action = "ENTER"
                            elif raw in ("q", "Q", "\x1b"):
                                action = "QUIT"
                            elif raw == " ":
                                action = "SPACE"
                            elif raw in ("r", "R"):
                                action = "REFRESH"
                            elif raw in ("1", "2", "3", "4"):
                                action = f"TAB_{raw}"
                            elif raw.startswith("\x1b[") or raw.startswith("\x1bO"):
                                if raw.endswith("A"):
                                    action = "UP"
                                elif raw.endswith("B"):
                                    action = "DOWN"

                            # Dispatch action
                            if action == "QUIT":
                                break
                            elif action == "UP":
                                self.selected_index = max(0, self.selected_index - 1)
                                self.selected_pane_id = snapshot.agents[self.selected_index].pane_id if snapshot.agents else None
                            elif action == "DOWN":
                                count = len(snapshot.agents)
                                if count > 0:
                                    self.selected_index = min(count - 1, self.selected_index + 1)
                                    self.selected_pane_id = snapshot.agents[self.selected_index].pane_id
                            elif action == "ENTER":
                                self.focus_selected_agent(snapshot.agents)
                            elif action == "SPACE":
                                self.scroll_pause = not self.scroll_pause
                            elif action == "REFRESH":
                                if self.current_tab == 1:
                                    self.telemetry.discovery.discover_all()
                                elif self.current_tab == 2:
                                    self.telemetry.trigger_toku_probe()
                                elif self.current_tab == 3:
                                    self.telemetry.load_context_on_demand(force=True)
                                elif self.current_tab == 4:
                                    self.scroll_pause = False
                            elif action and action.startswith("TAB_"):
                                tab_num = int(action.split("_")[1])
                                self.current_tab = tab_num
                                if self.current_tab == 3:
                                    self.telemetry.load_context_on_demand()
                        except Exception:
                            pass
                    else:
                        time.sleep(0.5)
        finally:
            if is_tty and old_settings is not None and term_mod is not None:
                try:
                    term_mod.tcsetattr(fd, term_mod.TCSADRAIN, old_settings)
                except Exception:
                    pass


# -----------------------------------------------------------------------------
# Integration Flags & CLI
# -----------------------------------------------------------------------------

def handle_integration_flags():
    parser = argparse.ArgumentParser(description="AGY Multi-Agent Live Monitor")
    parser.add_argument("--herdr", action="store_true", help="Launch via herdr split pane")
    parser.add_argument("--tmux", action="store_true", help="Launch via tmux split pane")
    args, _ = parser.parse_known_args()

    if args.herdr:
        res = subprocess.run(
            ["herdr", "pane", "split", "--direction", "right", "--cwd", os.getcwd()],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print(f"Error splitting herdr pane: {res.stderr.strip() or res.stdout.strip()}", file=sys.stderr)
            sys.exit(res.returncode or 1)

        try:
            data = json.loads(res.stdout)
            pane_id = data.get("result", {}).get("pane", {}).get("pane_id")
            if not pane_id:
                print("Error: Could not extract pane_id from herdr response", file=sys.stderr)
                sys.exit(1)
            subprocess.run(["herdr", "pane", "run", pane_id, f"{sys.executable} {shlex.quote(os.path.abspath(__file__))}"])
        except Exception as e:
            print(f"Error launching sidebar in herdr pane: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    elif args.tmux:
        res = subprocess.run(
            ["tmux", "split-window", "-h", "-c", os.getcwd(), sys.executable, os.path.abspath(__file__)]
        )
        sys.exit(res.returncode)


if __name__ == "__main__":
    handle_integration_flags()
    telemetry = TelemetryManager()
    try:
        app = SidebarApp(telemetry)
        app.run()
    finally:
        telemetry.stop()

#!/usr/bin/env python3
"""
agy-artifacts.py - Antigravity Agent Artifacts Monitor & Reviewer

Zero-daemon, pure Python 3 standard library utility to track, list, search,
follow, and interactively review artifacts generated across Antigravity (AGY) sessions.
"""

import os
import sys
import socket
import sqlite3
import json
import time
import datetime
import argparse
import pathlib
import shlex
import shutil
import subprocess
import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

try:
    import curses
except ImportError:
    curses = None  # unavailable; interactive TUI disabled

# Runtime-availability flag. All curses attribute access goes through the loose
# alias _C so type-checkers don't flag Optional-module members; _C is only
# touched behind HAS_CURSES guards.
HAS_CURSES = curses is not None
_C: Any = curses

HAS_RICH = None


# -----------------------------------------------------------------------------
# Constants & Defaults
# -----------------------------------------------------------------------------

DEFAULT_BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity-cli/brain")
DEFAULT_SUMMARIES_DB = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
DEFAULT_CONVERSATIONS_DIR = os.path.expanduser("~/.gemini/antigravity-cli/conversations")

SKIP_DIRS = {".system_generated", "__pycache__", ".pytest_cache", ".git", "node_modules", ".venv", "venv"}
SKIP_EXTENSIONS = {".tmp", ".log", ".swp", ".lock", ".bak", ".pyc", ".pyo", ".pyd"}

VALID_EXTENSIONS = {
    ".md", ".txt", ".json", ".html", ".htm", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".pdf", ".py", ".sh", ".bash", ".zsh", ".ts", ".js",
    ".tsx", ".jsx", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".xml",
    ".sql", ".rs", ".go", ".c", ".cpp", ".h", ".css", ".scss", ".diff", ".patch"
}

# Module-level session/transcript caches so watch mode (-w, 1.5s refresh) does
# not re-parse transcripts from disk every tick. Keys are paths, so distinct
# brain dirs never collide.
_SESSION_META_CACHE: Dict[str, Tuple[str, str]] = {}
_TRANSCRIPT_CACHE: Dict[str, Tuple[str, str]] = {}

DEBUG = bool(os.environ.get("AGY_ARTIFACTS_DEBUG"))
DEFAULT_LIMIT = 30


def _debug(msg: str) -> None:
    """Reports swallowed exceptions to stderr when AGY_ARTIFACTS_DEBUG is set."""
    if DEBUG:
        print(f"[agy-artifacts debug] {msg}", file=sys.stderr)


def sqlite_ro_connect(db_path: str, timeout: float = 1.0) -> sqlite3.Connection:
    """Opens a SQLite database read-only via a properly percent-encoded file URI."""
    uri = pathlib.Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout)


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class Artifact:
    index: int
    path: str
    filename: str
    cid: str
    session_title: str
    workspace: str
    mtime: float
    size: int
    heading: str = ""
    summary: str = ""
    user_facing: bool = True
    request_feedback: bool = False

    @property
    def age_human(self) -> str:
        try:
            now = time.time()
            delta = max(0, int(now - self.mtime))
        except (TypeError, ValueError, OSError):
            return "unknown"
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        if delta < 86400 * 7:
            return f"{delta // 86400}d ago"
        dt = datetime.datetime.fromtimestamp(self.mtime)
        return dt.strftime("%Y-%m-%d")

    @property
    def size_human(self) -> str:
        if self.size < 1024:
            return f"{self.size}B"
        if self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f}K"
        return f"{self.size / (1024 * 1024):.1f}M"

    @property
    def workspace_short(self) -> str:
        if not self.workspace:
            return ""
        ws = self.workspace.replace("file://", "").strip()
        if not ws:
            return ""
        norm = os.path.normpath(ws)
        base = os.path.basename(norm)
        return base if base else norm

    def to_dict(self) -> Dict[str, Any]:
        dt = datetime.datetime.fromtimestamp(self.mtime)
        return {
            "index": self.index,
            "path": self.path,
            "filename": self.filename,
            "conversation_id": self.cid,
            "session_title": self.session_title,
            "workspace": self.workspace,
            "workspace_short": self.workspace_short,
            "mtime": self.mtime,
            "mtime_iso": dt.isoformat(),
            "size": self.size,
            "size_human": self.size_human,
            "age_human": self.age_human,
            "heading": self.heading,
            "summary": self.summary,
            "user_facing": self.user_facing,
            "request_feedback": self.request_feedback,
        }


# -----------------------------------------------------------------------------
# Metadata & Content Extraction Helpers
# -----------------------------------------------------------------------------

def clean_user_prompt(text: str) -> str:
    """Strips system prompts, XML tags, and extracts user task prompt."""
    if not text:
        return ""
    # Strip <skill ...>...</skill> blocks
    text = re.sub(r"<skill[^>]*>.*?</skill>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Check for <USER_REQUEST> tags
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL | re.IGNORECASE)
    if m:
        content = m.group(1).strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if lines:
            for l in lines:
                if not l.startswith("/") or len(lines) == 1:
                    return l[:120]
            return lines[0][:120]

    # Strip generic tags
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        l_str = line.strip()
        if not l_str:
            continue
        if (
            l_str.startswith("The current local time is:")
            or l_str.startswith("The user has mentioned")
            or l_str.startswith("The user changed setting")
        ):
            continue
        lines.append(l_str)
    res = " ".join(lines).strip()
    return res[:120] if res else ""


def extract_md_heading(file_path: str) -> str:
    """Extracts top # or ## heading from markdown file."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(40):
                line = f.readline()
                if not line:
                    break
                s = line.strip()
                m = re.match(r"^#{1,3}\s+(.+)$", s)
                if m:
                    heading = m.group(1).strip()
                    # Clean markdown links or formatting
                    heading = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", heading)
                    heading = re.sub(r"[`*_~]", "", heading)
                    return heading
    except Exception:
        pass
    return ""


def load_artifact_metadata(file_path: str) -> Dict[str, Any]:
    """Loads and parses <file_path>.metadata.json if it exists."""
    meta_path = file_path + ".metadata.json"
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
            if isinstance(data, dict):
                summary = (
                    data.get("summary")
                    or data.get("Summary")
                    or data.get("description")
                    or data.get("Description")
                    or ""
                )
                user_facing = (
                    data.get("userFacing")
                    if "userFacing" in data
                    else data.get("UserFacing", True)
                )
                request_feedback = (
                    data.get("requestFeedback")
                    if "requestFeedback" in data
                    else data.get("RequestFeedback", False)
                )
                return {
                    "summary": str(summary).strip(),
                    "user_facing": bool(user_facing),
                    "request_feedback": bool(request_feedback),
                }
    except Exception:
        pass
    return {}


def load_db_sessions(db_path: str) -> Dict[str, Dict[str, Any]]:
    """Loads session metadata from SQLite conversation_summaries.db (Level 1)."""
    sessions: Dict[str, Dict[str, Any]] = {}
    if not os.path.isfile(db_path):
        return sessions
    con = None
    try:
        con = sqlite_ro_connect(db_path)
        cur = con.cursor()
        cur.execute(
            "SELECT conversation_id, title, preview, workspace_uris, last_modified_time FROM conversation_summaries"
        )
        for cid, title, preview, uris, mtime in cur.fetchall():
            ws = ""
            if uris:
                try:
                    u_list = json.loads(uris)
                    if isinstance(u_list, list) and u_list:
                        ws = str(u_list[0]).replace("file://", "").strip()
                    elif isinstance(u_list, str):
                        ws = u_list.replace("file://", "").strip()
                except Exception:
                    ws = str(uris).replace("file://", "").strip()
            t = (title or "").strip()
            if not t:
                t = (preview or "").strip()
            sessions[cid] = {
                "title": t,
                "workspace": ws,
                "last_modified_time": mtime,
            }
    except Exception as e:
        _debug(f"failed to load session db {db_path}: {e}")
    finally:
        if con:
            try:
                con.close()
            except Exception:
                pass
    return sessions


def extract_workspace_from_conversation_db(conv_db_path: str) -> str:
    """Level 2 resolver: Extracts workspace path from conversations/<cid>.db binary blob.

    Heuristic: scans raw blob bytes for file:// URIs and returns the first
    plausible path — blob layout is undocumented, so no stronger contract exists.
    """
    if not os.path.isfile(conv_db_path):
        return ""
    con = None
    try:
        con = sqlite_ro_connect(conv_db_path)
        cur = con.cursor()
        cur.execute('SELECT data FROM trajectory_metadata_blob WHERE id="main"')
        row = cur.fetchone()
        if row and row[0]:
            data = row[0]
            matches = re.findall(rb'file://([^\x00-\x1f\x7f-\xff"\'\s<>]+)', data)
            for m in matches:
                cand = m.decode("utf-8", errors="ignore").strip()
                if cand and cand != "/":
                    return cand
    except Exception:
        pass
    finally:
        if con:
            try:
                con.close()
            except Exception:
                pass
    return ""


_TOOL_CALL_PATH_KEYS = ["Cwd", "SearchDirectory", "TargetFile", "AbsolutePath"]


def _workspace_from_tool_call_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for k in _TOOL_CALL_PATH_KEYS:
        val = args.get(k)
        if val and isinstance(val, str):
            val = val.strip("\"'").replace("file://", "")
            if k in ["TargetFile", "AbsolutePath"]:
                val = os.path.dirname(val)
            if val and val != "/" and not val.startswith("/tmp"):
                return val
    return ""


def _workspace_from_record(d: Any) -> str:
    """Extracts a workspace path from one parsed transcript JSON record."""
    if not isinstance(d, dict):
        return ""
    ws = d.get("workspace")
    if ws:
        cand = str(ws).replace("file://", "").strip("\"'")
        if cand and cand != "/":
            return cand
    for tc in d.get("tool_calls") or []:
        cand = _workspace_from_tool_call_args(tc.get("args") if isinstance(tc, dict) else None)
        if cand:
            return cand
    content = d.get("content", "")
    if isinstance(content, str) and "<user_information>" in content:
        m_ui = re.search(r"(\/[^^\s\n\r\t<>\"\'\)]+)\s*->", content)
        if m_ui:
            cand = m_ui.group(1).replace("file://", "").strip()
            if cand and cand != "/":
                return cand
    return ""


def _workspace_from_raw_line(line: str, include_absolute_path: bool = True) -> str:
    """Regex fallback over a raw (possibly unparsable) transcript line."""
    keys = _TOOL_CALL_PATH_KEYS if include_absolute_path else _TOOL_CALL_PATH_KEYS[:-1]
    m_user = re.search(r"<user_information>.*?(\/[^^\s\n\r\t<>\"\'\)]+)\s*->", line, re.DOTALL)
    if m_user:
        cand = m_user.group(1).replace("file://", "").strip()
        if cand and cand != "/":
            return cand
    for key in keys:
        m = re.search(r'\"' + key + r'\"\s*:\s*\"\\?\"?([^\x00-\x1f\x7f-\xff\"\'<>\\]+)', line)
        if m:
            cand = m.group(1).replace("file://", "").strip()
            if key in ["TargetFile", "AbsolutePath"]:
                cand = os.path.dirname(cand)
            if cand and cand != "/" and not cand.startswith("/tmp"):
                return cand
    return ""


def extract_workspace_from_transcript(transcript_path: str) -> str:
    """Level 3 resolver: Extracts workspace from transcript.jsonl / transcript_full.jsonl."""
    if not os.path.isfile(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except Exception:
                    d = None
                cand = _workspace_from_record(d) or _workspace_from_raw_line(line)
                if cand:
                    return cand
    except Exception as e:
        _debug(f"transcript workspace extraction failed for {transcript_path}: {e}")
    return ""


def extract_prompt_from_transcript(transcript_path: str) -> Tuple[str, str]:
    """Fallback: extracts task title and workspace from transcript.jsonl."""
    if not os.path.isfile(transcript_path):
        return "", ""
    title = ""
    workspace = ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line.strip())
                except Exception:
                    d = None

                if not workspace:
                    workspace = _workspace_from_record(d) or _workspace_from_raw_line(
                        line, include_absolute_path=False
                    )

                if d and isinstance(d, dict) and d.get("type") == "USER_INPUT":
                    raw = d.get("content", "")
                    cand = clean_user_prompt(raw)
                    if cand:
                        if not title or title.startswith("/"):
                            title = cand
                            if not title.startswith("/") and workspace:
                                break
    except Exception as e:
        _debug(f"prompt extraction failed for {transcript_path}: {e}")
    return title, workspace


# -----------------------------------------------------------------------------
# Markdown Rendering Engine
# -----------------------------------------------------------------------------

def wrap_ansi(text: str, wrap_w: int) -> List[str]:
    """Word-wraps a string containing ANSI escape codes without counting or
    splitting the escapes themselves."""
    if not text or wrap_w <= 0:
        return [text]
    def visible_len(s: str) -> int:
        return len(re.sub(r"\033\[[0-9;]*m", "", s))
    if visible_len(text) <= wrap_w:
        return [text]
    words = text.split(" ")
    lines_list: List[str] = []
    curr: List[str] = []
    curr_len = 0
    for w in words:
        w_len = visible_len(w)
        space = 1 if curr else 0
        if curr_len + w_len + space <= wrap_w:
            curr.append(w)
            curr_len += w_len + space
        else:
            if curr:
                lines_list.append(" ".join(curr))
            curr = [w]
            curr_len = w_len
    if curr:
        lines_list.append(" ".join(curr))
    return lines_list or [text]


def _render_markdown_fallback_ansi(content: str, width: int = 80) -> str:
    """Pure Python ANSI markdown renderer fallback with word wrapping."""
    C_RESET = "\033[0m"
    C_BOLD = "\033[1m"
    C_DIM = "\033[2m"
    C_ITALIC = "\033[3m"
    C_UNDERLINE_BLUE = "\033[4;34m"
    C_CYAN = "\033[1;36m"
    C_YELLOW = "\033[1;33m"
    C_GREEN = "\033[32m"
    C_MAGENTA = "\033[1;35m"
    C_CODE = "\033[35m"
    C_CODE_BLOCK = "\033[33m"
    C_BOLD_WHITE = "\033[1;37m"
    C_BORDER = "\033[36m"

    lines = content.splitlines()
    out: List[str] = []
    in_code = False

    def format_inline(text: str) -> str:
        # Code snippets first to prevent interior token matching
        placeholders: List[str] = []

        def save_code(m: Any) -> str:
            placeholders.append(f"{C_CODE}{m.group(1)}{C_RESET}")
            return f"\x00CODE{len(placeholders)-1}\x00"

        text = re.sub(r"`([^`]+)`", save_code, text)

        # Links
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", rf"{C_UNDERLINE_BLUE}\1{C_RESET} {C_DIM}(\2){C_RESET}", text)
        # Bold
        text = re.sub(r"\*\*([^*]+)\*\*", rf"{C_BOLD_WHITE}\1{C_RESET}", text)
        text = re.sub(r"__([^_]+)__", rf"{C_BOLD_WHITE}\1{C_RESET}", text)
        # Italic
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", rf"{C_ITALIC}\1{C_RESET}", text)
        text = re.sub(r"(?<!_)_([^_]+)_(?!_)", rf"{C_ITALIC}\1{C_RESET}", text)
        # Strikethrough
        text = re.sub(r"~~([^~]+)~~", rf"{C_DIM}\1{C_RESET}", text)

        # Restore code placeholders
        for idx, rep in enumerate(placeholders):
            text = text.replace(f"\x00CODE{idx}\x00", rep)

        return text

    for line in lines:
        stripped = line.strip()

        # Code fences
        if stripped.startswith("```"):
            if not in_code:
                in_code = True
                lang = stripped.lstrip("`").strip()
                hdr = f"── {lang} " if lang else "──"
                bar_len = max(4, min(width - len(hdr) - 2, 70))
                out.append(f"{C_DIM}┌{hdr}" + "─" * bar_len + f"┐{C_RESET}")
            else:
                in_code = False
                out.append(f"{C_DIM}└" + "─" * min(width - 2, 76) + f"┘{C_RESET}")
            continue

        if in_code:
            out.append(f"{C_CODE_BLOCK}  {line}{C_RESET}")
            continue

        # Headings
        if re.match(r"^#\s+", line):
            h_text = re.sub(r"^#\s+", "", line)
            out.append(f"{C_CYAN}# {format_inline(h_text)}{C_RESET}")
        elif re.match(r"^##\s+", line):
            h_text = re.sub(r"^##\s+", "", line)
            out.append(f"{C_YELLOW}## {format_inline(h_text)}{C_RESET}")
        elif re.match(r"^###\s+", line):
            h_text = re.sub(r"^###\s+", "", line)
            out.append(f"\033[1;32m### {format_inline(h_text)}{C_RESET}")
        elif re.match(r"^####+\s+", line):
            h_text = re.sub(r"^#+\s+", "", line)
            out.append(f"{C_MAGENTA}#### {format_inline(h_text)}{C_RESET}")
        elif line.startswith(">"):
            q_text = line.lstrip("> ").strip()
            w_lines = wrap_ansi(format_inline(q_text), wrap_w=max(10, width - 4))
            for w in w_lines:
                out.append(f"{C_BORDER}│{C_RESET} {C_ITALIC}{w}{C_RESET}")
        elif re.match(r"^\s*[-*+]\s+", line):
            m = re.match(r"^(\s*)([-*+])\s+(.+)$", line)
            if m:
                indent, _, b_text = m.groups()
                w_lines = wrap_ansi(format_inline(b_text), wrap_w=max(10, width - len(indent) - 4))
                out.append(f"{indent}{C_GREEN}•{C_RESET} {w_lines[0]}")
                for w in w_lines[1:]:
                    out.append(f"{indent}  {w}")
            else:
                out.append(format_inline(line))
        elif re.match(r"^\s*\d+\.\s+", line):
            m = re.match(r"^(\s*)(\d+\.)\s+(.+)$", line)
            if m:
                indent, num, n_text = m.groups()
                w_lines = wrap_ansi(format_inline(n_text), wrap_w=max(10, width - len(indent) - len(num) - 2))
                out.append(f"{indent}{C_GREEN}{num}{C_RESET} {w_lines[0]}")
                for w in w_lines[1:]:
                    out.append(f"{indent}{' ' * len(num)} {w}")
            else:
                out.append(format_inline(line))
        elif line.startswith("|") and line.endswith("|"):
            if re.match(r"^[\|\s\-:]+$", line):
                border = re.sub(r"[\-:]+", "───", line).replace("|", "┼")
                out.append(f"{C_BORDER}{border}{C_RESET}")
            else:
                cells = line.split("|")[1:-1]
                formatted_cells = [format_inline(c) for c in cells]
                out.append(f"{C_BORDER}│{C_RESET}" + f"{C_BORDER}│{C_RESET}".join(formatted_cells) + f"{C_BORDER}│{C_RESET}")
        else:
            formatted = format_inline(line)
            w_lines = wrap_ansi(formatted, wrap_w=width)
            for w in w_lines:
                out.append(w)

    return "\n".join(out)


def render_markdown_ansi(content: str, use_color: bool = True, width: Optional[int] = None) -> str:
    """Renders markdown content into ANSI syntax-highlighted text."""
    if not content:
        return ""

    if not use_color or "NO_COLOR" in os.environ:
        return content

    term_w = width or shutil.get_terminal_size((80, 24)).columns

    global HAS_RICH
    if HAS_RICH is None:
        try:
            import rich
            import rich.console
            import rich.markdown
            import rich.theme
            HAS_RICH = True
        except ImportError:
            HAS_RICH = False

    if HAS_RICH:
        try:
            import io
            import rich.console
            import rich.markdown
            import rich.theme
            buf = io.StringIO()
            theme = rich.theme.Theme({
                "markdown.h1": "bold bright_cyan",
                "markdown.h2": "bold bright_yellow",
                "markdown.h3": "bold bright_green",
                "markdown.h4": "bold bright_magenta",
                "markdown.link": "underline bright_blue",
                "markdown.link_url": "dim bright_blue",
                "markdown.code": "bold bright_cyan on grey15",
                "markdown.block_quote": "italic bright_white",
                "markdown.item.bullet": "bold bright_green",
                "markdown.item.number": "bold bright_green",
            })
            color_sys = "truecolor" if os.environ.get("COLORTERM") in ("truecolor", "24bit") else "standard"
            console = rich.console.Console(
                file=buf,
                force_terminal=True,
                color_system=color_sys,
                theme=theme,
                width=term_w,
            )
            md = rich.markdown.Markdown(content, code_theme="monokai")
            console.print(md)
            res = buf.getvalue()
            if res:
                return res
        except Exception:
            pass

    return _render_markdown_fallback_ansi(content, width=term_w)


# -----------------------------------------------------------------------------
# Discovery Engine
# -----------------------------------------------------------------------------

def discover_artifacts(
    brain_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    workspace_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    include_hidden: bool = False,
) -> List[Artifact]:
    """Scans AGY brain directory and returns sorted and filtered Artifacts.

    Artifacts whose metadata marks them userFacing=false are skipped unless
    include_hidden is set.
    """
    brain_dir = brain_dir or os.environ.get("AGY_BRAIN_DIR", DEFAULT_BRAIN_DIR)
    db_path = db_path or os.environ.get("AGY_SUMMARIES_DB", DEFAULT_SUMMARIES_DB)
    conversations_dir = os.environ.get("AGY_CONVERSATIONS_DIR")
    if not conversations_dir:
        if db_path and os.path.dirname(db_path):
            conversations_dir = os.path.join(os.path.dirname(db_path), "conversations")
        else:
            conversations_dir = DEFAULT_CONVERSATIONS_DIR

    if not os.path.isdir(brain_dir):
        return []

    sessions = load_db_sessions(db_path)

    def get_session_meta(cid: str, cid_dir: str) -> Tuple[str, str]:
        # Module-level caches (keyed by path) survive across watch-mode ticks.
        if cid_dir in _SESSION_META_CACHE:
            return _SESSION_META_CACHE[cid_dir]
        s_info = sessions.get(cid, {})
        s_title = s_info.get("title", "")
        s_ws = s_info.get("workspace", "")

        # Level 2 resolver: conversations/<cid>.db
        if not s_ws and conversations_dir:
            conv_db_path = os.path.join(conversations_dir, f"{cid}.db")
            if os.path.isfile(conv_db_path):
                s_ws = extract_workspace_from_conversation_db(conv_db_path)

        # Level 3 resolver: transcript.jsonl
        if not s_title or not s_ws:
            t_path = os.path.join(cid_dir, ".system_generated", "logs", "transcript.jsonl")
            if not os.path.isfile(t_path):
                t_path = os.path.join(cid_dir, ".system_generated", "logs", "transcript_full.jsonl")
            if t_path not in _TRANSCRIPT_CACHE:
                _TRANSCRIPT_CACHE[t_path] = extract_prompt_from_transcript(t_path)
            t_title, t_ws = _TRANSCRIPT_CACHE[t_path]
            if not s_title and t_title:
                s_title = t_title
            if not s_ws and t_ws:
                s_ws = t_ws

        _SESSION_META_CACHE[cid_dir] = (s_title, s_ws)
        return s_title, s_ws

    # Fast file discovery: scan directories first
    raw_candidates: List[Tuple[float, str, str, str, str, int]] = []
    try:
        cids = os.listdir(brain_dir)
    except Exception:
        return []

    for cid in cids:
        cid_dir = os.path.join(brain_dir, cid)
        if not os.path.isdir(cid_dir) or cid in SKIP_DIRS:
            continue

        for root, dirs, files in os.walk(cid_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                if f.endswith(".metadata.json") or f.startswith("."):
                    continue
                _, ext = os.path.splitext(f)
                ext_lower = ext.lower()
                if ext_lower in SKIP_EXTENSIONS:
                    continue

                full_path = os.path.join(root, f)
                try:
                    st = os.stat(full_path)
                except Exception:
                    continue

                rel_inside = os.path.relpath(full_path, cid_dir)
                raw_candidates.append((st.st_mtime, full_path, rel_inside, cid, f, st.st_size))

    # Sort descending by mtime
    raw_candidates.sort(key=lambda x: x[0], reverse=True)

    # Build Artifacts lazily resolving session metadata
    artifacts: List[Artifact] = []
    for mtime, full_path, rel_inside, cid, f, size in raw_candidates:
        cid_dir = os.path.join(brain_dir, cid)
        session_title, session_ws = get_session_meta(cid, cid_dir)

        # Workspace pre-filter optimization
        if workspace_filter:
            wf = workspace_filter.lower()
            ws_short = os.path.basename(os.path.normpath(session_ws)) if session_ws else ""
            if wf not in session_ws.lower() and wf not in ws_short.lower():
                continue

        meta = load_artifact_metadata(full_path)

        # Honor userFacing=false unless explicitly included.
        if meta.get("user_facing", True) is not True and not include_hidden:
            continue

        heading = ""
        _, ext = os.path.splitext(f)
        if ext.lower() == ".md":
            heading = extract_md_heading(full_path)

        art = Artifact(
            index=0,
            path=full_path,
            filename=rel_inside,
            cid=cid,
            session_title=session_title,
            workspace=session_ws,
            mtime=mtime,
            size=size,
            heading=heading,
            summary=meta.get("summary", ""),
            user_facing=meta.get("user_facing", True),
            request_feedback=meta.get("request_feedback", False),
        )

        # Search query filter
        if search_query:
            sq = search_query.lower()
            if not (
                sq in art.filename.lower()
                or sq in art.heading.lower()
                or sq in art.summary.lower()
                or sq in art.session_title.lower()
                or sq in art.workspace.lower()
                or sq in art.path.lower()
            ):
                continue

        artifacts.append(art)
        if limit and limit > 0 and len(artifacts) >= limit and not search_query and not workspace_filter:
            break

    # Assign 1-based indices
    for idx, a in enumerate(artifacts, 1):
        a.index = idx

    return artifacts


# -----------------------------------------------------------------------------
# Terminal & Table Formatting
# -----------------------------------------------------------------------------

def print_artifacts_table(artifacts: List[Artifact], use_color: bool = True) -> None:
    """Renders formatted ANSI table of artifacts."""
    if not artifacts:
        print("No artifacts found.")
        return

    term_width = shutil.get_terminal_size((100, 24)).columns
    no_color = "NO_COLOR" in os.environ or not use_color

    C_RESET = "" if no_color else "\033[0m"
    C_BOLD = "" if no_color else "\033[1m"
    C_DIM = "" if no_color else "\033[2m"
    C_CYAN = "" if no_color else "\033[36m"
    C_YELLOW = "" if no_color else "\033[33m"
    C_GREEN = "" if no_color else "\033[32m"
    C_MAGENTA = "" if no_color else "\033[35m"

    print(
        f"{C_BOLD}{' #':>3}  {'AGE':<7}  {'WORKSPACE':<16}  {'FEEDBACK':<10}  {'ARTIFACT':<24}  {'HEADING / SUMMARY'}{C_RESET}"
    )
    print(f"{C_DIM}{'─' * min(term_width, 110)}{C_RESET}")

    for a in artifacts:
        idx_str = f"{a.index:>3}"
        age_str = f"{a.age_human:<7}"
        ws = (a.workspace_short or "—")[:16].ljust(16)
        fb = f"{C_YELLOW}[FEEDBACK]{C_RESET}" if a.request_feedback else "          "
        fn = a.filename[:24].ljust(24)
        desc = a.heading or a.summary or a.session_title or ""
        desc = desc.replace("\n", " ").strip()

        fn_display = f"{C_CYAN}{fn}{C_RESET}"
        print(
            f"{C_GREEN}{idx_str}{C_RESET}  {C_DIM}{age_str}{C_RESET}  {C_MAGENTA}{ws}{C_RESET}  {fb}  {fn_display}  {desc}"
        )

    print(f"{C_DIM}{'─' * min(term_width, 110)}{C_RESET}")
    print(
        f"{C_DIM}To view: agy-artifacts <N>  ·  To edit: agy-artifacts -e <N>  ·  To tail: agy-artifacts -f{C_RESET}"
    )


# -----------------------------------------------------------------------------
# Opener, Cat & Pager Logic
# -----------------------------------------------------------------------------

def cat_artifact(path: str) -> int:
    """Prints artifact content to stdout, syntax-colored when TTY."""
    if not os.path.isfile(path):
        print(f"Error: Artifact file not found: {path}", file=sys.stderr)
        return 1
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        _, ext = os.path.splitext(path)
        if ext.lower() == ".md" and use_color:
            rendered = render_markdown_ansi(content, use_color=True)
            sys.stdout.write(rendered if rendered.endswith("\n") else rendered + "\n")
        else:
            sys.stdout.write(content)
        sys.stdout.flush()
        return 0
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return 1


def open_artifact(path: str, edit: bool = False) -> int:
    """Opens artifact in editor or preferred viewer/pager with syntax coloring and text wrapping."""
    if not os.path.isfile(path):
        print(f"Error: Artifact file not found: {path}", file=sys.stderr)
        return 1

    if edit:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if not editor:
            for ed in ["editor", "nano", "nvim", "vim", "vi"]:
                if shutil.which(ed):
                    editor = ed
                    break
            editor = editor or "vi"
        return subprocess.call([editor, path])

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading file {path}: {e}", file=sys.stderr)
        return 1

    _, ext = os.path.splitext(path)
    use_color = "NO_COLOR" not in os.environ
    term_w = shutil.get_terminal_size((80, 24)).columns
    if ext.lower() in (".md", ".markdown") or not ext:
        rendered = render_markdown_ansi(content, use_color=use_color, width=term_w)
    else:
        rendered = content

    # 1. Preferred Pager: less -R -i with UTF-8 support
    pager_cmd = None
    if "VIEWER" in os.environ and os.environ["VIEWER"]:
        pager_cmd = shlex.split(os.environ["VIEWER"])
    elif "PAGER" in os.environ and os.environ["PAGER"]:
        p_raw = os.environ["PAGER"].strip()
        if "less" in p_raw and "-R" not in p_raw:
            pager_cmd = shlex.split(p_raw) + ["-R", "-i"]
        else:
            pager_cmd = shlex.split(p_raw)
    elif shutil.which("less"):
        pager_cmd = ["less", "-R", "-i"]

    if pager_cmd:
        try:
            env = dict(os.environ)
            env.setdefault("LESSCHARSET", "utf-8")
            p = subprocess.Popen(pager_cmd, stdin=subprocess.PIPE, env=env)
            p.communicate(input=rendered.encode("utf-8", errors="ignore"))
            return p.returncode or 0
        except Exception:
            pass

    # Fallback built-in paginator with word-wrapping
    term_width, term_height = shutil.get_terminal_size((80, 24))
    raw_lines = rendered.splitlines()
    wrapped_lines: List[str] = []
    for rl in raw_lines:
        w_sub = wrap_ansi(rl, term_width)
        wrapped_lines.extend(w_sub if w_sub else [rl])

    if len(wrapped_lines) <= term_height - 2 or not sys.stdout.isatty():
        for line in wrapped_lines:
            sys.stdout.write(line + "\n")
        sys.stdout.flush()
        return 0

    page_size = max(10, term_height - 3)
    i = 0
    while i < len(wrapped_lines):
        for l in wrapped_lines[i : i + page_size]:
            sys.stdout.write(l + "\n")
        i += page_size
        if i < len(wrapped_lines):
            sys.stdout.write(f"\033[7m-- More ({i}/{len(wrapped_lines)}) [Space: next, q: quit] --\033[0m")
            sys.stdout.flush()
            try:
                ch = sys.stdin.read(1)
                sys.stdout.write("\r\033[K")
                if ch.lower() == "q":
                    break
            except Exception:
                break
    return 0


def herdr_socket_request(sock_path: str, payload: Dict[str, Any], timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    """Sends one JSON request over a Unix socket and reads until the buffer
    parses as a JSON object, the peer closes, or the byte cap is hit — tolerant
    of partial frames and responses that omit the trailing newline."""
    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(sock_path)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = bytearray()
        while len(buf) <= 1024 * 1024:
            if buf:
                try:
                    obj = json.loads(bytes(buf).decode("utf-8", errors="ignore").strip())
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
        try:
            obj = json.loads(bytes(buf).decode("utf-8", errors="ignore").strip())
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    except Exception as e:
        _debug(f"herdr socket request failed ({payload.get('method')}): {e}")
        return None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def _pane_id_from_response(resp: Any) -> Optional[str]:
    if not isinstance(resp, dict):
        return None
    res_obj = resp.get("result", {})
    if isinstance(res_obj, dict):
        root_pane = res_obj.get("root_pane", {})
        if isinstance(root_pane, dict) and root_pane.get("pane_id"):
            return root_pane.get("pane_id")
        if res_obj.get("pane_id"):
            return res_obj.get("pane_id")
    return resp.get("pane_id") or None


def open_artifact_in_herdr_space(artifact: Artifact, edit: bool = False) -> bool:
    """Opens artifact in a new Herdr workspace/space with viewer or editor."""
    if not artifact or not getattr(artifact, "path", None):
        return False

    label = f"art:{artifact.filename}"
    cwd = artifact.workspace or os.path.dirname(artifact.path) or os.getcwd()
    pane_id = None
    sock_path = os.path.expanduser("~/.config/herdr/herdr.sock")

    quoted_path = shlex.quote(artifact.path)

    # 1. Try socket for workspace creation
    if os.path.exists(sock_path):
        resp = herdr_socket_request(
            sock_path,
            {
                "id": "art:ws_create",
                "method": "workspace.create",
                "params": {"label": label, "cwd": cwd, "focus": True},
            },
        )
        if resp and isinstance(resp, dict) and "result" in resp and "error" not in resp:
            pane_id = _pane_id_from_response(resp)

    # 2. Fallback to CLI for workspace creation
    if not pane_id:
        try:
            res = subprocess.run(
                ["herdr", "workspace", "create", "--label", label, "--cwd", cwd, "--focus"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0 and res.stdout:
                pane_id = _pane_id_from_response(json.loads(res.stdout.strip()))
        except Exception as e:
            _debug(f"herdr workspace create failed: {e}")
            pane_id = None

    if not pane_id:
        return False

    # 3. Determine command
    if edit:
        cmd = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if not cmd:
            for ed in ["editor", "nano", "nvim", "vim", "vi"]:
                if shutil.which(ed):
                    cmd = ed
                    break
            cmd = cmd or "vi"
        full_cmd = f"{cmd} {quoted_path}"
    else:
        if shutil.which("glow"):
            full_cmd = f"glow -p {quoted_path}"
        elif shutil.which("bat"):
            full_cmd = f"bat --style=plain {quoted_path}"
        else:
            script_bin = "art" if shutil.which("art") else f"python3 {os.path.abspath(__file__)}"
            full_cmd = f"{script_bin} --view {quoted_path}"

    # 4. Execute command in pane (socket first, CLI fallback); path is
    # shlex-quoted so filenames containing quotes/spaces cannot break the command.
    if os.path.exists(sock_path):
        resp = herdr_socket_request(sock_path, {
            "id": "art:pane_send",
            "method": "pane.send_text",
            "params": {"pane_id": pane_id, "text": f"{full_cmd}\n"},
        })
        if resp and isinstance(resp, dict) and "result" in resp and "error" not in resp:
            return True

    try:
        res = subprocess.run(
            ["herdr", "pane", "send-text", pane_id, f"{full_cmd}\n"],
            capture_output=True,
            text=True,
            timeout=2.0
        )
        if res.returncode == 0:
            return True
        res2 = subprocess.run(
            ["herdr", "pane", "run", pane_id, full_cmd],
            capture_output=True,
            text=True,
            timeout=2.0
        )
        return res2.returncode == 0
    except Exception as e:
        _debug(f"herdr pane send-text failed: {e}")
        return False


# -----------------------------------------------------------------------------
# Follow / Tail & Watch Modes
# -----------------------------------------------------------------------------

def tail_artifact(
    artifact: Artifact,
    workspace_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    include_hidden: bool = False,
) -> None:
    """Streams an artifact live; reopens on truncation and switches to a newer
    matching artifact if one appears."""
    current = artifact
    pos = 0
    last_switch_check = 0.0
    print(f"\033[1;36m==> Following artifact: {current.filename} ({current.path}) <==\033[0m\n")
    sys.stdout.flush()
    try:
        while True:
            try:
                size = os.path.getsize(current.path)
            except OSError:
                size = pos
            if size < pos:
                print("\033[1;33m==> file truncated, reopening <==\033[0m")
                pos = 0
            if size > pos:
                try:
                    with open(current.path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(pos)
                        for line in f:
                            sys.stdout.write(line)
                        pos = f.tell()
                    sys.stdout.flush()
                except OSError as e:
                    _debug(f"tail read failed: {e}")
            # Periodically check whether a newer artifact took over.
            now = time.time()
            if now - last_switch_check > 5.0:
                last_switch_check = now
                try:
                    newest = discover_artifacts(
                        workspace_filter=workspace_filter,
                        search_query=search_query,
                        limit=1,
                        include_hidden=include_hidden,
                    )
                except Exception as e:
                    _debug(f"tail switch-check failed: {e}")
                    newest = []
                if newest and newest[0].path != current.path:
                    current = newest[0]
                    pos = 0
                    print(f"\n\033[1;36m==> Now following: {current.filename} ({current.path}) <==\033[0m\n")
            time.sleep(0.3)
    except KeyboardInterrupt:
        sys.exit(0)


def watch_artifacts(
    workspace_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    include_hidden: bool = False,
) -> None:
    """Live watch loop showing newly created or modified artifacts."""
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")
            artifacts = discover_artifacts(
                workspace_filter=workspace_filter,
                search_query=search_query,
                limit=limit,
                include_hidden=include_hidden,
            )
            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            hdr = f"\033[1;36m[agy-artifacts watch]\033[0m · \033[1m{now_str}\033[0m · \033[33m{len(artifacts)} artifacts\033[0m"
            if workspace_filter:
                hdr += f" (workspace: {workspace_filter})"
            if search_query:
                hdr += f" (search: {search_query})"
            print(hdr)
            term_w = shutil.get_terminal_size((100, 24)).columns
            print("\033[2m" + "─" * min(term_w, 110) + "\033[0m")
            if artifacts:
                print_artifacts_table(artifacts, use_color=True)
            else:
                print("No artifacts found.")
            sys.stdout.flush()
            time.sleep(1.5)
    except KeyboardInterrupt:
        sys.exit(0)


# -----------------------------------------------------------------------------
# Curses Interactive TUI
# -----------------------------------------------------------------------------

def _color_pair(n: int, has_colors: bool) -> int:
    if not has_colors or not HAS_CURSES:
        return 0
    try:
        return _C.color_pair(n)
    except Exception:
        return 0


def _safe_addstr(
    stdscr: Any,
    y: int,
    x: int,
    text: str,
    max_w: Optional[int] = None,
    attr: Optional[int] = None,
) -> None:
    """Safely adds a string to curses stdscr, handling terminal bounds and suppressing ERR on bottom-right cell."""
    try:
        max_y, max_x = stdscr.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        avail = max_x - x
        if max_w is not None:
            avail = min(avail, max_w)
        if avail <= 0:
            return
        text_to_draw = text[:avail]
        if not text_to_draw:
            return
        if attr is not None:
            stdscr.addnstr(y, x, text_to_draw, avail, attr)
        else:
            stdscr.addnstr(y, x, text_to_draw, avail)
    except Exception:
        # Curses raises ERR when writing to lower-right corner cell (max_y - 1, max_x - 1)
        # Even though characters are drawn, cursor wrap triggers ERR. We safely swallow it.
        pass


def _render_curses_md_line(stdscr: Any, y: int, x: int, line: str, max_w: int, has_colors: bool) -> None:
    """Renders a single line of markdown with curses syntax coloring."""
    if y < 0 or max_w <= 0:
        return
    stripped = line.strip()
    if stripped.startswith("#"):
        attr = _C.A_BOLD | _color_pair(1, has_colors)
        _safe_addstr(stdscr, y, x, line.ljust(max_w), max_w, attr)
    elif stripped.startswith(("•", "-", "*", "+")):
        cp = _color_pair(4, has_colors)
        if cp:
            _safe_addstr(stdscr, y, x, line[:2], min(2, max_w), cp)
            if max_w > 2:
                _safe_addstr(stdscr, y, x + 2, line[2:], max_w - 2)
        else:
            _safe_addstr(stdscr, y, x, line, max_w)
    elif stripped.startswith("```") or stripped.startswith("`"):
        attr = _color_pair(5, has_colors) if has_colors else _C.A_NORMAL
        _safe_addstr(stdscr, y, x, line.ljust(max_w), max_w, attr)
    elif stripped.startswith(">"):
        attr = _color_pair(1, has_colors) if has_colors else _C.A_DIM
        _safe_addstr(stdscr, y, x, line.ljust(max_w), max_w, attr)
    elif "**" in line:
        attr = _C.A_BOLD | _color_pair(6, has_colors)
        _safe_addstr(stdscr, y, x, line.ljust(max_w), max_w, attr)
    else:
        _safe_addstr(stdscr, y, x, line.ljust(max_w), max_w)


def _decode_curses_key(stdscr: Any, ch: int) -> str:
    """Decodes curses getch code including raw escape sequences and keypad modes."""
    if ch in (_C.KEY_UP, ord("k"), ord("K")):
        return "UP"
    if ch in (_C.KEY_DOWN, ord("j"), ord("J")):
        return "DOWN"
    if ch in (_C.KEY_LEFT, ord("h"), ord("H")):
        return "LEFT"
    if ch in (_C.KEY_RIGHT, ord("l"), ord("L")):
        return "RIGHT"
    if ch in (_C.KEY_HOME, ord("g")):
        return "HOME"
    if ch in (_C.KEY_END, ord("G")):
        return "END"
    if ch in (_C.KEY_NPAGE,):
        return "PGDN"
    if ch in (_C.KEY_PPAGE,):
        return "PGUP"
    if ch in (10, 13):
        return "ENTER"
    if ch in (ord("q"), ord("Q")):
        return "QUIT"
    if ch == 27:
        try:
            stdscr.nodelay(True)
            c2 = stdscr.getch()
            if c2 in (ord("["), ord("O")):
                c3 = stdscr.getch()
                if c3 == ord("A"):
                    return "UP"
                elif c3 == ord("B"):
                    return "DOWN"
                elif c3 == ord("C"):
                    return "RIGHT"
                elif c3 == ord("D"):
                    return "LEFT"
                elif c3 == ord("H"):
                    return "HOME"
                elif c3 == ord("F"):
                    return "END"
                elif c3 == ord("5"):
                    _ = stdscr.getch()  # consume ~
                    return "PGUP"
                elif c3 == ord("6"):
                    _ = stdscr.getch()  # consume ~
                    return "PGDN"
                elif c3 == ord("1"):
                    _ = stdscr.getch()  # ;
                    _ = stdscr.getch()  # 5
                    c_end = stdscr.getch()
                    if c_end == ord("A"):
                        return "UP"
                    elif c_end == ord("B"):
                        return "DOWN"
            elif c2 == -1:
                return "ESC"
        except Exception:
            return "ESC"
        finally:
            try:
                stdscr.nodelay(False)
                stdscr.timeout(100)
            except Exception:
                pass
        return "ESC"
    return chr(ch) if (0 <= ch <= 255) else ""


def _curses_full_view(stdscr: Any, artifact: Artifact, has_colors: bool) -> None:
    """Scrollable curses full-view pane for an artifact with word-wrapping and markdown highlighting."""
    try:
        stdscr.keypad(True)
    except Exception:
        pass
    try:
        if HAS_CURSES and hasattr(_C, "set_escdelay"):
            _C.set_escdelay(25)
    except Exception:
        pass
    try:
        stdscr.timeout(100)
    except Exception:
        pass

    try:
        with open(artifact.path, "r", encoding="utf-8", errors="ignore") as f:
            raw_content = f.read()
    except Exception as e:
        raw_content = f"Error reading file: {e}"

    scroll = 0
    last_w = -1
    wrapped_lines: List[str] = []
    status_msg: Optional[str] = None

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        if max_y < 5 or max_x < 20:
            _safe_addstr(stdscr, 0, 0, "Window too small", max_x)
            stdscr.refresh()
            time.sleep(0.1)
            continue

        wrap_width = max(10, max_x - 2)
        if wrap_width != last_w:
            last_w = wrap_width
            wrapped_lines = []
            for raw_line in raw_content.splitlines():
                if not raw_line:
                    wrapped_lines.append("")
                elif raw_line.startswith(("```", "|", "  ")):
                    wrapped_lines.append(raw_line)
                elif len(raw_line) > wrap_width:
                    w_sub = textwrap.wrap(raw_line, width=wrap_width)
                    wrapped_lines.extend(w_sub if w_sub else [raw_line])
                else:
                    wrapped_lines.append(raw_line)

        title = f" {artifact.filename} ({artifact.size_human}, {artifact.age_human}) - [↑/↓/PgUp/PgDn] Scroll  [q/Esc] Back "
        attr_hdr = _C.A_BOLD | _color_pair(1, has_colors)
        _safe_addstr(stdscr, 0, 0, title.ljust(max_x), max_x, attr_hdr)

        visible_rows = max_y - 2
        for i in range(visible_rows):
            line_idx = scroll + i
            row_y = 1 + i
            if line_idx < len(wrapped_lines):
                _render_curses_md_line(stdscr, row_y, 0, wrapped_lines[line_idx], max_x, has_colors)

        pos_str = f" {min(scroll + visible_rows, len(wrapped_lines))}/{len(wrapped_lines)} lines "
        if status_msg:
            attr_st = _C.A_BOLD | _color_pair(4, has_colors)
            _safe_addstr(stdscr, max_y - 1, 0, f" {status_msg} ".ljust(max_x), max_x, attr_st)
        else:
            footer_str = " [s: New Space | W: Space Editor] "
            _safe_addstr(stdscr, max_y - 1, 0, footer_str, max_x, _C.A_DIM)
            _safe_addstr(stdscr, max_y - 1, max(0, max_x - len(pos_str) - 1), pos_str, len(pos_str), _C.A_DIM)

        stdscr.refresh()

        try:
            ch = stdscr.getch()
        except Exception:
            ch = -1

        if ch == -1:
            continue

        action = _decode_curses_key(stdscr, ch)

        if action in ("QUIT", "ESC"):
            break
        elif action == "DOWN":
            if scroll + visible_rows < len(wrapped_lines):
                scroll += 1
        elif action == "UP":
            if scroll > 0:
                scroll -= 1
        elif action in ("PGDN", " ") or ch in (ord("f"),):
            scroll = min(max(0, len(wrapped_lines) - visible_rows), scroll + visible_rows)
        elif action == "PGUP" or ch in (ord("b"),):
            scroll = max(0, scroll - visible_rows)
        elif action == "HOME":
            scroll = 0
        elif action == "END":
            scroll = max(0, len(wrapped_lines) - visible_rows)
        elif ch in (ord("s"), ord("S")):
            ok = open_artifact_in_herdr_space(artifact, edit=False)
            status_msg = "[OK] Opened in new Herdr space (viewer)" if ok else "[ERR] Failed to open in Herdr space"
        elif ch == ord("W"):
            ok = open_artifact_in_herdr_space(artifact, edit=True)
            status_msg = "[OK] Opened in new Herdr space (editor)" if ok else "[ERR] Failed to open in Herdr space"


def _curses_tui_main(stdscr: Any, artifacts: List[Artifact]) -> Tuple[str, Optional[str]]:
    try:
        _C.curs_set(0)
    except Exception:
        pass
    try:
        stdscr.keypad(True)
    except Exception:
        pass
    try:
        if HAS_CURSES and hasattr(_C, "set_escdelay"):
            _C.set_escdelay(25)
    except Exception:
        pass
    try:
        stdscr.timeout(100)
    except Exception:
        pass
    try:
        _C.use_default_colors()
    except Exception:
        pass

    has_colors = False
    try:
        has_colors = _C.has_colors()
        if has_colors:
            _C.init_pair(1, _C.COLOR_CYAN, -1)
            _C.init_pair(2, _C.COLOR_BLACK, _C.COLOR_CYAN)
            _C.init_pair(3, _C.COLOR_YELLOW, -1)
            _C.init_pair(4, _C.COLOR_GREEN, -1)
            _C.init_pair(5, _C.COLOR_MAGENTA, -1)
            _C.init_pair(6, _C.COLOR_WHITE, -1)
            _C.init_pair(7, _C.COLOR_BLUE, -1)
    except Exception:
        has_colors = False

    selected_idx = 0
    scroll_offset = 0
    search_mode = False
    search_text = ""
    status_msg: Optional[str] = None

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        if max_y < 8 or max_x < 40:
            _safe_addstr(stdscr, 0, 0, "Terminal too small.", max_x)
            stdscr.refresh()
            time.sleep(0.1)
            continue

        if search_text:
            st_lower = search_text.lower()
            filtered = [
                a for a in artifacts
                if st_lower in a.filename.lower()
                or st_lower in a.heading.lower()
                or st_lower in a.summary.lower()
                or st_lower in a.session_title.lower()
                or st_lower in a.workspace.lower()
                or st_lower in a.workspace_short.lower()
            ]
        else:
            filtered = list(artifacts)

        if not filtered:
            selected_idx = 0
        else:
            if selected_idx >= len(filtered):
                selected_idx = max(0, len(filtered) - 1)

        # Row 0: Title bar
        title_left = "  Antigravity Artifacts (agy-artifacts)"
        count_str = f"[{len(filtered)}/{len(artifacts)} items]  "
        attr_t = _C.A_BOLD | _color_pair(1, has_colors)
        _safe_addstr(stdscr, 0, 0, title_left.ljust(max_x), max_x, attr_t)
        if len(count_str) < max_x:
            _safe_addstr(stdscr, 0, max_x - len(count_str), count_str, len(count_str), attr_t)

        # Row 1: Keybindings bar
        help_bar = "  [↑/↓/j/k] Navigate  [v] View  [Enter/o] Open  [e] Edit  [s: New Space | W: Space Editor]  [c] Cat  [/] Search  [q] Quit"
        _safe_addstr(stdscr, 1, 0, help_bar.ljust(max_x), max_x, _C.A_DIM)

        # Row 2: Separator
        _safe_addstr(stdscr, 2, 0, "─" * max_x, max_x, _C.A_DIM)

        # Row 3: Column headers
        col_hdr = "   #   AGE     WORKSPACE        FEEDBACK   ARTIFACT               HEADING / SUMMARY"
        _safe_addstr(stdscr, 3, 0, col_hdr.ljust(max_x), max_x, _C.A_BOLD)

        # Geometry calculations
        preview_height = min(6, max(3, max_y // 4))
        list_start_y = 4
        list_max_rows = max(1, max_y - list_start_y - preview_height - 2)

        if selected_idx < scroll_offset:
            scroll_offset = selected_idx
        elif selected_idx >= scroll_offset + list_max_rows:
            scroll_offset = selected_idx - list_max_rows + 1

        for i in range(list_max_rows):
            item_idx = scroll_offset + i
            row_y = list_start_y + i
            if row_y >= max_y - preview_height - 1:
                break
            if item_idx < len(filtered):
                art = filtered[item_idx]
                is_selected = item_idx == selected_idx

                idx_str = f"{art.index:>3}"
                age_str = f"{art.age_human:>7}"
                ws_str = (art.workspace_short or "—")[:15].ljust(15)
                fb_str = "[FEEDBACK]" if art.request_feedback else "          "
                fn_str = art.filename[:22].ljust(22)
                desc = art.heading or art.summary or art.session_title or ""
                desc_str = desc.replace("\n", " ")

                prefix = "▶ " if is_selected else "  "
                line_str = f"{prefix}{idx_str}  {age_str}  {ws_str}  {fb_str} {fn_str} {desc_str}"

                if is_selected:
                    attr = _C.A_BOLD | _color_pair(2, has_colors)
                    _safe_addstr(stdscr, row_y, 0, line_str.ljust(max_x), max_x, attr)
                else:
                    _safe_addstr(stdscr, row_y, 0, line_str[:max_x], max_x)

        # Preview pane separator
        prev_sep_y = max_y - preview_height - 1
        if prev_sep_y > list_start_y:
            _safe_addstr(stdscr, prev_sep_y, 0, "─" * max_x, max_x, _C.A_DIM)

        # Preview pane content
        if filtered and 0 <= selected_idx < len(filtered):
            sel_art = filtered[selected_idx]
            p_y = prev_sep_y + 1
            if p_y < max_y - 1:
                p_hdr = f" Artifact: {sel_art.filename} ({sel_art.size_human}, {sel_art.age_human})"
                _safe_addstr(stdscr, p_y, 0, p_hdr.ljust(max_x), max_x, _C.A_BOLD)

            if p_y + 1 < max_y - 1:
                h_line = f" Heading:  {sel_art.heading}" if sel_art.heading else f" Path: {sel_art.path}"
                h_wrapped = textwrap.wrap(h_line, width=max(10, max_x - 4))
                attr_h = _C.A_BOLD | _color_pair(1, has_colors)
                _safe_addstr(stdscr, p_y + 1, 2, h_wrapped[0] if h_wrapped else h_line, max_x - 4, attr_h)

            if p_y + 2 < max_y - 1:
                sum_line = f" Summary:  {sel_art.summary}" if sel_art.summary else f" Session: {sel_art.session_title}"
                sum_wrapped = textwrap.wrap(sum_line, width=max(10, max_x - 4))
                attr_s = _color_pair(3, has_colors) if has_colors else _C.A_NORMAL
                _safe_addstr(stdscr, p_y + 2, 2, sum_wrapped[0] if sum_wrapped else sum_line, max_x - 4, attr_s)

            if p_y + 3 < max_y - 1 and sel_art.session_title:
                sess_line = f" Prompt:   {sel_art.session_title}"
                sess_wrapped = textwrap.wrap(sess_line, width=max(10, max_x - 4))
                _safe_addstr(stdscr, p_y + 3, 2, sess_wrapped[0] if sess_wrapped else sess_line, max_x - 4, _C.A_DIM)

        # Search bar or Status / Footer bar
        if search_mode:
            search_prompt = f" Search: {search_text}█"
            attr_s = _C.A_BOLD | _color_pair(3, has_colors)
            _safe_addstr(stdscr, max_y - 1, 0, search_prompt.ljust(max_x), max_x, attr_s)
        elif status_msg:
            attr_st = _C.A_BOLD | _color_pair(4, has_colors)
            _safe_addstr(stdscr, max_y - 1, 0, f"  {status_msg}".ljust(max_x), max_x, attr_st)
        else:
            footer_str = "  s: New Space | W: Space Editor"
            _safe_addstr(stdscr, max_y - 1, 0, footer_str.ljust(max_x), max_x, _C.A_DIM)

        stdscr.refresh()

        try:
            ch = stdscr.getch()
        except Exception:
            ch = -1

        if ch == -1:
            continue

        action = _decode_curses_key(stdscr, ch)

        if search_mode:
            # Raw key handling: printable input bypasses vi-style decoding so
            # letters like j/k/h/l/g can actually be typed into the query.
            if ch in (10, 13):  # Enter confirms
                search_mode = False
            elif ch == 27:  # Esc clears and exits
                search_mode = False
                search_text = ""
            elif ch in (_C.KEY_BACKSPACE, 127, 8):
                search_text = search_text[:-1]
                selected_idx = 0
            elif 32 <= ch <= 126:
                search_text += chr(ch)
                selected_idx = 0
            continue

        # Keybindings
        if action in ("QUIT", "ESC"):
            return "quit", None
        elif action == "DOWN":
            if filtered:
                selected_idx = min(len(filtered) - 1, selected_idx + 1)
        elif action == "UP":
            if filtered:
                selected_idx = max(0, selected_idx - 1)
        elif action == "HOME":
            selected_idx = 0
        elif action == "END":
            if filtered:
                selected_idx = len(filtered) - 1
        elif action == "PGDN":
            if filtered:
                selected_idx = min(len(filtered) - 1, selected_idx + list_max_rows)
        elif action == "PGUP":
            if filtered:
                selected_idx = max(0, selected_idx - list_max_rows)
        elif ch in (ord("v"), ord("V")):
            if filtered and 0 <= selected_idx < len(filtered):
                _curses_full_view(stdscr, filtered[selected_idx], has_colors)
        elif action == "ENTER" or ch in (ord("o"), ord("O")):
            if filtered and 0 <= selected_idx < len(filtered):
                return "open", filtered[selected_idx].path
        elif ch in (ord("e"), ord("E")):
            if filtered and 0 <= selected_idx < len(filtered):
                return "edit", filtered[selected_idx].path
        elif ch in (ord("c"), ord("C")):
            if filtered and 0 <= selected_idx < len(filtered):
                return "cat", filtered[selected_idx].path
        elif ch in (ord("s"), ord("S")):
            if filtered and 0 <= selected_idx < len(filtered):
                ok = open_artifact_in_herdr_space(filtered[selected_idx], edit=False)
                status_msg = "[OK] Opened in new Herdr space (viewer)" if ok else "[ERR] Failed to open in Herdr space"
        elif ch == ord("W"):
            if filtered and 0 <= selected_idx < len(filtered):
                ok = open_artifact_in_herdr_space(filtered[selected_idx], edit=True)
                status_msg = "[OK] Opened in new Herdr space (editor)" if ok else "[ERR] Failed to open in Herdr space"
        elif ch == ord("/"):
            search_mode = True
        elif ord("1") <= ch <= ord("9"):
            target_num = ch - ord("0")
            for idx, a in enumerate(filtered):
                if a.index == target_num:
                    selected_idx = idx
                    break


def _raw_tty_interactive_menu(artifacts: List[Artifact]) -> Tuple[str, Optional[str]]:
    """Pure stdlib ANSI arrow-key menu for non-curses interactive terminals."""
    if not artifacts:
        return "quit", None

    try:
        import termios
        import tty
        import select
    except ImportError:
        return "quit", None

    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return "quit", None

    try:
        old_settings = termios.tcgetattr(fd)
    except Exception:
        return "quit", None

    selected = 0

    def render() -> None:
        term_w, term_h = shutil.get_terminal_size((80, 24))
        out = ["\033[H\033[2J"]
        out.append(f"\033[1;36m  Antigravity Artifacts (agy-artifacts)\033[0m  \033[2m[{selected + 1}/{len(artifacts)} selected]\033[0m")
        out.append(f"\033[2m  [↑/↓/j/k] Navigate  [Enter/o] Open  [e] Edit  [s: Herdr Space]  [c] Cat  [q] Quit\033[0m")
        out.append(f"\033[2m{'─' * min(term_w, 80)}\033[0m")
        out.append(f"\033[1m{' #':>3}  {'AGE':<7}  {'WORKSPACE':<15}  {'FEEDBACK':<10}  {'ARTIFACT':<22}  {'HEADING / SUMMARY'}\033[0m")

        max_display = min(len(artifacts), max(1, term_h - 6))
        offset = 0
        if selected >= max_display:
            offset = selected - max_display + 1

        for i in range(max_display):
            idx = offset + i
            if idx >= len(artifacts):
                break
            a = artifacts[idx]
            is_sel = idx == selected
            prefix = "▶ " if is_sel else "  "
            idx_s = f"{a.index:>3}"
            age_s = f"{a.age_human:>7}"
            ws_s = (a.workspace_short or "—")[:15].ljust(15)
            fb_s = "\033[33m[FEEDBACK]\033[0m" if a.request_feedback else "          "
            fn_s = a.filename[:22].ljust(22)
            desc = (a.heading or a.summary or a.session_title or "").replace("\n", " ")

            row = f"{prefix}{idx_s}  {age_s}  {ws_s}  {fb_s} {fn_s} {desc}"
            if is_sel:
                out.append(f"\033[1;7;36m{row[:term_w]}\033[0m")
            else:
                out.append(row[:term_w])

        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?25l")  # Hide cursor
        sys.stdout.flush()
        while True:
            render()
            ch = sys.stdin.read(1)
            if not ch:
                break
            if ch == "\x1b":
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    c2 = sys.stdin.read(1)
                    if c2 in ("[", "O"):
                        c3 = sys.stdin.read(1)
                        if c3 == "A":  # UP
                            selected = max(0, selected - 1)
                        elif c3 == "B":  # DOWN
                            selected = min(len(artifacts) - 1, selected + 1)
                        elif c3 == "H":  # HOME
                            selected = 0
                        elif c3 == "F":  # END
                            selected = len(artifacts) - 1
                else:
                    return "quit", None
            elif ch in ("k", "K"):
                selected = max(0, selected - 1)
            elif ch in ("j", "J"):
                selected = min(len(artifacts) - 1, selected + 1)
            elif ch in ("\r", "\n", "o", "O"):
                if 0 <= selected < len(artifacts):
                    return "open", artifacts[selected].path
            elif ch in ("e", "E"):
                if 0 <= selected < len(artifacts):
                    return "edit", artifacts[selected].path
            elif ch in ("c", "C"):
                if 0 <= selected < len(artifacts):
                    return "cat", artifacts[selected].path
            elif ch in ("s", "S"):
                if 0 <= selected < len(artifacts):
                    open_artifact_in_herdr_space(artifacts[selected], edit=False)
            elif ch == "W":
                if 0 <= selected < len(artifacts):
                    open_artifact_in_herdr_space(artifacts[selected], edit=True)
            elif ch in ("q", "Q"):
                return "quit", None
            elif ch.isdigit() and 1 <= int(ch) <= 9:
                target_num = int(ch)
                for idx, a in enumerate(artifacts):
                    if a.index == target_num:
                        selected = idx
                        break
    finally:
        sys.stdout.write("\033[?25h\033[2J\033[H")  # Restore cursor and clear
        sys.stdout.flush()
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass

    return "quit", None


def run_interactive_tui(artifacts: List[Artifact]) -> Tuple[str, Optional[str]]:
    """Runs full interactive curses TUI or falls back to raw-TTY arrow menu or prompt."""
    if not artifacts:
        print("No artifacts found.")
        return "quit", None

    if HAS_CURSES and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return _C.wrapper(_curses_tui_main, artifacts)
        except Exception as e:
            _debug(f"curses TUI failed: {e}")

    # Fallback to raw TTY arrow menu if termios is available
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return _raw_tty_interactive_menu(artifacts)
        except Exception as e:
            _debug(f"raw tty menu failed: {e}")

    # Simple fallback prompt for non-interactive / non-TTY
    print_artifacts_table(artifacts)
    try:
        choice = input(f"Select artifact to review [1-{len(artifacts)}, q]: ").strip()
        if not choice or choice.lower() in ("q", "quit", "exit"):
            return "quit", None
        idx = int(choice)
        if 1 <= idx <= len(artifacts):
            return "open", artifacts[idx - 1].path
    except Exception:
        pass
    return "quit", None


# -----------------------------------------------------------------------------
# CLI Entrypoint & Dispatch
# -----------------------------------------------------------------------------

def resolve_target_index(artifacts: List[Artifact], target_idx: int) -> Optional[Artifact]:
    """Finds artifact by 1-based index or returns None."""
    for a in artifacts:
        if a.index == target_idx:
            return a
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agy-artifacts",
        description="Track, inspect, search, and review Antigravity agent artifacts in real time.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Artifact index (1..N) to open, or subcommand ('list', 'open')",
    )
    parser.add_argument(
        "target_index",
        nargs="?",
        type=int,
        help="Optional index if subcommand is 'open'",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="Print table of artifacts and exit (non-interactive)",
    )
    parser.add_argument(
        "-v",
        "--view",
        nargs="?",
        const="",
        type=str,
        help="Open artifact N (default 1) or file path for viewing with markdown coloring",
    )
    parser.add_argument(
        "-o",
        "--open",
        nargs="?",
        const=-1,
        type=int,
        help="Open artifact N (default 1) for review",
    )
    parser.add_argument(
        "-c",
        "--cat",
        nargs="?",
        const=-1,
        type=int,
        help="Dump artifact N (default 1) content to stdout",
    )
    parser.add_argument(
        "-e",
        "--edit",
        nargs="?",
        const=-1,
        type=int,
        help="Open artifact N (default 1) in $EDITOR",
    )
    parser.add_argument(
        "-S",
        "--space",
        nargs="?",
        const=-1,
        type=int,
        help="Open artifact N (default 1) in new Herdr space with viewer",
    )
    parser.add_argument(
        "--space-edit",
        nargs="?",
        const=-1,
        type=int,
        help="Open artifact N (default 1) in new Herdr space with $EDITOR",
    )
    parser.add_argument(
        "--raw-path",
        nargs="?",
        const=-1,
        type=int,
        help="Print absolute path of artifact N (default 1)",
    )
    parser.add_argument(
        "-f",
        "--tail",
        action="store_true",
        help="Follow/tail the newest active artifact live",
    )
    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        help="Live watch mode (1.5s refresh loop)",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Limit number of artifacts listed (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "-W",
        "--workspace",
        type=str,
        default=None,
        help="Filter artifacts by workspace pattern",
    )
    parser.add_argument(
        "-s",
        "--search",
        type=str,
        default=None,
        help="Search across filenames, headings, summaries, prompts",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output artifact list as JSON array",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include artifacts marked userFacing=false in their metadata",
    )

    args = parser.parse_args(argv)

    # Handle watch mode
    if args.watch:
        watch_artifacts(
            workspace_filter=args.workspace,
            search_query=args.search,
            limit=args.limit,
            include_hidden=args.all,
        )
        return 0

    # Discover artifacts
    artifacts = discover_artifacts(
        workspace_filter=args.workspace,
        search_query=args.search,
        limit=args.limit,
        include_hidden=args.all,
    )

    # JSON Output
    if args.json:
        data = [a.to_dict() for a in artifacts]
        print(json.dumps(data, indent=2))
        return 0

    # Tail mode
    if args.tail:
        if not artifacts:
            print("No artifacts found to follow.", file=sys.stderr)
            return 1
        tail_artifact(
            artifacts[0],
            workspace_filter=args.workspace,
            search_query=args.search,
            include_hidden=args.all,
        )
        return 0

    # Determine action and target index
    action = None
    target_idx = 1

    if args.view is not None:
        target_val = args.view.strip() if args.view else ""
        if target_val and os.path.isfile(target_val):
            return open_artifact(target_val, edit=False)
        action = "open"
        target_idx = 1
        if target_val.isdigit():
            try:
                target_idx = int(target_val)
            except ValueError:
                target_idx = 1
    elif args.cat is not None:
        action = "cat"
        target_idx = 1 if args.cat == -1 else args.cat
    elif args.edit is not None:
        action = "edit"
        target_idx = 1 if args.edit == -1 else args.edit
    elif args.space_edit is not None:
        action = "space_edit"
        target_idx = 1 if args.space_edit == -1 else args.space_edit
    elif args.space is not None:
        action = "space"
        target_idx = 1 if args.space == -1 else args.space
    elif args.open is not None:
        action = "open"
        target_idx = 1 if args.open == -1 else args.open
    elif args.raw_path is not None:
        action = "raw_path"
        target_idx = 1 if args.raw_path == -1 else args.raw_path
    elif args.target is not None:
        t_str = args.target.strip()
        if os.path.isfile(t_str):
            return open_artifact(t_str, edit=False)
        elif t_str == "list":
            action = "list"
        elif t_str in ("open", "view"):
            action = "open"
            target_idx = args.target_index if args.target_index else 1
        elif t_str.isdigit():
            action = "open"
            try:
                target_idx = int(t_str)
            except ValueError:
                action = "list"
        else:
            action = "list"

    if args.list:
        action = "list"

    # Execute specified direct action (single resolve-and-dispatch path)
    if action in ("raw_path", "cat", "edit", "open", "space", "space_edit"):
        if not artifacts:
            print("Error: No artifacts found.", file=sys.stderr)
            return 1
        art = resolve_target_index(artifacts, target_idx)
        if not art:
            print(
                f"Error: Artifact index {target_idx} is out of range (1..{len(artifacts)}).",
                file=sys.stderr,
            )
            return 1
        if action == "raw_path":
            print(art.path)
            return 0
        if action == "cat":
            return cat_artifact(art.path)
        if action == "edit":
            return open_artifact(art.path, edit=True)
        if action == "open":
            return open_artifact(art.path, edit=False)
        ok = open_artifact_in_herdr_space(art, edit=(action == "space_edit"))
        if not ok:
            print(f"Error: Failed to open {art.filename} in Herdr space.", file=sys.stderr)
            return 1
        mode = "editor" if action == "space_edit" else "viewer"
        print(f"Opened {art.filename} in new Herdr space ({mode}).")
        return 0

    if action == "list" or not sys.stdin.isatty() or not sys.stdout.isatty():
        print_artifacts_table(artifacts)
        return 0

    # Interactive TUI mode
    act, path = run_interactive_tui(artifacts)
    if act == "open" and path:
        return open_artifact(path, edit=False)
    elif act == "edit" and path:
        return open_artifact(path, edit=True)
    elif act == "cat" and path:
        return cat_artifact(path)

    return 0


if __name__ == "__main__":
    sys.exit(main())

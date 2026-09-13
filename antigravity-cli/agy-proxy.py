#!/usr/bin/env python3
"""Antigravity Local OpenAI-Compatible Proxy Bridge.

Exposes a standard `/v1/models` and `/v1/chat/completions` API on `127.0.0.1:8085`
backed by the local Antigravity (agy) subscription CLI.
"""
import http.server
import json
import os
import pty
import re
import socketserver
import subprocess
import sys
import time
import uuid
from typing import Dict, Any, List, Optional

PORT = int(os.environ.get("AGY_PROXY_PORT", "58285"))
HOST = os.environ.get("AGY_PROXY_HOST", "127.0.0.1")

AVAILABLE_MODELS = [
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6 (Thinking)",
        "context_length": 200000,
    },
    {
        "id": "claude-opus-4-6-thinking",
        "name": "Claude Opus 4.6 (Thinking)",
        "context_length": 200000,
    },
    {
        "id": "gemini-3.7-flash-high",
        "name": "Gemini 3.7 Flash (High)",
        "context_length": 2000000,
    },
    {
        "id": "gemini-3.7-flash-medium",
        "name": "Gemini 3.7 Flash (Medium)",
        "context_length": 2000000,
    },
    {
        "id": "gemini-3.7-flash-low",
        "name": "Gemini 3.7 Flash (Low)",
        "context_length": 2000000,
    },
    {
        "id": "gemini-3.1-pro-high",
        "name": "Gemini 3.1 Pro (High)",
        "context_length": 1000000,
    },
    {
        "id": "gpt-oss-120b-medium",
        "name": "GPT-OSS 120B (Medium)",
        "context_length": 128000,
    },
]


def normalize_model_id(raw_model: str) -> str:
    m = (raw_model or "").lower()
    if m.startswith("antigravity/"):
        m = m[len("antigravity/") :]
    if "opus" in m:
        return "claude-opus-4-6-thinking"
    if "sonnet" in m:
        return "claude-sonnet-4-6"
    if "3.1-pro" in m or "31pro" in m:
        return "gemini-3.1-pro-high"
    if "3.7-flash" in m or "37flash" in m or "flash" in m:
        if "medium" in m:
            return "gemini-3.7-flash-medium"
        if "low" in m:
            return "gemini-3.7-flash-low"
        return "gemini-3.7-flash-high"
    if "gpt-oss" in m:
        return "gpt-oss-120b-medium"
    return m or "gemini-3.7-flash-high"


SYSTEM_FORMATTING_RULE = (
    "Formatting rule: Always use clean relative file paths or backticks (e.g. `path/to/file`). "
    "NEVER use file:// URIs or github style markdown links with the file:/// scheme under any circumstances."
)


def format_messages_to_prompt(messages: List[Dict[str, Any]]) -> str:
    """Format OpenAI messages into a clean prompt string."""
    if not messages:
        return ""
    if len(messages) == 1 and messages[0].get("role") == "user":
        content = messages[0].get("content") or ""
        if isinstance(content, str):
            return f"<system>\n{SYSTEM_FORMATTING_RULE}\n</system>\n\n<user>\n{content}\n</user>"
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return f"<system>\n{SYSTEM_FORMATTING_RULE}\n</system>\n\n<user>\n{chr(10).join(parts)}\n</user>"

    formatted_parts = []
    has_system = False
    for msg in messages:
        role = (msg.get("role") or "").lower()
        content = msg.get("content") or ""
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            content = "\n".join(text_parts)

        if role == "system":
            has_system = True
            formatted_parts.append(f"<system>\n{SYSTEM_FORMATTING_RULE}\n\n{content}\n</system>")
        elif role == "assistant":
            formatted_parts.append(f"<assistant>\n{content}\n</assistant>")
        else:
            formatted_parts.append(f"<user>\n{content}\n</user>")

    if not has_system:
        formatted_parts.insert(0, f"<system>\n{SYSTEM_FORMATTING_RULE}\n</system>")

    return "\n\n".join(formatted_parts)


def clean_file_uris(text: str) -> str:
    """Clean markdown file:// URI expansions into clean readable file paths."""
    if not text:
        return ""
    # 1. Replace [label](file:///path/to/foo) with label
    t = re.sub(r"\[([^\]]+)\]\(file:///[^)]*\)", r"\1", text)
    # 2. Replace bare (file:///path/to/foo) with empty string
    t = re.sub(r"\s*\(file:///[^)]*\)", "", t)
    # 3. Replace any remaining file:///path/to/foo
    t = re.sub(r"file:///[^\s)>]+", "", t)
    return t


import shutil

def get_agy_bin() -> str:
    candidate = os.path.expanduser("~/.local/bin/agy")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    which_bin = shutil.which("agy")
    if which_bin:
        return which_bin
    return "agy"


class AgyProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        sys.stderr.write(f"[agy-proxy] {self.address_string()} - {format % args}\n")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/v1/models", "/models"):
            self._handle_models()
        elif self.path in ("/health", "/healthz"):
            self._send_json({"status": "ok", "timestamp": int(time.time())})
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            self._handle_chat_completions()
        else:
            self.send_error(404, "Not Found")

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_models(self):
        data = {
            "object": "list",
            "data": [
                {
                    "id": m["id"],
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "antigravity",
                    "permission": [],
                    "root": m["id"],
                    "parent": None,
                }
                for m in AVAILABLE_MODELS
            ],
        }
        self._send_json(data)

    def _handle_chat_completions(self):
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len <= 0:
            self.send_error(400, "Empty request body")
            return

        raw_body = self.rfile.read(content_len)
        try:
            req = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            self.send_error(400, f"Invalid JSON: {e}")
            return

        sys.stderr.write(f"[agy-proxy] Request keys: {list(req.keys())}\n")
        if "tools" in req:
            sys.stderr.write(f"[agy-proxy] Tools received: {[t.get('function', {}).get('name') for t in req.get('tools', [])]}\n")

        model_id = normalize_model_id(req.get("model", "gemini-3.7-flash-high"))
        messages = req.get("messages", [])
        stream = bool(req.get("stream", False))
        prompt_text = format_messages_to_prompt(messages)

        if not prompt_text:
            self.send_error(400, "No messages or prompt provided")
            return

        cmd = [
            get_agy_bin(),
            "--model",
            model_id,
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
        ]

        req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())

        if stream:
            self._handle_streaming_response(cmd, prompt_text, req_id, model_id, created_ts)
            return

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = proc.communicate(input=prompt_text)
        except Exception as e:
            self.send_error(500, f"Failed to spawn agy CLI: {e}")
            return

        self._handle_buffered_response(stdout, req_id, model_id, created_ts)

    def _handle_streaming_response(self, cmd: List[str], prompt_text: str, req_id: str, model_id: str, created_ts: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                text=True,
            )
            proc.stdin.write(prompt_text)
            proc.stdin.close()
        except Exception as e:
            os.close(master_fd)
            os.close(slave_fd)
            self.log_message(f"Failed to spawn agy: {e}")
            return
        finally:
            os.close(slave_fd)

        master_file = os.fdopen(master_fd, "r", encoding="utf-8", errors="replace")

        full_content = ""
        in_tokens = 0
        out_tokens = 0
        sent_first_chunk = False
        checkpoint_dur = 0.0
        thought_shown = False
        stream_buf = ""

        try:
            # Send initial role chunk required by OpenAI streaming clients
            first_chunk = {
                "id": req_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": None,
                    }
                ],
            }
            self.wfile.write(f"data: {json.dumps(first_chunk)}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                raw_line = master_file.readline()
                if not raw_line:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event_data = json.loads(line)
                except Exception:
                    continue

                event_type = event_data.get("event")
                if event_type == "step_update":
                    su = event_data.get("step_update", {})
                    st = su.get("step_type")
                    delta = su.get("text_delta") or ""
                    usage = su.get("usage") or {}
                    dur = su.get("duration_seconds")
                    if st == "checkpoint" and isinstance(dur, (int, float)):
                        checkpoint_dur = dur

                    if usage:
                        in_tokens = usage.get("input_tokens", in_tokens)
                        out_tokens = usage.get("output_tokens", out_tokens)

                    # Intercept thinking tokens and stream clean reasoning_content delta for native Pi Ctrl+O expansion
                    th_tokens = usage.get("thinking_tokens", 0)
                    if th_tokens > 0 and not thought_shown:
                        tot_dur = checkpoint_dur + (dur if isinstance(dur, (int, float)) else 0.0)
                        dur_str = f"{tot_dur:.1f}s" if tot_dur > 0 else f"{checkpoint_dur:.1f}s"
                        thought_detail = f"Thinking trace ({th_tokens} tokens, {dur_str}): Analyzed prompt requirements, evaluated active file context, and generated response."
                        chunk = {
                            "id": req_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model_id,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"reasoning_content": thought_detail},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        thought_shown = True

                    # Intercept internal tool executions and stream clean visual progress on completion
                    if st == "tool":
                        if dur is not None:
                            tool_name = su.get("tool_name") or "tool"
                            tool_info = su.get("tool_info") or {}
                            params = tool_info.get("parameters") or {}
                            dur_str = f" · ✓ {dur:.1f}s" if isinstance(dur, (int, float)) else ""
                            param_str = ""
                            for k in ("CommandLine", "command", "cmd", "AbsolutePath", "path", "Pattern", "Query", "TargetFile"):
                                if k in params:
                                    v = str(params[k]).split("/")[-1] if "/" in str(params[k]) and k not in ("CommandLine", "command", "cmd") else str(params[k])
                                    param_str = f" · {v[:50]}"
                                    break
                            if not param_str and params:
                                first_val = str(list(params.values())[0])
                                param_str = f" · {first_val[:50]}"

                            tool_badge = f"\n▸ {tool_name}{param_str}{dur_str}\n"
                            chunk = {
                                "id": req_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": model_id,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": tool_badge},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                            self.wfile.flush()

                    if delta:
                        stream_buf += delta
                        if ("[" in stream_buf and "]" not in stream_buf) or ("(file" in stream_buf and ")" not in stream_buf):
                            if len(stream_buf) < 400:
                                continue
                        cleaned = clean_file_uris(stream_buf)
                        stream_buf = ""
                        if cleaned:
                            full_content += cleaned
                            chunk = {
                                "id": req_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": model_id,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": cleaned},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                            self.wfile.flush()

                elif event_type == "result":
                    res = event_data.get("result", {})
                    usage = res.get("usage") or {}
                    if usage:
                        in_tokens = usage.get("input_tokens", in_tokens)
                        out_tokens = usage.get("output_tokens", out_tokens)
                    break

            if stream_buf:
                cleaned = clean_file_uris(stream_buf)
                if cleaned:
                    full_content += cleaned
                    chunk = {
                        "id": req_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_id,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": cleaned},
                                "finish_reason": None,
                            }
                        ],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                    self.wfile.flush()

            finish_chunk = {
                "id": req_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
            self.wfile.write(f"data: {json.dumps(finish_chunk)}\n\n".encode("utf-8"))

            if in_tokens or out_tokens:
                usage_chunk = {
                    "id": req_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_id,
                    "choices": [],
                    "usage": {
                        "prompt_tokens": in_tokens,
                        "completion_tokens": out_tokens,
                        "total_tokens": in_tokens + out_tokens,
                    },
                }
                self.wfile.write(f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8"))

            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                master_file.close()
            except Exception:
                pass

    def _handle_buffered_response(self, stdout: str, req_id: str, model_id: str, created_ts: int):
        full_content = ""
        in_tokens = 0
        out_tokens = 0

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event_data = json.loads(line)
            except Exception:
                continue
            event_type = event_data.get("event")
            if event_type == "step_update":
                su = event_data.get("step_update", {})
                delta = su.get("text_delta") or ""
                if delta:
                    full_content += clean_file_uris(delta)
                usage = su.get("usage") or {}
                if usage:
                    in_tokens = usage.get("input_tokens", in_tokens)
                    out_tokens = usage.get("output_tokens", out_tokens)
            elif event_type == "result":
                res = event_data.get("result", {})
                if not full_content:
                    full_content = clean_file_uris(res.get("response") or "")
                usage = res.get("usage") or {}
                if usage:
                    in_tokens = usage.get("input_tokens", in_tokens)
                    out_tokens = usage.get("output_tokens", out_tokens)

        response_payload = {
            "id": req_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": in_tokens,
                "completion_tokens": out_tokens,
                "total_tokens": in_tokens + out_tokens,
            },
        }
        self._send_json(response_payload)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = ThreadingHTTPServer((HOST, PORT), AgyProxyHandler)
    sys.stderr.write(f"[agy-proxy] Antigravity Bridge listening on http://{HOST}:{PORT}/v1\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[agy-proxy] Shutting down...\n")
        server.shutdown()


if __name__ == "__main__":
    main()

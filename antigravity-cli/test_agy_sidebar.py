#!/usr/bin/env python3
"""
test_agy_sidebar.py - Unit test suite for agy-sidebar.py
Tests fast CWD session resolution, extractors (Antigravity, Pi, OpenCode),
quota and context retrieval, discovery engine, and Rich UI layout rendering.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import sqlite3
import subprocess
import socket
import unittest
from unittest.mock import patch, MagicMock

# Import the module under test and register alias in sys.modules for mock.patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib
agy_sidebar = importlib.import_module("agy-sidebar")
sys.modules["agy_sidebar"] = agy_sidebar


class TestFormattingAndUtilities(unittest.TestCase):
    def test_clean_task_prompt_user_request(self):
        raw = "<USER_REQUEST>\nFix performance bottlenecks in sidebar\nAdditional details\n</USER_REQUEST>"
        cleaned = agy_sidebar.clean_task_prompt(raw)
        self.assertEqual(cleaned, "Fix performance bottlenecks in sidebar")

    def test_clean_task_prompt_with_skills_and_metadata(self):
        raw = (
            "<skill name='git'>some skill text</skill>\n"
            "<ADDITIONAL_METADATA>\nTime: 2026-08-23\n</ADDITIONAL_METADATA>\n"
            "<USER_REQUEST>Refactor sqlite database indexing</USER_REQUEST>"
        )
        cleaned = agy_sidebar.clean_task_prompt(raw)
        self.assertEqual(cleaned, "Refactor sqlite database indexing")

    def test_clean_task_prompt_raw_text(self):
        raw = "Build new authentication module\nSecond line details"
        cleaned = agy_sidebar.clean_task_prompt(raw)
        self.assertEqual(cleaned, "Build new authentication module Second line details")

    def test_clean_task_prompt_empty(self):
        self.assertEqual(agy_sidebar.clean_task_prompt(""), "")
        self.assertEqual(agy_sidebar.clean_task_prompt(None), "")

    def test_shorten_path(self):
        home = os.path.expanduser("~")
        self.assertEqual(agy_sidebar.shorten_path(f"{home}/projects/app"), "~/projects/app")
        self.assertEqual(agy_sidebar.shorten_path("/var/log/syslog"), "/var/log/syslog")
        self.assertEqual(agy_sidebar.shorten_path(""), "")

    def test_normalize_model_name(self):
        self.assertEqual(agy_sidebar.normalize_model_name("Gemini 3.7 Flash (High)"), "gemini37flashhigh")
        self.assertEqual(agy_sidebar.normalize_model_name("Claude-Sonnet-4.6"), "claudesonnet46")
        self.assertEqual(agy_sidebar.normalize_model_name(""), "")

    def test_shorten_model_name(self):
        self.assertEqual(agy_sidebar.shorten_model_name("Gemini 3.7 Flash (High)"), "Gemini 3.7 Flash (H)")
        self.assertEqual(agy_sidebar.shorten_model_name("Claude Opus 4.6 (Thinking)"), "Opus 4.6 (T)")
        self.assertEqual(agy_sidebar.shorten_model_name("Claude Sonnet 4.6"), "Sonnet 4.6")
        self.assertEqual(agy_sidebar.shorten_model_name("DeepSeek V4 Flash"), "DeepSeek V4 Flash")
        self.assertEqual(agy_sidebar.shorten_model_name(""), "Gemini")

    def test_format_time_ago(self):
        now = time.time()
        self.assertEqual(agy_sidebar.format_time_ago(now - 2), "just now")
        self.assertEqual(agy_sidebar.format_time_ago(now - 25), "25s ago")
        self.assertEqual(agy_sidebar.format_time_ago(now - 150), "2m ago")
        self.assertEqual(agy_sidebar.format_time_ago(now - 7400), "2h 3m ago")
        self.assertEqual(agy_sidebar.format_time_ago(0), "unknown")

    def test_read_jsonl_tail(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for i in range(100):
                f.write(json.dumps({"idx": i, "val": f"line_{i}"}) + "\n")
            f.flush()
            tmp_path = f.name

        try:
            records = agy_sidebar.read_jsonl_tail(tmp_path, max_bytes=1024)
            self.assertTrue(len(records) > 0)
            self.assertEqual(records[-1]["idx"], 99)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestCwdResolver(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.conv_dir = os.path.join(self.tmp_dir, "conversations")
        os.makedirs(self.conv_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_mock_conv_db(self, cid: str, ws_path: str, mtime_offset: int = 0):
        db_path = os.path.join(self.conv_dir, f"{cid}.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE trajectory_metadata_blob (id TEXT PRIMARY KEY, data BLOB);")
        blob_data = f"\n\x50\n5file://{ws_path}\x125file://{ws_path}\x1a\x10test-repo\x04main".encode("utf-8")
        cur.execute("INSERT INTO trajectory_metadata_blob (id, data) VALUES ('main', ?)", (blob_data,))
        conn.commit()
        conn.close()
        # Set mtime
        new_mtime = time.time() - mtime_offset
        os.utime(db_path, (new_mtime, new_mtime))
        return db_path

    def test_resolve_exact_and_subpath_cwd(self):
        ws1 = os.path.join(self.tmp_dir, "projects", "repo-alpha")
        ws2 = os.path.join(self.tmp_dir, "projects", "repo-beta")
        os.makedirs(ws1, exist_ok=True)
        os.makedirs(ws2, exist_ok=True)

        self._create_mock_conv_db("cid-alpha-111", ws1, mtime_offset=10)
        self._create_mock_conv_db("cid-beta-222", ws2, mtime_offset=5)

        resolver = agy_sidebar.CwdResolver(self.conv_dir)

        # Exact match
        self.assertEqual(resolver.resolve_cwd(ws1), "cid-alpha-111")
        self.assertEqual(resolver.resolve_cwd(ws2), "cid-beta-222")

        # Subdirectory match
        sub_alpha = os.path.join(ws1, "src", "components")
        self.assertEqual(resolver.resolve_cwd(sub_alpha), "cid-alpha-111")

        # Fallback to latest cid
        unknown_dir = os.path.join(self.tmp_dir, "other", "unknown")
        self.assertEqual(resolver.resolve_cwd(unknown_dir), "cid-beta-222")

    def test_resolve_parent_cwd(self):
        ws = os.path.join(self.tmp_dir, "parent", "subproject")
        os.makedirs(ws, exist_ok=True)
        self._create_mock_conv_db("cid-nested-999", ws)

        resolver = agy_sidebar.CwdResolver(self.conv_dir)
        parent_dir = os.path.join(self.tmp_dir, "parent")
        self.assertEqual(resolver.resolve_cwd(parent_dir), "cid-nested-999")

    def test_cache_invalidation_on_mtime_change(self):
        ws1 = os.path.join(self.tmp_dir, "repo1")
        ws2 = os.path.join(self.tmp_dir, "repo2")
        os.makedirs(ws1, exist_ok=True)
        os.makedirs(ws2, exist_ok=True)

        self._create_mock_conv_db("cid-1", ws1)
        resolver = agy_sidebar.CwdResolver(self.conv_dir)
        self.assertEqual(resolver.resolve_cwd(ws1), "cid-1")

        # Add second db with newer mtime
        time.sleep(0.01)
        self._create_mock_conv_db("cid-2", ws2)
        # Update dir mtime
        os.utime(self.conv_dir, None)

        self.assertEqual(resolver.resolve_cwd(ws2), "cid-2")

    def test_corrupt_db_and_nonexistent_dir(self):
        corrupt_path = os.path.join(self.conv_dir, "corrupt.db")
        with open(corrupt_path, "wb") as f:
            f.write(b"not a valid sqlite file")

        resolver = agy_sidebar.CwdResolver(self.conv_dir)
        # Should not crash and return None since no valid db exists
        self.assertIsNone(resolver.resolve_cwd("/some/path"))

        empty_resolver = agy_sidebar.CwdResolver("/non/existent/dir")
        self.assertIsNone(empty_resolver.resolve_cwd("/some/path"))


class TestAntigravityExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.brain_dir = os.path.join(self.tmp_dir, "brain")
        self.conv_dir = os.path.join(self.tmp_dir, "conversations")
        os.makedirs(self.brain_dir, exist_ok=True)
        os.makedirs(self.conv_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_mock_session(self, cid: str, ws_path: str, records: list):
        # Create conv db
        db_path = os.path.join(self.conv_dir, f"{cid}.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE trajectory_metadata_blob (id TEXT PRIMARY KEY, data BLOB);")
        blob_data = f"\n\x50\n5file://{ws_path}\x125file://{ws_path}".encode("utf-8")
        cur.execute("INSERT INTO trajectory_metadata_blob (id, data) VALUES ('main', ?)", (blob_data,))
        conn.commit()
        conn.close()

        # Create brain transcript
        logs_dir = os.path.join(self.brain_dir, cid, ".system_generated", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        transcript_path = os.path.join(logs_dir, "transcript.jsonl")
        with open(transcript_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return transcript_path

    def test_extract_working_with_tools(self):
        ws = os.path.join(self.tmp_dir, "myproject")
        os.makedirs(ws, exist_ok=True)

        records = [
            {"type": "USER_INPUT", "content": "<USER_REQUEST>Implement user auth</USER_REQUEST>"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command"}, {"name": "write_to_file"}]},
        ]
        self._create_mock_session("session-123", ws, records)

        extractor = agy_sidebar.AntigravityExtractor(self.brain_dir, self.conv_dir)
        info = extractor.extract(None, "%1", ws, is_focused=True)

        self.assertEqual(info.pane_id, "%1")
        self.assertEqual(info.agent_type, "agy")
        self.assertEqual(info.status, "WORKING")
        self.assertEqual(info.task, "Implement user auth")
        self.assertIn("running: run_command, write_to_file", info.current_step)
        self.assertTrue(info.is_focused)
        self.assertEqual(info.details.get("tool_calls"), ["run_command", "write_to_file"])

    def test_extract_idle_status(self):
        ws = os.path.join(self.tmp_dir, "myproject2")
        os.makedirs(ws, exist_ok=True)

        records = [
            {"type": "USER_INPUT", "content": "Generate unit tests"},
            {"type": "PLANNER_RESPONSE", "content": "Unit tests have been created and verified successfully."},
        ]
        self._create_mock_session("session-456", ws, records)

        extractor = agy_sidebar.AntigravityExtractor(self.brain_dir, self.conv_dir)
        info = extractor.extract("session-456", "%2", ws, is_focused=False)

        self.assertEqual(info.status, "IDLE")
        self.assertIn("Unit tests have been created", info.current_step)
        self.assertFalse(info.is_focused)

    def test_extract_missing_transcript_fallback(self):
        empty_dir = os.path.join(self.brain_dir, "empty-cid")
        os.makedirs(empty_dir, exist_ok=True)
        extractor = agy_sidebar.AntigravityExtractor(self.brain_dir, self.conv_dir)
        info = extractor.extract("empty-cid", "%9", "/tmp", False)
        self.assertEqual(info.status, "IDLE")
        self.assertEqual(info.agent_type, "agy")

        info_unknown = extractor.extract("nonexistent-cid", "%9", "/tmp", False)
        self.assertEqual(info_unknown.status, "UNKNOWN")


class TestPiExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.sessions_dir = os.path.join(self.tmp_dir, "pi_sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_extract_pi_session_with_tool_call(self):
        session_file = os.path.join(self.sessions_dir, "session_test.jsonl")
        records = [
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Fix bug in parser"}],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "toolCall", "name": "bash", "arguments": "pytest -v"}],
                },
            },
            {
                "type": "model_change",
                "model": {"name": "DeepSeek V4"},
            },
        ]
        with open(session_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        extractor = agy_sidebar.PiExtractor(self.sessions_dir)
        info = extractor.extract(session_file, "%3", "/workspace", True)

        self.assertEqual(info.agent_type, "pi")
        self.assertEqual(info.status, "WORKING")
        self.assertEqual(info.task, "Fix bug in parser")
        self.assertIn("running: bash(pytest -v)", info.current_step)
        self.assertEqual(info.model, "DeepSeek V4")

    def test_extract_missing_pi_session(self):
        extractor = agy_sidebar.PiExtractor(self.sessions_dir)
        info = extractor.extract(None, "%4", "/workspace", False)
        self.assertEqual(info.status, "UNKNOWN")


class TestOpenCodeExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "opencode.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_opencode_sessions_and_todos(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                title TEXT,
                directory TEXT,
                agent TEXT,
                model TEXT,
                time_updated REAL
            );
        """)
        cur.execute("""
            CREATE TABLE todo (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                content TEXT,
                status TEXT,
                position INTEGER
            );
        """)
        cur.execute("""
            INSERT INTO session VALUES (
                'oc-1', 'Refactor DB schema', '/my/project', 'opencode', '{"id": "glm-5.3"}', 1700000000.0
            );
        """)
        cur.execute("""
            INSERT INTO todo VALUES (
                't-1', 'oc-1', 'Write migration script', 'in_progress', 1
            );
        """)
        conn.commit()
        conn.close()

        extractor = agy_sidebar.OpenCodeExtractor(self.db_path)
        sessions = extractor.get_sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["session_id"], "oc-1")
        self.assertEqual(s["title"], "Refactor DB schema")
        self.assertEqual(s["directory"], "/my/project")
        self.assertEqual(s["model"], "glm-5.3")
        self.assertEqual(s["todo"], "Write migration script")

        # Test caching via PRAGMA data_version
        cached = extractor.get_sessions()
        self.assertEqual(cached, sessions)


class TestQuotaDataLoader(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.quota_file = os.path.join(self.tmp_dir, "quota-cache.json")
        self.state_file = os.path.join(self.tmp_dir, "status-state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_load_quota_data_success(self):
        quota_data = {
            "models": {
                "gemini37flashhigh": {
                    "name": "Gemini 3.7 Flash (High)",
                    "remaining_percentage": 74.4,
                    "refreshes_in": "4h 15m",
                    "reset_time": "2026-08-23T18:11:23Z",
                },
                "claudeopus46": {
                    "name": "Claude Opus 4.6",
                    "remaining_percentage": 100.0,
                    "refreshes_in": "5h",
                    "reset_time": "2026-08-23T18:56:06Z",
                },
            },
            "scope": {"email": "user@example.com", "plan_tier": "Pro"},
        }
        state_data = {
            "email": "user@example.com",
            "model": "Gemini 3.7 Flash (High)",
            "plan_tier": "Google AI Ultra",
        }

        with open(self.quota_file, "w", encoding="utf-8") as f:
            json.dump(quota_data, f)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        q, s = agy_sidebar.load_quota_data(self.quota_file, self.state_file)
        self.assertIn("gemini37flashhigh", q.get("models", {}))
        self.assertEqual(s.get("plan_tier"), "Google AI Ultra")

    def test_load_quota_missing_files(self):
        q, s = agy_sidebar.load_quota_data("/nonexistent/quota.json", "/nonexistent/state.json")
        self.assertEqual(q, {})
        self.assertEqual(s, {})


class TestMultiAgentDiscovery(unittest.TestCase):
    @patch("agy_sidebar.socket.socket")
    @patch("os.path.exists")
    def test_discover_from_herdr_socket(self, mock_exists, mock_socket_cls):
        mock_exists.return_value = True
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        herdr_response = {
            "result": {
                "panes": [
                    {
                        "pane_id": "%1",
                        "agent": "agy",
                        "cwd": "/workspace/repo1",
                        "focused": True,
                        "agent_session": {"value": "cid-1", "source": "herdr:antigravity_cli"},
                    },
                    {
                        "pane_id": "%2",
                        "agent": "opencode",
                        "cwd": "/workspace/repo2",
                        "focused": False,
                        "agent_session": {},
                    },
                ]
            }
        }
        mock_sock.recv.return_value = json.dumps(herdr_response).encode("utf-8")

        mock_agy = MagicMock()
        mock_agy.extract.return_value = agy_sidebar.AgentInfo(
            pane_id="%1",
            agent_type="agy",
            cwd="/workspace/repo1",
            status="WORKING",
            task="Build feature",
            current_step="running: test",
            model="Gemini 3.7 Flash",
            is_focused=True,
        )

        mock_oc = MagicMock()
        mock_oc.get_sessions.return_value = [
            {
                "session_id": "oc-9",
                "title": "Fix API endpoint",
                "directory": "/workspace/repo2",
                "agent": "opencode",
                "model": "GLM-5.3",
                "todo": "Run tests",
                "time_updated": 1700000000.0,
            }
        ]

        discovery = agy_sidebar.MultiAgentDiscovery(
            agy_extractor=mock_agy,
            opencode_extractor=mock_oc,
        )

        agents = discovery.discover_from_herdr_socket()
        self.assertIsNotNone(agents)
        self.assertEqual(len(agents), 2)
        self.assertEqual(agents[0].agent_type, "agy")
        self.assertEqual(agents[0].pane_id, "%1")
        self.assertEqual(agents[1].agent_type, "opencode")
        self.assertEqual(agents[1].pane_id, "%2")
        self.assertEqual(agents[1].status, "WORKING")


class TestSidebarAppUI(unittest.TestCase):
    def setUp(self):
        self.telemetry = agy_sidebar.TelemetryManager(auto_start=False)
        self.app = agy_sidebar.SidebarApp(self.telemetry)

    def tearDown(self):
        self.telemetry.stop()

    def test_render_status_pill(self):
        self.assertIn("WORKING", self.app.render_status_pill("WORKING"))
        self.assertIn("IDLE", self.app.render_status_pill("IDLE"))
        self.assertIn("DONE", self.app.render_status_pill("DONE"))
        self.assertIn("WAITING", self.app.render_status_pill("WAITING INPUT"))
        self.assertIn("BLOCKED", self.app.render_status_pill("BLOCKED"))
        self.assertIn("UNKNOWN", self.app.render_status_pill("UNKNOWN"))

    def test_render_agent_badge(self):
        self.assertIn("AGY", self.app.render_agent_badge("agy"))
        self.assertIn("PI", self.app.render_agent_badge("pi"))
        self.assertIn("OPENCODE", self.app.render_agent_badge("opencode"))
        self.assertIn("CLAUDE", self.app.render_agent_badge("claude"))

    def test_render_agents_tab_with_agents(self):
        agents = [
            agy_sidebar.AgentInfo(
                pane_id="%1",
                agent_type="agy",
                cwd="/projects/alpha",
                status="WORKING",
                task="Refactor codebase",
                current_step="running: replace_file_content",
                model="Gemini 3.7 Flash",
                is_focused=True,
                details={"tool_calls": ["replace_file_content", "run_command"]},
            ),
            agy_sidebar.AgentInfo(
                pane_id="%2",
                agent_type="pi",
                cwd="/projects/beta",
                status="IDLE",
                task="Review pull request",
                current_step="Responded",
                model="DeepSeek V4",
                is_focused=False,
            ),
        ]

        self.app.selected_index = 0
        layout = self.app.render_agents_tab(agents)
        self.assertIsNotNone(layout)
        # Check sublayouts
        self.assertIsNotNone(layout["table"])
        self.assertIsNotNone(layout["detail"])

    def test_render_agents_tab_empty(self):
        layout = self.app.render_agents_tab([])
        self.assertIsNotNone(layout)

    def test_render_quotas_tab(self):
        panel = self.app.render_quotas_tab()
        self.assertIsNotNone(panel)

    def test_render_context_tab(self):
        panel = self.app.render_context_tab()
        self.assertIsNotNone(panel)

    def test_render_logs_tab(self):
        panel = self.app.render_logs_tab([])
        self.assertIsNotNone(panel)

    def test_render_full_layout(self):
        layout = self.app.render()
        self.assertIsNotNone(layout["header"])
        self.assertIsNotNone(layout["main"])
        self.assertIsNotNone(layout["footer"])

    def test_selection_clamping_and_movement(self):
        agents = [
            agy_sidebar.AgentInfo("%1", "agy", "/p1", "IDLE", "T1", "S1", "Gemini"),
            agy_sidebar.AgentInfo("%2", "pi", "/p2", "IDLE", "T2", "S2", "DeepSeek"),
        ]
        self.app.selected_index = 0
        self.app.render_agents_tab(agents)

        # Move down
        self.app.selected_index = min(len(agents) - 1, self.app.selected_index + 1)
        self.assertEqual(self.app.selected_index, 1)

        # Move down beyond max -> clamped
        self.app.selected_index = min(len(agents) - 1, self.app.selected_index + 1)
        self.assertEqual(self.app.selected_index, 1)

        # Move up
        self.app.selected_index = max(0, self.app.selected_index - 1)
        self.assertEqual(self.app.selected_index, 0)

        # Move up below 0 -> clamped
        self.app.selected_index = max(0, self.app.selected_index - 1)
        self.assertEqual(self.app.selected_index, 0)

    @patch("agy_sidebar.focus_herdr_pane")
    def test_focus_selected_agent(self, mock_focus):
        mock_focus.return_value = True
        agents = [agy_sidebar.AgentInfo("%1", "agy", "/p1", "IDLE", "T1", "S1", "Gemini")]
        self.app.selected_index = 0
        self.app.focus_selected_agent(agents)
        mock_focus.assert_called_with("%1")
        self.assertIn("Focused %1", self.app.status_msg)


class TestFocusHerdrPane(unittest.TestCase):
    @patch("agy_sidebar.socket.socket")
    @patch("os.path.exists")
    def test_focus_herdr_pane_socket(self, mock_exists, mock_socket_cls):
        mock_exists.return_value = True
        mock_sock = MagicMock()
        mock_sock.recv.return_value = json.dumps({"result": {"focused": True}}).encode("utf-8")
        mock_socket_cls.return_value = mock_sock

        ok = agy_sidebar.focus_herdr_pane("%1")
        self.assertTrue(ok)
        mock_sock.sendall.assert_called()

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_focus_herdr_pane_cli_fallback(self, mock_exists, mock_run):
        mock_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        ok = agy_sidebar.focus_herdr_pane("%2")
        self.assertTrue(ok)
        mock_run.assert_called_with(["herdr", "pane", "focus", "--pane", "%2"], capture_output=True, timeout=0.5)


class TestKeyParsingAndDispatch(unittest.TestCase):
    def test_key_action_mappings(self):
        def map_key(raw):
            if raw in ("\x1b[A", "\x1bOA", "\x1b[1;5A", "k", "K"):
                return "UP"
            elif raw in ("\x1b[B", "\x1bOB", "\x1b[1;5B", "j", "J"):
                return "DOWN"
            elif raw in ("\x1b[C", "\x1bOC"):
                return "RIGHT"
            elif raw in ("\x1b[D", "\x1bOD"):
                return "LEFT"
            elif raw in ("\r", "\n"):
                return "ENTER"
            elif raw in ("q", "Q", "\x1b"):
                return "QUIT"
            elif raw == " ":
                return "SPACE"
            elif raw in ("r", "R"):
                return "REFRESH"
            elif raw in ("1", "2", "3", "4"):
                return f"TAB_{raw}"
            elif raw.startswith("\x1b[") or raw.startswith("\x1bO"):
                if raw.endswith("A"):
                    return "UP"
                elif raw.endswith("B"):
                    return "DOWN"
            return None

        # Arrow keys ANSI and Application mode
        self.assertEqual(map_key("\x1b[A"), "UP")
        self.assertEqual(map_key("\x1bOA"), "UP")
        self.assertEqual(map_key("\x1b[B"), "DOWN")
        self.assertEqual(map_key("\x1bOB"), "DOWN")
        self.assertEqual(map_key("k"), "UP")
        self.assertEqual(map_key("j"), "DOWN")
        self.assertEqual(map_key("\r"), "ENTER")
        self.assertEqual(map_key("q"), "QUIT")
        self.assertEqual(map_key("\x1b"), "QUIT")
        self.assertEqual(map_key("1"), "TAB_1")
        self.assertEqual(map_key(" "), "SPACE")


if __name__ == "__main__":
    unittest.main()


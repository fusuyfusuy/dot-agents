#!/usr/bin/env python3
"""
test_agy_artifacts.py - Unit test suite for agy-artifacts.py
"""

import os
import sys
import json
import time
import shutil
import tempfile
import sqlite3
import shlex
import subprocess
import unittest
from io import StringIO
from unittest.mock import patch, MagicMock

# Import the module under test
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib
agy_artifacts = importlib.import_module("agy-artifacts")


class TestPromptAndHeadingExtraction(unittest.TestCase):
    def test_clean_user_prompt_with_tag(self):
        raw = "<USER_REQUEST>\nImplement feature XYZ\nDetailed description here\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nTime: 2026-08-23\n</ADDITIONAL_METADATA>"
        cleaned = agy_artifacts.clean_user_prompt(raw)
        self.assertEqual(cleaned, "Implement feature XYZ")

    def test_clean_user_prompt_with_skill_block(self):
        raw = "<skill name='git'>some skill text</skill>\n<USER_REQUEST>Fix memory leak in parser</USER_REQUEST>"
        cleaned = agy_artifacts.clean_user_prompt(raw)
        self.assertEqual(cleaned, "Fix memory leak in parser")

    def test_clean_user_prompt_raw_text(self):
        raw = "Build new authentication module\nSecond line"
        cleaned = agy_artifacts.clean_user_prompt(raw)
        self.assertEqual(cleaned, "Build new authentication module Second line")

    def test_clean_user_prompt_empty(self):
        self.assertEqual(agy_artifacts.clean_user_prompt(""), "")
        self.assertEqual(agy_artifacts.clean_user_prompt(None), "")

    def test_extract_md_heading_h1(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# Main Project Plan\n\nSome text\n## Sub heading\n")
            tmp_path = f.name
        try:
            heading = agy_artifacts.extract_md_heading(tmp_path)
            self.assertEqual(heading, "Main Project Plan")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_extract_md_heading_h2_and_formatted(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("\n\n## Refactor [`StorageEngine`](file:///path) & **Cache**\n")
            tmp_path = f.name
        try:
            heading = agy_artifacts.extract_md_heading(tmp_path)
            self.assertEqual(heading, "Refactor StorageEngine & Cache")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_extract_md_heading_none(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("Just plain text without headers.\n")
            tmp_path = f.name
        try:
            heading = agy_artifacts.extract_md_heading(tmp_path)
            self.assertEqual(heading, "")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestMetadataAndSessionDb(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_artifact_metadata_camelcase(self):
        art_path = os.path.join(self.test_dir, "plan.md")
        meta_path = art_path + ".metadata.json"
        with open(art_path, "w") as f:
            f.write("# Plan\n")
        with open(meta_path, "w") as f:
            json.dump({
                "summary": "Plan for backend migration",
                "userFacing": True,
                "requestFeedback": True
            }, f)

        meta = agy_artifacts.load_artifact_metadata(art_path)
        self.assertEqual(meta["summary"], "Plan for backend migration")
        self.assertTrue(meta["user_facing"])
        self.assertTrue(meta["request_feedback"])

    def test_load_artifact_metadata_pascalcase(self):
        art_path = os.path.join(self.test_dir, "report.json")
        meta_path = art_path + ".metadata.json"
        with open(art_path, "w") as f:
            f.write("{}\n")
        with open(meta_path, "w") as f:
            json.dump({
                "Summary": "Execution report",
                "UserFacing": False,
                "RequestFeedback": False
            }, f)

        meta = agy_artifacts.load_artifact_metadata(art_path)
        self.assertEqual(meta["summary"], "Execution report")
        self.assertFalse(meta["user_facing"])
        self.assertFalse(meta["request_feedback"])

    def test_load_artifact_metadata_missing(self):
        art_path = os.path.join(self.test_dir, "missing.md")
        meta = agy_artifacts.load_artifact_metadata(art_path)
        self.assertEqual(meta, {})

    def test_load_db_sessions(self):
        db_path = os.path.join(self.test_dir, "summaries.db")
        con = sqlite3.connect(db_path)
        con.execute("""
            CREATE TABLE conversation_summaries (
                conversation_id text PRIMARY KEY,
                title text,
                preview text,
                workspace_uris text,
                last_modified_time datetime
            )
        """)
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
            ("cid-1", "Refactor Auth", "Auth preview", json.dumps(["file:///workspace/project-alpha"]), "2026-08-23 12:00:00")
        )
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
            ("cid-2", "", "Preview only title", json.dumps(["file:///workspace/project-beta"]), "2026-08-23 13:00:00")
        )
        con.commit()
        con.close()

        sessions = agy_artifacts.load_db_sessions(db_path)
        self.assertIn("cid-1", sessions)
        self.assertEqual(sessions["cid-1"]["title"], "Refactor Auth")
        self.assertEqual(sessions["cid-1"]["workspace"], "/workspace/project-alpha")

        self.assertIn("cid-2", sessions)
        self.assertEqual(sessions["cid-2"]["title"], "Preview only title")
        self.assertEqual(sessions["cid-2"]["workspace"], "/workspace/project-beta")


class TestWorkspaceResolver(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_ws_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_workspace_from_conversation_db(self):
        conv_db = os.path.join(self.test_dir, "conv_123.db")
        con = sqlite3.connect(conv_db)
        con.execute("CREATE TABLE trajectory_metadata_blob (id text PRIMARY KEY, data blob)")
        fake_blob = b"prefix_data\x00file:///workspace/projects/sample-repo\x00file:///workspace/projects/sample-repo"
        con.execute("INSERT INTO trajectory_metadata_blob VALUES (?, ?)", ("main", fake_blob))
        con.commit()
        con.close()

        ws = agy_artifacts.extract_workspace_from_conversation_db(conv_db)
        self.assertEqual(ws, "/workspace/projects/sample-repo")

    def test_extract_workspace_from_conversation_db_missing_or_empty(self):
        missing_db = os.path.join(self.test_dir, "missing.db")
        self.assertEqual(agy_artifacts.extract_workspace_from_conversation_db(missing_db), "")

        empty_db = os.path.join(self.test_dir, "empty.db")
        con = sqlite3.connect(empty_db)
        con.execute("CREATE TABLE other_table (id text)")
        con.commit()
        con.close()
        self.assertEqual(agy_artifacts.extract_workspace_from_conversation_db(empty_db), "")

    def test_extract_workspace_from_transcript_tool_calls_cwd(self):
        t_path = os.path.join(self.test_dir, "transcript.jsonl")
        lines = [
            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "hello"}),
            json.dumps({
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "run_command",
                        "args": {
                            "CommandLine": "git status",
                            "Cwd": '"/workspace/projects/my-config"'
                        }
                    }
                ]
            })
        ]
        with open(t_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        ws = agy_artifacts.extract_workspace_from_transcript(t_path)
        self.assertEqual(ws, "/workspace/projects/my-config")

    def test_extract_workspace_from_transcript_tool_calls_search_dir_and_target_file(self):
        t_path = os.path.join(self.test_dir, "transcript_sd.jsonl")
        lines = [
            json.dumps({
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "find_by_name",
                        "args": {
                            "SearchDirectory": "/workspace/projects/my-search-app"
                        }
                    }
                ]
            })
        ]
        with open(t_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        ws = agy_artifacts.extract_workspace_from_transcript(t_path)
        self.assertEqual(ws, "/workspace/projects/my-search-app")

        t_path_tf = os.path.join(self.test_dir, "transcript_tf.jsonl")
        lines_tf = [
            json.dumps({
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "write_to_file",
                        "args": {
                            "TargetFile": "/workspace/projects/target-app/src/index.ts"
                        }
                    }
                ]
            })
        ]
        with open(t_path_tf, "w") as f:
            f.write("\n".join(lines_tf) + "\n")

        ws_tf = agy_artifacts.extract_workspace_from_transcript(t_path_tf)
        self.assertEqual(ws_tf, "/workspace/projects/target-app/src")

    def test_extract_workspace_from_transcript_user_information(self):
        t_path = os.path.join(self.test_dir, "transcript_ui.jsonl")
        user_info = "<user_information>\n[URI] -> [CorpusName]:\n/workspace/projects/demo-tool -> demo-tool\n</user_information>"
        lines = [
            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": user_info})
        ]
        with open(t_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        ws = agy_artifacts.extract_workspace_from_transcript(t_path)
        self.assertEqual(ws, "/workspace/projects/demo-tool")

    def test_extract_workspace_from_transcript_regex_fallback(self):
        t_path = os.path.join(self.test_dir, "transcript_raw.jsonl")
        with open(t_path, "w") as f:
            f.write('NON_JSON_LINE "Cwd":"/workspace/projects/regex-project"\n')

        ws = agy_artifacts.extract_workspace_from_transcript(t_path)
        self.assertEqual(ws, "/workspace/projects/regex-project")

    def test_workspace_short_property(self):
        art1 = agy_artifacts.Artifact(1, "/p", "f", "c", "t", "/workspace/projects/demo-scrape", 0, 0)
        self.assertEqual(art1.workspace_short, "demo-scrape")

        art2 = agy_artifacts.Artifact(2, "/p", "f", "c", "t", "/workspace/projects/agents-config/", 0, 0)
        self.assertEqual(art2.workspace_short, "agents-config")

        art3 = agy_artifacts.Artifact(3, "/p", "f", "c", "t", "file:///workspace/projects/demo-tool", 0, 0)
        self.assertEqual(art3.workspace_short, "demo-tool")

        art4 = agy_artifacts.Artifact(4, "/p", "f", "c", "t", "/", 0, 0)
        self.assertEqual(art4.workspace_short, "/")

        art5 = agy_artifacts.Artifact(5, "/p", "f", "c", "t", "", 0, 0)
        self.assertEqual(art5.workspace_short, "")

    def test_multisource_resolution_hierarchy(self):
        brain_dir = os.path.join(self.test_dir, "brain")
        conv_dir = os.path.join(self.test_dir, "conversations")
        summaries_db = os.path.join(self.test_dir, "conversation_summaries.db")
        os.makedirs(brain_dir)
        os.makedirs(conv_dir)

        # 1. CID 1: resolved by summaries.db (Level 1)
        con = sqlite3.connect(summaries_db)
        con.execute("""
            CREATE TABLE conversation_summaries (
                conversation_id text PRIMARY KEY,
                title text,
                preview text,
                workspace_uris text,
                last_modified_time datetime
            )
        """)
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
            ("cid-1", "Task 1", "", json.dumps(["file:///projects/level1-ws"]), "2026-08-23")
        )
        con.commit()
        con.close()

        cid1_dir = os.path.join(brain_dir, "cid-1")
        os.makedirs(cid1_dir)
        with open(os.path.join(cid1_dir, "art1.md"), "w") as f:
            f.write("# Doc 1\n")

        # 2. CID 2: resolved by conversations/<cid>.db (Level 2)
        cid2_dir = os.path.join(brain_dir, "cid-2")
        os.makedirs(cid2_dir)
        with open(os.path.join(cid2_dir, "art2.md"), "w") as f:
            f.write("# Doc 2\n")

        conv2_db = os.path.join(conv_dir, "cid-2.db")
        con2 = sqlite3.connect(conv2_db)
        con2.execute("CREATE TABLE trajectory_metadata_blob (id text PRIMARY KEY, data blob)")
        con2.execute("INSERT INTO trajectory_metadata_blob VALUES (?, ?)", ("main", b"file:///projects/level2-ws"))
        con2.commit()
        con2.close()

        # 3. CID 3: resolved by transcript.jsonl (Level 3)
        cid3_dir = os.path.join(brain_dir, "cid-3")
        os.makedirs(os.path.join(cid3_dir, ".system_generated", "logs"))
        with open(os.path.join(cid3_dir, "art3.md"), "w") as f:
            f.write("# Doc 3\n")
        with open(os.path.join(cid3_dir, ".system_generated", "logs", "transcript.jsonl"), "w") as f:
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "run", "args": {"Cwd": "/projects/level3-ws"}}]
            }) + "\n")

        with patch.dict(os.environ, {"AGY_CONVERSATIONS_DIR": conv_dir}):
            arts = agy_artifacts.discover_artifacts(brain_dir=brain_dir, db_path=summaries_db)

        ws_map = {a.filename: a.workspace for a in arts}
        self.assertEqual(ws_map.get("art1.md"), "/projects/level1-ws")
        self.assertEqual(ws_map.get("art2.md"), "/projects/level2-ws")
        self.assertEqual(ws_map.get("art3.md"), "/projects/level3-ws")


class TestMarkdownRendering(unittest.TestCase):
    def test_render_markdown_rich(self):
        sample = "# Title\n\n- item 1\n`code`"
        rendered = agy_artifacts.render_markdown_ansi(sample, use_color=True, width=80)
        self.assertTrue(len(rendered) > 0)
        self.assertIn("Title", rendered)
        self.assertIn("item 1", rendered)

    def test_render_markdown_fallback_headings(self):
        sample = "# Heading 1\n## Heading 2\n### Heading 3\n#### Heading 4"
        out = agy_artifacts._render_markdown_fallback_ansi(sample, width=80)
        self.assertIn("\033[1;36m# Heading 1\033[0m", out)
        self.assertIn("\033[1;33m## Heading 2\033[0m", out)
        self.assertIn("\033[1;32m### Heading 3\033[0m", out)
        self.assertIn("\033[1;35m#### Heading 4\033[0m", out)

    def test_render_markdown_fallback_inline_formatting(self):
        sample = "Here is **bold** text and *italic* text with `inline_code()` and [Link Text](https://example.com)."
        out = agy_artifacts._render_markdown_fallback_ansi(sample, width=80)
        self.assertIn("\033[1;37mbold\033[0m", out)
        self.assertIn("\033[3mitalic\033[0m", out)
        self.assertIn("\033[35minline_code()\033[0m", out)
        self.assertIn("\033[4;34mLink Text\033[0m", out)

    def test_render_markdown_fallback_code_blocks(self):
        sample = "```python\ndef greet():\n    return 'hello'\n```"
        out = agy_artifacts._render_markdown_fallback_ansi(sample, width=80)
        self.assertIn("┌── python", out)
        self.assertIn("\033[33m  def greet():\033[0m", out)
        self.assertIn("└───", out)

    def test_render_markdown_fallback_lists_and_quotes(self):
        sample = "> Quote message\n- Bullet 1\n1. Numbered 1"
        out = agy_artifacts._render_markdown_fallback_ansi(sample, width=80)
        self.assertIn("│", out)
        self.assertIn("Quote message", out)
        self.assertIn("\033[32m•\033[0m Bullet 1", out)
        self.assertIn("\033[32m1.\033[0m Numbered 1", out)

    def test_render_markdown_fallback_tables(self):
        sample = "| Header 1 | Header 2 |\n|---|---|\n| Cell 1 | Cell 2 |"
        out = agy_artifacts._render_markdown_fallback_ansi(sample, width=80)
        self.assertIn("│", out)
        self.assertIn("┼", out)
        self.assertIn("Cell 1", out)

    def test_render_markdown_no_color(self):
        sample = "# Heading\n**bold** and `code`"
        rendered = agy_artifacts.render_markdown_ansi(sample, use_color=False)
        self.assertEqual(rendered, sample)

    def test_render_markdown_no_color_env(self):
        sample = "# Heading\n**bold** and `code`"
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            rendered = agy_artifacts.render_markdown_ansi(sample, use_color=True)
            self.assertEqual(rendered, sample)

    def test_render_markdown_wrapping(self):
        sample = "This is a long sentence that should be wrapped across multiple lines when rendered with a narrow terminal width."
        out = agy_artifacts._render_markdown_fallback_ansi(sample, width=40)
        lines = out.splitlines()
        self.assertTrue(len(lines) > 1)
        for l in lines:
            self.assertLessEqual(len(l), 40)


class TestTranscriptFallback(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_transcript_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_prompt_from_transcript(self):
        t_path = os.path.join(self.test_dir, "transcript.jsonl")
        lines = [
            json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>/agent-context</USER_REQUEST>"}),
            json.dumps({"type": "PLANNER_RESPONSE", "content": "Running agent-context"}),
            json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>Analyze system performance</USER_REQUEST>"}),
        ]
        with open(t_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        title, ws = agy_artifacts.extract_prompt_from_transcript(t_path)
        self.assertEqual(title, "Analyze system performance")


class TestDiscoveryAndFiltering(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_discovery_")
        self.brain_dir = os.path.join(self.test_dir, "brain")
        self.db_path = os.path.join(self.test_dir, "conversation_summaries.db")
        os.makedirs(self.brain_dir)

        # Create SQLite DB
        con = sqlite3.connect(self.db_path)
        con.execute("""
            CREATE TABLE conversation_summaries (
                conversation_id text PRIMARY KEY,
                title text,
                preview text,
                workspace_uris text,
                last_modified_time datetime
            )
        """)
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
            ("cid-alpha", "Alpha Migration", "", json.dumps(["file:///projects/alpha-service"]), "2026-08-23 10:00:00")
        )
        con.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
            ("cid-beta", "Beta Optimization", "", json.dumps(["file:///projects/beta-service"]), "2026-08-23 11:00:00")
        )
        con.commit()
        con.close()

        # Populate brain files
        # CID Alpha
        alpha_dir = os.path.join(self.brain_dir, "cid-alpha")
        os.makedirs(alpha_dir)
        p1 = os.path.join(alpha_dir, "migration_plan.md")
        with open(p1, "w") as f:
            f.write("# Migration Strategy for Alpha\nDetails...\n")
        with open(p1 + ".metadata.json", "w") as f:
            json.dump({"summary": "Alpha database migration step-by-step", "requestFeedback": True}, f)
        os.utime(p1, (time.time() - 100, time.time() - 100))

        # Nested artifact in alpha
        scratch_dir = os.path.join(alpha_dir, "scratch")
        os.makedirs(scratch_dir)
        p2 = os.path.join(scratch_dir, "migrate_helper.py")
        with open(p2, "w") as f:
            f.write("def run(): pass\n")
        os.utime(p2, (time.time() - 50, time.time() - 50))

        # CID Beta
        beta_dir = os.path.join(self.brain_dir, "cid-beta")
        os.makedirs(beta_dir)
        p3 = os.path.join(beta_dir, "perf_benchmark.json")
        with open(p3, "w") as f:
            f.write('{"latency_ms": 12.4}\n')
        with open(p3 + ".metadata.json", "w") as f:
            json.dump({"summary": "Latency measurements for beta"}, f)
        os.utime(p3, (time.time() - 10, time.time() - 10))

        # Skipped files and dirs
        sys_gen = os.path.join(beta_dir, ".system_generated")
        os.makedirs(sys_gen)
        with open(os.path.join(sys_gen, "log.txt"), "w") as f:
            f.write("internal log")

        pycache = os.path.join(alpha_dir, "__pycache__")
        os.makedirs(pycache)
        with open(os.path.join(pycache, "mod.cpython-314.pyc"), "w") as f:
            f.write("bytecode")

        with open(os.path.join(beta_dir, "temp.log"), "w") as f:
            f.write("log file")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_discover_all_artifacts(self):
        arts = agy_artifacts.discover_artifacts(
            brain_dir=self.brain_dir,
            db_path=self.db_path,
        )
        self.assertEqual(len(arts), 3)
        # Newest first: p3 (perf_benchmark.json) -> p2 (migrate_helper.py) -> p1 (migration_plan.md)
        self.assertEqual(arts[0].filename, "perf_benchmark.json")
        self.assertEqual(arts[0].index, 1)
        self.assertEqual(arts[0].workspace_short, "beta-service")

        self.assertEqual(arts[1].filename, "scratch/migrate_helper.py")
        self.assertEqual(arts[1].index, 2)

        self.assertEqual(arts[2].filename, "migration_plan.md")
        self.assertEqual(arts[2].heading, "Migration Strategy for Alpha")
        self.assertEqual(arts[2].summary, "Alpha database migration step-by-step")
        self.assertTrue(arts[2].request_feedback)

    def test_filter_by_workspace(self):
        arts = agy_artifacts.discover_artifacts(
            brain_dir=self.brain_dir,
            db_path=self.db_path,
            workspace_filter="alpha",
        )
        self.assertEqual(len(arts), 2)
        filenames = [a.filename for a in arts]
        self.assertIn("scratch/migrate_helper.py", filenames)
        self.assertIn("migration_plan.md", filenames)

    def test_filter_by_search_query(self):
        arts = agy_artifacts.discover_artifacts(
            brain_dir=self.brain_dir,
            db_path=self.db_path,
            search_query="strategy",
        )
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0].filename, "migration_plan.md")

    def test_limit_artifacts(self):
        arts = agy_artifacts.discover_artifacts(
            brain_dir=self.brain_dir,
            db_path=self.db_path,
            limit=2,
        )
        self.assertEqual(len(arts), 2)

    def test_default_limit_is_30(self):
        self.assertEqual(agy_artifacts.DEFAULT_LIMIT, 30)

    def test_artifact_to_dict_and_human_formatters(self):
        arts = agy_artifacts.discover_artifacts(
            brain_dir=self.brain_dir,
            db_path=self.db_path,
        )
        top = arts[0]
        d = top.to_dict()
        self.assertEqual(d["index"], 1)
        self.assertEqual(d["filename"], "perf_benchmark.json")
        self.assertIn("age_human", d)
        self.assertIn("size_human", d)
        self.assertIn("mtime_iso", d)


class TestCliDispatchAndOutput(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_cli_")
        self.brain_dir = os.path.join(self.test_dir, "brain")
        self.db_path = os.path.join(self.test_dir, "conversation_summaries.db")
        os.makedirs(self.brain_dir)

        # Single artifact setup
        cid_dir = os.path.join(self.brain_dir, "cid-test")
        os.makedirs(cid_dir)
        self.plan_path = os.path.join(cid_dir, "arch_plan.md")
        with open(self.plan_path, "w") as f:
            f.write("# System Architecture Plan\nContent of plan\n")

        # Set environment variables for isolation
        self.patcher_env = patch.dict(os.environ, {
            "AGY_BRAIN_DIR": self.brain_dir,
            "AGY_SUMMARIES_DB": self.db_path,
            "NO_COLOR": "1",
        })
        self.patcher_env.start()

    def tearDown(self):
        self.patcher_env.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cli_json_mode(self):
        captured_out = StringIO()
        with patch("sys.stdout", captured_out):
            code = agy_artifacts.main(["--json"])
        self.assertEqual(code, 0)
        data = json.loads(captured_out.getvalue())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["filename"], "arch_plan.md")
        self.assertEqual(data[0]["heading"], "System Architecture Plan")

    def test_cli_raw_path(self):
        captured_out = StringIO()
        with patch("sys.stdout", captured_out):
            code = agy_artifacts.main(["--raw-path", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(captured_out.getvalue().strip(), self.plan_path)

    def test_cli_raw_path_out_of_range(self):
        captured_err = StringIO()
        with patch("sys.stderr", captured_err):
            code = agy_artifacts.main(["--raw-path", "10"])
        self.assertEqual(code, 1)
        self.assertIn("out of range", captured_err.getvalue())

    def test_cli_cat_mode(self):
        captured_out = StringIO()
        with patch("sys.stdout", captured_out):
            code = agy_artifacts.main(["-c", "1"])
        self.assertEqual(code, 0)
        self.assertIn("# System Architecture Plan", captured_out.getvalue())

    def test_cli_list_table_mode(self):
        captured_out = StringIO()
        with patch("sys.stdout", captured_out):
            code = agy_artifacts.main(["-l"])
        self.assertEqual(code, 0)
        out = captured_out.getvalue()
        self.assertIn("arch_plan.md", out)
        self.assertIn("System Architecture Plan", out)

    def test_cli_open_dispatch(self):
        with patch.object(agy_artifacts, "open_artifact", return_value=0) as mock_open:
            code = agy_artifacts.main(["1"])
            self.assertEqual(code, 0)
            mock_open.assert_called_once_with(self.plan_path, edit=False)

    def test_cli_edit_dispatch(self):
        with patch.object(agy_artifacts, "open_artifact", return_value=0) as mock_open:
            code = agy_artifacts.main(["-e", "1"])
            self.assertEqual(code, 0)
            mock_open.assert_called_once_with(self.plan_path, edit=True)

    def test_cli_space_flag_short(self):
        with patch.object(agy_artifacts, "open_artifact_in_herdr_space", return_value=True) as mock_space:
            captured_out = StringIO()
            with patch("sys.stdout", captured_out):
                code = agy_artifacts.main(["-S", "1"])
            self.assertEqual(code, 0)
            mock_space.assert_called_once()
            self.assertEqual(mock_space.call_args[0][0].filename, "arch_plan.md")
            self.assertFalse(mock_space.call_args[1]["edit"])
            self.assertIn("Opened arch_plan.md in new Herdr space (viewer).", captured_out.getvalue())

    def test_cli_space_flag_long(self):
        with patch.object(agy_artifacts, "open_artifact_in_herdr_space", return_value=True) as mock_space:
            captured_out = StringIO()
            with patch("sys.stdout", captured_out):
                code = agy_artifacts.main(["--space", "1"])
            self.assertEqual(code, 0)
            mock_space.assert_called_once()
            self.assertEqual(mock_space.call_args[0][0].filename, "arch_plan.md")
            self.assertFalse(mock_space.call_args[1]["edit"])

    def test_cli_space_edit_flag(self):
        with patch.object(agy_artifacts, "open_artifact_in_herdr_space", return_value=True) as mock_space:
            captured_out = StringIO()
            with patch("sys.stdout", captured_out):
                code = agy_artifacts.main(["--space-edit", "1"])
            self.assertEqual(code, 0)
            mock_space.assert_called_once()
            self.assertEqual(mock_space.call_args[0][0].filename, "arch_plan.md")
            self.assertTrue(mock_space.call_args[1]["edit"])
            self.assertIn("Opened arch_plan.md in new Herdr space (editor).", captured_out.getvalue())

    def test_cli_space_failure(self):
        with patch.object(agy_artifacts, "open_artifact_in_herdr_space", return_value=False) as mock_space:
            captured_err = StringIO()
            with patch("sys.stderr", captured_err):
                code = agy_artifacts.main(["-S", "1"])
            self.assertEqual(code, 1)
            self.assertIn("Failed to open", captured_err.getvalue())

    def test_empty_brain_handling(self):
        empty_dir = os.path.join(self.test_dir, "empty_brain")
        os.makedirs(empty_dir)
        with patch.dict(os.environ, {"AGY_BRAIN_DIR": empty_dir}):
            captured_out = StringIO()
            with patch("sys.stdout", captured_out):
                code = agy_artifacts.main(["-l"])
            self.assertEqual(code, 0)
            self.assertIn("No artifacts found.", captured_out.getvalue())


class TestOpenerAndTui(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_tui_")
        self.file_path = os.path.join(self.test_dir, "doc.md")
        with open(self.file_path, "w") as f:
            f.write("# Hello World\nLine 2\n")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_open_artifact_missing_file(self):
        with patch("sys.stderr", StringIO()):
            code = agy_artifacts.open_artifact(os.path.join(self.test_dir, "nonexistent.md"))
        self.assertEqual(code, 1)

    def test_open_artifact_with_editor(self):
        with patch.dict(os.environ, {"EDITOR": "custom_editor", "PAGER": "", "VIEWER": ""}):
            with patch("subprocess.call", return_value=0) as mock_call:
                code = agy_artifacts.open_artifact(self.file_path, edit=True)
                self.assertEqual(code, 0)
                mock_call.assert_called_once_with(["custom_editor", self.file_path])

    def test_open_artifact_with_viewer(self):
        mock_proc = MagicMock(returncode=0)
        with patch.dict(os.environ, {"VIEWER": "custom_viewer", "PAGER": ""}):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                code = agy_artifacts.open_artifact(self.file_path, edit=False)
                self.assertEqual(code, 0)
                mock_popen.assert_called_once()
                self.assertEqual(mock_popen.call_args[0][0], ["custom_viewer"])
                mock_proc.communicate.assert_called_once()

    def test_open_artifact_with_pager(self):
        mock_proc = MagicMock(returncode=0)
        with patch.dict(os.environ, {"PAGER": "custom_pager", "VIEWER": ""}):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                code = agy_artifacts.open_artifact(self.file_path, edit=False)
                self.assertEqual(code, 0)
                mock_popen.assert_called_once()
                self.assertEqual(mock_popen.call_args[0][0], ["custom_pager"])
                mock_proc.communicate.assert_called_once()

    def test_open_artifact_with_less_pipe(self):
        def fake_which(cmd):
            return "/usr/bin/less" if cmd == "less" else None

        mock_proc = MagicMock(returncode=0)
        with patch.dict(os.environ, {"PAGER": "", "VIEWER": ""}):
            with patch("shutil.which", side_effect=fake_which):
                with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                    code = agy_artifacts.open_artifact(self.file_path, edit=False)
                    self.assertEqual(code, 0)
                    mock_popen.assert_called_once_with(["less", "-R", "-i"], stdin=subprocess.PIPE, env=mock_popen.call_args[1]["env"])
                    mock_proc.communicate.assert_called_once()

    def test_render_markdown_ansi_with_rich_theme(self):
        content = "# Title Heading\n`code_snippet()`\n```python\nx = 10\n```"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_COLOR", None)
            rendered = agy_artifacts.render_markdown_ansi(content, use_color=True)
        self.assertIn("\x1b[", rendered)
        self.assertIn("Title Heading", rendered)

    def test_cat_artifact(self):
        captured_out = StringIO()
        with patch("sys.stdout", captured_out):
            code = agy_artifacts.cat_artifact(self.file_path)
            self.assertEqual(code, 0)
            self.assertIn("Hello World", captured_out.getvalue())

    def test_curses_tui_quit(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.return_value = ord('q')

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )

        action, path = agy_artifacts._curses_tui_main(stdscr, [art])
        self.assertEqual(action, "quit")
        self.assertIsNone(path)

    def test_curses_tui_select_open(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.return_value = ord('o')

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )

        action, path = agy_artifacts._curses_tui_main(stdscr, [art])
        self.assertEqual(action, "open")
        self.assertEqual(path, self.file_path)

    def test_curses_tui_select_edit(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.return_value = ord('e')

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )

        action, path = agy_artifacts._curses_tui_main(stdscr, [art])
        self.assertEqual(action, "edit")
        self.assertEqual(path, self.file_path)

    def test_curses_tui_select_cat(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.return_value = ord('c')

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )

        action, path = agy_artifacts._curses_tui_main(stdscr, [art])
        self.assertEqual(action, "cat")
        self.assertEqual(path, self.file_path)

    def test_render_curses_md_line(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        # Heading
        agy_artifacts._render_curses_md_line(stdscr, 0, 0, "# Title", 80, True)
        stdscr.addnstr.assert_called()

        # Bullet
        agy_artifacts._render_curses_md_line(stdscr, 1, 0, "- Item", 80, True)
        # Code
        agy_artifacts._render_curses_md_line(stdscr, 2, 0, "```python", 80, True)
        # Quote
        agy_artifacts._render_curses_md_line(stdscr, 3, 0, "> Quote", 80, True)

    def test_safe_addstr_bounds_and_error_suppression(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)

        # Normal write
        agy_artifacts._safe_addstr(stdscr, 0, 0, "Hello", 80)
        stdscr.addnstr.assert_called_with(0, 0, "Hello", 80)

        # Out of bounds y
        stdscr.reset_mock()
        agy_artifacts._safe_addstr(stdscr, 25, 0, "Out", 80)
        stdscr.addnstr.assert_not_called()

        # Out of bounds x
        stdscr.reset_mock()
        agy_artifacts._safe_addstr(stdscr, 0, 85, "Out", 80)
        stdscr.addnstr.assert_not_called()

        # Error suppression on bottom-right corner ERR
        stdscr.reset_mock()
        stdscr.addnstr.side_effect = Exception("addnwstr() returned ERR")
        # Should not raise exception
        agy_artifacts._safe_addstr(stdscr, 23, 0, "x" * 80, 80)

    def test_curses_tui_select_space_viewer(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.side_effect = [ord('s'), ord('q')]

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )

        with patch.object(agy_artifacts, "open_artifact_in_herdr_space", return_value=True) as mock_space:
            action, path = agy_artifacts._curses_tui_main(stdscr, [art])
            self.assertEqual(action, "quit")
            mock_space.assert_called_once_with(art, edit=False)

    def test_curses_tui_select_space_editor(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.side_effect = [ord('W'), ord('q')]

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )

        with patch.object(agy_artifacts, "open_artifact_in_herdr_space", return_value=True) as mock_space:
            action, path = agy_artifacts._curses_tui_main(stdscr, [art])
            self.assertEqual(action, "quit")
            mock_space.assert_called_once_with(art, edit=True)

    def test_curses_full_view(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.return_value = ord('q')

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )
        agy_artifacts._curses_full_view(stdscr, art, True)
        stdscr.refresh.assert_called()

    def test_curses_full_view_keys_s_and_W(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        stdscr.getch.side_effect = [ord('s'), ord('W'), ord('q')]

        art = agy_artifacts.Artifact(
            index=1,
            path=self.file_path,
            filename="doc.md",
            cid="cid-1",
            session_title="Test Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Hello World",
        )

        with patch.object(agy_artifacts, "open_artifact_in_herdr_space", return_value=True) as mock_space:
            agy_artifacts._curses_full_view(stdscr, art, True)
            self.assertEqual(mock_space.call_count, 2)
            mock_space.assert_any_call(art, edit=False)
            mock_space.assert_any_call(art, edit=True)


class TestHerdrSpaceOpener(unittest.TestCase):
    def setUp(self):
        self.art = agy_artifacts.Artifact(
            index=1,
            path="/path/to/my_artifact.md",
            filename="my_artifact.md",
            cid="cid-123",
            session_title="Test Session",
            workspace="/path/to/my-app",
            mtime=time.time(),
            size=1234,
            heading="Test Heading",
        )

    def test_open_herdr_space_none_or_invalid(self):
        self.assertFalse(agy_artifacts.open_artifact_in_herdr_space(None))
        invalid_art = agy_artifacts.Artifact(1, "", "f", "c", "t", "w", 0, 0)
        self.assertFalse(agy_artifacts.open_artifact_in_herdr_space(invalid_art))

    @patch("os.path.exists")
    @patch("socket.socket")
    @patch("shutil.which")
    def test_open_herdr_space_socket_success_viewer(self, mock_which, mock_socket_cls, mock_exists):
        mock_exists.return_value = True
        mock_which.side_effect = lambda cmd: "/usr/bin/glow" if cmd == "glow" else None

        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recv.side_effect = [
            json.dumps({"result": {"root_pane": {"pane_id": "pane-101"}}}).encode("utf-8"),
            json.dumps({"result": {"type": "ok"}}).encode("utf-8"),
        ]

        ok = agy_artifacts.open_artifact_in_herdr_space(self.art, edit=False)
        self.assertTrue(ok)

        sent_calls = mock_sock.sendall.call_args_list
        self.assertEqual(len(sent_calls), 2)
        create_req = json.loads(sent_calls[0][0][0].decode("utf-8").strip())
        self.assertEqual(create_req["method"], "workspace.create")
        self.assertEqual(create_req["params"]["label"], "art:my_artifact.md")
        self.assertEqual(create_req["params"]["cwd"], "/path/to/my-app")
        self.assertTrue(create_req["params"]["focus"])

        send_req = json.loads(sent_calls[1][0][0].decode("utf-8").strip())
        self.assertEqual(send_req["method"], "pane.send_text")
        self.assertEqual(send_req["params"]["pane_id"], "pane-101")
        self.assertEqual(send_req["params"]["text"], "glow -p /path/to/my_artifact.md\n")

    @patch("os.path.exists")
    @patch("socket.socket")
    def test_open_herdr_space_socket_success_editor(self, mock_socket_cls, mock_exists):
        mock_exists.return_value = True
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recv.side_effect = [
            json.dumps({"result": {"root_pane": {"pane_id": "pane-202"}}}).encode("utf-8"),
            json.dumps({"result": {"type": "ok"}}).encode("utf-8"),
        ]

        with patch.dict(os.environ, {"EDITOR": "nvim"}):
            ok = agy_artifacts.open_artifact_in_herdr_space(self.art, edit=True)
            self.assertTrue(ok)

        sent_calls = mock_sock.sendall.call_args_list
        self.assertEqual(len(sent_calls), 2)
        send_req = json.loads(sent_calls[1][0][0].decode("utf-8").strip())
        self.assertEqual(send_req["method"], "pane.send_text")
        self.assertEqual(send_req["params"]["pane_id"], "pane-202")
        self.assertEqual(send_req["params"]["text"], "nvim /path/to/my_artifact.md\n")

    @patch("os.path.exists", return_value=True)
    @patch.object(agy_artifacts, "herdr_socket_request")
    def test_open_herdr_space_quotes_nasty_paths(self, mock_req, mock_exists):
        """Regression: paths containing single quotes must be shlex-quoted, not
        naively wrapped in single quotes (which broke the pane command)."""
        mock_req.side_effect = [
            {"result": {"root_pane": {"pane_id": "%5"}}},
            {"result": {"type": "ok"}},
        ]
        art = agy_artifacts.Artifact(
            index=1, path="/tmp/it's a test.md", filename="it's a test.md",
            cid="c", session_title="", workspace="/ws", mtime=0, size=10,
        )
        ok = agy_artifacts.open_artifact_in_herdr_space(art, edit=False)
        self.assertTrue(ok)
        sent_text = mock_req.call_args_list[-1][0][1]["params"]["text"]
        self.assertIn(shlex.quote(art.path), sent_text)
        self.assertNotIn("'it's a test.md'", sent_text)

    @patch("os.path.exists", return_value=False)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_open_herdr_space_cli_fallback(self, mock_which, mock_run, mock_exists):
        mock_which.side_effect = lambda cmd: "/usr/bin/bat" if cmd == "bat" else None

        proc_create = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": {"root_pane": {"pane_id": "pane-cli-303"}}}),
        )
        proc_send = MagicMock(returncode=0, stdout="")
        mock_run.side_effect = [proc_create, proc_send]

        ok = agy_artifacts.open_artifact_in_herdr_space(self.art, edit=False)
        self.assertTrue(ok)

        self.assertEqual(mock_run.call_count, 2)
        create_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(
            create_args,
            [
                "herdr",
                "workspace",
                "create",
                "--label",
                "art:my_artifact.md",
                "--cwd",
                "/path/to/my-app",
                "--focus",
            ],
        )

        send_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(
            send_args,
            [
                "herdr",
                "pane",
                "send-text",
                "pane-cli-303",
                "bat --style=plain /path/to/my_artifact.md\n",
            ],
        )

    @patch("os.path.exists", return_value=False)
    @patch("subprocess.run")
    def test_open_herdr_space_cli_create_failure(self, mock_run, mock_exists):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="workspace error")
        ok = agy_artifacts.open_artifact_in_herdr_space(self.art, edit=False)
        self.assertFalse(ok)

    @patch("os.path.exists", return_value=False)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_open_herdr_space_viewers_selection(self, mock_which, mock_run, mock_exists):
        # 1. Art/agy-artifacts view fallback when glow/bat not installed
        mock_which.side_effect = lambda cmd: "/usr/local/bin/art" if cmd == "art" else None
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=json.dumps({"result": {"root_pane": {"pane_id": "pane-1"}}}),
            ),
            MagicMock(returncode=0),
        ]
        agy_artifacts.open_artifact_in_herdr_space(self.art, edit=False)
        self.assertIn("art --view", mock_run.call_args_list[1][0][0][4])

        # 2. Python view fallback when no art binary on PATH
        mock_which.side_effect = lambda cmd: None
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=json.dumps({"result": {"root_pane": {"pane_id": "pane-2"}}}),
            ),
            MagicMock(returncode=0),
        ]
        agy_artifacts.open_artifact_in_herdr_space(self.art, edit=False)
        self.assertIn("--view", mock_run.call_args_list[3][0][0][4])


class TestCursesKeyDecoding(unittest.TestCase):
    def test_decode_standard_keys(self):
        mock_stdscr = MagicMock()
        # Normal curses keys
        if hasattr(agy_artifacts.curses, "KEY_UP"):
            self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, agy_artifacts.curses.KEY_UP), "UP")
            self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, agy_artifacts.curses.KEY_DOWN), "DOWN")
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, ord("k")), "UP")
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, ord("j")), "DOWN")
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, ord("q")), "QUIT")
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, 10), "ENTER")

    def test_decode_escape_sequences(self):
        mock_stdscr = MagicMock()

        # Up arrow sequence \x1b[A
        mock_stdscr.getch.side_effect = [ord("["), ord("A")]
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, 27), "UP")

        # Down arrow sequence \x1b[B
        mock_stdscr.getch.side_effect = [ord("["), ord("B")]
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, 27), "DOWN")

        # Application keypad sequence \x1bOA / \x1bOB
        mock_stdscr.getch.side_effect = [ord("O"), ord("A")]
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, 27), "UP")
        mock_stdscr.getch.side_effect = [ord("O"), ord("B")]
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, 27), "DOWN")

        # Standalone Esc
        mock_stdscr.getch.side_effect = [-1]
        self.assertEqual(agy_artifacts._decode_curses_key(mock_stdscr, 27), "ESC")


class TestInteractiveNavigationFlow(unittest.TestCase):
    def setUp(self):
        self.art1 = agy_artifacts.Artifact(
            index=1,
            path="/tmp/art1.md",
            filename="art1.md",
            cid="cid-1",
            session_title="First Session",
            workspace="/workspace/test",
            mtime=time.time(),
            size=100,
            heading="Doc 1",
        )
        self.art2 = agy_artifacts.Artifact(
            index=2,
            path="/tmp/art2.md",
            filename="art2.md",
            cid="cid-2",
            session_title="Second Session",
            workspace="/workspace/test",
            mtime=time.time() - 10,
            size=200,
            heading="Doc 2",
        )

    def test_curses_tui_arrow_navigation_and_select(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        # Navigate DOWN (j or KEY_DOWN), then ENTER (10)
        stdscr.getch.side_effect = [ord('j'), 10]

        action, path = agy_artifacts._curses_tui_main(stdscr, [self.art1, self.art2])
        self.assertEqual(action, "open")
        self.assertEqual(path, "/tmp/art2.md")

    def test_curses_tui_arrow_navigation_up_and_select(self):
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (24, 80)
        # Navigate DOWN, UP, then ENTER -> selects art1
        stdscr.getch.side_effect = [ord('j'), ord('k'), 10]

        action, path = agy_artifacts._curses_tui_main(stdscr, [self.art1, self.art2])
        self.assertEqual(action, "open")
        self.assertEqual(path, "/tmp/art1.md")

    @patch("termios.tcgetattr")
    @patch("termios.tcsetattr")
    @patch("tty.setcbreak")
    @patch("os.isatty", return_value=True)
    def test_raw_tty_menu_navigation(self, mock_isatty, mock_setcbreak, mock_tcset, mock_tcget):
        with patch("sys.stdin.read", side_effect=["j", "\n"]), \
             patch("sys.stdout.write") as mock_write:
            action, path = agy_artifacts._raw_tty_interactive_menu([self.art1, self.art2])
            self.assertEqual(action, "open")
            self.assertEqual(path, "/tmp/art2.md")

    @patch.object(agy_artifacts, "open_artifact", return_value=0)
    @patch("os.path.isfile", return_value=True)
    def test_cli_view_direct_filepath(self, mock_isfile, mock_open):
        code = agy_artifacts.main(["--view", "/path/to/my_doc.md"])
        self.assertEqual(code, 0)
        mock_open.assert_called_once_with("/path/to/my_doc.md", edit=False)

    @patch.object(agy_artifacts, "open_artifact", return_value=0)
    @patch("os.path.isfile", return_value=True)
    def test_cli_target_direct_filepath(self, mock_isfile, mock_open):
        code = agy_artifacts.main(["/path/to/my_doc.md"])
        self.assertEqual(code, 0)
        mock_open.assert_called_once_with("/path/to/my_doc.md", edit=False)


if __name__ == "__main__":
    unittest.main()


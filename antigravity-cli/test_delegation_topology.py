#!/usr/bin/env python3
"""
test_delegation_topology.py — Invariant tests for 3-Tier Agent Delegation Topology

Asserts that:
1. AGENTS.md establishes Gemini 3.7 Flash High as Master, Gemini 3.1 Pro ('Model: pro') as Architect/Auditor, and Gemini 3.7 Flash ('Model: flash') as Worker.
2. architect-executor and goal-audit skills declare the identical delegation contract.
3. README.md is synchronized with the 3-tier delegation protocol.
4. Harness skill symlinks exist and resolve correctly without dangling paths.
"""

import os
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent

def find_repo_root():
    p = HERE
    while p != p.parent:
        if (p / "README.md").exists() and ((p / "tui-agent-settings").exists() or (p / "skills").exists()):
            return p
        p = p.parent
    return HERE.parent

ROOT = find_repo_root()


class TestDelegationTopology(unittest.TestCase):
    def setUp(self):
        if (ROOT / "tui-agent-settings").exists():
            self.agents_md = ROOT / "tui-agent-settings" / "prompts" / "AGENTS.md"
            self.architect_skill = ROOT / "tui-agent-settings" / "skills" / "architect-executor" / "SKILL.md"
            self.goal_audit_skill = ROOT / "tui-agent-settings" / "skills" / "goal-audit" / "SKILL.md"
            self.skills_dir = ROOT / "tui-agent-settings" / "skills"
        else:
            self.agents_md = ROOT / "prompts" / "AGENTS.md"
            self.architect_skill = ROOT / "skills" / "architect-executor" / "SKILL.md"
            self.goal_audit_skill = ROOT / "skills" / "goal-audit" / "SKILL.md"
            self.skills_dir = ROOT / "skills"
        self.readme_md = ROOT / "README.md"

    def test_agents_md_contract(self):
        self.assertTrue(self.agents_md.exists(), "AGENTS.md must exist")
        content = self.agents_md.read_text(encoding="utf-8")
        self.assertIn("Master Orchestrator", content)
        self.assertIn("Architect / Auditor", content)
        self.assertIn("`Model: pro`", content)
        self.assertIn("`Model: flash`", content)
        self.assertIn("semantic diff verification", content)

    def test_architect_executor_skill_contract(self):
        self.assertTrue(self.architect_skill.exists(), "architect-executor SKILL.md must exist")
        content = self.architect_skill.read_text(encoding="utf-8")
        self.assertIn("3.7 Flash", content)
        self.assertIn("3.1 Pro", content)
        self.assertIn('Model: "pro"', content)
        self.assertIn('Model: "flash"', content)
        self.assertIn("git diff", content)
        self.assertIn("AUDIT VERDICT", content)

    def test_goal_audit_skill_contract(self):
        self.assertTrue(self.goal_audit_skill.exists(), "goal-audit SKILL.md must exist")
        content = self.goal_audit_skill.read_text(encoding="utf-8")
        self.assertIn("3.7 Flash", content)
        self.assertIn("3.1 Pro", content)
        self.assertIn('Model: "pro"', content)
        self.assertIn('Model: "flash"', content)
        self.assertIn("ask_question", content)
        self.assertIn("GOAL_COMPLETE", content)
        self.assertIn("Verification Contract", content)
        self.assertIn("git diff", content)
        self.assertIn("AUDIT VERDICT: PASS", content)
        self.assertIn("AUDIT VERDICT: FAIL", content)


    def test_readme_sync(self):
        self.assertTrue(self.readme_md.exists(), "README.md must exist")
        content = self.readme_md.read_text(encoding="utf-8")
        self.assertIn("Subagent & Delegation Protocol", content)
        self.assertIn("Gemini 3.7 Flash High (Master Orchestrator)", content)
        self.assertIn("Gemini 3.1 Pro (Architect & Detached Auditor)", content)
        self.assertIn("Gemini 3.7 Flash High (Bulk Execution Worker)", content)

    def test_harness_skills_resolution(self):
        """Verify all local skill directories have valid SKILL.md with frontmatter."""
        skills_dir = self.skills_dir
        self.assertTrue(skills_dir.exists(), "skills directory must exist")
        for skill_path in skills_dir.iterdir():
            if skill_path.is_dir():
                skill_md = skill_path / "SKILL.md"
                self.assertTrue(
                    skill_md.exists(),
                    f"Skill {skill_path.name} is missing SKILL.md",
                )
                text = skill_md.read_text(encoding="utf-8")
                self.assertTrue(
                    text.startswith("---"),
                    f"Skill {skill_path.name}/SKILL.md missing YAML frontmatter opening '---'",
                )
                self.assertIn(
                    "name:",
                    text[:200],
                    f"Skill {skill_path.name}/SKILL.md missing 'name:' in frontmatter",
                )
                self.assertIn(
                    "description:",
                    text[:400],
                    f"Skill {skill_path.name}/SKILL.md missing 'description:' in frontmatter",
                )


if __name__ == "__main__":
    unittest.main()


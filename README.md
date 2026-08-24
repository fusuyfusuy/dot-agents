# dot-agents

> Cross-agent operating system, zero-daemon symbol memory (mimori), and tools for Claude Code, Antigravity, Pi, and OpenCode.

Unified cross-agent operating system, shared operating rules, zero-daemon AST project memory ([`mimori`](skills/mimori)), Mosh-optimized alert dispatching, live quota telemetry, and AST-powered surgical tools across **Claude Code**, **Antigravity / Gemini CLI**, **pi**, and **OpenCode**.

---

## ⚡ Quickstart

Run the interactive setup script to configure your installed agents:

```bash
git clone https://github.com/fusuyfusuy/dot-agents.git ~/.agents-suite
cd ~/.agents-suite
./setup.sh
```

`setup.sh` auto-detects installed coding agents on your machine and interactively symlinks prompts, skills, extensions, and plugins.

---

## 📦 What's Included

### 1. `mimori` — Zero-Daemon Project Context & AST Symbol Map
Zero-daemon, stdlib-only project memory and ranked repository symbol mapper:
- `mimori init`: Initialize project memory (`memory.md`, `decisions.md`, `activity.jsonl`) with auto `git init`.
- `mimori dump --file`: Fast snapshot written to user-isolated temp (`$XDG_RUNTIME_DIR/mimori/ctx-<repo>-<commit>.md`).
- `mimori map`: Live PageRank + AST import-graph repository symbol orientation.
- `mimori log`: Machine-action telemetry recording.

### 2. Core Skills & Protocols
- **`AGENTS.md`**: Standardized pair-programming principles (Explore → Plan → Approve → Execute, Ponytail Lazy Senior Dev ladder, 3-Tier delegation topology).
- **`ponytail-debt`**: Deferral comment harvester and technical debt ledger auditor.
- **`code-summary`**: Think-in-code AST and repository inventory one-liners.
- **`agent-processes`**: Git worktree isolation recipes for multi-agent workflows.
- **`architect-executor`**: High-throughput orchestration topology.
- **`goal-audit`**: Autonomous task verification and anti-bamboozle auditing.
- **`pi-ast`**: Surgical AST structural refactoring and blast-radius analyzer.

### 3. Subagent & Delegation Protocol (3-Tier Topology)
- **Gemini 3.7 Flash High (Master Orchestrator)**: Rapid conversational turn pacing, triage, tool orchestration, and delegation.
- **Gemini 3.1 Pro (Architect & Detached Auditor)**: Deep architecture, contract design, and detached `/goal` audits.
- **Gemini 3.7 Flash High (Bulk Execution Worker)**: Mechanical code generation, high-speed edits, and verification execution.

### 4. Agent Integrations & Extensions
- **Claude Code**: Fast statusline hook and automated workspace state tracker.
- **Antigravity / Gemini CLI**: Artifact TUI inspector (`agy-artifacts.py`), MCP configuration, and checkpoint hooks.
- **pi**: Thinking level indicators, inference speed meters, timestamp logging, and tool gating.
- **tmux**: Live subscription quota monitor (`tmux-agent-quotas`) and idle alert notifications (`tmux-agent-alert`).

---

## 🙏 Acknowledgements & Kudos

This suite and its zero-daemon tooling stand on the shoulders of brilliant ideas from the developer and agentic coding community:

- **Ponytail (`ponytail`)**: Massive kudos for the **"Lazy Senior Dev"** operating philosophy, the YAGNI decision ladder (delete over add, platform over library, 1-liners over abstractions), and the `# ponytail:` debt ledger protocol.
- **Caveman (`caveman`)**: Special thanks for the **1-line caveman log style** and terse, high-signal communication rules that keep agent memory sharp and fluff-free.
- **Aider (`aider`) & Paul Gauthier**: Deep gratitude for pioneering the use of **PageRank on AST symbol definition and reference graphs** to generate token-budgeted repository maps.
- **Pi / Mario**: Kudos for modular agent extension patterns and ultra-fast, zero-overhead CLI workflows.
- **The Agentic Coding Community**: Thanks to all developers exploring the frontiers of human-agent pair programming and zero-daemon developer tooling.

---

## 📜 License
MIT License.

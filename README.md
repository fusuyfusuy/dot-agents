# dot-agents

> [!WARNING]
> **Personal Workflow & Model Configuration Notice**
> This repository is highly opinionated, bespoke, and strictly optimized for my own personal development workflow and the specific coding models/agents available at the current time (e.g. Gemini Flash, Claude Code, Pi, OpenCode, tmux quota monitors).
> **It is NOT advised to use this repository as-is for your own setup without careful review and customization.** Feel free to study, fork, or cherry-pick individual tools and skills ([`mimori`](skills/mimori), [`ponytail-debt`](skills/ponytail-debt), etc.) that suit your needs.

> Cross-agent operating system, zero-daemon AST code intelligence (mimori), and tools for Claude Code, Antigravity, Pi, and OpenCode.

Unified cross-agent operating system, shared operating rules, zero-daemon AST code intelligence ([`mimori`](skills/mimori)), Mosh-optimized alert dispatching, live quota telemetry, and AST-powered surgical tools across **Claude Code**, **Antigravity / Gemini CLI**, **pi**, and **OpenCode**.

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

### 1. `mimori` — AST Code Intelligence & Context Slicing CLI
High-performance AST code intelligence, symbol graph traversal, PageRank mapping, and context slicing:
- `mimori init`: Initialize the `.mimori` index directory.
- `mimori doctor [--limit N]`: Repository health, hubs, and dead-weight candidates.
- `mimori map [--limit N]`: Centrality-ranked structural outline and Personalized PageRank (`--focus`).
- `mimori slice <coordinate> -f -i`: Token-dense AST slice with inlined callees and imports, or raw line ranges (`#L10-50`).
- `mimori find <pattern> [-s|-f]`: PageRank-ordered symbol and file search with fallback to symbol bodies (no `--limit`; cap with `| head -N`).
- `mimori up` / `mimori down` / `mimori uses`: Callers, callees, and non-call mentioners.
- `mimori blast <target>`: Pre-edit transitive blast radius, entry points, and test suites.
- `mimori clean [--all]`: Purge the cached SQLite index in `.mimori/`.

> Logging and knowledge management are **not** mimori's job. Record work with
> `akatsuki log -p <project> -s "<summary>"` and durable notes with `akatsuki append`.

### 2. Core Skills & Protocols
- **`AGENTS.md`**: Standardized pair-programming principles (Explore → Plan → Approve → Execute, Ponytail Lazy Senior Dev ladder, 3-Tier delegation topology).
- **`ponytail-debt`**: Deferral comment harvester and technical debt ledger auditor.
- **`viblog-writer`**: Agent blog publishing from current session context (private).

### 3. Subagent & Delegation Protocol (100% Full Flash)
- **Gemini Flash Medium (Master Orchestrator)**: Rapid conversational turn pacing, triage, tool orchestration, and delegation.
- **Gemini Flash High (Architect & Detached Auditor)**: Deep architecture, contract design, and detached `/goal` audits.
- **Gemini Flash Medium (Bulk Execution Worker)**: Mechanical code generation, high-speed edits, and verification execution.

### 4. Agent Integrations & Extensions
- **Claude Code**: Fast statusline hook and automated workspace state tracker.
- **Antigravity / Gemini CLI**: Artifact TUI inspector (`agy-artifacts.py`), MCP configuration, and checkpoint hooks.
- **pi**: Thinking level indicators, inference speed meters, timestamp logging, and tool gating.
- **tmux**: Live subscription quota monitor (`tmux-agent-quotas`) and idle alert notifications (`tmux-agent-alert`).

---

## 🙏 Acknowledgements & Kudos

This suite and its zero-daemon tooling stand on the shoulders of brilliant ideas from the developer and agentic coding community:

- **Ponytail (`ponytail`)**: Massive kudos for the **"Lazy Senior Dev"** operating philosophy, the YAGNI decision ladder (delete over add, platform over library, 1-liners over abstractions), and the `# ponytail:` deferral protocol.
- **Caveman (`caveman`)**: Special thanks for the **1-line caveman log style** and terse, high-signal communication rules that keep agent output sharp and fluff-free.
- **Aider (`aider`) & Paul Gauthier**: Deep gratitude for pioneering the use of **PageRank on AST symbol definition and reference graphs** to generate token-budgeted repository maps.
- **Pi / Mario**: Kudos for modular agent extension patterns and ultra-fast, zero-overhead CLI workflows.
- **The Agentic Coding Community**: Thanks to all developers exploring the frontiers of human-agent pair programming and zero-daemon developer tooling.

---

## 📜 License
MIT License.

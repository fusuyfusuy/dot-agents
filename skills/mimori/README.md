# mimori (三森) — Zero-Daemon Agent Memory & Live AST Repository Map

[![CI](https://github.com/fusuyfusuy/mimori/actions/workflows/ci.yml/badge.svg)](https://github.com/fusuyfusuy/mimori/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0%20(stdlib%20only)-green.svg)](https://github.com/fusuyfusuy/mimori)

> **Fast, zero-daemon project orientation, live PageRank AST symbol mapping, ADR tracking, tasklists, and activity telemetry for AI coding agents.**

> [!NOTE]
> **Evolving Skill & Language Coverage**
> `mimori` is an actively evolving agent memory and symbol-mapping skill. It is battle-tested and optimized primarily for the programming languages and tech stacks we use the most: **Python**, **TypeScript / JavaScript** (Next.js, React, Node.js), **Go**, **Rust**, and **Shell / Bash**. Support for additional languages, AST grammars, and framework conventions continues to expand.

`mimori` is a lightweight, zero-dependency CLI tool written in pure Python that lives inside your agent harness or user environment. It gives coding agents (Claude Code, Antigravity, Pi, OpenCode, Aider) instant project memory and structural architectural awareness upon entering any repository — without background servers, vector databases, or bloated indexing daemons.

---

## 📖 About mimori

### Why mimori?
When AI coding agents enter a codebase, they typically suffer from two extremes:
1. **Blind Exploration Waste**: Agents fire dozens of unfocused `grep`, `find`, or `ls -R` commands, burning **20,000–50,000 tokens** before writing a single line of code.
2. **Heavy Daemon Bloat**: External vector databases, LSP daemons, or background indexers consume gigabytes of RAM, fail silently, drift out of sync with git branches, and require complex external dependencies.

`mimori` was created to solve this with a radically minimalist approach: **Deterministic computation over probabilistic retrieval, standard library over external dependencies, and pure markdown stores over opaque vector databases.**

### Core Design Philosophy
- **Zero Daemon, Zero Dependencies**: Written in 100% pure Python standard library (`ast`, `tokenize`, `urllib`, `array`). Single standalone executable. No background processes, no RAM overhead, and no installation hurdles.
- **Compute, Don't Read**: Instead of hallucinating relevance through vector similarity, `mimori` parses ASTs and executes vectorized **PageRank iterations** in microseconds to mathematically calculate which modules serve as the real architectural hubs of your repository.
- **Git-Native Project Memory**: All invariants, architecture decision records (ADRs), in-flight tasks, and activity journals live directly inside `.mimori/` in standard human-readable Markdown and JSON Lines.
- **Strict Character Budgeting & Anti-Decay**: Output scales intelligently with token budgets (`--budget default|large|immense`), gracefully collapsing peripheral modules without silent omissions, and actively warns when file references rot.
- **Ponytail & Caveman DNA**: Built on the **Ponytail** principle of ruthless minimalism (boring over clever, smallest diff wins) and **Caveman** compression (high-signal, zero-filler communication).

### Name Origin
**Mimori (三森)** translates to *"Three Forests"* in Japanese, representing the three foundational layers of structural agent context:
- 🌲 **Memory**: Repository invariants, gotchas, domain rules, and technical debt.
- 🌳 **Structure**: The live AST symbol topology and PageRank-weighted dependency graph.
- 🌴 **Action**: Immutable architecture decisions (ADRs), active task lifecycle, and session activity logs.

---

## ⚡ Key Features

1. **Instant Session Warmup (`mimori dump`)**:
   - Single command outputs **6 vital context layers**: Git working state, active memory invariants & gotchas, architecture decision records (ADRs), in-flight tasks & backlog, PageRank-ranked AST symbol map, and recent session telemetry.
   - Saves **20,000–50,000 tokens** by eliminating blind exploratory grepping and recursive directory tree crawling.

2. **Vectorized Live PageRank AST Symbol Map (`mimori map`)**:
   - Parses AST structures across **Python, TypeScript, JavaScript, Go, Rust, Ruby, C, and C++**.
   - Resolves `tsconfig.json`/`jsconfig.json` path aliases (`@/lib/*`), Go package paths (`go.mod`), and Rust module trees (`Cargo.toml`, `mod.rs`, `use crate::*`).
   - Microsecond power-iteration convergence using flat contiguous integer-indexed arrays (`array.array`).
   - Ranks files and symbols by **import in-degree**, entry-point detection, and recent git churn so the agent sees what actually matters first.
   - Dynamic token-budget management: gracefully collapses lower-ranked directories without silent omission.

3. **Zero-Daemon Task, Todo & Backlog Tracking (`mimori todo` / `mimori idea`)**:
   - Built-in CLI for in-progress tasks (`[/]`), pending action items (`[ ]`), and exploratory future ideas (`[?]`) stored in `.mimori/tasks.md`.
   - Priority tagging (`--prio high|med|low`), component tags (`--tag perf`), lifecycle state transitions (`start`, `done`, `reopen`, `promote`), and token-budgeted snapshot summaries.

4. **Automated Memory & Task Staleness/Rot Decay Scanner**:
   - Scans `.mimori/memory.md` and `.mimori/tasks.md` for dead file paths or nonexistent symbols.
   - Emits non-intrusive decay alerts in `mimori dump` to prompt active pruning before knowledge rots.

5. **Architecture Decision Records (`mimori decisions`)**:
   - Maintains immutable ADRs in `.mimori/decisions.md` following the Context → Decision → Consequences pattern.
   - Automatically surfaces active architectural invariants while keeping superseded decisions compact.

6. **1-Line Caveman Action Logging (`mimori log`)**:
   - Machine-action telemetry recorded into `.mimori/activity.jsonl` with author metadata, modified files, and concise caveman summaries for all repository actions and tooling runs.

7. **Ponytail Technical Debt Scanner & Reconciler (`mimori debt`)**:
   - Zero-daemon 2-pass scanner for in-code `# ponytail:` / `// ponytail:` deferral comments.
   - Parses multi-line ceilings and upgrade triggers, flagging `[no-trigger]` and `[duplicate]` issues.
   - `mimori debt sync`: Synchronizes code markers into `.mimori/memory.md` (`## KNOWN DEBT`), automatically pruning resolved debt while honoring the 30-line cap and preserving manual entries.
   - `mimori debt check`: CI validation gate (exits 0 if clean, 1 if broken triggers exist).

8. **Self-Cleaning Temp Cache (`mimori clean`)**:
   - Opportunistic in-flight garbage collection on `dump --file`: retains the 2 newest snapshots per repo, auto-expires files older than 72h, and caps total temp files.

---

## 📦 Installation

### One-Line Install (curl)
```bash
curl -fsSL https://raw.githubusercontent.com/fusuyfusuy/mimori/main/install.sh | bash
```

### Direct Download / Manual Install
`mimori` is a single standalone script with **zero external dependencies** (Python 3.10+ standard library only):

```bash
curl -fsSL https://raw.githubusercontent.com/fusuyfusuy/mimori/main/mimori -o ~/.local/bin/mimori
chmod +x ~/.local/bin/mimori
```

---

## 🚀 Quickstart

```bash
# Scaffold .mimori/ in the current repository (auto-inits git if missing)
mimori init

# Fast orientation snapshot written to user-isolated temp ($XDG_RUNTIME_DIR/mimori/ctx-<repo>-<commit>.md)
mimori dump --file

# Manage tasks, in-progress work, and todos
mimori todo add "Refactor token cache" --prio high --tag perf
mimori todo add "Implement query engine" --start  # Directly to In Progress ([/])
mimori todo                                      # List active tasks
mimori todo done 1                               # Mark task #1 completed ([x])

# Manage future ideas & proposals
mimori idea add "Explore distributed AST indexing"
mimori idea promote 1                            # Move idea #1 into Active Tasks

# Manage & synchronize in-code ponytail debt
mimori debt                                      # List code-level debt markers
mimori debt sync                                 # Reconcile markers into memory.md
mimori debt check                                # CI validation check

# Generate or refresh repository symbol map
mimori map

# Focused map on a specific subsystem
mimori map --stdout --focus "auth.py,api"

# Record a completed action or tooling execution (1-line caveman style)
mimori log --action "add-auth" --summary "Added JWT auth middleware" --files "auth.py,server.py"

# View recent session history
mimori history --limit 5

# Prune stale snapshot caches or wipe entirely
mimori clean
mimori clean --all
```

---

## 📁 Repository Layout (`.mimori/`)

Inside your workspace, `mimori` stores local state in pure markdown and JSON lines:

```
.mimori/
├── memory.md         # Invariants, gotchas, domain conventions, and open debt ledger
├── decisions.md      # Architecture Decision Records (ADRs)
├── tasks.md          # Active tasks, in-progress work, future ideas, and completed log
├── repo_map.md       # Full PageRank AST symbol graph
└── activity.jsonl    # Machine-readable action and session audit log
```

---

## 🤖 Using with AI Agents (`AGENTS.md` Integration)

`mimori` is designed to be the operational backbone for AI coding agents (Antigravity, Claude Code, Pi, OpenCode, Cursor, Roo Code, Aider).

### 1. Install the Agent Skill
Copy `SKILL.md` into your agent skills directory:

```bash
# Antigravity CLI (agy)
mkdir -p ~/.gemini/antigravity-cli/skills/mimori
cp SKILL.md ~/.gemini/antigravity-cli/skills/mimori/

# Claude Code
mkdir -p ~/.claude/skills/mimori
cp SKILL.md ~/.claude/skills/mimori/

# Pi / OpenCode / Custom Harness
mkdir -p ~/.pi/skills/mimori
cp SKILL.md ~/.pi/skills/mimori/
```

---

### 2. Drop-in `AGENTS.md` / `CLAUDE.md` Settings Section

Paste this configuration into your project root's `AGENTS.md` or `CLAUDE.md` to establish strict memory hygiene, surgical context exploration, and debt governance:

```markdown
## Project Memory & Lifecycle Protocol (mimori)

### 1. Explore -> Plan -> Approve -> Execute -> Verify
- **Explore**: Orient surgically without reading full files.
  - Snapshot full workspace context: `mimori dump --file`
  - Focus on a specific subsystem: `mimori dump --focus "auth,api"`
  - Live AST call/import inspection: `mimori map --stdout --focus "<target>"`
- **Plan**: Track multi-step tasks in `mimori todo` (e.g. `mimori todo add "Refactor parser" --start`).
- **Approve**: Multi-file, API-modifying, or dependency changes require plan review before execution.
- **Execute**: Deliver shortest working diff. Mark intentional shortcuts with `# ponytail: <what> <- <ceiling> -> <trigger>`.
- **Verify & Gate**: Run machine-verifiable tests. Ensure zero broken debt markers with `mimori debt check` (exit 0).
- **Log**: Record completed repository actions via `mimori log --action <act> --summary <caveman> --files <f1,f2>` (<160 chars).

### 2. Session Warmup & Hygiene
- **Warmup**: Run `mimori dump --file` at session start. Never read `.mimori/repo_map.md` directly.
- **Decay Pruning**: Remove stale/dead file references reported in `mimori dump` decay notices from `.mimori/memory.md`.
- **Subagent Kickoff**: Scope worker subagents with targeted context: `mimori dump --file --focus "<area>"`.

### 3. Debt Governance & Memory Writing Style
- **Writing Style**: Use Caveman compression (drop filler/articles, retain exact code, paths, numbers, and negations) in `.mimori/memory.md` and ADRs.
- **Debt Sync**: Reconcile in-code `# ponytail:` markers with `mimori debt sync`; gate CI with `mimori debt check`.
```

---

### 3. Agent Lifecycle Workflow Matrix

| Phase | Agent Action | CLI Command | Context Impact |
| :--- | :--- | :--- | :--- |
| **Session Start** | Cold-start warmup & decay check | `mimori dump --file` | ~12k–24k chars cached in temp (saves 50k tokens) |
| **Subsystem Exploration** | Surgical AST & dependency lookup | `mimori map --stdout --focus "auth"` | Precise top-ranked symbols & callers only |
| **Task Planning** | Create & activate action items | `mimori todo add "<task>" --start` | Structured state in `.mimori/tasks.md` |
| **Subagent Scoping** | Isolated worker kickoff context | `mimori dump --file --focus "<target>"` | Minimal targeted context window |
| **Pre-Commit Verification** | Validate debt triggers | `mimori debt check` | Deterministic CI gate (exit 0 / 1) |
| **Post-Action Journal** | Log discrete repo action | `mimori log --action <a> --summary <s>` | Append to `.mimori/activity.jsonl` |

---

## 🙏 Acknowledgements & Kudos

`mimori` stands on the shoulders of brilliant ideas from the developer and agentic coding community:

- **Ponytail (`ponytail`)**: Huge kudos for the **"Lazy Senior Dev"** operating philosophy, the YAGNI decision ladder (delete over add, platform over library), and the `# ponytail:` debt ledger protocol.
- **Caveman (`caveman`)**: Special thanks for the **1-line caveman log style** and terse, high-signal communication rules that keep agent memory sharp and fluff-free.
- **Aider (`aider`) & Paul Gauthier**: Deep gratitude for pioneering the use of **PageRank on AST symbol definition and reference graphs** to generate token-budgeted repository maps.
- **Pi / Mario**: Kudos for modular agent extension patterns and ultra-fast, zero-overhead CLI workflows.
- **The Agentic Coding Community**: Thanks to all developers exploring the frontiers of human-agent pair programming and zero-daemon developer tooling.

---

## 📜 License
[MIT License](LICENSE) © 2026 Yusuf Akcakaya.

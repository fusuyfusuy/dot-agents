# mimori (三森) — Zero-Daemon Agent Memory & AST Repository Map

> **Fast, zero-daemon project orientation, live PageRank symbol mapping, ADR tracking, and activity telemetry for AI coding agents.**

> [!NOTE]
> **Evolving Skill & Language Coverage**
> `mimori` is an actively evolving agent memory and symbol-mapping skill. It is battle-tested and optimized primarily for the programming languages and tech stacks we use the most: **Python**, **TypeScript / JavaScript** (Next.js, React, Node.js), **Go**, **Rust**, and **Shell / Bash**. Support for additional languages, AST grammars, and framework conventions continues to expand.

`mimori` is a lightweight, zero-dependency CLI tool written in pure Python that lives inside your agent harness. It gives coding agents (Claude Code, Antigravity, Pi, OpenCode) instant project memory and architectural awareness upon entering any repository — without background servers, vector databases, or bloated indexing daemons.

---

## ⚡ Key Features

1. **Instant Session Warmup (`mimori dump`)**:
   - Single command outputs **6 vital context layers**: Git working state, active memory invariants & gotchas, architecture decision records (ADRs), in-flight tasks & backlog, PageRank-ranked AST symbol map, and recent session telemetry.
   - Saves thousands of tokens by replacing broad exploratory grepping and blind directory tree crawling.

2. **Zero-Daemon Task & Backlog Tracking (`mimori todo` / `mimori idea`)**:
   - Built-in CLI for in-progress tasks, pending action items, and exploratory future ideas stored in `.mimori/tasks.md`.
   - Priority tagging (`--prio high|med|low`), component tags (`--tag perf`), lifecycle state transitions (`start`, `done`, `reopen`, `promote`), and token-budgeted snapshot summaries.

3. **Live PageRank AST Symbol Map (`mimori map`)**:
   - Parses AST structures across **Python, TypeScript, JavaScript, Go, Rust, Ruby, C, and C++**.
   - Ranks files and symbols by **import in-degree**, entry-point detection, and recent git churn so the agent sees what actually matters first.
   - Annotates importer relationships (e.g. `← cli, db, api`) directly in the symbol tree.
   - Dynamic token-budget management: gracefully collapses lower-ranked directories without silent omission.

4. **Architecture Decision Records (`mimori decisions`)**:
   - Maintains immutable ADRs in `.mimori/decisions.md` following the Context → Decision → Consequences pattern.
   - Automatically surfaces active architectural invariants while keeping superseded decisions compact.

5. **1-Line Caveman Activity Logging (`mimori log`)**:
   - Machine-action telemetry recorded into `.mimori/activity.jsonl` with author metadata, modified files, and concise caveman summaries.

6. **Debt Ledger Integration**:
   - Tracks open `# ponytail:` deferral comments and technical debt directly in `.mimori/memory.md`.

7. **Self-Cleaning Temp Cache (`mimori clean`)**:
   - Opportunistic in-flight garbage collection on `dump --file`: retains the 2 newest snapshots per repo, auto-expires files older than 72h, and caps total temp files.

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

# Generate or refresh repository symbol map
mimori map

# Focused map on a specific subsystem
mimori map --stdout --focus "auth.py,api"

# Record a completed task (1-line caveman style)
mimori log --action "add-auth" --summary "Added JWT auth middleware" --files "auth.py,server.py"

# View recent session history
mimori history --limit 5

# Prune stale snapshot caches or wipe entirely
mimori clean
mimori clean --all
```

---

## 📁 Repository Layout (`.mimori/`)

```
.mimori/
├── memory.md         # Invariants, gotchas, domain conventions, and open debt ledger
├── decisions.md      # Architecture Decision Records (ADRs)
├── tasks.md          # Active tasks, in-progress work, future ideas, and completed log
├── repo_map.md       # Full PageRank AST symbol graph
└── activity.jsonl    # Machine-readable action and session audit log
```

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
MIT License.

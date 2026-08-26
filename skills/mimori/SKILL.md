---
name: mimori
description: Zero-daemon project memory, structural symbol mapping, and activity tracking CLI (`mimori`). Use at session start for instant orientation and at completion for logging decisions and tasks.
---

# mimori

Zero-daemon agent context, AST symbol mapping, and activity tracking CLI stored in `.mimori/`. Standard library only.

## 1. Quick Reference & Triggers

| Action | Command | When to Use |
| :--- | :--- | :--- |
| **Warmup Snapshot** | `mimori dump --file` | Session start, subagent kickoff, pre-refactor orientation. |
| **Direct Dump Output** | `mimori dump` | Pipe full context directly to stdout without writing file. |
| **Print Structural Map** | `mimori map --stdout` | Inspect ranked symbols, callers, and import graph in terminal. |
| **Save Structural Map** | `mimori map` | Update `.mimori/repo_map.md` on disk for external tools. |
| **Initialize Workspace** | `mimori init` | First-time project setup; scaffolds `.mimori/` directory. |
| **List Tasks** | `mimori todo` | Review active tasks, in-progress items, and backlog. |
| **Add Task** | `mimori todo add "<task>"` | Track new discrete unit of work. |
| **Update Task State** | `mimori todo start <id>` / `done <id>` | Mark item active `[/]`, complete `[x]`, reopen `[ ]`, or rm. |
| **Track Future Ideas** | `mimori idea add "<idea>"` | Store backlog ideas `[?]` without cluttering active todo. |
| **Promote Idea** | `mimori idea promote <id>` | Move backlog idea into active tasks `[ ]`. |
| **Audit Ponytail Debt** | `mimori debt` | List all in-code `# ponytail:` / `// ponytail:` markers. |
| **Sync Debt Ledger** | `mimori debt sync` | Reconcile in-code markers into `.mimori/memory.md` (`## KNOWN DEBT`). |
| **Validate Debt CI** | `mimori debt check` | Exit 0 if all markers have valid triggers, exit 1 if malformed. |
| **Log Work Done** | `mimori log --action <a> --summary <s>` | Task milestone completion; records to `activity.jsonl`. |
| **View History** | `mimori history --limit <N>` | Audit recent cross-session activity log entries. |
| **Prune Cache** | `mimori clean` / `mimori clean --all` | Prune expired snapshots in `$XDG_RUNTIME_DIR/mimori`. |

---

## 2. Session Warmup (`dump`)

Generates unified context: working git state, project memory, decisions, tasks, ranked symbol map, recent activity.

### Commands
```bash
# Generate runtime file and print path (recommended)
mimori dump --file

# Print directly to stdout
mimori dump

# Focus on specific subsystem and its direct import graph neighbors
mimori dump --focus "auth,api"
mimori dump --file --focus "src/engine"

# Adjust character budget
mimori dump --budget default    # 24,000 chars (default)
mimori dump --budget large      # 48,000 chars
mimori dump --budget immense    # 96,000 chars
mimori dump --budget unlimited  # No truncation
mimori dump --budget 15000      # Custom character cap
```

### Environment Variables
- `MIMORI_FOCUS`: Comma-separated list of keywords/paths to auto-focus without CLI flags.
- `MIMORI_BUDGET`: Default budget override (`default`, `large`, `immense`, `unlimited`, or integer).

### Degradation Priority Under Budget
1. **Memory (`memory.md`)**: Invariants & gotchas kept first; epics truncated last.
2. **Decisions (`decisions.md`)**: All titles listed; newest expanded; superseded never expanded.
3. **Tasks (`tasks.md`)**: In-progress `[/]` and active `[ ]` prioritized; completed `[x]` collapsed to count.
4. **Map (`repo_map.md`)**: Low-ranked files collapsed into directory counts.
5. **Activity (`activity.jsonl`)**: Long summaries trimmed; oldest entries dropped with count note.

---

## 3. Structural AST Map (`map`)

Extracts top-level symbols (classes, functions, methods, line numbers) and computes PageRank from import graph.

### Commands
```bash
# Print map to stdout
mimori map --stdout

# Write map to .mimori/repo_map.md
mimori map

# Focus on specific files/modules + 1-hop dependencies
mimori map --stdout --focus "auth.py,server"

# Output as JSON
mimori map --stdout --format json
```

### Supported Languages & Parsers
- **Python**: Native `ast` parser (classes, methods, functions, signatures, docstrings, imports).
- **Polyglot (TS, JS, Go, Rust, Ruby, C, C++)**:
  - **Tier 1 (tree-sitter)**: Used automatically if `tree_sitter` Python module or CLI is installed.
  - **Tier 2 (ast-grep)**: Used automatically if `ast-grep` binary is in `$PATH`.
  - **Tier 3 (Pure Stdlib Fallback)**: Built-in regex heuristics used when external engines are absent. Zero runtime dependencies required.

### Ranking Factors
- Import in-degree (how many files import this file).
- PageRank score (vectorized pure-Python graph iteration).
- 90-day git churn (frequently edited files score higher).
- Entry-point heuristic (`main.*`, `index.*`, `cli.*`, `app.*`).

---

## 4. Tasks & Backlog (`todo` / `idea`)

Zero-daemon task tracking stored in `.mimori/tasks.md`.

### Task Commands (`todo`)
```bash
# List active and in-progress tasks
mimori todo
mimori todo list

# Add new task
mimori todo add "Implement cache lock" --prio high --tag perf
mimori todo add "Refactor parser" --start                  # Adds directly to In Progress [/]

# State transitions
mimori todo start 1         # Move task #1 to In Progress [/]
mimori todo done 1          # Mark task #1 completed [x] with ISO date
mimori todo reopen 1        # Move task #1 back to Active [ ]
mimori todo rm 1            # Delete task #1

# Fuzzy title targeting
mimori todo done "cache"    # Resolves unique substring match
```

### Backlog & Ideas Commands (`idea`)
```bash
# Add idea to backlog
mimori idea add "Add tree-sitter AST fallback" --tag parser

# List ideas only
mimori idea
mimori idea list

# Promote idea to active task
mimori idea promote 1       # Moves from [?] to [ ]
```

### Flags & Filters
- `--status <todo|in_progress|done|idea|all>`: Filter by status.
- `--tag <tag>` / `-t <tag>`: Filter by tag.
- `--prio <high|med|low>`: Filter or set priority.
- `--plain`: Plain text output (no ANSI colors, pipeline-friendly).

---

## 5. Ponytail Technical Debt (`debt`)

Tracks `# ponytail: <what> <- <ceiling> -> <upgrade trigger>` and `// ponytail: ...` in source code.

### Commands
```bash
# List all in-code debt markers with ceilings and triggers
mimori debt
mimori debt list

# Synchronize in-code markers into .mimori/memory.md (## KNOWN DEBT)
# Auto-prunes resolved markers; preserves manual waivers ('accepted ...')
mimori debt sync

# CI Validation Gate: verify all markers have explicit triggers
mimori debt check           # Exit 0 if valid, exit 1 if missing triggers
```

---

## 6. Telemetry & Activity Logging (`log` / `history`)

Appends milestone entries to `.mimori/activity.jsonl`.

### Commands
```bash
# Log completed milestone (keep summary short and factual)
mimori log \
  --action "refactor-auth" \
  --summary "Unified token validation middleware; removed duplicate endpoints" \
  --files "auth.py,middleware.py,test_auth.py"

# Inspect past activity log
mimori history
mimori history --limit 10
mimori history --plain
```

---

## 7. Cache Management & Garbage Collection (`clean`)

Snapshots from `mimori dump --file` live in `$XDG_RUNTIME_DIR/mimori` (fallback `/tmp/mimori-$UID/`).

### Retention Policy
- Max 2 snapshots per repo (`MIMORI_CACHE_MAX_PER_REPO=2`).
- Global TTL: 72 hours (`MIMORI_CACHE_TTL_HOURS=72.0`).
- Global Max Files: 50 (`MIMORI_CACHE_MAX_FILES=50`).
- Non-blocking GC runs automatically on every `mimori dump --file`.

### Manual Cleanup Commands
```bash
# Run GC pruning pass
mimori clean

# Purge all cached snapshots immediately
mimori clean --all
```

---

## 8. Directory & File Formats

```
.mimori/
├── memory.md        # Domain rules, invariants, gotchas, ## KNOWN DEBT
├── decisions.md     # ADRs (Context, Decision, Consequences)
├── tasks.md         # Tasks ([ ], [/], [x]) and Ideas ([?])
├── repo_map.md      # AST structural map output from `mimori map`
└── activity.jsonl   # Append-only milestone telemetry
```

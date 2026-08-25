---
name: mimori
description: Zero-daemon project memory, structural symbol mapping, and activity tracking CLI (`mimori`). Use at the start of any session to inspect repo structure and architecture invariants without costly exploratory searches, and use at completion to record architecture decisions and log tasks.
---

# Agent Context & Memory (mimori)

`mimori` provides zero-daemon project memory, AST repository mapping, and session activity tracking stored directly within `.mimori/` inside the target workspace.

## 1. Fast Warmup / Startup

Run this first, before any broad grepping or directory crawling:

```bash
mimori dump --file
```
Then view the printed file path (e.g. `/run/user/1000/mimori/ctx-<repo>-<commit>.md`). If the workspace is not yet a git repository, `mimori` will automatically initialize one. Output files are isolated to the current user's runtime directory (`$XDG_RUNTIME_DIR/mimori` or `/tmp/mimori-$UID/`) and tagged by repository name and short commit ID so parallel agent sessions never clash.

One call returns six things: **working state** (branch, uncommitted files, recent commits), **project memory**, **architecture decisions**, **tasks & backlog**, a **ranked symbol map**, and **recent activity**.

The map is regenerated live on every call, so it never serves stale cached data. Read it as an *orientation* layer, not an index:

- Files are ranked by import in-degree, recent commit churn, and entry-point detection — the top of the map is what matters in this repo, not what sorts first alphabetically.
- `← cli, db, kb_engine` on a file means those modules import it. That is the one thing `Grep` cannot give you in a single call.
- Symbols carry signatures and `:line` numbers, so you can `Read` with an offset instead of searching.
- Output is capped by a character budget. When lower-ranked files are collapsed, the map **says so explicitly** and gives the counts — if it doesn't say it was truncated, you are seeing everything.

Still use `Glob`/`Grep` for exact locations, call sites, and anything below the top-level symbols. The map tells you *where to look*; it does not replace searching.

To write a fresh copy of the map to `.mimori/repo_map.md` (for humans or other tools browsing the repo), or print it without writing:

```bash
mimori map
mimori map --stdout
```

### Budget

**`mimori map` is complete by default** — it writes `.mimori/repo_map.md` or stdout for browsing, which costs disk, not context. Measured on real repos a full map runs **~40 tokens per file**, so it stays cheap into the hundreds of files. Pass `--budget <chars|large|immense>` to cap it on a very large repo.

**`mimori dump` stays budgeted** (24 000 chars by default), because that output is spent directly on context. Override per call with `--budget <chars|default|large|immense|unlimited>` or the `MIMORI_BUDGET` env var.

Raising a budget costs **tokens, not time** — generation is flat regardless — and it saturates: past the point where every ranked file is already detailed, extra budget buys nothing. Reach for a bigger `dump` budget when orienting in an unfamiliar large repo; stay on `default` for routine work, since an oversized dump crowds the context it is meant to save.

When a budget binds, degradation is by priority and nothing vanishes silently:

- **Memory** keeps invariants and gotchas ahead of status/epics; an oversized top-priority section is truncated rather than dropped.
- **Decisions** always list **every ADR title**, expanding bodies newest-first. An ADR marked `**Superseded by**: ...` is kept for history but never expanded, so the file can grow without the snapshot growing with it.
- **Tasks & Backlog** prioritizes In-Progress `[/]` and Active `[ ]` tasks; Future Ideas `[?]` are included up to budget; Completed tasks `[x]` are collapsed to compact count summaries (`_N completed tasks hidden_`) so historical items never displace active context.
- **Map** collapses lower-ranked files by directory and reports the counts. Files with no parsed symbols are always summarized this way rather than listed individually, even at an unlimited budget — they are grouped, not dropped.
- **Recent Activity** (the last `mimori log` entries) is capped separately so a verbose logger can't starve the map's share: long summaries and file lists are elided per-entry, and entries are dropped oldest-first if they still don't fit, with a `_N of M entries shown_` note.

### Focused Maps for a Task

Give the map a task lens instead of a global ranking: files matching any focus substring render in full detail **together with their direct import-graph neighbors** (importers and imports); everything else collapses to a directory summary.

```bash
mimori map --stdout --focus "auth.py,server"
mimori dump --focus "src/engine"
MIMORI_FOCUS="auth,api" mimori dump
```

Use it on large repos when you already know the area of a task — it is the cheap, task-conditioned substitute for a global dump. Budget rules still apply; truncation is still announced.

**New agents (fresh sessions, subagents) get focus automatically** when `MIMORI_FOCUS` is exported in the shell they launch from: the standard warmup `mimori dump` then produces a focused map without any extra flag. For a one-off child, pass `--focus` explicitly in the subagent's task text instead.

## 2. Initialize a Project

To set up the `.mimori/` directory structure (`memory.md`, `decisions.md`, `tasks.md`, `repo_map.md`, `activity.jsonl`) in a new or existing repository:

```bash
mimori init
```

## 3. Persistent Memory & Architectural Decisions

- **`.mimori/memory.md`**: Update when discovering non-obvious domain rules, active epic milestones, or subtle edge-case gotchas.
- **`.mimori/decisions.md`**: Record new ADRs (*Context*, *Decision*, *Consequences*) when introducing new architectural patterns.
- **`.mimori/tasks.md`**: Track in-progress tasks, pending todos, and future ideas/backlog.

### Writing style: caveman

`memory.md`, `decisions.md`, and `log --summary` get scanned fast at session start — write them caveman-style, not prose.

Drop: articles (a/an/the), filler (just/really/basically/actually/simply), hedging ("it might be worth", "you could consider"), pleasantries, connective fluff (however/furthermore/additionally). Fragments OK. Short synonym over long phrase (big not extensive, fix not "implement a solution for"). Merge bullets saying the same thing twice; one example, not three.

Keep exact, never touch: code, inline code, file paths, commands, URLs, technical terms, numbers/versions, error strings. Never drop not/never/no/only — flips meaning, costs more than it saves. Never invent abbreviations (cfg/impl/req) to look terse — same token count, less clear; full word wins.

Applies to `.mimori/memory.md`, `.mimori/decisions.md`, `mimori log --summary`. Not README/CLAUDE.md prose or chat replies — those stay full sentences.

## 4. Todo, Tasklist & Future Ideas Tracking

`mimori` includes zero-daemon CLI task tracking stored in `.mimori/tasks.md`.

### Basic Usage

```bash
# List all tasks and backlog ideas
mimori todo
mimori todo list

# Add new tasks
mimori todo add "Implement AST cache pruning" --prio high --tag perf
mimori todo add "Refactor memory loader" --start           # Adds directly to In Progress

# Manage task state transitions
mimori todo start 1        # Move task #1 to In Progress ([/])
mimori todo done 1         # Mark task #1 as completed ([x] with date)
mimori todo reopen 1       # Move back to Active Tasks ([ ])
mimori todo rm 1           # Delete task #1

# Manage Future Ideas & Backlog
mimori idea add "Explore quantum symbol indexing" --tag ast
mimori idea list           # Filter view to ideas only
mimori idea promote 1      # Move idea #1 into Active Tasks
```

### Filtering & Options
- `--status <todo|in_progress|done|idea|all>`: Filter by lifecycle state.
- `--tag <tag>` / `-t <tag>`: Filter by tag (e.g. `mimori todo --tag perf`).
- `--plain`: Emit clean text without ANSI colors (safe for piping).
- Substring targeting: `mimori todo done "AST"` resolves fuzzy text matches if unique.

## 5. Ponytail Technical Debt & Ledger Reconciliation

`mimori debt` scans in-code `# ponytail:` / `// ponytail:` deferral comments and manages the debt ledger:

```bash
# List all in-code ponytail debt markers with ceilings and upgrade triggers
mimori debt
mimori debt list

# Synchronize in-code markers into .mimori/memory.md (## KNOWN DEBT)
# Automatically prunes resolved markers and respects the 30-line debt cap
mimori debt sync

# CI Validation Gate: Verify all markers have valid triggers (exit 0 / exit 1)
mimori debt check
```

## 6. Log Task Activity & Telemetry

When finishing a task or major milestone, log a **high-level overview** — what changed and why it matters, not a step-by-step of how you did it. One line, caveman style (above), similar in scope to a git commit subject line:

```bash
mimori log \
  --action "refactor-auth" \
  --summary "Unified token validation into one middleware, stop 3 endpoints re-implementing it" \
  --files "auth.py,middleware.py,test_auth.py"
```

`dump`'s Recent Activity elides any summary past ~160 chars (see Budget above), and `mimori log` warns if yours runs long — that's the signal you drifted into implementation-detail prose. Save the mechanism, the file-by-file breakdown, and the "how" for the commit message; `Recent Activity` exists so an agent can scan "what happened lately" in one pass at session start, not replay the session.

To inspect recent activities across sessions:

```bash
mimori history --limit 5
```

## 7. Cache Management & Garbage Collection

Context snapshots generated via `mimori dump --file` are stored in the user runtime directory (`$XDG_RUNTIME_DIR/mimori` or `/tmp/mimori-$UID/`).

### Automatic In-Flight Pruning
On every `mimori dump --file` execution, `mimori` automatically performs opportunistic non-blocking garbage collection:
- **Per-Repo LRU Retention**: Retains only the **2 most recent snapshots** per repository (`ctx-<repo>-<commit>.md`), deleting older commit snapshots for that repo.
- **Global TTL**: Any snapshot older than **72 hours (3 days)** is automatically removed across all repos.
- **Global Safety Cap**: Keeps at most **50 snapshots** total, purging oldest first.

### Environment Overrides
- `MIMORI_CACHE_MAX_PER_REPO`: Max snapshots per repository (default: `2`).
- `MIMORI_CACHE_TTL_HOURS`: Snapshot expiry in hours (default: `72.0`).
- `MIMORI_CACHE_MAX_FILES`: Max total snapshot files in cache (default: `50`).

### Manual Cleanup Command
To manually prune expired snapshots or wipe the cache:

```bash
mimori clean        # Prune expired and stale snapshots according to retention policy
mimori clean --all  # Immediately purge all cached snapshots in temp directory
```

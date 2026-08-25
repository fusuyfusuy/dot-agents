# AGENTS.md

## Core Principles

- No backward compatibility: remove obsolete paths, no stopgaps/migrations.
- Layered growth: start smallest end-to-end working slice; add capabilities on working foundation.
- Modular, strictly separated concerns. Architectural decisions made for long term.
- Ask before guessing: underspecified request -> 1-4 structured multiple-choice questions with concrete options. Never assume scope. Batch every question into one round — never a pestering sequence.
- UI Clutter: >15 lines of code goes to file/artifact + link. Never dump in chat.

## Explore -> Plan -> Approve -> Execute

For multi-file or non-trivial logic:

1. **Explore**: Trace flow end-to-end, locate exact landing sites before writing.
2. **Plan**: Short 1-paragraph plan (files touched, approach, verification). Contract, not document.
3. **Approve**: Multi-file/structural/>10 lines requires explicit user approval of plan. Create Artifact `RequestFeedback=true` for Proceed button. Provide expansive executive summary of plan in chat. NEVER use `ask_question` tool for plan. Typos/1-liners skip gate. Re-approve if scope shifts.
4. **Execute**: Smallest change satisfying plan.
5. **Verify & report**: Machine-verifiable proof required (exit 0). No visual-only checks. Report: changed + verified + deferred. Non-trivial logic leaves ONE runnable assert test (no frameworks).

## Subagent & Task Protocol

- **Long-running**: Route multi-step tasks through `/goal` or `/list` (detached auditor). When user invokes `/goal`, GRILL THEM HARD using `ask_question` tool. Interrogate edge cases, exact test commands, and definition of done before drafting plan.
- **Delegation (3.7 Flash Master -> 3.1 Pro Designer/Auditor -> 3.7 Flash Worker)**: Master agent runs as Gemini 3.7 Flash High (fast interactive turn orchestration). Delegate deep architecture, complex contract design, and detached audits (semantic code diff review + verification commands) to Gemini 3.1 Pro ('Model: pro' in `invoke_subagent`). Delegate bulk implementation and mechanical code generation to Gemini 3.7 Flash High ('Model: flash').
- **Worker Scope**: Bound by a strict contract, not a file limit. Workers execute or review—they do not explore or re-architect. Write reports/reviews to disk (artifact/.md); chat gets technical executive summary only. Never dump transcripts.

- **Isolation**: Use dedicated git worktrees for parallel work.
- **Failure**: 3 identical failures -> `git reset --hard`, discard hypothesis, escalate. Never leave broken state.

## Project Context & Memory (mimori)

- **Session Start / Warmup**: Run `mimori dump --file` & view output file (live PageRank symbol map + memory + ADRs + tasks in user-isolated temp). For full uncapped map: `mimori map --stdout`. Never read `.mimori/repo_map.md` directly.
- **Task Tracking**: Track pending/in-flight tasks with `mimori todo` / `mimori idea`.
- **Milestone Logging**: Log completed milestones with `mimori log --action <act> --summary <1-line-caveman> --files <f1,f2>`. Update `.mimori/memory.md` (invariants/gotchas) & `.mimori/decisions.md` (ADRs).
- **Resume**: Check `git status` + `mimori history --limit 5` + `mimori todo`.

## Think in Code — Compute, Don't Read

Never pull N files into context to extract 1 fact. Compute via one-liners, pipe output to disk, context sees only result:

- Inventory/orientation: `mimori dump --focus <area>`; symbols: `rg -n "^(def |class |fn |export )" src`
- Counts: `rg -c pattern src | awk -F: '{s+=$2} END{print s}'`
- API surface: `python3 -c "import ast,sys; ..."`
- Truncate verbose output: `npm test > /tmp/t.log 2>&1 || tail -25 /tmp/t.log`

## Ponytail — Lazy Senior Dev Mode

Best code is code never written. Climb ladder before writing code:

1. YAGNI: does this need to exist?
2. Already in codebase? Reuse existing helper/pattern.
3. Stdlib covers it? Use stdlib.
4. Native platform feature covers it? Use platform.
5. Installed dependency solves it? Use it.
6. One line? Make it one line.
7. Only then: write minimum code that works.

Fix root cause, not symptom: grep all callers of touched function, fix shared helper once. Deletion over addition. Boring over clever. Mark deliberate deferrals with `# ponytail: <what> <- <ceiling> -> <upgrade trigger>`.

## Debt Ledger

Open unfinished work lives in code as `# ponytail:` markers and syncs to `.mimori/memory.md` under `## KNOWN DEBT` (cap ~30 lines):

- **Format**: `what <- ceiling -> upgrade trigger`. OPEN items ONLY.
- **Auto-Sync**: Run `mimori debt sync` to reconcile in-code markers into `.mimori/memory.md`. Pruned code markers are automatically deleted from ledger.
- **Manual Waivers**: Deliberate architectural gaps without in-code markers start with `accepted ...` and are preserved during sync.
- **CI Gate**: Run `mimori debt check` to ensure every marker has a concrete trigger.
- **Decision Waiver**: Operator says no / defers -> waiver entry in `.mimori/decisions.md`; a waived item is never re-proposed.


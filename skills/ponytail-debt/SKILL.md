---
name: ponytail-debt
description: >
  Harvest every `ponytail:` comment in the codebase into a debt ledger, so the
  deliberate shortcuts and deferrals the Ponytail ruleset leaves behind get
  tracked instead of rotting into "later means never". Use when the user says
  "ponytail debt", "/ponytail-debt", "what did ponytail defer", "list the
  shortcuts", "ponytail ledger", or "what did we mark to do later". One-shot
  report, does not apply fixes.
---

# Ponytail Debt

Every deliberate corner cut under the Ponytail ruleset is marked with a
`ponytail: <ceiling>, <upgrade path>` comment naming its ceiling and upgrade
path. This collects them into one ledger so a deferral can't quietly become
permanent.

## Scan

Two passes — a strict one for normal single-line comments, a loose one for
markers embedded in docstrings or block comments that a naive `#`/`//`-only
grep misses (verified against this repo: `mimori`'s `build_import_graph`
docstring marker doesn't match a comment-prefix pattern, and it's an
extensionless executable so an allowlist-by-extension loose pass misses it
too — block prose/data formats instead of allowlisting code extensions).

Use `command grep`, not bare `grep` — some shells alias/wrap it (this
sandbox routes `grep` through `ugrep` with injected flags that combine
`--exclude` globs differently), which can silently change results.

```bash
# Strict: standard single-line comment prefix immediately before the marker.
command grep -rnE '(#|//) ?ponytail:' . --exclude-dir=.git --exclude-dir=node_modules

# Loose: bare marker, no prefix required. Blocklist prose/data formats rather
# than allowlist code extensions, so extensionless scripts (shebang files
# like mimori) still get caught. .json/.jsonl are always excluded — they
# can't have comments, so any hit inside one is log/data content (e.g. an
# activity.jsonl summary that just mentions the word), never a real marker.
command grep -rnE 'ponytail:' . --exclude-dir=.git --exclude-dir=node_modules \
  --exclude='*.md' --exclude='*.mdx' --exclude='*.txt' --exclude='*.rst' --exclude='*.adoc' \
  --exclude='*.json' --exclude='*.jsonl' --exclude='*.csv' --exclude='*.lock'
```

Merge the two, dedupe by file:line. Add other prose/data extensions this
repo uses to the loose pass's exclude list if it's picking up false
positives.

Each hit is one ledger row. Read a few lines of context around the hit (the
marker convention is `ponytail: <ceiling>, <upgrade path>`, often wrapped
across several comment lines) to pull the ceiling and upgrade path.

## Output

One row per marker, grouped by file:

```
<file>:<line>, <what was simplified>. ceiling: <the limit named>. upgrade: <the trigger to revisit>.
```

A marker counts as having a real trigger only if it names a **condition,
dependency, size threshold, or owner** for when to revisit — not just a
direction. `"upgrade path: add pagination (deferred)"` names a direction but
no trigger; `"upgrade path: wrap in a transaction once PocketBase supports
multi-collection transactions"` names one. Tag the former `[no-trigger]` —
these are the ones most likely to rot silently.

**Duplicate check**: normalize each marker's comment text (whitespace,
casing) and group. Any group present in 2+ files gets `[duplicate ×N: file1,
file2, ...]` — the same deferred decision living in multiple places means an
eventual fix has to remember every copy, and a new caller can drift out of
sync with none of them.

Want an owner per row too? Add `git blame -L<line>,<line>`.

End with `<N> markers, <M> no-trigger, <K> duplicate groups.` Nothing found:
`No ponytail: debt. Clean ledger.`

## Boundaries

Reads and reports only, changes nothing. To persist it, ask and write the
ledger to a file (e.g. `PONYTAIL-DEBT.md`). One-shot. "stop ponytail-debt" or
"normal mode" to revert.

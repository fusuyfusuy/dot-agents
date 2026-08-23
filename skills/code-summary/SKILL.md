---
name: code-summary
description: "Think in code, not in reads: summarize, count, and inventory the codebase with small bash/python one-liners that print only the result, instead of pulling raw file contents into context. Use for any task that would otherwise read many files just to extract a few facts."
---

# Code Summary — Compute, Don't Read

The costliest pattern in agentic work is reading N files into context to extract one fact. Every `Read` result becomes input tokens you pay for on every subsequent turn (cached or not). Instead: **write a one-liner that computes the answer and prints only the result.**

## The Rule

Before reading a file or directory, ask: *"can a script answer this in one line?"* If yes, run the script. Raw contents stay on disk; context only sees the summary.

## Ready one-liners

| Need | Command |
|---|---|
| Function/class inventory in a tree | `rg -n "^(def |class |async def |fn |export (default )?(function\|class\|const))" src` |
| Count symbols per file | `rg -c "^(def |class )" src | sort -t: -k2 -rn | head` |
| File + line counts for a dir | `find src -name "*.py" \| xargs wc -l \| sort -rn \| head` |
| Dir sizes / bloat | `du -sh --max-depth=1 * \| sort -rh \| head` |
| TODO/FIXME sweep | `rg -n "TODO\|FIXME\|HACK" src \| head -30` |
| API surface of one file | `python3 -c "import ast,sys; t=ast.parse(open(sys.argv[1]).read()); [print(f'{n.lineno}: {n.name}({ast.unparse(n.args)})') for n in t.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))]" file.py` |
| Who imports / is imported by X | `mimori dump --focus X` (detail + graph neighbors) |
| Count of something across files | `rg -c "pattern" src \| awk -F: '{s+=$2} END{print s}'` |
| Cross-file callers of a symbol | `rg -n "symbolName\(" src --glob "!test*"` |

## When it pays most

- Orientation ("what's in this repo?") — `mimori dump --focus <area>` instead of walking the tree.
- Extracting facts (counts, lists, signatures, TODOs).
- Anything that would be 5+ `Read` calls for one answer.

## When to still read

- You need the actual content to edit it (read that one file, not the thirty).
- Semantics beyond grep/ast (then read the 2–3 files `mimori` ranked as core).

Keep summaries small: `head -30`, `wc -l`, `sort | uniq -c`. The model's job is deciding; the script's job is the counting.

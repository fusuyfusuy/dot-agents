---
name: pi-ast
description: Empryo-inspired surgical AST editing and genome-ranked navigation for pi — ast_edit (ts-morph) for TS/JS, structural_edit (ast-grep) for polyglot, plus soul_find / soul_impact ranking and blast-radius before you edit. Use when the user says "surgical edit", "ast edit", "add import", "rename symbol", "move symbol", "blast radius", "what breaks if I touch this", "find file by concept", or when edit fails on whitespace/JSX/escaping.
---

# pi-ast — Surgical edits, not string patches

pi's built-in `edit` is `oldText/newText` string matching. It fails on whitespace drift, escaped quotes, JSX/TSX, unicode, and long needles, and it can't do atomic multi-file changes. **pi-ast** ports Empryo's split (65+ ops, 5.7× fewer tokens in benchmarks):

- **`.ts/.tsx/.js/.jsx/.mts/.cts/.mjs/.cjs` → `ast_edit`** — ts-morph, symbol-addressed, no line math
- **`.py/.go/.rs/.java/.kt/.rb/.php/.swift/.dart…` → `structural_edit`** — ast-grep, `$X` / `$$$ARGS` structural match
- **`soul_find` / `soul_impact`** — ranked search + "what breaks if I touch this?" before the first keystroke
- **`move_symbol`** — cross-file atomic move with `WriteTransaction` + CAS

All five live in one extension: `tui-agent-settings/pi/extensions/pi-ast.ts` (synced to `~/.pi/agent/extensions/pi-ast.ts` by `setup.sh`, hot-reload with `/reload`).

## When to use

- `edit` failed with "oldText not found" on a JSX block, a quoted string, or a re-indented file
- You need `add import + use it` in **one atomic turn** (all-or-nothing, no half-written file)
- You need to `rename` a symbol, `set_body` of a function, or `replace` a whole interface without pasting 200 lines of `oldText`
- Polyglot edit in `.py` / `.go` / `.rs` — `edit` would be fragile text, `structural_edit` is tree-aware
- You don't know the file path — you know the concept ("quota fetch", "auth middleware")
- You're about to edit a high-fan-in file — you want `soul_impact blast_radius` first

## Tool reference

### `ast_edit` — surgical TS/JS (ts-morph, lazy)

Stub wired: 6 ops; full 65 clean-room. Valid `target` kinds per action are enforced:

```ts
ast_edit({ path, action, target, name, value, newCode, index, operations })
ast_edit({ path: "src/foo.ts", action: "add_named_import", value: "zod", newCode: "z" })
ast_edit({ path: "src/foo.ts", action: "rename", target: "function", name: "oldName", value: "newName" })
ast_edit({ path: "src/foo.ts", action: "set_body", target: "function", name: "handler", newCode: "return 42;" })
ast_edit({ path: "src/foo.ts", action: "add_statement", target: "function", name: "handler", newCode: "logger.info('hit')" })
ast_edit({ path: "src/foo.ts", action: "replace", target: "class", name: "Foo", newCode: "export class Foo { /* ... */ }" })
ast_edit({ path: "src/new.ts", action: "create_file", newCode: "export const x = 1\n" })
// atomic — all or rollback:
ast_edit({ path: "src/foo.ts", operations: [
  { action: "add_named_import", value: "zod", newCode: "z" },
  { action: "add_statement", target: "function", name: "handler", newCode: "z.string().parse(input)" },
]})
```

Tiers (pick smallest):

- **micro** (1-10 tok): `set_type, set_return_type, set_async, set_export, rename, remove, set_initializer, add_parameter, set_optional`
- **body** (10-100 tok): `set_body, add_statement, insert_statement, add_property, add_method, add_constructor, set_extends …`
- **full**: `replace` (whole symbol), `create_file`

File-level: `add_import, add_named_import` (idempotent merge), `organize_imports` (ponytail).

Shape contract — get this wrong and the file corrupts:

- `set_body` / `add_statement` / `insert_statement` take **contents only** — no surrounding `{}`
- `add_method` / `add_constructor` take **full declaration with braces** (`foo(x: number) { return x; }`)
- `replace` takes **whole symbol text with braces**
- `add_property` on interface `name: type`; on class `name: type = value`
- `replace_in_body` stub not yet wired — ponytail, use `replace` for now

Fails gracefully if `ts-morph` not installed: `npm i -D ts-morph` / `bun add -d ts-morph` then retry. For non-TS files use `structural_edit`.

### `structural_edit` — polyglot (ast-grep)

```ts
structural_edit({ file: "toku.py", pattern: "fmt($X)", rewrite: "fmt($X)", preview: true })
structural_edit({ file: "main.go", pattern: "fmt.Println($X)", rewrite: "log.Println($X)" })
```

`pattern`/`rewrite` use `$X` single + `$$$ARGS` variadic. `lang` auto from ext via `EXT_TO_AST_GREP_LANG`; override if needed. `preview:true` shows diff without writing; omit to `--update-all` write. Rejects `.ts/.js` — uses `ast_edit` instead. Requires `ast-grep` binary: pi-lens auto-installs to `~/.pi/agent/bin`; else `cargo install ast-grep` / `brew install ast-grep`.

### `move_symbol` — atomic cross-file

```ts
move_symbol({ symbol: "fetchWindows", from: "tui-agent-settings/usage/ocgo.py", to: "tui-agent-settings/usage/toku.py" })
```

`WriteTransaction {stage→commit→rollback}` + CAS. Stub harvests the def block heuristically; full LSP `workspaceSymbol + ts-morph move` is ponytail upgrade.

### `soul_find` — ranked fuzzy search

```ts
soul_find({ query: "quota fetch", limit: 12 })
soul_find({ query: "auth middleware" })
```

Ranks `git ls-files --exclude-standard` by basename/path fuzzy + `git log co-change` boost (top-5). Penalizes `node_modules/.venv`. Stub for full PageRank+trigram SQLite graph (ponytail). Prefer over `find`/`grep` when you don't know the path.

### `soul_impact` — blast radius before you edit

```ts
soul_impact({ action: "blast_radius", file: "tui-agent-settings/usage/toku.py" })
soul_impact({ action: "dependents", file: "tui-agent-settings/pi/extensions/pi-ast.ts" })
soul_impact({ action: "cochanges", file: "tui-agent-settings/skills/mimori/mimori" })
soul_impact({ action: "dependencies", file: "toku.py" })
```

Returns `dependents (grep approx)` + `cochanges (git log -n80)` + `total affected`. Run before editing any file `pi-lens` or `soul_find` ranked high.

## Discovery funnel (prefer in order)

```
soul_find "concept"  →  module_report path  →  read_symbol path symbol
      ↓                          ↓                     ↓
   orient                 explain file          read exact body
```

`pi-ast` covers step 1 + step 0 ("what breaks?"). For step 2 use `pi-lens: module_report` (`blastRadius:true`) and `read_symbol` — `pi-ast` does not replace them.

## Minimal tutorial

```bash
# 1) locate — you know the idea, not the file
soul_find query="quota fetch" limit=5
# → 01. tui-agent-settings/usage/toku.py (score 18)
#   02. tui-agent-settings/usage/ocgo.py (score 15 +co2)

# 2) impact — check blast radius before touching toku
soul_impact action=blast_radius file=tui-agent-settings/usage/toku.py
# → direct dependents: 2, co-change partners: 4, total affected: 5

# 3a) TS/JS surgical — add import + statement atomically (one tool call)
ast_edit path=tui-agent-settings/pi/extensions/pi-ast.ts operations='[
  {"action":"add_named_import","value":"zod","newCode":"z"},
  {"action":"add_statement","target":"function","name":"surgicalEditViaTsMorph","newCode":"z.string().parse(cwd)"}
]'

# 3b) polyglot structural — preview first, then write
structural_edit file=tui-agent-settings/usage/toku.py pattern='fmt($X)' rewrite='fmt($X)' preview=true
structural_edit file=tui-agent-settings/usage/toku.py pattern='fmt($X)' rewrite='human($X)'

# 3c) create a new file without hand-rolling mkdir
ast_edit path=src/new-feature.ts action=create_file newCode='export const flag = true\n'

# 3d) cross-file move
move_symbol symbol=human from=tui-agent-settings/usage/toku.py to=tui-agent-settings/usage/format.ts
```

## Gotchas

- `ast_edit` rejects non-TS/JS — use `structural_edit` for `.py/.go/.rs…`, or raw `edit` for `.md/.json`.
- `ast_edit` without `ts-morph` still `create_file`s; other ops return a hint — install `ts-morph` in that project to unlock them.
- `structural_edit` without `ast-grep` binary returns a hint — pi-lens installs it to `~/.pi/agent/bin`; restart pi if first-run hasn't finished.
- `soul_find` caps `cochange` to top-5 for speed; full `PageRank damping 0.85 + trigram WITHOUT ROWID` is `ponytail:` to `mimori` / `pi-lens`.
- `move_symbol` is heuristic block harvest — review `git diff` after; LSP move is ponytail.

## Ponytail ledger

`command grep -rn ponytail tui-agent-settings/pi/extensions/pi-ast.ts` → 9 markers. Full SQLite repomap, 65-op ts-morph, LSP move, and `replace_in_body` range are deferred by design (see `ponytail:` ceilings in-file). Harvest via `tui-agent-settings/skills/ponytail-debt`.

## Value recap (why this over plain `edit`)

- **Correctness:** no whitespace/quote/JSX escaping failures; type-aware locate.
- **Atomicity:** `operations:[]` all-or-nothing + CAS concurrent-mod check + `WriteTransaction` rollback.
- **Tokens:** fewer `read→edit→read→edit` loops — Empryo vs pi: 5.7× fewer input tokens, 57% faster on real bugs.
- **Safety:** `soul_impact` before high-fan-in edits; `soul_find` by concept not path.

## See also

- Extension source: `tui-agent-settings/pi/extensions/pi-ast.ts` (header comments + ponytails are the spec)
- Empryo concepts: `https://github.com/proxysoul/Empryo` `mintlify-docs/concepts/architecture.mdx`, `src/core/tools/ast-edit.ts`, `src/core/intelligence/repo-map.ts`
- pi-lens funnel: `symbol_search` → `module_report` → `read_symbol` (this skill complements, not replaces)

---
name: mimori
description: "High-performance code intelligence CLI: AST slicing, symbol search, dependency traversal, PageRank architectural mapping, blast radius, and repo health."
---

# MIMORI(1) — General Commands Manual

## NAME
**`mimori`** — zero-config AST code-intelligence, symbol-graph, and context-slicing engine

## SYNOPSIS
```shell
mimori init
mimori map    [--scope <dir>] [--focus <target>] [--seed <term>] [--limit <N>] [--json]
mimori slice  <coordinate> [-f|--follow-local] [-i|--with-imports] [--budget <N>] [--json]
mimori find   <pattern> [-s|--symbols-only] [-f|--files-only] [--json]
mimori up     <target> [--json]
mimori down   <target> [--json]
mimori uses   <target> [--json]
mimori missing <pattern> [--scope <dir>] [--defines <lit>] [--json]
mimori blast  <target> [-d|--depth <N>] [--down] [--with-sinks <a,b,c>] [--json]
mimori doctor [--limit <N>] [--json]
mimori clean  [--all]
```

## DESCRIPTION
**`mimori`** provides token-dense structural code intelligence for AI agents and developers. It statically embeds Tree-sitter parsers for Rust, TypeScript/JavaScript, Python, and Go, indexing functions, methods, classes, class fields (incl. ctor param properties), traits, interfaces, exported constants, builder patterns, and object literal members (e.g. tRPC routers, Drizzle tables, Hono routes). Edges cover calls, construction (TypeScript `new X()` incl. `throw new X`; Rust `S::new()`/`S::default()`), member-access reads (`error.command`), call-arg mentions, and template-interpolation mentions (coarse dataflow, e.g. `` `(${cmd}) >> ${logPath}` ``). It builds an in-memory cross-file dependency graph, computes architectural centrality via in-degree PageRank, and persists parsed symbols into an embedded SQLite database (`.mimori/index.db`). Indexing is near-linear in workspace size; a warm run on a 481,200-symbol workspace takes ~1.8s, and small repositories index in milliseconds.

All commands output compact Markdown optimized for LLM prompt context windows by default, or machine-readable JSON when `--json` is specified.

---

## GLOBAL OPTIONS
* `--json`  
  Emit results as structured JSON instead of human/LLM-readable Markdown. Honoured by every subcommand, including the `init` and `clean` acknowledgements.
* `-h`, `--help`  
  Print help information.
* `-V`, `--version`  
  Print version information.

---

## SUBCOMMANDS

### `map`
```shell
mimori map [--scope <dir>] [--focus <target>] [--seed <term>] [--limit <N>] [--json]
```
Generate a hierarchical, centrality-ranked structural outline of codebase symbols, modules, and entry points.
* `--scope <dir>`: Restrict the map to files within the specified directory.
* `--focus <target>`: Run Personalized PageRank (PPR) biased toward `<target>` to surface its relevant architectural neighborhood.
* `--seed <term>`: Bias the ranking toward symbols whose name or file matches the term.
* `--limit <N>`: Keep only the top `N` symbols by centrality.

### `slice`
```shell
mimori slice <coordinate> [-f|--follow-local] [-i|--with-imports] [--budget <N>] [--json]
```
Extract an isolated, token-dense view containing a symbol's exact source body, coordinates, signature, and immediate 1-hop dependencies.
* `<coordinate>`: Target identifier (see **COORDINATE SYNTAX** below).
* `-f`, `--follow-local`: Inline private local callee symbol bodies declared within the same file.
* `-i`, `--with-imports`: Include top-of-file import statements in the slice header for direct dependency context without needing extra file reads.
* `--budget <N>`: Pack markdown output to roughly `N` tokens (heuristic chars/token calibrated per language: 3 for Rust/Go, 4 for TS/JS, 5 for Python). The core slice is never cut; context drops in order: inlined locals, callees, callers, imports. A notice names what was dropped. Markdown only — ignored with a warning under `--json`.
* Ambiguous coordinates fail with pastable `mimori slice '<coordinate>'` retry commands; unknown symbols suggest `mimori find`.
* *Note*: Bodies exceeding 250 lines are cleanly truncated with head/tail excerpts to preserve token budgets.

### `find`
```shell
mimori find <pattern> [-s|--symbols-only] [-f|--files-only] [--json]
```
Search for symbols and files across the repository, ordered by exact match and in-degree PageRank centrality.
* `-s`, `--symbols-only`: Restrict search hits strictly to symbol declarations.
* `-f`, `--files-only`: Restrict search hits strictly to file paths. Mutually exclusive with `-s`; passing both exits `2`.
* *Hybrid Fallback*: When zero symbols **and** files match, falls back to a case-insensitive literal scan of indexed **files**, one hit per matching line (count matches `rg -c` on the same query). Covers code outside symbol bodies (imports, module-level statements); files outside the index (unsupported extensions, ignored dirs) still need `rg`.
### `up`
```shell
mimori up <target> [--json]
Display all upstream **callers** (functions, methods, types) that invoke or construct `<target>`, plus weakest-tier **value uses**: direct mentioners (property reads, type refs, arg/template identifiers — e.g. a `${var}` interpolation site) labeled `[Value Use]`, never traversed. A symbol that both calls and mentions appears once, as a caller. Member calls (`X.foo()`) resolve same-file or drop — never unique-global, since the receiver is unknown. `new X(...)` (incl. `throw new X`) counts as a caller edge to `X` and `X::constructor`, and `up` on a class folds its constructor's callers in. Zero callers on a class-like prints a `try X::constructor / rg "new X"` hint.
### `down`
```shell
mimori down <target> [--json]
```
Display all downstream **callees** (functions, methods, types) invoked or constructed by `<target>`. Call-only: property reads, type references, and call-arg/template identifiers are mentions, not edges — see `uses`.

### `uses`
```shell
mimori uses <target> [--json]
```
Display all **mentioners**: symbols whose non-call mentions (member-property reads like `error.command`, type references, call-argument and template-interpolation identifiers) name `<target>`. Never a call edge — the queryable half of the calls/mentions split that keeps field-like names out of centrality and blast radius.

### `missing`
```shell
mimori missing <pattern> [--scope <dir>] [--defines <lit>] [--json]
```
List files under `--scope` whose content lacks `pattern` — the absence query no graph verb can express (e.g. which lane file has no `Remote` branch). A walker sweep, not graph work: `|` separates literal alternatives (`Remote|serverId`), `--defines` restricts to files containing a marker. Only indexed (supported-extension) files are swept.

### `blast`
```shell
mimori blast <target> [-d|--depth <N>] [--down] [--with-sinks <a,b,c>] [--json]
```
Evaluate the transitive **blast radius** (ripple impact) when `<target>` changes. Traverses the upstream reachability closure up to depth `N` (default: 3), reporting affected callers, entry points, and test suites — plus weakest-tier **value uses** (direct mentioners, `[Value Use]`, never traversed, call edge wins on overlap). An entry point is a symbol with no callers, or one named `main`; test detection matches real conventions (`tests/`, `__tests__/`, `_test.go`, `.spec.ts`, `test_*`) rather than any path containing the substring "test".
* `--json` adds a `direction` key (`"up"`/`"down"`) and a `value_uses` array (upstream only); downstream nodes set `is_sink` instead of `is_entry_point`. `up --json` carries `callers` plus `value_uses`. With `--with-sinks`, JSON also carries `sinks` and `sink_hits`.
### `doctor`
```shell
mimori doctor [--limit <N>] [--json]
```
Show repository health: file/symbol/edge counts, edge-resolution accounting (resolved / ambiguous-dropped / unresolved / external), PageRank iterations and convergence, top hubs by fan-in, and dead-weight candidates split into `likely dead` (isolated functions) vs `needs human review` (isolated types/constants — often exported surface). Framework-dispatched entries (tRPC `appRouter::*` procedures, Next.js `page|route|layout|middleware|server` files, index-barrel re-exports) are exempt; without `--limit` the likely tier caps at 50. Dead-weight is a hint, not a verdict — dynamic entry points and runtime registration produce false positives.
* `--limit <N>`: keep only the top `N` candidates per tier.

### `init`
```shell
mimori init
```
Initialize the `.mimori` workspace directory and cache storage.

### `clean`
```shell
mimori clean [--all]
```
Purge the embedded SQLite cache (`.mimori/index.db`, WAL, SHM) to force a fresh re-index on the next command. If `--all` is passed, also removes `.mimori/.cache/`.

---

## COORDINATE SYNTAX

Commands accept coordinates in three formats:
1. **Symbol Coordinate**: `path/to/file:<symbol>`  
   Targets a specific declaration within a file (e.g., `src/auth.rs:authenticate` or `src/service.ts:UserService::findUser`).

1. exact workspace-relative path (absolute paths normalize into this tier),
2. path suffix on a **component** boundary (`alpha/mod.rs` matches `src/alpha/mod.rs`, `ha/mod.rs` does not),
3. basename alone.

### Resolution order

Call-edge resolution is tiered per reference: same-file, then same-directory, then unique-global. A tier with more than one candidate is ambiguous — the edge is dropped and counted (`ambiguous-dropped` in the `map` header and `doctor`), never picked by index order, and property/type/mention names never become edges at all. Three precision guards sit in front of the tiers: member calls (`X.foo()`, receiver unknown) resolve same-file or drop, never fuzzy; only Function/Method/Class/Struct/Enum kinds may receive edges (calls landing on Variables/Fields are type errors and drop); calls matching a name the file imports from a module outside this workspace are marked `external` and never resolved locally. That classification is three-way, not two-way: a specifier is first-party when it is relative, when it matches a `tsconfig.json`/`jsconfig.json` `compilerOptions.paths` key (following `extends`, including a shared base addressed by package name), or when it is a workspace member's `package.json` `name`. Bare `new`/`default` skip fuzzy tiers — qualified `S::new` refs carry those edges.

**If any tier matches more than one symbol, the command exits `1` and prints the candidates
ranked by centrality.** `mimori` never silently picks between two files sharing a basename
such as `mod.rs`, `index.ts`, or `__init__.py`.

---
### Centrality & In-Degree PageRank
Rather than dumping symbols alphabetically or in source order, `mimori` models caller $\to$ callee dependency topology and computes weighted in-degree PageRank via power iteration ($d = 0.85$, convergence at $\lVert next - scores \rVert_1 < 10^{-6}$, cap 100 iterations; iters and convergence print in the `map` header and `doctor`). Only true call edges feed the graph — member-property reads, type references, and call-arg/template mentions are tracked separately and queried via `uses`. Edge weights scale sublinearly with call-site multiplicity ($\min(\sqrt{n}, 8)$), so a 50-callsite dependency outweighs a 1-callsite one without dominating. Test-file callers keep their edges at quarter weight, so test-only call density stops outranking the application's hubs. Foundational abstractions (traits, types, shared utilities) rank highest, ensuring token budgets are spent on architectural backbones rather than leaf helpers.
### Persistence & Incremental Synchronization
Parsed symbols, coordinates, calls, mentions, member-call flags, call-site counts, and per-file external imports are persisted into `.mimori/index.db` using embedded SQLite with WAL (Write-Ahead Logging). Centrality is recomputed on load rather than stored.

Re-parsing is decided by **FNV-1a content hash**, not by timestamp: every source file is read and hashed on every run, and only files whose hash changed are re-parsed. Trusting mtime would let `cp -p`, `touch -r`, `rsync -t` or a `tar` extraction leave the index permanently stale. Reading and hashing costs roughly 0.4% of a warm run.

The index is derived data, safe to delete at any time, and rebuilds automatically when the embedded parser version changes.

---

## AGENT NAVIGATION WORKFLOW

When exploring or modifying a codebase, AI agents should follow the **Canopy $\to$ Slice $\to$ Blast $\to$ Log** discipline:

1. **Canopy (Orientation)**:
   ```shell
   mimori map --scope <dir> --limit 100
   ```
   Inspect the high-centrality symbols and entry points of the target subsystem without reading raw files. `--limit` caps the output at the top `N` symbols by centrality, which is what keeps a large workspace inside a token budget.

2. **1-Hop Slice (Inspection)**:
   ```shell
   mimori slice <file:symbol> -f
   ```
   Retrieve the exact target symbol body along with its 1-hop callers, callees, and inlined private helpers. Consumes ~120 tokens vs 3,000+ for whole-file reads.

3. **Blast Radius (Pre-edit Check)**:
   ```shell
   mimori blast <file:symbol>
   ```
   Identify all upstream callers, public entry points, and test suites that could break before editing a signature or contract.

---

## EXAMPLES

### 1. Initialize cache storage
```shell
mimori init
```

### 2. Search for high-centrality symbols
```shell
mimori find "authenticate" -s
```

### 3. Extract 1-hop AST context slice with inlined private helpers and imports
```shell
mimori slice src/auth/service.rs:authenticate -f -i
```

### 4. Traverse dependency callers and callees
```shell
mimori up src/auth/service.rs:authenticate
mimori down src/auth/service.rs:authenticate
```

### 5. Check blast radius before refactoring a core function
```shell
mimori blast src/db/connection.rs:query -d 3
```

### 6. Focus architectural map on a specific subsystem
```shell
mimori map --scope src/payment --focus PaymentGateway
```

### 7. Force full re-indexing of the repository
```shell
mimori clean --all
```

---

## FILES
* `.mimori/index.db`: Embedded SQLite database storing file records, parsed symbols, and PageRank centrality scores.
* `.mimori/index.db-wal`: SQLite write-ahead log.
* `.mimoriignore`: Optional ignore file supplementing `.gitignore` for custom exclusion patterns.

---

## ENVIRONMENT
* `MIMORI_PROFILE`: When set to any value, prints per-phase indexing timings to stderr. Costs nothing when unset.

---

## EXIT STATUS
* `0`: Success.
* `1`: Symbol not found, ambiguous coordinate, coordinate parse error, unreadable workspace, or database error.
* `2`: Invalid command-line arguments.

---

## SEE ALSO
`rg`(1), `tree-sitter`(1), `git`(1)

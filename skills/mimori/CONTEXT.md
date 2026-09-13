# mimori-rs

A zero-config, high-performance Rust AST code-intelligence and symbol-graph CLI that enables one-shot discovery, context slicing, and dependency traversal for AI agents and developers.

## Language

**Symbol**:
A named code construct (function, struct, method, class, enum, interface, trait, type alias, or top-level variable) declared in a source file.
_Avoid_: token, identifier, entity

**Slice**:
An isolated, token-dense view containing a symbol's exact source body, coordinates, signature, and immediate 1-hop dependencies.
_Avoid_: snippet, chunk, window

**Caller**:
An upstream symbol or function that invokes, references, or depends on a target symbol.
_Avoid_: parent, dependent, consumer

**Callee**:
A downstream symbol or function that is invoked or referenced by a target symbol.
_Avoid_: child, dependency, sub-routine

**Blast Radius**:
The transitive closure of upstream callers and entry points impacted by a modification to a target symbol.
_Avoid_: impact tree, ripple effect, cascade

**Map**:
A hierarchical, ranked structural overview of top-level symbols, modules, and entry points across a codebase.
_Avoid_: outline, summary, tree

**Coordinate**:
An unambiguous path and line identifier targeting a symbol or line range (e.g., `src/auth.rs:login` or `src/auth.rs:#L40-85`).
_Avoid_: location, position, pointer

**Centrality**:
A graph-theoretic score measuring a symbol's structural importance based on inbound dependency topology.
_Avoid_: weight, popularity, importance

# 2. Syntactic Graph with SQLite Persistence

We decided to resolve cross-file symbol relationships using syntactic AST queries and heuristic import matching, persisted into `.mimori/index.db` via embedded SQLite (`rusqlite` bundled) with WAL mode and file mtime/hash invalidation.

Exact compiler-level semantic resolution requires language-specific toolchains and fails when files are in intermediate, broken states during agent editing loops. Syntactic Tree-sitter graphs provide zero-config instant lookups across heterogeneous multi-language repositories. Embedded SQLite provides sub-millisecond point queries, atomic incremental updates on single-file changes, and zero-RAM overhead compared to monolithic binary deserialization.

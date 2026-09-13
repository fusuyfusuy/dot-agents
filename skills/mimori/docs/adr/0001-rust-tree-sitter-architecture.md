# 1. Rust CLI with Embedded Tree-sitter Grammars

We decided to build mimori-rs as a standalone Rust CLI binary with statically embedded Tree-sitter parsers (Rust, TypeScript/JavaScript, Python, Go) rather than a Node/Python script or an LSP/SCIP client.

Agents require sub-50ms cold-start execution and deterministic parsing on untrusted, dirty, or partial codebases without requiring language runtimes, npm installs, or complex compiler environments. Rust delivers single-binary portability and multi-threaded Rayon performance.

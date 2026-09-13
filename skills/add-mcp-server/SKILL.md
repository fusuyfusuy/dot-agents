---
name: add-mcp-server
description: "Opt this project into extra MCP servers from the shared library (ast, browser, context7, context, akatsuki) for this project only. Use when the user asks to add, enable, or set up an MCP server in a repository."
disable-model-invocation: true
---

# Add MCP Server to Project

Globally, exactly three MCP servers are active everywhere: **akatsuki**,
**context-mode**, and **mimori** (see `mcp/global.json`). Every
global server's tool list is injected into every session, so the global set is
deliberately closed. Everything else is **opt-in per project** through this skill.

## Which servers exist

| Name | Provides |
| :--- | :--- |
| `ast` | AST pattern rewriting (ast-grep), LSP diagnostics, blast radius |
| `browser` | Headless browser automation via Playwright |
| `context7` | Up-to-date framework/library/package docs lookup |
| `context` | context-mode — only needed if the global entry is unavailable |
| `akatsuki` | Second-brain gateway — same server as the global one |

## Process

### 1. Pick the server(s)

Confirm with the user which server they want. If unsure what is available, list
the library first:

```bash
enable-mcp list
```

### 2. Enable for this project

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
enable-mcp <server> "$PROJECT_ROOT"
```

Multiple servers, or everything:

```bash
enable-mcp ast "$PROJECT_ROOT"
enable-mcp browser "$PROJECT_ROOT"
enable-mcp all "$PROJECT_ROOT"      # opt into every library server
```

This writes two things into the project:

- `.agents/plugins/<name>` — symlink to the shared library (Antigravity / agent-plugins)
- `.pi/mcp.json` — `mcpServers` entry for pi (project scope)

### 3. Verify

```bash
enable-mcp status "$PROJECT_ROOT"
```

### 4. Document it

Add a short section to the project's `AGENTS.md` so the choice is discoverable:

```markdown
## MCP servers

Enabled locally for this project: `ast`, `browser`.
```

### 5. Tell the user to restart

The new servers are picked up on the next session start, not mid-session.

## Removing

```bash
enable-mcp disable <server> "$PROJECT_ROOT"
enable-mcp disable all "$PROJECT_ROOT"
```

## Rules

- **Never** add a server to the global set as a shortcut. Global additions go
  through `mcp/global.json` and must be a deliberate repo edit.
- `mimori` is already global — do not enable it per project; it defaults to the
  current workspace, so one global entry covers every repository.
- Do not edit `.pi/mcp.json` by hand — the script merges and unmerges `mcpServers`
  safely. A hand-edit tends to clobber the global entry's precedence.
- Server definitions live in `mcp-library/<name>/mcp_config.json`.
  A new third-party server needs a directory there before `enable-mcp` can reach it.

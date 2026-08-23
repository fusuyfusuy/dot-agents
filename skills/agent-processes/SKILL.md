---
name: agent-processes
description: Recipes for git worktree isolation. Use when orchestrating multi-step or multi-agent coding work or setting up workspace isolation.
---

# Agent Processes

Recipes supporting the Subagent Management Protocol in `AGENTS.md` (read that first for the rules).

## When to use

- Orchestrating multi-step or multi-agent coding tasks.
- Setting up workspace isolation to avoid concurrent write collisions.

## Workspace Isolation

```bash
git worktree add -b agent/task-<id> ../worktrees/task-<id> origin/main
cd ../worktrees/task-<id>
# verify locally, then:
git diff origin/main...HEAD > /tmp/task-<id>.patch
git worktree remove ../worktrees/task-<id>
```

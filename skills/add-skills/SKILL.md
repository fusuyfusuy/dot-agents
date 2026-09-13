---
name: add-skills
description: "Opt this project into skill suites from the shared library (interfaces, matt, specterops) for this project only. Use when the user asks to add, enable, or set up skills in a repository."
disable-model-invocation: true
---

# Add Skills to Project

The shared skill suites deliberately live in a library and are **never global**.
A skill in a global root is discovered by every session and its description is
injected into every system prompt regardless of what the project needs, so
suites are opt-in per project through this skill.

## Which suites exist

| Suite | Contents |
| :--- | :--- |
| `interfaces` | Jakub Krehel design & UI polish (better-ui, better-colors, …) |
| `matt` | Matt Pocock engineering & architecture skills |
| `specterops` | All security skills (75) |
| `specterops:<sub>` | `bloodhound`, `appsec`, `recon`, `c2`, `re`, `payloads`, `infra` |

List everything currently available:

```bash
enable-skills list
```

A single skill can also be enabled by name, e.g. `enable-skills better-ui`.

## Process

### 1. Pick the suite

Confirm which suite the user wants. Sub-suites exist for `specterops` so you can
pull in AppSec without dragging in C2 and payload tooling.

### 2. Enable for this project

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
enable-skills <suite> "$PROJECT_ROOT"
```

Examples:

```bash
enable-skills interfaces "$PROJECT_ROOT"
enable-skills matt "$PROJECT_ROOT"
enable-skills specterops:appsec "$PROJECT_ROOT"
enable-skills specterops "$PROJECT_ROOT"          # all 75
```

This symlinks each skill into `<project>/.agents/skills/<name>`, which pi reads
for trusted projects.

### 3. Verify

```bash
ls -l "$PROJECT_ROOT/.agents/skills"
```

Every entry must be a symlink resolving into
`configs/agents-config/skills-library/`.

### 4. Document it

Add a section to the project's `AGENTS.md`:

```markdown
## Agent skills

Enabled locally for this project: `interfaces`, `specterops:appsec`.
```

### 5. Tell the user to restart

Project skills load at session start.

## Rules

- **Never** copy or link a library skill into a global root (`~/.agents/skills`,
  `~/.pi/agent/skills`, `~/.claude`, `~/.omp/agent`, …). That is global
  pollution, and the activator refuses it. If a skill genuinely belongs
  everywhere, it goes in `skills/` (the fan-out source) as a
  deliberate repo edit.
- Never copy skill files into a project — always symlink, so library updates
  propagate.
- If `enable-skills doctor` reports a leak or suite drift, fix the library before
  enabling anything. Do not work around it.

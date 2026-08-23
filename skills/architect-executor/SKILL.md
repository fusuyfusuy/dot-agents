---
name: architect-executor
description: "Workflow pattern with 3.7 Flash as master orchestrator, 3.1 Pro as strategic architect/planner/auditor, and 3.7 Flash for bulk execution. Use when orchestrating complex design tasks that require deep reasoning and cheap, high-speed code generation."
---

# Architect-Executor Handoff Protocol

This skill formalizes the 3-tier model cost/capability optimization strategy: leveraging **Gemini 3.7 Flash High** as the fast, agile master orchestrator, **Gemini 3.1 Pro** for high-value strategic planning, design contracts, and detached auditing (`Model: pro`), and **Gemini 3.7 Flash High** for high-speed, cost-effective bulk code execution (`Model: flash`).

## When to Use
- The task requires complex system design, deep repository exploration, or critical decision-making, while the code generation is large, multi-file, or mechanical.
- The user requests the "architect pattern", "design and handoff", or multi-agent delegation.
- You (3.7 Flash Master) need deep architectural planning or independent auditing on complex logic before bulk implementation.

## The 4-Phase Protocol

### 1. Design & Planning Phase (3.1 Pro Subagent or Direct Master)
- For high-complexity tasks: Master (3.7 Flash) calls `invoke_subagent` with `Model: "pro"`, `Role: "Pro Architect"`.
- The Pro Architect computes context (`mimori`, `rg`, `fd`), evaluates invariants, and drafts a strict, 1-paragraph plan (the "Contract") outlining exact files, logic, and verification commands.
- For straightforward tasks: 3.7 Flash Master drafts the contract directly.

### 2. Approval Gate
- **STOP.** The user must explicitly approve the plan.
- **How to ask:** Write the execution plan into an Artifact (using `write_to_file`) with `RequestFeedback=true` in `ArtifactMetadata`. This displays the plan in the side panel with a native "Proceed" button. Provide an expansive executive summary directly in chat.
- **CRITICAL**: NEVER use the `ask_question` tool for plan approval.
- Do not proceed until the user explicitly approves.

### 3. Bulk Execution Phase (3.7 Flash Subagent)
- Upon approval, Master calls `invoke_subagent` with `Model: "flash"`, `Role: "Flash Executor"`.
- **Prompt**: Pass the approved contract and boundaries. Instruct the Flash worker: *"Do not re-architect. Implement strictly according to the plan. Verify your changes via tests or terminal commands. Return a technical executive summary of completed work."*
- Flash worker executes in parallel or isolated git worktree and reports back.

### 4. Review & Audit Phase (3.1 Pro Auditor / Master)
- For non-trivial or critical changes: Master invokes `Model: "pro"` as `"Pro Auditor"` for an independent GLLA audit pass.
- **Dual-Mandate Audit**: The Pro Auditor inspects the full `git diff` for code quality, invariant compliance, absence of hardcoded values, and test assertion validity, while independently executing verification contract commands (exit 0).
- Emits explicit `AUDIT VERDICT: PASS` or `AUDIT VERDICT: FAIL` before session sign-off.
- Complete session by logging activity using `mimori log`.


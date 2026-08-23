---
name: goal-audit
description: >-
  Deterministic goal drafting, verification contract execution, and detached subagent auditing.
  Enforces the anti-bamboozle rule (GLLA protocol) where work cannot self-grade or complete without
  independent machine-verifiable proof. Use for long-running, multi-file, or unattended work.
---

# Goal-Audit (GLLA Protocol)

`goal-audit` ports the core anti-bamboozle control plane from GLLA (`pi-goal-list-loop-audit`) into the Antigravity (`agy`) and Gemini 3-tier subagent architecture.

> **Core Axiom (Anti-Bamboozle)**:
> An agent **never grades its own work**. Completion requires an independent, machine-verifiable verification contract evaluated by a detached auditor pass.

---

## 1. 3-Tier Model Delegation Topology

The `/goal` workflow operates strictly under the 3-tier delegation contract:
- **Master Orchestrator (Gemini 3.7 Flash High)**: Fast interactive turn orchestration, user communication, grilling intake, and tool dispatch.
- **Architect & Detached Auditor (Gemini 3.1 Pro)**: High-value strategic planning, design contracts, and detached goal auditing via `invoke_subagent` with `Model: "pro"`.
- **Bulk Execution Worker (Gemini 3.7 Flash High)**: High-speed, bounded implementation via `invoke_subagent` with `Model: "flash"`.

---

## 2. The 5-Phase Contract Loop

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ 0. Grill Intake  │ ──> │ 1. Draft Contract│ ──> │ 2. Approval Gate │ ──> │ 3. Bulk Execute  │ ──> │ 4. Detached Audit│
│    (ask_question)│     │    & Seed Plan   │     │ (RequestFeedback)│     │   (Model: flash) │     │    (Model: pro)  │
└──────────────────┘     └──────────────────┘     └──────────────────┘     └──────────────────┘     └──────────────────┘
                                                                                                              │
                                                                                                              ▼
                                                                                                    ┌──────────────────┐
                                                                                                    │ 5. Sign-Off &    │
                                                                                                    │    GOAL_COMPLETE │
                                                                                                    └──────────────────┘
```

### Phase 0: Intake & Grilling Gate (`ask_question`)
When the user invokes `/goal`, never assume scope or proceed on underspecified requirements:
- Use the `ask_question` tool to present 1–4 structured multiple-choice questions in a single round.
- Interrogate:
  1. Exact edge cases and boundary conditions.
  2. Machine-verifiable test commands (unit tests, CLI exit codes, HTTP checks).
  3. Strict Definition of Done (DoD).

### Phase 1: Pro Architecture Design & Verification Contract (`Model: "pro"`)
- For multi-file or non-trivial architectural goals: Master (3.7 Flash) invokes **Gemini 3.1 Pro** (`invoke_subagent` with `Model: "pro"`, `Role: "Pro Architect"`).
- The Pro Architect explores the repository (`mimori`, `rg`, AST call graphs), evaluates invariants and edge cases, and drafts the 1-paragraph plan alongside the deterministic **Verification Contract** (the exact machine commands that must exit 0 to prove success):

```markdown
### Goal: Add real-time PTY unbuffering to agy-proxy

**Verification Contract**:
1. `pytest tests/test_proxy.py` exits 0.
2. `curl -N http://127.0.0.1:58285/health` returns status "ok".
3. `git diff --stat` touches only `tui-agent-settings/antigravity-cli/agy-proxy.py`.
```

### Phase 2: Approval Gate (Artifact with `RequestFeedback=true`)
For multi-file or non-trivial structural changes (>10 lines):
- Write the plan and contract into an Artifact using `write_to_file` with `ArtifactMetadata` property `RequestFeedback=true`.
- Provide an expansive executive summary in chat.
- Wait for explicit user approval via the native "Proceed" button. **NEVER** use `ask_question` for plan approval.

### Phase 3: Implementation & Bulk Execution (`Model: "flash"`)
- Apply changes following the Ponytail ladder (fewest files, standard libraries, no unneeded abstractions).
- For large changes, delegate to a `Model: "flash"` subagent with strict boundary instructions: *"Do not re-architect. Implement strictly according to the plan."*
- Run local checks without self-signing completion.

### Phase 4: Detached Pro Auditor Pass (`Model: "pro"`)
Spawn an independent auditor subagent using `invoke_subagent` with `Model: "pro"` and `Role: "Pro Auditor"`. Provide the auditor **only** the goal objective, the verification contract commands, and the working directory.

The Pro Auditor must execute a **dual-mandate audit** before approving:

1. **Semantic & Architectural Code Review (`git diff`)**:
   - Inspect the raw `git diff` against codebase invariants, gotchas, and Ponytail principles.
   - Verify code quality: check for proper error handling, absence of hardcoded test-satisfying values, no credentials/secrets leaked, and no scope creep outside contract boundaries.
   - Verify test validity: confirm that test assertions test genuine domain invariants rather than trivial tautologies (`assert True`).
2. **Independent Machine Contract Execution**:
   - Execute each verification contract command in the terminal and capture raw exit codes.
3. **Structured Audit Verdict**:
   - Emit an explicit verdict:
     - `AUDIT VERDICT: PASS` — with technical rationale detailing verified code quality and test outputs.
     - `AUDIT VERDICT: FAIL` — with exact file, line number, and defect breakdown. The implementation agent iterates to fix findings.

### Phase 5: Sign-Off, Telemetry & Goal Termination
Once the detached auditor issues `AUDIT VERDICT: PASS` and all contract commands exit 0:
1. Log activity with `mimori`:
   ```bash
   mimori log \
     --action "complete-goal-name" \
     --summary "One-line high-level outcome in caveman style" \
     --files "path/to/touched/files"
   ```
2. Update `.mimori/memory.md` (invariants/gotchas) and `.mimori/decisions.md` (ADRs).
3. Conclude the Antigravity background loop:
   - Include `<!-- GOAL_COMPLETE -->` when fully verified.
   - Include `<!-- GOAL_CANCELLED -->` if the goal was explicitly aborted by the user.

---

## 3. Rules & Invariants

- **No Self-Grading**: Collapsed tool outputs or model assertions like *"tests passed"* do not count as proof. Only raw command exit codes and semantic diff review evaluated by the auditor are valid.
- **Strict Scope**: Keep subagent scope bounded to one isolated file, symbol, or question before invoking.
- **Fail Fast & Escalate**: If the same test or check fails 3 times in a row: stop, `git reset --hard`, discard hypothesis, re-read source code from first principles, and re-present an updated plan.

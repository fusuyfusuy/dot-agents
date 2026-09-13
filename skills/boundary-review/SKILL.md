---
name: boundary-review
description: >-
  Boundary-first architectural and code review protocol with explicit Seam-Auditing,
  standardized scoring calibration, interactive executive approval gate, and machine-verifiable verification.
  Harness- and model-agnostic: operates via parallel subagents or sequential passes across any agent environment.
---

# Boundary-Review Protocol

`boundary-review` provides a deterministic architectural audit protocol that decomposes complex codebases along natural subsystem boundaries, audits both internal components and cross-boundary seams, presents an interactive executive scorecard for operator review, and executes remediations only upon explicit approval.

> **Core Axiom**:
> Deep architectural audits require **isolated horizontal scoping** for high context efficiency combined with **explicit seam-auditing** to eliminate interface blind spots. **Audits are diagnostic by default; code remediation requires explicit user approval.**

---

## 1. Architectural Topology

```mermaid
graph TD
    ORCH[Master Orchestrator / Review Driver] -->|Boundary Discovery| MAP[mimori map & doctor / AST Survey / File Tree]
    ORCH -->|Dispatch: Parallel Swarm OR Sequential Pass| SCOPES[Auditor Workflows]

    subgraph Horizontal Scope Auditors [Profile: Fast / Balanced Code Analyzer]
        S1[Scope 1: Ingestion / Edge Adapters]
        S2[Scope 2: Core Domain / Storage Engine]
        S3[Scope 3: Backend API / Services]
        S4[Scope 4: Client / Frontend / Presentation]
        S5[Scope 5: Infra / Tooling / CI / Config]
    end

    subgraph The Seam Auditor [Profile: Deep Reasoning / Frontier Multi-Hop]
        SEAM[Seam Auditor: Cross-Boundary Contract Diffing]
    end

    SCOPES --> Horizontal Scope Auditors
    SCOPES --> The Seam Auditor

    Horizontal Scope Auditors -->|Write to Disk| REVIEWS[reviews/scope_*.md]
    The Seam Auditor -->|Write to Disk| REVIEWS

    REVIEWS -->|Synthesize| MASTER[master_architectural_audit_report.md]
    MASTER -->|Executive Summary in Chat| GATE{Approval Gate: Wait for User}
    GATE -->|User Confirms: proceed / fix| EXEC[Execute & Machine-Verify]
    GATE -->|User Defers / Modifies| REVISE[Adjust Plan or Stop]
```

---

## 2. The 6-Phase Review Loop

### Phase 1: Boundary Mapping & Scoping
1. **Canopy Survey**: Map repository structure, architectural hubs, and module boundaries.
   - *With mimori (v2.2+)*:
     - Run `mimori map --limit <N>` (e.g. `--limit 30`; always pass `--limit` on unfamiliar repos).
     - Inspect top architectural hubs, fan-in, and dead-weight candidates via `mimori doctor --limit 15`.
     - For session warmup context on turn 0: query prior architecture, deploy, and project context via `akatsuki search "<query>"` and `akatsuki read <note> [--section <heading>]`. Code-shape warmup stays in-repo: `mimori doctor --limit 15` for hubs and dead-weight candidates. (No snapshot command exists: `mimori log`/`mimori dump` and the `.mimori/` knowledge files were retired in mimori 2.x — see its ADR-0004; `mimori` is code-intelligence only, and repo-local `.agents/memory.md` is never scaffolded.)
   - *Without mimori*:
     - Survey top-level package roots, entry points, and module interfaces via `rg`, `ast-grep`, or directory trees.
2. **Partitioning**: Divide the codebase into 3–6 non-overlapping, high-cohesion horizontal scopes (e.g., Ingestion/Parsers, Core Domain/Storage, Public API/Routing, Presentation/UI, Infrastructure/CI).
3. **Plan Artifact**: Formulate the review plan outlining target scopes, files, and identified boundary interfaces.

### Phase 2: Horizontal & Seam Scope Definition
Define distinct evaluation briefs for:
- **Horizontal Auditors** (1 per subsystem): Evaluates internal correctness, robustness, memory/performance, and security vulnerabilities strictly within that boundary.
- **The Seam Auditor** (1 across all boundaries): Focuses *exclusively* on contract seams:
  1. **API & RPC Seams**: Backend route schemas / OpenAPI models vs Frontend clients / SDKs / Zod schemas.
  2. **Persistence Seams**: Database migrations / DDL vs Repository queries, ORM models, and domain entities.
  3. **Async Event & Queue Seams**: Event bus / message queue producers (payload shapes, topic names) vs consumer handlers.
  4. **Configuration Seams**: Environment variable definitions / config schemas vs Docker, Helm, CI/CD, and `.env.example`.
  5. **Auth & Permission Boundaries**: Middleware authentication / RBAC guards vs individual route handler permissions.
  6. **Documented Invariants**: Architecture specifications / `AGENTS.md` rules vs live runtime enforcement paths.

*Seam Inspection Intelligence (mimori v2.2+)*:
- Use `mimori uses <target>` to uncover non-call mentions (property reads like `req.user`, type references, call arguments, template interpolations) that cross subsystem boundaries.
- Use `mimori missing <pattern> [--scope <dir>]` to perform absence sweeps across boundary files (e.g., locating routes lacking rate-limiting or auth wrappers).
- Use `mimori blast <target> [-d <N>] [--down] [--with-sinks <sinks>]` to trace downstream boundary sinks and ripple impact.

### Phase 3: Execution Dispatch (Harness- & Model-Agnostic)

This protocol executes deterministically across any agent harness or model tier:

#### A. Execution Modes
- **Multi-Agent / Swarm Harnesses** (environments supporting concurrent subagents or background worker tasks):
  - Dispatch horizontal auditors and the seam auditor concurrently in a single flat batch.
  - **Flat Execution Rule**: Subagents must perform analysis directly without spawning nested child subagents.
- **Single-Agent / Linear Harnesses** (environments operating sequentially within a single conversation context):
  - Execute sequentially: iterate through each horizontal scope one-by-one, followed by the seam audit.
  - Write each scope's findings to disk at `reviews/scope_<id>_<name>.md` before moving to the next.

#### B. Cognitive Model Profiles (Vendor-Agnostic Tiers)
When the harness supports model or reasoning effort selection:
- **Horizontal Scope Auditors**: Configure for **Fast / Balanced code analysis** (high-throughput, lower-latency execution optimized for AST traversal and bounded scope inspection).
- **The Seam Auditor**: Configure for **Deep Reasoning / High-Effort analysis** (extended thinking or frontier reasoning capability for multi-hop critical-path reasoning and cross-boundary discrepancy detection).
- *Default / Unconfigurable*: Use the environment's default configuration (`inherit`).

#### C. Tooling Agnosticism & Cost-Aware Routing
- **Targeted Symbol / Body Slicing**:
  - *With mimori*: Use `mimori slice <coordinate> [-f] [-i] [--budget <N>]` (inlines private local callees with `-f`, prepends imports with `-i`, and bounds output tokens via `--budget`).
  - *Without mimori*: Use targeted line-range reads (`view_file` with StartLine/EndLine) or `ast-grep`. Never pull whole files >250 lines or >3KB into context for isolated facts.
- **Traversal & Call Graphs**:
  - *With mimori*: Use `mimori up <target>` for callers/constructors, `mimori down <target>` for callees, and `mimori uses <target>` for non-call mentioners.
  - *Without mimori*: Use `rg` / `grep` or language LSP references.
- **Deliverable Target**: Each auditor writes its complete markdown report to `reviews/scope_<id>_<name>.md` and provides an executive summary.

### Phase 4: Standardized Scoring & Severity Calibration
To eliminate subjective scoring variance across agents or passes, evaluate against this standardized matrix:

| Score Band | Severity | Criteria & Failure Mechanisms |
| :---: | :---: | :--- |
| **< 7.0** | **Critical** | Remote code execution, unescaped XSS/injection, silent data loss / dropped records, process-level OOM / crash loops, unauthenticated mutating endpoints, unrecoverable deadlock. |
| **7.0 – 8.4** | **Moderate** | Missing rate limiting on public endpoints, CORS misconfigurations, unhandled async promise rejections / missing fallbacks, memory bloat without immediate crash, invariant drift between subsystems. |
| **8.5 – 9.4** | **Minor** | Missing validation on optional query parameters, client-side display rounding nuances, non-critical regex fallback improvements, incomplete docstrings on public boundaries. |
| **9.5 – 10.0** | **Exemplary** | Zero invariant violations, strict parameterized queries, complete error isolation, comprehensive test coverage with zero resource leaks. |

### Phase 5: Master Synthesis & Executive Approval Gate (MANDATORY STOP)
1. **Master Consolidated Artifact**: Synthesize all review files into a single master report (`master_architectural_audit_report.md`):
   - Executive scorecard table (Score, Status, Finding Counts).
   - Exact line-numbered citations using markdown links (`[path/to/file#L123](file:///absolute/path/to/file#L123)` or `path/to/file:L123`).
   - Prioritized remediation roadmap (P1: Security/Crash -> P2: Invariants/Robustness -> P3: Docs/Polish).
2. **Interactive Chat Summary & Approval Gate**:
   - Present a concise summary in chat (scorecard, top 3–5 key findings, and artifact path).
   - **MANDATORY GATE**: DO NOT modify any code or execute remediations automatically. Audits are strictly diagnostic until confirmed.
   - Prompt the operator for explicit approval or selection of remediation batches.
   - Stop execution and wait for operator instruction.

### Phase 6: Approved Remediation & Verification (Post-Approval Only)
1. **Execution Gate**: Upon receiving operator confirmation (e.g. "proceed", "fix P1", or specific scope approvals), implement minimal diffs adhering to the plan.
2. **Machine-Verifiable Proof**:
   - **Two-Phase Invariant Check**: For bug fixes, reproduce the failure with an automated test or smoke command (exit $\ne$ 0), apply the minimal diff, and verify the test passes (exit 0).
   - Run available test suites, linters, and typecheckers to confirm clean exit codes.
3. **Activity Logging**: Record remediation actions to repository history via `akatsuki log -p <project> -s "<summary>"` (summary <160 chars), or via clean git commits. Do not use `mimori log` — it was retired in mimori 2.x (ADR-0004); action logging belongs to akatsuki.

---

## 3. Subagent & Execution Prompt Templates

### A. Horizontal Scope Auditor Prompt Template
```markdown
You are the [Scope Name] Auditor for [Project Name].
Your goal is to perform a deep, rigorous audit of the [Scope Name] boundary.

Target files:
- [List specific files / directories]
- [Relevant test files and docs]

Review Dimensions:
1. Correctness: Invariant compliance, domain models, pure functions vs network boundaries, data transformations.
2. Robustness: Error isolation, timeout handling, retry backoff, malformed input tolerance, crash resistance.
3. Performance: Memory footprint (streaming vs buffering), query efficiency, event loop blocking.
4. Security: Injection risks, authentication/authorization, input sanitization, safe deserialization.

Instructions:
- Do NOT spawn subagents; perform direct analysis using targeted code inspections (e.g. `mimori slice <file>[:<sym>]` or targeted line-range reads).
- Scope Bounds: Confine analysis strictly to target files. State factual findings and exact line references directly.
- Apply the standardized scoring rubric (Critical <7, Moderate 7-8.4, Minor 8.5-9.4, Exemplary 9.5-10).
- Write your full report to `reviews/scope_<id>_<name>.md`.
- Return an executive summary including: Health Score (0-10), Invariant Breaches, Top Findings with exact file:line references, and Prioritized Actionable Remediations.
```

### B. Seam Auditor Prompt Template
```markdown
You are the Cross-Boundary Seam & Interface Auditor for [Project Name].
Your SOLE goal is to inspect the seams and contract interfaces between subsystems.

Contract Seams to Cross-Check:
1. API & RPC Contracts: Backend route schemas / OpenAPI models vs Frontend typed clients / Zod schemas.
2. Persistence Contracts: Database migrations / DDL vs Repository queries and domain models.
3. Async Event & Queue Contracts: Event producers and queue payloads vs consumer handlers.
4. Configuration Seams: Environment variables / Settings defaults vs Docker Compose / Helm / CI workflows.
5. Auth & Permission Boundaries: Middleware authentication / RBAC guards vs individual endpoint permissions.
6. Invariant Contracts: Rules documented in architecture docs / `AGENTS.md` vs runtime enforcement mechanisms.

Analysis Tools (use available tools):
- Inspect non-call mentioners across boundaries (e.g. `mimori uses <symbol>`).
- Check for missing boundary validation (e.g. `mimori missing <pattern> --scope <dir>`).
- Check downstream sinks and blast radius (e.g. `mimori blast <symbol> --down`).
- Diff contract interfaces using targeted line-range reads or slices.

Instructions:
- Perform targeted diffing across the boundary files on both sides of each seam.
- Scope Bounds: Focus deep reasoning strictly on contract divergence and schema discrepancies between boundaries.
- Apply the standardized scoring rubric (Critical <7, Moderate 7-8.4, Minor 8.5-9.4, Exemplary 9.5-10).
- Write your full report to `reviews/scope_seams.md`.
- Return an executive summary highlighting contract drifts, schema mismatches, and specific file:line citations on both sides of each seam.
```

---

## 4. Quick Reference Checklist

- [ ] Survey codebase (via `mimori map --limit 30` & `mimori doctor` or file tree/grep) and partition into 3–6 cohesive horizontal scopes + 1 Seam scope.
- [ ] Dispatch auditor workflows: parallel batch for multi-agent swarms, or sequential passes for single-agent harnesses.
- [ ] Select appropriate model profile if supported (Balanced tier for horizontal; Deep Reasoning tier for seams; or `inherit`).
- [ ] Enforce flat execution (auditors inspect directly, no child spawning).
- [ ] Collect written markdown reviews from disk (`reviews/scope_*.md`).
- [ ] Synthesize consolidated master report (`master_architectural_audit_report.md`) with clickable links and priority ranking.
- [ ] **MANDATORY STOP**: Present executive summary to chat with scorecard and await operator approval.
- [ ] *Only after operator approval*: Execute approved remediations (P1 -> P2 -> P3) with two-phase verification (exit $\ne$ 0 -> exit 0).
- [ ] Verify test suite passes (exit 0) and log action via `akatsuki log -p <project> -s "<summary>"` or git commit.

# Buffalo Procurement OS — Project Governance & Quality System

**Purpose:** define one durable, low-error, low-waste operating process for planning, building, reviewing, approving, releasing, and handing off Buffalo Procurement OS work across ChatGPT, Codex, Claude Code, Cursor, Replit, and GitHub.

This document is process authority. It does not replace the canonical procurement specification, `procurement/config/rules.toml`, database constraints, or readiness gates.

## 1. Governance principles

1. **One source of business truth.** The canonical specification and machine-enforced rules control procurement behavior.
2. **One writer at a time.** Only one coding agent may modify the active worktree for a task unless the owner explicitly authorizes a coordinated exception.
3. **Independent review.** The implementation agent must not be the only reviewer of material backend/data/procurement logic.
4. **Machine proof before opinion.** Deterministic tests, control totals, database constraints, and readiness gates outrank an AI statement that code “looks correct.”
5. **Fail closed.** Material ambiguity in identity, pricing, financial logic, inventory state, or PO readiness blocks trusted output.
6. **Human approval for consequential decisions.** Permanent identity mappings, exclusions, supplier mapping ambiguity, large speculative buys, pricing promotions, and PO release require the owner where defined by the canonical system.
7. **Git is the durable project memory.** Important process, current state, decisions, tests, and phase boundaries must be recoverable from the repository without relying on chat history.
8. **No silent scope expansion.** A phase/task may do only what its authorization explicitly permits.
9. **No phase completion by assertion.** A phase is complete only when its Definition of Done and required gates are satisfied by evidence.
10. **Optimize review effort by risk.** Do not spend multiple agents/tokens reviewing cosmetic changes; do spend independent review on data, money, identity, forecasting, and PO logic.

## 2. Authority and document order

When sources conflict, use this order unless a newer owner-approved authority explicitly supersedes it:

1. `procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md`
2. `procurement/config/rules.toml`
3. current database schema/migrations/constraints and tested implementation
4. `docs/PROJECT_GOVERNANCE.md` for development/review/release process
5. `docs/CODEX_HANDOFF.md` for the verified current checkpoint and authorization boundary
6. `procurement/docs/PHASE_STATUS.md` for executive roadmap/status
7. supporting architecture/build reports and historical evidence

If a material conflict remains after applying this order, stop and escalate to the owner rather than choosing silently.

## 3. Roles and responsibilities

### Owner / Sponsor / Product Authority — Vedang Patel

- final business authority and sponsor;
- approves phase boundaries and material scope changes;
- approves required permanent business decisions and real-money actions;
- accepts or rejects material tradeoffs presented by the technical team;
- does not need to perform routine coding or test execution.

### ChatGPT — Program Architect / Project Manager / Business-Rule Guardian

Primary responsibilities:

- translate owner goals into scoped phases/tasks and acceptance criteria;
- preserve canonical procurement reasoning and identify conflicts;
- define STOP conditions, human-review points, and required evidence;
- synthesize implementation reports and independent reviews;
- review business logic and control totals, not merely code style;
- maintain the program-level roadmap with the owner;
- recommend the next authorized action only after current-state evidence is understood.

ChatGPT is not a substitute for deterministic tests or owner approval.

### Codex — Primary Implementation Agent

Default writer unless the owner assigns another tool.

Responsibilities:

- read repository authorities before work;
- inspect git/database/readiness state before modifying anything;
- implement only authorized scope;
- add/update deterministic and adversarial tests;
- run full validation after material changes;
- review its diff and secret safety before handoff;
- update current handoff/status documentation at required milestones;
- stop at the authorization boundary.

### Claude Code — Independent Adversarial Reviewer / Backup Writer

Default role is reviewer, not co-writer.

Responsibilities when reviewing:

- review the actual diff and tests independently of the implementing agent's summary;
- try to disprove correctness;
- search for false-PASS gates, data loss, destructive behavior, identity ambiguity, SQL/transaction/idempotency errors, missing tests, and specification violations;
- rank findings by severity and provide exact evidence;
- do not edit during the initial review.

If Codex is unavailable, Claude Code may become the writer; another independent reviewer should then be assigned where risk warrants it.

### Cursor — Specialist Read-Only Reviewer / IDE / Backup Writer

Default use:

- human-friendly code inspection;
- targeted read-only review of a high-risk specialty (SQL/data integrity, financial math, forecasting, concurrency, performance, etc.);
- debugging support.

Do not duplicate a broad Claude review unless the risk justifies it. If used as writer, preserve the one-writer rule and assign a different reviewer.

### Replit — Runtime / Infrastructure Authority

Use for:

- production PostgreSQL;
- runtime and hosting;
- Secrets/environment;
- scheduled jobs/workflows;
- storage;
- deployment logs and health.

### Replit Agent — Replit-Specific Specialist / Reserve

Use sparingly for platform-specific issues such as deployment, Autoscale, workflow configuration, Replit networking, App Storage, or environment behavior. It is not the default coding agent.

### GitHub — Durable Source / Change-Control System

Use for:

- branches;
- commits;
- pull requests;
- automated CI/status checks;
- durable history and rollback point;
- review package for material changes.

### Deterministic Tests / Control Totals / Database Constraints — Independent Machine QA

These are evidence, not opinions. For material logic, passing machine checks is required but not always sufficient.

## 4. Standard task lifecycle

Every material task follows this sequence.

### G0 — Initiate / authorize

Owner + ChatGPT define:

- objective;
- in-scope behavior;
- out-of-scope behavior;
- acceptance criteria;
- required tests/control totals;
- irreversible/material decisions requiring owner approval;
- STOP condition.

No implementation begins from an ambiguous mandate.

### G1 — Baseline

Writer must inspect:

- `AGENTS.md` / applicable agent instructions;
- canonical authority;
- `docs/PROJECT_GOVERNANCE.md`;
- `docs/CODEX_HANDOFF.md`;
- git status/branch/HEAD;
- relevant tests;
- relevant live database/readiness state when authorized.

Unexpected changes or an invalid prerequisite gate stop work.

### G2 — Branch / change isolation

For a major phase, material backend/data change, or high-risk logic, create/use a feature branch. Do not combine unrelated work.

Small documentation/cosmetic fixes may follow a lighter path when explicitly authorized.

### G3 — Implement

Writer changes only authorized scope and preserves architecture, audit history, constraints, and fail-closed behavior.

### G4 — Machine validation

Before independent AI review:

- run existing tests;
- run new tests;
- run adversarial tests appropriate to the risk;
- run database/control-total/integrity checks where applicable;
- verify idempotency/retry/resume behavior where applicable;
- verify no secrets or unintended generated data are included.

A failing required machine check returns work to G3.

### G5 — Independent adversarial review

Required for material backend/data/business logic.

Default: Claude Code reviews without editing first.

Reviewer must examine actual code/diff and try to find defects rather than confirm the writer's narrative.

### G6 — Targeted specialist review

Use only when risk warrants it. Cursor or another independent model reviews a specific risk domain rather than repeating a general review.

Typical triggers:

- SQL/data migration/control totals;
- forecasting/statistics;
- financial/procurement optimization;
- concurrency/transactions;
- identity/pricing logic;
- production safety.

### G7 — Remediation

Findings go back to the designated writer. One writer fixes them. Re-run affected tests and full required validation.

Material fixes may require reviewer re-check.

### G8 — GitHub CI / PR proof

For PR-governed changes:

- push branch;
- CI/status checks must pass when configured;
- PR must contain scope, test evidence, review findings, open risks, and owner decisions needed;
- no force-push/history rewrite unless explicitly authorized for a recovery scenario.

### G9 — Business / owner acceptance

ChatGPT and the owner review the evidence for material behavior.

Human approval remains mandatory where the canonical system requires it.

### G10 — Merge / release

Merge only when required gates for the change are satisfied. Deployment is a separate controlled action where applicable.

### G11 — Post-change verification

After merge/deployment/live execution, verify actual state rather than assuming success:

- health;
- readiness gates;
- expected counts/control totals;
- error logs;
- no unintended writes;
- rollback/containment if acceptance criteria fail.

### G12 — Handoff / closeout

Before stopping a meaningful milestone:

1. update `docs/CODEX_HANDOFF.md` with **verified current state** and the exact authorization boundary;
2. update `procurement/docs/PHASE_STATUS.md` when a phase/program milestone changes;
3. include test result, gate state, relevant counts/control totals, open decisions, open blockers/risks, branch/commit/PR, and next authorized action;
4. commit/push the documentation with the work when appropriate;
5. do not mark a phase complete if a blocking gate or owner decision remains outstanding.

This closeout step is mandatory because the repository, not chat history, is the durable continuity mechanism.

## 5. Risk-based review levels

### Level 0 — documentation/cosmetic

Examples: copy, labels, nonfunctional layout.

Minimum:

- writer review;
- relevant lightweight tests/checks.

### Level 1 — ordinary application logic

Minimum:

- writer;
- deterministic tests;
- CI when configured.

### Level 2 — backend/data pipeline

Minimum:

- writer;
- full tests/adversarial tests;
- independent reviewer;
- integrity/idempotency controls.

### Level 3 — financial, forecasting, identity, pricing, inventory, procurement logic

Minimum:

- writer;
- deterministic fixture tests with known expected answers;
- adversarial tests;
- independent broad reviewer;
- targeted specialist review where useful;
- ChatGPT business-rule review;
- owner approval for defined material decisions;
- CI and live/control-total verification where applicable.

### Level 4 — actual PO release / irreversible production action

Minimum:

- all Level 3 controls;
- shadow-mode evidence where specified;
- explicit owner authorization;
- post-action reconciliation/audit.

## 6. Definition of Done

A task/phase is not “done” merely because code exists.

Required as applicable:

- acceptance criteria satisfied;
- all required existing/new/adversarial tests pass;
- independent review findings resolved or explicitly accepted;
- control totals/integrity checks reconcile;
- migrations are safe and applied as intended;
- relevant readiness gates reflect actual state through normal logic;
- no unauthorized Shopify/production writes occurred;
- audit evidence exists for permanent decisions;
- GitHub history is clean and recoverable;
- current-state handoff is updated;
- open owner decisions are explicitly listed;
- next phase is not started without authorization.

## 7. Change control

Any proposed change to canonical business behavior, architecture, identity policy, pricing lifecycle, readiness criteria, PO safety, or phase scope must be treated as a change request.

The proposing agent must state:

- current rule;
- proposed change;
- reason;
- alternatives considered;
- risk/impact;
- migration/backward-compatibility effect;
- tests required;
- owner decision required.

Do not silently “improve” or reinterpret a locked rule.

## 8. Risk / issue / decision handling

Use the following distinction:

- **Risk:** something that may happen and could affect objectives.
- **Issue:** a problem that has already happened or currently blocks work.
- **Decision:** an owner/authority choice that resolves a material fork.
- **Exception:** a known record/rule deviation requiring controlled handling.

Material open items must be visible in the current handoff or the system's audited exception/review mechanism. Do not bury them in chat transcripts.

## 9. Communication standard

Milestone reports should be concise and evidence-based:

- what is actually running;
- tests passing/failing;
- readiness gates;
- key counts/control totals;
- defects/findings and disposition;
- exact human decisions needed;
- commit/branch/PR;
- next authorized action.

Avoid long implementation narratives when evidence is available.

## 10. Token / credit efficiency

- Use ChatGPT for planning and business synthesis rather than repeated repo scanning by every agent.
- Use one writer.
- Run machine tests before paying for multiple AI reviews.
- Use Claude Code for broad independent review only on material work.
- Use Cursor for targeted specialist review, not duplicate general review.
- Reserve Replit Agent for Replit-specific platform work.
- Store durable context in Git so new sessions read concise authorities instead of replaying chat history.
- Review diffs/changed files, not the entire repository, once baseline context is established.

## 11. Program roadmap and phase numbering

Official implementation phase numbering follows the repository authority documents. Do not renumber canonical phases casually.

The broader business roadmap may be discussed as program workstreams, but that does not supersede official phase IDs.

Current official foundation sequence includes:

- Phase 0 — safe working repo/baseline
- Phase 1 — production infrastructure
- Phase 2 — schema + verified seed
- Phase 3 — live catalog reconciliation
- Phase 4 — historical ShopifyQL sales backfill/reconciliation
- Phase 5 — foundation UI as defined in the build authority (some UI may already be delivered earlier as needed)
- Phase 6 — foundation test/acceptance completion

After the first foundation gates pass, continue the canonical ordered workstreams for inventory history, vendor rules, PO ledger, price-book ingestion, CURRENT/FUTURE lifecycle, forecasting, strategic procurement economics, review queue, PO output, and shadow mode.

## 12. Current-state source

For the latest verified checkpoint, always read `docs/CODEX_HANDOFF.md` directly. Do not copy current counts from this governance document.

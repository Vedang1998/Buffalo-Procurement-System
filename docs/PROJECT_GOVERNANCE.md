# Buffalo Procurement OS — Project Governance & Quality System

**Purpose:** define one durable, low-error, low-waste operating process for planning, building, reviewing, approving, releasing, and handing off Buffalo Procurement OS work across ChatGPT, Codex Cloud, Claude Code, Cursor, GitHub, and approved infrastructure environments.

**Process revision:** 2026-10-29. Section 13 defines continuous execution inside an authorized task; it does not expand operational authority. Buffalo Procurement OS is separate from ProPo/Stocky.

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

### Development and infrastructure platforms

- **Codex Cloud:** ordinary implementation and supported development checks. Do not depend on the owner's Mac or Replit SSH for routine development.
- **GitHub-hosted validation:** use the specifically authorized workflow and supported host for tests requiring capabilities absent from the coding environment. A passing test on another host does not prove deployment compatibility.
- **Railway:** intended hosting destination, not the editing workspace. Configuration, deployment, data transfer, secrets and cutover retain their own approvals and acceptance gates. An intended destination is not evidence of a completed migration.
- **Replit:** retained legacy runtime/data/recovery context where applicable. Do not delete, shut down or reconnect to legacy infrastructure without explicit authority. Verify actual state rather than inferring it from migration plans.

Use approved secret management for the relevant environment; never place secrets in Git, chat, reports or reusable development snapshots. This process revision authorizes no secret transfer.

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

Unexpected changes require reconciliation before touching affected work. An invalid prerequisite blocks its dependent action; other independent, already-authorized work may continue under section 13. A frozen review target must not move.

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

A failing required machine check returns the affected work to G3 for diagnosis and in-scope repair. It blocks acceptance, not all authorized implementation; apply section 13 before requesting renewed owner approval.

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
- explicit owner approval and append-only evidence for any terminal historical
  identity exclusion affecting material units or revenue;
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

## 13. Continuous, goal-based execution (2026-10-29)

### 13.1 Goal and scope

Use one active, bounded delivery objective in the existing task plan and handoff. Record the objective, scope/exclusions, prerequisites, exactly one writer, risk level, acceptance tests/control totals, required reviewers, resource/usage bounds and authorization boundary before implementation. Describe the user-visible outcome or necessary proof, not an elapsed-hours or test-count target.

Reuse the existing plan, finding ledger and evidence format. Do not introduce an agent-management service, new state-file hierarchy, orchestrator or duplicated roadmap merely to make a run longer. Where the actual interface supplies a persistent Goal control, use it; otherwise retain the objective in the existing task context and repository. Do not claim a control was enabled or automatic resumption exists without verification.

This section governs how authorized work proceeds. It grants no new architecture, production, installation, runtime-input, resource, spending, credential, deployment or business authority. Existing narrow approvals and unresolved change-control decisions remain controlling.

### 13.2 Continuous implementation loop

The designated writer owns inspect -> implement -> focused test -> diagnose -> repair -> revalidate -> checkpoint -> continue. A checkpoint preserves progress; it is not a stopping condition. Do not ask the owner to approve routine edits, test runs, commits, in-scope repairs or the written restatement of an already-approved design.

Run the smallest trustworthy causal test first. Reuse source-bound evidence only where code, inputs and environment assumptions are unchanged and the gate permits it. Run the required full authoritative and startup suites on the final coherent candidate and after material fixes as applicable; do not rerun an unchanged known host refusal merely to increase activity. Never reduce floors, skip failures or replace full acceptance with focused results.

Before retrying a deterministic failure, record new evidence or a materially changed hypothesis/correction. Do not repeat an unchanged attempt. After two distinct evidence-backed repairs fail to reduce the same blocker, reassess the approach and choose another authorized path; escalate only if the remaining choice requires new authority or no defensible in-scope path exists. This is a diagnostic rule, not permission to ignore a failed gate.

Continue while executable authorized work remains. Progress updates, component completion, reviewer requests and documentation commits do not themselves end the task. Do not manufacture busywork or spin to consume time or evade platform budgets.

### 13.3 Parallel work and review handoffs

Codex remains the single implementation writer unless the owner explicitly reassigns the role. Parallel subagents may investigate, inspect logs, analyze tests and review read-only; they must not edit the active branch/worktree. Claude supplies broad independent review for material work. Cursor supplies targeted specialist review where useful, not another duplicate broad review.

Freeze and publish a coherent candidate with passing applicable machine checks, exact base/commit/tree, changed scope, evidence manifest, findings, known failures and requested gate decision. Code with unresolved blockers may receive early diagnosis, but no acceptance approval is implied.

Claude may review the frozen candidate while Codex works on independent authorized descendants. Reviews never follow a moving tip. Do not update the frozen target silently. Material later changes require a targeted delta review; unchanged historical closures retain their original commit and limitations. A code review, host measurement and operational authorization are different gates.

Actual reviewer identity/tool/model where exposed, reviewed commit/tree, commands/results, findings and limitations must be retained in the existing review package. Owner-pasted reports are attributed as owner-supplied, not independently fetched. Internal Codex reviews cannot satisfy a required external Claude gate. Do not say a reviewer was invoked merely because a prompt/package was prepared.

Read-only review forbids candidate edits, commits, pushes and unauthorized external effects; it permits bounded tests to create and modify reviewer-owned disposable fixtures, including fixture permissions, then clean them up. Do not change the original checkout or shared runtime to make a test pass.

If no callable reviewer channel exists, give the owner one exact review handoff and continue independent authorized work. Request another report only for a genuinely new candidate/delta or missing evidence. Do not send the same frozen target through duplicate internal audits while awaiting Claude.

### 13.4 Continue versus pause

A failed check blocks the affected acceptance/release transition. Missing review blocks the action requiring it. Missing private data blocks data-dependent validation. None automatically blocks other independent, authorized development.

Pause the whole task only when the owner pauses it, the actual budget/session/access prevents further work, source/authority uncertainty prevents all safe progress, or every remaining executable path depends on unavailable review/evidence or new authority. Preserve the checkpoint and state the precise reason.

For a genuine owner decision, consolidate the current rule, demonstrated problem, smallest proposed change, alternatives, risk, exact effects, tests and approval boundary into one request. Record and reuse the decision; do not ask again merely because a context was compacted. Changed facts or material scope require a new decision, not silent reuse.

Privileged proof, synthetic credential release, deployment, production data access, pricing/cutover and real purchasing remain separate transitions. An owner-approved task must still satisfy its machine and review prerequisites before executing a gated action. No self-authored PASS flag, increased autonomy setting or instruction file supplies missing authority. Stop at the canonical phase boundary unless the next phase has its own explicit authorization.

### 13.5 Evidence, memory and resumption

Use docs/CODEX_HANDOFF.md as the current checkpoint, the existing plan as the objective/dependency map, and the existing finding/evidence package as the review record. Do not assume ChatGPT, Codex, Claude and Cursor share conversation memory. Read the actual published state at startup; do not infer it from an old chat or static memory note.

At a meaningful checkpoint or before a foreseeable budget stop, record:

- branch, published commit/tree, tested material commit/tree and worktree state;
- objective, completed outcome/gate, next executable action and its prerequisites;
- actual test counts including not-run/error-event distinctions and evidence locations;
- reviewer target, attributed dispositions, affected later changes and blocked gates;
- existing approvals versus new decisions, resource cleanup and remaining budget/access limitation.

Scan secrets/private payloads before publishing only the authorized branch; verify the remote tip. A local commit is not remote recovery. Preserve failed evidence. Documentation-only descendants do not acquire unexecuted test results. Never add overlapping populations, label discovery as execution, or combine separate hosts/launches into a same-generation PASS.

A new session resumes the verified next action rather than recreating plans or completed packaging. Saved setup instructions, a published environment snapshot and a tested fresh-task restoration are different statuses. Do not promise resumption after a usage reset, purchase credits, raise limits or change plans without authority.

### 13.6 Product delivery and proportionality

Prioritize the shortest authorized path to the required buying workflow: verified current inputs -> approved supplier/price/pack economics -> supported demand/net need -> owner accept/edit/reject and recalculation -> separate vendor DRAFTs and reconciled outputs. Real release and supplier transmission retain their separate human approvals. This objective does not reduce canonical business scope.

Keep four readiness states separate: component/synthetic proof, hosted staging, current commercial/data readiness, and production purchasing. Test counts and infrastructure commits are not a completion percentage.

Every infrastructure change must identify the next application/proof gate it unblocks. If consecutive checkpoints add only infrastructure while the same live gate remains unrun, explicitly reassess the critical path and identify the smallest permitted experiment or a consolidated change-control decision. Do not silently redesign the security model, omit required controls, expand the harness into a general framework, or let the deadline substitute for acceptance.

Measure outcomes using the existing handoff: newly demonstrated gates, remaining causal blockers, review turnaround and owner interruptions. No telemetry platform or new automation is required by this revision. Cosmetic/docs-only changes receive lightweight checks; material backend, data, security and procurement changes retain their existing risk-based reviews.

# Buffalo Procurement OS — Repository Instructions

This repository is Buffalo Procurement OS. These instructions apply to every future coding-agent session and the entire repository.

## Read before any work

1. `AGENTS.md`.
2. The canonical specification chain: `procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md` (which incorporates the detailed Master Plan), plus `procurement/docs/CURRENT_AUTHORITY.md` and the Master Plan it designates at `procurement/docs/MASTER_PLAN_v2_0.md`.
3. `procurement/config/rules.toml`.
4. `docs/PROJECT_GOVERNANCE.md`.
5. `docs/CODEX_HANDOFF.md`.
6. The schema, migrations, and tests relevant to the requested phase.

If authorities conflict or material facts remain uncertain, stop and surface the conflict; do not improvise.

## Mandatory project-management workflow

- Follow `docs/PROJECT_GOVERNANCE.md` for task initiation, baseline, branch isolation, implementation, machine validation, independent review, remediation, CI/PR proof, owner acceptance, release, post-change verification, and closeout.
- Use **one writer at a time**. Do not let Codex, Claude Code, Cursor, or Replit Agent concurrently modify the same active task/worktree unless the owner explicitly approves a coordinated exception.
- Material backend/data/procurement work requires an independent reviewer after machine tests. The implementation agent cannot be the sole reviewer.
- Machine evidence outranks AI confidence: tests, control totals, constraints, readiness gates, and live verification determine acceptance.
- Review intensity is risk-based. Identity, pricing, inventory, financial, forecasting, procurement, and PO-release logic receive the strongest review.
- At every meaningful milestone/phase boundary, update `docs/CODEX_HANDOFF.md` with verified current state, blockers, gate states, test result, relevant counts/control totals, branch/commit/PR, owner decisions needed, and the exact next authorization boundary.
- Update `procurement/docs/PHASE_STATUS.md` when the phase/program milestone changes.
- Do not mark a phase complete or begin the next phase merely because code exists. Satisfy the applicable Definition of Done and obtain required owner authorization.

## Permanent operating guardrails

- Shopify Variant ID is canonical identity. Supplier SKU is mapping evidence, never permanent identity.
- Fail closed on material uncertainty. Never guess supplier mappings, auto-create recreation aliases, or auto-retire historical identities.
- Never write to Shopify unless the specifically authorized phase permits it. Never manually force readiness gates.
- Never enable PO generation until every required gate passes. Keep each vendor on its own PO.
- Preserve the no-runtime-LLM architecture.
- Preserve the no-reusable-price-archive decision. A finalized run may retain only its exact run economics snapshot for audit and reproducibility.
- Preserve all historical and audit data.
- Inspect `git status` before work and report unexpected modifications. Run tests before and after material changes.
- Do not redesign accepted architecture unless a material technical blocker is demonstrated.
- Stop at every phase boundary and wait for explicit authorization.
- Never expose, log, or persist secrets.

Use the canonical documents for detailed architecture and business rules; do not restate or reinterpret them here.

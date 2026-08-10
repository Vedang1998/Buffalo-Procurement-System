# Buffalo Procurement OS — Repository Instructions

This repository is Buffalo Procurement OS. These instructions apply to every future Codex session and the entire repository.

## Read before any work

1. `AGENTS.md`.
2. The canonical specification chain: `procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md` (which incorporates the detailed Master Plan), plus `procurement/docs/CURRENT_AUTHORITY.md` and the Master Plan it designates at `procurement/docs/MASTER_PLAN_v2_0.md`.
3. `procurement/config/rules.toml`.
4. `docs/CODEX_HANDOFF.md`.
5. The schema, migrations, and tests relevant to the requested phase.

If authorities conflict or material facts remain uncertain, stop and surface the conflict; do not improvise.

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

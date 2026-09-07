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

## Cursor Cloud specific instructions

The real product is the Python/FastAPI **procurement engine** in `procurement/` (all business logic). The Node/pnpm workspace (`artifacts/*`, `lib/*`, `scripts`) is optional scaffolding (Express gateway + UI mockups) and must never duplicate procurement business logic.

### Automatic startup (already handled by the update script)
On session start the update script runs `uv sync` (Python deps → `/workspace/.venv`, CPython 3.14 managed by uv) and `pnpm install --frozen-lockfile` (Node deps). You do not need to reinstall these. Run Python through the uv venv, e.g. `uv run --project /workspace python ...` (or `/workspace/.venv/bin/python`). `uv` lives at `$HOME/.local/bin/uv` and is on `PATH` in login shells.

### PostgreSQL is required but NOT auto-started
PostgreSQL 16 is installed locally; the cluster does not auto-start on a fresh pod. Bring it up and point the engine at it (role `procurement` / db `procurement` already exist; schema + seed persist in the snapshot DB):
```bash
sudo pg_ctlcluster 16 main start
export DATABASE_URL="postgresql://procurement:procurement@127.0.0.1:5432/procurement"
```
If the DB is ever empty, re-apply (both steps are idempotent) from `procurement/`:
```bash
uv run --project /workspace python tools/apply_schema.py --database-url "$DATABASE_URL"
uv run --project /workspace python tools/import_seed_csv.py --seed-dir seed --database-url "$DATABASE_URL"
```

### Run the engine
From `procurement/`:
```bash
PYTHONPATH=src uv run --project /workspace python -m uvicorn procurement_os.api:app --host 0.0.0.0 --port 8000
```
Key pages: `/admin/status` (status), `/health/full` (JSON health), `/reconciliation`, `/historical-sales/review`. Reconciliation and historical-sales **decision** endpoints fail closed (HTTP 503) unless `RECONCILIATION_REVIEW_TOKEN` is set.

### Tests / typecheck (standard commands live in `procurement/README.md` and `package.json`)
- Python (142 tests; needs `DATABASE_URL` for the Postgres integration tests): from `procurement/`, `PYTHONPATH=src uv run --project /workspace python -m unittest discover -s tests -v`.
- Node has no JS test runner; the static gate is `pnpm run typecheck` (root). No ESLint/Python linter is configured.

### Non-obvious gotchas
- Readiness gates are read from Postgres. On a fresh seed with no live Shopify sync, `CATALOG_SYNC` and `SALES_BACKFILL` show **FAIL** and PO generation stays **DISABLED** — this is the correct fail-closed state, not a setup error.
- Shopify credentials are optional (only the `catalog_sync`/`sales_backfill` jobs and full health need them). When unset the app still runs and reports Shopify as "not configured".
- `POST /economics/target-cost` expects `target_margin_pct` as a fraction in `[0,1)` (e.g. `0.30`), not a whole-number percent.

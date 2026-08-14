# Buffalo House Procurement OS

Internal procurement operating system for Buffalo House Liquor & Wines — deterministic, fail-closed purchasing intelligence replacing Shopify Stocky. Python/FastAPI is the procurement-engine authority (lives in `procurement/`); the Node monorepo scaffold coexists but must never duplicate procurement business logic.

## Read before any Replit Agent work

1. `AGENTS.md`
2. `procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md`
3. `procurement/config/rules.toml`
4. `docs/PROJECT_GOVERNANCE.md`
5. `docs/CODEX_HANDOFF.md`
6. relevant schema/migrations/tests

Do not simplify or reinterpret procurement business rules without explicit owner approval. Read actual readiness gates from PostgreSQL rather than assuming the handoff is still current.

## Replit Agent role

Replit Agent is the **Replit-specific specialist/reserve**, not the default coding agent. Prefer it for Replit deployment, Autoscale, Workflows/Scheduled Deployments, App Storage, networking/ports, environment/platform behavior, and other Replit-specific issues.

Follow `docs/PROJECT_GOVERNANCE.md`:

- one writer at a time;
- do not modify the same task concurrently with Codex/Claude/Cursor;
- machine tests/control totals before confidence claims;
- independent review for material backend/data/procurement logic;
- owner approval at phase boundaries and for defined consequential decisions;
- update `docs/CODEX_HANDOFF.md` at meaningful milestone closeout if Replit Agent was the writer;
- do not mark a phase complete while a blocking gate or owner decision remains open.

PO generation stays disabled while any required blocking gate fails. Never auto-merge Variant identities. Never manually force readiness gates.

## Procurement app — run & operate

- Workflow `Procurement OS` runs uvicorn on port 8000 (`procurement/src/procurement_os/api.py`); `/admin/status` is the status page, `/health/full` machine-readable, and `/historical-sales/review` is the Phase 4 owner queue
- Tests, from the repository root: `./scripts/procurement-tests`
- The direct unittest command is not the current validation route; use the
  authoritative repository-root command above.
- Schema: `python3 tools/apply_schema.py --database-url "$DATABASE_URL"` (idempotent, transactional; order in MIGRATION_ORDER)
- Seed: `tools/import_seed_csv.py` then `tools/verify_seed_import.py` (writes audit row to `seed_import_records`)
- Object storage must go through `procurement_os/storage.py` adapter — never platform calls in domain logic

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Pointers

- Current checkpoint: `docs/CODEX_HANDOFF.md`
- Project-management/review process: `docs/PROJECT_GOVERNANCE.md`
- Executive phase status: `procurement/docs/PHASE_STATUS.md`
- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details

# Buffalo House Procurement OS

Internal procurement operating system for Buffalo House Liquor & Wines — deterministic, fail-closed purchasing intelligence replacing Shopify Stocky. Python/FastAPI is the procurement-engine authority (lives in `procurement/`); the Node monorepo scaffold coexists but must never duplicate procurement business logic.

## Authority order (mandatory)

1. `procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md`
2. `procurement/config/rules.toml` (machine-enforced)
3. `procurement/docs/authority/03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md` (sequencing)
4. `procurement/docs/authority/05_CURRENT_BUILD_STATUS_v1_3.md`

Do not simplify or reinterpret procurement business rules without explicit owner approval. Read actual readiness gates from PostgreSQL; at the current handoff `CATALOG_SYNC=PASS` and `SALES_BACKFILL=FAIL` pending historical-identity review. PO generation stays disabled while any blocking gate fails. Never auto-merge Variant identities. Owner approval is required between phases.

## Procurement app — run & operate

- Workflow `Procurement OS` runs uvicorn on port 8000 (`procurement/src/procurement_os/api.py`); `/admin/status` is the status page, `/health/full` machine-readable, and `/historical-sales/review` is the Phase 4 owner queue
- Tests: `cd procurement && PYTHONPATH=src python3 -m unittest discover -s tests`
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

## Where things live

_Populate as you build — short repo map plus pointers to the source-of-truth file for DB schema, API contracts, theme files, etc._

## Architecture decisions

_Populate as you build — non-obvious choices a reader couldn't infer from the code (3-5 bullets)._

## Product

_Describe the high-level user-facing capabilities of this app once they exist._

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details

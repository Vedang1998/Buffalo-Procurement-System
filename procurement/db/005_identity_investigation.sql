-- Migration 005 — Phase 3 identity investigation (diagnostic layer, no decisions).
CREATE TABLE IF NOT EXISTS identity_investigations (
    investigation_id BIGSERIAL PRIMARY KEY,
    catalog_sync_id  UUID NOT NULL REFERENCES catalog_sync_runs(catalog_sync_id),
    subject          TEXT NOT NULL CHECK (subject IN ('MISSING_SEED','NEW_LIVE')),
    variant_id       TEXT NOT NULL,
    shopify_lookup_json JSONB,          -- raw node lookup result (null lookup recorded explicitly)
    shopify_status   TEXT,              -- ACTIVE / DRAFT / ARCHIVED / OTHER / NOT_RESOLVABLE
    classification   TEXT NOT NULL,
    evidence_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    heightened_review BOOLEAN NOT NULL DEFAULT FALSE,
    looked_up_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalog_sync_id, subject, variant_id)
);

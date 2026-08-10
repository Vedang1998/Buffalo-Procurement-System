-- Migration 002 — narrowly scoped: auditable seed import record.
-- Owner requirement (Phase 0-2 approval, item 6): persist manifest/version,
-- import timestamp, expected/actual counts, validation result and checksum info.
CREATE TABLE IF NOT EXISTS seed_import_records (
    import_id BIGSERIAL PRIMARY KEY,
    manifest_source TEXT NOT NULL,
    manifest_source_sha256 TEXT NOT NULL,
    manifest_json JSONB NOT NULL,
    seed_package_version TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expected_counts_json JSONB NOT NULL,
    actual_counts_json JSONB NOT NULL,
    file_hash_validation_json JSONB NOT NULL,
    fk_orphan_validation_json JSONB NOT NULL,
    validation_result TEXT NOT NULL CHECK (validation_result IN ('PASS','FAIL')),
    notes TEXT
);

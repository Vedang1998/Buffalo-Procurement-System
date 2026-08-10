-- Buffalo Procurement OS v1.3 — catalog + historical-sales foundation
-- Safe additive migration from v1.2. CURRENT/FUTURE only; no reusable price archive.

ALTER TABLE variants ADD COLUMN IF NOT EXISTS inventory_item_gid TEXT;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS inventory_tracked BOOLEAN;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS catalog_state TEXT NOT NULL DEFAULT 'SEEDED';
ALTER TABLE variants ADD COLUMN IF NOT EXISTS catalog_last_seen_at TIMESTAMPTZ;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS catalog_missing_since TIMESTAMPTZ;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS catalog_resolution_note TEXT;

ALTER TABLE variant_aliases ADD COLUMN IF NOT EXISTS approved BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE variant_aliases ADD COLUMN IF NOT EXISTS valid_from DATE;
ALTER TABLE variant_aliases ADD COLUMN IF NOT EXISTS valid_to DATE;
ALTER TABLE variant_aliases ADD COLUMN IF NOT EXISTS evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE variant_aliases ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE variant_aliases ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_variant_alias_historical_sku
    ON variant_aliases(historical_sku);

CREATE TABLE IF NOT EXISTS catalog_sync_runs (
    catalog_sync_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK(status IN ('RUNNING','COMPLETED','FAILED')),
    shopify_api_version TEXT,
    shopify_reported_variant_count INTEGER,
    live_rows_received INTEGER NOT NULL DEFAULT 0,
    exact_current_ids INTEGER NOT NULL DEFAULT 0,
    new_live_variants INTEGER NOT NULL DEFAULT 0,
    missing_seed_variants INTEGER NOT NULL DEFAULT 0,
    potential_recreations INTEGER NOT NULL DEFAULT 0,
    unresolved_count INTEGER NOT NULL DEFAULT 0,
    source_hash TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS catalog_reconciliation_items (
    reconciliation_item_id BIGSERIAL PRIMARY KEY,
    catalog_sync_id UUID NOT NULL REFERENCES catalog_sync_runs(catalog_sync_id) ON DELETE CASCADE,
    variant_id TEXT,
    seed_variant_id TEXT,
    live_variant_id TEXT,
    classification TEXT NOT NULL CHECK(classification IN (
        'EXACT','NEW','MISSING','INACTIVE','POTENTIAL_RECREATION','CHANGED_ATTRIBUTES','RESOLVED'
    )),
    blocking BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_reconciliation_item
    ON catalog_reconciliation_items(catalog_sync_id,classification,COALESCE(seed_variant_id,''),COALESCE(live_variant_id,''));

CREATE INDEX IF NOT EXISTS idx_catalog_recon_blocking
    ON catalog_reconciliation_items(catalog_sync_id,blocking,classification);

CREATE TABLE IF NOT EXISTS readiness_gates (
    gate_id BIGSERIAL PRIMARY KEY,
    gate_name TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'GLOBAL'
        CHECK(scope_type IN ('GLOBAL','VENDOR','VARIANT','RUN')),
    scope_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('PASS','WARN','FAIL')),
    severity TEXT NOT NULL DEFAULT 'HIGH'
        CHECK(severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')),
    blocks_po BOOLEAN NOT NULL DEFAULT TRUE,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    message TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(gate_name,scope_type,scope_id)
);

CREATE INDEX IF NOT EXISTS idx_readiness_blockers
    ON readiness_gates(blocks_po,status,scope_type,scope_id);

CREATE TABLE IF NOT EXISTS sales_backfill_runs (
    sales_backfill_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK(status IN ('RUNNING','COMPLETED','FAILED')),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    source TEXT NOT NULL DEFAULT 'SHOPIFYQL_SALES',
    query_version TEXT,
    raw_rows INTEGER NOT NULL DEFAULT 0,
    resolved_rows INTEGER NOT NULL DEFAULT 0,
    unresolved_rows INTEGER NOT NULL DEFAULT 0,
    ambiguous_rows INTEGER NOT NULL DEFAULT 0,
    resolved_units NUMERIC(16,4) NOT NULL DEFAULT 0,
    unresolved_units NUMERIC(16,4) NOT NULL DEFAULT 0,
    source_net_sales NUMERIC(16,2),
    canonical_net_sales NUMERIC(16,2),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS shopify_sales_daily_raw (
    raw_sales_id BIGSERIAL PRIMARY KEY,
    sales_backfill_id UUID REFERENCES sales_backfill_runs(sales_backfill_id) ON DELETE SET NULL,
    sale_date DATE NOT NULL,
    source_variant_id TEXT,
    source_sku TEXT,
    source_product_title TEXT,
    source_variant_title TEXT,
    net_items_sold NUMERIC(14,4) NOT NULL,
    net_sales NUMERIC(16,2),
    canonical_variant_id TEXT REFERENCES variants(variant_id),
    resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED'
        CHECK(resolution_status IN ('RESOLVED','UNRESOLVED','AMBIGUOUS','EXCLUDED')),
    resolution_method TEXT,
    resolution_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_row_hash TEXT NOT NULL UNIQUE,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_raw_sales_resolution
    ON shopify_sales_daily_raw(resolution_status,sale_date);
CREATE INDEX IF NOT EXISTS idx_raw_sales_source_variant
    ON shopify_sales_daily_raw(source_variant_id);
CREATE INDEX IF NOT EXISTS idx_raw_sales_source_sku
    ON shopify_sales_daily_raw(source_sku);
CREATE INDEX IF NOT EXISTS idx_raw_sales_canonical
    ON shopify_sales_daily_raw(canonical_variant_id,sale_date);

CREATE TABLE IF NOT EXISTS mapping_rejections (
    rejection_id BIGSERIAL PRIMARY KEY,
    mapping_type TEXT NOT NULL CHECK(mapping_type IN ('HISTORICAL_VARIANT','SUPPLIER_OFFER')),
    source_key TEXT NOT NULL,
    rejected_variant_id TEXT NOT NULL REFERENCES variants(variant_id),
    vendor_id UUID REFERENCES vendors(vendor_id),
    source_text TEXT,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    rejected_by TEXT,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_mapping_rejections_lookup
    ON mapping_rejections(mapping_type,source_key,active);

CREATE TABLE IF NOT EXISTS daily_inventory_snapshots (
    snapshot_date DATE NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE CASCADE,
    location_gid TEXT NOT NULL DEFAULT '',
    available_quantity NUMERIC(14,4),
    incoming_quantity NUMERIC(14,4),
    on_hand_quantity NUMERIC(14,4),
    committed_quantity NUMERIC(14,4),
    reserved_quantity NUMERIC(14,4),
    damaged_quantity NUMERIC(14,4),
    source TEXT NOT NULL DEFAULT 'SHOPIFY_GRAPHQL',
    PRIMARY KEY(snapshot_date,variant_id,location_gid)
);

CREATE TABLE IF NOT EXISTS run_price_snapshots (
    run_price_snapshot_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    offer_id BIGINT NOT NULL REFERENCES supplier_offers(offer_id),
    price_state TEXT NOT NULL CHECK(price_state IN ('current','future')),
    effective_month DATE NOT NULL,
    level_type TEXT NOT NULL,
    break_qty NUMERIC(12,4),
    break_unit TEXT,
    case_price NUMERIC(14,4),
    unit_price NUMERIC(14,4) NOT NULL,
    source_file TEXT,
    source_page INTEGER,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_run_price_snapshot
    ON run_price_snapshots(run_id,offer_id,price_state,level_type,COALESCE(break_qty,-1),COALESCE(break_unit,''),unit_price);

ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS shopify_import_status TEXT NOT NULL DEFAULT 'NOT_IMPORTED';
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS shopify_po_reference TEXT;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS expected_receipt_at TIMESTAMPTZ;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS receipt_status TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMPTZ;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS emergency_packet_path TEXT;

CREATE TABLE IF NOT EXISTS change_log (
    change_id BIGSERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    row_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('INSERT','UPDATE','DELETE','APPROVE','REJECT','SUPERSEDE')),
    before_json JSONB,
    after_json JSONB,
    actor TEXT,
    run_id UUID REFERENCES runs(run_id),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_change_log_lookup ON change_log(table_name,row_key,occurred_at DESC);

-- Seed required readiness gates; actual status is recomputed by jobs.
INSERT INTO readiness_gates(gate_name,status,severity,blocks_po,message)
VALUES
 ('CATALOG_SYNC','FAIL','CRITICAL',TRUE,'Live Shopify catalog has not yet been reconciled against canonical identity.'),
 ('SALES_BACKFILL','FAIL','CRITICAL',TRUE,'Historical sales backfill has not yet passed canonical identity resolution.'),
 ('INVENTORY_HISTORY','WARN','HIGH',FALSE,'Own daily inventory snapshots are not yet confirmed running.'),
 ('VENDOR_RULES','FAIL','HIGH',TRUE,'Vendor operating rules are not yet confirmed complete.'),
 ('PRICE_COVERAGE','WARN','HIGH',FALSE,'Full-catalog verified supplier pricing is not yet complete.'),
 ('MAPPING_INTEGRITY','WARN','HIGH',FALSE,'Supplier mapping integrity is not yet fully validated.'),
 ('OPEN_PO_RECONCILIATION','WARN','HIGH',FALSE,'Procurement PO reconciliation is not yet fully operational.')
ON CONFLICT(gate_name,scope_type,scope_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS historical_sales_exclusions (
    source_key TEXT PRIMARY KEY,
    source_variant_id TEXT,
    source_sku TEXT,
    source_product_title TEXT,
    source_variant_title TEXT,
    reason TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    active BOOLEAN NOT NULL DEFAULT TRUE
);

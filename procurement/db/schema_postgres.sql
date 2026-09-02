-- Buffalo House Procurement OS v1.3
-- PostgreSQL production schema. CURRENT/FUTURE only; no operational price archive.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS meta (
 key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS variants (
 variant_id TEXT PRIMARY KEY, shopify_gid TEXT UNIQUE, product_id TEXT, product_gid TEXT,
 product_title TEXT NOT NULL, variant_title TEXT NOT NULL, handle TEXT, status TEXT, sku TEXT, barcode TEXT,
 retail_price NUMERIC(12,2), shopify_current_cost NUMERIC(12,4), shopify_vendor TEXT, product_type TEXT,
 active BOOLEAN NOT NULL DEFAULT TRUE, variant_created_at TIMESTAMPTZ, source_snapshot TEXT, last_synced_at TIMESTAMPTZ,
 catalog_state TEXT NOT NULL DEFAULT 'SEEDED', identity_scope TEXT NOT NULL DEFAULT 'CURRENT',
 restoration_manifest_sha256 TEXT, restoration_manifest_row_number INTEGER,
 restoration_evidence_version TEXT, restoration_owner_authorization TEXT,
 restoration_authority_git_sha TEXT, restoration_execution_git_sha TEXT
);
-- Keep schema re-application safe on databases created before identity_scope
-- existed.  Operational views below must never be recreated without this
-- explicit scope column and filter.
ALTER TABLE variants
    ADD COLUMN IF NOT EXISTS identity_scope TEXT NOT NULL DEFAULT 'CURRENT';

CREATE OR REPLACE FUNCTION is_operational_current_variant(checked_variant_id TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM variants
        WHERE variant_id=checked_variant_id
          AND identity_scope='CURRENT'
          AND active=TRUE
    )
$$;
CREATE INDEX IF NOT EXISTS idx_variants_product ON variants(product_id,product_title,variant_title);
CREATE INDEX IF NOT EXISTS idx_variants_sku ON variants(sku);
CREATE INDEX IF NOT EXISTS idx_variants_barcode ON variants(barcode);

CREATE TABLE IF NOT EXISTS variant_aliases (
 alias_id BIGSERIAL PRIMARY KEY, variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE CASCADE,
 old_variant_id TEXT, historical_product_title TEXT, historical_variant_title TEXT, historical_sku TEXT,
 normalized_key TEXT, match_method TEXT NOT NULL, confidence NUMERIC(5,4), source TEXT NOT NULL, notes TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_variant_alias_old_id ON variant_aliases(old_variant_id);
CREATE INDEX IF NOT EXISTS idx_variant_alias_normalized ON variant_aliases(normalized_key);

CREATE TABLE IF NOT EXISTS sales_daily (
 sale_date DATE NOT NULL, variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE CASCADE,
 units_sold NUMERIC(14,4) NOT NULL DEFAULT 0, net_sales NUMERIC(14,2), distinct_orders INTEGER,
 source TEXT NOT NULL, source_product_title TEXT, source_variant_title TEXT, run_id UUID,
 PRIMARY KEY(sale_date,variant_id,source)
);
CREATE INDEX IF NOT EXISTS idx_sales_daily_variant_date ON sales_daily(variant_id,sale_date);

CREATE TABLE IF NOT EXISTS vendors (
 vendor_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), vendor_name TEXT NOT NULL UNIQUE, active BOOLEAN NOT NULL DEFAULT TRUE,
 order_day TEXT NOT NULL DEFAULT 'Monday', order_cycle_days INTEGER, lead_time_days INTEGER NOT NULL DEFAULT 1,
 expected_delivery_note TEXT, delivery_case_threshold NUMERIC(12,2), delivery_dollar_threshold NUMERIC(12,2),
 threshold_logic TEXT, fee_below_threshold NUMERIC(12,2), fee_qualified NUMERIC(12,2), loose_unit_fee NUMERIC(12,2),
 notes TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS supplier_offers (
 offer_id BIGSERIAL PRIMARY KEY, variant_id TEXT NOT NULL REFERENCES variants(variant_id),
 vendor_id UUID NOT NULL REFERENCES vendors(vendor_id), supplier_sku TEXT, supplier_description TEXT,
 package_type TEXT NOT NULL DEFAULT 'STANDARD', size_text TEXT, raw_pack TEXT,
 shopify_units_per_case NUMERIC(12,4), qualifying_units_per_case NUMERIC(12,4),
 assortment_scope TEXT NOT NULL DEFAULT 'PRODUCT' CHECK(assortment_scope IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')),
 assortment_group TEXT, assortable BOOLEAN, allocation_limit NUMERIC(12,4), active BOOLEAN NOT NULL DEFAULT TRUE,
 valid_from DATE, valid_to DATE, replaces_offer_id BIGINT REFERENCES supplier_offers(offer_id),
 source_file TEXT, source_page INTEGER, confidence TEXT, notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_vendor_supplier_sku ON supplier_offers(vendor_id,supplier_sku)
 WHERE active=TRUE AND supplier_sku IS NOT NULL AND supplier_sku<>'';
CREATE INDEX IF NOT EXISTS idx_supplier_offers_variant ON supplier_offers(variant_id);

CREATE TABLE IF NOT EXISTS supplier_aliases (
 alias_id BIGSERIAL PRIMARY KEY, vendor_id UUID NOT NULL REFERENCES vendors(vendor_id) ON DELETE CASCADE,
 variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE CASCADE,
 supplier_text TEXT NOT NULL, normalized_supplier_text TEXT NOT NULL, supplier_sku TEXT,
 size_text TEXT, pack_text TEXT, approved BOOLEAN NOT NULL DEFAULT FALSE, match_method TEXT NOT NULL,
 confidence NUMERIC(5,4), approved_by TEXT, approved_at TIMESTAMPTZ, notes TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_alias ON supplier_aliases(vendor_id,normalized_supplier_text,COALESCE(supplier_sku,''));

CREATE TABLE IF NOT EXISTS prices (
 price_id BIGSERIAL PRIMARY KEY, offer_id BIGINT NOT NULL REFERENCES supplier_offers(offer_id) ON DELETE CASCADE,
 price_state TEXT NOT NULL CHECK(price_state IN ('current','future')), effective_month DATE NOT NULL,
 level_type TEXT NOT NULL CHECK(level_type IN ('BASE','BREAK')), break_qty NUMERIC(12,4),
 break_unit TEXT CHECK(break_unit IN ('CS','BT','EA') OR break_unit IS NULL), case_price NUMERIC(14,4),
 unit_price NUMERIC(14,4) NOT NULL, source_file TEXT NOT NULL, source_page INTEGER,
 extraction_confidence TEXT, verified BOOLEAN NOT NULL DEFAULT FALSE, notes TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_ladder_row ON prices(offer_id,price_state,effective_month,level_type,COALESCE(break_qty,-1),COALESCE(break_unit,''),unit_price);
CREATE INDEX IF NOT EXISTS idx_prices_state_month ON prices(price_state,effective_month);

CREATE TABLE IF NOT EXISTS manual_overrides (
 override_id BIGSERIAL PRIMARY KEY, active BOOLEAN NOT NULL DEFAULT TRUE, variant_id TEXT REFERENCES variants(variant_id),
 vendor_id UUID REFERENCES vendors(vendor_id), supplier_sku TEXT, override_type TEXT NOT NULL,
 effective_from DATE, effective_through DATE, reversion_date DATE, expected_reversion_cost NUMERIC(14,4),
 allocation_limit NUMERIC(12,4), demand_override_pct NUMERIC(9,4), note TEXT NOT NULL,
 entered_by TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS variant_policies (
 policy_id BIGSERIAL PRIMARY KEY, variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE CASCADE,
 policy_type TEXT NOT NULL, value_json JSONB NOT NULL DEFAULT '{}'::jsonb, active BOOLEAN NOT NULL DEFAULT TRUE,
 effective_from DATE, effective_through DATE, approved_by TEXT, note TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
 event_id BIGSERIAL PRIMARY KEY, event_type TEXT NOT NULL, event_name TEXT NOT NULL, starts_at TIMESTAMPTZ NOT NULL,
 ends_at TIMESTAMPTZ, scope_type TEXT NOT NULL DEFAULT 'STORE', scope_value TEXT, source TEXT NOT NULL,
 expected_direction TEXT, note TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS combos (
 combo_id BIGSERIAL PRIMARY KEY, vendor_id UUID NOT NULL REFERENCES vendors(vendor_id), supplier_combo_code TEXT,
 combo_name TEXT, price_state TEXT NOT NULL CHECK(price_state IN ('current','future')), effective_month DATE NOT NULL,
 total_cost NUMERIC(14,4), customer_limit NUMERIC(12,4), active BOOLEAN NOT NULL DEFAULT TRUE,
 source_file TEXT NOT NULL, source_page INTEGER, verified BOOLEAN NOT NULL DEFAULT FALSE, notes TEXT
);
CREATE TABLE IF NOT EXISTS combo_components (
 component_id BIGSERIAL PRIMARY KEY, combo_id BIGINT NOT NULL REFERENCES combos(combo_id) ON DELETE CASCADE,
 offer_id BIGINT REFERENCES supplier_offers(offer_id), supplier_sku TEXT, quantity NUMERIC(12,4) NOT NULL,
 component_unit_cost NUMERIC(14,4), notes TEXT
);
CREATE TABLE IF NOT EXISTS combo_usage (
 combo_id BIGINT NOT NULL REFERENCES combos(combo_id) ON DELETE CASCADE, effective_month DATE NOT NULL,
 use_count INTEGER NOT NULL DEFAULT 0, last_po_id UUID, PRIMARY KEY(combo_id,effective_month)
);

CREATE TABLE IF NOT EXISTS runs (
 run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), run_type TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 completed_at TIMESTAMPTZ, status TEXT NOT NULL, source_data_through TIMESTAMPTZ,
 current_price_month DATE, future_price_month DATE, model_version TEXT, exception_count INTEGER NOT NULL DEFAULT 0, notes TEXT
);
CREATE TABLE IF NOT EXISTS inventory_snapshots (
 run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE, variant_id TEXT NOT NULL REFERENCES variants(variant_id),
 available_quantity NUMERIC(14,4), incoming_quantity NUMERIC(14,4), captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,variant_id)
);

CREATE TABLE IF NOT EXISTS forecast_results (
 run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE, variant_id TEXT NOT NULL REFERENCES variants(variant_id),
 demand_regime TEXT, selected_model TEXT, abc_class TEXT CHECK(abc_class IN ('A','B','C') OR abc_class IS NULL),
 xyz_class TEXT CHECK(xyz_class IN ('X','Y','Z') OR xyz_class IS NULL), forecast_units NUMERIC(14,4),
 safety_stock_units NUMERIC(14,4), protection_days NUMERIC(10,2), baseline_replenishment_units NUMERIC(14,4),
 calendar_velocity NUMERIC(14,6), in_stock_velocity NUMERIC(14,6), wape NUMERIC(10,6), mase NUMERIC(10,6), bias NUMERIC(10,6),
 confidence TEXT, diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb, PRIMARY KEY(run_id,variant_id)
);

CREATE TABLE IF NOT EXISTS procurement_recommendations (
 recommendation_id BIGSERIAL PRIMARY KEY, run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
 variant_id TEXT NOT NULL REFERENCES variants(variant_id), vendor_id UUID NOT NULL REFERENCES vendors(vendor_id),
 offer_id BIGINT REFERENCES supplier_offers(offer_id), baseline_units NUMERIC(14,4) NOT NULL DEFAULT 0,
 recommended_cases NUMERIC(14,4) NOT NULL DEFAULT 0, recommended_loose_units NUMERIC(14,4) NOT NULL DEFAULT 0,
 current_margin_pct NUMERIC(9,6), target_margin_pct NUMERIC(9,6), target_cost NUMERIC(14,4), recommended_unit_cost NUMERIC(14,4),
 incremental_cash NUMERIC(14,2), incremental_gp_90d NUMERIC(14,2), gp_per_100_incremental_cash NUMERIC(14,4),
 projected_days_supply NUMERIC(12,2), reason_code TEXT, review_required BOOLEAN NOT NULL DEFAULT FALSE,
 explanation TEXT, metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS review_decisions (
 decision_id BIGSERIAL PRIMARY KEY, run_id UUID REFERENCES runs(run_id) ON DELETE CASCADE,
 recommendation_id BIGINT REFERENCES procurement_recommendations(recommendation_id), decision_type TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('RUN_ONLY','TEMPORARY','PERMANENT')), action TEXT NOT NULL, comment TEXT,
 effective_from DATE, effective_through DATE, decided_by TEXT NOT NULL, decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 writeback_type TEXT, writeback_id TEXT
);

CREATE TABLE IF NOT EXISTS exceptions (
 exception_id BIGSERIAL PRIMARY KEY, run_id UUID REFERENCES runs(run_id), exception_type TEXT NOT NULL,
 severity TEXT NOT NULL CHECK(severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')), variant_id TEXT REFERENCES variants(variant_id),
 offer_id BIGINT REFERENCES supplier_offers(offer_id), vendor_id UUID REFERENCES vendors(vendor_id), supplier_sku TEXT,
 message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','REVIEWED','RESOLVED','IGNORED')),
 resolution TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_exceptions_status ON exceptions(status,severity);

CREATE TABLE IF NOT EXISTS purchase_orders (
 po_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), run_id UUID NOT NULL REFERENCES runs(run_id),
 vendor_id UUID NOT NULL REFERENCES vendors(vendor_id), po_status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(po_status IN ('DRAFT','REVIEW','FINAL','CANCELLED')),
 merchandise_total NUMERIC(14,2), delivery_fee NUMERIC(14,2), po_total NUMERIC(14,2), below_vendor_minimum BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finalized_at TIMESTAMPTZ, notes TEXT, UNIQUE(run_id,vendor_id)
);
CREATE TABLE IF NOT EXISTS purchase_order_lines (
 po_line_id BIGSERIAL PRIMARY KEY, po_id UUID NOT NULL REFERENCES purchase_orders(po_id) ON DELETE CASCADE,
 variant_id TEXT NOT NULL REFERENCES variants(variant_id), offer_id BIGINT REFERENCES supplier_offers(offer_id), supplier_sku TEXT,
 cases NUMERIC(14,4) NOT NULL DEFAULT 0, loose_units NUMERIC(14,4) NOT NULL DEFAULT 0, unit_cost NUMERIC(14,4),
 line_total NUMERIC(14,2), reason_code TEXT, comment TEXT
);

CREATE OR REPLACE VIEW v_current_prices AS
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,o.shopify_units_per_case,o.qualifying_units_per_case,o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
WHERE p.price_state='current'
  AND o.active
  AND is_operational_current_variant(o.variant_id);
CREATE OR REPLACE VIEW v_future_prices AS
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,o.shopify_units_per_case,o.qualifying_units_per_case,o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
WHERE p.price_state='future'
  AND o.active
  AND is_operational_current_variant(o.variant_id);



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

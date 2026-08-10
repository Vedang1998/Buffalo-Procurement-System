-- Buffalo Procurement OS — Phase 4 historical-sales backfill hardening.
--
-- Additive, re-runnable migration.  The source-row natural identity remains
-- shopify_sales_daily_raw.source_row_hash; changing Shopify metrics are
-- observations of that fact, not part of its identity.  No customer data is
-- represented in this schema.

-- A failed run may have durable, successfully fetched chunks.  PARTIAL makes
-- that state explicit without treating it as readiness-complete.
ALTER TABLE sales_backfill_runs
    DROP CONSTRAINT IF EXISTS sales_backfill_runs_status_check;
ALTER TABLE sales_backfill_runs
    ADD CONSTRAINT sales_backfill_runs_status_check
    CHECK (status IN ('RUNNING', 'PARTIAL', 'COMPLETED', 'FAILED'));

ALTER TABLE sales_backfill_runs
    ADD COLUMN IF NOT EXISTS store_timezone TEXT,
    ADD COLUMN IF NOT EXISTS chunk_days INTEGER CHECK (chunk_days > 0),
    ADD COLUMN IF NOT EXISTS page_size INTEGER CHECK (page_size > 0),
    ADD COLUMN IF NOT EXISTS expected_chunks INTEGER NOT NULL DEFAULT 0
        CHECK (expected_chunks >= 0),
    ADD COLUMN IF NOT EXISTS completed_chunks INTEGER NOT NULL DEFAULT 0
        CHECK (completed_chunks >= 0),
    ADD COLUMN IF NOT EXISTS expected_pages INTEGER NOT NULL DEFAULT 0
        CHECK (expected_pages >= 0),
    ADD COLUMN IF NOT EXISTS completed_pages INTEGER NOT NULL DEFAULT 0
        CHECK (completed_pages >= 0),
    ADD COLUMN IF NOT EXISTS source_rows INTEGER NOT NULL DEFAULT 0
        CHECK (source_rows >= 0),
    ADD COLUMN IF NOT EXISTS unique_source_facts INTEGER NOT NULL DEFAULT 0
        CHECK (unique_source_facts >= 0),
    ADD COLUMN IF NOT EXISTS excluded_rows INTEGER NOT NULL DEFAULT 0
        CHECK (excluded_rows >= 0),
    ADD COLUMN IF NOT EXISTS source_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS raw_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS raw_net_sales NUMERIC(20,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS canonical_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS excluded_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS excluded_net_sales NUMERIC(20,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS unresolved_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS unresolved_net_sales NUMERIC(20,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ambiguous_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ambiguous_net_sales NUMERIC(20,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS query_contract_hash TEXT,
    ADD COLUMN IF NOT EXISTS coverage_complete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pages_complete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS source_facts_persisted BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS idempotency_verified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS control_totals_reconciled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS canonical_aggregate_rebuilt BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS control_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS error_class TEXT,
    ADD COLUMN IF NOT EXISTS sanitized_error_message TEXT,
    ADD COLUMN IF NOT EXISTS last_checkpoint_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_sales_backfill_runs_status_started
    ON sales_backfill_runs(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_backfill_runs_requested_range
    ON sales_backfill_runs(start_date, end_date, started_at DESC);

-- Raw facts retain stable identity while recording repeated observations.
ALTER TABLE shopify_sales_daily_raw
    ADD COLUMN IF NOT EXISTS source_identity_key TEXT,
    ADD COLUMN IF NOT EXISTS first_fetched_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_fetched_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS fetch_count INTEGER NOT NULL DEFAULT 1
        CHECK (fetch_count >= 1);

-- Preserve existing fetch evidence when upgrading a populated database.
UPDATE shopify_sales_daily_raw
SET first_fetched_at = fetched_at
WHERE first_fetched_at IS NULL;
UPDATE shopify_sales_daily_raw
SET last_fetched_at = fetched_at
WHERE last_fetched_at IS NULL;

ALTER TABLE shopify_sales_daily_raw
    ALTER COLUMN first_fetched_at SET DEFAULT now(),
    ALTER COLUMN first_fetched_at SET NOT NULL,
    ALTER COLUMN last_fetched_at SET DEFAULT now(),
    ALTER COLUMN last_fetched_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_sales_source_identity
    ON shopify_sales_daily_raw(source_identity_key, sale_date);

-- One durable checkpoint per deterministic date chunk in a run.
CREATE TABLE IF NOT EXISTS sales_backfill_chunks (
    sales_backfill_chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_backfill_id UUID NOT NULL
        REFERENCES sales_backfill_runs(sales_backfill_id) ON DELETE RESTRICT,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    requested_start_date DATE NOT NULL,
    requested_end_date DATE NOT NULL,
    query_version TEXT NOT NULL CHECK (btrim(query_version) <> ''),
    query_contract_hash TEXT NOT NULL CHECK (btrim(query_contract_hash) <> ''),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'PARTIAL', 'COMPLETED', 'FAILED')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    page_size INTEGER NOT NULL CHECK (page_size > 0),
    expected_pages INTEGER CHECK (expected_pages >= 0),
    completed_pages INTEGER NOT NULL DEFAULT 0 CHECK (completed_pages >= 0),
    row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    unique_fact_count INTEGER NOT NULL DEFAULT 0 CHECK (unique_fact_count >= 0),
    duplicate_observation_count INTEGER NOT NULL DEFAULT 0
        CHECK (duplicate_observation_count >= 0),
    restated_fact_count INTEGER NOT NULL DEFAULT 0 CHECK (restated_fact_count >= 0),
    source_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    source_net_sales NUMERIC(20,2) NOT NULL DEFAULT 0,
    control_net_items_sold NUMERIC(20,4),
    control_net_sales NUMERIC(20,2),
    control_reconciled BOOLEAN NOT NULL DEFAULT FALSE,
    control_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_hash TEXT,
    parse_state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (parse_state IN ('PENDING', 'PASS', 'FAIL')),
    parse_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_class TEXT,
    sanitized_error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_checkpoint_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (requested_start_date <= requested_end_date),
    UNIQUE (sales_backfill_id, chunk_index),
    UNIQUE (sales_backfill_id, requested_start_date, requested_end_date)
);
ALTER TABLE sales_backfill_chunks
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0
        CHECK (attempt_count >= 0);

CREATE INDEX IF NOT EXISTS idx_sales_backfill_chunks_run_status
    ON sales_backfill_chunks(sales_backfill_id, status, chunk_index);
CREATE INDEX IF NOT EXISTS idx_sales_backfill_chunks_range
    ON sales_backfill_chunks(requested_start_date, requested_end_date);

-- Each LIMIT/OFFSET response is checkpointed before the next page is fetched.
CREATE TABLE IF NOT EXISTS sales_backfill_pages (
    sales_backfill_page_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_backfill_chunk_id UUID NOT NULL
        REFERENCES sales_backfill_chunks(sales_backfill_chunk_id) ON DELETE RESTRICT,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    page_offset INTEGER NOT NULL CHECK (page_offset >= 0),
    page_limit INTEGER NOT NULL CHECK (page_limit > 0),
    requested_start_date DATE NOT NULL,
    requested_end_date DATE NOT NULL,
    query_version TEXT NOT NULL CHECK (btrim(query_version) <> ''),
    query_contract_hash TEXT NOT NULL CHECK (btrim(query_contract_hash) <> ''),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    is_terminal BOOLEAN NOT NULL DEFAULT FALSE,
    row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    unique_fact_count INTEGER NOT NULL DEFAULT 0 CHECK (unique_fact_count >= 0),
    duplicate_observation_count INTEGER NOT NULL DEFAULT 0
        CHECK (duplicate_observation_count >= 0),
    restated_fact_count INTEGER NOT NULL DEFAULT 0 CHECK (restated_fact_count >= 0),
    source_net_items_sold NUMERIC(20,4) NOT NULL DEFAULT 0,
    source_net_sales NUMERIC(20,2) NOT NULL DEFAULT 0,
    source_hash TEXT,
    parse_state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (parse_state IN ('PENDING', 'PASS', 'FAIL')),
    parse_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_class TEXT,
    sanitized_error_message TEXT,
    requested_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ,
    persisted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (requested_start_date <= requested_end_date),
    UNIQUE (sales_backfill_chunk_id, page_index),
    UNIQUE (sales_backfill_chunk_id, page_offset)
);
ALTER TABLE sales_backfill_pages
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0
        CHECK (attempt_count >= 0);

CREATE INDEX IF NOT EXISTS idx_sales_backfill_pages_chunk_status
    ON sales_backfill_pages(sales_backfill_chunk_id, status, page_index);
CREATE INDEX IF NOT EXISTS idx_sales_backfill_pages_fetch_state
    ON sales_backfill_pages(status, parse_state, created_at);

-- Durable run-to-fact membership keeps prior run observations available even
-- when Shopify later restates the metrics on the canonical raw fact.
CREATE TABLE IF NOT EXISTS sales_backfill_run_facts (
    sales_backfill_id UUID NOT NULL
        REFERENCES sales_backfill_runs(sales_backfill_id) ON DELETE RESTRICT,
    raw_sales_id BIGINT NOT NULL
        REFERENCES shopify_sales_daily_raw(raw_sales_id) ON DELETE RESTRICT,
    source_row_hash TEXT NOT NULL CHECK (btrim(source_row_hash) <> ''),
    first_observed_chunk_id UUID
        REFERENCES sales_backfill_chunks(sales_backfill_chunk_id) ON DELETE SET NULL,
    first_observed_page_id UUID
        REFERENCES sales_backfill_pages(sales_backfill_page_id) ON DELETE SET NULL,
    last_observed_chunk_id UUID
        REFERENCES sales_backfill_chunks(sales_backfill_chunk_id) ON DELETE SET NULL,
    last_observed_page_id UUID
        REFERENCES sales_backfill_pages(sales_backfill_page_id) ON DELETE SET NULL,
    first_observed_net_items_sold NUMERIC(20,4) NOT NULL,
    first_observed_net_sales NUMERIC(20,2),
    observed_net_items_sold NUMERIC(20,4) NOT NULL,
    observed_net_sales NUMERIC(20,2),
    observation_count INTEGER NOT NULL DEFAULT 1 CHECK (observation_count >= 1),
    restatement_detected BOOLEAN NOT NULL DEFAULT FALSE,
    first_observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sales_backfill_id, raw_sales_id),
    UNIQUE (sales_backfill_id, source_row_hash),
    CHECK (first_observed_at <= last_observed_at)
);

CREATE INDEX IF NOT EXISTS idx_sales_backfill_run_facts_raw
    ON sales_backfill_run_facts(raw_sales_id, sales_backfill_id);
CREATE INDEX IF NOT EXISTS idx_sales_backfill_run_facts_restatement
    ON sales_backfill_run_facts(sales_backfill_id, restatement_detected)
    WHERE restatement_detected;

-- Append-only evidence of every human review action.  Operational aliases and
-- exclusions remain in their existing tables; this table is their audit trail
-- and also records deliberate LEAVE_UNRESOLVED decisions.
CREATE TABLE IF NOT EXISTS historical_sales_review_decisions (
    historical_sales_review_decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_backfill_id UUID
        REFERENCES sales_backfill_runs(sales_backfill_id) ON DELETE SET NULL,
    source_identity_key TEXT NOT NULL CHECK (btrim(source_identity_key) <> ''),
    source_variant_id TEXT,
    source_sku TEXT,
    source_product_title TEXT,
    source_variant_title TEXT,
    decision_action TEXT NOT NULL
        CHECK (decision_action IN ('MAP', 'EXCLUDE', 'LEAVE_UNRESOLVED')),
    canonical_variant_id TEXT REFERENCES variants(variant_id) ON DELETE RESTRICT,
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    supersedes_decision_id UUID
        REFERENCES historical_sales_review_decisions(historical_sales_review_decision_id)
        ON DELETE RESTRICT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (decision_action = 'MAP' AND canonical_variant_id IS NOT NULL)
        OR (decision_action IN ('EXCLUDE', 'LEAVE_UNRESOLVED')
            AND canonical_variant_id IS NULL)
    ),
    CHECK (supersedes_decision_id IS NULL
        OR supersedes_decision_id <> historical_sales_review_decision_id)
);

CREATE INDEX IF NOT EXISTS idx_historical_sales_review_source
    ON historical_sales_review_decisions(source_identity_key, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_historical_sales_review_action
    ON historical_sales_review_decisions(decision_action, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_historical_sales_review_target
    ON historical_sales_review_decisions(canonical_variant_id, decided_at DESC)
    WHERE canonical_variant_id IS NOT NULL;

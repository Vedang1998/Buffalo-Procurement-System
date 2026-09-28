-- Monday MVP Packet 4: strict normalized supplier price-book staging.
-- Uploads may create VERIFIED FUTURE only. CURRENT remains untouched until a
-- separately authorized, audited first-of-month rollover implementation. Typed staging is
-- transient; compact batch/event/issue evidence and the raw source object are
-- retained, while promoted/rejected/superseded typed rows are purged.

CREATE TABLE IF NOT EXISTS price_book_batches (
    price_book_batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_generation BIGSERIAL NOT NULL UNIQUE,
    vendor_id UUID REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    vendor_name TEXT NOT NULL CHECK (btrim(vendor_name) <> ''),
    batch_ref TEXT NOT NULL CHECK (btrim(batch_ref) <> ''),
    target_price_state TEXT NOT NULL CHECK (target_price_state = 'future'),
    effective_from DATE NOT NULL,
    effective_through DATE,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    raw_storage_key TEXT NOT NULL CHECK (btrim(raw_storage_key) <> ''),
    future_predecessor_sha256 TEXT NOT NULL
        CHECK (future_predecessor_sha256 ~ '^[0-9a-f]{64}$'),
    future_predecessor_batch_id UUID
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (
        status IN (
            'STAGING','INVALID','VALIDATED','VERIFIED_FUTURE',
            'REJECTED','SUPERSEDED'
        )
    ),
    row_count INTEGER NOT NULL CHECK (row_count > 0),
    valid_row_count INTEGER NOT NULL DEFAULT 0 CHECK (valid_row_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    expected_offer_count INTEGER NOT NULL DEFAULT 0 CHECK (expected_offer_count >= 0),
    covered_offer_count INTEGER NOT NULL DEFAULT 0 CHECK (covered_offer_count >= 0),
    missing_offer_count INTEGER NOT NULL DEFAULT 0 CHECK (missing_offer_count >= 0),
    validation_fingerprint TEXT CHECK (
        validation_fingerprint IS NULL
        OR validation_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    validation_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(validation_evidence) = 'object'),
    staged_by TEXT NOT NULL CHECK (btrim(staged_by) <> ''),
    staged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_by TEXT,
    promoted_at TIMESTAMPTZ,
    disposition_by TEXT,
    disposition_reason TEXT,
    disposition_at TIMESTAMPTZ,
    UNIQUE (vendor_name, batch_ref),
    CONSTRAINT ck_price_book_batch_period CHECK (
        effective_through IS NULL OR effective_through >= effective_from
    ),
    CONSTRAINT ck_price_book_batch_counts CHECK (
        valid_row_count <= row_count
        AND covered_offer_count <= expected_offer_count
        AND missing_offer_count = expected_offer_count - covered_offer_count
    ),
    CONSTRAINT ck_price_book_batch_state CHECK (
        (status = 'STAGING'
            AND valid_row_count = 0 AND error_count = 0 AND warning_count = 0
            AND expected_offer_count = 0 AND covered_offer_count = 0
            AND missing_offer_count = 0 AND validation_fingerprint IS NULL
            AND validation_evidence = '{}'::jsonb
            AND promoted_by IS NULL AND promoted_at IS NULL
            AND disposition_by IS NULL AND disposition_reason IS NULL
            AND disposition_at IS NULL)
        OR (status = 'INVALID'
            AND error_count > 0 AND validation_fingerprint IS NOT NULL
            AND promoted_by IS NULL AND promoted_at IS NULL
            AND disposition_by IS NULL AND disposition_reason IS NULL
            AND disposition_at IS NULL)
        OR (status = 'VALIDATED'
            AND error_count = 0 AND missing_offer_count = 0
            AND valid_row_count = row_count AND validation_fingerprint IS NOT NULL
            AND promoted_by IS NULL AND promoted_at IS NULL
            AND disposition_by IS NULL AND disposition_reason IS NULL
            AND disposition_at IS NULL)
        OR (status = 'VERIFIED_FUTURE'
            AND error_count = 0 AND missing_offer_count = 0
            AND valid_row_count = row_count AND validation_fingerprint IS NOT NULL
            AND promoted_by IS NOT NULL AND btrim(promoted_by) <> ''
            AND promoted_at IS NOT NULL
            AND disposition_by IS NULL AND disposition_reason IS NULL
            AND disposition_at IS NULL)
        OR (status IN ('REJECTED','SUPERSEDED')
            AND validation_fingerprint IS NOT NULL
            AND disposition_by IS NOT NULL AND btrim(disposition_by) <> ''
            AND disposition_reason IS NOT NULL AND btrim(disposition_reason) <> ''
            AND disposition_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_price_book_batches_status
    ON price_book_batches(status, staged_at DESC, price_book_batch_id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_verified_future_batch_per_vendor
    ON price_book_batches(vendor_id) WHERE status = 'VERIFIED_FUTURE';

CREATE TABLE IF NOT EXISTS price_book_staging_rows (
    price_book_staging_row_id BIGSERIAL PRIMARY KEY,
    price_book_batch_id UUID NOT NULL
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
    vendor_name TEXT,
    supplier_sku TEXT,
    supplier_description TEXT,
    canonical_variant_id TEXT,
    offer_id BIGINT REFERENCES supplier_offers(offer_id) ON DELETE RESTRICT,
    package_type TEXT,
    size_text TEXT,
    raw_pack TEXT,
    shopify_units_per_case NUMERIC(12,4),
    qualifying_units_per_case NUMERIC(12,4),
    assortment_scope TEXT CHECK (
        assortment_scope IS NULL
        OR assortment_scope IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')
    ),
    assortment_group TEXT,
    assortable BOOLEAN,
    assortment_evidence TEXT,
    level_type TEXT CHECK (level_type IS NULL OR level_type IN ('BASE','BREAK')),
    break_quantity NUMERIC(12,4),
    break_unit TEXT CHECK (break_unit IN ('BT','CS') OR break_unit IS NULL),
    case_price NUMERIC(14,4),
    unit_price NUMERIC(14,4),
    source_file TEXT,
    source_page INTEGER,
    source_evidence TEXT,
    extraction_confidence TEXT,
    review_note TEXT,
    validation_status TEXT NOT NULL CHECK (validation_status IN ('VALID','INVALID')),
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(validation_errors) = 'array'),
    validation_warnings JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(validation_warnings) = 'array'),
    raw_payload JSONB NOT NULL CHECK (jsonb_typeof(raw_payload) = 'object'),
    UNIQUE (price_book_batch_id, source_row_number),
    CONSTRAINT ck_valid_price_book_staging_row CHECK (
        validation_status = 'INVALID'
        OR (
            offer_id IS NOT NULL
            AND vendor_name IS NOT NULL AND btrim(vendor_name) <> ''
            AND supplier_sku IS NOT NULL AND btrim(supplier_sku) <> ''
            AND supplier_description IS NOT NULL AND btrim(supplier_description) <> ''
            AND canonical_variant_id IS NOT NULL AND btrim(canonical_variant_id) <> ''
            AND package_type IS NOT NULL AND btrim(package_type) <> ''
            AND size_text IS NOT NULL AND btrim(size_text) <> ''
            AND raw_pack IS NOT NULL AND btrim(raw_pack) <> ''
            AND shopify_units_per_case > 0
            AND shopify_units_per_case IS NOT NULL
            AND shopify_units_per_case = trunc(shopify_units_per_case)
            AND qualifying_units_per_case > 0
            AND qualifying_units_per_case IS NOT NULL
            AND qualifying_units_per_case = trunc(qualifying_units_per_case)
            AND assortment_scope IS NOT NULL AND assortable IS NOT NULL
            AND (
                assortment_scope <> 'EXPLICIT_CROSS_PRODUCT'
                OR (assortment_group IS NOT NULL AND btrim(assortment_group) <> ''
                    AND assortment_evidence IS NOT NULL
                    AND btrim(assortment_evidence) <> '')
            )
            AND level_type IS NOT NULL
            AND unit_price IS NOT NULL
            AND unit_price > 0
            AND source_file IS NOT NULL AND btrim(source_file) <> ''
            AND (source_page IS NULL OR source_page > 0)
            AND source_evidence IS NOT NULL AND btrim(source_evidence) <> ''
            AND extraction_confidence IS NOT NULL
            AND extraction_confidence = 'VERIFIED'
            AND validation_errors = '[]'::jsonb
            AND (
                case_price IS NULL
                OR abs(case_price / shopify_units_per_case - unit_price) <= 0.01
            )
            AND (
                (level_type = 'BASE' AND break_quantity IS NULL
                    AND break_unit IS NULL AND case_price IS NOT NULL
                    AND case_price > 0)
                OR (level_type = 'BREAK' AND break_quantity IS NOT NULL
                    AND break_quantity > 0
                    AND break_quantity = trunc(break_quantity)
                    AND break_unit IS NOT NULL AND break_unit IN ('BT','CS')
                    AND (case_price IS NULL OR case_price > 0)
                    AND (break_unit <> 'CS' OR case_price IS NOT NULL))
            )
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_valid_staging_ladder_structure
    ON price_book_staging_rows(
        price_book_batch_id, offer_id, level_type,
        COALESCE(break_quantity, -1), COALESCE(break_unit, '')
    ) WHERE validation_status = 'VALID';

CREATE TABLE IF NOT EXISTS price_book_validation_issues (
    price_book_validation_issue_id BIGSERIAL PRIMARY KEY,
    price_book_batch_id UUID NOT NULL
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    source_row_number INTEGER,
    issue_code TEXT NOT NULL CHECK (btrim(issue_code) <> ''),
    severity TEXT NOT NULL CHECK (severity IN ('WARN','ERROR')),
    vendor_id UUID REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    variant_id TEXT,
    offer_id BIGINT REFERENCES supplier_offers(offer_id) ON DELETE RESTRICT,
    message TEXT NOT NULL CHECK (btrim(message) <> ''),
    resolved_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_price_book_validation_issue
    ON price_book_validation_issues(
        price_book_batch_id, COALESCE(source_row_number, -1), issue_code,
        COALESCE(offer_id, -1), COALESCE(variant_id, '')
    );

CREATE TABLE IF NOT EXISTS price_book_promotion_events (
    price_book_promotion_event_id BIGSERIAL PRIMARY KEY,
    price_book_batch_id UUID NOT NULL
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    prior_status TEXT NOT NULL CHECK (prior_status = 'VALIDATED'),
    new_status TEXT NOT NULL CHECK (new_status = 'VERIFIED_FUTURE'),
    validation_fingerprint TEXT NOT NULL CHECK (validation_fingerprint ~ '^[0-9a-f]{64}$'),
    predecessor_future_sha256 TEXT NOT NULL CHECK (predecessor_future_sha256 ~ '^[0-9a-f]{64}$'),
    predecessor_batch_id UUID
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    promoted_future_sha256 TEXT NOT NULL CHECK (promoted_future_sha256 ~ '^[0-9a-f]{64}$'),
    promoted_semantic_md5 TEXT NOT NULL CHECK (promoted_semantic_md5 ~ '^[0-9a-f]{32}$'),
    promoted_row_count INTEGER NOT NULL CHECK (promoted_row_count > 0),
    acknowledged_warning_codes JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(acknowledged_warning_codes) = 'array'),
    review_reason TEXT,
    evidence_json JSONB NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'object' AND evidence_json <> '{}'::jsonb
    ),
    recorded_by TEXT NOT NULL CHECK (btrim(recorded_by) <> ''),
    transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (price_book_batch_id, validation_fingerprint)
);

CREATE TABLE IF NOT EXISTS price_book_disposition_events (
    price_book_disposition_event_id BIGSERIAL PRIMARY KEY,
    price_book_batch_id UUID NOT NULL
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    prior_status TEXT NOT NULL
        CHECK (prior_status IN ('INVALID','VALIDATED','VERIFIED_FUTURE')),
    new_status TEXT NOT NULL CHECK (new_status IN ('REJECTED','SUPERSEDED')),
    validation_fingerprint TEXT NOT NULL CHECK (validation_fingerprint ~ '^[0-9a-f]{64}$'),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    evidence_json JSONB NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'object' AND evidence_json <> '{}'::jsonb
    ),
    recorded_by TEXT NOT NULL CHECK (btrim(recorded_by) <> ''),
    transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (price_book_batch_id)
);

-- Fresh-install/rerun compatibility for the one reviewed August seed bundle.
-- This is not a general CURRENT-price writer: the source hash and row count are
-- frozen, the event is append-only, price triggers restrict it to seed-tagged
-- offers, and a deferred assertion proves the complete final seed set.
CREATE TABLE IF NOT EXISTS legacy_price_seed_events (
    legacy_price_seed_event_id BIGSERIAL PRIMARY KEY,
    manifest_sha256 TEXT NOT NULL
        CHECK (manifest_sha256='2231ee97b9f01e98ada7da765718f1b217d4c6003a56e27506443f2848556456'),
    supplier_offer_seed_sha256 TEXT NOT NULL
        CHECK (supplier_offer_seed_sha256='e31f23c1a8f306efff4b2850d5299b84cca36ca92f2e744e6ec4f54f065c7253'),
    expected_offer_row_count INTEGER NOT NULL CHECK (expected_offer_row_count=85),
    seed_sha256 TEXT NOT NULL
        CHECK (seed_sha256='44bacf203e19bd12071b616430a9091ca5c1c4b46da48ea1e6f04f7b8f7f8201'),
    expected_row_count INTEGER NOT NULL CHECK (expected_row_count=271),
    semantic_md5 TEXT NOT NULL DEFAULT '3a14202bc6ff3f6e85820e9db9bafd63'
        CHECK (semantic_md5='3a14202bc6ff3f6e85820e9db9bafd63'),
    recorded_by TEXT NOT NULL CHECK (btrim(recorded_by) <> ''),
    transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION authorize_legacy_price_seed_import(
    requested_manifest_sha256 TEXT,
    requested_offer_sha256 TEXT,
    requested_offer_row_count INTEGER,
    requested_sha256 TEXT,
    requested_row_count INTEGER,
    requested_actor TEXT
) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE event_id BIGINT;
BEGIN
    IF requested_manifest_sha256 <> '2231ee97b9f01e98ada7da765718f1b217d4c6003a56e27506443f2848556456'
       OR requested_offer_sha256 <> 'e31f23c1a8f306efff4b2850d5299b84cca36ca92f2e744e6ec4f54f065c7253'
       OR requested_offer_row_count <> 85
       OR requested_sha256 <> '44bacf203e19bd12071b616430a9091ca5c1c4b46da48ea1e6f04f7b8f7f8201'
       OR requested_row_count <> 271
       OR requested_actor IS NULL OR btrim(requested_actor)='' THEN
        RAISE EXCEPTION 'legacy CURRENT seed authority does not match reviewed bundle';
    END IF;
    INSERT INTO legacy_price_seed_events(
        manifest_sha256,supplier_offer_seed_sha256,expected_offer_row_count,
        seed_sha256,expected_row_count,recorded_by
    ) VALUES (
        requested_manifest_sha256,requested_offer_sha256,requested_offer_row_count,
        requested_sha256,requested_row_count,requested_actor
    )
    RETURNING legacy_price_seed_event_id INTO event_id;
    RETURN event_id;
END
$$;

ALTER TABLE prices ADD COLUMN IF NOT EXISTS source_price_book_batch_id UUID
    REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT;
ALTER TABLE prices ADD COLUMN IF NOT EXISTS source_price_book_row_number INTEGER;
ALTER TABLE prices ADD COLUMN IF NOT EXISTS effective_from DATE;
ALTER TABLE prices ADD COLUMN IF NOT EXISTS effective_through DATE;

-- Pre-011 FUTURE rows have no reviewed batch/source authority. Failing the
-- migration is safer than silently adopting or deleting them. Reapplication
-- remains valid because Packet-4 FUTURE rows always carry batch provenance.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM prices
         WHERE price_state='future' AND source_price_book_batch_id IS NULL
    ) THEN
        RAISE EXCEPTION 'unprovenanced pre-011 FUTURE prices require review before migration';
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_prices_price_book_provenance'
          AND conrelid = 'prices'::regclass
    ) THEN
        ALTER TABLE prices ADD CONSTRAINT ck_prices_price_book_provenance CHECK (
            (source_price_book_batch_id IS NULL AND source_price_book_row_number IS NULL)
            OR (source_price_book_batch_id IS NOT NULL
                AND source_price_book_row_number IS NOT NULL
                AND source_price_book_row_number >= 2
                AND effective_from IS NOT NULL
                AND (effective_through IS NULL OR effective_through >= effective_from))
        );
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM prices
        GROUP BY offer_id, price_state, level_type,
                 COALESCE(break_qty, -1), COALESCE(break_unit, '')
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'price ladder has duplicate operational structural levels';
    END IF;
END
$$;

DROP INDEX IF EXISTS uq_price_ladder_structure;
CREATE UNIQUE INDEX IF NOT EXISTS uq_operational_price_ladder_structure
    ON prices(
        offer_id, price_state, level_type,
        COALESCE(break_qty, -1), COALESCE(break_unit, '')
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_book_promoted_source_row
    ON prices(source_price_book_batch_id,source_price_book_row_number)
    WHERE source_price_book_batch_id IS NOT NULL;

CREATE OR REPLACE FUNCTION guard_price_book_batch_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'STAGING'
       OR NEW.valid_row_count <> 0 OR NEW.error_count <> 0
       OR NEW.warning_count <> 0 OR NEW.expected_offer_count <> 0
       OR NEW.covered_offer_count <> 0 OR NEW.missing_offer_count <> 0
       OR NEW.validation_fingerprint IS NOT NULL
       OR NEW.validation_evidence <> '{}'::jsonb
       OR NEW.promoted_by IS NOT NULL OR NEW.promoted_at IS NOT NULL
       OR NEW.disposition_by IS NOT NULL OR NEW.disposition_reason IS NOT NULL
       OR NEW.disposition_at IS NOT NULL THEN
        RAISE EXCEPTION 'price-book batch must begin as pristine STAGING';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_price_book_batch_insert ON price_book_batches;
CREATE TRIGGER trg_guard_price_book_batch_insert
BEFORE INSERT ON price_book_batches
FOR EACH ROW EXECUTE FUNCTION guard_price_book_batch_insert();

CREATE OR REPLACE FUNCTION guard_price_book_child_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_status TEXT;
BEGIN
    SELECT status INTO parent_status FROM price_book_batches
     WHERE price_book_batch_id = NEW.price_book_batch_id;
    IF parent_status IS DISTINCT FROM 'STAGING' THEN
        RAISE EXCEPTION 'price-book child evidence may only be added while STAGING';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_price_book_staging_insert ON price_book_staging_rows;
CREATE TRIGGER trg_guard_price_book_staging_insert
BEFORE INSERT ON price_book_staging_rows
FOR EACH ROW EXECUTE FUNCTION guard_price_book_child_insert();
DROP TRIGGER IF EXISTS trg_guard_price_book_issue_insert ON price_book_validation_issues;
CREATE TRIGGER trg_guard_price_book_issue_insert
BEFORE INSERT ON price_book_validation_issues
FOR EACH ROW EXECUTE FUNCTION guard_price_book_child_insert();

CREATE OR REPLACE FUNCTION price_book_staging_semantic_md5(target_batch UUID)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT md5(COALESCE(string_agg(
        jsonb_build_array(
            s.offer_id,date_trunc('month',b.effective_from)::date,s.level_type,
            s.break_quantity,s.break_unit,s.case_price,s.unit_price,s.source_file,
            s.source_page,s.extraction_confidence,s.review_note,b.effective_from,
            b.effective_through
        )::text,
        E'\n' ORDER BY s.offer_id,(s.level_type<>'BASE'),s.break_unit NULLS FIRST,
        s.break_quantity NULLS FIRST,s.source_row_number
    ),''))
      FROM price_book_staging_rows s
      JOIN price_book_batches b USING(price_book_batch_id)
     WHERE s.price_book_batch_id=target_batch AND s.validation_status='VALID'
$$;

CREATE OR REPLACE FUNCTION price_book_operational_semantic_md5(target_batch UUID)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT md5(COALESCE(string_agg(
        jsonb_build_array(
            p.offer_id,p.effective_month,p.level_type,p.break_qty,p.break_unit,
            p.case_price,p.unit_price,p.source_file,p.source_page,
            p.extraction_confidence,p.notes,p.effective_from,p.effective_through
        )::text,
        E'\n' ORDER BY p.offer_id,(p.level_type<>'BASE'),p.break_unit NULLS FIRST,
        p.break_qty NULLS FIRST,p.source_price_book_row_number
    ),''))
      FROM prices p
     WHERE p.source_price_book_batch_id=target_batch AND p.price_state='future'
$$;

CREATE OR REPLACE FUNCTION validate_price_book_promotion_event()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    b price_book_batches%ROWTYPE;
    active_predecessor UUID;
    expected_offers JSONB;
    staged_offers JSONB;
    error_codes JSONB;
    warning_codes JSONB;
BEGIN
    SELECT * INTO b FROM price_book_batches
     WHERE price_book_batch_id = NEW.price_book_batch_id FOR UPDATE;
    SELECT predecessor.price_book_batch_id INTO active_predecessor
      FROM price_book_batches predecessor
     WHERE predecessor.vendor_id=b.vendor_id
       AND predecessor.status='VERIFIED_FUTURE';
    SELECT COALESCE(jsonb_agg(o.offer_id ORDER BY o.offer_id), '[]'::jsonb)
      INTO expected_offers
      FROM supplier_offers o
      JOIN vendors v ON v.vendor_id=o.vendor_id
     WHERE o.vendor_id=b.vendor_id AND o.active
       AND v.active AND is_procurement_eligible_variant(o.variant_id);
    SELECT COALESCE(jsonb_agg(distinct_ids.offer_id ORDER BY distinct_ids.offer_id),'[]'::jsonb)
      INTO staged_offers
      FROM (
            SELECT DISTINCT s.offer_id
              FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status='VALID' AND s.offer_id IS NOT NULL
      ) distinct_ids;
    SELECT COALESCE(jsonb_agg(c.issue_code ORDER BY c.issue_code),'[]'::jsonb)
      INTO error_codes
      FROM (
            SELECT DISTINCT issue_code
              FROM price_book_validation_issues
             WHERE price_book_batch_id=b.price_book_batch_id AND severity='ERROR'
      ) c;
    SELECT COALESCE(jsonb_agg(c.issue_code ORDER BY c.issue_code),'[]'::jsonb)
      INTO warning_codes
      FROM (
            SELECT DISTINCT issue_code
              FROM price_book_validation_issues
             WHERE price_book_batch_id=b.price_book_batch_id AND severity='WARN'
      ) c;
    IF b.status <> 'VALIDATED'
       OR b.target_price_state <> 'future'
       OR b.vendor_id IS NULL
       OR NOT EXISTS (
            SELECT 1 FROM vendors v
             WHERE v.vendor_id=b.vendor_id
               AND v.vendor_name=b.vendor_name
               AND v.active
       )
       OR b.validation_fingerprint IS DISTINCT FROM NEW.validation_fingerprint
       OR b.future_predecessor_sha256 IS DISTINCT FROM NEW.predecessor_future_sha256
       OR b.future_predecessor_batch_id IS DISTINCT FROM NEW.predecessor_batch_id
       OR active_predecessor IS DISTINCT FROM b.future_predecessor_batch_id
       OR NEW.transaction_id <> txid_current()
       OR NEW.promoted_row_count <> b.row_count
       OR NEW.promoted_semantic_md5 IS DISTINCT FROM
            price_book_staging_semantic_md5(b.price_book_batch_id)
       OR NEW.evidence_json->>'promoted_future_sha256'
            IS DISTINCT FROM NEW.promoted_future_sha256
       OR NEW.evidence_json->>'promoted_semantic_md5'
            IS DISTINCT FROM NEW.promoted_semantic_md5
       OR NEW.evidence_json->>'validation_fingerprint'
            IS DISTINCT FROM NEW.validation_fingerprint
       OR NEW.evidence_json->>'content_sha256' IS DISTINCT FROM b.content_sha256
       OR expected_offers IS DISTINCT FROM
            COALESCE(b.validation_evidence->'expected_offer_ids','[]'::jsonb)
       OR expected_offers IS DISTINCT FROM
            COALESCE(b.validation_evidence->'covered_offer_ids','[]'::jsonb)
       OR staged_offers IS DISTINCT FROM expected_offers
       OR b.expected_offer_count <> jsonb_array_length(expected_offers)
       OR b.covered_offer_count <> jsonb_array_length(staged_offers)
       OR b.missing_offer_count <> 0
       OR COALESCE(b.validation_evidence->'missing_offer_ids','[]'::jsonb) <> '[]'::jsonb
       OR COALESCE(b.validation_evidence->'error_codes','[]'::jsonb)
            IS DISTINCT FROM error_codes
       OR COALESCE(b.validation_evidence->'warning_codes','[]'::jsonb)
            IS DISTINCT FROM warning_codes
       OR b.error_count <> (SELECT count(*) FROM price_book_validation_issues i
                             WHERE i.price_book_batch_id=b.price_book_batch_id
                               AND i.severity='ERROR')
       OR b.warning_count <> (SELECT count(*) FROM price_book_validation_issues i
                               WHERE i.price_book_batch_id=b.price_book_batch_id
                                 AND i.severity='WARN')
       OR NEW.acknowledged_warning_codes IS DISTINCT FROM
            COALESCE(b.validation_evidence->'warning_codes', '[]'::jsonb)
       OR (b.warning_count > 0 AND (NEW.review_reason IS NULL OR btrim(NEW.review_reason) = ''))
       OR (b.warning_count = 0 AND NEW.review_reason IS NOT NULL)
       OR date_trunc('month',b.effective_from)::date IS DISTINCT FROM
            (date_trunc('month',(clock_timestamp() AT TIME ZONE 'America/New_York'))
             + interval '1 month')::date
       OR EXISTS (
            SELECT 1 FROM price_book_batches newer
             WHERE newer.vendor_id=b.vendor_id
               AND newer.price_book_batch_id<>b.price_book_batch_id
               AND newer.status IN ('VALIDATED','VERIFIED_FUTURE')
               AND (
                    date_trunc('month',newer.effective_from) > date_trunc('month',b.effective_from)
                    OR (date_trunc('month',newer.effective_from)=date_trunc('month',b.effective_from)
                        AND newer.batch_generation>b.batch_generation)
               )
       )
       OR (b.future_predecessor_batch_id IS NULL AND EXISTS (
            SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
             WHERE o.vendor_id=b.vendor_id AND p.price_state='future'
       ))
       OR (b.future_predecessor_batch_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
             WHERE o.vendor_id=b.vendor_id AND p.price_state='future'
               AND p.source_price_book_batch_id IS DISTINCT FROM b.future_predecessor_batch_id
       ))
       OR (SELECT count(*) FROM price_book_staging_rows s
            WHERE s.price_book_batch_id=b.price_book_batch_id
              AND s.validation_status='VALID') <> b.row_count
       OR (SELECT count(*) FROM price_book_staging_rows s
            WHERE s.price_book_batch_id=b.price_book_batch_id) <> b.row_count
       OR EXISTS (
            SELECT 1 FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status<>'VALID'
       )
       OR EXISTS (
            SELECT s.offer_id
              FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status='VALID'
             GROUP BY s.offer_id
            HAVING count(*) FILTER (WHERE s.level_type='BASE') <> 1
       )
       OR EXISTS (
            SELECT 1
              FROM price_book_staging_rows break_row
              JOIN price_book_staging_rows base_row
                ON base_row.price_book_batch_id=break_row.price_book_batch_id
               AND base_row.offer_id=break_row.offer_id
               AND base_row.level_type='BASE'
             WHERE break_row.price_book_batch_id=b.price_book_batch_id
               AND break_row.validation_status='VALID'
               AND break_row.level_type='BREAK'
               AND break_row.unit_price>base_row.unit_price
       )
       OR EXISTS (
            SELECT 1
              FROM price_book_staging_rows deeper
              JOIN price_book_staging_rows shallower
                ON shallower.price_book_batch_id=deeper.price_book_batch_id
               AND shallower.offer_id=deeper.offer_id
               AND shallower.level_type='BREAK'
               AND shallower.break_unit=deeper.break_unit
               AND shallower.break_quantity<deeper.break_quantity
             WHERE deeper.price_book_batch_id=b.price_book_batch_id
               AND deeper.validation_status='VALID'
               AND deeper.level_type='BREAK'
               AND deeper.unit_price>shallower.unit_price
       )
       OR EXISTS (
            SELECT 1
              FROM price_book_staging_rows s
              LEFT JOIN supplier_offers o ON o.offer_id=s.offer_id
              LEFT JOIN vendors v ON v.vendor_id=o.vendor_id
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status='VALID'
               AND (
                    o.offer_id IS NULL OR o.vendor_id IS DISTINCT FROM b.vendor_id
                    OR NOT o.active OR o.confidence IS DISTINCT FROM 'VERIFIED'
                    OR NOT COALESCE(v.active,FALSE)
                    OR NOT is_procurement_eligible_variant(o.variant_id)
                    OR s.vendor_name IS DISTINCT FROM b.vendor_name
                    OR s.supplier_sku IS DISTINCT FROM o.supplier_sku
                    OR s.canonical_variant_id IS DISTINCT FROM o.variant_id
                    OR upper(s.package_type) IS DISTINCT FROM upper(o.package_type)
                    OR regexp_replace(btrim(s.size_text),'\\s+',' ','g')
                       IS DISTINCT FROM regexp_replace(btrim(o.size_text),'\\s+',' ','g')
                    OR regexp_replace(btrim(s.raw_pack),'\\s+',' ','g')
                       IS DISTINCT FROM regexp_replace(btrim(o.raw_pack),'\\s+',' ','g')
                    OR s.shopify_units_per_case IS DISTINCT FROM o.shopify_units_per_case
                    OR s.qualifying_units_per_case IS DISTINCT FROM o.qualifying_units_per_case
                    OR s.assortment_scope IS DISTINCT FROM o.assortment_scope
                    OR s.assortment_group IS DISTINCT FROM o.assortment_group
                    OR s.assortable IS DISTINCT FROM o.assortable
               )
       ) THEN
        RAISE EXCEPTION 'price-book promotion event does not match locked validation authority';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_price_book_promotion_event
    ON price_book_promotion_events;
CREATE TRIGGER trg_validate_price_book_promotion_event
BEFORE INSERT ON price_book_promotion_events
FOR EACH ROW EXECUTE FUNCTION validate_price_book_promotion_event();

CREATE OR REPLACE FUNCTION validate_price_book_disposition_event()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b price_book_batches%ROWTYPE;
BEGIN
    SELECT * INTO b FROM price_book_batches
     WHERE price_book_batch_id = NEW.price_book_batch_id FOR UPDATE;
    IF b.status IS DISTINCT FROM NEW.prior_status
       OR b.validation_fingerprint IS DISTINCT FROM NEW.validation_fingerprint
       OR NEW.transaction_id <> txid_current()
       OR (NEW.prior_status = 'VERIFIED_FUTURE' AND NEW.new_status <> 'SUPERSEDED')
       OR (NEW.prior_status = 'INVALID' AND NEW.new_status <> 'REJECTED')
       OR (NEW.new_status='SUPERSEDED' AND NOT EXISTS (
            SELECT 1
              FROM price_book_promotion_events pe
              JOIN price_book_batches replacement
                ON replacement.price_book_batch_id=pe.price_book_batch_id
             WHERE pe.transaction_id=txid_current()
               AND replacement.vendor_id=b.vendor_id
               AND replacement.price_book_batch_id<>b.price_book_batch_id
               AND (
                    date_trunc('month',replacement.effective_from) > date_trunc('month',b.effective_from)
                    OR (date_trunc('month',replacement.effective_from)=date_trunc('month',b.effective_from)
                        AND replacement.batch_generation>b.batch_generation)
               )
       )) THEN
        RAISE EXCEPTION 'price-book disposition event does not match locked batch authority';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_price_book_disposition_event
    ON price_book_disposition_events;
CREATE TRIGGER trg_validate_price_book_disposition_event
BEFORE INSERT ON price_book_disposition_events
FOR EACH ROW EXECUTE FUNCTION validate_price_book_disposition_event();

CREATE OR REPLACE FUNCTION guard_price_book_batch_update()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_ok BOOLEAN := FALSE;
BEGIN
    IF ROW(
        NEW.batch_generation,NEW.vendor_id,NEW.vendor_name,NEW.batch_ref,
        NEW.target_price_state,NEW.effective_from,NEW.effective_through,
        NEW.content_sha256,NEW.raw_storage_key,NEW.future_predecessor_sha256,
        NEW.future_predecessor_batch_id,
        NEW.row_count,NEW.staged_by,NEW.staged_at
    ) IS DISTINCT FROM ROW(
        OLD.batch_generation,OLD.vendor_id,OLD.vendor_name,OLD.batch_ref,
        OLD.target_price_state,OLD.effective_from,OLD.effective_through,
        OLD.content_sha256,OLD.raw_storage_key,OLD.future_predecessor_sha256,
        OLD.future_predecessor_batch_id,
        OLD.row_count,OLD.staged_by,OLD.staged_at
    ) THEN
        RAISE EXCEPTION 'price-book batch identity and raw provenance are immutable';
    END IF;
    IF OLD.status <> 'STAGING' AND ROW(
        NEW.valid_row_count,NEW.error_count,NEW.warning_count,
        NEW.expected_offer_count,NEW.covered_offer_count,NEW.missing_offer_count,
        NEW.validation_fingerprint,NEW.validation_evidence
    ) IS DISTINCT FROM ROW(
        OLD.valid_row_count,OLD.error_count,OLD.warning_count,
        OLD.expected_offer_count,OLD.covered_offer_count,OLD.missing_offer_count,
        OLD.validation_fingerprint,OLD.validation_evidence
    ) THEN
        RAISE EXCEPTION 'validated price-book controls are immutable';
    END IF;
    IF OLD.status IN ('REJECTED','SUPERSEDED')
       AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'completed price-book batch is immutable';
    END IF;

    IF ROW(NEW.promoted_by,NEW.promoted_at) IS DISTINCT FROM
       ROW(OLD.promoted_by,OLD.promoted_at) THEN
        IF NOT (
            OLD.status='VALIDATED' AND NEW.status='VERIFIED_FUTURE'
            AND EXISTS (
                SELECT 1 FROM price_book_promotion_events e
                 WHERE e.price_book_batch_id=OLD.price_book_batch_id
                   AND e.validation_fingerprint=OLD.validation_fingerprint
                   AND e.transaction_id=txid_current()
                   AND e.recorded_by=NEW.promoted_by
                   AND e.recorded_at=NEW.promoted_at
            )
        ) THEN
            RAISE EXCEPTION 'price-book promotion attribution is immutable and event-bound';
        END IF;
    END IF;
    IF ROW(NEW.disposition_by,NEW.disposition_reason,NEW.disposition_at)
       IS DISTINCT FROM
       ROW(OLD.disposition_by,OLD.disposition_reason,OLD.disposition_at) THEN
        IF NOT (
            NEW.status IN ('REJECTED','SUPERSEDED')
            AND EXISTS (
                SELECT 1 FROM price_book_disposition_events e
                 WHERE e.price_book_batch_id=OLD.price_book_batch_id
                   AND e.prior_status=OLD.status AND e.new_status=NEW.status
                   AND e.transaction_id=txid_current()
                   AND e.recorded_by=NEW.disposition_by
                   AND e.reason=NEW.disposition_reason
                   AND e.recorded_at=NEW.disposition_at
            )
        ) THEN
            RAISE EXCEPTION 'price-book disposition attribution is immutable and event-bound';
        END IF;
    END IF;
    IF OLD.status = 'STAGING' AND NEW.status NOT IN ('INVALID','VALIDATED') THEN
        RAISE EXCEPTION 'STAGING batch must transition through validation';
    ELSIF OLD.status = 'INVALID' AND NEW.status <> 'REJECTED' THEN
        RAISE EXCEPTION 'INVALID batch may only be rejected';
    ELSIF OLD.status = 'VALIDATED'
       AND NEW.status NOT IN ('VERIFIED_FUTURE','REJECTED','SUPERSEDED') THEN
        RAISE EXCEPTION 'VALIDATED batch requires promotion or disposition';
    ELSIF OLD.status = 'VERIFIED_FUTURE' AND NEW.status <> 'SUPERSEDED' THEN
        RAISE EXCEPTION 'VERIFIED FUTURE batch may only be superseded by a newer promotion';
    END IF;

    IF OLD.status = 'VALIDATED' AND NEW.status = 'VERIFIED_FUTURE' THEN
        event_ok := EXISTS (
            SELECT 1 FROM price_book_promotion_events e
             WHERE e.price_book_batch_id=OLD.price_book_batch_id
               AND e.validation_fingerprint=OLD.validation_fingerprint
               AND e.transaction_id=txid_current()
               AND e.recorded_by=NEW.promoted_by
               AND e.recorded_at=NEW.promoted_at
        );
        IF NOT event_ok
           OR EXISTS (SELECT 1 FROM price_book_staging_rows s
                       WHERE s.price_book_batch_id=OLD.price_book_batch_id)
           OR (SELECT count(*) FROM prices p
                WHERE p.source_price_book_batch_id=OLD.price_book_batch_id
                  AND p.price_state='future' AND p.verified) <> OLD.row_count
           OR EXISTS (
                SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
                 WHERE o.vendor_id=OLD.vendor_id AND p.price_state='future'
                   AND p.source_price_book_batch_id IS DISTINCT FROM OLD.price_book_batch_id
           ) THEN
            RAISE EXCEPTION 'price-book promotion does not own the complete vendor FUTURE set';
        END IF;
    ELSIF NEW.status IN ('REJECTED','SUPERSEDED') THEN
        event_ok := EXISTS (
            SELECT 1 FROM price_book_disposition_events e
             WHERE e.price_book_batch_id=OLD.price_book_batch_id
               AND e.prior_status=OLD.status AND e.new_status=NEW.status
               AND e.transaction_id=txid_current()
               AND e.recorded_by=NEW.disposition_by
               AND e.reason=NEW.disposition_reason
               AND e.recorded_at=NEW.disposition_at
        );
        IF NOT event_ok OR EXISTS (
            SELECT 1 FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=OLD.price_book_batch_id
        ) THEN
            RAISE EXCEPTION 'price-book disposition lacks authority or typed-row purge';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_price_book_batch_update ON price_book_batches;
CREATE TRIGGER trg_guard_price_book_batch_update
BEFORE UPDATE ON price_book_batches
FOR EACH ROW EXECUTE FUNCTION guard_price_book_batch_update();

CREATE OR REPLACE FUNCTION prevent_price_book_batch_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'price-book batch authority cannot be deleted';
END
$$;
DROP TRIGGER IF EXISTS trg_prevent_price_book_batch_delete ON price_book_batches;
CREATE TRIGGER trg_prevent_price_book_batch_delete
BEFORE DELETE ON price_book_batches
FOR EACH ROW EXECUTE FUNCTION prevent_price_book_batch_delete();

CREATE OR REPLACE FUNCTION guard_price_book_staging_row_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_batch UUID := COALESCE(NEW.price_book_batch_id, OLD.price_book_batch_id);
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'price-book staging rows are immutable';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM price_book_promotion_events e
         WHERE e.price_book_batch_id=target_batch AND e.transaction_id=txid_current()
        UNION ALL
        SELECT 1 FROM price_book_disposition_events e
         WHERE e.price_book_batch_id=target_batch AND e.transaction_id=txid_current()
    ) THEN
        RAISE EXCEPTION 'typed staging rows require promotion/disposition authority';
    END IF;
    RETURN OLD;
END
$$;
DROP TRIGGER IF EXISTS trg_guard_price_book_staging_row_mutation
    ON price_book_staging_rows;
CREATE TRIGGER trg_guard_price_book_staging_row_mutation
BEFORE UPDATE OR DELETE ON price_book_staging_rows
FOR EACH ROW EXECUTE FUNCTION guard_price_book_staging_row_mutation();

CREATE OR REPLACE FUNCTION guard_price_book_issue_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'price-book validation issues cannot be deleted';
    END IF;
    IF ROW(NEW.price_book_batch_id,NEW.source_row_number,NEW.issue_code,
           NEW.severity,NEW.vendor_id,NEW.variant_id,NEW.offer_id,NEW.message)
       IS DISTINCT FROM
       ROW(OLD.price_book_batch_id,OLD.source_row_number,OLD.issue_code,
           OLD.severity,OLD.vendor_id,OLD.variant_id,OLD.offer_id,OLD.message)
       OR OLD.resolved_at IS NOT NULL OR NEW.resolved_at IS NULL
       OR NOT EXISTS (
            SELECT 1 FROM price_book_promotion_events e
             WHERE e.price_book_batch_id=OLD.price_book_batch_id
               AND e.transaction_id=txid_current()
            UNION ALL
            SELECT 1 FROM price_book_disposition_events e
             WHERE e.price_book_batch_id=OLD.price_book_batch_id
               AND e.transaction_id=txid_current()
       ) THEN
        RAISE EXCEPTION 'price-book validation issue evidence is immutable';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_guard_price_book_issue_mutation
    ON price_book_validation_issues;
CREATE TRIGGER trg_guard_price_book_issue_mutation
BEFORE UPDATE OR DELETE ON price_book_validation_issues
FOR EACH ROW EXECUTE FUNCTION guard_price_book_issue_mutation();

CREATE OR REPLACE FUNCTION prevent_price_book_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'price-book event authority is append-only';
END
$$;
DROP TRIGGER IF EXISTS trg_prevent_price_book_promotion_event_mutation
    ON price_book_promotion_events;
CREATE TRIGGER trg_prevent_price_book_promotion_event_mutation
BEFORE UPDATE OR DELETE ON price_book_promotion_events
FOR EACH ROW EXECUTE FUNCTION prevent_price_book_event_mutation();
DROP TRIGGER IF EXISTS trg_prevent_price_book_disposition_event_mutation
    ON price_book_disposition_events;
CREATE TRIGGER trg_prevent_price_book_disposition_event_mutation
BEFORE UPDATE OR DELETE ON price_book_disposition_events
FOR EACH ROW EXECUTE FUNCTION prevent_price_book_event_mutation();
DROP TRIGGER IF EXISTS trg_prevent_legacy_price_seed_event_mutation
    ON legacy_price_seed_events;
CREATE TRIGGER trg_prevent_legacy_price_seed_event_mutation
BEFORE UPDATE OR DELETE ON legacy_price_seed_events
FOR EACH ROW EXECUTE FUNCTION prevent_price_book_event_mutation();
CREATE OR REPLACE FUNCTION assert_price_book_event_completed()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'price_book_promotion_events' THEN
        IF NOT EXISTS (
            SELECT 1 FROM price_book_batches b
             WHERE b.price_book_batch_id=NEW.price_book_batch_id
               AND b.status='VERIFIED_FUTURE'
               AND b.validation_fingerprint=NEW.validation_fingerprint
        ) OR (SELECT count(*) FROM prices p
               WHERE p.source_price_book_batch_id=NEW.price_book_batch_id
                 AND p.price_state='future' AND p.verified) <> NEW.promoted_row_count
          OR price_book_operational_semantic_md5(NEW.price_book_batch_id)
                IS DISTINCT FROM NEW.promoted_semantic_md5
          OR EXISTS (
                SELECT 1 FROM price_book_validation_issues i
                 WHERE i.price_book_batch_id=NEW.price_book_batch_id
                   AND i.resolved_at IS NULL
          )
          OR NOT EXISTS (
                SELECT 1 FROM price_book_batches b
                JOIN vendors v ON v.vendor_id=b.vendor_id
                 WHERE b.price_book_batch_id=NEW.price_book_batch_id
                   AND b.promoted_by=NEW.recorded_by
                   AND b.promoted_at=NEW.recorded_at
                   AND v.vendor_name=b.vendor_name AND v.active
          ) THEN
            RAISE EXCEPTION 'price-book promotion event lacks completed FUTURE state';
        END IF;
    ELSIF TG_TABLE_NAME = 'price_book_disposition_events' THEN
        IF NOT EXISTS (
            SELECT 1 FROM price_book_batches b
             WHERE b.price_book_batch_id=NEW.price_book_batch_id
               AND b.status=NEW.new_status
        ) OR EXISTS (
            SELECT 1 FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=NEW.price_book_batch_id
        ) OR EXISTS (
            SELECT 1 FROM price_book_validation_issues i
             WHERE i.price_book_batch_id=NEW.price_book_batch_id
               AND i.resolved_at IS NULL
        ) OR (NEW.new_status='SUPERSEDED' AND EXISTS (
            SELECT 1 FROM prices p
             WHERE p.source_price_book_batch_id=NEW.price_book_batch_id
        )) OR NOT EXISTS (
            SELECT 1 FROM price_book_batches b
             WHERE b.price_book_batch_id=NEW.price_book_batch_id
               AND b.disposition_by=NEW.recorded_by
               AND b.disposition_reason=NEW.reason
               AND b.disposition_at=NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'price-book disposition event lacks completed purge state';
        END IF;
    ELSIF TG_TABLE_NAME = 'legacy_price_seed_events' THEN
        IF (SELECT count(DISTINCT o.offer_id)
              FROM prices p
              JOIN supplier_offers o ON o.offer_id=p.offer_id
             WHERE o.notes LIKE 'v0.1 seed migration:%') <> NEW.expected_offer_row_count
           OR (SELECT count(*)
              FROM prices p
              JOIN supplier_offers o ON o.offer_id=p.offer_id
             WHERE o.notes LIKE 'v0.1 seed migration:%') <> NEW.expected_row_count
           OR EXISTS (
                SELECT 1
                  FROM prices p
                  JOIN supplier_offers o ON o.offer_id=p.offer_id
                 WHERE o.notes LIKE 'v0.1 seed migration:%'
                   AND (p.source_price_book_batch_id IS NOT NULL
                        OR p.price_state<>'current'
                        OR p.effective_month<>'2026-08-01'::date)
           )
           OR (SELECT md5(string_agg(
                    jsonb_build_array(
                        o.variant_id,vr.active,vr.catalog_state,vr.identity_scope,
                        v.vendor_name,v.active,o.supplier_sku,o.supplier_description,
                        o.package_type,o.size_text,o.raw_pack,o.shopify_units_per_case,
                        o.qualifying_units_per_case,o.assortment_scope,o.assortment_group,
                        o.assortable,o.allocation_limit,o.active,o.valid_from,o.valid_to,
                        o.source_file,o.source_page,o.confidence,o.notes,
                        p.price_state,p.effective_month,p.level_type,p.break_qty,p.break_unit,
                        p.case_price,p.unit_price,p.source_file,p.source_page,
                        p.extraction_confidence,p.verified,p.notes
                    )::text,
                    E'\n' ORDER BY o.variant_id,v.vendor_name,o.supplier_sku,
                    o.package_type,o.size_text,o.raw_pack,p.level_type,
                    p.break_qty NULLS FIRST,p.break_unit NULLS FIRST,
                    p.unit_price,p.source_file,p.source_page
                ))
                FROM prices p
                JOIN supplier_offers o ON o.offer_id=p.offer_id
                JOIN vendors v ON v.vendor_id=o.vendor_id
                JOIN variants vr ON vr.variant_id=o.variant_id
               WHERE o.notes LIKE 'v0.1 seed migration:%')
              IS DISTINCT FROM NEW.semantic_md5 THEN
            RAISE EXCEPTION 'legacy CURRENT seed event lacks the exact reviewed final set';
        END IF;
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_assert_price_book_promotion_completed
    ON price_book_promotion_events;
CREATE CONSTRAINT TRIGGER trg_assert_price_book_promotion_completed
AFTER INSERT ON price_book_promotion_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_price_book_event_completed();
DROP TRIGGER IF EXISTS trg_assert_price_book_disposition_completed
    ON price_book_disposition_events;
CREATE CONSTRAINT TRIGGER trg_assert_price_book_disposition_completed
AFTER INSERT ON price_book_disposition_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_price_book_event_completed();
DROP TRIGGER IF EXISTS trg_assert_legacy_price_seed_completed
    ON legacy_price_seed_events;
CREATE CONSTRAINT TRIGGER trg_assert_legacy_price_seed_completed
AFTER INSERT ON legacy_price_seed_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_price_book_event_completed();
CREATE OR REPLACE FUNCTION validate_price_book_price_provenance()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    b price_book_batches%ROWTYPE;
    offer_vendor UUID;
    seed_offer BOOLEAN := FALSE;
BEGIN
    IF NEW.source_price_book_batch_id IS NULL THEN
        IF TG_OP='INSERT' THEN
            SELECT notes LIKE 'v0.1 seed migration:%' INTO seed_offer
              FROM supplier_offers WHERE offer_id=NEW.offer_id;
            IF seed_offer
               AND NEW.price_state='current'
               AND NEW.effective_month='2026-08-01'::date
               AND EXISTS (
                    SELECT 1 FROM legacy_price_seed_events e
                     WHERE e.transaction_id=txid_current()
               ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'new unprovenanced operational prices are not authorized';
        END IF;
        RAISE EXCEPTION 'grandfathered unprovenanced price evidence is immutable';
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'batch price provenance cannot be attached or rewritten';
    END IF;
    SELECT * INTO b FROM price_book_batches
     WHERE price_book_batch_id=NEW.source_price_book_batch_id;
    SELECT vendor_id INTO offer_vendor FROM supplier_offers WHERE offer_id=NEW.offer_id;
    IF b.vendor_id IS NULL OR offer_vendor IS DISTINCT FROM b.vendor_id
       OR NEW.verified IS NOT TRUE THEN
        RAISE EXCEPTION 'operational price does not match batch vendor/provenance';
    END IF;
    IF NEW.price_state='future' THEN
        IF b.status <> 'VALIDATED'
           OR NOT EXISTS (
                SELECT 1 FROM price_book_promotion_events e
                 WHERE e.price_book_batch_id=b.price_book_batch_id
                   AND e.transaction_id=txid_current()
           ) THEN
            RAISE EXCEPTION 'FUTURE price insert lacks promotion authority';
        END IF;
    ELSIF NEW.price_state='current' THEN
        RAISE EXCEPTION 'CURRENT price transition is not implemented or authorized';
    ELSE
        RAISE EXCEPTION 'batch-sourced price state is invalid';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM price_book_staging_rows s
        JOIN price_book_batches pb USING(price_book_batch_id)
        JOIN supplier_offers o ON o.offer_id=s.offer_id
        JOIN vendors v ON v.vendor_id=o.vendor_id
         WHERE s.price_book_batch_id=NEW.source_price_book_batch_id
           AND s.source_row_number=NEW.source_price_book_row_number
           AND s.validation_status='VALID'
           AND s.offer_id IS NOT DISTINCT FROM NEW.offer_id
           AND date_trunc('month',pb.effective_from)::date
               IS NOT DISTINCT FROM NEW.effective_month
           AND s.level_type IS NOT DISTINCT FROM NEW.level_type
           AND s.break_quantity IS NOT DISTINCT FROM NEW.break_qty
           AND s.break_unit IS NOT DISTINCT FROM NEW.break_unit
           AND s.case_price IS NOT DISTINCT FROM NEW.case_price
           AND s.unit_price IS NOT DISTINCT FROM NEW.unit_price
           AND s.source_file IS NOT DISTINCT FROM NEW.source_file
           AND s.source_page IS NOT DISTINCT FROM NEW.source_page
           AND s.extraction_confidence IS NOT DISTINCT FROM NEW.extraction_confidence
           AND s.review_note IS NOT DISTINCT FROM NEW.notes
           AND pb.effective_from IS NOT DISTINCT FROM NEW.effective_from
           AND pb.effective_through IS NOT DISTINCT FROM NEW.effective_through
           AND pb.vendor_id=o.vendor_id
           AND v.vendor_name IS NOT DISTINCT FROM pb.vendor_name
           AND o.active AND o.confidence='VERIFIED' AND v.active
           AND is_procurement_eligible_variant(o.variant_id)
           AND s.vendor_name IS NOT DISTINCT FROM pb.vendor_name
           AND s.supplier_sku IS NOT DISTINCT FROM o.supplier_sku
           AND s.canonical_variant_id IS NOT DISTINCT FROM o.variant_id
           AND upper(s.package_type) IS NOT DISTINCT FROM upper(o.package_type)
           AND regexp_replace(btrim(s.size_text),'\\s+',' ','g')
               IS NOT DISTINCT FROM regexp_replace(btrim(o.size_text),'\\s+',' ','g')
           AND regexp_replace(btrim(s.raw_pack),'\\s+',' ','g')
               IS NOT DISTINCT FROM regexp_replace(btrim(o.raw_pack),'\\s+',' ','g')
           AND s.shopify_units_per_case IS NOT DISTINCT FROM o.shopify_units_per_case
           AND s.qualifying_units_per_case IS NOT DISTINCT FROM o.qualifying_units_per_case
           AND s.assortment_scope IS NOT DISTINCT FROM o.assortment_scope
           AND s.assortment_group IS NOT DISTINCT FROM o.assortment_group
           AND s.assortable IS NOT DISTINCT FROM o.assortable
    ) THEN
        RAISE EXCEPTION 'promoted price differs from validated staging evidence';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_validate_price_book_price_provenance ON prices;
CREATE TRIGGER trg_validate_price_book_price_provenance
BEFORE INSERT OR UPDATE OF offer_id,price_state,effective_month,level_type,
    break_qty,break_unit,case_price,unit_price,source_file,source_page,
    extraction_confidence,verified,notes,source_price_book_batch_id,
    source_price_book_row_number,effective_from,effective_through ON prices
FOR EACH ROW EXECUTE FUNCTION validate_price_book_price_provenance();

CREATE OR REPLACE FUNCTION protect_promoted_price()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_vendor UUID;
BEGIN
    IF OLD.source_price_book_batch_id IS NULL THEN
        IF TG_OP='DELETE'
           AND OLD.price_state='current'
           AND OLD.effective_month='2026-08-01'::date
           AND EXISTS (
                SELECT 1 FROM supplier_offers o
                 WHERE o.offer_id=OLD.offer_id
                   AND o.notes LIKE 'v0.1 seed migration:%'
           )
           AND EXISTS (
                SELECT 1 FROM legacy_price_seed_events e
                 WHERE e.transaction_id=txid_current()
           ) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'grandfathered unprovenanced price evidence is immutable';
    END IF;
    SELECT vendor_id INTO old_vendor FROM supplier_offers WHERE offer_id=OLD.offer_id;
    IF TG_OP='DELETE' AND OLD.price_state='future' AND EXISTS (
        SELECT 1 FROM price_book_promotion_events e
        JOIN price_book_batches b USING(price_book_batch_id)
         WHERE e.transaction_id=txid_current()
           AND b.vendor_id=old_vendor AND b.target_price_state='future'
           AND b.future_predecessor_batch_id=OLD.source_price_book_batch_id
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'promoted price economics are immutable outside authorized FUTURE replacement';
END
$$;
DROP TRIGGER IF EXISTS trg_protect_promoted_price ON prices;
CREATE TRIGGER trg_protect_promoted_price
BEFORE UPDATE OR DELETE ON prices
FOR EACH ROW EXECUTE FUNCTION protect_promoted_price();

CREATE OR REPLACE FUNCTION protect_promoted_offer_contract()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM prices p
         WHERE p.offer_id=OLD.offer_id
    ) AND (
        TG_OP='DELETE'
        OR ROW(NEW.variant_id,NEW.vendor_id,NEW.supplier_sku,NEW.package_type,
               NEW.size_text,NEW.raw_pack,NEW.shopify_units_per_case,
               NEW.qualifying_units_per_case,NEW.assortment_scope,
               NEW.assortment_group,NEW.assortable,NEW.active,NEW.confidence)
           IS DISTINCT FROM
           ROW(OLD.variant_id,OLD.vendor_id,OLD.supplier_sku,OLD.package_type,
               OLD.size_text,OLD.raw_pack,OLD.shopify_units_per_case,
               OLD.qualifying_units_per_case,OLD.assortment_scope,
               OLD.assortment_group,OLD.assortable,OLD.active,OLD.confidence)
    ) THEN
        RAISE EXCEPTION 'active promoted pricing freezes its supplier offer contract';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_protect_promoted_offer_contract ON supplier_offers;
CREATE TRIGGER trg_protect_promoted_offer_contract
BEFORE UPDATE OR DELETE ON supplier_offers
FOR EACH ROW EXECUTE FUNCTION protect_promoted_offer_contract();

CREATE OR REPLACE FUNCTION protect_priced_vendor_contract()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM supplier_offers o JOIN prices p ON p.offer_id=o.offer_id
         WHERE o.vendor_id=OLD.vendor_id
    ) AND (
        TG_OP='DELETE'
        OR ROW(NEW.vendor_name,NEW.active)
           IS DISTINCT FROM ROW(OLD.vendor_name,OLD.active)
    ) THEN
        RAISE EXCEPTION 'operational pricing freezes its vendor identity and active state';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_protect_priced_vendor_contract ON vendors;
CREATE TRIGGER trg_protect_priced_vendor_contract
BEFORE UPDATE OR DELETE ON vendors
FOR EACH ROW EXECUTE FUNCTION protect_priced_vendor_contract();

CREATE OR REPLACE VIEW v_verified_current_prices AS
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,
       o.shopify_units_per_case,o.qualifying_units_per_case,
       o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
JOIN vendors v ON v.vendor_id=o.vendor_id
WHERE p.price_state='current' AND p.verified
  AND p.source_price_book_batch_id IS NULL
  AND o.active AND o.confidence='VERIFIED' AND v.active
  AND is_procurement_eligible_variant(o.variant_id);

CREATE OR REPLACE VIEW v_verified_future_prices AS
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,
       o.shopify_units_per_case,o.qualifying_units_per_case,
       o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
JOIN vendors v ON v.vendor_id=o.vendor_id
JOIN price_book_batches b ON b.price_book_batch_id=p.source_price_book_batch_id
WHERE p.price_state='future' AND p.verified
  AND b.status='VERIFIED_FUTURE' AND b.vendor_id=o.vendor_id
  AND date_trunc('month',p.effective_month)::date =
      (date_trunc('month',(clock_timestamp() AT TIME ZONE 'America/New_York'))
       + interval '1 month')::date
  AND o.active AND o.confidence='VERIFIED' AND v.active
  AND is_procurement_eligible_variant(o.variant_id);

-- Preserve the historical column contract while tightening every consumer to
-- verified mappings/vendors/eligible identities. Accepted verified legacy
-- CURRENT rows remain available; unverified rows never become trusted.
CREATE OR REPLACE VIEW v_current_prices AS
SELECT p.price_id,p.offer_id,p.price_state,p.effective_month,p.level_type,
       p.break_qty,p.break_unit,p.case_price,p.unit_price,p.source_file,
       p.source_page,p.extraction_confidence,p.verified,p.notes,p.created_at,
       o.variant_id,o.vendor_id,o.supplier_sku,o.shopify_units_per_case,
       o.qualifying_units_per_case,o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
JOIN vendors v ON v.vendor_id=o.vendor_id
WHERE p.price_state='current' AND p.verified
  AND p.source_price_book_batch_id IS NULL
  AND o.active AND o.confidence='VERIFIED' AND v.active
  AND is_procurement_eligible_variant(o.variant_id);

CREATE OR REPLACE VIEW v_future_prices AS
SELECT p.price_id,p.offer_id,p.price_state,p.effective_month,p.level_type,
       p.break_qty,p.break_unit,p.case_price,p.unit_price,p.source_file,
       p.source_page,p.extraction_confidence,p.verified,p.notes,p.created_at,
       o.variant_id,o.vendor_id,o.supplier_sku,o.shopify_units_per_case,
       o.qualifying_units_per_case,o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
JOIN vendors v ON v.vendor_id=o.vendor_id
JOIN price_book_batches b ON b.price_book_batch_id=p.source_price_book_batch_id
WHERE p.price_state='future' AND p.verified
  AND b.status='VERIFIED_FUTURE' AND b.vendor_id=o.vendor_id
  AND date_trunc('month',p.effective_month)::date =
      (date_trunc('month',(clock_timestamp() AT TIME ZONE 'America/New_York'))
       + interval '1 month')::date
  AND o.active AND o.confidence='VERIFIED' AND v.active
  AND is_procurement_eligible_variant(o.variant_id);

INSERT INTO meta(key,value)
VALUES ('monday_price_book_contract','v2-future-only')
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now();

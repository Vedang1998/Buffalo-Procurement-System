-- Monday MVP Packet 2: confirmed, typed vendor operating rules.
-- Seed/default columns on vendors remain historical evidence and are not treated
-- as owner-confirmed operating truth.

CREATE TABLE IF NOT EXISTS vendor_operating_rules (
    vendor_id UUID PRIMARY KEY REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    order_days TEXT[] NOT NULL CHECK (cardinality(order_days) > 0),
    order_cutoff_local TIME NOT NULL,
    timezone_name TEXT NOT NULL CHECK (btrim(timezone_name) <> ''),
    expected_delivery_days TEXT[] NOT NULL
        CHECK (cardinality(expected_delivery_days) > 0),
    order_cycle_days INTEGER NOT NULL CHECK (order_cycle_days >= 1),
    lead_time_days INTEGER NOT NULL CHECK (lead_time_days >= 0),
    lead_time_variability_days NUMERIC(8,2) NOT NULL
        CHECK (lead_time_variability_days >= 0),
    reliability_pct NUMERIC(7,6) NOT NULL
        CHECK (reliability_pct >= 0 AND reliability_pct <= 1),
    minimum_type TEXT NOT NULL
        CHECK (minimum_type IN ('NONE','CASE','DOLLAR')),
    minimum_value NUMERIC(14,2),
    below_minimum_fee NUMERIC(14,2) NOT NULL
        CHECK (below_minimum_fee >= 0),
    loose_order_allowed BOOLEAN NOT NULL,
    loose_unit_fee NUMERIC(14,2),
    special_rules TEXT,
    holiday_blackout_notes TEXT,
    confirmation_source TEXT NOT NULL CHECK (btrim(confirmation_source) <> ''),
    confirmed_by TEXT NOT NULL CHECK (btrim(confirmed_by) <> ''),
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rules_version INTEGER NOT NULL CHECK (rules_version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_vendor_rule_minimum_value CHECK (
        (minimum_type='NONE' AND minimum_value IS NULL)
        OR (minimum_type IN ('CASE','DOLLAR') AND minimum_value > 0)
    ),
    CONSTRAINT ck_vendor_rule_loose_fee CHECK (
        (loose_order_allowed=FALSE AND (loose_unit_fee IS NULL OR loose_unit_fee >= 0))
        OR (loose_order_allowed=TRUE AND loose_unit_fee IS NOT NULL AND loose_unit_fee >= 0)
    )
);

CREATE TABLE IF NOT EXISTS vendor_rule_revisions (
    vendor_rule_revision_id BIGSERIAL PRIMARY KEY,
    vendor_id UUID NOT NULL REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    rules_version INTEGER NOT NULL CHECK (rules_version >= 1),
    before_json JSONB,
    after_json JSONB NOT NULL,
    changed_by TEXT NOT NULL CHECK (btrim(changed_by) <> ''),
    change_reason TEXT NOT NULL CHECK (btrim(change_reason) <> ''),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vendor_id, rules_version)
);

CREATE INDEX IF NOT EXISTS idx_vendor_rule_revisions_vendor
    ON vendor_rule_revisions(vendor_id, rules_version DESC);

INSERT INTO meta(key, value)
VALUES ('monday_vendor_rules_contract', 'v1')
ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

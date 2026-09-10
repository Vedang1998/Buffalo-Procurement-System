# First Persistent Mapping Foundation — Construction-Ready Implementation Specification

Status: **PROPOSED / DESIGN ONLY / NOT AUTHORIZED FOR MIGRATION EXECUTION**

Prepared: 2026-09-10

Preserved parent: `codex/supplier-mapping-review-policy-followup` at
`a056e111e2f21b96a9452be9a559e10f03805a6f`, tree
`22552b1460a360823b3f35558a995855af887596`

Design branch: `codex/persistent-mapping-foundation-design`

This specification turns Step 1 of the reviewed persistent-multi-offer design
into one construction-ready package. It does not replace
`2026-09-08-persistent-multi-offer-mapping-authority-design.md`. That reviewed
document continues to control the later pricing, carry-forward, deal-overlay,
policy-execution, recommendation-cutover, and Shopify work.

No SQL below is in `procurement/db`, no migration number is assigned, and no
runtime/configuration/authority byte is changed by this design branch. The
sealed V5 package remains immutable `REVIEW ONLY / NOT_APPROVED /
NOT_IMPORT_READY` evidence.

## 1. Exact integration dependency and target

### Verified refs and current gap

| Ref | Verified commit/tree | Meaning |
|---|---|---|
| Preserved source branch, local/upstream/live remote | `a056e111e2f21b96a9452be9a559e10f03805a6f` / `22552b1460a360823b3f35558a995855af887596` | Source of this design package; it remains untouched |
| Live `origin/main` | `f308ac666a2377f540e528bc873463daecc20cf8` / `0a8a2ea80721a97858c2120545d1e6b6f3805247` | Current proposed integration base; the local branch named `main` is divergent and must not be used |
| Monday foundation review head | `88bf800` | Reviewed unmerged foundation through migrations 008–013 |
| PR #23 integration candidate | `ec71fe9c5a6f13832a8cad65b065be9747010486` / `543be91aee06386b7889a1fc4198eaafe89e74df` | Merge of `88bf800` with `f308ac6`; draft and not yet acceptable as an implementation base |

The merge base of `origin/main` and the preserved design lineage is
`1920a16a6dc13a1b4357315f5049b938cbe7c0e2`. Main has three unique commits and
the design lineage has 39. Therefore neither this document nor the newest
reader/follow-up commits are standalone cherry-picks onto main.

PR #23 is open and draft. Its current public metadata reports 14 commits, 65
files, `+20,781/-146`, `mergeable=true`, `rebaseable=false`, and
`mergeable_state=unstable`; its body still describes the older `88bf800`
source and pre-integration counts. Procurement CI run `34185466802` completed
with failure: startup passed, the full-suite job exited 1 after 959 seconds,
and the public metadata does not expose the failure cause. Detailed logs were
previously admin-only (HTTP 403), and authenticated `gh` metadata is not
available in this environment. Those access failures were not repeated.

A three-way tree analysis predicts a conflict in `docs/CODEX_HANDOFF.md` when
joining the preserved lineage to either current main or the PR #23 candidate.
Files changed on both sides including `procurement/tools/run_tests.py` appear
auto-mergeable in that model, but this is not evidence that 39 commits are
independently cherry-pickable or behaviorally compatible.

### One recommended future integration sequence

1. Make the Monday foundation an approved integration baseline. Refresh PR #23
   or create a separately authorized integration branch from the then-current
   approved main; deliberately preserve both handoff histories; diagnose the
   unexplained CI failure; and require green CI on the exact integrated tree.
   `ec71fe9` is the currently constructed candidate, not an acceptable
   implementation target while it remains draft, unstable, and non-green.
2. After that foundation lands, integrate and independently validate the
   dependent reader lineage in reviewed order:
   - offline bridge `fa594b6..2a7192f` (including fail-closed hardening
     `bc160a7` and reviewed implementation `ecc1835`);
   - V5 reader `48f5b35..6528bc6` (review checkpoint `bb0aaf3`);
   - A1 adapter `7e57301..9ef51a2`;
   - policy/follow-up and test closure `db39429..a056e11`.
   Resolve the handoff conflict intentionally, re-register exact test floors,
   and run the full suite on the integrated bytes.
3. Layer this design-package commit on that approved integrated target. Only
   then create a fresh implementation branch for the schema/domain-service
   slice below. Recheck the migration chain, assign the next number at that
   time, and obtain owner authorization before writing runtime SQL.

This sequence preserves the reviewed contract boundaries and avoids disguising
the unmerged foundation as a small mapping PR. No merge, rebase, cherry-pick,
PR mutation, or CI retry is part of this task.

## 2. First-slice object and lifecycle contract

### Four different facts

1. A **printed source occurrence** is one immutable candidate row with exact
   artifact hash, source table/file, page bounds, row/occurrence key,
   qualifiers, component relationships, owner clarifications, and historical
   capture scope. Repeated occurrences remain repeated evidence.
2. An **operational supplier offer** is the existing `supplier_offers` identity
   used by procurement. Its identity contract is Variant, vendor, exact
   supplier-code bytes, offer/package class, size/pack, conversion quantities,
   and assortment contract. Price tiers and printed occurrences are not new
   offer identities.
3. A **mapping decision** is an immutable approval, rejection, or deferral for
   one candidate and one exact decision scope. It may link several equivalent
   occurrences to the same offer. It cannot select, price, sync, or order.
4. A **selected routine offer** is the latest append-only selection event named
   by the one mutable `ROUTINE_PROCUREMENT_STANDARD` head for a Variant. It is a
   separate confirmation even when the same authenticated owner made the
   mapping decision.

`operational_offer_key_sha256` is calculated over the vendor, stable raw
distributor-product identity, exact supplier-code bytes, Variant, offer class,
package/size/pack/conversion and component identity. It deliberately excludes
page, occurrence ordinal, price, tier, and deal terms. The application takes a
transaction advisory lock on this key and reuses an exact-contract offer
already linked by an effective approval. It refuses a second offer ID for the
same key. Different regular/gift/special/alternate/component/combo identities
produce different keys and remain separately queryable.

A supplier code reused for a different product or contract never rewrites the
old offer. Human review may create a new **inactive** historical offer while
preserving the old identity and references. The existing partial unique index
`uq_active_vendor_supplier_sku` remains authoritative: two rows with the same
nonblank vendor/code may coexist only while at most one is active. The existing
PO-reference trigger from migration 010 and priced-offer/vendor protections
from migration 011 remain in force. The new mapping protection is additive.

### Exact first-slice lifecycle

| Step | Permitted result | Explicitly not implied |
|---|---|---|
| Intake | Immutable batch and candidates, including explicit nulls and absent fields | Approval, offer creation, selection, price, Shopify or order authority |
| `APPROVE_MAPPING` | Link an exact existing offer, or create one inactive exact-contract `supplier_offers` row in the same transaction | Offer activation, price creation/verification, routine selection, recommendation use |
| `REJECT_MAPPING` | Link a new active `mapping_rejections` row with exact negative evidence | Deleting a candidate or alternative |
| `DEFER` | Append reason/evidence only | Approval by default |
| `SELECT` | Advance the Variant-wide head to an effective approved regular offer after a second preview and confirmation | Changing `supplier_offers.active`, creating prices, or changing recommendations |
| `CLEAR` | Advance the head to an explicit reviewed null | Deleting selection history or deactivating an offer |

An active exact legacy offer may be selected without changing its active state.
A newly approved inactive offer may also be selected, but the read model labels
it `SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION`. First-slice code must not
activate it. A later, separately authorized activation/cutover transaction must
revalidate active vendor/code uniqueness, protected references, mapping and
selection heads, price eligibility, and recommendation shadow parity before it
can change operational behavior. This is the defined transition; mapping and
selection do not activate pricing.

Accordingly, the current recommendation consumer in `recommendations._load_context`
continues to require exactly one active `STANDARD` offer. It does not query the
new views in this slice. Existing recommendations remain byte-for-byte and
behaviorally unchanged until a separately approved cutover.

## 3. Proposed migration identity and preconditions

The artifact below is named
`PROPOSED_UNNUMBERED_persistent_mapping_foundation.sql` here only. Under the
currently intended integrated chain its exact predecessor is
`013_monday_p1_remediation.sql`. Do not copy it into `procurement/db`, add it to
`MIGRATION_ORDER`, or assign a number until all of the following are true:

- the exact integrated target is approved and clean;
- `schema_postgres.sql` through `013_monday_p1_remediation.sql` are the complete
  predecessor chain with no intervening migration;
- `monday_price_book_contract='v2-future-only'` and
  `monday_p1_remediation_contract='v1'` survive integration;
- the active vendor/SKU partial unique index and migration 010/011 protection
  triggers exist and are valid;
- there are no partial objects from this contract;
- a real historical upgrade fixture has been built from the exact predecessor,
  not from a consolidated schema containing the new objects.

If another migration lands after 013 first, stop, reassess dependencies, rename
this migration to the next available number, and update the expected marker set
and tests. Do not renumber an already published migration.

`apply_schema.py` already wraps each file and its migration-marker insertion in
one database transaction. Therefore the migration file must contain no
`BEGIN`, `COMMIT`, or autonomous transaction. The application operations below
each require their own explicit `SERIALIZABLE` transaction.

## 4. Exact proposed SQL

The following is the reviewable SQL proposed for the future migration. It is
intentionally outside the automatically discovered migration directory.

```sql
-- PROPOSED_UNNUMBERED_persistent_mapping_foundation.sql
-- Exact predecessor under the reviewed chain: 013_monday_p1_remediation.sql.
-- apply_schema.py supplies the enclosing transaction and migration marker.

SELECT pg_advisory_xact_lock(
    hashtextextended('buffalo:persistent-mapping-foundation:v1', 0)
);

DO $migration_preconditions$
DECLARE
    actual_markers TEXT[];
    expected_markers CONSTANT TEXT[] := ARRAY[
        'migration:001_v1_3_catalog_sales.sql',
        'migration:002_seed_import_records.sql',
        'migration:003_phase3_reconciliation.sql',
        'migration:004_identity_decision_invariants.sql',
        'migration:005_identity_investigation.sql',
        'migration:006_phase4_sales_backfill.sql',
        'migration:007_phase4_terminal_disposition.sql',
        'migration:008_monday_inventory_foundation.sql',
        'migration:009_monday_vendor_rules.sql',
        'migration:010_monday_po_ledger.sql',
        'migration:011_monday_price_book_staging.sql',
        'migration:012_monday_review_draft_packet.sql',
        'migration:013_monday_p1_remediation.sql',
        'migration:schema_postgres.sql'
    ];
    installed_contract TEXT;
    initial_install BOOLEAN;
BEGIN
    SELECT value INTO installed_contract
      FROM meta WHERE key='persistent_mapping_foundation_contract';

    initial_install := installed_contract IS NULL;
    PERFORM set_config(
        'procurement.persistent_mapping_foundation_initial_install',
        CASE WHEN initial_install THEN 'true' ELSE 'false' END,
        true
    );

    IF initial_install THEN
        SELECT array_agg(key ORDER BY key) INTO actual_markers
          FROM meta WHERE key LIKE 'migration:%';
        IF actual_markers IS DISTINCT FROM expected_markers THEN
            RAISE EXCEPTION
                'persistent mapping foundation requires exact predecessor 013; markers=%',
                actual_markers;
        END IF;
        IF (SELECT value FROM meta WHERE key='monday_price_book_contract')
               IS DISTINCT FROM 'v2-future-only'
           OR (SELECT value FROM meta WHERE key='monday_p1_remediation_contract')
               IS DISTINCT FROM 'v1' THEN
            RAISE EXCEPTION 'required Monday predecessor contracts differ';
        END IF;
        IF to_regclass('public.supplier_mapping_review_batches') IS NOT NULL
           OR to_regclass('public.supplier_mapping_review_candidates') IS NOT NULL
           OR to_regclass('public.supplier_mapping_decisions') IS NOT NULL
           OR to_regclass('public.supplier_offer_selection_events') IS NOT NULL
           OR to_regclass('public.supplier_offer_selection_heads') IS NOT NULL
           OR to_regclass('public.v_selected_standard_supplier_offers') IS NOT NULL
           OR to_regclass('public.v_supplier_offer_selection_shadow') IS NOT NULL
           OR to_regprocedure('persistent_mapping_text_sha256(text)') IS NOT NULL
           OR to_regprocedure('supplier_mapping_policy_is_published(text,text,text,text)')
                IS NOT NULL THEN
            RAISE EXCEPTION 'partial persistent mapping objects exist without contract marker';
        END IF;
    ELSIF installed_contract IS DISTINCT FROM 'v1-shadow-only' THEN
        RAISE EXCEPTION 'persistent mapping foundation contract version differs: %',
            installed_contract;
    ELSIF to_regclass('public.supplier_mapping_review_batches') IS NULL
       OR to_regclass('public.supplier_mapping_review_candidates') IS NULL
       OR to_regclass('public.supplier_mapping_decisions') IS NULL
       OR to_regclass('public.supplier_offer_selection_events') IS NULL
       OR to_regclass('public.supplier_offer_selection_heads') IS NULL
       OR to_regclass('public.v_selected_standard_supplier_offers') IS NULL
       OR to_regclass('public.v_supplier_offer_selection_shadow') IS NULL THEN
        RAISE EXCEPTION 'installed persistent mapping contract is structurally incomplete';
    END IF;

    IF to_regclass('public.variants') IS NULL
       OR to_regclass('public.vendors') IS NULL
       OR to_regclass('public.supplier_offers') IS NULL
       OR to_regclass('public.mapping_rejections') IS NULL
       OR to_regclass('public.prices') IS NULL
       OR to_regclass('public.purchase_order_lines') IS NULL
       OR to_regclass('public.procurement_recommendations') IS NULL
       OR to_regclass('public.run_price_snapshots') IS NULL
       OR to_regprocedure('is_procurement_eligible_variant(text)') IS NULL
       OR to_regprocedure('prevent_referenced_offer_identity_change()') IS NULL
       OR to_regprocedure('protect_promoted_offer_contract()') IS NULL THEN
        RAISE EXCEPTION 'required catalog, offer, pricing, or reference contract is absent';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid=i.indexrelid
         WHERE c.relname='uq_active_vendor_supplier_sku'
           AND i.indisunique AND i.indisvalid AND i.indpred IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'active vendor/supplier-code uniqueness contract is absent';
    END IF;
END
$migration_preconditions$;

CREATE OR REPLACE FUNCTION persistent_mapping_text_sha256(value TEXT)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$ SELECT encode(digest(convert_to(value, 'UTF8'), 'sha256'), 'hex') $$;

CREATE OR REPLACE FUNCTION persistent_mapping_json_sha256(value JSONB)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$ SELECT persistent_mapping_text_sha256(value::text) $$;

CREATE TABLE IF NOT EXISTS supplier_mapping_review_batches (
    review_batch_id UUID PRIMARY KEY,
    intake_idempotency_key UUID NOT NULL UNIQUE,
    source_package_kind TEXT NOT NULL CHECK (source_package_kind='SEALED_V5_REVIEW_PACKAGE'),
    source_package_id TEXT NOT NULL CHECK (btrim(source_package_id)<>''),
    source_revision TEXT NOT NULL CHECK (btrim(source_revision)<>''),
    source_artifact_ref TEXT NOT NULL CHECK (btrim(source_artifact_ref)<>''),
    source_artifact_sha256 TEXT NOT NULL CHECK (source_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    source_root_sha256 TEXT NOT NULL CHECK (source_root_sha256 ~ '^[0-9a-f]{64}$'),
    source_seal_sha256 TEXT NOT NULL CHECK (source_seal_sha256 ~ '^[0-9a-f]{64}$'),
    relationship_table_sha256 TEXT NOT NULL CHECK (relationship_table_sha256 ~ '^[0-9a-f]{64}$'),
    source_batch_sha256 TEXT NOT NULL CHECK (source_batch_sha256 ~ '^[0-9a-f]{64}$'),
    source_payload_sha256 TEXT NOT NULL CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_set_sha256 TEXT NOT NULL CHECK (candidate_set_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_count INTEGER NOT NULL CHECK (candidate_count>=0),
    supplier_period_scope JSONB NOT NULL CHECK (jsonb_typeof(supplier_period_scope)='object'),
    prerequisites JSONB NOT NULL CHECK (jsonb_typeof(prerequisites)='object'),
    structural_state TEXT NOT NULL CHECK (structural_state IN ('READY','BLOCKED')),
    source_evidence_state TEXT NOT NULL CHECK (source_evidence_state IN ('READY','BLOCKED')),
    semantic_state TEXT NOT NULL CHECK (semantic_state IN ('READY','BLOCKED')),
    source_authority_state TEXT NOT NULL DEFAULT 'NOT_APPROVED'
        CHECK (source_authority_state='NOT_APPROVED'),
    source_import_state TEXT NOT NULL DEFAULT 'NOT_IMPORT_READY'
        CHECK (source_import_state='NOT_IMPORT_READY'),
    source_is_simulation BOOLEAN NOT NULL DEFAULT FALSE,
    creator_principal_ref TEXT NOT NULL CHECK (btrim(creator_principal_ref)<>''),
    creator_role_ref TEXT NOT NULL CHECK (btrim(creator_role_ref)<>''),
    creator_authn_context_sha256 TEXT NOT NULL
        CHECK (creator_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_txid BIGINT NOT NULL DEFAULT txid_current()
);

CREATE TABLE IF NOT EXISTS supplier_mapping_review_candidates (
    review_batch_id UUID NOT NULL
        REFERENCES supplier_mapping_review_batches(review_batch_id) ON DELETE RESTRICT,
    candidate_id UUID NOT NULL,
    occurrence_index INTEGER NOT NULL CHECK (occurrence_index>=1),
    occurrence_key TEXT NOT NULL CHECK (btrim(occurrence_key)<>''),
    printed_occurrence_sha256 TEXT NOT NULL
        CHECK (printed_occurrence_sha256 ~ '^[0-9a-f]{64}$'),
    supplier_identity_key_sha256 TEXT NOT NULL
        CHECK (supplier_identity_key_sha256 ~ '^[0-9a-f]{64}$'),
    operational_offer_key_sha256 TEXT NOT NULL
        CHECK (operational_offer_key_sha256 ~ '^[0-9a-f]{64}$'),
    decision_scope_sha256 TEXT NOT NULL
        CHECK (decision_scope_sha256 ~ '^[0-9a-f]{64}$'),
    source_table_name TEXT NOT NULL CHECK (btrim(source_table_name)<>''),
    source_row_key TEXT NOT NULL CHECK (btrim(source_row_key)<>''),
    source_file_name TEXT NOT NULL CHECK (btrim(source_file_name)<>''),
    source_file_sha256 TEXT NOT NULL CHECK (source_file_sha256 ~ '^[0-9a-f]{64}$'),
    source_page_start INTEGER CHECK (source_page_start IS NULL OR source_page_start>=1),
    source_page_end INTEGER CHECK (source_page_end IS NULL OR source_page_end>=1),
    source_locator JSONB NOT NULL CHECK (jsonb_typeof(source_locator)='object'),
    proposed_variant_id TEXT,
    proposed_vendor_id UUID,
    source_vendor_identity TEXT NOT NULL CHECK (btrim(source_vendor_identity)<>''),
    distributor_product_id_state TEXT NOT NULL
        CHECK (distributor_product_id_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    distributor_product_id_value TEXT,
    supplier_code_state TEXT NOT NULL
        CHECK (supplier_code_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    supplier_code_value TEXT,
    offer_class TEXT NOT NULL CHECK (offer_class IN
        ('REGULAR','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    occurrence_role TEXT NOT NULL CHECK (occurrence_role IN
        ('PRIMARY','TIER','REPEAT','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    package_type_state TEXT NOT NULL
        CHECK (package_type_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    package_type_value TEXT,
    size_state TEXT NOT NULL CHECK (size_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    size_value TEXT,
    raw_pack_state TEXT NOT NULL CHECK (raw_pack_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    raw_pack_value TEXT,
    physical_units_state TEXT NOT NULL
        CHECK (physical_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    physical_units_value NUMERIC(12,4),
    retail_pack_units_state TEXT NOT NULL
        CHECK (retail_pack_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    retail_pack_units_value NUMERIC(12,4),
    shopify_units_state TEXT NOT NULL
        CHECK (shopify_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    shopify_units_value NUMERIC(12,4),
    qualifying_units_state TEXT NOT NULL
        CHECK (qualifying_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    qualifying_units_value NUMERIC(12,4),
    assortment_scope_state TEXT NOT NULL
        CHECK (assortment_scope_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_scope_value TEXT,
    assortment_group_state TEXT NOT NULL
        CHECK (assortment_group_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_group_value TEXT,
    assortable_state TEXT NOT NULL
        CHECK (assortable_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortable_value BOOLEAN,
    identity_qualifiers JSONB NOT NULL CHECK (jsonb_typeof(identity_qualifiers)='object'),
    component_relationships JSONB NOT NULL CHECK (jsonb_typeof(component_relationships)='array'),
    independent_linkage_evidence JSONB NOT NULL
        CHECK (jsonb_typeof(independent_linkage_evidence)='array'),
    owner_clarifications JSONB NOT NULL CHECK (jsonb_typeof(owner_clarifications)='array'),
    historical_capture_scope JSONB NOT NULL CHECK (jsonb_typeof(historical_capture_scope)='object'),
    related_artifact_hashes JSONB NOT NULL CHECK (jsonb_typeof(related_artifact_hashes)='object'),
    blockers JSONB NOT NULL CHECK (jsonb_typeof(blockers)='array'),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    candidate_sha256 TEXT NOT NULL CHECK (candidate_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY (candidate_id),
    UNIQUE (review_batch_id,candidate_id),
    UNIQUE (review_batch_id,occurrence_index),
    UNIQUE (review_batch_id,occurrence_key),
    CHECK ((source_page_start IS NULL)=(source_page_end IS NULL)),
    CHECK (source_page_end IS NULL OR source_page_end>=source_page_start),
    CHECK ((distributor_product_id_state='VALUE')=(distributor_product_id_value IS NOT NULL)),
    CHECK ((supplier_code_state='VALUE')=(supplier_code_value IS NOT NULL)),
    CHECK ((package_type_state='VALUE')=(package_type_value IS NOT NULL)),
    CHECK ((size_state='VALUE')=(size_value IS NOT NULL)),
    CHECK ((raw_pack_state='VALUE')=(raw_pack_value IS NOT NULL)),
    CHECK ((physical_units_state='VALUE')=(physical_units_value IS NOT NULL)),
    CHECK ((retail_pack_units_state='VALUE')=(retail_pack_units_value IS NOT NULL)),
    CHECK ((shopify_units_state='VALUE')=(shopify_units_value IS NOT NULL)),
    CHECK ((qualifying_units_state='VALUE')=(qualifying_units_value IS NOT NULL)),
    CHECK ((assortment_scope_state='VALUE')=(assortment_scope_value IS NOT NULL)),
    CHECK (assortment_scope_value IS NULL OR
           assortment_scope_value IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')),
    CHECK ((assortment_group_state='VALUE')=(assortment_group_value IS NOT NULL)),
    CHECK ((assortable_state='VALUE')=(assortable_value IS NOT NULL)),
    CHECK (physical_units_value IS NULL OR physical_units_value>0),
    CHECK (retail_pack_units_value IS NULL OR retail_pack_units_value>0),
    CHECK (shopify_units_value IS NULL OR shopify_units_value>0),
    CHECK (qualifying_units_value IS NULL OR qualifying_units_value>0)
);

CREATE OR REPLACE FUNCTION persistent_mapping_candidate_supplier_identity_key(
    c supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','SUPPLIER_IDENTITY_V1',
        'proposed_vendor_id',c.proposed_vendor_id,
        'source_vendor_identity',c.source_vendor_identity,
        'distributor_product_id_state',c.distributor_product_id_state,
        'distributor_product_id_value',c.distributor_product_id_value,
        'supplier_code_state',c.supplier_code_state,
        'supplier_code_value',c.supplier_code_value
    ))
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_candidate_operational_offer_key(
    c supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','OPERATIONAL_OFFER_V1',
        'supplier_identity_key_sha256',c.supplier_identity_key_sha256,
        'proposed_variant_id',c.proposed_variant_id,
        'offer_class',c.offer_class,
        'package_type_state',c.package_type_state,
        'package_type_value',c.package_type_value,
        'size_state',c.size_state,'size_value',c.size_value,
        'raw_pack_state',c.raw_pack_state,'raw_pack_value',c.raw_pack_value,
        'physical_units_state',c.physical_units_state,
        'physical_units_value',c.physical_units_value,
        'retail_pack_units_state',c.retail_pack_units_state,
        'retail_pack_units_value',c.retail_pack_units_value,
        'shopify_units_state',c.shopify_units_state,
        'shopify_units_value',c.shopify_units_value,
        'qualifying_units_state',c.qualifying_units_state,
        'qualifying_units_value',c.qualifying_units_value,
        'assortment_scope_state',c.assortment_scope_state,
        'assortment_scope_value',c.assortment_scope_value,
        'assortment_group_state',c.assortment_group_state,
        'assortment_group_value',c.assortment_group_value,
        'assortable_state',c.assortable_state,
        'assortable_value',c.assortable_value,
        'identity_qualifiers',c.identity_qualifiers,
        'component_relationships',c.component_relationships
    ))
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_candidate_decision_scope(
    c supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','CANDIDATE_DECISION_SCOPE_V1',
        'review_batch_id',c.review_batch_id,
        'candidate_id',c.candidate_id
    ))
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_candidate_reviewed_facts(
    c supplier_mapping_review_candidates
)
RETURNS JSONB
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT jsonb_build_object(
        'package_type_state',c.package_type_state,
        'package_type_value',c.package_type_value,
        'physical_units_state',c.physical_units_state,
        'physical_units_value',c.physical_units_value,
        'retail_pack_units_state',c.retail_pack_units_state,
        'retail_pack_units_value',c.retail_pack_units_value,
        'assortment_scope_state',c.assortment_scope_state,
        'assortment_scope_value',c.assortment_scope_value,
        'assortment_group_state',c.assortment_group_state,
        'assortment_group_value',c.assortment_group_value,
        'assortable_state',c.assortable_state,
        'assortable_value',c.assortable_value,
        'identity_qualifiers',c.identity_qualifiers,
        'independent_linkage_evidence',c.independent_linkage_evidence,
        'related_artifact_hashes',c.related_artifact_hashes,
        'source_locator',c.source_locator,
        'source_file_name',c.source_file_name,
        'source_file_sha256',c.source_file_sha256,
        'source_page_start',c.source_page_start,
        'source_page_end',c.source_page_end
    )
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_candidate_evidence_set_sha256(
    c supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','INDEPENDENT_LINKAGE_EVIDENCE_V1',
        'printed_source_sha256',c.source_file_sha256,
        'evidence',c.independent_linkage_evidence
    ))
$$;

CREATE INDEX IF NOT EXISTS idx_mapping_candidates_offer_key
    ON supplier_mapping_review_candidates(operational_offer_key_sha256);
CREATE INDEX IF NOT EXISTS idx_mapping_candidates_supplier_identity
    ON supplier_mapping_review_candidates(supplier_identity_key_sha256);
CREATE INDEX IF NOT EXISTS idx_mapping_candidates_proposed_identity
    ON supplier_mapping_review_candidates(proposed_vendor_id,proposed_variant_id);
CREATE INDEX IF NOT EXISTS idx_mapping_candidates_supplier_code
    ON supplier_mapping_review_candidates(proposed_vendor_id,supplier_code_value)
    WHERE supplier_code_state='VALUE';

CREATE OR REPLACE FUNCTION persistent_mapping_catalog_fingerprint(wanted_variant_id TEXT)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
AS $$
    SELECT persistent_mapping_json_sha256(jsonb_build_object(
        'variant_id',v.variant_id,'shopify_gid',v.shopify_gid,
        'product_id',v.product_id,'product_gid',v.product_gid,
        'sku',v.sku,'barcode',v.barcode,'active',v.active,
        'identity_scope',v.identity_scope,'catalog_state',v.catalog_state,
        'source_snapshot',v.source_snapshot,'last_synced_at',v.last_synced_at
    )) FROM variants v WHERE v.variant_id=wanted_variant_id
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_vendor_fingerprint(wanted_vendor_id UUID)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
AS $$
    SELECT persistent_mapping_json_sha256(jsonb_build_object(
        'vendor_id',v.vendor_id,'vendor_name',v.vendor_name,'active',v.active,
        'order_day',v.order_day,'order_cycle_days',v.order_cycle_days,
        'lead_time_days',v.lead_time_days,'updated_at',v.updated_at
    )) FROM vendors v WHERE v.vendor_id=wanted_vendor_id
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_rejection_fingerprint(
    wanted_vendor_id UUID, wanted_source_key TEXT
)
RETURNS TEXT
LANGUAGE sql STABLE
AS $$
    SELECT persistent_mapping_json_sha256(COALESCE(jsonb_agg(
        jsonb_build_object(
            'rejection_id',r.rejection_id,'mapping_type',r.mapping_type,
            'source_key',r.source_key,'variant_id',r.rejected_variant_id,
            'vendor_id',r.vendor_id,'source_text',r.source_text,
            'evidence_json',r.evidence_json,'rejected_by',r.rejected_by,
            'rejected_at',r.rejected_at
        ) ORDER BY r.rejection_id
    ),'[]'::jsonb))
     FROM mapping_rejections r
     WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
       AND r.vendor_id IS NOT DISTINCT FROM wanted_vendor_id
       AND r.source_key=wanted_source_key
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_offer_fingerprint(wanted_offer_id BIGINT)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
AS $$
    SELECT persistent_mapping_json_sha256(jsonb_build_object(
        'offer_id',o.offer_id,'variant_id',o.variant_id,'vendor_id',o.vendor_id,
        'supplier_sku',o.supplier_sku,'package_type',o.package_type,
        'size_text',o.size_text,'raw_pack',o.raw_pack,
        'shopify_units_per_case',o.shopify_units_per_case,
        'qualifying_units_per_case',o.qualifying_units_per_case,
        'assortment_scope',o.assortment_scope,'assortment_group',o.assortment_group,
        'assortable',o.assortable,'valid_from',o.valid_from,'valid_to',o.valid_to,
        'active',o.active
    )) FROM supplier_offers o WHERE o.offer_id=wanted_offer_id
$$;

CREATE OR REPLACE FUNCTION persistent_mapping_require_human_context(
    expected_principal_ref TEXT,
    expected_role_ref TEXT,
    expected_authn_context_sha256 TEXT,
    expected_action TEXT
)
RETURNS VOID
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    IF current_setting('procurement.principal_kind',true) IS DISTINCT FROM 'HUMAN'
       OR current_setting('procurement.principal_ref',true)
            IS DISTINCT FROM expected_principal_ref
       OR current_setting('procurement.authorized_role_ref',true)
            IS DISTINCT FROM expected_role_ref
       OR current_setting('procurement.authn_context_sha256',true)
            IS DISTINCT FROM expected_authn_context_sha256
       OR current_setting('procurement.authorized_action',true)
            IS DISTINCT FROM expected_action THEN
        RAISE EXCEPTION 'verified named-human authorization context is absent or differs';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION supplier_mapping_policy_is_published(
    policy_ref TEXT,
    policy_version TEXT,
    publication_sha256 TEXT,
    evidence_set_sha256 TEXT
)
RETURNS BOOLEAN
LANGUAGE sql STABLE
AS $$ SELECT FALSE $$;

CREATE OR REPLACE FUNCTION reject_persistent_mapping_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only and cannot be %d',TG_TABLE_NAME,lower(TG_OP);
END
$$;

CREATE OR REPLACE FUNCTION validate_mapping_review_batch_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE expected_payload JSONB;
BEGIN
    IF NEW.created_txid<>txid_current() THEN
        RAISE EXCEPTION 'review batch transaction identity differs';
    END IF;
    PERFORM persistent_mapping_require_human_context(
        NEW.creator_principal_ref,NEW.creator_role_ref,
        NEW.creator_authn_context_sha256,'MAPPING_REVIEW_INTAKE'
    );
    expected_payload := to_jsonb(NEW)-ARRAY[
        'review_batch_id','intake_idempotency_key','canonical_payload',
        'payload_sha256','created_at','created_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'review batch canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION validate_mapping_review_candidate_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE parent_txid BIGINT; expected_payload JSONB;
BEGIN
    SELECT created_txid INTO parent_txid
      FROM supplier_mapping_review_batches
     WHERE review_batch_id=NEW.review_batch_id FOR SHARE;
    IF parent_txid IS DISTINCT FROM txid_current()
       OR NEW.created_txid<>txid_current() THEN
        RAISE EXCEPTION 'batch and candidates must be created in one transaction';
    END IF;
    IF NEW.supplier_identity_key_sha256 IS DISTINCT FROM
            persistent_mapping_candidate_supplier_identity_key(NEW)
       OR NEW.operational_offer_key_sha256 IS DISTINCT FROM
            persistent_mapping_candidate_operational_offer_key(NEW)
       OR NEW.decision_scope_sha256 IS DISTINCT FROM
            persistent_mapping_candidate_decision_scope(NEW) THEN
        RAISE EXCEPTION 'candidate identity, offer, or decision-scope key differs';
    END IF;
    expected_payload := to_jsonb(NEW)-ARRAY[
        'candidate_id','canonical_payload','candidate_sha256','created_at','created_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.candidate_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'candidate canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION validate_mapping_review_candidate_set()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE actual_count BIGINT; actual_sha256 TEXT;
BEGIN
    SELECT count(*),persistent_mapping_text_sha256(COALESCE(
        string_agg(candidate_sha256||E'\n','' ORDER BY occurrence_index,candidate_id),''
    )) INTO actual_count,actual_sha256
      FROM supplier_mapping_review_candidates
     WHERE review_batch_id=NEW.review_batch_id;
    IF actual_count<>NEW.candidate_count
       OR actual_sha256 IS DISTINCT FROM NEW.candidate_set_sha256 THEN
        RAISE EXCEPTION 'review batch candidate count or set fingerprint differs';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_mapping_review_batch_insert
    ON supplier_mapping_review_batches;
CREATE TRIGGER trg_validate_mapping_review_batch_insert
BEFORE INSERT ON supplier_mapping_review_batches
FOR EACH ROW EXECUTE FUNCTION validate_mapping_review_batch_insert();
DROP TRIGGER IF EXISTS trg_validate_mapping_review_candidate_insert
    ON supplier_mapping_review_candidates;
CREATE TRIGGER trg_validate_mapping_review_candidate_insert
BEFORE INSERT ON supplier_mapping_review_candidates
FOR EACH ROW EXECUTE FUNCTION validate_mapping_review_candidate_insert();
DROP TRIGGER IF EXISTS trg_validate_mapping_review_candidate_set
    ON supplier_mapping_review_batches;
CREATE CONSTRAINT TRIGGER trg_validate_mapping_review_candidate_set
AFTER INSERT ON supplier_mapping_review_batches DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION validate_mapping_review_candidate_set();

DROP TRIGGER IF EXISTS trg_immutable_mapping_review_batches
    ON supplier_mapping_review_batches;
CREATE TRIGGER trg_immutable_mapping_review_batches
BEFORE UPDATE OR DELETE ON supplier_mapping_review_batches
FOR EACH ROW EXECUTE FUNCTION reject_persistent_mapping_mutation();
DROP TRIGGER IF EXISTS trg_immutable_mapping_review_candidates
    ON supplier_mapping_review_candidates;
CREATE TRIGGER trg_immutable_mapping_review_candidates
BEFORE UPDATE OR DELETE ON supplier_mapping_review_candidates
FOR EACH ROW EXECUTE FUNCTION reject_persistent_mapping_mutation();

CREATE TABLE IF NOT EXISTS supplier_mapping_decisions (
    mapping_decision_id UUID PRIMARY KEY,
    decision_idempotency_key UUID NOT NULL UNIQUE,
    review_batch_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    decision_scope_sha256 TEXT NOT NULL CHECK (decision_scope_sha256 ~ '^[0-9a-f]{64}$'),
    supplier_identity_key_sha256 TEXT NOT NULL
        CHECK (supplier_identity_key_sha256 ~ '^[0-9a-f]{64}$'),
    operational_offer_key_sha256 TEXT NOT NULL
        CHECK (operational_offer_key_sha256 ~ '^[0-9a-f]{64}$'),
    action TEXT NOT NULL CHECK (action IN ('APPROVE_MAPPING','REJECT_MAPPING','DEFER')),
    decision_origin TEXT NOT NULL CHECK (decision_origin IN ('HUMAN','POLICY')),
    authority_kind TEXT CHECK (authority_kind IN ('HUMAN_APPROVED','POLICY_APPROVED')),
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE RESTRICT,
    vendor_id UUID NOT NULL REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    printed_occurrence_sha256 TEXT NOT NULL
        CHECK (printed_occurrence_sha256 ~ '^[0-9a-f]{64}$'),
    distributor_product_id_state TEXT NOT NULL
        CHECK (distributor_product_id_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    distributor_product_id_value TEXT,
    supplier_code_state TEXT NOT NULL
        CHECK (supplier_code_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    supplier_code_value TEXT,
    offer_class TEXT NOT NULL CHECK (offer_class IN
        ('REGULAR','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    supplier_offer_package_type TEXT NOT NULL CHECK (btrim(supplier_offer_package_type)<>''),
    size_state TEXT NOT NULL CHECK (size_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    size_value TEXT,
    raw_pack_state TEXT NOT NULL CHECK (raw_pack_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    raw_pack_value TEXT,
    shopify_units_state TEXT NOT NULL
        CHECK (shopify_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    shopify_units_value NUMERIC(12,4),
    qualifying_units_state TEXT NOT NULL
        CHECK (qualifying_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    qualifying_units_value NUMERIC(12,4),
    assortment_scope_state TEXT NOT NULL
        CHECK (assortment_scope_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_scope_value TEXT,
    assortment_group_state TEXT NOT NULL
        CHECK (assortment_group_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_group_value TEXT,
    assortable_state TEXT NOT NULL
        CHECK (assortable_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortable_value BOOLEAN,
    reviewed_facts JSONB NOT NULL CHECK (jsonb_typeof(reviewed_facts)='object'),
    component_relationships JSONB NOT NULL CHECK (jsonb_typeof(component_relationships)='array'),
    owner_clarifications JSONB NOT NULL CHECK (jsonb_typeof(owner_clarifications)='array'),
    historical_capture_scope JSONB NOT NULL CHECK (jsonb_typeof(historical_capture_scope)='object'),
    expected_batch_payload_sha256 TEXT NOT NULL
        CHECK (expected_batch_payload_sha256 ~ '^[0-9a-f]{64}$'),
    expected_candidate_sha256 TEXT NOT NULL
        CHECK (expected_candidate_sha256 ~ '^[0-9a-f]{64}$'),
    expected_catalog_sha256 TEXT NOT NULL
        CHECK (expected_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    expected_vendor_sha256 TEXT NOT NULL
        CHECK (expected_vendor_sha256 ~ '^[0-9a-f]{64}$'),
    expected_rejection_memory_sha256 TEXT NOT NULL
        CHECK (expected_rejection_memory_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_set_sha256 TEXT NOT NULL CHECK (evidence_set_sha256 ~ '^[0-9a-f]{64}$'),
    human_principal_ref TEXT,
    human_role_ref TEXT,
    human_authn_context_sha256 TEXT,
    preview_sha256 TEXT,
    confirmation_sha256 TEXT,
    service_principal_ref TEXT,
    policy_ref TEXT,
    policy_version TEXT,
    policy_publication_sha256 TEXT,
    policy_predicate_version TEXT,
    policy_predicate_result_sha256 TEXT,
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    result_offer_id BIGINT REFERENCES supplier_offers(offer_id) ON DELETE RESTRICT,
    offer_link_kind TEXT CHECK (offer_link_kind IN ('CREATED_INACTIVE','LINKED_EXISTING')),
    result_offer_contract_sha256 TEXT,
    result_rejection_id BIGINT REFERENCES mapping_rejections(rejection_id) ON DELETE RESTRICT,
    supersedes_mapping_decision_id UUID
        REFERENCES supplier_mapping_decisions(mapping_decision_id) ON DELETE RESTRICT,
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    decided_txid BIGINT NOT NULL DEFAULT txid_current(),
    FOREIGN KEY (review_batch_id,candidate_id)
        REFERENCES supplier_mapping_review_candidates(review_batch_id,candidate_id)
        ON DELETE RESTRICT,
    UNIQUE (mapping_decision_id,result_offer_id),
    CHECK ((distributor_product_id_state='VALUE')=
           (distributor_product_id_value IS NOT NULL)),
    CHECK ((supplier_code_state='VALUE')=(supplier_code_value IS NOT NULL)),
    CHECK ((size_state='VALUE')=(size_value IS NOT NULL)),
    CHECK ((raw_pack_state='VALUE')=(raw_pack_value IS NOT NULL)),
    CHECK ((shopify_units_state='VALUE')=(shopify_units_value IS NOT NULL)),
    CHECK ((qualifying_units_state='VALUE')=(qualifying_units_value IS NOT NULL)),
    CHECK ((assortment_scope_state='VALUE')=(assortment_scope_value IS NOT NULL)),
    CHECK (assortment_scope_value IS NULL OR
           assortment_scope_value IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')),
    CHECK ((assortment_group_state='VALUE')=(assortment_group_value IS NOT NULL)),
    CHECK ((assortable_state='VALUE')=(assortable_value IS NOT NULL)),
    CHECK (shopify_units_value IS NULL OR shopify_units_value>0),
    CHECK (qualifying_units_value IS NULL OR qualifying_units_value>0),
    CHECK (
        (action='APPROVE_MAPPING'
         AND authority_kind IS NOT NULL AND result_offer_id IS NOT NULL
         AND offer_link_kind IS NOT NULL
         AND result_offer_contract_sha256 ~ '^[0-9a-f]{64}$'
         AND result_rejection_id IS NULL)
        OR
        (action='REJECT_MAPPING' AND authority_kind IS NULL
         AND result_offer_id IS NULL AND offer_link_kind IS NULL
         AND result_offer_contract_sha256 IS NULL AND result_rejection_id IS NOT NULL)
        OR
        (action='DEFER' AND authority_kind IS NULL
         AND result_offer_id IS NULL AND offer_link_kind IS NULL
         AND result_offer_contract_sha256 IS NULL AND result_rejection_id IS NULL)
    ),
    CHECK (
        (decision_origin='HUMAN'
         AND human_principal_ref IS NOT NULL AND btrim(human_principal_ref)<>''
         AND human_role_ref IS NOT NULL AND btrim(human_role_ref)<>''
         AND human_authn_context_sha256 ~ '^[0-9a-f]{64}$'
         AND preview_sha256 ~ '^[0-9a-f]{64}$'
         AND confirmation_sha256 ~ '^[0-9a-f]{64}$'
         AND service_principal_ref IS NULL AND policy_ref IS NULL
         AND policy_version IS NULL AND policy_publication_sha256 IS NULL
         AND policy_predicate_version IS NULL AND policy_predicate_result_sha256 IS NULL
         AND (authority_kind IS NULL OR authority_kind='HUMAN_APPROVED'))
        OR
        (decision_origin='POLICY' AND authority_kind='POLICY_APPROVED'
         AND human_principal_ref IS NULL AND human_role_ref IS NULL
         AND human_authn_context_sha256 IS NULL
         AND preview_sha256 IS NULL AND confirmation_sha256 IS NULL
         AND service_principal_ref IS NOT NULL AND btrim(service_principal_ref)<>''
         AND policy_ref IS NOT NULL AND btrim(policy_ref)<>''
         AND policy_version IS NOT NULL AND btrim(policy_version)<>''
         AND policy_publication_sha256 ~ '^[0-9a-f]{64}$'
         AND policy_predicate_version IS NOT NULL AND btrim(policy_predicate_version)<>''
         AND policy_predicate_result_sha256 ~ '^[0-9a-f]{64}$')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_decision_root_per_scope
    ON supplier_mapping_decisions(decision_scope_sha256)
    WHERE supersedes_mapping_decision_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_decision_successor
    ON supplier_mapping_decisions(supersedes_mapping_decision_id)
    WHERE supersedes_mapping_decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_candidate
    ON supplier_mapping_decisions(review_batch_id,candidate_id,decided_at);
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_offer_key
    ON supplier_mapping_decisions(operational_offer_key_sha256,decided_at);
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_supplier_identity
    ON supplier_mapping_decisions(supplier_identity_key_sha256,decided_at);
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_result_offer
    ON supplier_mapping_decisions(result_offer_id)
    WHERE result_offer_id IS NOT NULL;

CREATE OR REPLACE FUNCTION validate_supplier_mapping_decision_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    b supplier_mapping_review_batches%ROWTYPE;
    c supplier_mapping_review_candidates%ROWTYPE;
    o supplier_offers%ROWTYPE;
    prior supplier_mapping_decisions%ROWTYPE;
    expected_payload JSONB;
    rejection_source_key TEXT;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'mapping decisions require SERIALIZABLE isolation';
    END IF;
    IF NEW.decided_txid<>txid_current() THEN
        RAISE EXCEPTION 'mapping decision transaction identity differs';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('supplier-mapping:'||NEW.decision_scope_sha256,0)
    );
    PERFORM pg_advisory_xact_lock(
        hashtextextended('supplier-offer-key:'||NEW.operational_offer_key_sha256,0)
    );

    SELECT * INTO b FROM supplier_mapping_review_batches
     WHERE review_batch_id=NEW.review_batch_id FOR SHARE;
    SELECT * INTO c FROM supplier_mapping_review_candidates
     WHERE review_batch_id=NEW.review_batch_id AND candidate_id=NEW.candidate_id
     FOR SHARE;
    PERFORM 1 FROM variants WHERE variant_id=NEW.variant_id FOR SHARE;
    PERFORM 1 FROM vendors WHERE vendor_id=NEW.vendor_id FOR SHARE;
    IF NOT FOUND OR b.review_batch_id IS NULL OR c.candidate_id IS NULL THEN
        RAISE EXCEPTION 'decision batch, candidate, Variant, or vendor is absent';
    END IF;

    IF NEW.expected_batch_payload_sha256 IS DISTINCT FROM b.payload_sha256
       OR NEW.expected_candidate_sha256 IS DISTINCT FROM c.candidate_sha256
       OR NEW.expected_catalog_sha256 IS DISTINCT FROM
            persistent_mapping_catalog_fingerprint(NEW.variant_id)
       OR NEW.expected_vendor_sha256 IS DISTINCT FROM
            persistent_mapping_vendor_fingerprint(NEW.vendor_id)
       OR NEW.expected_rejection_memory_sha256 IS DISTINCT FROM
            persistent_mapping_rejection_fingerprint(
                NEW.vendor_id,
                'persistent-mapping:'||NEW.supplier_identity_key_sha256
            )
       OR NEW.decision_scope_sha256 IS DISTINCT FROM c.decision_scope_sha256
       OR NEW.supplier_identity_key_sha256 IS DISTINCT FROM c.supplier_identity_key_sha256
       OR NEW.operational_offer_key_sha256 IS DISTINCT FROM c.operational_offer_key_sha256
       OR NEW.printed_occurrence_sha256 IS DISTINCT FROM c.printed_occurrence_sha256
       OR NEW.variant_id IS DISTINCT FROM c.proposed_variant_id
       OR NEW.vendor_id IS DISTINCT FROM c.proposed_vendor_id
       OR NEW.distributor_product_id_state IS DISTINCT FROM c.distributor_product_id_state
       OR NEW.distributor_product_id_value IS DISTINCT FROM c.distributor_product_id_value
       OR NEW.supplier_code_state IS DISTINCT FROM c.supplier_code_state
       OR NEW.supplier_code_value IS DISTINCT FROM c.supplier_code_value
       OR NEW.offer_class IS DISTINCT FROM c.offer_class
       OR NEW.size_state IS DISTINCT FROM c.size_state
       OR NEW.size_value IS DISTINCT FROM c.size_value
       OR NEW.raw_pack_state IS DISTINCT FROM c.raw_pack_state
       OR NEW.raw_pack_value IS DISTINCT FROM c.raw_pack_value
       OR NEW.shopify_units_state IS DISTINCT FROM c.shopify_units_state
       OR NEW.shopify_units_value IS DISTINCT FROM c.shopify_units_value
       OR NEW.qualifying_units_state IS DISTINCT FROM c.qualifying_units_state
       OR NEW.qualifying_units_value IS DISTINCT FROM c.qualifying_units_value
       OR NEW.assortment_scope_state IS DISTINCT FROM c.assortment_scope_state
       OR NEW.assortment_scope_value IS DISTINCT FROM c.assortment_scope_value
       OR NEW.assortment_group_state IS DISTINCT FROM c.assortment_group_state
       OR NEW.assortment_group_value IS DISTINCT FROM c.assortment_group_value
       OR NEW.assortable_state IS DISTINCT FROM c.assortable_state
       OR NEW.assortable_value IS DISTINCT FROM c.assortable_value
       OR NEW.reviewed_facts IS DISTINCT FROM
            persistent_mapping_candidate_reviewed_facts(c)
       OR NEW.evidence_set_sha256 IS DISTINCT FROM
            persistent_mapping_candidate_evidence_set_sha256(c)
       OR NEW.component_relationships IS DISTINCT FROM c.component_relationships
       OR NEW.owner_clarifications IS DISTINCT FROM c.owner_clarifications
       OR NEW.historical_capture_scope IS DISTINCT FROM c.historical_capture_scope THEN
        RAISE EXCEPTION 'mapping preview is stale or candidate identity differs';
    END IF;

    IF NEW.supersedes_mapping_decision_id IS NOT NULL THEN
        SELECT * INTO prior FROM supplier_mapping_decisions
         WHERE mapping_decision_id=NEW.supersedes_mapping_decision_id FOR UPDATE;
        IF prior.mapping_decision_id IS NULL
           OR prior.decision_scope_sha256<>NEW.decision_scope_sha256
           OR EXISTS (SELECT 1 FROM supplier_mapping_decisions d
                       WHERE d.supersedes_mapping_decision_id=prior.mapping_decision_id) THEN
            RAISE EXCEPTION 'stale or wrong-scope prior mapping decision';
        END IF;
    END IF;

    IF NEW.decision_origin='HUMAN' THEN
        PERFORM persistent_mapping_require_human_context(
            NEW.human_principal_ref,NEW.human_role_ref,
            NEW.human_authn_context_sha256,'SUPPLIER_MAPPING_DECIDE'
        );
    ELSIF NOT supplier_mapping_policy_is_published(
        NEW.policy_ref,NEW.policy_version,NEW.policy_publication_sha256,
        NEW.evidence_set_sha256
    ) THEN
        RAISE EXCEPTION 'no published mapping policy authorizes this decision';
    END IF;

    rejection_source_key := 'persistent-mapping:'||NEW.supplier_identity_key_sha256;
    IF NEW.action='APPROVE_MAPPING' THEN
        IF b.structural_state<>'READY' OR b.source_evidence_state<>'READY'
           OR b.semantic_state<>'READY' OR b.source_is_simulation
           OR jsonb_array_length(c.blockers)<>0 THEN
            RAISE EXCEPTION 'blocked or simulated evidence cannot approve a mapping';
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM jsonb_array_elements(c.independent_linkage_evidence) evidence
             WHERE evidence->>'evidence_mode'='DETERMINISTIC_INDEPENDENT'
               AND evidence->>'origin_sha256' ~ '^[0-9a-f]{64}$'
               AND evidence->>'observation_sha256' ~ '^[0-9a-f]{64}$'
               AND evidence->>'origin_sha256'<>c.source_file_sha256
        ) OR EXISTS (
            SELECT 1
              FROM jsonb_array_elements(c.independent_linkage_evidence) evidence
             WHERE evidence->>'evidence_mode'='FUZZY_SCORE'
               AND evidence->>'authoritative'='true'
        ) THEN
            RAISE EXCEPTION
                'mapping approval requires independent deterministic evidence; fuzzy is never authority';
        END IF;
        IF NEW.distributor_product_id_state<>'VALUE'
           OR btrim(NEW.distributor_product_id_value)=''
           OR (NEW.supplier_code_state='VALUE' AND (
                btrim(NEW.supplier_code_value)=''
                OR NEW.supplier_code_value<>btrim(NEW.supplier_code_value)
              ))
           OR NEW.offer_class IN ('COMBO','UNKNOWN')
           OR NEW.assortment_scope_state<>'VALUE'
           OR NEW.supplier_offer_package_type IS DISTINCT FROM CASE NEW.offer_class
                WHEN 'REGULAR' THEN 'STANDARD'
                WHEN 'GIFT' THEN 'GIFT'
                WHEN 'SPECIAL' THEN 'SPECIAL'
                WHEN 'ALTERNATE' THEN 'ALTERNATE'
                WHEN 'COMPONENT' THEN 'COMPONENT'
              END THEN
            RAISE EXCEPTION 'approval lacks a supported exact operational offer identity';
        END IF;
        IF NOT is_procurement_eligible_variant(NEW.variant_id)
           OR NOT EXISTS (
                SELECT 1 FROM vendors v
                 WHERE v.vendor_id=NEW.vendor_id AND v.active
              ) THEN
            RAISE EXCEPTION 'mapping approval requires an active canonical Variant and vendor';
        END IF;
        IF EXISTS (
            SELECT 1 FROM mapping_rejections r
             WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
               AND r.vendor_id=NEW.vendor_id
               AND r.source_key=rejection_source_key
        ) THEN
            RAISE EXCEPTION 'active rejection memory blocks mapping approval';
        END IF;
        SELECT * INTO o FROM supplier_offers
         WHERE offer_id=NEW.result_offer_id FOR SHARE;
        IF o.offer_id IS NULL
           OR o.variant_id<>NEW.variant_id OR o.vendor_id<>NEW.vendor_id
           OR o.supplier_sku IS DISTINCT FROM NEW.supplier_code_value
           OR o.package_type<>NEW.supplier_offer_package_type
           OR o.size_text IS DISTINCT FROM NEW.size_value
           OR o.raw_pack IS DISTINCT FROM NEW.raw_pack_value
           OR o.shopify_units_per_case IS DISTINCT FROM NEW.shopify_units_value
           OR o.qualifying_units_per_case IS DISTINCT FROM NEW.qualifying_units_value
           OR o.assortment_scope IS DISTINCT FROM NEW.assortment_scope_value
           OR o.assortment_group IS DISTINCT FROM NEW.assortment_group_value
           OR o.assortable IS DISTINCT FROM NEW.assortable_value
           OR persistent_mapping_offer_fingerprint(o.offer_id)
                IS DISTINCT FROM NEW.result_offer_contract_sha256 THEN
            RAISE EXCEPTION 'resulting supplier offer contract differs';
        END IF;
        IF NEW.offer_link_kind='CREATED_INACTIVE' AND (
            o.active
            OR EXISTS (SELECT 1 FROM prices p WHERE p.offer_id=o.offer_id)
            OR EXISTS (SELECT 1 FROM purchase_order_lines p WHERE p.offer_id=o.offer_id)
            OR EXISTS (SELECT 1 FROM procurement_recommendations p WHERE p.offer_id=o.offer_id)
            OR EXISTS (SELECT 1 FROM run_price_snapshots p WHERE p.offer_id=o.offer_id)
            OR EXISTS (SELECT 1 FROM combo_components p WHERE p.offer_id=o.offer_id)
        ) THEN
            RAISE EXCEPTION 'mapping-created supplier offer must be inactive and unreferenced';
        END IF;
        IF EXISTS (
            SELECT 1 FROM supplier_mapping_decisions d
             WHERE d.action='APPROVE_MAPPING'
               AND d.operational_offer_key_sha256=NEW.operational_offer_key_sha256
               AND d.result_offer_id<>NEW.result_offer_id
        ) THEN
            RAISE EXCEPTION 'equivalent printed occurrences must share one operational offer';
        END IF;
    ELSIF NEW.action='REJECT_MAPPING' THEN
        IF NOT EXISTS (
            SELECT 1 FROM mapping_rejections r
             WHERE r.rejection_id=NEW.result_rejection_id AND r.active
               AND r.mapping_type='SUPPLIER_OFFER'
               AND r.vendor_id=NEW.vendor_id
               AND r.rejected_variant_id=NEW.variant_id
               AND r.source_key=rejection_source_key
        ) THEN
            RAISE EXCEPTION 'mapping rejection result differs from decision scope';
        END IF;
    END IF;

    expected_payload := to_jsonb(NEW)-ARRAY[
        'mapping_decision_id','decision_idempotency_key','canonical_payload',
        'payload_sha256','decided_at','decided_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'mapping decision canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_supplier_mapping_decision_insert
    ON supplier_mapping_decisions;
CREATE TRIGGER trg_validate_supplier_mapping_decision_insert
BEFORE INSERT ON supplier_mapping_decisions
FOR EACH ROW EXECUTE FUNCTION validate_supplier_mapping_decision_insert();
DROP TRIGGER IF EXISTS trg_immutable_supplier_mapping_decisions
    ON supplier_mapping_decisions;
CREATE TRIGGER trg_immutable_supplier_mapping_decisions
BEFORE UPDATE OR DELETE ON supplier_mapping_decisions
FOR EACH ROW EXECUTE FUNCTION reject_persistent_mapping_mutation();

CREATE TABLE IF NOT EXISTS supplier_offer_selection_events (
    selection_event_id UUID PRIMARY KEY,
    selection_idempotency_key UUID NOT NULL UNIQUE,
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE RESTRICT,
    selection_scope TEXT NOT NULL DEFAULT 'ROUTINE_PROCUREMENT_STANDARD'
        CHECK (selection_scope='ROUTINE_PROCUREMENT_STANDARD'),
    action TEXT NOT NULL CHECK (action IN ('SELECT','CLEAR')),
    selected_offer_id BIGINT REFERENCES supplier_offers(offer_id) ON DELETE RESTRICT,
    mapping_decision_id UUID,
    expected_prior_event_id UUID
        REFERENCES supplier_offer_selection_events(selection_event_id) ON DELETE RESTRICT,
    expected_prior_head_version BIGINT NOT NULL CHECK (expected_prior_head_version>=0),
    expected_mapping_decision_sha256 TEXT,
    expected_offer_contract_sha256 TEXT,
    expected_catalog_sha256 TEXT NOT NULL CHECK (expected_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    expected_vendor_sha256 TEXT,
    expected_rejection_memory_sha256 TEXT,
    effective_from DATE NOT NULL,
    effective_through DATE,
    human_principal_ref TEXT NOT NULL CHECK (btrim(human_principal_ref)<>''),
    human_role_ref TEXT NOT NULL CHECK (btrim(human_role_ref)<>''),
    human_authn_context_sha256 TEXT NOT NULL
        CHECK (human_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    preview_sha256 TEXT NOT NULL CHECK (preview_sha256 ~ '^[0-9a-f]{64}$'),
    confirmation_sha256 TEXT NOT NULL CHECK (confirmation_sha256 ~ '^[0-9a-f]{64}$'),
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    selected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    selected_txid BIGINT NOT NULL DEFAULT txid_current(),
    FOREIGN KEY (mapping_decision_id,selected_offer_id)
        REFERENCES supplier_mapping_decisions(mapping_decision_id,result_offer_id)
        ON DELETE RESTRICT,
    CHECK (effective_through IS NULL OR effective_through>=effective_from),
    CHECK (
        (action='SELECT' AND selected_offer_id IS NOT NULL
         AND mapping_decision_id IS NOT NULL
         AND expected_mapping_decision_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_offer_contract_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_vendor_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_rejection_memory_sha256 ~ '^[0-9a-f]{64}$')
        OR
        (action='CLEAR' AND selected_offer_id IS NULL
         AND mapping_decision_id IS NULL
         AND expected_mapping_decision_sha256 IS NULL
         AND expected_offer_contract_sha256 IS NULL
         AND expected_vendor_sha256 IS NULL
         AND expected_rejection_memory_sha256 IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS supplier_offer_selection_heads (
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE RESTRICT,
    selection_scope TEXT NOT NULL DEFAULT 'ROUTINE_PROCUREMENT_STANDARD'
        CHECK (selection_scope='ROUTINE_PROCUREMENT_STANDARD'),
    selection_event_id UUID NOT NULL UNIQUE
        REFERENCES supplier_offer_selection_events(selection_event_id) ON DELETE RESTRICT,
    head_version BIGINT NOT NULL CHECK (head_version>=1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY (variant_id,selection_scope)
);

CREATE INDEX IF NOT EXISTS idx_offer_selection_events_offer
    ON supplier_offer_selection_events(selected_offer_id)
    WHERE selected_offer_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_offer_selection_events_mapping
    ON supplier_offer_selection_events(mapping_decision_id)
    WHERE mapping_decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION validate_supplier_offer_selection_event_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_head supplier_offer_selection_heads%ROWTYPE;
    mapping supplier_mapping_decisions%ROWTYPE;
    offer supplier_offers%ROWTYPE;
    candidate supplier_mapping_review_candidates%ROWTYPE;
    expected_payload JSONB;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'routine selection requires SERIALIZABLE isolation';
    END IF;
    IF NEW.selected_txid<>txid_current() THEN
        RAISE EXCEPTION 'selection event transaction identity differs';
    END IF;
    PERFORM persistent_mapping_require_human_context(
        NEW.human_principal_ref,NEW.human_role_ref,
        NEW.human_authn_context_sha256,'ROUTINE_OFFER_SELECT'
    );
    PERFORM pg_advisory_xact_lock(
        hashtextextended('routine-offer-selection:'||NEW.variant_id,0)
    );
    SELECT * INTO current_head FROM supplier_offer_selection_heads
     WHERE variant_id=NEW.variant_id AND selection_scope=NEW.selection_scope
     FOR UPDATE;
    IF current_head.selection_event_id IS NULL THEN
        IF NEW.expected_prior_event_id IS NOT NULL
           OR NEW.expected_prior_head_version<>0 THEN
            RAISE EXCEPTION 'stale prior selection head';
        END IF;
    ELSIF NEW.expected_prior_event_id IS DISTINCT FROM current_head.selection_event_id
       OR NEW.expected_prior_head_version<>current_head.head_version THEN
        RAISE EXCEPTION 'stale prior selection head';
    END IF;
    IF NEW.expected_catalog_sha256 IS DISTINCT FROM
        persistent_mapping_catalog_fingerprint(NEW.variant_id) THEN
        RAISE EXCEPTION 'selection catalog preview is stale';
    END IF;

    IF NEW.action='SELECT' THEN
        SELECT * INTO mapping FROM supplier_mapping_decisions
         WHERE mapping_decision_id=NEW.mapping_decision_id FOR SHARE;
        SELECT * INTO offer FROM supplier_offers
         WHERE offer_id=NEW.selected_offer_id FOR SHARE;
        SELECT * INTO candidate FROM supplier_mapping_review_candidates
         WHERE candidate_id=mapping.candidate_id FOR SHARE;
        IF mapping.mapping_decision_id IS NULL OR mapping.action<>'APPROVE_MAPPING'
           OR mapping.variant_id<>NEW.variant_id
           OR EXISTS (SELECT 1 FROM supplier_mapping_decisions successor
                       WHERE successor.supersedes_mapping_decision_id=mapping.mapping_decision_id)
           OR candidate.offer_class<>'REGULAR'
           OR mapping.supplier_offer_package_type<>'STANDARD'
           OR mapping.supplier_code_state<>'VALUE'
           OR offer.offer_id IS NULL OR offer.variant_id<>NEW.variant_id
           OR offer.package_type<>'STANDARD'
           OR persistent_mapping_json_sha256(mapping.canonical_payload)
                IS DISTINCT FROM NEW.expected_mapping_decision_sha256
           OR persistent_mapping_offer_fingerprint(offer.offer_id)
                IS DISTINCT FROM NEW.expected_offer_contract_sha256
           OR persistent_mapping_vendor_fingerprint(mapping.vendor_id)
                IS DISTINCT FROM NEW.expected_vendor_sha256
           OR persistent_mapping_rejection_fingerprint(
                mapping.vendor_id,
                'persistent-mapping:'||mapping.supplier_identity_key_sha256
              ) IS DISTINCT FROM NEW.expected_rejection_memory_sha256
           OR NOT is_procurement_eligible_variant(NEW.variant_id)
           OR NOT EXISTS (SELECT 1 FROM vendors v
                           WHERE v.vendor_id=mapping.vendor_id AND v.active)
           OR (offer.valid_from IS NOT NULL AND offer.valid_from>NEW.effective_from)
           OR (offer.valid_to IS NOT NULL AND offer.valid_to<NEW.effective_from)
           OR EXISTS (
                SELECT 1 FROM mapping_rejections r
                 WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
                   AND r.vendor_id=mapping.vendor_id
                   AND r.source_key='persistent-mapping:'||mapping.supplier_identity_key_sha256
              )
           OR EXISTS (
                SELECT 1 FROM supplier_offers reused
                 WHERE reused.vendor_id=mapping.vendor_id
                   AND reused.supplier_sku=mapping.supplier_code_value
                   AND reused.offer_id<>offer.offer_id
                   AND reused.active
              ) THEN
            RAISE EXCEPTION 'selected offer is stale, ineligible, rejected, or not regular';
        END IF;
        IF NOT offer.active AND (
            mapping.offer_link_kind<>'CREATED_INACTIVE'
            OR EXISTS (SELECT 1 FROM prices p WHERE p.offer_id=offer.offer_id)
            OR EXISTS (SELECT 1 FROM purchase_order_lines p WHERE p.offer_id=offer.offer_id)
            OR EXISTS (SELECT 1 FROM procurement_recommendations p WHERE p.offer_id=offer.offer_id)
            OR EXISTS (SELECT 1 FROM run_price_snapshots p WHERE p.offer_id=offer.offer_id)
            OR EXISTS (SELECT 1 FROM combo_components p WHERE p.offer_id=offer.offer_id)
        ) THEN
            RAISE EXCEPTION 'inactive selection is not a fresh unpriced mapping result';
        END IF;
    END IF;

    expected_payload := to_jsonb(NEW)-ARRAY[
        'selection_event_id','selection_idempotency_key','canonical_payload',
        'payload_sha256','selected_at','selected_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'selection canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION validate_supplier_offer_selection_head_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE event supplier_offer_selection_events%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'routine selection head cannot be deleted';
    END IF;
    IF NEW.updated_txid<>txid_current() THEN
        RAISE EXCEPTION 'selection head transaction identity differs';
    END IF;
    SELECT * INTO event FROM supplier_offer_selection_events
     WHERE selection_event_id=NEW.selection_event_id;
    IF event.selection_event_id IS NULL OR event.selected_txid<>txid_current()
       OR event.variant_id<>NEW.variant_id
       OR event.selection_scope<>NEW.selection_scope
       OR NEW.head_version<>event.expected_prior_head_version+1 THEN
        RAISE EXCEPTION 'head must advance beside its exact new event';
    END IF;
    IF TG_OP='UPDATE' AND (
        ROW(NEW.variant_id,NEW.selection_scope)
            IS DISTINCT FROM ROW(OLD.variant_id,OLD.selection_scope)
        OR event.expected_prior_event_id IS DISTINCT FROM OLD.selection_event_id
        OR event.expected_prior_head_version<>OLD.head_version
    ) THEN
        RAISE EXCEPTION 'head update prior state differs';
    ELSIF TG_OP='INSERT' AND event.expected_prior_event_id IS NOT NULL THEN
        RAISE EXCEPTION 'initial head event unexpectedly has a predecessor';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION validate_supplier_offer_selection_event_committed()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM supplier_offer_selection_heads h
         WHERE h.variant_id=NEW.variant_id
           AND h.selection_scope=NEW.selection_scope
           AND h.selection_event_id=NEW.selection_event_id
           AND h.head_version=NEW.expected_prior_head_version+1
    ) THEN
        RAISE EXCEPTION 'selection event has no same-transaction head advancement';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_offer_selection_event_insert
    ON supplier_offer_selection_events;
CREATE TRIGGER trg_validate_offer_selection_event_insert
BEFORE INSERT ON supplier_offer_selection_events
FOR EACH ROW EXECUTE FUNCTION validate_supplier_offer_selection_event_insert();
DROP TRIGGER IF EXISTS trg_immutable_offer_selection_events
    ON supplier_offer_selection_events;
CREATE TRIGGER trg_immutable_offer_selection_events
BEFORE UPDATE OR DELETE ON supplier_offer_selection_events
FOR EACH ROW EXECUTE FUNCTION reject_persistent_mapping_mutation();
DROP TRIGGER IF EXISTS trg_validate_offer_selection_head_change
    ON supplier_offer_selection_heads;
CREATE TRIGGER trg_validate_offer_selection_head_change
BEFORE INSERT OR UPDATE OR DELETE ON supplier_offer_selection_heads
FOR EACH ROW EXECUTE FUNCTION validate_supplier_offer_selection_head_change();
DROP TRIGGER IF EXISTS trg_validate_offer_selection_event_committed
    ON supplier_offer_selection_events;
CREATE CONSTRAINT TRIGGER trg_validate_offer_selection_event_committed
AFTER INSERT ON supplier_offer_selection_events DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION validate_supplier_offer_selection_event_committed();

CREATE OR REPLACE FUNCTION protect_persistently_mapped_offer_contract()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM supplier_mapping_decisions d
         WHERE d.action='APPROVE_MAPPING' AND d.result_offer_id=OLD.offer_id
    ) AND (
        TG_OP='DELETE'
        OR ROW(NEW.variant_id,NEW.vendor_id,NEW.supplier_sku,NEW.package_type,
               NEW.size_text,NEW.raw_pack,NEW.shopify_units_per_case,
               NEW.qualifying_units_per_case,NEW.assortment_scope,
               NEW.assortment_group,NEW.assortable)
           IS DISTINCT FROM
           ROW(OLD.variant_id,OLD.vendor_id,OLD.supplier_sku,OLD.package_type,
               OLD.size_text,OLD.raw_pack,OLD.shopify_units_per_case,
               OLD.qualifying_units_per_case,OLD.assortment_scope,
               OLD.assortment_group,OLD.assortable)
        OR NEW.active IS DISTINCT FROM OLD.active
    ) THEN
        RAISE EXCEPTION
            'mapped offer identity/activation requires a separately approved transition';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION protect_unactivated_mapped_offer_price()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE checked_offer_id BIGINT;
BEGIN
    checked_offer_id := CASE WHEN TG_OP='DELETE' THEN OLD.offer_id ELSE NEW.offer_id END;
    IF EXISTS (
        SELECT 1
          FROM supplier_mapping_decisions d
          JOIN supplier_offers o ON o.offer_id=d.result_offer_id
         WHERE d.action='APPROVE_MAPPING'
           AND d.offer_link_kind='CREATED_INACTIVE'
           AND d.result_offer_id=checked_offer_id
           AND NOT o.active
    ) THEN
        RAISE EXCEPTION 'inactive mapping result cannot receive operational pricing';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_persistently_mapped_offer_contract
    ON supplier_offers;
CREATE TRIGGER trg_protect_persistently_mapped_offer_contract
BEFORE UPDATE OR DELETE ON supplier_offers
FOR EACH ROW EXECUTE FUNCTION protect_persistently_mapped_offer_contract();
DROP TRIGGER IF EXISTS trg_protect_unactivated_mapped_offer_price ON prices;
CREATE TRIGGER trg_protect_unactivated_mapped_offer_price
BEFORE INSERT OR UPDATE OR DELETE ON prices
FOR EACH ROW EXECUTE FUNCTION protect_unactivated_mapped_offer_price();

CREATE OR REPLACE VIEW v_effective_supplier_mapping_decisions AS
SELECT d.*
  FROM supplier_mapping_decisions d
 WHERE NOT EXISTS (
    SELECT 1 FROM supplier_mapping_decisions successor
     WHERE successor.supersedes_mapping_decision_id=d.mapping_decision_id
 );

CREATE OR REPLACE VIEW v_supplier_offer_selection_diagnostics AS
SELECT
    h.variant_id,h.selection_scope,h.selection_event_id,h.head_version,
    e.action,e.selected_offer_id,e.mapping_decision_id,
    o.vendor_id,o.supplier_sku,o.package_type,o.active AS offer_active,
    CASE
      WHEN e.action='CLEAR' THEN 'EXPLICITLY_CLEARED'
      WHEN d.mapping_decision_id IS NULL THEN 'STALE_MAPPING_DECISION'
      WHEN persistent_mapping_json_sha256(d.canonical_payload)
           IS DISTINCT FROM e.expected_mapping_decision_sha256
        THEN 'STALE_MAPPING_DECISION'
      WHEN o.offer_id IS NULL OR persistent_mapping_offer_fingerprint(o.offer_id)
           IS DISTINCT FROM e.expected_offer_contract_sha256 THEN 'STALE_OFFER_CONTRACT'
      WHEN persistent_mapping_catalog_fingerprint(h.variant_id)
           IS DISTINCT FROM e.expected_catalog_sha256 THEN 'STALE_CATALOG'
      WHEN persistent_mapping_vendor_fingerprint(d.vendor_id)
           IS DISTINCT FROM e.expected_vendor_sha256 THEN 'STALE_VENDOR'
      WHEN persistent_mapping_rejection_fingerprint(
               d.vendor_id,'persistent-mapping:'||d.supplier_identity_key_sha256
           ) IS DISTINCT FROM e.expected_rejection_memory_sha256
        THEN 'STALE_REJECTION_MEMORY'
      WHEN NOT is_procurement_eligible_variant(h.variant_id) THEN 'INELIGIBLE_VARIANT'
      WHEN NOT v.active THEN 'INACTIVE_VENDOR'
      WHEN EXISTS (
        SELECT 1 FROM mapping_rejections r
         WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
           AND r.vendor_id=d.vendor_id AND r.rejected_variant_id=d.variant_id
           AND r.source_key='persistent-mapping:'||d.supplier_identity_key_sha256
      ) THEN 'ACTIVE_REJECTION'
      WHEN e.effective_from>current_date
        OR (e.effective_through IS NOT NULL AND e.effective_through<current_date)
        OR (o.valid_from IS NOT NULL AND o.valid_from>current_date)
        OR (o.valid_to IS NOT NULL AND o.valid_to<current_date) THEN 'OUTSIDE_VALIDITY'
      WHEN o.active THEN 'SELECTED_ACTIVE'
      ELSE 'SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION'
    END AS selection_state,
    FALSE AS recommendation_cutover_enabled
  FROM supplier_offer_selection_heads h
  JOIN supplier_offer_selection_events e
    ON e.selection_event_id=h.selection_event_id
  LEFT JOIN v_effective_supplier_mapping_decisions d
    ON d.mapping_decision_id=e.mapping_decision_id AND d.action='APPROVE_MAPPING'
  LEFT JOIN supplier_offers o ON o.offer_id=e.selected_offer_id
  LEFT JOIN vendors v ON v.vendor_id=o.vendor_id;

CREATE OR REPLACE VIEW v_selected_standard_supplier_offers AS
SELECT *
  FROM v_supplier_offer_selection_diagnostics
 WHERE selection_state IN
    ('SELECTED_ACTIVE','SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION');

CREATE OR REPLACE VIEW v_supplier_offer_selection_shadow AS
WITH legacy AS (
    SELECT o.variant_id,count(*) AS active_standard_count,
           min(o.offer_id) AS only_active_standard_offer_id
      FROM supplier_offers o
     WHERE o.active AND o.package_type='STANDARD'
     GROUP BY o.variant_id
)
SELECT d.*,
       COALESCE(l.active_standard_count,0) AS legacy_active_standard_count,
       CASE
         WHEN d.selection_state='EXPLICITLY_CLEARED' THEN 'HEAD_CLEARED'
         WHEN d.selection_state NOT IN
              ('SELECTED_ACTIVE','SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION')
              THEN 'HEAD_STALE_OR_INELIGIBLE'
         WHEN NOT d.offer_active THEN 'SELECTED_INACTIVE_NO_LEGACY_CHANGE'
         WHEN l.active_standard_count=1
              AND l.only_active_standard_offer_id=d.selected_offer_id THEN 'MATCH'
         WHEN l.active_standard_count=0 THEN 'LEGACY_HAS_NO_ACTIVE_STANDARD'
         WHEN l.active_standard_count>1 THEN 'LEGACY_HAS_MULTIPLE_ACTIVE_STANDARD'
         ELSE 'DIFFERENT_ACTIVE_STANDARD'
       END AS shadow_comparison
  FROM v_supplier_offer_selection_diagnostics d
  LEFT JOIN legacy l ON l.variant_id=d.variant_id;

CREATE OR REPLACE FUNCTION assert_persistent_mapping_foundation_contract()
RETURNS VOID
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    IF to_regclass('public.supplier_mapping_review_batches') IS NULL
       OR to_regclass('public.supplier_mapping_review_candidates') IS NULL
       OR to_regclass('public.supplier_mapping_decisions') IS NULL
       OR to_regclass('public.supplier_offer_selection_events') IS NULL
       OR to_regclass('public.supplier_offer_selection_heads') IS NULL
       OR to_regclass('public.v_selected_standard_supplier_offers') IS NULL
       OR to_regclass('public.v_supplier_offer_selection_shadow') IS NULL THEN
        RAISE EXCEPTION 'persistent mapping foundation object is absent';
    END IF;
    IF current_setting(
           'procurement.persistent_mapping_foundation_initial_install',true
       )='true' AND (
       EXISTS (SELECT 1 FROM supplier_mapping_review_batches)
       OR EXISTS (SELECT 1 FROM supplier_mapping_review_candidates)
       OR EXISTS (SELECT 1 FROM supplier_mapping_decisions)
       OR EXISTS (SELECT 1 FROM supplier_offer_selection_events)
       OR EXISTS (SELECT 1 FROM supplier_offer_selection_heads)
    ) THEN
        RAISE EXCEPTION 'migration must not adopt or synthesize mapping authority';
    END IF;
    IF supplier_mapping_policy_is_published(NULL,NULL,NULL,NULL) THEN
        RAISE EXCEPTION 'absent mapping policy must fail closed';
    END IF;
END
$$;

SELECT assert_persistent_mapping_foundation_contract();
INSERT INTO meta(key,value)
VALUES ('persistent_mapping_foundation_contract','v1-shadow-only')
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now();
```

### SQL review notes that control implementation

- `jsonb::text` is the database canonical form used by the proposed hash
  helpers. Application payload builders must generate the same JSONB object and
  compare the database-returned hash; they must not hash language-specific JSON
  serialization.
- `ABSENT` and `EXPLICIT_NULL` both require a SQL null value but remain distinct
  in their typed state columns and canonical JSON. `VALUE` requires a value.
  Source payload, owner clarifications, relationships, component membership,
  source locator/pages, and historical capture scope remain immutable.
- Candidate Variant/vendor references are deliberately untrusted text/UUID.
  Only a decision uses foreign keys, and the insert guard requires the reviewed
  values to equal a fresh candidate plus fresh catalog/vendor fingerprints.
- `supplier_mapping_policy_is_published` returns false for every input. A later
  policy migration may replace it only after an owner-approved publication
  table, evidence classes, evaluator, roles, and tests exist. There is no
  approved default.
- Selection is a shadow authority projection only. The two protection triggers
  prevent first-slice code from activating a mapped offer or pricing an inactive
  mapping result. A later activation/pricing change must replace those guards
  explicitly rather than bypass them.
- No table has `ON DELETE CASCADE`. Review, decision, rejection, selection, and
  operational identity histories remain durable.
- Existing `variants`, `vendors`, `supplier_offers`, `supplier_aliases`,
  `mapping_rejections`, `prices`, and artifact storage remain the only
  canonical contracts for their facts. This schema stores an opaque durable
  artifact reference plus its hashes rather than copying the source blob. It
  neither creates a catalog/price engine nor writes a convenience alias.

## 5. Exact service and transaction boundaries

No public route is part of this slice. Implement these internal domain methods
only after the authority/config changes are approved:

### `intake_supplier_mapping_review(request, principal)`

1. Start `SERIALIZABLE`; set the four `procurement.*` transaction-local GUCs
   from a server-verified named-human principal and authorized intake role.
2. Re-run the supported V5 reader without changing the sealed package. Verify
   the artifact/root/seal/relationship/batch/payload hashes, readiness states,
   zero-authority flags, page bounds, prerequisites, and candidate-set digest.
3. Look up `intake_idempotency_key`. If present and `payload_sha256` is exactly
   equal, return the existing IDs; if different, raise `IDEMPOTENCY_CONFLICT`.
4. Insert one batch and all candidates in that same transaction. Commit-time
   count/digest validation makes a short or altered set roll back completely.
5. Return evidence IDs only. Do not call mapping, pricing, recommendation,
   Shopify, supplier, order, or file-mutating services.

### `record_supplier_mapping_decision(request, principal_or_policy)`

1. Start `SERIALIZABLE`; for the first release accept only a server-verified
   human principal. Set transaction-local authorization context. Policy origin
   is modeled but the database stub always refuses it.
2. Resolve identical idempotent replay before mutation; same key/different
   payload fails. Lock the decision scope, operational-offer key, batch,
   candidate, catalog Variant, vendor, rejection memory, and relevant offers.
3. Recompute every expected hash. An approval with altered/missing evidence,
   blockers, a simulation, wrong Variant/vendor, or active rejection fails.
4. For approval, compute the supplier-identity, operational-offer, and
   candidate-decision keys in PostgreSQL from their versioned field sets. The
   decision scope is one exact candidate, so repeated printed occurrences may
   each retain a decision while their shared operational key still requires a
   single offer. If
   any historical approval already links that key, require its exact offer and
   contract. Otherwise either link an exact existing offer or insert one
   inactive offer. Supplier-code reuse with a different contract always creates
   an inactive row; never update or deactivate the old row.
5. For rejection, insert a `mapping_rejections` row using source key
   `persistent-mapping:<supplier_identity_key_sha256>` and link it. For defer,
   create neither offer nor rejection.
6. Insert the append-only decision last. A late trigger failure rolls back an
   inserted offer/rejection. Commit. Do not touch aliases, prices, heads,
   recommendations, Shopify, POs, artifacts, or the sealed package.

### `record_routine_offer_selection(request, principal)`

1. Start a new `SERIALIZABLE` transaction and require a new idempotency key,
   preview hash, confirmation hash, and the exact prior event/version. The same
   owner may act again, but the mapping confirmation is not reusable.
2. Set the server-derived named-human authorization context; lock the Variant
   advisory key and current head.
3. Recompute catalog, vendor, offer, mapping, and rejection fingerprints. A
   `SELECT` requires the current effective approval, `REGULAR`/`STANDARD`, exact
   supplier code, eligible Variant/vendor, valid dates, and no rejection. An
   inactive target must be the fresh unpriced `CREATED_INACTIVE` result.
4. Insert the event, then advance the head using one conditional statement:

   ```sql
   INSERT INTO supplier_offer_selection_heads(
       variant_id,selection_scope,selection_event_id,head_version,updated_txid
   ) VALUES ($variant_id,'ROUTINE_PROCUREMENT_STANDARD',$event_id,1,txid_current())
   ON CONFLICT (variant_id,selection_scope) DO UPDATE
      SET selection_event_id=EXCLUDED.selection_event_id,
          head_version=supplier_offer_selection_heads.head_version+1,
          updated_at=clock_timestamp(),updated_txid=txid_current()
    WHERE supplier_offer_selection_heads.selection_event_id=$expected_prior_event_id
      AND supplier_offer_selection_heads.head_version=$expected_prior_head_version
   RETURNING head_version;
   ```

   Require exactly one returned row. A concurrent loser or stale preview rolls
   back its event completely; the deferred trigger prevents an event without a
   head.
5. Commit without updating `supplier_offers`, `prices`, recommendations,
   Shopify, POs, or orders.

Read methods may query the two new views but must label them `SHADOW ONLY` and
must not substitute them into recommendation execution.

## 6. Authorization boundary without invented configuration

The database stores opaque principal, role, authentication-context, policy,
publication, and evidence fingerprints. It does not name an identity provider
or grant a role. Client-entered actor strings and the existing shared review
tokens cannot populate the transaction-local human context.

Before any persistent intake/decision/selection route is exposed, a later
authorization change must:

- configure the private identity provider and named-account role assignments;
- derive `principal_ref`, `authorized_role_ref`, and authentication-context hash
  on the server from the verified session;
- authorize mapping and selection separately even if one owner has both roles;
- deny list/detail/evidence download before storage/DB access when no valid
  private session exists;
- ensure Shopify app credentials establish transport only, never human identity.

The unresolved IdP/role choice blocks route exposure and real writes; it does
not block schema construction, pure domain tests, or disposable-PostgreSQL
tests with explicitly labeled synthetic principals.

Policy approval additionally requires a real owner-published immutable policy,
published independent-linkage classes, a service principal, exact policy
version/publication hash, predicate version/result, and independently sourced
evidence-set hash. The false stub makes absence fail closed. Publishing the
policy/evaluator is a later implementation step, not a prerequisite for the
human-only schema slice.

## 7. Exact proposed authority and configuration amendments

These diffs are proposals for the **next implementation change**, not changes
made by this branch. They are limited to mapping intake/decision/selection and
shadow reads.

### Canonical specification proposal

Insert the following immediately after canonical section `G. Human intelligence
that must NEVER disappear`:

```diff
+Persistent supplier mapping authority
+
+A printed source occurrence, an operational supplier offer, a mapping
+decision, and the selected routine offer are separate records. Printed tiers
+or repeated occurrences do not automatically create duplicate operational
+offers. Supplier-code reuse never overwrites a historical offer identity.
+
+Review intake is immutable evidence and carries no mapping, selection, price,
+Shopify, supplier-contact, or order authority. A sealed review package remains
+unapproved evidence; a later application decision references it without
+rewriting package approval/import flags.
+
+Mapping decisions are append-only APPROVE_MAPPING, REJECT_MAPPING, or DEFER
+events. Human approval requires a server-verified named principal and a
+separate exact confirmation. Policy approval requires a service principal,
+an owner-published immutable policy/version, independently corroborated
+deterministic evidence, and exact fingerprints. Missing policy fails closed.
+Fuzzy similarity is supporting evidence only and can never authorize mapping.
+
+An approved mapping may create an inactive supplier offer. It never activates
+that offer, verifies or activates price, selects a routine offer, writes
+Shopify, or authorizes an order. One append-only selection event and narrow
+ROUTINE_PROCUREMENT_STANDARD head may separately choose one regular offer per
+Shopify Variant. The same named owner may map and select only through separate
+previews, confirmations, idempotency keys, and events. Alternatives and
+explicit reviewed nulls remain preserved.
+
+The selected-offer view is shadow-only until a separately approved
+recommendation cutover. Existing active-offer recommendation semantics remain
+unchanged. Cost, retail-price, and primary-SKU synchronization remain three
+separately controlled later capabilities.
```

Do not change canonical section 11's price lifecycle in this slice. The
supplier-specific last-approved-book carry-forward and scoped deal-overlay
amendments remain required for the later price-authority change; mixing them
into this first mapping schema would enlarge the authority surface.

### `rules.toml` proposal

Add the one fail-closed matching statement and a disabled new section:

```diff
 [matching]
 sku_first = true
 accepted_alias_second = true
 negative_mapping_memory = true
 auto_match_min_score = 0.92
 review_min_score = 0.82
 size_conflict_blocks_auto_match = true
 pack_conflict_blocks_auto_match = true
 fuzzy_is_supporting_evidence_only = true
+fuzzy_can_authorize = false
+
+[persistent_mapping]
+contract_version = "v1-shadow-only"
+routine_selection_scope = "ROUTINE_PROCUREMENT_STANDARD"
+review_intake_writes_enabled = false
+human_mapping_writes_enabled = false
+policy_mapping_writes_enabled = false
+routine_selection_writes_enabled = false
+selected_offer_shadow_reads_enabled = false
+recommendation_cutover_enabled = false
+offer_activation_enabled = false
```

Do not toggle these flags in the migration. Enabling intake, human decisions,
selection, or shadow reads requires the named-identity boundary and separate
owner authorization. Policy remains disabled until publication/evidence policy
exists. Offer activation and recommendation cutover remain false in this slice.

No first-slice diff changes `selling_price_auto_update`,
`auto_write_supplier_sku_to_shopify`, pricing rollover/archive flags, price
cadence, deal overlays, or supplier communication/order settings. Those belong
to later scoped changes.

## 8. Acceptance matrix and planned test registration

Every result below is future acceptance work, not evidence executed by this
documentation task. `PG` means a disposable PostgreSQL database; `PURE` means
no database/network. Synthetic principals and packages must be labeled
simulation in test output.

| Invariant/case | Boundary | Later executable test and required result |
|---|---|---|
| Exact predecessor only | Migration/PG | `test_upgrade_requires_exact_013_marker_set_and_contracts`: 012-only, unknown intervening marker, missing index/trigger, or altered contract refuses with no new object |
| Fresh schema | Migration/PG | `test_fresh_schema_applies_foundation_once_and_reapplies_idempotently`: full true chain plus proposed migration installs exact objects/marker and a second apply is unchanged |
| Historical upgrade | Migration/PG | `test_exact_013_upgrade_preserves_all_legacy_bytes_and_counts`: build through actual 013 files, seed legacy rows, snapshot table digests and recommendation output, then apply; new authority tables are empty |
| Failed migration/late validation | Migration/PG | `test_migration_failure_and_late_validation_roll_back_every_object`: precondition failure and an injected failure after the final assertion both leave the predecessor byte/count snapshot and schema unchanged |
| Late domain validation | Mapping + selection/PG | `test_late_decision_or_head_validation_rolls_back_the_whole_transaction`: a failure after inserting an offer/rejection or event but before commit leaves none of those rows and no head change |
| Valid intake, zero authority effects | Intake/PG | `test_valid_intake_adds_only_immutable_batch_and_candidates`: exact counts; zero decisions, heads, events, prices, offer changes, Shopify calls, POs, orders, supplier effects |
| Intake idempotency | Intake service/PG | `test_intake_exact_replay_returns_existing_and_payload_change_conflicts`: same key/hash returns IDs; same key/different payload changes nothing |
| Altered/missing evidence | Reader + intake/PG | `test_intake_rejects_missing_or_altered_source_hash_page_and_prerequisite`: each changed artifact/root/seal/table/page-bound input rolls back fully |
| Explicit null versus absent | Intake + DB/PG | `test_candidate_preserves_explicit_null_and_absent_states`: states and canonical hashes differ, values remain SQL null, replay is stable |
| Printed repeats/tiers | Intake + mapping/PG | `test_repeated_occurrences_and_tiers_share_one_operational_offer`: candidates remain distinct while equal operational keys link one offer ID |
| Offer class separation | Mapping/PG | `test_regular_gift_special_alternate_component_and_combo_do_not_collapse`: distinct keys/offers; component relationships remain exact; only regular can be selected |
| Supplier-code reuse | Mapping + offer/PG | `test_reused_supplier_code_preserves_old_offer_and_creates_inactive_history`: no old-row update; active uniqueness stays valid; policy path refuses |
| Wrong Variant/vendor | Mapping/PG | `test_mapping_refuses_wrong_variant_or_vendor_and_stale_fingerprints`: FK-valid but candidate-inconsistent references fail with no offer/decision |
| Append-only intake/decisions/events | DB/PG | `test_authority_history_rejects_update_delete_and_cascade`: each UPDATE/DELETE fails and every row remains |
| Exact decision replay | Mapping service/PG | `test_mapping_exact_replay_and_same_key_different_payload`: exact replay returns existing; changed payload has no partial offer/rejection |
| Stale mapping preview/prior | Mapping/PG | `test_mapping_rejects_stale_preview_and_stale_or_forked_prior`: changed catalog/vendor/rejection/candidate or non-tip predecessor fails |
| Human identity | Authorization + mapping/PG | `test_mapping_requires_server_named_human_context`: absent/mismatched GUC, client actor, or shared token cannot insert; verified synthetic context can |
| Policy fail closed | Authorization + mapping/PG | `test_policy_mapping_requires_published_policy_and_independent_evidence`: false stub rejects every policy event and creates no approved default |
| Approval lifecycle | Mapping/PG | `test_mapping_approval_creates_inactive_unpriced_unselected_offer`: decision/offer commit atomically; zero price/head/recommendation effects |
| Distinct selection | Selection/PG | `test_valid_mapping_then_separate_selection_requires_second_confirmation`: mapping confirmation/idempotency cannot be reused; selection advances only its head |
| Explicit reviewed null | Selection/PG | `test_clear_appends_event_and_preserves_prior_selection`: CLEAR is a head event, not deletion or offer deactivation |
| Stale selection preview/head | Selection/PG | `test_selection_rejects_stale_offer_catalog_vendor_rejection_and_prior_head`: each mismatch rolls back event/head |
| Concurrent selection | Selection/PG | `test_concurrent_selection_attempts_commit_one_complete_winner`: two SERIALIZABLE transactions from one prior head produce one event/head; loser leaves no partial event |
| Inactive selection lifecycle | Selection + views/PG | `test_inactive_selected_offer_remains_inactive_unpriced_and_shadow_labelled`: head may select only fresh mapped inactive offer; no active/price mutation; exact state label |
| Active legacy selection | Selection + views/PG | `test_existing_active_offer_selection_does_not_mutate_offer_or_price`: exact active row may be selected; table digests outside authority objects stay fixed |
| Rejection memory | Mapping + selection/PG | `test_active_rejection_and_conflicting_evidence_block_approval_and_selection`: rejection persists; conflicting source never silently reactivates |
| Mapped/priced/reference protections | Offer/PG | `test_mapped_priced_and_referenced_offer_contracts_cannot_be_rewritten`: new and existing migration 010/011 guards all remain effective |
| V5 non-adoption | Migration + intake/PG | `test_unapproved_v5_package_is_never_backfilled_or_relabelled`: migration creates zero rows; intake preserves fixed NOT_APPROVED/NOT_IMPORT_READY flags |
| Legacy recommendations | Recommendation shadow/PG | `test_legacy_recommendations_are_identical_until_cutover`: before/after outputs and blocker semantics are exact; `_load_context` does not query new views |
| Zero external/operational effects | Service boundary/PURE | `test_mapping_foundation_has_no_shopify_price_order_supplier_or_artifact_mutator`: dependency injection sentinels record zero calls for intake/map/select/error/replay paths |
| Authority/config diff | Static/PURE | `test_mapping_authority_and_disabled_flags_match_approved_contract`: exact canonical text and false flags; no price/Shopify/carry-forward flags changed |
| Registration floor | Test runner/PURE | `test_persistent_mapping_modules_are_registered_at_exact_discovery_floors`: removal of one planned module/method trips its module floor and the sum-derived global floor |

This matrix deliberately names 29 PostgreSQL test methods and three pure/static
methods. Planned files and initial module floors are
`procurement/tests/test_persistent_mapping_foundation_postgres.py` at 29 and
`procurement/tests/test_persistent_mapping_foundation_contract.py` at 3. The
registration-floor test lives in the latter and imports the runner seams. At
the implementation checkpoint, add both modules to `TEST_MODULES` and
`REQUIRED_MODULE_MINIMUMS`; if implementation adds any method, raise that
module's floor to its actual discovered count, never below the counts above,
and keep
`GLOBAL_MINIMUM_TESTS=sum(REQUIRED_MODULE_MINIMUMS.values())`. Do not guess a
replacement count or lower another floor to absorb the new tests.

Fresh-schema validation must run the actual complete file chain. Upgrade-path
validation must stop at the exact intended predecessor and then apply the new
file; a consolidated `schema_postgres.sql` that already contains these objects
is not an upgrade test. PostgreSQL, identity, package, and concurrency fixtures
are simulations unless real evidence is separately authorized. No live/private
or Shopify result may be reported from this matrix without executing it.
For positive-path database tests, `source_is_simulation=false` means only that
the fixture is exercising the authoritative-format branch of the contract; the
test report must still label the package, principal, and result as synthetic and
must not describe them as a real owner approval or private-package replay.

## 9. Retained owner requirements and unresolved dependencies

The first slice retains one Variant-wide primary regular selection without
deleting alternatives; separate mapping and selection confirmations by the
same authenticated owner; human versus policy provenance; independently
corroborated deterministic matching with no fuzzy authority; and complete
source/rejection/null/relationship/history evidence. The reviewed design's
supplier-specific last-approved-book carry-forward, scoped deal overlays, and
separate cost/retail/primary-SKU controls remain binding later requirements,
not schema shortcuts here.

| Unresolved detail | Does not block | Actually blocks |
|---|---|---|
| Private IdP and named-role assignments | DDL, immutable model, pure and disposable-PG tests | Private route exposure, real intake, human mapping/selection writes |
| Owner-published independent initial-linkage evidence classes | Human-only schema and review UI planning | Policy publication/evaluator and every `POLICY_APPROVED` event |
| Whether a later service policy may execute eligible SKU requests unattended | Entire mapping foundation, price work, owner-confirmed first SKU release | Only a future unattended Shopify SKU executor |
| Supplier/book cadence, validity, and price-scope configuration | Mapping intake/decision/selection schema | Price schedule policies, carry-forward, replacement, and deal-overlay execution |

The six answered owner policy questions and Shopify InventoryItem cost
destination are not reopened. Nothing here grants permissions, selects an IdP,
publishes evidence policy, invents supplier schedules, enables unattended
execution, or approves a real mapping.

## 10. Exact next implementation boundary

After the dependency sequence is integrated and owner authorization is given,
the recommended next change is only:

1. apply the approved first-slice canonical/config amendments with every new
   capability flag false;
2. assign the next migration number after rechecking the exact chain, implement
   the five tables, functions/triggers, empty shadow views, and no-backfill
   contract above;
3. implement internal intake/mapping/selection domain services behind no public
   route, with policy hard-disabled;
4. add and register the two planned test modules; prove fresh and exact-013
   upgrades, rollback, idempotency, concurrency, immutability, and zero effects;
5. obtain independent backend/data review and stop for owner acceptance.

Explicitly excluded are identity-provider selection/configuration, public or
private routes, policy publication/execution, pricing lifecycle, carry-forward,
deal overlays, offer activation, recommendation cutover, Shopify reads/writes,
OAuth scope changes, supplier communication, PO/order actions, production DB
access, deployment, and `CURRENT` activation.

This document is the completed construction specification only. Its SQL and
diffs remain proposals until a new authorization names an approved integrated
target and migration predecessor.

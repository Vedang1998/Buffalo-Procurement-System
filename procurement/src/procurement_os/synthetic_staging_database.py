"""Versioned least-privilege contract for the synthetic staging database.

The accepted local fixture is immutable.  This module defines the one reviewed
catalog transition which may be applied to a restored copy of that fixture.
Ordinary application startup only calls the read-only attestation path.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from psycopg import sql


CONTRACT_VERSION = "BUFFALO_SYNTHETIC_STAGING_DATABASE_V2"
SCHEMA = "qa_mapping_test"
LEGACY_OWNER = "qa_mapping_owner"
LEGACY_LOGIN = "qa_release_login"
OBJECT_OWNER = "buffalo_synthetic_owner"
PROVISIONER = "buffalo_synthetic_provisioner"
RUNTIME_LOGIN = "buffalo_synthetic_runtime"
# The suffix is part of the accepted immutable price-policy provenance.  A
# restore to another database name would invalidate that protected evidence.
EXPECTED_DATABASE = "buffalo_synthetic_staging_demo"
EXPECTED_POSTGRES_MAJOR = 16
FIXTURE_CONTRACT = "BUFFALO_SYNTHETIC_OWNER_DEMO_V1"
MULTIVENDOR_FIXTURE_CONTRACT = "BUFFALO_SYNTHETIC_MULTIVENDOR_ACCEPTANCE_V2"
DEVELOPMENT_FIXTURE_CONTRACT = "BUFFALO_SYNTHETIC_DEVELOPMENT_FORECAST_V2"
DEVELOPMENT_FIXTURE_PROFILE = "development-forecast-v2"
FIXTURE_BUSINESS_DATE = "2026-10-05"
FIXTURE_SALES_BACKFILL_ID = "cdf2ca35-1038-4ae5-92e2-265976b1587e"
IMMUTABLE_FIXTURE_MANIFEST_SHA256 = (
    "08fd401f70ad55f7957b09bd47e99797a648d1f7ba2c0c86e24f5d0034683ec1"
)

LEGACY_MARKERS: Mapping[str, str] = {
    "migration:014_persistent_mapping_foundation.sql": (
        "sha256:80c5d6c0a0299edf8d04f9c5f684f9cea277494b894fa0feb0a387476bfa8c86"
    ),
    "persistent_mapping_foundation_contract": "v1-shadow-only",
    "persistent_mapping_foundation_catalog_sha256": (
        "5a9fff00c1d62c2de89ca1dc27d4e264def12eb726ab249a9ab86a9fc529c72e"
    ),
    "migration:015_monday_forecast_v2_retirement.sql": (
        "sha256:e3f69e23cf6fce0760add5ec6a329426444dd44b681e338aded9d833e89ba56b"
    ),
    "monday_forecast_v2_retirement_contract": "v1",
    "monday_forecast_v2_retirement_catalog_sha256": (
        "2fafe14a6dd9394fbebb471f84b768f77bd9e2675cd8e3746576a8a9d3099e7d"
    ),
    "migration:016_synthetic_price_replacement.sql": (
        "sha256:5d74074b7c2f79eb8e9b92bbb3302923c80003b6bce25803671078b0645ea35d"
    ),
    "synthetic_price_replacement_contract": (
        "v1-complete-vendor-monthly-synthetic"
    ),
    "synthetic_price_replacement_catalog_sha256": (
        "00e01c90bdc324544b2746880d5c8d6821dad3a6b1698b761a78d91c8b9c1a88"
    ),
}
LEGACY_RETIREMENT_CATALOG_MARKERS = frozenset(
    {
        LEGACY_MARKERS["monday_forecast_v2_retirement_catalog_sha256"],
        "0d151f70f6eec2965428e0bec64ab573962a8aad344b14a9d44332edff284cd9",
    }
)

IMMUTABLE_FIXTURE_RELATIONS: Mapping[str, tuple[str, ...]] = {
    "daily_inventory_snapshots": ("inventory_snapshot_run_id",),
    "inventory_snapshot_run_rows": ("inventory_snapshot_run_id",),
    "inventory_snapshot_runs": ("inventory_snapshot_run_id",),
    "sales_daily": (),
    "supplier_offers": ("created_at",),
    "supplier_price_schedule_policies": ("created_at", "created_txid"),
    "variant_policies": ("created_at",),
    "variants": (
        "variant_created_at",
        "last_synced_at",
        "catalog_last_seen_at",
    ),
    "vendor_operating_rules": ("confirmed_at", "updated_at"),
    "vendors": ("updated_at",),
}

PGCRYPTO_ROUTINES = (
    "armor(bytea)",
    "armor(bytea,text[],text[])",
    "crypt(text,text)",
    "dearmor(text)",
    "decrypt(bytea,bytea,text)",
    "decrypt_iv(bytea,bytea,bytea,text)",
    "digest(bytea,text)",
    "digest(text,text)",
    "encrypt(bytea,bytea,text)",
    "encrypt_iv(bytea,bytea,bytea,text)",
    "gen_random_bytes(integer)",
    "gen_random_uuid()",
    "gen_salt(text)",
    "gen_salt(text,integer)",
    "hmac(bytea,bytea,text)",
    "hmac(text,text,text)",
    "pgp_armor_headers(text)",
    "pgp_key_id(bytea)",
    "pgp_pub_decrypt(bytea,bytea)",
    "pgp_pub_decrypt(bytea,bytea,text)",
    "pgp_pub_decrypt(bytea,bytea,text,text)",
    "pgp_pub_decrypt_bytea(bytea,bytea)",
    "pgp_pub_decrypt_bytea(bytea,bytea,text)",
    "pgp_pub_decrypt_bytea(bytea,bytea,text,text)",
    "pgp_pub_encrypt(text,bytea)",
    "pgp_pub_encrypt(text,bytea,text)",
    "pgp_pub_encrypt_bytea(bytea,bytea)",
    "pgp_pub_encrypt_bytea(bytea,bytea,text)",
    "pgp_sym_decrypt(bytea,text)",
    "pgp_sym_decrypt(bytea,text,text)",
    "pgp_sym_decrypt_bytea(bytea,text)",
    "pgp_sym_decrypt_bytea(bytea,text,text)",
    "pgp_sym_encrypt(text,text)",
    "pgp_sym_encrypt(text,text,text)",
    "pgp_sym_encrypt_bytea(bytea,text)",
    "pgp_sym_encrypt_bytea(bytea,text,text)",
)
PGCRYPTO_DEFINITION_SHA256 = (
    "de59d5a5df4ff6bcca818c44b428e8adc07ca6851d6353030f5d742e9b29c0b3"
)

# The gateway exposes reviewed reads across the synthetic operational UI.  The
# list is the complete accepted 001-016 application relation/view inventory;
# an added relation is unlisted and therefore inaccessible until a new contract.
APPLICATION_RELATIONS = (
    "catalog_reconciliation_items",
    "catalog_sync_runs",
    "change_log",
    "combo_components",
    "combo_usage",
    "combos",
    "daily_inventory_snapshots",
    "events",
    "exceptions",
    "forecast_results",
    "historical_sales_exclusion_authority_runs",
    "historical_sales_exclusions",
    "historical_sales_review_decisions",
    "identity_investigations",
    "inventory_snapshot_run_rows",
    "inventory_snapshot_runs",
    "inventory_snapshots",
    "legacy_price_seed_events",
    "manual_overrides",
    "mapping_rejections",
    "meta",
    "monday_material_edit_confirmations",
    "monday_packet_build_events",
    "monday_run_artifacts",
    "monday_run_blocker_exclusions",
    "monday_stale_forecast_retirements",
    "po_operational_events",
    "po_reconciliation_events",
    "price_book_batches",
    "price_book_disposition_events",
    "price_book_promotion_events",
    "price_book_scope_memberships",
    "price_book_staging_rows",
    "price_book_validation_issues",
    "prices",
    "procurement_recommendations",
    "purchase_order_lines",
    "purchase_orders",
    "readiness_gates",
    "review_decisions",
    "run_price_snapshots",
    "runs",
    "sales_backfill_chunks",
    "sales_backfill_pages",
    "sales_backfill_run_facts",
    "sales_backfill_runs",
    "sales_daily",
    "seed_import_records",
    "shopify_sales_daily_raw",
    "supplier_aliases",
    "supplier_mapping_decisions",
    "supplier_mapping_review_batches",
    "supplier_mapping_review_candidates",
    "supplier_offer_selection_events",
    "supplier_offer_selection_heads",
    "supplier_offers",
    "supplier_price_authority_events",
    "supplier_price_authority_heads",
    "supplier_price_schedule_policies",
    "variant_aliases",
    "variant_policies",
    "variants",
    "vendor_operating_rules",
    "vendor_rule_revisions",
    "vendors",
    "v_current_prices",
    "v_effective_supplier_mapping_decisions",
    "v_future_prices",
    "v_latest_procurement_inventory",
    "v_open_procurement_incoming",
    "v_operational_daily_inventory_snapshots",
    "v_operational_inventory_snapshots",
    "v_operational_variants",
    "v_procurement_daily_inventory_snapshots",
    "v_selected_standard_supplier_offers",
    "v_supplier_offer_selection_diagnostics",
    "v_supplier_offer_selection_shadow",
    "v_verified_current_prices",
    "v_verified_future_prices",
)

# These objects are present in the accepted 001-016 catalog but are not read by
# an enabled staging route, the canonical V2 browser workflow, or its health
# checks.  Keeping the deny list explicit makes catalog growth fail closed.
NON_RUNTIME_READ_RELATIONS = frozenset(
    {
        "combo_components",
        "combo_usage",
        "combos",
        "events",
        "historical_sales_exclusion_authority_runs",
        "historical_sales_exclusions",
        "manual_overrides",
        "supplier_aliases",
        "v_current_prices",
        "v_future_prices",
        "v_latest_procurement_inventory",
        "v_open_procurement_incoming",
        "v_operational_daily_inventory_snapshots",
        "v_operational_inventory_snapshots",
        "v_operational_variants",
        "v_procurement_daily_inventory_snapshots",
        "v_selected_standard_supplier_offers",
        "v_supplier_offer_selection_diagnostics",
        "v_verified_future_prices",
    }
)
READ_RELATIONS = tuple(
    relation
    for relation in APPLICATION_RELATIONS
    if relation not in NON_RUNTIME_READ_RELATIONS
)

# These are the guarded mapping -> price -> recommendation -> review ->
# DRAFT/packet mutations exposed by the accepted synthetic UI.  Historical,
# catalog-maintenance, purchasing-transmission, and production writes remain
# outside the contract.
WRITE_RELATIONS: Mapping[str, tuple[str, ...]] = {
    "change_log": ("INSERT",),
    "exceptions": ("INSERT",),
    "forecast_results": ("INSERT",),
    "inventory_snapshots": ("INSERT",),
    "monday_material_edit_confirmations": ("INSERT",),
    "monday_packet_build_events": ("INSERT",),
    "monday_run_artifacts": ("INSERT",),
    "monday_run_blocker_exclusions": ("INSERT",),
    "monday_stale_forecast_retirements": ("INSERT",),
    "price_book_batches": ("INSERT", "UPDATE"),
    "price_book_promotion_events": ("INSERT",),
    "price_book_scope_memberships": ("INSERT",),
    "price_book_staging_rows": ("INSERT", "DELETE"),
    "price_book_validation_issues": ("INSERT", "UPDATE"),
    "prices": ("INSERT", "UPDATE", "DELETE"),
    "procurement_recommendations": ("INSERT",),
    "purchase_order_lines": ("INSERT",),
    "purchase_orders": ("INSERT", "UPDATE"),
    "review_decisions": ("INSERT",),
    "run_price_snapshots": ("INSERT",),
    "runs": ("INSERT", "UPDATE"),
    "supplier_mapping_review_batches": ("INSERT",),
    "supplier_mapping_review_candidates": ("INSERT",),
    "supplier_mapping_decisions": ("INSERT",),
    "supplier_offer_selection_events": ("INSERT",),
    "supplier_offer_selection_heads": ("INSERT", "UPDATE"),
    "supplier_price_authority_events": ("INSERT",),
    "supplier_price_authority_heads": ("UPDATE",),
}

SEQUENCE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "change_log_change_id_seq": ("USAGE",),
    "exceptions_exception_id_seq": ("USAGE",),
    "monday_material_edit_confirma_material_edit_confirmation_id_seq": ("USAGE",),
    "monday_run_artifacts_monday_run_artifact_id_seq": ("USAGE",),
    "monday_run_blocker_exclusions_exclusion_id_seq": ("USAGE",),
    "price_book_batches_batch_generation_seq": ("USAGE",),
    "price_book_promotion_events_price_book_promotion_event_id_seq": ("USAGE",),
    "price_book_staging_rows_price_book_staging_row_id_seq": ("USAGE",),
    "price_book_validation_issues_price_book_validation_issue_id_seq": ("USAGE",),
    "prices_price_id_seq": ("USAGE",),
    "procurement_recommendations_recommendation_id_seq": ("USAGE",),
    "purchase_order_lines_po_line_id_seq": ("USAGE",),
    "review_decisions_decision_id_seq": ("USAGE",),
    "run_price_snapshots_run_price_snapshot_id_seq": ("USAGE",),
}

# Exact signatures include the direct Python calls and their SQL call-graph
# closure. Trigger functions themselves do not require EXECUTE at trigger time.
EXECUTE_ROUTINES = (
    "assert_synthetic_staging_contract()",
    "compute_synthetic_staging_catalog_sha256()",
    "compute_synthetic_staging_persistent_mapping_sha256()",
    "digest(bytea,text)",
    "gen_random_uuid()",
    "is_operational_current_variant(text)",
    "is_procurement_eligible_variant(text)",
    "is_internal_draft_po(uuid)",
    "monday_artifact_set_sha256(uuid)",
    "monday_edit_materiality(bigint,numeric)",
    "monday_effective_material_blocker_count(uuid)",
    "monday_po_reconciliation_rollup(uuid)",
    "monday_stale_forecast_retirement_confirmation_sha256(uuid,text,text,text,text)",
    "persistent_mapping_assert_safe_role_topology()",
    "persistent_mapping_candidate_decision_scope(qa_mapping_test.supplier_mapping_review_candidates)",
    "persistent_mapping_candidate_evidence_set_sha256(qa_mapping_test.supplier_mapping_review_candidates)",
    "persistent_mapping_candidate_operational_offer_key(qa_mapping_test.supplier_mapping_review_candidates)",
    "persistent_mapping_candidate_reviewed_facts(qa_mapping_test.supplier_mapping_review_candidates)",
    "persistent_mapping_candidate_supplier_identity_key(qa_mapping_test.supplier_mapping_review_candidates)",
    "persistent_mapping_catalog_fingerprint(text)",
    "persistent_mapping_decision_confirmation_sha256(qa_mapping_test.supplier_mapping_decisions)",
    "persistent_mapping_decision_preview_sha256(qa_mapping_test.supplier_mapping_decisions)",
    "persistent_mapping_decision_request_sha256(qa_mapping_test.supplier_mapping_decisions)",
    "persistent_mapping_json_sha256(jsonb)",
    "persistent_mapping_lock_supplier_offers(bigint[])",
    "persistent_mapping_offer_fingerprint(bigint)",
    "persistent_mapping_rejection_fingerprint(uuid,text,bigint)",
    "persistent_mapping_require_enabled_capability(text)",
    "persistent_mapping_require_human_context(text,text,text,text)",
    "persistent_mapping_selection_confirmation_sha256(qa_mapping_test.supplier_offer_selection_events)",
    "persistent_mapping_selection_preview_sha256(qa_mapping_test.supplier_offer_selection_events)",
    "persistent_mapping_text_sha256(text)",
    "persistent_mapping_vendor_fingerprint(uuid)",
    "phase4_assert_current_operational_variant(text,text)",
    "price_book_operational_semantic_md5(uuid)",
    "price_book_staging_semantic_md5(uuid)",
    "supplier_price_membership_sha256(uuid)",
    "supplier_price_require_enabled_capability(text)",
    "supplier_price_scope_batch_as_current_payload(uuid,uuid)",
    "supplier_price_scope_batch_as_current_sha256(uuid,uuid)",
    "supplier_price_scope_current_payload(uuid)",
    "supplier_price_scope_current_sha256(uuid)",
    "supplier_price_unaffected_state_sha256(uuid)",
    "synthetic_staging_lock_exception(bigint)",
    "synthetic_staging_lock_recommendation(bigint)",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def permission_records() -> tuple[dict[str, str], ...]:
    records: list[dict[str, str]] = []
    for relation in READ_RELATIONS:
        records.append(
            {
                "object": f"{SCHEMA}.{relation}",
                "operation": "SELECT",
                "reason": "reviewed synthetic gateway read route",
                "service": "synthetic-worker",
            }
        )
    for relation, operations in WRITE_RELATIONS.items():
        for operation in operations:
            records.append(
                {
                    "object": f"{SCHEMA}.{relation}",
                    "operation": operation,
                    "reason": "guarded synthetic DRAFT workflow",
                    "service": "synthetic-worker",
                }
            )
    for sequence, operations in SEQUENCE_PRIVILEGES.items():
        for operation in operations:
            records.append(
                {
                    "object": f"{SCHEMA}.{sequence}",
                    "operation": operation,
                    "reason": "identity allocation for an allowed insert",
                    "service": "synthetic-worker",
                }
            )
    for routine in EXECUTE_ROUTINES:
        records.append(
            {
                "object": f"{SCHEMA}.{routine}",
                "operation": "EXECUTE",
                "reason": "reviewed query/assertion call graph",
                "service": "synthetic-worker",
            }
        )
    return tuple(records)


PERMISSION_MATRIX_SHA256 = hashlib.sha256(_canonical(permission_records())).hexdigest()

# Derived only from the reviewed transition applied to an independently
# initialized accepted fixture.  Placeholders are replaced before publication;
# production code refuses to run while either is not a SHA-256 value.
PREDECESSOR_CATALOG_SHA256 = (
    "59ebe68a203511cb54d2d02d7c73ef44cb1ee3887f2c85effbaa08f7276cceda"
)
SUCCESSOR_CATALOG_SHA256 = (
    "af02588dee120940bd34a44c6d1fbc4b66062082eaeda36e268344ae992eaea3"
)
STAGING_PERSISTENT_MAPPING_CATALOG_SHA256 = (
    "7200d33604f1c4e6273a266eee403063c57c06e6f5a642835fd279d55b4a3dd4"
)
STAGING_RETIREMENT_CATALOG_SHA256 = (
    "bf3df233d9e3cc5f757076f755890cdfefca5eee5b8acecac208cc4eef27a61a"
)
STAGING_PRICE_CATALOG_SHA256 = (
    "17605263963002022223a06b7e7a32ce6838eeb3f1e714098406b43c9f5bcc30"
)


class SyntheticStagingDatabaseError(RuntimeError):
    """The connected database is not the reviewed synthetic staging target."""


@dataclass(frozen=True)
class SyntheticStagingTarget:
    database_url: str
    expected_private_host: str
    project_id: str
    environment_id: str
    app_service_id: str
    postgres_service_id: str
    owned_local_port: int | None = None

    def validate_static(self) -> None:
        from .staging_config import (
            EXPECTED_APP_SERVICE_ID,
            EXPECTED_ENVIRONMENT_ID,
            EXPECTED_POSTGRES_SERVICE_ID,
            EXPECTED_PROJECT_ID,
        )

        try:
            parsed = urlparse(self.database_url)
            parsed_port = parsed.port
        except ValueError as exc:
            raise SyntheticStagingDatabaseError(
                "synthetic staging database destination differs"
            ) from exc
        remote = self.owned_local_port is None
        host_ok = (
            parsed.hostname == self.expected_private_host
            and (
                bool(
                    re.fullmatch(
                        r"[a-z0-9.-]+\.railway\.internal",
                        self.expected_private_host,
                    )
                )
                if remote
                else self.expected_private_host in {"127.0.0.1", "::1"}
            )
        )
        port_ok = (
            parsed_port in {None, 5432}
            if remote
            else parsed_port == self.owned_local_port
        )
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not host_ok
            or not port_ok
            or parsed.username != RUNTIME_LOGIN
            or parsed.password is not None
            or parsed.path != f"/{EXPECTED_DATABASE}"
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SyntheticStagingDatabaseError(
                "synthetic staging database destination differs"
            )
        if (
            self.project_id != EXPECTED_PROJECT_ID
            or self.environment_id != EXPECTED_ENVIRONMENT_ID
            or self.app_service_id != EXPECTED_APP_SERVICE_ID
            or self.postgres_service_id != EXPECTED_POSTGRES_SERVICE_ID
        ):
            raise SyntheticStagingDatabaseError(
                "synthetic staging Railway scope differs"
            )


def target_from_environment(environment: Mapping[str, str]) -> SyntheticStagingTarget:
    """Construct the server-owned Railway target; browser fields are irrelevant."""

    required = {
        "DATABASE_URL",
        "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_SERVICE_ID",
        "BUFFALO_STAGING_POSTGRES_SERVICE_ID",
    }
    if not required.issubset(environment):
        raise SyntheticStagingDatabaseError(
            "synthetic staging destination configuration is incomplete"
        )
    owned_local_port: int | None = None
    raw_local_port = environment.get("BUFFALO_STAGING_OWNED_LOCAL_PORT")
    if raw_local_port is not None:
        if environment.get("BUFFALO_STAGING_LOCAL_ACCEPTANCE") != "1":
            raise SyntheticStagingDatabaseError(
                "synthetic staging local database authority is absent"
            )
        try:
            owned_local_port = int(raw_local_port)
        except (TypeError, ValueError) as exc:
            raise SyntheticStagingDatabaseError(
                "synthetic staging local database port is malformed"
            ) from exc
        if not 1 <= owned_local_port <= 65535:
            raise SyntheticStagingDatabaseError(
                "synthetic staging local database port is malformed"
            )
    target = SyntheticStagingTarget(
        database_url=str(environment["DATABASE_URL"]),
        expected_private_host=str(
            environment["BUFFALO_STAGING_POSTGRES_PRIVATE_HOST"]
        ),
        project_id=str(environment["RAILWAY_PROJECT_ID"]),
        environment_id=str(environment["RAILWAY_ENVIRONMENT_ID"]),
        app_service_id=str(environment["RAILWAY_SERVICE_ID"]),
        postgres_service_id=str(
            environment["BUFFALO_STAGING_POSTGRES_SERVICE_ID"]
        ),
        owned_local_port=owned_local_port,
    )
    target.validate_static()
    return target


def _role_flags(conn: Any, role: str) -> tuple[bool, ...]:
    row = conn.execute(
        "SELECT rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
        "rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=%s",
        (role,),
    ).fetchone()
    if row is None:
        raise SyntheticStagingDatabaseError("synthetic staging role is absent")
    return tuple(bool(value) for value in row)


def _require_source_hashes() -> None:
    if not all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (
            PREDECESSOR_CATALOG_SHA256,
            SUCCESSOR_CATALOG_SHA256,
            STAGING_PERSISTENT_MAPPING_CATALOG_SHA256,
            STAGING_RETIREMENT_CATALOG_SHA256,
            STAGING_PRICE_CATALOG_SHA256,
            IMMUTABLE_FIXTURE_MANIFEST_SHA256,
        )
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging source catalog identities are not pinned"
        )


def is_staging_runtime_connection(conn: Any) -> bool:
    """Return true only for the exact staging runtime; reject partial identity."""

    row = conn.execute(
        "SELECT pg_catalog.current_database(),session_user::text,current_user::text,"
        "pg_catalog.current_setting('server_version_num')::integer"
    ).fetchone()
    if row is None:
        raise SyntheticStagingDatabaseError(
            "synthetic staging connected identity is absent"
        )
    # The accepted 001-016 fixture is initialized and verified under the
    # legacy role pair in the final database name before the reviewed
    # transition runs.  The database name alone therefore cannot select the
    # staging successor.  A runtime-role signal is unambiguous; once present,
    # require the complete target identity below and fail closed on a partial
    # match.
    staging_signal = str(row[1]) == RUNTIME_LOGIN or str(row[2]) == RUNTIME_LOGIN
    if not staging_signal:
        return False
    if (
        str(row[0]),
        str(row[1]),
        str(row[2]),
        int(row[3]) // 10000,
    ) != (
        EXPECTED_DATABASE,
        RUNTIME_LOGIN,
        RUNTIME_LOGIN,
        EXPECTED_POSTGRES_MAJOR,
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging connected identity differs"
        )
    return True


def verify_staging_persistent_mapping_contract(conn: Any) -> str:
    """Verify the staging successor of the otherwise immutable 014 contract."""

    _require_source_hashes()
    if not is_staging_runtime_connection(conn):
        raise SyntheticStagingDatabaseError(
            "synthetic staging runtime identity is absent"
        )
    observed = conn.execute(
        sql.SQL(
            "SELECT {}.compute_synthetic_staging_persistent_mapping_sha256()"
        ).format(
            sql.Identifier(SCHEMA)
        )
    ).fetchone()[0]
    if observed != STAGING_PERSISTENT_MAPPING_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging persistent-mapping successor differs"
        )
    conn.execute(
        sql.SQL("SELECT {}.assert_synthetic_staging_contract()").format(
            sql.Identifier(SCHEMA)
        )
    )
    return str(observed)


def _catalog_payload(conn: Any) -> dict[str, Any]:
    """Return a cross-cluster-stable catalog projection including every ACL."""

    # PostgreSQL pretty-printers and reg* casts are search-path sensitive.  A
    # fixed path makes provisioner and runtime projections byte-identical; the
    # caller's transaction-local setting is restored before returning.
    previous_search_path = conn.execute(
        "SELECT current_setting('search_path')"
    ).fetchone()[0]
    conn.execute("SELECT set_config('search_path','pg_catalog',true)")

    try:
        return _catalog_payload_with_fixed_path(conn)
    finally:
        conn.execute(
            "SELECT set_config('search_path',%s,true)", (previous_search_path,)
        )


def _normalized_acl_sql(expression: str) -> str:
    """SQL fragment returning NULL-state plus an order-stable direct ACL."""

    return (
        f"{expression} IS NULL,COALESCE((SELECT pg_catalog.jsonb_agg("
        "pg_catalog.jsonb_build_object("
        "'grantor',pg_catalog.pg_get_userbyid(acl.grantor),"
        "'grantee',CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE "
        "pg_catalog.pg_get_userbyid(acl.grantee) END,"
        "'privilege',acl.privilege_type,'grantable',acl.is_grantable) "
        "ORDER BY acl.grantee,acl.privilege_type,acl.grantor) FROM "
        f"pg_catalog.aclexplode({expression}) acl),'[]'::pg_catalog.jsonb)"
    )


def _normalized_text_array_sql(expression: str) -> str:
    return (
        "COALESCE((SELECT pg_catalog.jsonb_agg(item ORDER BY item) FROM "
        f"pg_catalog.unnest({expression}) item),'[]'::pg_catalog.jsonb)"
    )


def _catalog_payload_with_fixed_path(conn: Any) -> dict[str, Any]:
    """Implementation for :func:`_catalog_payload` under pg_catalog-only path."""

    database = conn.execute(
        "SELECT d.datname,pg_catalog.pg_get_userbyid(d.datdba),"
        + _normalized_acl_sql("d.datacl")
        + ",d.datallowconn,d.datistemplate FROM pg_catalog.pg_database d "
        "WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    schemas = conn.execute(
        "SELECT n.nspname,pg_catalog.pg_get_userbyid(n.nspowner),"
        + _normalized_acl_sql("n.nspacl")
        + " FROM pg_catalog.pg_namespace n "
        "WHERE n.nspname IN (%s,'public') ORDER BY n.nspname",
        (SCHEMA,),
    ).fetchall()
    classes = conn.execute(
        "SELECT c.relname,c.relkind,pg_catalog.pg_get_userbyid(c.relowner),"
        + _normalized_acl_sql("c.relacl")
        + ",c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,"
        + _normalized_text_array_sql("c.reloptions")
        + ","
        "CASE WHEN c.relkind IN ('v','m') THEN pg_catalog.pg_get_viewdef(c.oid,true) "
        "WHEN c.relkind IN ('i','I') THEN pg_catalog.pg_get_indexdef(c.oid) "
        "ELSE '<NULL>' END "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,c.relkind",
        (SCHEMA,),
    ).fetchall()
    sequences = conn.execute(
        "SELECT c.relname,pg_catalog.format_type(s.seqtypid,NULL),"
        "s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle "
        "FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c "
        "ON c.oid=s.seqrelid JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname",
        (SCHEMA,),
    ).fetchall()
    columns = conn.execute(
        "SELECT c.relname,a.attnum,a.attname,"
        "pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,"
        "a.attgenerated,"
        + _normalized_acl_sql("a.attacl")
        + ","
        "COALESCE(pg_catalog.pg_get_expr(d.adbin,d.adrelid),'<NULL>') "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY c.relname,a.attnum",
        (SCHEMA,),
    ).fetchall()
    routines = conn.execute(
        "SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
        "pg_catalog.pg_get_function_result(p.oid),p.prokind,l.lanname,p.provolatile,"
        "p.proparallel,p.prosecdef,p.proleakproof,p.proisstrict,p.proretset,"
        "pg_catalog.pg_get_userbyid(p.proowner),"
        + _normalized_acl_sql("p.proacl")
        + ","
        + _normalized_text_array_sql("p.proconfig")
        + ",p.prosrc,COALESCE(p.probin,'') "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
        "ON n.oid=p.pronamespace JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname=%s ORDER BY p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid)",
        (SCHEMA,),
    ).fetchall()
    constraints = conn.execute(
        "SELECT c.relname,k.conname,k.contype,k.condeferrable,k.condeferred,"
        "k.convalidated,pg_catalog.pg_get_constraintdef(k.oid,true) "
        "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c "
        "ON c.oid=k.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s ORDER BY c.relname,k.conname",
        (SCHEMA,),
    ).fetchall()
    triggers = conn.execute(
        "SELECT c.relname,t.tgname,t.tgenabled,t.tgdeferrable,t.tginitdeferred,"
        "p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
        "pg_catalog.pg_get_triggerdef(t.oid,true) "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        (SCHEMA,),
    ).fetchall()
    extensions = conn.execute(
        "SELECT e.extname,e.extversion,n.nspname,"
        "pg_catalog.pg_get_userbyid(e.extowner) FROM pg_catalog.pg_extension e "
        "JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace "
        "WHERE n.nspname=%s ORDER BY e.extname",
        (SCHEMA,),
    ).fetchall()
    defaults = conn.execute(
        "SELECT pg_catalog.pg_get_userbyid(d.defaclrole),"
        "COALESCE(n.nspname,'<GLOBAL>'),d.defaclobjtype,"
        + _normalized_acl_sql("d.defaclacl")
        + " "
        "FROM pg_catalog.pg_default_acl d LEFT JOIN pg_catalog.pg_namespace n "
        "ON n.oid=d.defaclnamespace JOIN pg_catalog.pg_roles owner "
        "ON owner.oid=d.defaclrole WHERE (n.nspname=%s OR n.nspname IS NULL) "
        "AND owner.rolname=ANY(%s) "
        "ORDER BY 1,2,3,4",
        (
            SCHEMA,
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
        ),
    ).fetchall()
    roles = conn.execute(
        "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
        "rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=ANY(%s) ORDER BY rolname",
        ([LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],),
    ).fetchall()
    memberships = conn.execute(
        "SELECT parent.rolname,member.rolname,m.admin_option,m.inherit_option,"
        "m.set_option FROM pg_catalog.pg_auth_members m "
        "JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid "
        "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
        "WHERE parent.rolname=ANY(%s) OR member.rolname=ANY(%s) "
        "ORDER BY parent.rolname,member.rolname",
        (
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
        ),
    ).fetchall()
    settings = conn.execute(
        "SELECT r.rolname,d.datname,"
        + _normalized_text_array_sql("s.setconfig")
        + " "
        "FROM pg_catalog.pg_db_role_setting s JOIN pg_catalog.pg_roles r "
        "ON r.oid=s.setrole LEFT JOIN pg_catalog.pg_database d ON d.oid=s.setdatabase "
        "WHERE r.rolname=ANY(%s) ORDER BY r.rolname,d.datname",
        ([LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],),
    ).fetchall()
    return {
        "classes": [list(row) for row in classes],
        "columns": [list(row) for row in columns],
        "constraints": [list(row) for row in constraints],
        "database": list(database) if database else None,
        "default_acls": [list(row) for row in defaults],
        "extensions": [list(row) for row in extensions],
        "memberships": [list(row) for row in memberships],
        "roles": [list(row) for row in roles],
        "role_settings": [list(row) for row in settings],
        "routines": [list(row) for row in routines],
        "schemas": [list(row) for row in schemas],
        "sequences": [list(row) for row in sequences],
        "triggers": [list(row) for row in triggers],
    }


def compute_catalog_sha256(conn: Any) -> str:
    from psycopg.types.json import Jsonb

    # PostgreSQL jsonb text is the shared canonicalization used by the SQL
    # calculator.  This deliberately avoids a Python/SQL serializer mismatch.
    canonical = conn.execute(
        "SELECT %s::pg_catalog.jsonb::text", (Jsonb(_catalog_payload(conn)),)
    ).fetchone()[0]
    return hashlib.sha256(str(canonical).encode()).hexdigest()


def _metadata(conn: Any, keys: list[str]) -> dict[str, str]:
    return dict(
        conn.execute(
            sql.SQL("SELECT key,value FROM {}.meta WHERE key=ANY(%s)").format(
                sql.Identifier(SCHEMA)
            ),
            (keys,),
        ).fetchall()
    )


def compute_immutable_fixture_sha256(conn: Any) -> str:
    """Hash source-defined immutable fixture facts, excluding generated IDs."""

    keys = [
        *LEGACY_MARKERS,
        "synthetic_owner_demo_contract",
        "synthetic_multivendor_acceptance_contract",
        "synthetic_development_forecast_contract",
        "synthetic_owner_demo_profile",
        "synthetic_owner_demo_business_date",
        "synthetic_owner_demo_sales_backfill_id",
        "synthetic_development_forecast_registration",
    ]
    metadata = _metadata(conn, keys)
    retirement_marker = metadata.get(
        "monday_forecast_v2_retirement_catalog_sha256"
    )
    if set(metadata) != set(keys) or retirement_marker not in (
        LEGACY_RETIREMENT_CATALOG_MARKERS
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging immutable fixture metadata differs"
        )
    metadata["monday_forecast_v2_retirement_catalog_sha256"] = (
        "<ACCEPTED_015_CATALOG_PROVENANCE>"
    )
    relation_payload: dict[str, list[str]] = {}
    for relation, excluded_columns in sorted(
        IMMUTABLE_FIXTURE_RELATIONS.items()
    ):
        relation_payload[relation] = [
            str(row[0])
            for row in conn.execute(
                sql.SQL(
                    "SELECT (pg_catalog.to_jsonb(t)-%s::text[])::text "
                    "FROM {}.{} t ORDER BY "
                    "(pg_catalog.to_jsonb(t)-%s::text[])::text"
                ).format(sql.Identifier(SCHEMA), sql.Identifier(relation)),
                (list(excluded_columns), list(excluded_columns)),
            ).fetchall()
        ]
    return hashlib.sha256(
        _canonical({"metadata": metadata, "tables": relation_payload})
    ).hexdigest()


def _verify_fixture_provenance(conn: Any) -> None:
    from .development_forecast import development_forecast_v2_registration

    wanted = {
        **LEGACY_MARKERS,
        "synthetic_owner_demo_contract": FIXTURE_CONTRACT,
        "synthetic_multivendor_acceptance_contract": MULTIVENDOR_FIXTURE_CONTRACT,
        "synthetic_development_forecast_contract": DEVELOPMENT_FIXTURE_CONTRACT,
        "synthetic_owner_demo_profile": DEVELOPMENT_FIXTURE_PROFILE,
        "synthetic_owner_demo_business_date": FIXTURE_BUSINESS_DATE,
        "synthetic_owner_demo_sales_backfill_id": FIXTURE_SALES_BACKFILL_ID,
        "synthetic_development_forecast_registration": json.dumps(
            development_forecast_v2_registration(),
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    observed = _metadata(conn, list(wanted))
    retirement_marker = observed.pop(
        "monday_forecast_v2_retirement_catalog_sha256", None
    )
    expected_without_retirement = dict(wanted)
    expected_without_retirement.pop(
        "monday_forecast_v2_retirement_catalog_sha256"
    )
    if (
        observed != expected_without_retirement
        or retirement_marker not in LEGACY_RETIREMENT_CATALOG_MARKERS
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging predecessor or fixture provenance differs"
        )
    controls = conn.execute(
        sql.SQL(
            "SELECT (SELECT count(DISTINCT sale_date) FROM {}.sales_daily),"
            "(SELECT count(*) FROM {}.sales_daily),"
            "(SELECT count(DISTINCT variant_id) FROM {}.sales_daily),"
            "(SELECT count(*) FROM {}.supplier_price_schedule_policies),"
            "(SELECT bool_and(fixture_database_name=current_database()) "
            "FROM {}.supplier_price_schedule_policies)"
        ).format(*(sql.Identifier(SCHEMA) for _ in range(5)))
    ).fetchone()
    if controls != (138, 966, 7, 2, True):
        raise SyntheticStagingDatabaseError(
            "synthetic staging immutable fixture controls differ"
        )
    if compute_immutable_fixture_sha256(conn) != IMMUTABLE_FIXTURE_MANIFEST_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging immutable fixture manifest differs"
        )
    staging_markers = _observed_staging_markers(conn)
    if staging_markers and staging_markers != _staging_markers():
        raise SyntheticStagingDatabaseError(
            "synthetic staging state is partial or conflicting"
        )
    persistent_expected = (
        STAGING_PERSISTENT_MAPPING_CATALOG_SHA256
        if staging_markers
        else LEGACY_MARKERS["persistent_mapping_foundation_catalog_sha256"]
    )
    persistent_function = (
        "compute_synthetic_staging_persistent_mapping_sha256"
        if staging_markers
        else "compute_persistent_mapping_catalog_sha256"
    )
    persistent_observed = conn.execute(
        sql.SQL("SELECT {}.{}()").format(
            sql.Identifier(SCHEMA),
            sql.Identifier(persistent_function),
        )
    ).fetchone()[0]
    from .monday_forecast_retirement import compute_retirement_catalog_sha256
    from .synthetic_price_replacement_contract import (
        compute_synthetic_price_catalog_sha256,
    )

    retirement_expected = (
        STAGING_RETIREMENT_CATALOG_SHA256
        if staging_markers
        else LEGACY_MARKERS["monday_forecast_v2_retirement_catalog_sha256"]
    )
    price_expected = (
        STAGING_PRICE_CATALOG_SHA256
        if staging_markers
        else LEGACY_MARKERS["synthetic_price_replacement_catalog_sha256"]
    )
    if (
        persistent_observed != persistent_expected
        or compute_retirement_catalog_sha256(conn, SCHEMA) != retirement_expected
        or compute_synthetic_price_catalog_sha256(conn, SCHEMA) != price_expected
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging integrity-consumer catalog differs"
        )


def _verify_role_topology(conn: Any, *, bootstrapped: bool) -> None:
    expected = {
        LEGACY_OWNER: (False, False, False, False, False, False, False),
        LEGACY_LOGIN: (
            False,
            False,
            False,
            False,
            not bootstrapped,
            False,
            False,
        ),
    }
    if bootstrapped:
        expected.update(
            {
                OBJECT_OWNER: (False, False, False, False, False, False, False),
                PROVISIONER: (False, True, False, False, True, False, False),
                RUNTIME_LOGIN: (False, False, False, False, True, False, False),
            }
        )
    for role, flags in expected.items():
        if _role_flags(conn, role) != flags:
            raise SyntheticStagingDatabaseError(
                "synthetic staging role flags differ"
            )
    edges = set(
        conn.execute(
            "SELECT parent.rolname,member.rolname,m.admin_option,"
            "m.inherit_option,m.set_option FROM pg_catalog.pg_auth_members m "
            "JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid "
            "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
            "WHERE parent.rolname=ANY(%s) OR member.rolname=ANY(%s)",
            (
                [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
                [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
            ),
        ).fetchall()
    )
    wanted = (
        {
            (LEGACY_OWNER, PROVISIONER, False, True, True),
            (OBJECT_OWNER, PROVISIONER, False, True, True),
        }
        if bootstrapped
        else {(LEGACY_OWNER, LEGACY_LOGIN, False, False, True)}
    )
    if edges != wanted:
        raise SyntheticStagingDatabaseError(
            "synthetic staging role membership differs"
        )


def _require_transaction(conn: Any) -> None:
    if bool(getattr(conn, "autocommit", False)):
        raise SyntheticStagingDatabaseError(
            "synthetic staging transition requires one explicit transaction"
        )


def _pgcrypto_definition_records(conn: Any) -> tuple[list[list[Any]], list[Any]]:
    previous_search_path = conn.execute(
        "SELECT current_setting('search_path')"
    ).fetchone()[0]
    conn.execute("SELECT set_config('search_path','pg_catalog',true)")
    try:
        rows = conn.execute(
            "SELECT p.oid,p.proname,"
            "pg_catalog.replace(pg_catalog.oidvectortypes(p.proargtypes),', ',','),"
            "pg_catalog.pg_get_function_result(p.oid),l.lanname,p.provolatile,"
            "p.proparallel,p.prosecdef,p.proleakproof,p.proisstrict,p.proretset,"
            "p.prosrc,COALESCE(p.probin,''),"
            "pg_catalog.pg_get_userbyid(p.proowner) "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_proc'::regclass "
            "AND d.objid=p.oid AND d.deptype='e' "
            "JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid "
            "WHERE e.extname='pgcrypto' AND n.nspname=%s "
            "ORDER BY p.proname,pg_catalog.oidvectortypes(p.proargtypes)",
            (SCHEMA,),
        ).fetchall()
    finally:
        conn.execute(
            "SELECT set_config('search_path',%s,true)", (previous_search_path,)
        )
    definitions = [list(row[1:13]) for row in rows]
    return definitions, list(rows)


def _verified_pgcrypto_rows(conn: Any) -> list[Any]:
    extension = conn.execute(
        "SELECT e.extversion,n.nspname,pg_catalog.pg_get_userbyid(e.extowner) "
        "FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n "
        "ON n.oid=e.extnamespace WHERE e.extname='pgcrypto'"
    ).fetchone()
    if extension != ("1.3", SCHEMA, LEGACY_OWNER):
        raise SyntheticStagingDatabaseError(
            "synthetic staging pgcrypto extension identity differs"
        )
    definitions, rows = _pgcrypto_definition_records(conn)
    identities = tuple(f"{row[1]}({row[2]})" for row in rows)
    if identities != PGCRYPTO_ROUTINES or hashlib.sha256(
        _canonical(definitions)
    ).hexdigest() != PGCRYPTO_DEFINITION_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging pgcrypto routine source differs"
        )
    return rows


def _normalize_pgcrypto_owners(conn: Any, rows: list[Any]) -> None:
    if {str(row[13]) for row in rows} != {str(conn.info.user)}:
        raise SyntheticStagingDatabaseError(
            "synthetic staging pgcrypto routine owner state is partial"
        )
    for _oid, name, arguments, *_properties, owner in rows:
        assert str(owner) == str(conn.info.user)
        conn.execute(
            sql.SQL("ALTER FUNCTION {}.{}({}) OWNER TO {}").format(
                sql.Identifier(SCHEMA),
                sql.Identifier(name),
                sql.SQL(str(arguments)),
                sql.Identifier(OBJECT_OWNER),
            )
        )


def bootstrap_roles(conn: Any) -> bool:
    """Administrative, transactional role bootstrap; never an app operation."""

    _require_transaction(conn)
    row = conn.execute(
        "SELECT current_user=session_user AND rolsuper FROM pg_catalog.pg_roles "
        "WHERE rolname=current_user"
    ).fetchone()
    if row != (True,):
        raise SyntheticStagingDatabaseError(
            "synthetic staging bootstrap requires an administrative session"
        )
    if conn.execute(
        "SELECT current_database(),current_setting('server_version_num')::int/10000"
    ).fetchone() != (EXPECTED_DATABASE, EXPECTED_POSTGRES_MAJOR):
        raise SyntheticStagingDatabaseError(
            "synthetic staging bootstrap destination differs"
        )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (f"{CONTRACT_VERSION}:{EXPECTED_DATABASE}:bootstrap",),
    )
    _verify_fixture_provenance(conn)
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)",
            ([OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],),
        ).fetchall()
    }
    database_owner = conn.execute(
        "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
        "WHERE datname=current_database()"
    ).fetchone()[0]
    pgcrypto_rows = _verified_pgcrypto_rows(conn)
    pgcrypto_owners = {str(row[13]) for row in pgcrypto_rows}
    pre_bootstrap = (
        not existing
        and database_owner == LEGACY_OWNER
        and pgcrypto_owners == {str(conn.info.user)}
    )
    post_bootstrap = (
        existing == {OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN}
        and database_owner == OBJECT_OWNER
        and pgcrypto_owners == {OBJECT_OWNER}
    )
    if post_bootstrap:
        _verify_role_topology(conn, bootstrapped=True)
        return False
    if not pre_bootstrap:
        raise SyntheticStagingDatabaseError(
            "synthetic staging bootstrap state is partial or conflicting"
        )
    _verify_role_topology(conn, bootstrapped=False)
    changed = True
    if pre_bootstrap:
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(OBJECT_OWNER))
        )
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(PROVISIONER))
        )
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(RUNTIME_LOGIN))
        )
        conn.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(LEGACY_OWNER), sql.Identifier(LEGACY_LOGIN)
            )
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(LEGACY_LOGIN))
        )
        for parent in (LEGACY_OWNER, OBJECT_OWNER):
            conn.execute(
                sql.SQL(
                    "GRANT {} TO {} WITH ADMIN FALSE, INHERIT TRUE, SET TRUE"
                ).format(sql.Identifier(parent), sql.Identifier(PROVISIONER))
            )
        conn.execute(
            sql.SQL(
                "ALTER ROLE {} IN DATABASE {} SET search_path TO {},pg_catalog"
            ).format(
                sql.Identifier(RUNTIME_LOGIN),
                sql.Identifier(EXPECTED_DATABASE),
                sql.Identifier(SCHEMA),
            )
        )
        conn.execute(
            sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(EXPECTED_DATABASE), sql.Identifier(OBJECT_OWNER)
            )
        )
    _normalize_pgcrypto_owners(conn, pgcrypto_rows)
    _verify_role_topology(conn, bootstrapped=True)
    return changed


def _alter_application_owners(conn: Any) -> None:
    """Transfer the preflighted application inventory without REASSIGN OWNED."""

    conn.execute(
        sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
            sql.Identifier(SCHEMA), sql.Identifier(OBJECT_OWNER)
        )
    )
    rows = conn.execute(
        "SELECT c.relkind,c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f') "
        "ORDER BY CASE c.relkind WHEN 'r' THEN 1 WHEN 'p' THEN 1 "
        "WHEN 'v' THEN 2 WHEN 'm' THEN 2 ELSE 3 END,c.relname",
        (SCHEMA,),
    ).fetchall()
    keywords = {
        "r": "TABLE",
        "p": "TABLE",
        "v": "VIEW",
        "m": "MATERIALIZED VIEW",
        "f": "FOREIGN TABLE",
    }
    for kind, name in rows:
        conn.execute(
            sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                sql.SQL(keywords[kind]),
                sql.Identifier(SCHEMA),
                sql.Identifier(name),
                sql.Identifier(OBJECT_OWNER),
            )
        )
    sequences = conn.execute(
        "SELECT c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relkind='S' "
        "AND pg_catalog.pg_get_userbyid(c.relowner)<>%s ORDER BY c.relname",
        (SCHEMA, OBJECT_OWNER),
    ).fetchall()
    for (name,) in sequences:
        conn.execute(
            sql.SQL("ALTER SEQUENCE {}.{} OWNER TO {}").format(
                sql.Identifier(SCHEMA),
                sql.Identifier(name),
                sql.Identifier(OBJECT_OWNER),
            )
        )
    previous_search_path = conn.execute(
        "SELECT current_setting('search_path')"
    ).fetchone()[0]
    conn.execute("SELECT set_config('search_path','pg_catalog',true)")
    try:
        routines = conn.execute(
            "SELECT p.proname,pg_catalog.replace("
            "pg_catalog.oidvectortypes(p.proargtypes),', ',',' ) "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND NOT EXISTS ("
            "SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e "
            "ON e.oid=d.refobjid WHERE d.classid='pg_catalog.pg_proc'::regclass "
            "AND d.objid=p.oid AND d.deptype='e') "
            "ORDER BY p.proname,pg_catalog.oidvectortypes(p.proargtypes)",
            (SCHEMA,),
        ).fetchall()
        for name, arguments in routines:
            conn.execute(
                sql.SQL("ALTER FUNCTION {}.{}({}) OWNER TO {}").format(
                    sql.Identifier(SCHEMA),
                    sql.Identifier(name),
                    sql.SQL(str(arguments)),
                    sql.Identifier(OBJECT_OWNER),
                )
            )
    finally:
        conn.execute(
            "SELECT set_config('search_path',%s,true)",
            (str(previous_search_path),),
        )


def _apply_permissions(conn: Any) -> None:
    conn.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC,{},{}").format(
            sql.Identifier(EXPECTED_DATABASE),
            sql.Identifier(RUNTIME_LOGIN),
            sql.Identifier(PROVISIONER),
        )
    )
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {},{}").format(
            sql.Identifier(EXPECTED_DATABASE),
            sql.Identifier(RUNTIME_LOGIN),
            sql.Identifier(PROVISIONER),
        )
    )
    conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    conn.execute(
        sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC,{}").format(
            sql.Identifier(SCHEMA), sql.Identifier(RUNTIME_LOGIN)
        )
    )
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(SCHEMA), sql.Identifier(RUNTIME_LOGIN)
        )
    )
    relation_rows = conn.execute(
        "SELECT c.relname,c.relkind FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f') ORDER BY 1",
        (SCHEMA,),
    ).fetchall()
    if {row[0] for row in relation_rows} != set(APPLICATION_RELATIONS):
        raise SyntheticStagingDatabaseError(
            "synthetic staging relation inventory differs"
        )
    for name, _kind in relation_rows:
        conn.execute(
            sql.SQL("REVOKE ALL ON TABLE {}.{} FROM PUBLIC,{}").format(
                sql.Identifier(SCHEMA),
                sql.Identifier(name),
                sql.Identifier(RUNTIME_LOGIN),
            )
        )
        if name in READ_RELATIONS:
            conn.execute(
                sql.SQL("GRANT SELECT ON TABLE {}.{} TO {}").format(
                    sql.Identifier(SCHEMA),
                    sql.Identifier(name),
                    sql.Identifier(RUNTIME_LOGIN),
                )
            )
        operations = WRITE_RELATIONS.get(name, ())
        if operations:
            conn.execute(
                sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
                    sql.SQL(",").join(sql.SQL(value) for value in operations),
                    sql.Identifier(SCHEMA),
                    sql.Identifier(name),
                    sql.Identifier(RUNTIME_LOGIN),
                )
            )
    sequence_rows = {
        row[0]
        for row in conn.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind='S'",
            (SCHEMA,),
        ).fetchall()
    }
    if not set(SEQUENCE_PRIVILEGES).issubset(sequence_rows):
        raise SyntheticStagingDatabaseError(
            "synthetic staging required sequence is absent"
        )
    for name in sorted(sequence_rows):
        conn.execute(
            sql.SQL("REVOKE ALL ON SEQUENCE {}.{} FROM PUBLIC,{}").format(
                sql.Identifier(SCHEMA),
                sql.Identifier(name),
                sql.Identifier(RUNTIME_LOGIN),
            )
        )
        operations = SEQUENCE_PRIVILEGES.get(name, ())
        if operations:
            conn.execute(
                sql.SQL("GRANT {} ON SEQUENCE {}.{} TO {}").format(
                    sql.SQL(",").join(sql.SQL(value) for value in operations),
                    sql.Identifier(SCHEMA),
                    sql.Identifier(name),
                    sql.Identifier(RUNTIME_LOGIN),
                )
            )
    previous_search_path = conn.execute(
        "SELECT current_setting('search_path')"
    ).fetchone()[0]
    conn.execute("SELECT set_config('search_path','pg_catalog',true)")
    try:
        routine_rows = conn.execute(
            "SELECT p.oid,p.proname,pg_catalog.replace("
            "pg_catalog.oidvectortypes(p.proargtypes),', ',',' ) "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s ORDER BY p.proname,"
            "pg_catalog.oidvectortypes(p.proargtypes)",
            (SCHEMA,),
        ).fetchall()
        routine_names = {
            f"{name}({arguments})" for _oid, name, arguments in routine_rows
        }
        if not set(EXECUTE_ROUTINES).issubset(routine_names):
            raise SyntheticStagingDatabaseError(
                "synthetic staging required routine is absent"
            )
        for _oid, name, arguments in routine_rows:
            conn.execute(
                sql.SQL(
                    "REVOKE ALL ON FUNCTION {}.{}({}) FROM PUBLIC,{}"
                ).format(
                    sql.Identifier(SCHEMA),
                    sql.Identifier(name),
                    sql.SQL(str(arguments)),
                    sql.Identifier(RUNTIME_LOGIN),
                )
            )
        for signature in EXECUTE_ROUTINES:
            conn.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                    sql.SQL(f"{SCHEMA}.{signature}"),
                    sql.Identifier(RUNTIME_LOGIN),
                )
            )
    finally:
        conn.execute(
            "SELECT set_config('search_path',%s,true)", (previous_search_path,)
        )
    conn.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} "
            "REVOKE ALL ON FUNCTIONS FROM PUBLIC,{}"
        ).format(sql.Identifier(OBJECT_OWNER), sql.Identifier(RUNTIME_LOGIN))
    )
    for object_type in ("TABLES", "SEQUENCES"):
        conn.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                "REVOKE ALL ON {} FROM PUBLIC,{}"
            ).format(
                sql.Identifier(OBJECT_OWNER),
                sql.Identifier(SCHEMA),
                sql.SQL(object_type),
                sql.Identifier(RUNTIME_LOGIN),
            )
        )


def _staging_markers() -> dict[str, str]:
    return {
        "synthetic_staging_contract": CONTRACT_VERSION,
        "synthetic_staging_fixture_manifest_sha256": (
            IMMUTABLE_FIXTURE_MANIFEST_SHA256
        ),
        "synthetic_staging_permission_matrix_sha256": PERMISSION_MATRIX_SHA256,
        "synthetic_staging_catalog_sha256": SUCCESSOR_CATALOG_SHA256,
    }


def _observed_staging_markers(conn: Any) -> dict[str, str]:
    return dict(
        conn.execute(
            sql.SQL(
                "SELECT key,value FROM {}.meta "
                "WHERE key LIKE 'synthetic_staging_%' ORDER BY key"
            ).format(sql.Identifier(SCHEMA))
        ).fetchall()
    )


def provision_contract(conn: Any, *, sql_path: Path | None = None) -> bool:
    """Apply predecessor -> successor as the constrained provisioner."""

    _require_transaction(conn)
    _require_source_hashes()
    if conn.execute(
        "SELECT current_database(),current_setting('server_version_num')::int/10000,"
        "session_user::text,current_user::text"
    ).fetchone() != (
        EXPECTED_DATABASE,
        EXPECTED_POSTGRES_MAJOR,
        PROVISIONER,
        PROVISIONER,
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging provisioner identity differs"
        )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (f"{CONTRACT_VERSION}:{EXPECTED_DATABASE}",),
    )
    _verify_role_topology(conn, bootstrapped=True)
    _verify_fixture_provenance(conn)
    wanted = _staging_markers()
    observed = _observed_staging_markers(conn)
    if observed:
        if observed != wanted or compute_catalog_sha256(conn) != SUCCESSOR_CATALOG_SHA256:
            raise SyntheticStagingDatabaseError(
                "synthetic staging state is partial or conflicting"
            )
        return False
    if compute_catalog_sha256(conn) != PREDECESSOR_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging predecessor catalog differs"
        )
    _alter_application_owners(conn)
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OBJECT_OWNER)))
    contract_path = (
        sql_path
        or Path(__file__).resolve().parents[2]
        / "db"
        / "017_synthetic_staging_contract.sql"
    )
    contract_sql = contract_path.read_text(encoding="utf-8")
    bindings = {
        "__PERMISSION_MATRIX_SHA256__": PERMISSION_MATRIX_SHA256,
        "__FIXTURE_MANIFEST_SHA256__": IMMUTABLE_FIXTURE_MANIFEST_SHA256,
    }
    if any(contract_sql.count(token) != 1 for token in bindings):
        raise SyntheticStagingDatabaseError(
            "synthetic staging SQL source binding differs"
        )
    for token, value in bindings.items():
        contract_sql = contract_sql.replace(token, value)
    conn.execute(contract_sql)
    conn.execute("RESET ROLE")
    _apply_permissions(conn)
    for key, value in wanted.items():
        conn.execute(
            sql.SQL("INSERT INTO {}.meta(key,value) VALUES (%s,%s)").format(
                sql.Identifier(SCHEMA)
            ),
            (key, value),
        )
    observed_hash = compute_catalog_sha256(conn)
    if observed_hash != SUCCESSOR_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(
            f"synthetic staging successor catalog differs ({observed_hash})"
        )
    return True


def _audit_runtime_privileges(conn: Any) -> None:
    unexpected_schema = conn.execute(
        "SELECT n.nspname FROM pg_catalog.pg_namespace n "
        "WHERE n.nspname<>%s "
        "AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') "
        "AND n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$' "
        "AND (pg_catalog.has_schema_privilege(%s,n.oid,'USAGE') "
        "OR pg_catalog.has_schema_privilege(%s,n.oid,'CREATE')) "
        "ORDER BY n.nspname LIMIT 1",
        (SCHEMA, RUNTIME_LOGIN, RUNTIME_LOGIN),
    ).fetchone()
    if unexpected_schema is not None:
        raise SyntheticStagingDatabaseError(
            "synthetic staging runtime can access another schema"
        )
    unexpected_relation = conn.execute(
        "SELECT n.nspname,c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname<>%s "
        "AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') "
        "AND n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$' "
        "AND c.relkind IN ('r','p','v','m','f','S') AND ("
        "(c.relkind<>'S' AND ("
        "pg_catalog.has_table_privilege(%s,c.oid,'SELECT') OR "
        "pg_catalog.has_table_privilege(%s,c.oid,'INSERT') OR "
        "pg_catalog.has_table_privilege(%s,c.oid,'UPDATE') OR "
        "pg_catalog.has_table_privilege(%s,c.oid,'DELETE') OR "
        "pg_catalog.has_table_privilege(%s,c.oid,'TRUNCATE') OR "
        "pg_catalog.has_table_privilege(%s,c.oid,'REFERENCES') OR "
        "pg_catalog.has_table_privilege(%s,c.oid,'TRIGGER'))) OR "
        "(c.relkind='S' AND ("
        "pg_catalog.has_sequence_privilege(%s,c.oid,'USAGE') OR "
        "pg_catalog.has_sequence_privilege(%s,c.oid,'SELECT') OR "
        "pg_catalog.has_sequence_privilege(%s,c.oid,'UPDATE'))) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid "
        "AND a.attnum>0 AND NOT a.attisdropped AND ("
        "pg_catalog.has_column_privilege(%s,c.oid,a.attnum,'SELECT') OR "
        "pg_catalog.has_column_privilege(%s,c.oid,a.attnum,'INSERT') OR "
        "pg_catalog.has_column_privilege(%s,c.oid,a.attnum,'UPDATE') OR "
        "pg_catalog.has_column_privilege(%s,c.oid,a.attnum,'REFERENCES')))) "
        "ORDER BY n.nspname,c.relname LIMIT 1",
        (
            SCHEMA,
            *([RUNTIME_LOGIN] * 14),
        ),
    ).fetchone()
    if unexpected_relation is not None:
        raise SyntheticStagingDatabaseError(
            "synthetic staging runtime can access an unlisted relation"
        )
    unexpected_routine = conn.execute(
        "SELECT n.nspname,p.proname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname<>%s "
        "AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') "
        "AND n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$' "
        "AND pg_catalog.has_function_privilege(%s,p.oid,'EXECUTE') "
        "ORDER BY n.nspname,p.proname LIMIT 1",
        (SCHEMA, RUNTIME_LOGIN),
    ).fetchone()
    if unexpected_routine is not None:
        raise SyntheticStagingDatabaseError(
            "synthetic staging runtime can execute an unlisted routine"
        )
    schema_relations = {
        row[0]
        for row in conn.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f')",
            (SCHEMA,),
        ).fetchall()
    }
    if schema_relations != set(APPLICATION_RELATIONS):
        raise SyntheticStagingDatabaseError(
            "synthetic staging relation inventory differs"
        )
    for relation in sorted(schema_relations):
        expected = set(WRITE_RELATIONS.get(relation, ()))
        if relation in READ_RELATIONS:
            expected.add("SELECT")
        for operation in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "TRIGGER",
            "REFERENCES",
        ):
            actual = conn.execute(
                "SELECT pg_catalog.has_table_privilege(%s,%s,%s)",
                (RUNTIME_LOGIN, f"{SCHEMA}.{relation}", operation),
            ).fetchone()[0]
            if bool(actual) != (operation in expected):
                raise SyntheticStagingDatabaseError(
                    "synthetic staging effective relation privileges differ"
                )
    sequence_rows = {
        row[0]
        for row in conn.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind='S'",
            (SCHEMA,),
        ).fetchall()
    }
    for sequence in sorted(sequence_rows):
        expected = set(SEQUENCE_PRIVILEGES.get(sequence, ()))
        for operation in ("USAGE", "SELECT", "UPDATE"):
            actual = conn.execute(
                "SELECT pg_catalog.has_sequence_privilege(%s,%s,%s)",
                (RUNTIME_LOGIN, f"{SCHEMA}.{sequence}", operation),
            ).fetchone()[0]
            if bool(actual) != (operation in expected):
                raise SyntheticStagingDatabaseError(
                    "synthetic staging effective sequence privileges differ"
                )
    expected_routine_oids: set[int] = set()
    for signature in EXECUTE_ROUTINES:
        oid = conn.execute(
            "SELECT pg_catalog.to_regprocedure(%s)::oid",
            (f"{SCHEMA}.{signature}",),
        ).fetchone()[0]
        if oid is None:
            raise SyntheticStagingDatabaseError(
                "synthetic staging required routine is absent"
            )
        expected_routine_oids.add(int(oid))
    if len(expected_routine_oids) != len(EXECUTE_ROUTINES):
        raise SyntheticStagingDatabaseError(
            "synthetic staging routine identities are ambiguous"
        )
    routines = conn.execute(
        "SELECT p.oid FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=%s ORDER BY p.oid",
        (SCHEMA,),
    ).fetchall()
    for (oid,) in routines:
        actual = conn.execute(
            "SELECT pg_catalog.has_function_privilege(%s,%s,'EXECUTE')",
            (RUNTIME_LOGIN, oid),
        ).fetchone()[0]
        if bool(actual) != (int(oid) in expected_routine_oids):
            raise SyntheticStagingDatabaseError(
                "synthetic staging effective routine privileges differ"
            )
    unexpected_acl = conn.execute(
        "SELECT EXISTS("
        "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x "
        "WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped "
        "AND x.grantee<>c.relowner) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) x "
        "WHERE n.nspname=%s AND x.is_grantable AND x.grantee<>c.relowner) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
        "ON n.oid=p.pronamespace CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) x "
        "WHERE n.nspname=%s AND x.is_grantable AND x.grantee<>p.proowner)",
        (SCHEMA, SCHEMA, SCHEMA),
    ).fetchone()[0]
    if unexpected_acl:
        raise SyntheticStagingDatabaseError(
            "synthetic staging column or grant-option privilege differs"
        )


def attest_runtime_connection(
    conn: Any, target: SyntheticStagingTarget
) -> str:
    """Read-only proof of identity, fixture, catalog, topology, and ACL matrix."""

    _require_source_hashes()
    target.validate_static()
    row = conn.execute(
        "SELECT current_database(),current_setting('server_version_num')::int,"
        "session_user::text,current_user::text,current_schema(),"
        "pg_catalog.pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d "
        "WHERE d.datname=current_database()"
    ).fetchone()
    if not row or (
        row[0],
        int(row[1]) // 10000,
        row[2],
        row[3],
        row[4],
        row[5],
    ) != (
        EXPECTED_DATABASE,
        EXPECTED_POSTGRES_MAJOR,
        RUNTIME_LOGIN,
        RUNTIME_LOGIN,
        SCHEMA,
        OBJECT_OWNER,
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging connected identity differs"
        )
    _verify_role_topology(conn, bootstrapped=True)
    _verify_fixture_provenance(conn)
    if _observed_staging_markers(conn) != _staging_markers():
        raise SyntheticStagingDatabaseError(
            "synthetic staging contract marker differs"
        )
    if compute_catalog_sha256(conn) != SUCCESSOR_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging successor catalog differs"
        )
    db_privileges = tuple(
        bool(value)
        for value in conn.execute(
            "SELECT pg_catalog.has_database_privilege(%s,current_database(),'CONNECT'),"
            "pg_catalog.has_database_privilege(%s,current_database(),'CREATE'),"
            "pg_catalog.has_database_privilege(%s,current_database(),'TEMP')",
            (RUNTIME_LOGIN, RUNTIME_LOGIN, RUNTIME_LOGIN),
        ).fetchone()
    )
    schema_privileges = tuple(
        bool(value)
        for value in conn.execute(
            "SELECT pg_catalog.has_schema_privilege(%s,%s,'USAGE'),"
            "pg_catalog.has_schema_privilege(%s,%s,'CREATE')",
            (RUNTIME_LOGIN, SCHEMA, RUNTIME_LOGIN, SCHEMA),
        ).fetchone()
    )
    if db_privileges != (True, False, False) or schema_privileges != (True, False):
        raise SyntheticStagingDatabaseError(
            "synthetic staging database or schema privileges differ"
        )
    other_database = conn.execute(
        "SELECT d.datname FROM pg_catalog.pg_database d WHERE d.datallowconn "
        "AND d.datname<>current_database() "
        "AND pg_catalog.has_database_privilege(%s,d.oid,'CONNECT') "
        "ORDER BY d.datname LIMIT 1",
        (RUNTIME_LOGIN,),
    ).fetchone()
    if other_database is not None:
        raise SyntheticStagingDatabaseError(
            "synthetic staging runtime can connect to another database"
        )
    ownership = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r "
        "ON r.oid=n.nspowner WHERE r.rolname=%s) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_roles r "
        "ON r.oid=c.relowner WHERE r.rolname=%s) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r "
        "ON r.oid=p.proowner WHERE r.rolname=%s)",
        (RUNTIME_LOGIN, RUNTIME_LOGIN, RUNTIME_LOGIN),
    ).fetchone()[0]
    if ownership:
        raise SyntheticStagingDatabaseError(
            "synthetic staging runtime owns a database object"
        )
    _audit_runtime_privileges(conn)
    verify_staging_persistent_mapping_contract(conn)
    from .monday_forecast_retirement import (
        verify_monday_forecast_v2_retirement_contract,
    )
    from .synthetic_price_replacement_contract import (
        verify_synthetic_price_replacement_contract,
    )

    verify_monday_forecast_v2_retirement_contract(conn)
    verify_synthetic_price_replacement_contract(conn)
    identity = hashlib.sha256(
        _canonical(
            {
                "catalog": SUCCESSOR_CATALOG_SHA256,
                "contract": CONTRACT_VERSION,
                "database": EXPECTED_DATABASE,
                "fixture": DEVELOPMENT_FIXTURE_CONTRACT,
                "permission_matrix": PERMISSION_MATRIX_SHA256,
                "postgres_major": EXPECTED_POSTGRES_MAJOR,
                "schema": SCHEMA,
            }
        )
    ).hexdigest()
    return identity

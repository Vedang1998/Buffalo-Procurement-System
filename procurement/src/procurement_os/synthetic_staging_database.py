"""Versioned least-privilege contract for the synthetic staging database.

The accepted local fixture is immutable.  This module defines the one reviewed
catalog transition which may be applied to a restored copy of that fixture.
Ordinary application startup only calls the read-only attestation path.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

from psycopg import sql


CONTRACT_VERSION = "BUFFALO_SYNTHETIC_STAGING_DATABASE_V2"
MIGRATION_NAME = "017_synthetic_staging_contract.sql"
MIGRATION_SHA256 = (
    "a883c6048f94580e6a83b67e0d4f14b54de1158fa595d4c3c464afc0a25da359"
)
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
EXPECTED_PRIVATE_HOST = "postgres.railway.internal"
EXPECTED_DATABASE_CREATION: Mapping[str, object] = {
    "allow_connections": True,
    "collation": "C",
    "collation_version": None,
    "connection_limit": -1,
    "ctype": "C",
    "encoding": "UTF8",
    "icu_locale": None,
    "icu_rules": None,
    "is_template": False,
    "locale_provider": "c",
    "server_encoding": "UTF8",
    "tablespace": "pg_default",
    "time_zone": "UTC",
}
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
TRANSFER_SOURCE_CATALOG_SHA256 = (
    "f23bc0543bbb8562957ae10a4da29ac9efc8e35a58ef6b376aeb3610fba923ba"
)
TRANSFER_SEQUENCE_OWNERSHIP_SHA256 = (
    "2c7bfae88dfabb23b27087a1b1fcd9e386575f9a0d16fb9f115f3bc169527e9e"
)
CORE_SYSTEM_VIEW_SHA256 = (
    "b4cfaffba9b5c37dbef9ab5fd96c453716e5dffc42e08a73eb85a67059ae2569"
)
CORE_SYSTEM_ROUTINE_SHA256 = (
    "78d732c718df86f8709d3d69b3dc74b9ae96e5ce0a69a1223e2b0265acd22387"
)
_PGCRYPTO_BOOTSTRAP_OWNER = "<PGCRYPTO_BOOTSTRAP_OWNER>"
_TRANSFER_DATABASE_ACL = "<TRANSFER_DATABASE_ACL>"
_TRANSFER_SOURCE_DATABASE_ACL: tuple[object, object] = (True, [])
_DATABASE_SECURITY_DEFAULTS = ("session_replication_role=origin",)
_TRANSFER_FENCED_DATABASE_ACL: tuple[object, object] = (
    False,
    [
        {
            "grantee": LEGACY_OWNER,
            "grantor": LEGACY_OWNER,
            "grantable": False,
            "privilege": privilege,
        }
        for privilege in ("CONNECT", "CREATE", "TEMPORARY")
    ],
)
_PREDECESSOR_FENCED_DATABASE_ACL: tuple[object, object] = (
    False,
    [
        {
            "grantee": OBJECT_OWNER,
            "grantor": OBJECT_OWNER,
            "grantable": False,
            "privilege": privilege,
        }
        for privilege in ("CONNECT", "CREATE", "TEMPORARY")
    ],
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
    "synthetic_staging_backup_v2_state_facts()",
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
    "0690b5c784b48b1bb82fc7022bc7695d8eb5195f8da0edd90a5437a2bb1089fc"
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
EXPECTED_RUNTIME_ATTESTATION_IDENTITY = (
    "517843a848fd07e5fc62b9713279a8bcfc52a782890aee68c7d900dd3600d77a"
)
STAGING_BACKUP_RELEASE_CONTRACT = "BUFFALO_SYNTHETIC_STAGING_BACKUP_RELEASE_V1"
STAGING_TRANSFER_CONTRACT = "BUFFALO_SYNTHETIC_STAGING_TRANSFER_V1"
TRANSFER_CONTRACT_META_KEY = "staging_transfer_contract"
TRANSFER_MANIFEST_META_KEY = "staging_transfer_manifest_sha256"
_SCRAM_ITERATIONS = 4096
_SCRAM_VERIFIER = re.compile(
    r"^SCRAM-SHA-256\$([1-9][0-9]*):([A-Za-z0-9+/]+={0,2})\$"
    r"([A-Za-z0-9+/]+={0,2}):([A-Za-z0-9+/]+={0,2})$"
)


def _validated_role_secret(value: str, *, label: str) -> bytes:
    if (
        type(value) is not str
        or not 32 <= len(value) <= 256
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise SyntheticStagingDatabaseError(
            f"synthetic staging {label} credential differs"
        )
    return value.encode("ascii")


def _scram_sha256_verifier(secret: str, *, salt: bytes | None = None) -> str:
    password = _validated_role_secret(secret, label="role")
    selected_salt = secrets.token_bytes(16) if salt is None else salt
    if type(selected_salt) is not bytes or len(selected_salt) != 16:
        raise SyntheticStagingDatabaseError(
            "synthetic staging role credential salt differs"
        )
    salted = hashlib.pbkdf2_hmac(
        "sha256", password, selected_salt, _SCRAM_ITERATIONS
    )
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    encoded = lambda value: base64.b64encode(value).decode("ascii")
    return (
        f"SCRAM-SHA-256${_SCRAM_ITERATIONS}:{encoded(selected_salt)}$"
        f"{encoded(stored_key)}:{encoded(server_key)}"
    )


def _scram_secret_matches(secret: str, verifier: str | None) -> bool:
    password = _validated_role_secret(secret, label="role")
    if not isinstance(verifier, str):
        return False
    matched = _SCRAM_VERIFIER.fullmatch(verifier)
    if matched is None or int(matched.group(1)) != _SCRAM_ITERATIONS:
        return False
    try:
        salt = base64.b64decode(matched.group(2), validate=True)
        expected = _scram_sha256_verifier(password.decode("ascii"), salt=salt)
    except (ValueError, SyntheticStagingDatabaseError):
        return False
    return hmac.compare_digest(verifier, expected)


def staging_backup_release(target: Any | None = None) -> dict[str, object]:
    """Return the source-defined delivery binding embedded in Backup V2."""

    from .staging_config import (
        EXPECTED_APP_SERVICE_ID,
        EXPECTED_ENVIRONMENT_ID,
        EXPECTED_POSTGRES_SERVICE_ID,
        EXPECTED_PROJECT_ID,
    )

    if target is not None:
        target.validate_static()

    return {
        "contract": STAGING_BACKUP_RELEASE_CONTRACT,
        "database": EXPECTED_DATABASE,
        "database_contract": CONTRACT_VERSION,
        "fixture_contract": DEVELOPMENT_FIXTURE_CONTRACT,
        "fixture_profile": DEVELOPMENT_FIXTURE_PROFILE,
        "fixture_manifest_sha256": IMMUTABLE_FIXTURE_MANIFEST_SHA256,
        "migration": MIGRATION_NAME,
        "migration_sha256": MIGRATION_SHA256,
        "object_owner": OBJECT_OWNER,
        "permission_matrix_sha256": PERMISSION_MATRIX_SHA256,
        "postgres_major": EXPECTED_POSTGRES_MAJOR,
        "private_host": EXPECTED_PRIVATE_HOST,
        "provisioner": PROVISIONER,
        "catalog_sha256": SUCCESSOR_CATALOG_SHA256,
        "runtime_login": RUNTIME_LOGIN,
        "schema": SCHEMA,
        "persistent_mapping_catalog_sha256": (
            STAGING_PERSISTENT_MAPPING_CATALOG_SHA256
        ),
        "retirement_catalog_sha256": STAGING_RETIREMENT_CATALOG_SHA256,
        "price_catalog_sha256": STAGING_PRICE_CATALOG_SHA256,
        "runtime_attestation_identity": EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        "target": {
            "app_service_id": EXPECTED_APP_SERVICE_ID,
            "environment_id": EXPECTED_ENVIRONMENT_ID,
            "postgres_service_id": EXPECTED_POSTGRES_SERVICE_ID,
            "project_id": EXPECTED_PROJECT_ID,
        },
    }


STAGING_BACKUP_RELEASE_SHA256 = hashlib.sha256(
    _canonical(staging_backup_release())
).hexdigest()


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
    transfer_manifest_sha256: str
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
                self.expected_private_host == EXPECTED_PRIVATE_HOST
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
            or re.fullmatch(r"[0-9a-f]{64}", self.transfer_manifest_sha256)
            is None
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
        "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256",
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
        transfer_manifest_sha256=str(
            environment["BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256"]
        ),
        owned_local_port=owned_local_port,
    )
    target.validate_static()
    return target


def _role_flags(conn: Any, role: str) -> tuple[Any, ...]:
    row = conn.execute(
        "SELECT rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
        "rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil::text "
        "FROM pg_catalog.pg_roles WHERE rolname=%s",
        (role,),
    ).fetchone()
    if row is None:
        raise SyntheticStagingDatabaseError("synthetic staging role is absent")
    return (
        *(bool(value) for value in row[:7]),
        int(row[7]),
        None if row[8] is None else str(row[8]),
    )


def database_creation_envelope(conn: Any) -> dict[str, object]:
    """Return DB creation facts that a schema-only dump cannot transfer."""

    row = conn.execute(
        "SELECT d.datallowconn,d.datcollate,d.datcollversion,d.datconnlimit,"
        "d.datctype,"
        "pg_catalog.pg_encoding_to_char(d.encoding),d.daticulocale,d.daticurules,"
        "d.datistemplate,d.datlocprovider,"
        "pg_catalog.current_setting('server_encoding'),"
        "t.spcname,pg_catalog.current_setting('TimeZone') "
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_tablespace t "
        "ON t.oid=d.dattablespace WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    if row is None:
        raise SyntheticStagingDatabaseError(
            "synthetic staging database creation envelope is absent"
        )
    return {
        "allow_connections": bool(row[0]),
        "collation": str(row[1]),
        "collation_version": None if row[2] is None else str(row[2]),
        "connection_limit": int(row[3]),
        "ctype": str(row[4]),
        "encoding": str(row[5]),
        "icu_locale": None if row[6] is None else str(row[6]),
        "icu_rules": None if row[7] is None else str(row[7]),
        "is_template": bool(row[8]),
        "locale_provider": str(row[9]),
        "server_encoding": str(row[10]),
        "tablespace": str(row[11]),
        "time_zone": str(row[12]),
    }


def verify_database_creation_envelope(conn: Any) -> None:
    if database_creation_envelope(conn) != dict(EXPECTED_DATABASE_CREATION):
        raise SyntheticStagingDatabaseError(
            "synthetic staging database creation envelope differs"
        )


def _require_source_hashes() -> None:
    values = (
        PREDECESSOR_CATALOG_SHA256,
        SUCCESSOR_CATALOG_SHA256,
        MIGRATION_SHA256,
        STAGING_PERSISTENT_MAPPING_CATALOG_SHA256,
        STAGING_RETIREMENT_CATALOG_SHA256,
        STAGING_PRICE_CATALOG_SHA256,
        IMMUTABLE_FIXTURE_MANIFEST_SHA256,
        EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        TRANSFER_SOURCE_CATALOG_SHA256,
        STAGING_BACKUP_RELEASE_SHA256,
    )
    if not all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in values
    ) or "0" * 64 in values:
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
        "SELECT pg_catalog.current_setting('search_path')"
    ).fetchone()[0]
    conn.execute(
        "SELECT pg_catalog.set_config('search_path','pg_catalog',true)"
    )

    try:
        return _catalog_payload_with_fixed_path(conn)
    finally:
        conn.execute(
            "SELECT pg_catalog.set_config('search_path',%s,true)",
            (previous_search_path,),
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
        "SELECT COALESCE(r.rolname,'<ALL_ROLES>'),d.datname,"
        + _normalized_text_array_sql("s.setconfig")
        + " "
        "FROM pg_catalog.pg_db_role_setting s LEFT JOIN pg_catalog.pg_roles r "
        "ON r.oid=s.setrole LEFT JOIN pg_catalog.pg_database d ON d.oid=s.setdatabase "
        "WHERE r.rolname=ANY(%s) OR (s.setrole=0 AND d.datname=%s) "
        "AND s.setconfig<>%s "
        "ORDER BY 1,2,3",
        (
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
            EXPECTED_DATABASE,
            list(_DATABASE_SECURITY_DEFAULTS),
        ),
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


def _catalog_payload_sha256(conn: Any, payload: Mapping[str, Any]) -> str:
    from psycopg.types.json import Jsonb

    # PostgreSQL jsonb text is the shared canonicalization used by the SQL
    # calculator.  This deliberately avoids a Python/SQL serializer mismatch.
    canonical = conn.execute(
        "SELECT %s::pg_catalog.jsonb::text", (Jsonb(payload),)
    ).fetchone()[0]
    return hashlib.sha256(str(canonical).encode()).hexdigest()


def compute_catalog_sha256(conn: Any) -> str:
    return _catalog_payload_sha256(conn, _catalog_payload(conn))


def compute_predecessor_catalog_sha256(
    conn: Any, *, database_acl: Literal["source", "fenced"] = "source"
) -> str:
    """Hash the accepted predecessor under one caller-authorized ACL state.

    The one-time transfer fence replaces the database's implicit owner/PUBLIC
    ACL with an explicit owner-only ACL before bootstrap.  That transport
    control is verified exactly here and only then normalized to the original
    source ACL representation.  No other catalog field is normalized, and the
    accepted predecessor identity remains unchanged.
    """

    payload = _catalog_payload(conn)
    database = payload.get("database")
    expected_acl = {
        "source": _TRANSFER_SOURCE_DATABASE_ACL,
        "fenced": _PREDECESSOR_FENCED_DATABASE_ACL,
    }.get(database_acl)
    if (
        expected_acl is None
        or not isinstance(database, list)
        or len(database) != 6
        or tuple(database[2:4]) != expected_acl
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging predecessor database ACL differs"
        )
    if database_acl == "fenced":
        database[2:4] = list(_TRANSFER_SOURCE_DATABASE_ACL)
    return _catalog_payload_sha256(conn, payload)


def _transfer_source_catalog_payload(
    conn: Any, *, database_acl: str
) -> dict[str, Any]:
    """Return the exact pre-bootstrap catalog with one portable owner token.

    PostgreSQL creates trusted-extension members as the cluster bootstrap
    administrator even when the extension itself is created under the legacy
    owner.  The administrator name is cluster-local, so only those 36 reviewed
    pgcrypto member owners are replaced with one explicit token.  Every other
    catalog field, including function bodies, trigger definitions and ACLs,
    remains byte-significant.
    """

    payload = _catalog_payload(conn)
    expected_database_acl = {
        "source": _TRANSFER_SOURCE_DATABASE_ACL,
        "fenced": _TRANSFER_FENCED_DATABASE_ACL,
    }.get(database_acl)
    database = payload.get("database")
    if (
        expected_database_acl is None
        or not isinstance(database, list)
        or len(database) != 6
        or tuple(database[2:4]) != expected_database_acl
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer database ACL differs"
        )
    database[2:4] = [_TRANSFER_DATABASE_ACL, _TRANSFER_DATABASE_ACL]
    extensions = payload.get("extensions")
    if extensions != [["pgcrypto", "1.3", SCHEMA, LEGACY_OWNER]]:
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer source extension differs"
        )
    member_rows = conn.execute(
        "SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
        "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_proc'::regclass "
        "AND d.objid=p.oid AND d.deptype='e' "
        "JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid "
        "WHERE n.nspname=%s AND e.extname='pgcrypto' "
        "ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)",
        (SCHEMA,),
    ).fetchall()
    member_identities = {(str(row[0]), str(row[1])) for row in member_rows}
    if len(member_rows) != len(PGCRYPTO_ROUTINES) or len(member_identities) != len(
        PGCRYPTO_ROUTINES
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer source pgcrypto inventory differs"
        )
    observed: set[tuple[str, str]] = set()
    bootstrap_owners: set[str] = set()
    for routine in payload.get("routines", []):
        if not isinstance(routine, list) or len(routine) != 17:
            raise SyntheticStagingDatabaseError(
                "synthetic staging transfer source routine inventory differs"
            )
        identity = (str(routine[0]), str(routine[1]))
        if identity not in member_identities:
            continue
        if identity in observed or routine[12:14] != [True, []]:
            raise SyntheticStagingDatabaseError(
                "synthetic staging transfer source pgcrypto ACL differs"
            )
        observed.add(identity)
        bootstrap_owners.add(str(routine[11]))
        routine[11] = _PGCRYPTO_BOOTSTRAP_OWNER
    if observed != member_identities or len(bootstrap_owners) != 1:
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer source pgcrypto owner differs"
        )
    bootstrap_owner = next(iter(bootstrap_owners))
    if conn.execute(
        "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=%s",
        (bootstrap_owner,),
    ).fetchone() != (True,):
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer source pgcrypto owner differs"
        )
    return payload


def compute_transfer_source_catalog_sha256(
    conn: Any, *, database_acl: str = "source"
) -> str:
    """Hash the accepted 001-016 source without executing restored code."""

    return hashlib.sha256(
        _canonical(
            _transfer_source_catalog_payload(conn, database_acl=database_acl)
        )
    ).hexdigest()


def verify_transfer_source_catalog(
    conn: Any, *, database_acl: str = "source"
) -> str:
    """Prove the immutable legacy catalog before any restored code is called."""

    # This verifier is an operator-only transaction boundary.  Keep the fixed
    # path in force for every subsequent legacy fixture check in the same
    # transaction rather than restoring a possibly attacker-controlled path.
    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    verify_database_creation_envelope(conn)
    _verify_semantic_catalog_envelope(conn)
    observed = compute_transfer_source_catalog_sha256(
        conn, database_acl=database_acl
    )
    if observed != TRANSFER_SOURCE_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer source catalog differs"
        )
    return observed


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
        LEGACY_OWNER: (
            False, False, False, False, False, False, False, -1, None
        ),
        LEGACY_LOGIN: (
            False,
            False,
            False,
            False,
            not bootstrapped,
            False,
            False,
            -1,
            None,
        ),
    }
    if bootstrapped:
        expected.update(
            {
                OBJECT_OWNER: (
                    False, False, False, False, False, False, False, -1, None
                ),
                PROVISIONER: (
                    False, True, False, False, True, False, False, -1, None
                ),
                RUNTIME_LOGIN: (
                    False, False, False, False, True, False, False, -1, None
                ),
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
    if bootstrapped:
        security_defaults = conn.execute(
            "SELECT s.setconfig FROM pg_catalog.pg_db_role_setting s "
            "JOIN pg_catalog.pg_database d ON d.oid=s.setdatabase "
            "WHERE s.setrole=0 AND d.datname=%s",
            (EXPECTED_DATABASE,),
        ).fetchall()
        if security_defaults != [(list(_DATABASE_SECURITY_DEFAULTS),)]:
            raise SyntheticStagingDatabaseError(
                "synthetic staging database security defaults differ"
            )


def _verify_role_credentials(
    conn: Any,
    *,
    bootstrapped: bool,
    provisioner_secret: str,
    runtime_secret: str,
) -> None:
    """Verify credential state as admin without exposing verifier bytes."""

    provisioner_bytes = _validated_role_secret(
        provisioner_secret, label="provisioner"
    )
    runtime_bytes = _validated_role_secret(runtime_secret, label="runtime")
    if hmac.compare_digest(provisioner_bytes, runtime_bytes):
        raise SyntheticStagingDatabaseError(
            "synthetic staging role credentials must be distinct"
        )
    wanted = [LEGACY_OWNER, LEGACY_LOGIN]
    if bootstrapped:
        wanted.extend([OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN])
    rows = dict(
        conn.execute(
            "SELECT rolname,rolpassword FROM pg_catalog.pg_authid "
            "WHERE rolname=ANY(%s) ORDER BY rolname",
            (wanted,),
        ).fetchall()
    )
    if set(rows) != set(wanted) or any(
        rows[role] is not None for role in (LEGACY_OWNER, LEGACY_LOGIN)
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging role credential state differs"
        )
    if not bootstrapped:
        return
    if (
        rows[OBJECT_OWNER] is not None
        or not _scram_secret_matches(provisioner_secret, rows[PROVISIONER])
        or not _scram_secret_matches(runtime_secret, rows[RUNTIME_LOGIN])
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging role credential state differs"
        )


def _require_transaction(conn: Any) -> None:
    if bool(getattr(conn, "autocommit", False)):
        raise SyntheticStagingDatabaseError(
            "synthetic staging transition requires one explicit transaction"
        )


def _transfer_markers(manifest_sha256: str) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None:
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer manifest identity is malformed"
        )
    return {
        TRANSFER_CONTRACT_META_KEY: STAGING_TRANSFER_CONTRACT,
        TRANSFER_MANIFEST_META_KEY: manifest_sha256,
    }


def _observed_transfer_markers(conn: Any) -> dict[str, str]:
    return dict(
        conn.execute(
            sql.SQL(
                "SELECT key,value FROM {}.meta WHERE key=ANY(%s) ORDER BY key"
            ).format(sql.Identifier(SCHEMA)),
            ([TRANSFER_CONTRACT_META_KEY, TRANSFER_MANIFEST_META_KEY],),
        ).fetchall()
    )


def _verify_transfer_markers(conn: Any, manifest_sha256: str) -> None:
    if _observed_transfer_markers(conn) != _transfer_markers(manifest_sha256):
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer provenance differs"
        )


def _verify_semantic_catalog_envelope(conn: Any) -> None:
    """Reject schema semantics omitted from the pinned catalog projections."""

    row = conn.execute(
        "SELECT "
        "EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r "
        "JOIN pg_catalog.pg_class c ON c.oid=r.ev_class "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND r.rulename<>'_RETURN'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_policy p "
        "JOIN pg_catalog.pg_class c ON c.oid=p.polrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relkind='c'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_type t "
        "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace "
        "WHERE n.nspname=%s AND t.typrelid=0 AND t.typelem=0),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_operator o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.oprnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_opclass o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.opcnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_opfamily o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.opfnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_collation c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.collnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_conversion c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_config x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.cfgnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_dict x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.dictnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_parser x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.prsnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_template x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.tmplnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
        "WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped "
        "AND a.attcollation<>0 AND a.attcollation<>t.typcollation),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
        "WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped "
        "AND (a.attstorage<>t.typstorage OR a.attcompression::text<>'' "
        "OR a.attstattarget<>-1 OR a.attoptions IS NOT NULL "
        "OR a.attfdwoptions IS NOT NULL OR a.attinhcount<>0 "
        "OR NOT a.attislocal)),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_trigger g "
        "JOIN pg_catalog.pg_class c ON c.oid=g.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND g.tgisinternal AND g.tgenabled<>'O'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=%s AND p.pronargdefaults<>0),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname=%s AND (p.procost<>(CASE WHEN l.lanname IN "
        "('c','internal') THEN 1 ELSE 100 END) OR "
        "p.prorows<>(CASE WHEN p.proretset THEN 1000 ELSE 0 END) "
        "OR p.prosupport<>0)),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND ((c.relkind='r' AND c.relreplident<>'d') "
        "OR (c.relkind<>'r' AND c.relreplident<>'n'))),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.reltablespace<>0),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_index i "
        "JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND (i.indisclustered OR i.indisreplident)),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_class t ON t.oid=c.reltoastrelid "
        "WHERE n.nspname=%s AND t.reloptions IS NOT NULL),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_depend d "
        "LEFT JOIN pg_catalog.pg_proc p "
        "ON d.classid='pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND d.objid=p.oid "
        "LEFT JOIN pg_catalog.pg_class c "
        "ON d.classid='pg_catalog.pg_class'::pg_catalog.regclass "
        "AND d.objid=c.oid "
        "LEFT JOIN pg_catalog.pg_type t "
        "ON d.classid='pg_catalog.pg_type'::pg_catalog.regclass "
        "AND d.objid=t.oid "
        "LEFT JOIN pg_catalog.pg_trigger g "
        "ON d.classid='pg_catalog.pg_trigger'::pg_catalog.regclass "
        "AND d.objid=g.oid "
        "LEFT JOIN pg_catalog.pg_class gc ON gc.oid=g.tgrelid "
        "LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace "
        "LEFT JOIN pg_catalog.pg_namespace gn ON gn.oid=gc.relnamespace "
        "WHERE d.deptype='x' AND (pn.nspname=%s OR cn.nspname=%s "
        "OR tn.nspname=%s OR gn.nspname=%s)),"
        "(SELECT d.description FROM pg_catalog.pg_description d "
        "JOIN pg_catalog.pg_extension e ON "
        "d.classoid='pg_catalog.pg_extension'::pg_catalog.regclass "
        "AND d.objoid=e.oid AND d.objsubid=0 "
        "WHERE e.extname='pgcrypto') IS DISTINCT FROM 'cryptographic functions',"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_statistic_ext),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_server),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_user_mappings),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_publication),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_subscription),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_description d "
        "LEFT JOIN pg_catalog.pg_class c "
        "ON d.classoid='pg_catalog.pg_class'::pg_catalog.regclass "
        "AND d.objoid=c.oid "
        "LEFT JOIN pg_catalog.pg_proc p "
        "ON d.classoid='pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND d.objoid=p.oid "
        "LEFT JOIN pg_catalog.pg_type t "
        "ON d.classoid='pg_catalog.pg_type'::pg_catalog.regclass "
        "AND d.objoid=t.oid "
        "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace "
        "WHERE cn.nspname=%s OR pn.nspname=%s OR tn.nspname=%s "
        "OR (d.classoid='pg_catalog.pg_namespace'::pg_catalog.regclass "
        "AND d.objoid=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s))),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_largeobject_metadata),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_seclabel),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_shseclabel l "
        "LEFT JOIN pg_catalog.pg_database d "
        "ON l.classoid='pg_catalog.pg_database'::pg_catalog.regclass "
        "AND l.objoid=d.oid LEFT JOIN pg_catalog.pg_roles r "
        "ON l.classoid='pg_catalog.pg_authid'::pg_catalog.regclass "
        "AND l.objoid=r.oid WHERE d.datname=pg_catalog.current_database() "
        "OR r.rolname=ANY(%s)),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_db_role_setting s "
        "LEFT JOIN pg_catalog.pg_roles r ON r.oid=s.setrole "
        "WHERE s.setdatabase=(SELECT oid FROM pg_catalog.pg_database "
        "WHERE datname=current_database()) "
        "AND ((s.setrole=0 AND s.setconfig<>%s) "
        "OR (s.setrole<>0 AND r.rolname<>ALL(%s))) "
        "OR (s.setdatabase=0 AND r.rolname=ANY(%s)))",
        (
            *(SCHEMA for _ in range(30)),
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
            list(_DATABASE_SECURITY_DEFAULTS),
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
        ),
    ).fetchone()
    if row is None or any(bool(value) for value in row):
        raise SyntheticStagingDatabaseError(
            "synthetic staging semantic catalog envelope differs"
        )
    namespaces = {
        str(record[0])
        for record in conn.execute(
            "SELECT nspname FROM pg_catalog.pg_namespace "
            "WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'"
        ).fetchall()
    }
    extensions = {
        str(record[0])
        for record in conn.execute(
            "SELECT extname FROM pg_catalog.pg_extension ORDER BY extname"
        ).fetchall()
    }
    if namespaces != {"public", SCHEMA} or extensions != {"plpgsql", "pgcrypto"}:
        raise SyntheticStagingDatabaseError(
            "synthetic staging semantic catalog envelope differs"
        )
    sequence_ownership = [
        [str(value) for value in record]
        for record in conn.execute(
            "SELECT s.relname,t.relname,a.attname "
            "FROM pg_catalog.pg_class s "
            "JOIN pg_catalog.pg_namespace sn ON sn.oid=s.relnamespace "
            "JOIN pg_catalog.pg_depend d "
            "ON d.classid='pg_catalog.pg_class'::pg_catalog.regclass "
            "AND d.objid=s.oid AND d.objsubid=0 "
            "AND d.refclassid='pg_catalog.pg_class'::pg_catalog.regclass "
            "AND d.deptype='a' "
            "JOIN pg_catalog.pg_class t ON t.oid=d.refobjid "
            "JOIN pg_catalog.pg_namespace tn ON tn.oid=t.relnamespace "
            "JOIN pg_catalog.pg_attribute a "
            "ON a.attrelid=t.oid AND a.attnum=d.refobjsubid "
            "WHERE sn.nspname=%s AND tn.nspname=%s AND s.relkind='S' "
            "ORDER BY s.relname,t.relname,a.attname",
            (SCHEMA, SCHEMA),
        ).fetchall()
    ]
    if (
        len(sequence_ownership) != 33
        or hashlib.sha256(_canonical(sequence_ownership)).hexdigest()
        != TRANSFER_SEQUENCE_OWNERSHIP_SHA256
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging semantic catalog envelope differs"
        )


def _verify_effective_postgres_safety_settings(conn: Any) -> None:
    effective_security = conn.execute(
        "SELECT pg_catalog.current_setting('session_replication_role'),"
        "pg_catalog.current_setting('fsync'),"
        "pg_catalog.current_setting('full_page_writes'),"
        "pg_catalog.current_setting('synchronous_commit'),"
        "pg_catalog.current_setting('data_sync_retry'),"
        "pg_catalog.current_setting('zero_damaged_pages'),"
        "pg_catalog.current_setting('ignore_invalid_pages'),"
        "pg_catalog.current_setting('ignore_checksum_failure'),"
        "pg_catalog.current_setting('max_prepared_transactions')"
    ).fetchone()
    if effective_security != (
        "origin",
        "on",
        "on",
        "on",
        "off",
        "off",
        "off",
        "off",
        "0",
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging core global privilege envelope differs"
        )
    prepared = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_prepared_xacts "
        "WHERE database=pg_catalog.current_database())"
    ).fetchone()
    if prepared != (False,):
        raise SyntheticStagingDatabaseError(
            "synthetic staging core global privilege envelope differs"
        )


def _verify_core_global_privilege_envelope(conn: Any) -> None:
    """Reject inherited/global authority outside the application catalog."""

    conn.execute("SET LOCAL search_path = pg_catalog")
    flags = conn.execute(
        "SELECT "
        "EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE("
        "n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a "
        "WHERE n.nspname IN ('pg_catalog','information_schema','pg_toast') "
        "AND NOT ((a.grantee=n.nspowner AND a.privilege_type IN ('USAGE','CREATE')) "
        "OR (n.nspname IN ('pg_catalog','information_schema') AND a.grantee=0 "
        "AND a.privilege_type='USAGE' AND NOT a.is_grantable))),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_init_privs i "
        "ON i.classoid='pg_catalog.pg_class'::pg_catalog.regclass "
        "AND i.objoid=c.oid AND i.objsubid=0 AND i.privtype='i' "
        "WHERE n.nspname='pg_catalog' "
        "AND c.relacl IS DISTINCT FROM i.initprivs) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a "
        "WHERE n.nspname='information_schema' "
        "AND NOT ((a.grantee=c.relowner) OR (a.grantee=0 "
        "AND a.privilege_type='SELECT' AND NOT a.is_grantable))) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='pg_toast' AND c.relacl IS NOT NULL) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_init_privs i "
        "ON i.classoid='pg_catalog.pg_class'::pg_catalog.regclass "
        "AND i.objoid=c.oid AND i.objsubid=a.attnum AND i.privtype='i' "
        "WHERE n.nspname='pg_catalog' "
        "AND a.attnum>0 AND NOT a.attisdropped "
        "AND a.attacl IS DISTINCT FROM i.initprivs) OR EXISTS("
        "SELECT 1 FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('information_schema','pg_toast') "
        "AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "LEFT JOIN pg_catalog.pg_init_privs i "
        "ON i.classoid='pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND i.objoid=p.oid AND i.objsubid=0 AND i.privtype='i' "
        "WHERE n.nspname='pg_catalog' "
        "AND p.proacl IS DISTINCT FROM i.initprivs),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_tablespace t "
        "LEFT JOIN pg_catalog.pg_init_privs i "
        "ON i.classoid='pg_catalog.pg_tablespace'::pg_catalog.regclass "
        "AND i.objoid=t.oid AND i.objsubid=0 AND i.privtype='i' "
        "WHERE t.spcacl IS DISTINCT FROM i.initprivs),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_parameter_acl),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_cast WHERE oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_transform),"
        "(SELECT pg_catalog.array_agg(lanname ORDER BY lanname) "
        "FROM pg_catalog.pg_language)<>ARRAY['c','internal','plpgsql','sql']::name[],"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_language l "
        "LEFT JOIN pg_catalog.pg_init_privs i "
        "ON i.classoid='pg_catalog.pg_language'::pg_catalog.regclass "
        "AND i.objoid=l.oid AND i.objsubid=0 AND i.privtype='i' "
        "WHERE l.lanacl IS DISTINCT FROM i.initprivs),"
        "(SELECT pg_catalog.array_agg(amname||':'||amtype::pg_catalog.text "
        "ORDER BY amname) "
        "FROM pg_catalog.pg_am)<>ARRAY["
        "'brin:i','btree:i','gin:i','gist:i','hash:i','heap:t','spgist:i']::text[],"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND c.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND p.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_type t "
        "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND t.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_operator o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.oprnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND o.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_conversion c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND c.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_opclass o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.opcnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND o.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_opfamily o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.opfnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND o.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_config x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.cfgnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND x.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_dict x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.dictnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND x.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_parser x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.prsnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND x.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_template x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.tmplnamespace "
        "WHERE n.nspname IN ('pg_catalog','information_schema') "
        "AND x.oid>=16384),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_type t "
        "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_operator o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.oprnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_collation c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.collnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_conversion c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_opclass o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.opcnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_opfamily o "
        "JOIN pg_catalog.pg_namespace n ON n.oid=o.opfnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_config x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.cfgnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_dict x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.dictnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_parser x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.prsnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_ts_template x "
        "JOIN pg_catalog.pg_namespace n ON n.oid=x.tmplnamespace "
        "WHERE n.nspname='public')",
    ).fetchone()
    if flags is None or any(bool(value) for value in flags):
        raise SyntheticStagingDatabaseError(
            "synthetic staging core global privilege envelope differs"
        )
    views = [
        list(record)
        for record in conn.execute(
            "SELECT n.nspname,c.relname,c.relkind,"
            "CASE WHEN c.relowner=b.owner_oid THEN '<BOOTSTRAP_ADMIN>' "
            "ELSE pg_catalog.pg_get_userbyid(c.relowner) END,"
            "c.reloptions,c.relrowsecurity,c.relforcerowsecurity,c.relispopulated,"
            "pg_catalog.pg_get_viewdef(c.oid,false) "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN (SELECT nspowner AS owner_oid "
            "FROM pg_catalog.pg_namespace WHERE nspname='pg_catalog') b "
            "WHERE n.nspname IN ('pg_catalog','information_schema') "
            "AND c.relkind IN ('v','m') ORDER BY n.nspname,c.relname"
        ).fetchall()
    ]
    routines = [
        list(record)
        for record in conn.execute(
            "SELECT n.nspname,p.proname,"
            "pg_catalog.pg_get_function_identity_arguments(p.oid),"
            "pg_catalog.pg_get_function_result(p.oid),l.lanname,p.prokind,"
            "p.provolatile,p.proparallel,p.prosecdef,p.proleakproof,p.proisstrict,"
            "p.proretset,p.pronargs,p.pronargdefaults,p.proargmodes::text,"
            "p.proargnames,p.proallargtypes::text,p.proargdefaults::text,"
            "p.prosrc,COALESCE(p.probin,''),p.prosqlbody::text,p.proconfig,"
            "p.procost::text,p.prorows::text,p.prosupport::regproc::text,"
            "CASE WHEN p.proowner=b.owner_oid THEN '<BOOTSTRAP_ADMIN>' "
            "ELSE pg_catalog.pg_get_userbyid(p.proowner) END "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "CROSS JOIN (SELECT nspowner AS owner_oid "
            "FROM pg_catalog.pg_namespace WHERE nspname='pg_catalog') b "
            "WHERE n.nspname IN ('pg_catalog','information_schema') "
            "ORDER BY n.nspname,p.proname,"
            "pg_catalog.pg_get_function_identity_arguments(p.oid)"
        ).fetchall()
    ]
    if (
        len(views) != 141
        or hashlib.sha256(_canonical(views)).hexdigest()
        != CORE_SYSTEM_VIEW_SHA256
        or len(routines) != 3297
        or hashlib.sha256(_canonical(routines)).hexdigest()
        != CORE_SYSTEM_ROUTINE_SHA256
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging core global privilege envelope differs"
        )
    _verify_effective_postgres_safety_settings(conn)


def install_transfer_provenance(conn: Any, manifest_sha256: str) -> bool:
    """Atomically bind one caller-pinned private transfer before bootstrap."""

    _require_transaction(conn)
    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    row = conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::int/10000,"
        "current_user=session_user AND rolsuper FROM pg_catalog.pg_authid "
        "WHERE rolname=current_user"
    ).fetchone()
    if row != (EXPECTED_DATABASE, EXPECTED_POSTGRES_MAJOR, True):
        raise SyntheticStagingDatabaseError(
            "synthetic staging transfer marker requires an administrative session"
        )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (f"{STAGING_TRANSFER_CONTRACT}:{EXPECTED_DATABASE}",),
    )
    verify_transfer_source_catalog(conn, database_acl="fenced")
    _verify_role_topology(conn, bootstrapped=False)
    _verify_fixture_provenance(conn)
    wanted = _transfer_markers(manifest_sha256)
    observed = _observed_transfer_markers(conn)
    if observed:
        if observed != wanted:
            raise SyntheticStagingDatabaseError(
                "synthetic staging transfer provenance is partial or conflicting"
            )
        return False
    for key, value in wanted.items():
        conn.execute(
            sql.SQL("INSERT INTO {}.meta(key,value) VALUES (%s,%s)").format(
                sql.Identifier(SCHEMA)
            ),
            (key, value),
        )
    _verify_transfer_markers(conn, manifest_sha256)
    return True


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


def bootstrap_roles(
    conn: Any,
    *,
    transfer_manifest_sha256: str,
    provisioner_secret: str,
    runtime_secret: str,
) -> bool:
    """Administrative, transactional role bootstrap; never an app operation."""

    _require_transaction(conn)
    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    row = conn.execute(
        "SELECT current_user=session_user AND rolsuper FROM pg_catalog.pg_authid "
        "WHERE rolname=current_user"
    ).fetchone()
    if row != (True,):
        raise SyntheticStagingDatabaseError(
            "synthetic staging bootstrap requires an administrative session"
        )
    _validated_role_secret(provisioner_secret, label="provisioner")
    _validated_role_secret(runtime_secret, label="runtime")
    if hmac.compare_digest(provisioner_secret, runtime_secret):
        raise SyntheticStagingDatabaseError(
            "synthetic staging role credentials must be distinct"
        )
    if conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::int/10000"
    ).fetchone() != (EXPECTED_DATABASE, EXPECTED_POSTGRES_MAJOR):
        raise SyntheticStagingDatabaseError(
            "synthetic staging bootstrap destination differs"
        )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (f"{CONTRACT_VERSION}:{EXPECTED_DATABASE}:bootstrap",),
    )
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
        verify_database_creation_envelope(conn)
        _verify_semantic_catalog_envelope(conn)
        _verify_role_topology(conn, bootstrapped=True)
        observed_predecessor = compute_predecessor_catalog_sha256(
            conn, database_acl="fenced"
        )
        if observed_predecessor != PREDECESSOR_CATALOG_SHA256:
            raise SyntheticStagingDatabaseError(
                "synthetic staging predecessor catalog differs "
                f"({observed_predecessor})"
            )
        _verify_transfer_markers(conn, transfer_manifest_sha256)
        _verify_role_credentials(
            conn,
            bootstrapped=True,
            provisioner_secret=provisioner_secret,
            runtime_secret=runtime_secret,
        )
        # The exact catalog is proven before any restored fixture routine is
        # invoked on the idempotent post-bootstrap path.
        _verify_fixture_provenance(conn)
        return False
    if not pre_bootstrap:
        raise SyntheticStagingDatabaseError(
            "synthetic staging bootstrap state is partial or conflicting"
        )
    _verify_role_credentials(
        conn,
        bootstrapped=False,
        provisioner_secret=provisioner_secret,
        runtime_secret=runtime_secret,
    )
    verify_transfer_source_catalog(
        conn, database_acl="fenced"
    )
    _verify_transfer_markers(conn, transfer_manifest_sha256)
    _verify_role_topology(conn, bootstrapped=False)
    # The exact source catalog is proven before these restored fixture calls.
    _verify_fixture_provenance(conn)
    conn.execute(
        sql.SQL(
            "CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(OBJECT_OWNER))
    )
    conn.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(
            sql.Identifier(PROVISIONER),
            sql.Literal(_scram_sha256_verifier(provisioner_secret)),
        )
    )
    conn.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(
            sql.Identifier(RUNTIME_LOGIN),
            sql.Literal(_scram_sha256_verifier(runtime_secret)),
        )
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
        sql.SQL(
            "ALTER DATABASE {} SET session_replication_role TO origin"
        ).format(sql.Identifier(EXPECTED_DATABASE))
    )
    conn.execute(
        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
            sql.Identifier(EXPECTED_DATABASE), sql.Identifier(OBJECT_OWNER)
        )
    )
    _normalize_pgcrypto_owners(conn, pgcrypto_rows)
    _verify_role_topology(conn, bootstrapped=True)
    _verify_role_credentials(
        conn,
        bootstrapped=True,
        provisioner_secret=provisioner_secret,
        runtime_secret=runtime_secret,
    )
    observed_predecessor = compute_predecessor_catalog_sha256(
        conn, database_acl="fenced"
    )
    if observed_predecessor != PREDECESSOR_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging predecessor catalog differs "
            f"({observed_predecessor})"
        )
    _verify_fixture_provenance(conn)
    return True


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


def _verify_successor_catalog_and_fixture(
    conn: Any,
    *,
    transfer_manifest_sha256: str,
    error_message: str,
) -> None:
    """Verify catalog-only successor facts before invoking restored routines."""

    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    verify_database_creation_envelope(conn)
    _verify_semantic_catalog_envelope(conn)
    if compute_catalog_sha256(conn) != SUCCESSOR_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(error_message)
    _verify_transfer_markers(conn, transfer_manifest_sha256)
    if _observed_staging_markers(conn) != _staging_markers():
        raise SyntheticStagingDatabaseError(error_message)
    _verify_fixture_provenance(conn)


def provision_contract(
    conn: Any,
    *,
    transfer_manifest_sha256: str,
    sql_path: Path | None = None,
) -> bool:
    """Apply predecessor -> successor as the constrained provisioner."""

    _require_transaction(conn)
    _require_source_hashes()
    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    if conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::int/10000,"
        "session_user::pg_catalog.text,current_user::pg_catalog.text"
    ).fetchone() != (
        EXPECTED_DATABASE,
        EXPECTED_POSTGRES_MAJOR,
        PROVISIONER,
        PROVISIONER,
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging provisioner identity differs"
        )
    contract_path = (
        sql_path
        or Path(__file__).resolve().parents[2]
        / "db"
        / MIGRATION_NAME
    )
    try:
        contract_bytes = contract_path.read_bytes()
        contract_sql = contract_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SyntheticStagingDatabaseError(
            "synthetic staging SQL source is unavailable"
        ) from exc
    if hashlib.sha256(contract_bytes).hexdigest() != MIGRATION_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging SQL source hash differs"
        )
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
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (f"{CONTRACT_VERSION}:{EXPECTED_DATABASE}",),
    )
    _verify_role_topology(conn, bootstrapped=True)
    verify_database_creation_envelope(conn)
    _verify_semantic_catalog_envelope(conn)
    wanted = _staging_markers()
    observed_catalog = compute_catalog_sha256(conn)
    if observed_catalog == SUCCESSOR_CATALOG_SHA256:
        _verify_successor_catalog_and_fixture(
            conn,
            transfer_manifest_sha256=transfer_manifest_sha256,
            error_message="synthetic staging state is partial or conflicting",
        )
        return False
    observed_predecessor = compute_predecessor_catalog_sha256(
        conn, database_acl="fenced"
    )
    if observed_predecessor != PREDECESSOR_CATALOG_SHA256:
        raise SyntheticStagingDatabaseError(
            "synthetic staging predecessor catalog differs "
            f"({observed_predecessor})"
        )
    _verify_transfer_markers(conn, transfer_manifest_sha256)
    if _observed_staging_markers(conn):
        raise SyntheticStagingDatabaseError(
            "synthetic staging state is partial or conflicting"
        )
    # No restored routine runs until the exact caller-authorized predecessor
    # catalog has passed above.
    _verify_fixture_provenance(conn)
    _alter_application_owners(conn)
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OBJECT_OWNER)))
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


def attest_provisioner_connection(
    conn: Any, target: SyntheticStagingTarget
) -> str:
    """Read-only proof for the offline dump/backup operator connection."""

    _require_source_hashes()
    target.validate_static()
    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    row = conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::int/10000,"
        "session_user::pg_catalog.text,current_user::pg_catalog.text,"
        "pg_catalog.pg_get_userbyid(d.datdba) "
        "FROM pg_catalog.pg_database d "
        "WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    if row != (
        EXPECTED_DATABASE,
        EXPECTED_POSTGRES_MAJOR,
        PROVISIONER,
        PROVISIONER,
        OBJECT_OWNER,
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging provisioner connection differs"
        )
    _verify_role_topology(conn, bootstrapped=True)
    _verify_successor_catalog_and_fixture(
        conn,
        transfer_manifest_sha256=target.transfer_manifest_sha256,
        error_message="synthetic staging provisioner catalog differs",
    )
    persistent = conn.execute(
        sql.SQL(
            "SELECT {}.compute_synthetic_staging_persistent_mapping_sha256()"
        ).format(sql.Identifier(SCHEMA))
    ).fetchone()[0]
    from .monday_forecast_retirement import compute_retirement_catalog_sha256
    from .synthetic_price_replacement_contract import (
        compute_synthetic_price_catalog_sha256,
    )

    if (
        persistent != STAGING_PERSISTENT_MAPPING_CATALOG_SHA256
        or compute_retirement_catalog_sha256(conn, SCHEMA)
        != STAGING_RETIREMENT_CATALOG_SHA256
        or compute_synthetic_price_catalog_sha256(conn, SCHEMA)
        != STAGING_PRICE_CATALOG_SHA256
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging provisioner derived catalog differs"
        )
    return STAGING_BACKUP_RELEASE_SHA256


def attest_runtime_connection(
    conn: Any, target: SyntheticStagingTarget
) -> str:
    """Read-only proof of identity, fixture, catalog, topology, and ACL matrix."""

    _require_source_hashes()
    target.validate_static()
    original_search_path = conn.execute("SHOW search_path").fetchone()
    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    if original_search_path is None or not isinstance(
        original_search_path[0], str
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging connected search path differs"
        )
    conn.execute(
        "SELECT pg_catalog.set_config('search_path',%s,true)",
        (original_search_path[0],),
    )
    search_path = conn.execute(
        "SELECT pg_catalog.current_schemas(false)"
    ).fetchone()
    if search_path is None or tuple(search_path[0]) != (SCHEMA, "pg_catalog"):
        raise SyntheticStagingDatabaseError(
            "synthetic staging connected search path differs"
        )
    conn.execute("SET LOCAL search_path = pg_catalog")
    row = conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::int,"
        "session_user::pg_catalog.text,current_user::pg_catalog.text,"
        "pg_catalog.pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d "
        "WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    if not row or (
        row[0],
        int(row[1]) // 10000,
        row[2],
        row[3],
        row[4],
    ) != (
        EXPECTED_DATABASE,
        EXPECTED_POSTGRES_MAJOR,
        RUNTIME_LOGIN,
        RUNTIME_LOGIN,
        OBJECT_OWNER,
    ):
        raise SyntheticStagingDatabaseError(
            "synthetic staging connected identity differs"
        )
    _verify_role_topology(conn, bootstrapped=True)
    _verify_successor_catalog_and_fixture(
        conn,
        transfer_manifest_sha256=target.transfer_manifest_sha256,
        error_message="synthetic staging successor catalog differs",
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
    if identity != EXPECTED_RUNTIME_ATTESTATION_IDENTITY:
        raise SyntheticStagingDatabaseError(
            "synthetic staging readiness identity differs"
        )
    return EXPECTED_RUNTIME_ATTESTATION_IDENTITY

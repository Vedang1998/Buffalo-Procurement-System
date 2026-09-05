#!/usr/bin/env python3
"""One-time, restart-safe Phase 4 correction for published production.

This command is deliberately deployment- and Git-bound. It orchestrates the
already-approved Phase 4 services; it does not define identity decisions.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterator, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.historical_sales import (  # noqa: E402
    acquire_backfill_transaction_lock,
    derive_exclusion_integrity,
)
from procurement_os.historical_sales_manifest import (  # noqa: E402
    APPROVED_RUN_ID,
    ManifestExecutionContext,
    dry_run_manifest,
    load_authorized_manifest,
    persist_manifest_decisions,
    protected_state_fingerprints,
    require_review_authorization,
    validate_database_preflight,
)
from procurement_os.historical_sales_terminal import (  # noqa: E402
    ExecutionGitIdentity,
    TerminalExecutionContext,
    derive_runtime_execution_git_identity,
    dry_run_terminal_disposition,
    inspect_terminal_state,
    load_terminal_artifact,
    persist_terminal_disposition,
)
from procurement_os.readiness import po_readiness  # noqa: E402
from procurement_os.sales import rerun_sales_identity_resolution  # noqa: E402


CANONICAL_ORIGIN = "https://github.com/Vedang1998/Buffalo-Procurement-System.git"
EXPECTED_DATABASE_NAME = "neondb"
EXPECTED_POSTGRESQL_MAJOR = 16
EXPECTED_SCHEMA = "public"
START_DATE = date(2024, 11, 28)
END_DATE = date(2026, 8, 10)

ORIGINAL_MANIFEST = (
    PROCUREMENT_ROOT / "review" / "phase4_identity_manifest_corrected.csv"
)
TERMINAL_MANIFEST = (
    PROCUREMENT_ROOT / "review" / "phase4_terminal_disposition_manifest.csv"
)
MIGRATION_007 = PROCUREMENT_ROOT / "db" / "007_phase4_terminal_disposition.sql"
MIGRATION_007_MARKER = "migration:007_phase4_terminal_disposition.sql"
REQUIRED_PRE_007_MARKERS = frozenset(
    {
        "migration:schema_postgres.sql",
        "migration:001_v1_3_catalog_sales.sql",
        "migration:002_seed_import_records.sql",
        "migration:003_phase3_reconciliation.sql",
        "migration:004_identity_decision_invariants.sql",
        "migration:005_identity_investigation.sql",
        "migration:006_phase4_sales_backfill.sql",
    }
)

FROZEN_PROTECTED_FINGERPRINTS = {
    "sales_daily": {
        "sha256": "fd2b4e504b492d9e7609ef8642320f7de300f5294369476da0877aee8da8b2e8",
        "row_count": 55966,
    },
    "raw_resolution": {
        "sha256": "06e2726cc33849fc180788fa036a45dcd1b1acd7af32cf813f0ec9311b7dd37a",
        "row_count": 59083,
    },
    "sales_backfill_runs": {
        "sha256": "d26f1326eea8e16be6626684db5623c291f582a63564e7aeda9c90167507d409",
        "row_count": 1,
    },
    "readiness_gates": {
        "sha256": "3e3c67ec4fbf0f29824311b4b97ad77bc20635acc3a2e3822c89c73a3119c21a",
        "row_count": 7,
    },
    "purchase_orders": {
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "row_count": 0,
    },
    "purchase_order_lines": {
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "row_count": 0,
    },
}

EXPECTED_INITIAL_STATUS_COUNTS = {
    "RESOLVED": 55971,
    "UNRESOLVED": 3112,
    "AMBIGUOUS": 0,
    "EXCLUDED": 0,
}
EXPECTED_FINAL_STATUS_COUNTS = {
    "RESOLVED": 57429,
    "UNRESOLVED": 0,
    "AMBIGUOUS": 0,
    "EXCLUDED": 1654,
}
EXPECTED_FINAL_METHOD_COUNTS = {
    "EXACT_ACTIVE_VARIANT_ID": 36397,
    "APPROVED_VARIANT_ID_ALIAS": 19430,
    "APPROVED_HISTORICAL_IDENTITY": 136,
    "EXACT_PRESERVED_HISTORICAL_VARIANT_ID": 443,
    "APPROVED_SOURCE_IDENTITY_DECISION": 1023,
    "EXPLICIT_EXCLUSION": 189,
    "EXPLICIT_UNATTRIBUTABLE_EXCLUSION": 1465,
}
EXPECTED_GATE_STATUSES = {
    "CATALOG_SYNC": "PASS",
    "SALES_BACKFILL": "PASS",
    "VENDOR_RULES": "FAIL",
    "INVENTORY_HISTORY": "WARN",
    "MAPPING_INTEGRITY": "WARN",
    "OPEN_PO_RECONCILIATION": "WARN",
    "PRICE_COVERAGE": "WARN",
}
EXPECTED_FLOW_TOTALS = {
    "SOURCE": {
        "rows": 59083,
        "net_units": Decimal("82501.0000"),
        "absolute_units": Decimal("82545.0000"),
        "net_sales": Decimal("1300975.14"),
        "absolute_sales": Decimal("1304920.80"),
    },
    "RESOLVED": {
        "rows": 57429,
        "net_units": Decimal("80659.0000"),
        "absolute_units": Decimal("80693.0000"),
        "net_sales": Decimal("1263133.84"),
        "absolute_sales": Decimal("1264065.52"),
    },
    "EXCLUDED": {
        "rows": 1654,
        "net_units": Decimal("1842.0000"),
        "absolute_units": Decimal("1852.0000"),
        "net_sales": Decimal("37841.30"),
        "absolute_sales": Decimal("40855.28"),
    },
}
EXPECTED_SALES_DAILY = {
    "rows": 57424,
    "net_units": Decimal("80659.0000"),
    "net_sales": Decimal("1263133.84"),
}
EXPECTED_REASON_BUCKETS = {
    "PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION": {
        "keys": 8,
        "raw_rows": 189,
        "net_units": "44.0000",
        "absolute_units": "44.0000",
        "net_sales": "145.93",
        "absolute_sales": "169.91",
    },
    "HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW": {
        "keys": 190,
        "raw_rows": 1465,
        "net_units": "1798.0000",
        "absolute_units": "1808.0000",
        "net_sales": "37695.37",
        "absolute_sales": "40685.37",
    },
}

TERMINAL_VARIANT_COLUMNS = frozenset(
    {
        "identity_scope",
        "restoration_manifest_sha256",
        "restoration_manifest_row_number",
        "restoration_evidence_version",
        "restoration_owner_authorization",
        "restoration_authority_git_sha",
        "restoration_execution_git_sha",
    }
)
TERMINAL_DECISION_COLUMNS = frozenset(
    {
        "decision_schema_version",
        "reason_code",
        "primary_manifest_sha256",
        "primary_manifest_row_number",
        "evidence_version",
        "owner_authorization",
        "authority_git_sha",
        "execution_git_sha",
    }
)
TERMINAL_EXCLUSION_COLUMNS = frozenset({"reason_code", "effective_decision_id"})
TERMINAL_CONSTRAINTS = frozenset(
    {
        "ck_variants_identity_scope",
        "ck_variants_identity_invariants",
        "ck_variants_restoration_provenance",
        "ck_historical_sales_review_decision_action",
        "ck_historical_sales_review_decision_target",
        "ck_historical_sales_review_decision_terminal_provenance",
        "fk_historical_sales_exclusion_effective_decision",
        "ck_historical_sales_exclusion_structured_provenance",
    }
)
TERMINAL_TRIGGERS = frozenset(
    {
        "trg_phase4_review_decisions_append_only",
        "trg_phase4_exclusion_authority_append_only",
        "trg_phase4_exclusion_decision_link",
        "trg_phase4_supplier_offer_guard",
        "trg_phase4_supplier_alias_guard",
        "trg_phase4_price_guard",
        "trg_phase4_combo_component_guard",
        "trg_phase4_manual_override_guard",
        "trg_phase4_variant_policy_guard",
        "trg_phase4_forecast_guard",
        "trg_phase4_procurement_recommendation_guard",
        "trg_phase4_po_line_guard",
        "trg_phase4_historical_scope_transition",
    }
)
TERMINAL_FUNCTIONS = frozenset(
    {
        "phase4_reject_review_decision_mutation",
        "phase4_reject_exclusion_authority_mutation",
        "phase4_validate_exclusion_decision_link",
        "is_operational_current_variant",
        "phase4_assert_current_operational_variant",
        "phase4_guard_direct_operational_variant",
        "phase4_guard_active_operational_variant",
        "phase4_guard_approved_supplier_alias",
        "phase4_guard_offer_operational_reference",
        "phase4_guard_historical_scope_transition",
    }
)
TERMINAL_VIEWS = frozenset(
    {
        "v_current_prices",
        "v_future_prices",
        "v_operational_inventory_snapshots",
        "v_operational_daily_inventory_snapshots",
        "v_operational_variants",
    }
)


class CorrectiveValidationError(RuntimeError):
    """Raised whenever the corrective executor cannot prove an exact state."""


@dataclass(frozen=True)
class RuntimeIdentity:
    repository_root: Path
    git_sha: str
    tree_sha: str


@dataclass(frozen=True)
class PreparedExecution:
    database_url: str
    execution: RuntimeIdentity
    original_manifest: Any
    terminal_artifact: Any


def _require_sha(value: str, label: str) -> str:
    normalized = str(value).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", normalized):
        raise CorrectiveValidationError(f"{label} must be a full 40-character SHA")
    return normalized


def _git_text(repository_root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CorrectiveValidationError("unable to verify corrective Git identity") from exc


def validate_runtime_execution_identity(
    expected_git_sha: str, expected_tree_sha: str
) -> RuntimeIdentity:
    expected_git_sha = _require_sha(expected_git_sha, "expected execution Git SHA")
    expected_tree_sha = _require_sha(expected_tree_sha, "expected execution tree SHA")
    observed: ExecutionGitIdentity = derive_runtime_execution_git_identity(
        expected_git_sha
    )
    origin = _git_text(observed.repository_root, "remote", "get-url", "origin")
    if origin != CANONICAL_ORIGIN:
        raise CorrectiveValidationError("execution Git origin is not canonical")
    tree_sha = _git_text(observed.repository_root, "rev-parse", "HEAD^{tree}").lower()
    if tree_sha != expected_tree_sha:
        raise CorrectiveValidationError("execution Git tree differs from reviewed tree")
    for relative in (
        "scripts/phase4-published-production-bootstrap.sh",
        "procurement/tools/reconcile_phase4_published_production.py",
    ):
        tracked = _git_text(
            observed.repository_root, "ls-files", "--error-unmatch", "--", relative
        )
        if tracked != relative:
            raise CorrectiveValidationError(
                f"required corrective runtime path is not tracked: {relative}"
            )
    return RuntimeIdentity(observed.repository_root, observed.git_sha, tree_sha)


def prepare_execution(
    environment: Mapping[str, str],
    *,
    expected_git_sha: str,
    expected_tree_sha: str,
) -> PreparedExecution:
    """Complete every authorization/provenance check before any DB connection."""

    if environment.get("REPLIT_DEPLOYMENT") != "1":
        raise CorrectiveValidationError("corrective execution requires Replit deployment")
    database_url = environment.get("DATABASE_URL")
    if not database_url:
        raise CorrectiveValidationError("published database configuration is unavailable")
    configured_token = environment.get("RECONCILIATION_REVIEW_TOKEN")
    supplied_token = environment.get("PHASE4_REVIEW_TOKEN_INPUT")
    if not configured_token:
        raise PermissionError("reconciliation review authorization is not configured")
    if not supplied_token:
        raise PermissionError("Phase 4 review authorization input is unavailable")
    require_review_authorization(configured_token, supplied_token)

    execution = validate_runtime_execution_identity(
        expected_git_sha, expected_tree_sha
    )
    original_manifest = load_authorized_manifest(ORIGINAL_MANIFEST)
    terminal_artifact = load_terminal_artifact(
        TERMINAL_MANIFEST, ORIGINAL_MANIFEST
    )
    if terminal_artifact.original_manifest.raw_bytes != original_manifest.raw_bytes:
        raise CorrectiveValidationError("terminal authority embeds different original bytes")
    return PreparedExecution(
        database_url=database_url,
        execution=execution,
        original_manifest=original_manifest,
        terminal_artifact=terminal_artifact,
    )


def _clear_libpq_environment() -> None:
    for name in tuple(os.environ):
        if name.startswith("PG"):
            os.environ.pop(name, None)


def connect_database(database_url: str) -> Any:
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def verify_database_target(conn: Any) -> dict[str, Any]:
    """First SQL on every connection; establishes target without assigning an XID."""

    with conn.cursor() as cur:
        cur.execute(
            """SELECT current_database(),
                      current_setting('server_version'),
                      current_setting('server_version_num')::integer,
                      current_schema(),txid_current_if_assigned()"""
        )
        row = cur.fetchone()
    if row is None:
        raise CorrectiveValidationError("database identity query returned no row")
    database, server_version, server_version_num, schema, xid = row
    if str(database) != EXPECTED_DATABASE_NAME:
        raise CorrectiveValidationError("database target is not published production")
    if int(server_version_num) // 10000 != EXPECTED_POSTGRESQL_MAJOR:
        raise CorrectiveValidationError("published production must use PostgreSQL 16")
    if str(schema) != EXPECTED_SCHEMA:
        raise CorrectiveValidationError("published production schema is not public")
    if xid is not None:
        raise CorrectiveValidationError("database identity query unexpectedly assigned an XID")
    conn.rollback()
    return {
        "database": str(database),
        "postgresql_version": str(server_version),
        "postgresql_major": int(server_version_num) // 10000,
        "schema": str(schema),
        "identity_xid": None,
    }


@contextmanager
def verified_connection(database_url: str) -> Iterator[tuple[Any, dict[str, Any]]]:
    conn = connect_database(database_url)
    try:
        identity = verify_database_target(conn)
        yield conn, identity
    finally:
        conn.close()


def _read_only(conn: Any, operation: Any) -> Any:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT txid_current_if_assigned()")
            if cur.fetchone()[0] is not None:
                raise CorrectiveValidationError("read-only inspection began with an XID")
        result = operation()
        with conn.cursor() as cur:
            cur.execute("SELECT txid_current_if_assigned()")
            if cur.fetchone()[0] is not None:
                raise CorrectiveValidationError("read-only inspection assigned an XID")
        return result


def migration_007_state(conn: Any) -> dict[str, Any]:
    def inspect() -> dict[str, Any]:
        with conn.cursor() as cur:
            cur.execute("SELECT key,value FROM meta WHERE key LIKE 'migration:%'")
            markers = {str(row[0]): str(row[1]) for row in cur.fetchall()}
            columns: dict[str, set[str]] = {}
            for table in (
                "variants",
                "historical_sales_review_decisions",
                "historical_sales_exclusions",
            ):
                cur.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema=current_schema() AND table_name=%s""",
                    (table,),
                )
                columns[table] = {str(row[0]) for row in cur.fetchall()}
            cur.execute(
                """SELECT conname FROM pg_constraint
                   WHERE connamespace=(SELECT oid FROM pg_namespace
                                       WHERE nspname=current_schema())
                     AND convalidated"""
            )
            constraints = {str(row[0]) for row in cur.fetchall()}
            cur.execute(
                """SELECT tgname FROM pg_trigger t
                   JOIN pg_class c ON c.oid=t.tgrelid
                   JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname=current_schema() AND NOT t.tgisinternal
                     AND t.tgenabled<>'D'"""
            )
            triggers = {str(row[0]) for row in cur.fetchall()}
            cur.execute(
                """SELECT p.proname FROM pg_proc p
                   JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname=current_schema()"""
            )
            functions = {str(row[0]) for row in cur.fetchall()}
            cur.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema=current_schema()
                     AND table_name='historical_sales_exclusion_authority_runs'"""
            )
            authority_table = cur.fetchone() is not None
            cur.execute(
                """SELECT table_name FROM information_schema.views
                   WHERE table_schema=current_schema()"""
            )
            views = {str(row[0]) for row in cur.fetchall()}
            cur.execute(
                """SELECT table_name,column_name,is_nullable
                   FROM information_schema.columns
                   WHERE table_schema=current_schema()
                     AND table_name='variants' AND column_name='product_id'"""
            )
            product_id = cur.fetchone()
            cur.execute(
                """SELECT viewname,definition FROM pg_views
                   WHERE schemaname=current_schema()
                     AND viewname IN ('v_current_prices','v_future_prices')"""
            )
            guarded_price_views = {
                str(row[0])
                for row in cur.fetchall()
                if "is_operational_current_variant" in str(row[1])
            }

        unique_columns_present = bool(
            TERMINAL_VARIANT_COLUMNS & columns["variants"]
            or TERMINAL_DECISION_COLUMNS
            & columns["historical_sales_review_decisions"]
            or TERMINAL_EXCLUSION_COLUMNS & columns["historical_sales_exclusions"]
        )
        unique_objects_present = bool(
            TERMINAL_CONSTRAINTS & constraints
            or TERMINAL_TRIGGERS & triggers
            or TERMINAL_FUNCTIONS & functions
            or authority_table
        )
        marker_present = markers.get(MIGRATION_007_MARKER) == "applied"
        pre_markers_present = all(
            markers.get(marker) == "applied" for marker in REQUIRED_PRE_007_MARKERS
        )
        absent = (
            pre_markers_present
            and MIGRATION_007_MARKER not in markers
            and not unique_columns_present
            and not unique_objects_present
        )
        complete = all(
            (
                pre_markers_present,
                marker_present,
                TERMINAL_VARIANT_COLUMNS <= columns["variants"],
                TERMINAL_DECISION_COLUMNS
                <= columns["historical_sales_review_decisions"],
                TERMINAL_EXCLUSION_COLUMNS
                <= columns["historical_sales_exclusions"],
                TERMINAL_CONSTRAINTS <= constraints,
                TERMINAL_TRIGGERS <= triggers,
                TERMINAL_FUNCTIONS <= functions,
                TERMINAL_VIEWS <= views,
                authority_table,
                product_id is not None and str(product_id[2]) == "YES",
                guarded_price_views == {"v_current_prices", "v_future_prices"},
            )
        )
        return {
            "classification": (
                "ABSENT" if absent else "COMPLETE" if complete else "PARTIAL_OR_DRIFTED"
            ),
            "pre_007_markers_present": pre_markers_present,
            "migration_007_marker_present": marker_present,
            "required_columns_present": complete,
            "required_constraints": len(TERMINAL_CONSTRAINTS & constraints),
            "required_triggers": len(TERMINAL_TRIGGERS & triggers),
            "required_functions": len(TERMINAL_FUNCTIONS & functions),
            "required_views": len(TERMINAL_VIEWS & views),
            "authority_table_present": authority_table,
        }

    return _read_only(conn, inspect)


def _raw_status_counts(conn: Any) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.resolution_status,COUNT(*)::int
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s GROUP BY r.resolution_status""",
            (APPROVED_RUN_ID,),
        )
        observed = {str(row[0]): int(row[1]) for row in cur.fetchall()}
    return {
        status: observed.get(status, 0)
        for status in ("RESOLVED", "UNRESOLVED", "AMBIGUOUS", "EXCLUDED")
    }


def _basic_inventory(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::int FROM variants")
        variants = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*)::int FROM historical_sales_review_decisions")
        decisions = int(cur.fetchone()[0])
        cur.execute(
            """SELECT COUNT(*)::int,
                      COUNT(*) FILTER (WHERE active)::int
               FROM historical_sales_exclusions"""
        )
        exclusion_rows, active_exclusions = (int(value) for value in cur.fetchone())
        cur.execute(
            """SELECT COUNT(*)::int FROM sales_backfill_run_facts
               WHERE sales_backfill_id=%s""",
            (APPROVED_RUN_ID,),
        )
        source_facts = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*)::int FROM sales_daily")
        sales_daily = int(cur.fetchone()[0])
        cur.execute(
            """SELECT status,evidence_json,message FROM readiness_gates
               WHERE gate_name='SALES_BACKFILL'
                 AND scope_type='GLOBAL' AND scope_id=''"""
        )
        sales_gate = cur.fetchone()
    return {
        "variants": variants,
        "decision_ledger_rows": decisions,
        "exclusion_rows": exclusion_rows,
        "active_exclusions": active_exclusions,
        "source_facts": source_facts,
        "sales_daily_rows": sales_daily,
        "sales_gate_status": str(sales_gate[0]) if sales_gate else None,
        "sales_gate_evidence": sales_gate[1] if sales_gate else {},
        "sales_gate_message": str(sales_gate[2]) if sales_gate else None,
        "raw_status_counts": _raw_status_counts(conn),
    }


def _assert_frozen_fingerprints(observed: Mapping[str, Any]) -> None:
    if dict(observed) != FROZEN_PROTECTED_FINGERPRINTS:
        raise CorrectiveValidationError("frozen protected fingerprint mismatch")


def _initial_sales_blocker_present(inventory: Mapping[str, Any]) -> bool:
    evidence = inventory.get("sales_gate_evidence") or {}
    blockers = evidence.get("blockers") if isinstance(evidence, dict) else None
    message = str(inventory.get("sales_gate_message") or "")
    return (
        blockers == ["MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED"]
        or "MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED" in message
    )


def _manifest_state(
    conn: Any, prepared: PreparedExecution
) -> tuple[str, dict[str, Any]]:
    context = ManifestExecutionContext(
        actor="phase4-published-production-correction",
        implementation_git_sha=prepared.execution.git_sha,
    )
    report = dry_run_manifest(conn, prepared.original_manifest, context)
    _assert_frozen_fingerprints(report["protected_fingerprints"])
    inventory = _read_only(conn, lambda: _basic_inventory(conn))
    if (
        inventory["source_facts"] != 59083
        or inventory["sales_daily_rows"] != 55966
        or inventory["raw_status_counts"] != EXPECTED_INITIAL_STATUS_COUNTS
        or inventory["sales_gate_status"] != "FAIL"
        or not _initial_sales_blocker_present(inventory)
    ):
        raise CorrectiveValidationError("initial Phase 4 source controls drifted")

    states = report["decision_state_counts"]
    artifacts = report["decision_artifact_counts"]
    planned = report["planned_mutations"]
    if (
        inventory["variants"] == 2049
        and inventory["decision_ledger_rows"] == 0
        and inventory["exclusion_rows"] == 0
        and inventory["active_exclusions"] == 0
        and states
        == {
            "MISSING": 343,
            "LEGACY_COMPATIBLE": 0,
            "CURRENT_PROVENANCE": 0,
            "CONFLICT": 0,
        }
        and artifacts["compatible_existing_alias_families"] == 0
        and planned
        == {
            "decision_rows": 343,
            "legacy_normalizations": 0,
            "exclusion_rows": 8,
            "alias_rows": 17,
            "change_log_rows": 343,
            "total": 711,
        }
    ):
        return "A_FROZEN_PRODUCTION_BASELINE", report
    if (
        inventory["variants"] == 2049
        and inventory["decision_ledger_rows"] == 343
        and inventory["exclusion_rows"] == 8
        and inventory["active_exclusions"] == 8
        and states
        == {
            "MISSING": 0,
            "LEGACY_COMPATIBLE": 0,
            "CURRENT_PROVENANCE": 343,
            "CONFLICT": 0,
        }
        and artifacts["compatible_existing_alias_families"] == 17
        and artifacts["sales_backfill_review_alias_rows"] == 17
        and planned["total"] == 0
        and report["readback"]["map_to_canonical"] == 55
        and report["readback"]["exclude_historical_item"] == 8
        and report["readback"]["leave_unresolved"] == 280
    ):
        return "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007", report
    raise CorrectiveValidationError("pre-007 state is neither exact State A nor State B")


def _terminal_inventory(conn: Any) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::int FROM variants")
        variants = int(cur.fetchone()[0])
        cur.execute(
            """SELECT COUNT(*)::int FROM variants
               WHERE identity_scope='HISTORICAL_ONLY'"""
        )
        historical_only = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*)::int FROM historical_sales_review_decisions")
        decisions = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*)::int FROM historical_sales_exclusion_authority_runs"
        )
        authority = int(cur.fetchone()[0])
    return {
        "variants": variants,
        "historical_only_variants": historical_only,
        "decision_ledger_rows": decisions,
        "authority_registry_rows": authority,
    }


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _flow_rows(conn: Any) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(r.resolution_status,'SOURCE'),COUNT(*)::int,
                      COALESCE(SUM(rf.observed_net_items_sold),0),
                      COALESCE(SUM(ABS(rf.observed_net_items_sold)),0),
                      COALESCE(SUM(rf.observed_net_sales),0),
                      COALESCE(SUM(ABS(rf.observed_net_sales)),0)
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s
               GROUP BY ROLLUP(r.resolution_status)""",
            (APPROVED_RUN_ID,),
        )
        rows = cur.fetchall()
    result = {}
    for status, count, units, absolute_units, sales, absolute_sales in rows:
        key = "SOURCE" if status == "SOURCE" and int(count) == 59083 else str(status)
        result[key] = {
            "rows": int(count),
            "net_units": _decimal(units),
            "absolute_units": _decimal(absolute_units),
            "net_sales": _decimal(sales),
            "absolute_sales": _decimal(absolute_sales),
        }
    return result


def final_business_controls(conn: Any) -> dict[str, Any]:
    flows = _flow_rows(conn)
    for name, expected in EXPECTED_FLOW_TOTALS.items():
        if flows.get(name) != expected:
            raise CorrectiveValidationError(f"final {name.lower()} flow controls drifted")
    if _raw_status_counts(conn) != EXPECTED_FINAL_STATUS_COUNTS:
        raise CorrectiveValidationError("final resolution status counts drifted")

    with conn.cursor() as cur:
        cur.execute(
            """SELECT resolution_method,COUNT(*)::int
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s GROUP BY resolution_method""",
            (APPROVED_RUN_ID,),
        )
        methods = {str(row[0]): int(row[1]) for row in cur.fetchall()}
        cur.execute(
            """SELECT COUNT(*)::int,COALESCE(SUM(units_sold),0),
                      COALESCE(SUM(net_sales),0)
               FROM sales_daily WHERE source='SHOPIFYQL_SALES'
                 AND sale_date BETWEEN %s AND %s""",
            (START_DATE, END_DATE),
        )
        daily = cur.fetchone()
        cur.execute("SELECT COUNT(*)::int FROM purchase_orders")
        po_count = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*)::int FROM purchase_order_lines")
        po_line_count = int(cur.fetchone()[0])
    if methods != EXPECTED_FINAL_METHOD_COUNTS:
        raise CorrectiveValidationError("final resolution method counts drifted")
    sales_daily = {
        "rows": int(daily[0]),
        "net_units": _decimal(daily[1]),
        "net_sales": _decimal(daily[2]),
    }
    if sales_daily != EXPECTED_SALES_DAILY:
        raise CorrectiveValidationError("final sales_daily controls drifted")

    exclusion = derive_exclusion_integrity(conn, APPROVED_RUN_ID)
    if (
        not exclusion.passed
        or exclusion.diagnostics
        or exclusion.active_exclusion_keys != 198
        or exclusion.excluded_source_facts != 1654
        or exclusion.reason_buckets != EXPECTED_REASON_BUCKETS
    ):
        raise CorrectiveValidationError("final exclusion integrity is not exact")

    readiness = po_readiness(conn)
    global_gates = {
        str(gate["gate_name"]): gate
        for gate in readiness["gates"]
        if gate["scope_type"] == "GLOBAL" and gate["scope_id"] == ""
    }
    gates = {
        name: str(gate["status"]) for name, gate in global_gates.items()
    }
    sales_gate = global_gates.get("SALES_BACKFILL") or {}
    sales_evidence = sales_gate.get("evidence") or {}
    po_blockers = [
        blocker
        for blocker in readiness["blockers"]
        if blocker.get("type") == "READINESS_GATE"
    ]
    if gates != EXPECTED_GATE_STATUSES:
        raise CorrectiveValidationError("final canonical readiness gates drifted")
    if (
        sales_evidence.get("sales_backfill_id") != APPROVED_RUN_ID
        or list(sales_evidence.get("blockers") or [])
        or not sales_evidence.get("control_totals_reconciled")
        or not sales_evidence.get("canonical_aggregate_rebuilt")
        or not (sales_evidence.get("exclusion_integrity") or {}).get("passed")
    ):
        raise CorrectiveValidationError("final SALES_BACKFILL gate evidence drifted")
    if (
        readiness["po_generation_enabled"]
        or len(po_blockers) != 1
        or po_blockers[0]["detail"].get("gate_name") != "VENDOR_RULES"
        or not str(po_blockers[0]["detail"].get("message") or "").strip()
        or po_count != 0
        or po_line_count != 0
    ):
        raise CorrectiveValidationError("final PO fail-closed controls drifted")

    fingerprints = protected_state_fingerprints(conn)
    return {
        "flows": flows,
        "resolution_methods": methods,
        "sales_daily": sales_daily,
        "exclusion_integrity": {
            "passed": exclusion.passed,
            "diagnostics": list(exclusion.diagnostics),
            "active_source_keys": exclusion.active_exclusion_keys,
            "excluded_facts": exclusion.excluded_source_facts,
            "reason_buckets": exclusion.reason_buckets,
        },
        "gate_statuses": gates,
        "sales_backfill_gate_evidence": sales_evidence,
        "po_generation_enabled": readiness["po_generation_enabled"],
        "po_blocking_gate": "VENDOR_RULES",
        "purchase_orders": po_count,
        "purchase_order_lines": po_line_count,
        "production_final_protected_fingerprints": fingerprints,
    }


def classify_state(
    conn: Any, prepared: PreparedExecution
) -> tuple[str, dict[str, Any]]:
    migration = migration_007_state(conn)
    if migration["classification"] == "ABSENT":
        state, report = _manifest_state(conn, prepared)
        return state, {"migration_007": migration, "manifest": report}
    if migration["classification"] != "COMPLETE":
        raise CorrectiveValidationError("migration 007 is partial or drifted")

    terminal_context = TerminalExecutionContext(
        actor="phase4-published-production-correction",
        expected_execution_git_sha=prepared.execution.git_sha,
    )
    terminal = dry_run_terminal_disposition(
        conn, prepared.terminal_artifact, terminal_context
    )
    inventory = _read_only(conn, lambda: _terminal_inventory(conn))
    if terminal["classification"] == "PRE_TERMINAL_EXACT":
        _assert_frozen_fingerprints(terminal["protected_fingerprints"])
        if terminal["diagnostics"] or terminal["planned_mutations"] != {
            "restored_variants": 43,
            "terminal_decisions": 280,
            "original_exclusion_normalizations": 8,
            "terminal_aliases": 39,
            "active_exclusions": 198,
            "authority_registrations": 1,
        }:
            raise CorrectiveValidationError("terminal prestate plan drifted")
        if inventory != {
            "variants": 2049,
            "historical_only_variants": 0,
            "decision_ledger_rows": 343,
            "authority_registry_rows": 0,
        }:
            raise CorrectiveValidationError("State C inventory drifted")
        return "C_POST_007_PRE_TERMINAL", {
            "migration_007": migration,
            "terminal": terminal,
        }
    if terminal["classification"] != "CURRENT_TERMINAL_EXACT":
        raise CorrectiveValidationError(
            "terminal state is not an exact permitted classification"
        )
    if (
        terminal["diagnostics"]
        or terminal["effective_actions"]
        != {"RESTORE": 43, "MAP": 102, "EXCLUDE": 198, "LEAVE_UNRESOLVED": 0}
        or terminal["historical_only_variants"] != 43
        or terminal["active_exclusions"] != 198
        or terminal["approved_alias_families"] != 56
        or terminal["planned_mutations"]
        != {
            "restored_variants": 0,
            "terminal_decisions": 0,
            "original_exclusion_normalizations": 0,
            "terminal_aliases": 0,
            "active_exclusions": 0,
            "authority_registrations": 0,
        }
        or inventory
        != {
            "variants": 2092,
            "historical_only_variants": 43,
            "decision_ledger_rows": 631,
            "authority_registry_rows": 1,
        }
    ):
        raise CorrectiveValidationError("current terminal inventory drifted")
    if terminal["source_lifecycle"] == "PRE_REBUILD":
        _assert_frozen_fingerprints(terminal["protected_fingerprints"])
        return "D_CURRENT_TERMINAL_PRE_REBUILD", {
            "migration_007": migration,
            "terminal": terminal,
        }
    if terminal["source_lifecycle"] == "POST_REBUILD":
        final = _read_only(conn, lambda: final_business_controls(conn))
        return "E_CURRENT_TERMINAL_POST_REBUILD", {
            "migration_007": migration,
            "terminal": terminal,
            "final": final,
        }
    raise CorrectiveValidationError("current terminal source lifecycle is invalid")


def apply_original_manifest_stage(
    conn: Any, prepared: PreparedExecution, *, actor: str
) -> dict[str, Any]:
    context = ManifestExecutionContext(
        actor=actor, implementation_git_sha=prepared.execution.git_sha
    )
    result = persist_manifest_decisions(conn, prepared.original_manifest, context)
    if (
        result["committed_mutations"] != 711
        or result["inserted_decisions"] != 343
        or result["inserted_alias_families"] != 17
        or result["upserted_exclusions"] != 8
        or result["protected_fingerprints_before"]
        != result["protected_fingerprints_after"]
    ):
        raise CorrectiveValidationError("original manifest mutation controls drifted")
    _assert_frozen_fingerprints(result["protected_fingerprints_after"])
    return result


def apply_migration_007_stage(conn: Any, prepared: PreparedExecution) -> dict[str, Any]:
    sql = MIGRATION_007.read_text(encoding="utf-8")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        acquire_backfill_transaction_lock(conn)
        preflight = validate_database_preflight(conn, prepared.original_manifest)
        if preflight.decision_state_counts != {
            "MISSING": 0,
            "LEGACY_COMPATIBLE": 0,
            "CURRENT_PROVENANCE": 343,
            "CONFLICT": 0,
        }:
            raise CorrectiveValidationError("migration stage did not re-prove State B")
        inventory = _basic_inventory(conn)
        if (
            inventory["variants"] != 2049
            or inventory["decision_ledger_rows"] != 343
            or inventory["exclusion_rows"] != 8
            or inventory["active_exclusions"] != 8
            or inventory["source_facts"] != 59083
            or inventory["sales_daily_rows"] != 55966
            or inventory["raw_status_counts"] != EXPECTED_INITIAL_STATUS_COUNTS
            or inventory["sales_gate_status"] != "FAIL"
        ):
            raise CorrectiveValidationError("migration stage State B inventory drifted")
        before = protected_state_fingerprints(conn)
        _assert_frozen_fingerprints(before)
        conn.execute(sql)
        conn.execute(
            """INSERT INTO meta(key,value) VALUES (%s,'applied')
               ON CONFLICT(key) DO UPDATE
               SET value='applied',updated_at=now()""",
            (MIGRATION_007_MARKER,),
        )
        after = protected_state_fingerprints(conn)
        if after != before:
            raise CorrectiveValidationError("migration 007 changed protected state")
    return {
        "migration": "007_phase4_terminal_disposition.sql",
        "transaction_isolation": "serializable",
        "protected_fingerprints_before": before,
        "protected_fingerprints_after": after,
    }


def apply_terminal_stage(
    conn: Any, prepared: PreparedExecution, *, actor: str
) -> dict[str, Any]:
    context = TerminalExecutionContext(
        actor=actor,
        expected_execution_git_sha=prepared.execution.git_sha,
    )
    result = persist_terminal_disposition(
        conn, prepared.terminal_artifact, context
    )
    if (
        result["classification_before"] != "PRE_TERMINAL_EXACT"
        or result["classification_after"] != "CURRENT_TERMINAL_EXACT"
        or result["committed_mutations"] != 858
        or result["planned_mutations"]
        != {
            "restored_variants": 43,
            "terminal_decisions": 280,
            "original_exclusion_normalizations": 8,
            "terminal_aliases": 39,
            "active_exclusions": 198,
            "authority_registrations": 1,
        }
        or result["protected_fingerprints_before"]
        != result["protected_fingerprints_after"]
    ):
        raise CorrectiveValidationError("terminal mutation controls drifted")
    _assert_frozen_fingerprints(result["protected_fingerprints_after"])
    return result


def prove_terminal_noop(
    conn: Any, prepared: PreparedExecution, *, actor: str
) -> dict[str, Any]:
    context = TerminalExecutionContext(
        actor=actor,
        expected_execution_git_sha=prepared.execution.git_sha,
    )
    result = persist_terminal_disposition(
        conn, prepared.terminal_artifact, context
    )
    if (
        result["classification_before"] != "CURRENT_TERMINAL_EXACT"
        or result["classification_after"] != "CURRENT_TERMINAL_EXACT"
        or result["committed_mutations"] != 0
        or any(result["planned_mutations"].values())
        or result["protected_fingerprints_before"]
        != result["protected_fingerprints_after"]
    ):
        raise CorrectiveValidationError("mandatory terminal replay was not zero DML")
    _assert_frozen_fingerprints(result["protected_fingerprints_after"])
    return result


def apply_rebuild_stage(conn: Any, prepared: PreparedExecution) -> dict[str, Any]:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        acquire_backfill_transaction_lock(conn)
        before = inspect_terminal_state(
            conn, prepared.terminal_artifact, prepared.execution.git_sha
        )
        if (
            before["classification"] != "CURRENT_TERMINAL_EXACT"
            or before["source_lifecycle"] != "PRE_REBUILD"
        ):
            raise CorrectiveValidationError("rebuild stage did not re-prove exact State D")
        if _terminal_inventory(conn) != {
            "variants": 2092,
            "historical_only_variants": 43,
            "decision_ledger_rows": 631,
            "authority_registry_rows": 1,
        }:
            raise CorrectiveValidationError("rebuild stage State D inventory drifted")
        _assert_frozen_fingerprints(before["protected_fingerprints"])
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sales_backfill_id::text FROM sales_backfill_runs
                   WHERE start_date=%s AND end_date=%s
                     AND coverage_complete=TRUE AND pages_complete=TRUE
                   ORDER BY started_at DESC LIMIT 1""",
                (START_DATE, END_DATE),
            )
            selected = cur.fetchone()
        if selected is None or str(selected[0]) != APPROVED_RUN_ID:
            raise CorrectiveValidationError("canonical rerun selector did not choose approved run")
        result = rerun_sales_identity_resolution(
            conn, start_date=START_DATE, end_date=END_DATE
        )
        if (
            result.get("status") != "PASS"
            or result.get("blockers") != []
            or result.get("unique_source_facts") != 59083
            or result.get("resolved_rows") != 57429
            or result.get("excluded_rows") != 1654
            or result.get("unresolved_rows") != 0
            or result.get("ambiguous_rows") != 0
            or result.get("expected_chunks") != 21
            or result.get("completed_chunks") != 21
            or result.get("expected_pages") != 70
            or result.get("completed_pages") != 70
        ):
            raise CorrectiveValidationError("canonical SALES_BACKFILL finalizer did not pass")
        final = final_business_controls(conn)
    return {**result, "verified_final_controls": final}


def execute(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    prepared = prepare_execution(
        environment,
        expected_git_sha=args.expected_execution_git_sha,
        expected_tree_sha=args.expected_execution_tree_sha,
    )
    _clear_libpq_environment()

    stages: list[dict[str, Any]] = []
    seen_states: set[str] = set()
    while True:
        with verified_connection(prepared.database_url) as (conn, database_identity):
            state, evidence = classify_state(conn, prepared)
        if state in seen_states:
            raise CorrectiveValidationError("corrective state machine did not advance")
        seen_states.add(state)
        stages.append({"state": state, "read_only_evidence": evidence})

        if state == "E_CURRENT_TERMINAL_POST_REBUILD":
            return {
                "result": "PHASE4_PUBLISHED_PRODUCTION_RECONCILED",
                "execution_git_sha": prepared.execution.git_sha,
                "execution_tree_sha": prepared.execution.tree_sha,
                "database_identity": database_identity,
                "initial_state": stages[0]["state"],
                "final_state": state,
                "stages": stages,
                "repeat_safe": len(stages) == 1,
            }
        if state == "A_FROZEN_PRODUCTION_BASELINE":
            with verified_connection(prepared.database_url) as (conn, _):
                mutation = apply_original_manifest_stage(
                    conn, prepared, actor=args.actor
                )
            stages[-1]["permitted_action"] = "ORIGINAL_MANIFEST"
            stages[-1]["mutation_evidence"] = mutation
            continue
        if state == "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007":
            if evidence["manifest"]["planned_mutations"]["total"] != 0:
                raise CorrectiveValidationError("State B manifest replay is not a no-op")
            with verified_connection(prepared.database_url) as (conn, _):
                mutation = apply_migration_007_stage(conn, prepared)
            stages[-1]["permitted_action"] = "MIGRATION_007"
            stages[-1]["mutation_evidence"] = mutation
            continue
        if state == "C_POST_007_PRE_TERMINAL":
            with verified_connection(prepared.database_url) as (conn, _):
                mutation = apply_terminal_stage(conn, prepared, actor=args.actor)
            stages[-1]["permitted_action"] = "TERMINAL_PERSISTENCE"
            stages[-1]["mutation_evidence"] = mutation
            continue
        if state == "D_CURRENT_TERMINAL_PRE_REBUILD":
            with verified_connection(prepared.database_url) as (conn, _):
                replay = prove_terminal_noop(conn, prepared, actor=args.actor)
            with verified_connection(prepared.database_url) as (conn, _):
                mutation = apply_rebuild_stage(conn, prepared)
            stages[-1]["mandatory_terminal_replay"] = replay
            stages[-1]["permitted_action"] = "LOCAL_RERESOLUTION_FINALIZATION"
            stages[-1]["mutation_evidence"] = mutation
            continue
        raise CorrectiveValidationError("unrecognized corrective state")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Correct published-production Phase 4 historical identity state"
    )
    parser.add_argument("--expected-execution-git-sha", required=True)
    parser.add_argument("--expected-execution-tree-sha", required=True)
    parser.add_argument(
        "--actor", default="phase4-published-production-corrective-executor"
    )
    return parser


def _safe_error(exc: BaseException, environment: Mapping[str, str]) -> str:
    message = str(exc)
    for name in (
        "DATABASE_URL",
        "RECONCILIATION_REVIEW_TOKEN",
        "PHASE4_REVIEW_TOKEN_INPUT",
    ):
        value = environment.get(name)
        if value:
            message = message.replace(value, "[REDACTED]")
    message = message.replace("\r", " ").replace("\n", " ")
    message = re.sub(
        r"(?i)postgres(?:ql)?://[^\s]+", "postgresql://[REDACTED]", message
    )
    return message[:1000] or type(exc).__name__


def main(argv: Sequence[str] | None = None) -> int:
    try:
        report = execute(argv)
    except BaseException as exc:
        print(
            f"ERROR: {type(exc).__name__}: {_safe_error(exc, os.environ)}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

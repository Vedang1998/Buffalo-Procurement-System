"""Startup/health layer.

Distinguishes (owner requirement 7):
  - application health,
  - database connectivity,
  - schema/migration state,
  - seed state,
  - Shopify credential availability,
  - every effective readiness gate,
  - canonical PO readiness.

Missing Shopify credentials must not prevent the app from starting; the
dependent gates simply remain FAIL and Shopify is reported "not configured".

No-SQLite production guard: the production runtime requires PostgreSQL.
"""
from __future__ import annotations

import os
from typing import Any

from .readiness import po_readiness

APP_VERSION = "1.3.0"

CORE_TABLES = [
    "meta", "variants", "variant_aliases", "vendors", "supplier_offers",
    "prices", "readiness_gates", "catalog_sync_runs", "sales_backfill_runs",
    "sales_backfill_chunks", "sales_backfill_pages", "sales_backfill_run_facts",
    "shopify_sales_daily_raw", "historical_sales_review_decisions",
    "seed_import_records",
]

SHOPIFY_ENV_VARS = ["SHOPIFY_SHOP", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"]


def database_url_guard() -> dict[str, Any]:
    """Fail-closed: reject missing DATABASE_URL and any SQLite production usage."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return {"ok": False, "configured": False, "reason": "DATABASE_URL is not configured"}
    if url.startswith("sqlite") or url.endswith(".db"):
        return {"ok": False, "configured": True,
                "reason": "SQLite is not permitted as the production database; PostgreSQL required"}
    if not (url.startswith("postgres://") or url.startswith("postgresql://")):
        return {"ok": False, "configured": True,
                "reason": "DATABASE_URL must be a PostgreSQL connection string"}
    return {"ok": True, "configured": True}


def shopify_credentials_status() -> dict[str, Any]:
    """Report availability only. Never log or return secret values."""
    missing = [k for k in SHOPIFY_ENV_VARS if not os.getenv(k)]
    return {"configured": not missing, "missing_vars": missing}


def check_database(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"ok": True}


def check_schema(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
        present = {r[0] for r in cur.fetchall()}
    missing = [t for t in CORE_TABLES if t not in present]
    return {"ok": not missing, "tables_present": len(present), "missing_core_tables": missing}


def check_seed(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM seed_import_records WHERE validation_result='PASS'")
        passing = cur.fetchone()[0]
        cur.execute(
            "SELECT imported_at, validation_result, actual_counts_json"
            " FROM seed_import_records ORDER BY import_id DESC LIMIT 1"
        )
        latest = cur.fetchone()
    if latest is None:
        return {"ok": False, "imported": False, "reason": "no seed import record"}
    return {
        "ok": latest[1] == "PASS",
        "imported": True,
        "latest_import_at": str(latest[0]),
        "latest_validation_result": latest[1],
        "latest_actual_counts": latest[2],
        "passing_imports": passing,
    }


def check_gate(conn: Any, gate_name: str) -> dict[str, Any]:
    if gate_name == "CATALOG_SYNC":
        from .catalog import authoritative_catalog_gate

        gate = authoritative_catalog_gate(conn)
        return {
            "status": gate["status"],
            "severity": gate["severity"],
            "blocks_po": gate["blocks_po"],
            "message": gate["message"],
            "evidence": gate["evidence"],
            "checked_at": str(gate["checked_at"]),
            "ok": gate["status"] == "PASS",
        }
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, severity, blocks_po, message, evidence_json, checked_at FROM readiness_gates"
            " WHERE gate_name=%s AND scope_type='GLOBAL' ORDER BY checked_at DESC LIMIT 1",
            (gate_name,),
        )
        row = cur.fetchone()
    if row is None:
        return {"status": "MISSING", "ok": False}
    return {
        "status": row[0], "severity": row[1], "blocks_po": row[2],
        "message": row[3], "evidence": row[4] or {},
        "checked_at": str(row[5]), "ok": row[0] == "PASS",
    }


def _unavailable_po_readiness(reason: str) -> dict[str, Any]:
    """Return an explicit fail-closed operational result when evaluation cannot run."""
    return {
        "po_generation_enabled": False,
        "scope": {"vendor_id": None, "variant_id": None, "run_id": None},
        "applicable_gate_names": [],
        "blockers": [
            {
                "type": "READINESS_UNAVAILABLE",
                "detail": {"message": reason},
            }
        ],
        "gates": [],
    }


def _global_gate_health_map(readiness: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Preserve the existing name-keyed health shape for effective global gates."""
    result: dict[str, dict[str, Any]] = {}
    for gate in readiness["gates"]:
        if gate["scope_type"] != "GLOBAL" or gate["scope_id"] != "":
            continue
        result[gate["gate_name"]] = {
            "status": gate["status"],
            "severity": gate["severity"],
            "blocks_po": gate["blocks_po"],
            "message": gate["message"],
            "evidence": gate["evidence"],
            "checked_at": (
                str(gate["checked_at"]) if gate["checked_at"] is not None else None
            ),
            "ok": gate["status"] == "PASS",
        }
    return result


def _effective_global_gate(
    readiness: dict[str, Any], gate_name: str
) -> dict[str, Any] | None:
    return next(
        (
            gate
            for gate in readiness["gates"]
            if gate["gate_name"] == gate_name
            and gate["scope_type"] == "GLOBAL"
            and gate["scope_id"] == ""
        ),
        None,
    )


_SALES_RUN_COLUMNS = (
    "sales_backfill_id",
    "start_date",
    "end_date",
    "started_at",
    "completed_at",
    "status",
    "completed_chunks",
    "expected_chunks",
    "completed_pages",
    "expected_pages",
    "unique_source_facts",
    "resolved_rows",
    "unresolved_rows",
    "ambiguous_rows",
    "excluded_rows",
    "coverage_complete",
    "pages_complete",
    "source_facts_persisted",
    "idempotency_verified",
    "control_totals_reconciled",
    "canonical_aggregate_rebuilt",
    "last_checkpoint_at",
)


def _sales_run_row(cur: Any, run_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"""SELECT {','.join(_SALES_RUN_COLUMNS)}
             FROM sales_backfill_runs WHERE sales_backfill_id::text=%s""",
        (run_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    result = dict(zip(_SALES_RUN_COLUMNS, row, strict=True))
    result["sales_backfill_id"] = str(result["sales_backfill_id"])
    return result


def data_sync_run_status(conn: Any) -> dict[str, Any]:
    """Return SELECT-only operational evidence for catalog and historical runs."""
    from .historical_sales import latest_reviewable_run

    readiness = po_readiness(conn)
    catalog_gate = _effective_global_gate(readiness, "CATALOG_SYNC")
    sales_gate = _effective_global_gate(readiness, "SALES_BACKFILL")

    catalog_evidence = (catalog_gate or {}).get("evidence", {})
    catalog_run_id = catalog_evidence.get("catalog_sync_id")
    catalog_run = None
    if catalog_run_id is not None:
        catalog_run = {
            "catalog_sync_id": catalog_run_id,
            "started_at": catalog_evidence.get("started_at"),
            "completed_at": catalog_evidence.get("completed_at"),
            "status": catalog_evidence.get("run_status"),
            "shopify_api_version": catalog_evidence.get("shopify_api_version"),
            "shopify_reported_variant_count": catalog_evidence.get(
                "shopify_reported_variant_count"
            ),
            "live_rows_received": catalog_evidence.get("live_rows_received"),
            "exact_current_ids": catalog_evidence.get("exact_current_ids"),
            "new_live_variants": catalog_evidence.get("new_live_variants"),
            "pagination_complete": catalog_evidence.get("pagination_complete"),
            "unresolved_blockers": catalog_evidence.get("unresolved_blockers"),
        }

    sales_run_id = None
    sales_selection = "CANONICAL_READINESS_GATE"
    if sales_gate is not None:
        sales_run_id = (sales_gate.get("evidence") or {}).get("sales_backfill_id")

    with conn.cursor() as cur:
        if sales_run_id is None:
            reviewable = latest_reviewable_run(cur)
            if reviewable is not None:
                sales_run_id = reviewable[0]
                sales_selection = "LATEST_REVIEWABLE_RUN"
            else:
                cur.execute(
                    """SELECT sales_backfill_id FROM sales_backfill_runs
                       ORDER BY started_at DESC, sales_backfill_id DESC LIMIT 1"""
                )
                row = cur.fetchone()
                sales_run_id = str(row[0]) if row is not None else None
                sales_selection = "LATEST_ATTEMPT_DIAGNOSTIC"
        sales_run = _sales_run_row(cur, str(sales_run_id)) if sales_run_id else None

    return {
        "catalog": {
            "selection": "AUTHORITATIVE_CATALOG_ATTEMPT",
            "run": catalog_run,
            "readiness": {
                "status": catalog_gate["status"] if catalog_gate else "MISSING",
                "message": catalog_gate["message"] if catalog_gate else None,
                "blockers": list(catalog_evidence.get("readiness_blockers", [])),
            },
        },
        "historical_sales": {
            "selection": sales_selection,
            "run": sales_run,
            "readiness": {
                "status": sales_gate["status"] if sales_gate else "MISSING",
                "message": sales_gate["message"] if sales_gate else None,
                "blockers": list((sales_gate or {}).get("evidence", {}).get("blockers", [])),
            },
        },
    }


def full_health() -> dict[str, Any]:
    unavailable = _unavailable_po_readiness(
        "Canonical readiness is unavailable until database and schema checks pass."
    )
    report: dict[str, Any] = {
        "application": {"ok": True, "service": "buffalo-procurement-os", "version": APP_VERSION},
        "shopify_credentials": shopify_credentials_status(),
        "po_generation_enabled": False,
        "po_readiness": unavailable,
    }
    guard = database_url_guard()
    report["database_url_guard"] = guard
    if not guard["ok"]:
        report["database"] = {"ok": False, "reason": guard["reason"]}
        report["schema"] = {"ok": False, "reason": "database unavailable"}
        report["seed"] = {"ok": False, "reason": "database unavailable"}
        report["gates"] = {}
        return report

    import psycopg

    try:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            report["database"] = check_database(conn)
            report["schema"] = check_schema(conn)
            if report["schema"]["ok"]:
                report["seed"] = check_seed(conn)
                readiness = po_readiness(conn)
                report["po_readiness"] = readiness
                report["po_generation_enabled"] = readiness["po_generation_enabled"]
                report["gates"] = _global_gate_health_map(readiness)
            else:
                report["seed"] = {"ok": False, "reason": "schema incomplete"}
                report["gates"] = {}
    except Exception as exc:  # connectivity failure is a report, not a crash
        report["database"] = {"ok": False, "reason": f"connection failed: {type(exc).__name__}"}
        report["schema"] = {"ok": False, "reason": "database unavailable"}
        report["seed"] = {"ok": False, "reason": "database unavailable"}
        report["gates"] = {}
    return report

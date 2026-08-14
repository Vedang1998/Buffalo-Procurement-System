"""Startup/health layer.

Distinguishes (owner requirement 7):
  - application health,
  - database connectivity,
  - schema/migration state,
  - seed state,
  - Shopify credential availability,
  - CATALOG_SYNC readiness,
  - SALES_BACKFILL readiness.

Missing Shopify credentials must not prevent the app from starting; the
dependent gates simply remain FAIL and Shopify is reported "not configured".

No-SQLite production guard: the production runtime requires PostgreSQL.
"""
from __future__ import annotations

import os
from typing import Any

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


def full_health() -> dict[str, Any]:
    report: dict[str, Any] = {
        "application": {"ok": True, "service": "buffalo-procurement-os", "version": APP_VERSION},
        "shopify_credentials": shopify_credentials_status(),
        "po_generation_enabled": False,
    }
    guard = database_url_guard()
    report["database_url_guard"] = guard
    if not guard["ok"]:
        report["database"] = {"ok": False, "reason": guard["reason"]}
        report["schema"] = {"ok": False, "reason": "database unavailable"}
        report["seed"] = {"ok": False, "reason": "database unavailable"}
        report["gates"] = {"CATALOG_SYNC": {"status": "UNKNOWN", "ok": False},
                           "SALES_BACKFILL": {"status": "UNKNOWN", "ok": False}}
        return report

    import psycopg

    try:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            report["database"] = check_database(conn)
            report["schema"] = check_schema(conn)
            if report["schema"]["ok"]:
                report["seed"] = check_seed(conn)
                report["gates"] = {
                    "CATALOG_SYNC": check_gate(conn, "CATALOG_SYNC"),
                    "SALES_BACKFILL": check_gate(conn, "SALES_BACKFILL"),
                }
            else:
                report["seed"] = {"ok": False, "reason": "schema incomplete"}
                report["gates"] = {"CATALOG_SYNC": {"status": "UNKNOWN", "ok": False},
                                   "SALES_BACKFILL": {"status": "UNKNOWN", "ok": False}}
    except Exception as exc:  # connectivity failure is a report, not a crash
        report["database"] = {"ok": False, "reason": f"connection failed: {type(exc).__name__}"}
        report["schema"] = {"ok": False, "reason": "database unavailable"}
        report["seed"] = {"ok": False, "reason": "database unavailable"}
        report["gates"] = {"CATALOG_SYNC": {"status": "UNKNOWN", "ok": False},
                           "SALES_BACKFILL": {"status": "UNKNOWN", "ok": False}}
    return report

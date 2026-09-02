#!/usr/bin/env python3
"""Apply the PostgreSQL schema + migrations transactionally and idempotently.

All statements use IF NOT EXISTS / ON CONFLICT semantics, and each file is
applied inside a single transaction: a failure leaves nothing partially
applied from that file.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

MIGRATION_ORDER = [
    "schema_postgres.sql",
    "001_v1_3_catalog_sales.sql",
    "002_seed_import_records.sql",
    "003_phase3_reconciliation.sql",
    "004_identity_decision_invariants.sql",
    "005_identity_investigation.sql",
    "006_phase4_sales_backfill.sql",
    "007_phase4_terminal_disposition.sql",
]


def apply_schema_connection(conn: Any, db_dir: Path) -> list[str]:
    """Apply every file transactionally on an already-scoped connection."""
    applied = []
    for name in MIGRATION_ORDER:
        sql = (db_dir / name).read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                "INSERT INTO meta(key,value) VALUES (%s,'applied')"
                " ON CONFLICT(key) DO UPDATE SET value='applied', updated_at=now()",
                (f"migration:{name}",),
            )
        applied.append(name)
    return applied


def apply_schema(db_dir: Path, database_url: str) -> list[str]:
    import psycopg

    with psycopg.connect(database_url) as conn:
        return apply_schema_connection(conn, db_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", type=Path, default=Path(__file__).resolve().parents[1] / "db")
    ap.add_argument("--database-url", required=True)
    args = ap.parse_args()
    for name in apply_schema(args.db_dir, args.database_url):
        print(f"applied: {name}")


if __name__ == "__main__":
    main()

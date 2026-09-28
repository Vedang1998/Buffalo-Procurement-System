#!/usr/bin/env python3
"""Capture a normalized owned inventory snapshot without contacting Shopify."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.inventory import capture_daily_inventory


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist a strict normalized inventory JSON fixture/feed."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--business-date", type=date.fromisoformat, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        parser.error("input must be a JSON array or an object containing a rows array")

    import psycopg

    with psycopg.connect(database_url) as connection:
        result = capture_daily_inventory(
            connection,
            business_date=args.business_date,
            rows=rows,
            source=args.source,
            captured_at=datetime.now(timezone.utc),
        )
    print(
        json.dumps(
            {
                "inventory_snapshot_run_id": result["inventory_snapshot_run_id"],
                "idempotent_replay": result["idempotent_replay"],
                "rows_written": result["rows_written"],
                "readiness_status": result["readiness"]["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

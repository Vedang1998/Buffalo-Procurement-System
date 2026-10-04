#!/usr/bin/env python3
"""Operator-only synthetic staging role/ACL transition; never called at startup."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procurement_os.synthetic_staging_database import provision_contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-database", required=True)
    args = parser.parse_args()
    url = os.environ.get("BUFFALO_SYNTHETIC_PROVISIONING_URL")
    if not url or args.confirm_database != "buffalo_synthetic_staging":
        raise SystemExit("explicit synthetic staging provisioning input is required")
    if os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL"):
        raise SystemExit("ambient application/test database URLs are forbidden")
    with psycopg.connect(url, connect_timeout=5, autocommit=False) as conn:
        if conn.info.dbname != args.confirm_database:
            raise SystemExit("connected provisioning database differs")
        provision_contract(conn)
        conn.commit()
    print("synthetic staging database contract provisioned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

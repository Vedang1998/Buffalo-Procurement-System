#!/usr/bin/env python3
"""Operator-only synthetic staging bootstrap/transition; never called at startup."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE,
    bootstrap_roles,
    provision_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("bootstrap", "provision"))
    parser.add_argument("--confirm-database", required=True)
    args = parser.parse_args()
    variable = (
        "BUFFALO_SYNTHETIC_BOOTSTRAP_URL"
        if args.phase == "bootstrap"
        else "BUFFALO_SYNTHETIC_PROVISIONING_URL"
    )
    url = os.environ.get(variable)
    if not url or args.confirm_database != EXPECTED_DATABASE:
        raise SystemExit("explicit synthetic staging provisioning input is required")
    if os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL"):
        raise SystemExit("ambient application/test database URLs are forbidden")
    with psycopg.connect(url, connect_timeout=5, autocommit=False) as conn:
        if conn.info.dbname != args.confirm_database:
            raise SystemExit("connected provisioning database differs")
        changed = (
            bootstrap_roles(conn)
            if args.phase == "bootstrap"
            else provision_contract(conn)
        )
        conn.commit()
    disposition = "changed" if changed else "verified-no-op"
    print(f"synthetic staging {args.phase} {disposition}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

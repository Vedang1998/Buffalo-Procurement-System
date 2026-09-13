#!/usr/bin/env python3
"""Initialize the owned, isolated Buffalo synthetic owner-demo database."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg import sql

from apply_schema import (
    MAPPING_MIGRATION_NAME,
    MIGRATION_ORDER,
    _verify_or_apply_mapping_release,
    apply_verified_legacy_file,
)


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "db"
SCHEMA = "qa_mapping_test"
VENDOR_ID = "00000000-0000-4000-8000-000000000001"
VARIANT_ID = "1001"
BLOCKED_VARIANT_ID = "2002"
DEMO_CONTRACT = "BUFFALO_SYNTHETIC_OWNER_DEMO_V1"


def _require_owned_target(conn: psycopg.Connection, database_url: str) -> None:
    parsed = urlparse(database_url)
    if parsed.hostname not in {"127.0.0.1", "::1"}:
        raise RuntimeError("synthetic demo database must be loopback")
    row = conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::integer,"
        "pg_catalog.inet_server_addr()::text,session_user::text,current_user::text"
    ).fetchone()
    assert row is not None
    if not str(row[0]).endswith("_demo") or int(row[1]) // 10000 != 16:
        raise RuntimeError("synthetic demo requires an owned PostgreSQL 16 *_demo database")
    try:
        server_address = ipaddress.ip_interface(str(row[2])).ip
    except ValueError as exc:
        raise RuntimeError("synthetic demo PostgreSQL address is malformed") from exc
    if not server_address.is_loopback:
        raise RuntimeError("synthetic demo PostgreSQL server is not loopback")
    if (str(row[3]), str(row[4])) != ("qa_release_login", "qa_mapping_owner"):
        raise RuntimeError("synthetic demo maintenance role pair differs")


def _sales_digest(rows: list[tuple[object, ...]]) -> str:
    encoded = json.dumps(
        [[str(value) if value is not None else None for value in row] for row in rows],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _apply_legacy(conn: psycopg.Connection, name: str, *, schema_oid: int) -> None:
    with conn.transaction():
        apply_verified_legacy_file(
            conn,
            DB_DIR,
            name,
            schema_oid=schema_oid,
        )


def _seed_pre_price(conn: psycopg.Connection, business_date: date) -> None:
    order_day = business_date.strftime("%A").upper()
    delivery_day = (business_date + timedelta(days=3)).strftime("%A").upper()
    with conn.transaction():
        conn.execute(
            "INSERT INTO vendors(vendor_id,vendor_name,active) VALUES (%s,%s,TRUE)",
            (VENDOR_ID, "Synthetic Southern"),
        )
        conn.execute(
            """INSERT INTO vendor_operating_rules(
                   vendor_id,order_days,order_cutoff_local,timezone_name,
                   expected_delivery_days,order_cycle_days,lead_time_days,
                   lead_time_variability_days,reliability_pct,minimum_type,
                   minimum_value,below_minimum_fee,loose_order_allowed,
                   loose_unit_fee,confirmation_source,confirmed_by,rules_version)
               VALUES (%s,ARRAY[%s],'23:59:59','America/New_York',ARRAY[%s],
                   2,1,0,1,'DOLLAR',20,5,FALSE,NULL,
                   'FABRICATED OWNER DEMO','synthetic:local-owner:01',1)""",
            (VENDOR_ID, order_day, delivery_day),
        )
        conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,active,
                   catalog_state,identity_scope,sku,barcode,retail_price)
               VALUES (%s,'synthetic-product-1001','Synthetic Citrus','750ML',TRUE,
                   'LIVE','CURRENT','SYN-1001','0000000001001',4.99)""",
            (VARIANT_ID,),
        )
        conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,active,
                   catalog_state,identity_scope,sku,barcode,retail_price)
               VALUES (%s,'synthetic-product-2002','Synthetic Inactive Blocker','750ML',
                   FALSE,'LIVE','CURRENT','SYN-2002',
                   '0000000002002',NULL)""",
            (BLOCKED_VARIANT_ID,),
        )
        offer_id = conn.execute(
            """INSERT INTO supplier_offers(
                   variant_id,vendor_id,supplier_sku,supplier_description,
                   package_type,size_text,raw_pack,shopify_units_per_case,
                   qualifying_units_per_case,assortment_scope,assortable,
                   active,confidence,source_file,notes)
               VALUES (%s,%s,'SUP-001','Synthetic Citrus','STANDARD','750ML',
                   '6x750ML',6,6,'PRODUCT',FALSE,TRUE,'VERIFIED',
                   'fabricated-authoritative-format-source.txt',
                   'TEST DATA — NOT FOR ORDERING') RETURNING offer_id""",
            (VARIANT_ID, VENDOR_ID),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO prices(
                   offer_id,price_state,effective_month,level_type,case_price,
                   unit_price,source_file,extraction_confidence,verified,notes)
               VALUES (%s,'current',%s,'BASE',10.01,1.6683333333,
                   'synthetic-owner-demo.csv','VERIFIED',TRUE,
                   'Legacy synthetic CURRENT fixture installed before migration 011')""",
            (offer_id, business_date.replace(day=1)),
        )


def _seed_evidence(conn: psycopg.Connection, business_date: date) -> None:
    from procurement_os.catalog import recompute_catalog_gate
    from procurement_os.inventory import capture_daily_inventory
    from procurement_os.po_ledger import recompute_open_po_reconciliation_gate
    from procurement_os.vendor_rules import recompute_vendor_rules_gates

    with conn.transaction():
        conn.execute(
            """INSERT INTO catalog_sync_runs(
                   completed_at,status,shopify_api_version,
                   shopify_reported_variant_count,live_rows_received,
                   exact_current_ids,new_live_variants,source_hash,
                   pagination_complete,notes)
               VALUES (pg_catalog.now(),'COMPLETED','FABRICATED_OFFLINE',1,1,1,0,%s,
                   TRUE,'TEST DATA — no Shopify call')""",
            ("c" * 64,),
        )
        conn.execute(
            """INSERT INTO variant_policies(
                   variant_id,policy_type,value_json,active,effective_from,
                   approved_by,note)
               VALUES (%s,'REPLENISHMENT_MODE','{"mode":"ROUTINE"}',TRUE,%s,
                   'synthetic:local-owner:01','FABRICATED OWNER DEMO POLICY')""",
            (VARIANT_ID, business_date),
        )
        for offset in range(84):
            conn.execute(
                """INSERT INTO sales_daily(sale_date,variant_id,units_sold,source)
                   VALUES (%s,%s,%s,'SYNTHETIC_DEMO')""",
                (
                    business_date - timedelta(days=84 - offset),
                    VARIANT_ID,
                    Decimal("1") if offset < 70 else Decimal("2"),
                ),
            )
    capture = capture_daily_inventory(
        conn,
        business_date=business_date,
        captured_at=datetime.combine(
            business_date, datetime.min.time(), tzinfo=timezone.utc
        )
        + timedelta(hours=12),
        source="SYNTHETIC_DEMO",
        rows=(
            {
                "variant_id": VARIANT_ID,
                "location_gid": "synthetic-location-001",
                "available_quantity": 0,
                "incoming_quantity": 0,
            },
        ),
    )
    if capture["readiness"]["status"] != "PASS":
        raise RuntimeError("synthetic inventory did not pass its exact coverage gate")
    if recompute_catalog_gate(conn)["status"] != "PASS":
        raise RuntimeError("synthetic catalog gate did not pass")
    if recompute_vendor_rules_gates(conn)["status"] != "PASS":
        raise RuntimeError("synthetic vendor-rules gate did not pass")
    evaluation_at = datetime.combine(
        business_date, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=12)
    if recompute_open_po_reconciliation_gate(conn, as_of=evaluation_at)["status"] != "PASS":
        raise RuntimeError("synthetic open-PO reconciliation gate did not pass")
    rows = conn.execute(
        """SELECT sale_date,units_sold,net_sales,distinct_orders,source,run_id::text
             FROM sales_daily WHERE variant_id=%s AND source='SYNTHETIC_DEMO'
             ORDER BY sale_date,source""",
        (VARIANT_ID,),
    ).fetchall()
    evidence = {
        "coverage_contract": "OWNED_SYNTHETIC_DEMO_DAILY_VARIANT_COVERAGE_V1",
        "source": "SYNTHETIC_DEMO",
        "sales_rows": len(rows),
        "variant_count": 1,
        "history_start": str(business_date - timedelta(days=84)),
        "history_end": str(business_date - timedelta(days=1)),
        "variant_coverage": {
            VARIANT_ID: {"row_count": len(rows), "sha256": _sales_digest(rows)}
        },
    }
    with conn.transaction():
        updated = conn.execute(
            """UPDATE readiness_gates SET status='PASS',severity='CRITICAL',
                   blocks_po=TRUE,message='Owned demo has exact synthetic sales coverage.',
                   evidence_json=%s::jsonb,checked_at=pg_catalog.transaction_timestamp()
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''
               RETURNING status""",
            (json.dumps(evidence, sort_keys=True),),
        ).fetchall()
        if updated != [("PASS",)]:
            raise RuntimeError("synthetic sales gate was not unique")
        conn.execute(
            "INSERT INTO meta(key,value) VALUES (%s,%s),(%s,%s)",
            (
                "synthetic_owner_demo_contract",
                DEMO_CONTRACT,
                "synthetic_owner_demo_business_date",
                business_date.isoformat(),
            ),
        )


def initialize(database_url: str, business_date: date) -> dict[str, object]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        _require_owned_target(conn, database_url)
        existing = conn.execute(
            "SELECT pg_catalog.to_regnamespace(%s)", (SCHEMA,)
        ).fetchone()[0]
        if existing is not None:
            marker = conn.execute(
                sql.SQL("SELECT value FROM {}.meta WHERE key=%s").format(
                    sql.Identifier(SCHEMA)
                ),
                ("synthetic_owner_demo_contract",),
            ).fetchone()
            if marker == (DEMO_CONTRACT,):
                return {"initialized": False, "contract": DEMO_CONTRACT}
            raise RuntimeError("synthetic demo schema already exists without its exact marker")
        with conn.transaction():
            conn.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION CURRENT_USER").format(
                    sql.Identifier(SCHEMA)
                )
            )
        conn.execute(
            sql.SQL("SET search_path TO {},pg_catalog").format(sql.Identifier(SCHEMA))
        )
        schema_oid = int(
            conn.execute(
                "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s", (SCHEMA,)
            ).fetchone()[0]
        )
        for name in MIGRATION_ORDER[:11]:
            _apply_legacy(conn, name, schema_oid=schema_oid)
        _seed_pre_price(conn, business_date)
        for name in MIGRATION_ORDER[11:-1]:
            _apply_legacy(conn, name, schema_oid=schema_oid)
        with conn.transaction():
            if not _verify_or_apply_mapping_release(conn, DB_DIR):
                raise RuntimeError("fresh synthetic demo did not apply mapping release")
        _seed_evidence(conn, business_date)
        return {
            "initialized": True,
            "contract": DEMO_CONTRACT,
            "business_date": business_date.isoformat(),
            "variant_id": VARIANT_ID,
            "vendor_id": VENDOR_ID,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--business-date", type=date.fromisoformat, default=date(2026, 9, 14))
    args = parser.parse_args()
    result = initialize(args.database_url, args.business_date)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

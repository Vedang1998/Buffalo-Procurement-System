#!/usr/bin/env python3
"""Initialize the owned, isolated Buffalo synthetic owner-demo database."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import psycopg
from psycopg import sql

from apply_schema import (
    LEGACY_MIGRATION_SHA256,
    PERSISTENT_MAPPING_RELEASE_MANIFEST,
    POST_MAPPING_APPLICATION_RELEASE_MANIFEST,
    _verify_or_apply_mapping_release,
    _verify_or_apply_post_mapping_release,
    apply_verified_legacy_file,
    verify_or_apply_synthetic_price_replacement,
)
from local_purchasing_candidate import acquire_database_lifecycle_lock


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "db"
SCHEMA = "qa_mapping_test"
VENDOR_ID = "00000000-0000-4000-8000-000000000001"
CONTROL_VENDOR_ID = "00000000-0000-4000-8000-000000000002"
VARIANT_ID = "1001"
BLOCKED_VARIANT_ID = "2002"
CONTROL_VARIANT_ID = "3003"
DEMO_CONTRACT = "BUFFALO_SYNTHETIC_OWNER_DEMO_V1"
STALE_V1_RUN_ID = "00000000-0000-4000-8000-000000000901"


def _require_owned_target(conn: psycopg.Connection, database_url: str) -> None:
    try:
        parsed = urlparse(database_url)
        port = parsed.port
        query = parse_qs(
            parsed.query, strict_parsing=True, keep_blank_values=True
        )
    except ValueError as exc:
        raise RuntimeError("synthetic demo database URL is malformed") from exc
    database = parsed.path.removeprefix("/")
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or parsed.username != "qa_release_login"
        or parsed.password is not None
        or parsed.fragment
        or parsed.params
        or re.fullmatch(r"[a-z][a-z0-9_]*_demo", database) is None
        or query != {"options": ["-c role=qa_mapping_owner"]}
    ):
        raise RuntimeError("synthetic demo database must be loopback")
    row = conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::integer,"
        "pg_catalog.inet_server_addr()::text,session_user::text,current_user::text,"
        "pg_catalog.pg_get_userbyid(d.datdba) "
        "FROM pg_catalog.pg_database d "
        "WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    assert row is not None
    if str(row[0]) != database or int(row[1]) // 10000 != 16:
        raise RuntimeError("synthetic demo requires an owned PostgreSQL 16 *_demo database")
    try:
        server_address = ipaddress.ip_interface(str(row[2])).ip
    except ValueError as exc:
        raise RuntimeError("synthetic demo PostgreSQL address is malformed") from exc
    if not server_address.is_loopback:
        raise RuntimeError("synthetic demo PostgreSQL server is not loopback")
    if (
        (str(row[3]), str(row[4]), str(row[5]))
        != ("qa_release_login", "qa_mapping_owner", "qa_mapping_owner")
    ):
        raise RuntimeError("synthetic demo maintenance role pair differs")
    acquire_database_lifecycle_lock(conn, str(row[0]))
    user_schemas = tuple(
        value[0]
        for value in conn.execute(
            "SELECT nspname FROM pg_catalog.pg_namespace "
            "WHERE nspname <> 'public' AND nspname <> 'information_schema' "
            "AND nspname !~ '^pg_' ORDER BY nspname"
        ).fetchall()
    )
    extensions = tuple(
        value[0]
        for value in conn.execute(
            "SELECT extname FROM pg_catalog.pg_extension ORDER BY extname"
        ).fetchall()
    )
    public_objects = tuple(
        int(value)
        for value in conn.execute(
            "SELECT "
            "(SELECT count(*) FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public'),"
            "(SELECT count(*) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public'),"
            "(SELECT count(*) FROM pg_catalog.pg_type t "
            "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname='public')"
        ).fetchone()
    )
    schema_exists = conn.execute(
        "SELECT pg_catalog.to_regnamespace(%s) IS NOT NULL", (SCHEMA,)
    ).fetchone()[0]
    if public_objects != (0, 0, 0):
        raise RuntimeError("synthetic demo public schema is not empty")
    if schema_exists:
        if user_schemas != (SCHEMA,) or extensions != ("pgcrypto", "plpgsql"):
            raise RuntimeError("synthetic demo installed inventory differs")
    elif user_schemas or extensions != ("plpgsql",):
        raise RuntimeError("synthetic demo target is not empty")


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
        conn.execute(
            "INSERT INTO vendors(vendor_id,vendor_name,active) VALUES (%s,%s,TRUE)",
            (CONTROL_VENDOR_ID, "Synthetic Northern Control"),
        )
        conn.execute(
            """INSERT INTO vendor_operating_rules(
                   vendor_id,order_days,order_cutoff_local,timezone_name,
                   expected_delivery_days,order_cycle_days,lead_time_days,
                   lead_time_variability_days,reliability_pct,minimum_type,
                   minimum_value,below_minimum_fee,loose_order_allowed,
                   loose_unit_fee,confirmation_source,confirmed_by,rules_version)
               VALUES (%s,ARRAY[%s],'23:59:59','America/New_York',ARRAY[%s],
                   2,1,0,1,'DOLLAR',40,4,FALSE,NULL,
                   'FABRICATED OWNER DEMO','synthetic:local-owner:01',1)""",
            (CONTROL_VENDOR_ID, order_day, delivery_day),
        )
        conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,active,
                   catalog_state,identity_scope,sku,barcode,retail_price)
               VALUES (%s,'synthetic-product-3003','Synthetic Northern Control','750ML',
                   TRUE,'LIVE','CURRENT','SYN-3003','0000000003003',6.99)""",
            (CONTROL_VARIANT_ID,),
        )
        alternative_offer_id = conn.execute(
            """INSERT INTO supplier_offers(
                   variant_id,vendor_id,supplier_sku,supplier_description,
                   package_type,size_text,raw_pack,shopify_units_per_case,
                   qualifying_units_per_case,assortment_scope,assortable,
                   active,confidence,source_file,notes)
               VALUES (%s,%s,'SUP-ALT','Synthetic Citrus legacy alternative','STANDARD','750ML',
                   '6x750ML',6,6,'PRODUCT',FALSE,TRUE,'VERIFIED',
                   'fabricated-authoritative-format-source.txt',
                   'TEST DATA — NOT FOR ORDERING') RETURNING offer_id""",
            (VARIANT_ID, VENDOR_ID),
        ).fetchone()[0]
        selected_offer_id = conn.execute(
            """INSERT INTO supplier_offers(
                   variant_id,vendor_id,supplier_sku,supplier_description,
                   package_type,size_text,raw_pack,shopify_units_per_case,
                   qualifying_units_per_case,assortment_scope,assortable,
                   active,confidence,source_file,notes)
               VALUES (%s,%s,'SUP-001','Synthetic Citrus selected offer','STANDARD','750ML',
                   '6x750ML',6,6,'PRODUCT',FALSE,TRUE,'VERIFIED',
                   'fabricated-authoritative-format-source.txt',
                   'TEST DATA — NOT FOR ORDERING') RETURNING offer_id""",
            (VARIANT_ID, VENDOR_ID),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO prices(
                   offer_id,price_state,effective_month,level_type,break_qty,break_unit,
                   case_price,unit_price,source_file,source_page,
                   extraction_confidence,verified,notes)
               VALUES
                   (%s,'current',%s,'BASE',NULL,NULL,10.01,1.6683333333,
                    'synthetic-owner-demo.csv',1,'VERIFIED',TRUE,
                    'Synthetic alternative BASE installed before migration 011'),
                   (%s,'current',%s,'BREAK',2,'CS',10.00,1.6666666667,
                    'synthetic-owner-demo.csv',2,'VERIFIED',TRUE,
                    'Synthetic alternative BREAK installed before migration 011'),
                   (%s,'current',%s,'BASE',NULL,NULL,12.00,2.0000000000,
                    'synthetic-owner-demo.csv',3,'VERIFIED',TRUE,
                    'Synthetic selected BASE installed before migration 011'),
                   (%s,'current',%s,'BREAK',2,'CS',9.50,1.5833333333,
                    'synthetic-owner-demo.csv',4,'VERIFIED',TRUE,
                    'Synthetic selected BREAK installed before migration 011')""",
            (
                alternative_offer_id,business_date.replace(day=1),
                alternative_offer_id,business_date.replace(day=1),
                selected_offer_id,business_date.replace(day=1),
                selected_offer_id,business_date.replace(day=1),
            ),
        )
        control_offer_id = conn.execute(
            """INSERT INTO supplier_offers(
                   variant_id,vendor_id,supplier_sku,supplier_description,
                   package_type,size_text,raw_pack,shopify_units_per_case,
                   qualifying_units_per_case,assortment_scope,assortable,
                   active,confidence,source_file,notes)
               VALUES (%s,%s,'CTRL-3003','Synthetic Northern control offer',
                   'STANDARD','750ML','6x750ML',6,6,'PRODUCT',FALSE,TRUE,'VERIFIED',
                   'fabricated-authoritative-format-source.txt',
                   'TEST DATA — NOT FOR ORDERING') RETURNING offer_id""",
            (CONTROL_VARIANT_ID, CONTROL_VENDOR_ID),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO prices(
                   offer_id,price_state,effective_month,level_type,break_qty,break_unit,
                   case_price,unit_price,source_file,source_page,
                   extraction_confidence,verified,notes)
               VALUES
                   (%s,'current',%s,'BASE',NULL,NULL,24.00,4.0000,
                    'synthetic-control-demo.csv',1,'VERIFIED',TRUE,
                    'Synthetic control BASE installed before migration 011'),
                   (%s,'current',%s,'BREAK',2,'CS',21.00,3.5000,
                    'synthetic-control-demo.csv',2,'VERIFIED',TRUE,
                    'Synthetic control BREAK installed before migration 011')""",
            (
                control_offer_id,
                business_date.replace(day=1),
                control_offer_id,
                business_date.replace(day=1),
            ),
        )


def _synthetic_inventory_capture_specs(
    business_date: date,
) -> tuple[dict[str, object], ...]:
    return (
        {
            "business_date": business_date - timedelta(days=1),
            "captured_at": datetime.combine(
                business_date - timedelta(days=1),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            + timedelta(hours=12),
            "rows": (
                {
                    "variant_id": VARIANT_ID,
                    "location_gid": "synthetic-location-001",
                    "available_quantity": 1,
                    "incoming_quantity": 0,
                },
                {
                    "variant_id": VARIANT_ID,
                    "location_gid": "synthetic-location-002",
                    "available_quantity": 2,
                    "incoming_quantity": 0,
                },
                {
                    "variant_id": CONTROL_VARIANT_ID,
                    "location_gid": "synthetic-location-001",
                    "available_quantity": 4,
                    "incoming_quantity": 0,
                },
            ),
        },
        {
            "business_date": business_date,
            "captured_at": datetime.combine(
                business_date, datetime.min.time(), tzinfo=timezone.utc
            )
            + timedelta(hours=12),
            "rows": (
                {
                    "variant_id": VARIANT_ID,
                    "location_gid": "synthetic-location-001",
                    "available_quantity": 0,
                    "incoming_quantity": 0,
                },
                {
                    "variant_id": CONTROL_VARIANT_ID,
                    "location_gid": "synthetic-location-001",
                    "available_quantity": 4,
                    "incoming_quantity": 0,
                },
            ),
        },
    )


def _seed_evidence(
    conn: psycopg.Connection,
    business_date: date,
    *,
    canonical_sales_end_date: date | None = None,
) -> str:
    from procurement_os.catalog import recompute_catalog_gate
    from procurement_os.historical_sales import (
        AUTHORITATIVE_START_DATE,
        ControlTotals,
        _chunk_rows,
        _complete_chunk_control,
        _mark_page_running,
        _persist_page,
        create_sales_backfill_run,
        finalize_sales_backfill,
        query_contract_hash,
    )
    from procurement_os.inventory import capture_daily_inventory
    from procurement_os.po_ledger import recompute_open_po_reconciliation_gate
    from procurement_os.sales import load_identity_index
    from procurement_os.vendor_rules import recompute_vendor_rules_gates

    with conn.transaction():
        conn.execute(
            """INSERT INTO catalog_sync_runs(
                   completed_at,status,shopify_api_version,
                   shopify_reported_variant_count,live_rows_received,
                   exact_current_ids,new_live_variants,source_hash,
                   pagination_complete,notes)
               VALUES (pg_catalog.now(),'COMPLETED','FABRICATED_OFFLINE',3,3,3,0,%s,
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
        conn.execute(
            """INSERT INTO variant_policies(
                   variant_id,policy_type,value_json,active,effective_from,
                   approved_by,note)
               VALUES (%s,'REPLENISHMENT_MODE','{"mode":"ROUTINE"}',TRUE,%s,
                   'synthetic:local-owner:01','FABRICATED OWNER DEMO POLICY')""",
            (CONTROL_VARIANT_ID, business_date),
        )
    for capture_spec in _synthetic_inventory_capture_specs(business_date):
        capture = capture_daily_inventory(
            conn,
            business_date=capture_spec["business_date"],
            captured_at=capture_spec["captured_at"],
            source="SYNTHETIC_DEMO",
            rows=capture_spec["rows"],
        )
        if capture["readiness"]["status"] != "PASS":
            raise RuntimeError(
                "synthetic inventory did not pass its exact coverage gate"
            )
    if recompute_catalog_gate(conn)["status"] != "PASS":
        raise RuntimeError("synthetic catalog gate did not pass")
    if recompute_vendor_rules_gates(conn)["status"] != "PASS":
        raise RuntimeError("synthetic vendor-rules gate did not pass")
    evaluation_at = datetime.combine(
        business_date, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=12)
    if recompute_open_po_reconciliation_gate(conn, as_of=evaluation_at)["status"] != "PASS":
        raise RuntimeError("synthetic open-PO reconciliation gate did not pass")
    # Exercise the production-intended durable raw-first sales pipeline.  The
    # fabricated rows are inputs to its independent page/chunk/final controls;
    # the initializer never writes a readiness PASS itself.
    sales_end_date = canonical_sales_end_date or business_date
    sales_rows = _synthetic_sales_rows(business_date)
    totals = ControlTotals(
        net_items_sold=sum((row.net_items_sold for row in sales_rows), Decimal("0")),
        net_sales=sum((row.net_sales or Decimal("0") for row in sales_rows), Decimal("0")),
    )
    run_id = create_sales_backfill_run(
        conn,
        start_date=AUTHORITATIVE_START_DATE,
        end_date=sales_end_date,
        store_timezone="America/New_York",
        chunk_days=10000,
        page_size=1000,
    )
    synthetic_started_at = datetime.combine(
        sales_end_date, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=12)
    with conn.transaction():
        updated = conn.execute(
            """UPDATE sales_backfill_runs
                  SET started_at=%s,
                      notes=notes || '; FABRICATED SYNTHETIC FIXTURE CLOCK'
                WHERE sales_backfill_id=%s AND status='RUNNING'
                RETURNING started_at""",
            (synthetic_started_at, run_id),
        ).fetchone()
        if updated != (synthetic_started_at,):
            raise RuntimeError("synthetic sales fixture clock was not installed")
    chunk = _chunk_rows(conn, run_id)
    if len(chunk) != 1:
        raise RuntimeError("synthetic sales service did not create exactly one chunk")
    page_id, prior_status = _mark_page_running(
        conn,
        chunk_id=str(chunk[0][0]),
        page_index=0,
        page_size=1000,
        chunk_start=chunk[0][2],
        chunk_end=chunk[0][3],
        contract_hash=query_contract_hash(),
    )
    if prior_status != "RUNNING":
        raise RuntimeError("synthetic sales service page did not enter RUNNING")
    _persist_page(
        conn,
        run_id=run_id,
        chunk_id=str(chunk[0][0]),
        page_id=page_id,
        rows=sales_rows,
        identity=load_identity_index(conn),
        terminal=True,
    )
    _complete_chunk_control(
        conn,
        run_id=run_id,
        chunk_id=str(chunk[0][0]),
        totals=totals,
    )
    sales_result = finalize_sales_backfill(
        conn,
        run_id=run_id,
        independent_totals=totals,
    )
    if sales_result["status"] != "PASS":
        raise RuntimeError("synthetic sales service did not pass its derived controls")
    return str(run_id)


def _synthetic_sales_rows(business_date: date) -> list[object]:
    """Return the exact fabricated corpus consumed by the real backfill service."""

    from procurement_os.sales import SalesSourceRow

    rows: list[SalesSourceRow] = []
    for offset in range(84):
        sale_date = business_date - timedelta(days=84 - offset)
        target_units = Decimal("1") if offset < 70 else Decimal("2")
        rows.extend(
            (
                SalesSourceRow(
                    sale_date=sale_date,
                    source_variant_id=VARIANT_ID,
                    source_sku="SYN-1001",
                    source_product_title="Synthetic Citrus",
                    source_variant_title="750ML",
                    net_items_sold=target_units,
                    net_sales=target_units * Decimal("4.99"),
                ),
                SalesSourceRow(
                    sale_date=sale_date,
                    source_variant_id=CONTROL_VARIANT_ID,
                    source_sku="SYN-3003",
                    source_product_title="Synthetic Northern Control",
                    source_variant_title="750ML",
                    net_items_sold=Decimal("1"),
                    net_sales=Decimal("6.99"),
                ),
            )
        )
    return rows


def _verify_synthetic_sales_corpus(
    conn: psycopg.Connection,
    *,
    business_date: date,
    sales_backfill_id: UUID,
) -> None:
    """Bind replay acceptance to the exact raw and canonical fabricated facts."""

    from procurement_os.historical_sales import source_identity_key
    from procurement_os.sales import source_row_hash

    expected_started_at = datetime.combine(
        business_date, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=12)
    started_at = conn.execute(
        "SELECT started_at FROM sales_backfill_runs WHERE sales_backfill_id=%s",
        (sales_backfill_id,),
    ).fetchone()
    if started_at != (expected_started_at,):
        raise RuntimeError("synthetic demo sales fixture clock differs")
    expected_rows = _synthetic_sales_rows(business_date)
    actual_raw = conn.execute(
        """SELECT r.sale_date,r.source_variant_id,r.source_sku,
                  r.source_product_title,r.source_variant_title,r.net_items_sold,
                  r.net_sales,r.canonical_variant_id,r.resolution_status,
                  r.resolution_method,r.resolution_evidence,r.source_identity_key,
                  r.source_row_hash,rf.source_row_hash,
                  rf.first_observed_net_items_sold,rf.first_observed_net_sales,
                  rf.observed_net_items_sold,rf.observed_net_sales,
                  rf.observation_count,rf.restatement_detected,r.fetch_count,
                  r.sales_backfill_id::text,
                  rf.first_observed_chunk_id IS NOT NULL
                    AND rf.first_observed_chunk_id=rf.last_observed_chunk_id,
                  rf.first_observed_page_id IS NOT NULL
                    AND rf.first_observed_page_id=rf.last_observed_page_id
             FROM sales_backfill_run_facts rf
             JOIN shopify_sales_daily_raw r USING(raw_sales_id)
            WHERE rf.sales_backfill_id=%s
            ORDER BY r.sale_date,r.raw_sales_id""",
        (sales_backfill_id,),
    ).fetchall()
    expected_raw = [
        (
            row.sale_date,
            row.source_variant_id,
            row.source_sku,
            row.source_product_title,
            row.source_variant_title,
            row.net_items_sold,
            row.net_sales,
            row.source_variant_id,
            "RESOLVED",
            "EXACT_ACTIVE_VARIANT_ID",
            {
                "candidates": [row.source_variant_id],
                "catalog_state": "LIVE",
                "source_variant_id": row.source_variant_id,
            },
            source_identity_key(row),
            source_row_hash(row),
            source_row_hash(row),
            row.net_items_sold,
            row.net_sales,
            row.net_items_sold,
            row.net_sales,
            1,
            False,
            1,
            str(sales_backfill_id),
            True,
            True,
        )
        for row in expected_rows
    ]
    if actual_raw != expected_raw:
        raise RuntimeError("synthetic demo raw sales corpus differs")

    actual_daily = conn.execute(
        """SELECT sale_date,variant_id,units_sold,net_sales,distinct_orders,source
             FROM sales_daily
            WHERE source='SHOPIFYQL_SALES'
            ORDER BY sale_date,variant_id"""
    ).fetchall()
    expected_daily = [
        (
            row.sale_date,
            row.source_variant_id,
            row.net_items_sold,
            row.net_sales,
            None,
            "SHOPIFYQL_SALES",
        )
        for row in expected_rows
    ]
    if actual_daily != expected_daily:
        raise RuntimeError("synthetic demo canonical sales corpus differs")


def _verify_synthetic_inventory_corpus(
    conn: psycopg.Connection, *, business_date: date
) -> None:
    from procurement_os.inventory import (
        inventory_source_hash,
        normalize_inventory_levels,
    )

    expected_rows: list[tuple[object, ...]] = []
    for spec in _synthetic_inventory_capture_specs(business_date):
        rows = normalize_inventory_levels(spec["rows"])
        source_hash = inventory_source_hash(
            rows, business_date=spec["business_date"]
        )
        evidence = {
            "validation_counts": {
                "ARCHIVAL_ONLY": 0,
                "INCOMPLETE": 0,
                "INVALID": 0,
                "VALID": len(rows),
            }
        }
        for row in rows:
            expected_rows.append(
                (
                    spec["business_date"],
                    spec["captured_at"],
                    spec["captured_at"],
                    "COMPLETED",
                    "SYNTHETIC_DEMO",
                    source_hash,
                    len(rows),
                    len(rows),
                    0,
                    0,
                    0,
                    evidence,
                    row.variant_id,
                    row.location_gid,
                    row.available_quantity,
                    row.incoming_quantity,
                    row.on_hand_quantity,
                    row.committed_quantity,
                    row.reserved_quantity,
                    row.damaged_quantity,
                    "VALID",
                    None,
                    True,
                    True,
                )
            )
    actual_rows = conn.execute(
        """SELECT r.business_date,r.started_at,r.completed_at,r.status,r.source,
                  r.source_hash,r.rows_received,r.eligible_rows,r.archival_rows,
                  r.invalid_rows,r.incomplete_rows,r.evidence_json,
                  rr.variant_id,rr.location_gid,rr.available_quantity,
                  rr.incoming_quantity,rr.on_hand_quantity,rr.committed_quantity,
                  rr.reserved_quantity,rr.damaged_quantity,rr.validation_status,
                  rr.validation_message,
                  d.inventory_snapshot_run_id=r.inventory_snapshot_run_id
                    AND d.snapshot_date=r.business_date
                    AND d.captured_at=r.completed_at
                    AND d.source=r.source
                    AND d.available_quantity IS NOT DISTINCT FROM rr.available_quantity
                    AND d.incoming_quantity IS NOT DISTINCT FROM rr.incoming_quantity
                    AND d.on_hand_quantity IS NOT DISTINCT FROM rr.on_hand_quantity
                    AND d.committed_quantity IS NOT DISTINCT FROM rr.committed_quantity
                    AND d.reserved_quantity IS NOT DISTINCT FROM rr.reserved_quantity
                    AND d.damaged_quantity IS NOT DISTINCT FROM rr.damaged_quantity
                    AND d.validation_status=rr.validation_status
                    AND d.validation_message IS NOT DISTINCT FROM rr.validation_message,
                  r.started_at=r.completed_at
             FROM inventory_snapshot_runs r
             JOIN inventory_snapshot_run_rows rr USING(inventory_snapshot_run_id)
             LEFT JOIN daily_inventory_snapshots d
               ON d.snapshot_date=r.business_date
              AND d.variant_id=rr.variant_id AND d.location_gid=rr.location_gid
            WHERE r.source='SYNTHETIC_DEMO'
            ORDER BY r.business_date,rr.variant_id,rr.location_gid"""
    ).fetchall()
    if actual_rows != expected_rows:
        raise RuntimeError("synthetic demo inventory corpus differs")


def _stale_v1_fixture_values(business_date: date) -> dict[str, object]:
    from procurement_os.monday_controls import load_material_edit_policy

    evaluation_at = datetime.combine(
        business_date, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=12)
    source_data_through = datetime.combine(
        business_date - timedelta(days=1),
        datetime.max.time(),
        tzinfo=timezone.utc,
    )
    manifest = json.dumps(
        {
            "business_date": business_date,
            "contexts": [],
            "evaluation_at": evaluation_at,
            "material_edit_policy": load_material_edit_policy().evidence(),
            "method_version": "EMERGENCY_TRANSPARENT_V1",
            "safety_label": "TEST DATA — NOT FOR ORDERING",
            "variant_ids": [VARIANT_ID],
        },
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "evaluation_at": evaluation_at,
        "source_data_through": source_data_through,
        "idempotency_key": f"synthetic-stale-v1:{business_date.isoformat()}",
        "manifest": manifest,
        "fingerprint": hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
        "notes": (
            "TEST DATA — NOT FOR ORDERING; fabricated stale V1 lifecycle fixture"
        ),
    }


def _seed_stale_v1_fixture(
    conn: psycopg.Connection, business_date: date
) -> None:
    """Create the sole fabricated V1 fixture before migration 015 retires writes."""

    expected = _stale_v1_fixture_values(business_date)
    with conn.transaction():
        inserted = conn.execute(
            """INSERT INTO runs(
                       run_id,run_type,status,source_data_through,business_date,
                       started_at,idempotency_key,input_fingerprint,workflow_stage,
                       model_version,notes,procurement_output_mode,
                       procurement_input_manifest)
                VALUES (%s,'MONDAY_PROCUREMENT','RUNNING',%s,%s,%s,%s,%s,
                        'PREPARING','EMERGENCY_TRANSPARENT_V1',%s,
                        'INTERNAL_DRAFT_ONLY',%s)
                RETURNING run_id""",
            (
                STALE_V1_RUN_ID,
                expected["source_data_through"],
                business_date,
                expected["evaluation_at"],
                expected["idempotency_key"],
                expected["fingerprint"],
                expected["notes"],
                expected["manifest"],
            ),
        ).fetchall()
        if len(inserted) != 1 or str(inserted[0][0]) != STALE_V1_RUN_ID:
            raise RuntimeError("synthetic stale V1 fixture was not created exactly once")


def _publish_demo_marker(
    conn: psycopg.Connection, business_date: date, sales_backfill_id: str
) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO meta(key,value) VALUES (%s,%s),(%s,%s)",
            (
                "synthetic_owner_demo_business_date",
                business_date.isoformat(),
                "synthetic_owner_demo_sales_backfill_id",
                sales_backfill_id,
            ),
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES (%s,%s)",
            ("synthetic_owner_demo_contract", DEMO_CONTRACT),
        )


def _verify_initialized_demo(
    conn: psycopg.Connection, business_date: date
) -> None:
    from procurement_os.historical_sales import AUTHORITATIVE_START_DATE

    conn.execute(
        sql.SQL("SET search_path TO {},pg_catalog").format(sql.Identifier(SCHEMA))
    )
    with conn.transaction():
        if _verify_or_apply_mapping_release(conn, DB_DIR):
            raise RuntimeError("initialized demo unexpectedly applied mapping state")
    with conn.transaction():
        if _verify_or_apply_post_mapping_release(conn, DB_DIR):
            raise RuntimeError("initialized demo unexpectedly applied retirement state")
    with conn.transaction():
        if verify_or_apply_synthetic_price_replacement(conn, DB_DIR):
            raise RuntimeError(
                "initialized demo unexpectedly applied synthetic price state"
            )
    metadata = dict(
        conn.execute(
            """SELECT key,value FROM meta WHERE key=ANY(%s)""",
            (
                [
                    "synthetic_owner_demo_contract",
                    "synthetic_owner_demo_business_date",
                    "synthetic_owner_demo_sales_backfill_id",
                ],
            ),
        ).fetchall()
    )
    if (
        metadata.get("synthetic_owner_demo_contract") != DEMO_CONTRACT
        or metadata.get("synthetic_owner_demo_business_date")
        != business_date.isoformat()
        or not metadata.get("synthetic_owner_demo_sales_backfill_id")
    ):
        raise RuntimeError("synthetic demo metadata differs")
    offer_prices = conn.execute(
        """SELECT o.supplier_sku,o.active,o.package_type,o.confidence,
                  o.shopify_units_per_case,o.qualifying_units_per_case,
                  p.price_state,p.effective_month,p.level_type,p.break_qty,p.break_unit,
                  p.case_price,p.unit_price,p.source_file,p.source_page
             FROM supplier_offers o JOIN prices p USING(offer_id)
            WHERE o.variant_id=%s
            ORDER BY o.supplier_sku,p.source_page""",
        (VARIANT_ID,),
    ).fetchall()
    expected_month = business_date.replace(day=1)
    expected_offer_prices = [
        ("SUP-001",True,"STANDARD","VERIFIED",Decimal("6"),Decimal("6"),
         "current",expected_month,"BASE",None,None,Decimal("12.0000"),
         Decimal("2.0000000000"),"synthetic-owner-demo.csv",3),
        ("SUP-001",True,"STANDARD","VERIFIED",Decimal("6"),Decimal("6"),
         "current",expected_month,"BREAK",Decimal("2"),"CS",Decimal("9.5000"),
         Decimal("1.5833"),"synthetic-owner-demo.csv",4),
        ("SUP-ALT",True,"STANDARD","VERIFIED",Decimal("6"),Decimal("6"),
         "current",expected_month,"BASE",None,None,Decimal("10.0100"),
         Decimal("1.6683"),"synthetic-owner-demo.csv",1),
        ("SUP-ALT",True,"STANDARD","VERIFIED",Decimal("6"),Decimal("6"),
         "current",expected_month,"BREAK",Decimal("2"),"CS",Decimal("10.0000"),
         Decimal("1.6667"),"synthetic-owner-demo.csv",2),
    ]
    if offer_prices != expected_offer_prices:
        raise RuntimeError("synthetic selected-offer price fixture differs")
    control_offer_prices = conn.execute(
        """SELECT o.supplier_sku,o.active,o.package_type,o.confidence,
                  o.shopify_units_per_case,o.qualifying_units_per_case,
                  p.price_state,p.effective_month,p.level_type,p.break_qty,p.break_unit,
                  p.case_price,p.unit_price,p.source_file,p.source_page
             FROM supplier_offers o JOIN prices p USING(offer_id)
            WHERE o.variant_id=%s
            ORDER BY p.source_page""",
        (CONTROL_VARIANT_ID,),
    ).fetchall()
    expected_control_prices = [
        ("CTRL-3003",True,"STANDARD","VERIFIED",Decimal("6"),Decimal("6"),
         "current",expected_month,"BASE",None,None,Decimal("24.0000"),
         Decimal("4.0000"),"synthetic-control-demo.csv",1),
        ("CTRL-3003",True,"STANDARD","VERIFIED",Decimal("6"),Decimal("6"),
         "current",expected_month,"BREAK",Decimal("2"),"CS",Decimal("21.0000"),
         Decimal("3.5000"),"synthetic-control-demo.csv",2),
    ]
    if control_offer_prices != expected_control_prices:
        raise RuntimeError("synthetic control-vendor price fixture differs")
    authority = conn.execute(
        """SELECT p.vendor_id::text,p.price_scope_key,p.fixture_database_name,
                  h.head_version,h.active_price_book_batch_id,
                  e.action,e.resulting_current_scope_sha256=h.current_scope_sha256
             FROM supplier_price_schedule_policies p
             JOIN supplier_price_authority_heads h
               ON h.vendor_id=p.vendor_id AND h.price_scope_key=p.price_scope_key
             JOIN supplier_price_authority_events e
               ON e.supplier_price_authority_event_id=h.supplier_price_authority_event_id
            ORDER BY p.vendor_id"""
    ).fetchall()
    if authority != [
        (VENDOR_ID,"COMPLETE_VENDOR",conn.info.dbname,1,None,"ADOPT_EXISTING_BASELINE",True),
        (CONTROL_VENDOR_ID,"COMPLETE_VENDOR",conn.info.dbname,1,None,
         "ADOPT_EXISTING_BASELINE",True),
    ]:
        raise RuntimeError("synthetic baseline price authority differs")
    sales_backfill_id = str(metadata["synthetic_owner_demo_sales_backfill_id"])
    try:
        sales_backfill_uuid = UUID(sales_backfill_id)
    except ValueError as exc:
        raise RuntimeError("synthetic demo sales-backfill binding is malformed") from exc
    sales = conn.execute(
        """SELECT g.status,g.severity,g.blocks_po,g.evidence_json,
                  b.sales_backfill_id::text,b.status,b.completed_at,b.start_date,
                  b.end_date,b.source,b.query_version,b.store_timezone,
                  b.expected_chunks,b.completed_chunks,b.expected_pages,
                  b.completed_pages,b.source_rows,b.unique_source_facts,
                  b.resolved_rows,b.unresolved_rows,b.ambiguous_rows,b.excluded_rows,
                  b.coverage_complete,b.pages_complete,b.source_facts_persisted,
                  b.idempotency_verified,b.control_totals_reconciled,
                  b.canonical_aggregate_rebuilt,b.control_evidence
             FROM readiness_gates g
             JOIN sales_backfill_runs b ON b.sales_backfill_id=%s
            WHERE g.gate_name='SALES_BACKFILL'
              AND g.scope_type='GLOBAL' AND g.scope_id=''""",
        (sales_backfill_uuid,),
    ).fetchone()
    if sales is None:
        raise RuntimeError("synthetic demo sales-backfill binding is absent")
    gate_evidence = sales[3] if isinstance(sales[3], dict) else None
    run_evidence = sales[28] if isinstance(sales[28], dict) else None
    expected_gate_evidence = dict(gate_evidence or {})
    blockers = expected_gate_evidence.pop("blockers", None)
    integer_evidence = {
        "expected_chunks": 12,
        "completed_chunks": 13,
        "expected_pages": 14,
        "completed_pages": 15,
        "source_rows": 16,
        "unique_source_facts": 17,
        "resolved_rows": 18,
        "unresolved_rows": 19,
        "ambiguous_rows": 20,
        "excluded_rows": 21,
    }
    boolean_evidence = {
        "coverage_complete": 22,
        "pages_complete": 23,
        "source_facts_persisted": 24,
        "idempotency_verified": 25,
        "control_totals_reconciled": 26,
        "canonical_aggregate_rebuilt": 27,
    }
    if not (
        sales[:3] == ("PASS", "CRITICAL", True)
        and gate_evidence is not None
        and gate_evidence.get("sales_backfill_id") == sales_backfill_id
        and blockers == []
        and sales[4] == sales_backfill_id
        and sales[5] == "COMPLETED"
        and sales[6] is not None
        and sales[7] == AUTHORITATIVE_START_DATE
        and sales[8] == business_date
        and gate_evidence.get("start_date") == sales[7].isoformat()
        and gate_evidence.get("end_date") == business_date.isoformat()
        and gate_evidence.get("store_timezone") == sales[11]
        and sales[9:12]
        == ("SHOPIFYQL_SALES", "SHOPIFYQL_SALES_V2", "America/New_York")
        and sales[12] > 0
        and sales[13] == sales[12]
        and sales[14] > 0
        and sales[15] == sales[14]
        and sales[16] == sales[17]
        and sales[17] > 0
        and sales[18] == sales[17]
        and sales[19:22] == (0, 0, 0)
        and all(value is True for value in sales[22:28])
        and all(
            type(gate_evidence.get(key)) is int
            and gate_evidence.get(key) == sales[index]
            for key, index in integer_evidence.items()
        )
        and all(
            gate_evidence.get(key) is True and sales[index] is True
            for key, index in boolean_evidence.items()
        )
        and run_evidence is not None
        and expected_gate_evidence == run_evidence
    ):
        raise RuntimeError("synthetic demo sales-backfill contract differs")
    _verify_synthetic_sales_corpus(
        conn,
        business_date=business_date,
        sales_backfill_id=sales_backfill_uuid,
    )
    _verify_synthetic_inventory_corpus(conn, business_date=business_date)
    expected = _stale_v1_fixture_values(business_date)
    fixture = conn.execute(
        """SELECT run_type,status,workflow_stage,model_version,
                  procurement_output_mode,business_date,source_data_through,
                  started_at,idempotency_key,input_fingerprint,notes,
                  procurement_input_manifest,
                  encode(digest(convert_to(procurement_input_manifest,'UTF8'),'sha256'),'hex'),
                  (SELECT count(*) FROM purchase_orders p WHERE p.run_id=r.run_id),
                  (SELECT count(*) FROM monday_run_artifacts a WHERE a.run_id=r.run_id),
                  (SELECT count(*) FROM monday_stale_forecast_retirements e
                    WHERE e.run_id=r.run_id),
                  (SELECT count(*) FROM change_log c WHERE c.run_id=r.run_id
                    AND c.evidence_json->>'contract'=
                        'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1'),
                  (SELECT count(*) FROM runs v
                    WHERE v.model_version='EMERGENCY_TRANSPARENT_V1'),
                  ARRAY[
                    (SELECT count(*) FROM forecast_results f WHERE f.run_id=r.run_id),
                    (SELECT count(*) FROM procurement_recommendations p
                      WHERE p.run_id=r.run_id),
                    (SELECT count(*) FROM inventory_snapshots i WHERE i.run_id=r.run_id),
                    (SELECT count(*) FROM run_price_snapshots p WHERE p.run_id=r.run_id),
                    (SELECT count(*) FROM exceptions e WHERE e.run_id=r.run_id),
                    (SELECT count(*) FROM review_decisions d WHERE d.run_id=r.run_id),
                    (SELECT count(*) FROM monday_run_blocker_exclusions x
                      WHERE x.run_id=r.run_id),
                    (SELECT count(*) FROM monday_material_edit_confirmations m
                      WHERE m.run_id=r.run_id),
                    (SELECT count(*) FROM purchase_orders p WHERE p.run_id=r.run_id),
                    (SELECT count(*) FROM purchase_order_lines l
                      JOIN purchase_orders p ON p.po_id=l.po_id
                      WHERE p.run_id=r.run_id),
                    (SELECT count(*) FROM monday_run_artifacts a WHERE a.run_id=r.run_id),
                    (SELECT count(*) FROM monday_packet_build_events b
                      WHERE b.run_id=r.run_id)
                  ],
                  r.completed_at,r.current_price_month,r.future_price_month,
                  r.exception_count
             FROM runs r WHERE run_id=%s""",
        (STALE_V1_RUN_ID,),
    ).fetchone()
    if fixture is None or not (
        fixture[0] == "MONDAY_PROCUREMENT"
        and fixture[3] == "EMERGENCY_TRANSPARENT_V1"
        and fixture[4] == "INTERNAL_DRAFT_ONLY"
        and fixture[5] == business_date
        and fixture[6] == expected["source_data_through"]
        and fixture[7] == expected["evaluation_at"]
        and fixture[8] == expected["idempotency_key"]
        and fixture[9] == expected["fingerprint"]
        and fixture[10] == expected["notes"]
        and fixture[11] == expected["manifest"]
        and fixture[12] == expected["fingerprint"]
        and fixture[13:15] == (0, 0)
        and fixture[17] == 1
        and all(value == 0 for value in fixture[18])
        and fixture[19:23] == (None, None, None, 0)
    ):
        raise RuntimeError("synthetic stale V1 fixture differs")
    if not (
        fixture[1:3] == ("RUNNING", "PREPARING")
        and fixture[15:17] == (0, 0)
        or fixture[1:3] == ("FAILED", "FAILED")
        and fixture[15:17] == (1, 1)
    ):
        raise RuntimeError("synthetic stale V1 lifecycle state differs")


def initialize(database_url: str, business_date: date) -> dict[str, object]:
    if (
        len(PERSISTENT_MAPPING_RELEASE_MANIFEST) != 1
        or len(POST_MAPPING_APPLICATION_RELEASE_MANIFEST) != 1
    ):
        raise RuntimeError(
            "synthetic initializer requires the reviewed singleton release plan"
        )
    with psycopg.connect(database_url, autocommit=True) as conn:
        _require_owned_target(conn, database_url)
        existing = conn.execute(
            "SELECT pg_catalog.to_regnamespace(%s)", (SCHEMA,)
        ).fetchone()[0]
        if existing is not None:
            _verify_initialized_demo(conn, business_date)
            return {"initialized": False, "contract": DEMO_CONTRACT}
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
        legacy_names = tuple(LEGACY_MIGRATION_SHA256)
        for name in legacy_names[:11]:
            _apply_legacy(conn, name, schema_oid=schema_oid)
        _seed_pre_price(conn, business_date)
        for name in legacy_names[11:]:
            _apply_legacy(conn, name, schema_oid=schema_oid)
        with conn.transaction():
            if not _verify_or_apply_mapping_release(conn, DB_DIR):
                raise RuntimeError("fresh synthetic demo did not apply mapping release")
        sales_backfill_id = _seed_evidence(conn, business_date)
        _seed_stale_v1_fixture(conn, business_date)
        with conn.transaction():
            if not _verify_or_apply_post_mapping_release(conn, DB_DIR):
                raise RuntimeError("fresh synthetic demo did not apply retirement release")
        with conn.transaction():
            if not verify_or_apply_synthetic_price_replacement(
                conn,
                DB_DIR,
                enable_fixture_registration=True,
            ):
                raise RuntimeError(
                    "fresh synthetic demo did not apply synthetic price release"
                )
        _publish_demo_marker(conn, business_date, sales_backfill_id)
        _verify_initialized_demo(conn, business_date)
        return {
            "initialized": True,
            "contract": DEMO_CONTRACT,
            "business_date": business_date.isoformat(),
            "variant_id": VARIANT_ID,
            "vendor_id": VENDOR_ID,
            "control_variant_id": CONTROL_VARIANT_ID,
            "control_vendor_id": CONTROL_VENDOR_ID,
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

#!/usr/bin/env python3
"""Import the verified August v0.1 seed CSV bundle into v1.3 PostgreSQL.

The CSV export is deliberately included in the build package so production migration does
not depend on a local SQLite file surviving. This importer is idempotent for the canonical
master rows and fail-closed for pricing.

Intentional transformations:
- historical 2,029 variants are seed identities, catalog_state='SEEDED' until Shopify sync;
- 3,301 alias rows are approved historical evidence inherited from the audited prototype;
- old blanket assortable_working is NOT promoted to explicit book evidence;
- default assortment_scope='PRODUCT'; explicit cross-product rules must be re-established
  from book evidence / human approval;
- qualifying_units_per_case is initialized from the verified sellable case count for the
  existing BT seed offers; future parser imports must supply it explicitly;
- only CURRENT August price rows are imported. No ARCHIVE is created.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def null(v: str | None) -> str | None:
    if v is None: return None
    s=str(v).strip()
    return s if s else None


def num(v: str | None) -> float | None:
    s=null(v)
    return None if s is None else float(s)


def integer(v: str | None) -> int | None:
    s=null(v)
    return None if s is None else int(float(s))


def boolean(v: str | None, default: bool=False) -> bool:
    s=(v or '').strip().lower()
    if not s: return default
    return s in {'1','true','yes','y','t'}


def import_seed(seed_dir: Path, database_url: str) -> dict[str,int]:
    import psycopg

    variants=read_csv(seed_dir/'variants.csv')
    aliases=read_csv(seed_dir/'variant_aliases.csv')
    vendors=read_csv(seed_dir/'vendors.csv')
    offers=read_csv(seed_dir/'supplier_offers.csv')
    prices=read_csv(seed_dir/'current_prices.csv')
    exceptions=read_csv(seed_dir/'open_exceptions.csv')

    counts={}
    with psycopg.connect(database_url) as conn:
      with conn.transaction():
       with conn.cursor() as cur:
        vendor_ids={}
        for v in vendors:
            cur.execute("""
                INSERT INTO vendors(vendor_name,active,order_day,order_cycle_days,lead_time_days,
                    delivery_case_threshold,delivery_dollar_threshold,threshold_logic,
                    fee_below_threshold,fee_qualified,loose_unit_fee,notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(vendor_name) DO UPDATE SET
                    active=EXCLUDED.active,order_day=EXCLUDED.order_day,
                    order_cycle_days=EXCLUDED.order_cycle_days,lead_time_days=EXCLUDED.lead_time_days,
                    delivery_case_threshold=EXCLUDED.delivery_case_threshold,
                    delivery_dollar_threshold=EXCLUDED.delivery_dollar_threshold,
                    threshold_logic=EXCLUDED.threshold_logic,fee_below_threshold=EXCLUDED.fee_below_threshold,
                    fee_qualified=EXCLUDED.fee_qualified,loose_unit_fee=EXCLUDED.loose_unit_fee,notes=EXCLUDED.notes
                RETURNING vendor_id
            """,(v['vendor_name'],boolean(v['active'],True),v['order_day'] or 'Monday',integer(v['order_cycle_days']),
                  integer(v['lead_time_days']) or 1,num(v['delivery_case_threshold']),num(v['delivery_dollar_threshold']),
                  null(v['threshold_logic']),num(v['fee_below_threshold']),num(v['fee_qualified']),num(v['loose_unit_fee']),null(v['notes'])))
            vendor_ids[v['vendor_name']]=cur.fetchone()[0]
        counts['vendors']=len(vendors)

        for v in variants:
            cur.execute("""
                INSERT INTO variants(variant_id,shopify_gid,product_id,product_gid,product_title,variant_title,handle,status,
                    sku,barcode,retail_price,shopify_current_cost,shopify_vendor,product_type,active,variant_created_at,
                    source_snapshot,last_synced_at,inventory_tracked,catalog_state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SEEDED')
                ON CONFLICT(variant_id) DO UPDATE SET
                    product_id=EXCLUDED.product_id,product_title=EXCLUDED.product_title,variant_title=EXCLUDED.variant_title,
                    sku=EXCLUDED.sku,barcode=EXCLUDED.barcode,retail_price=EXCLUDED.retail_price,
                    shopify_current_cost=EXCLUDED.shopify_current_cost,shopify_vendor=EXCLUDED.shopify_vendor,
                    product_type=EXCLUDED.product_type,source_snapshot=EXCLUDED.source_snapshot,catalog_state='SEEDED'
            """,(v['variant_id'],null(v['shopify_gid']),v['product_id'],null(v['product_gid']),v['product_title'],v['variant_title'],
                  null(v['handle']),null(v['status']),null(v['sku']),null(v['barcode']),num(v['retail_price']),num(v['current_cost']),
                  null(v['shopify_vendor']),null(v['product_type']),boolean(v['active'],True),null(v['variant_created_at']),v['source_snapshot'],
                  null(v['last_synced_at']),boolean(v.get('inventory_tracked'),False)))
        counts['variants']=len(variants)

        # Alias bundle is static seed data. Delete only rows explicitly originating from the same migration source
        # to make reruns idempotent without touching later human-approved aliases.
        cur.execute("DELETE FROM variant_aliases WHERE source LIKE 'v0.1 seed:%'")
        for a in aliases:
            cur.execute("""
                INSERT INTO variant_aliases(variant_id,old_variant_id,historical_product_title,historical_variant_title,
                    historical_sku,normalized_key,match_method,confidence,source,notes,approved,approved_at,evidence_json)
                VALUES (%s,%s,%s,%s,%s,NULL,%s,NULL,%s,%s,TRUE,now(),%s::jsonb)
            """,(a['variant_id'],null(a['old_variant_id']),null(a['historical_product_title']),null(a['historical_variant_title']),
                  null(a['historical_sku']),null(a['match_method']) or 'MIGRATED',f"v0.1 seed:{a['source']}",null(a['notes']),
                  json.dumps({'legacy_alias_id':a['alias_id'],'is_current_id':boolean(a.get('is_current_id'))})))
        counts['variant_aliases']=len(aliases)

        # Determine which old offers actually use a BT ladder so qualifying units can be initialized from the
        # already-mined sellable case count. This is explicit seed conversion, not a future parser default.
        bt_offer_ids={p['offer_id'] for p in prices if (p.get('break_unit') or '').upper()=='BT'}
        offer_map={}
        # Delete/reimport only offers tagged by this migration source.
        cur.execute("SELECT offer_id FROM supplier_offers WHERE notes LIKE 'v0.1 seed migration:%'")
        old_seed_offer_ids=[r[0] for r in cur.fetchall()]
        if old_seed_offer_ids:
            cur.execute("DELETE FROM prices WHERE offer_id = ANY(%s)",(old_seed_offer_ids,))
            cur.execute("DELETE FROM supplier_offers WHERE offer_id = ANY(%s)",(old_seed_offer_ids,))
        for o in offers:
            legacy_id=o['offer_id']
            units=num(o['shopify_units_per_case'])
            qualifying=units if legacy_id in bt_offer_ids else units
            notes='v0.1 seed migration: old blanket assortability intentionally not trusted.'
            if null(o['notes']): notes += ' Legacy note: '+o['notes']
            cur.execute("""
                INSERT INTO supplier_offers(variant_id,vendor_id,supplier_sku,supplier_description,package_type,size_text,raw_pack,
                    shopify_units_per_case,qualifying_units_per_case,assortment_scope,assortment_group,assortable,
                    allocation_limit,active,valid_from,valid_to,source_file,confidence,notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'PRODUCT',NULL,NULL,%s,%s,%s,%s,%s,%s,%s)
                RETURNING offer_id
            """,(o['variant_id'],vendor_ids[o['vendor_name']],null(o['supplier_sku']),null(o['supplier_description']),
                  o['package_type'] or 'STANDARD',null(o['size_text']),null(o['raw_pack']),units,qualifying,num(o['allocation_limit']),
                  boolean(o['active'],True),null(o['valid_from']),null(o['valid_to']),null(o['source']),null(o['confidence']),notes))
            offer_map[legacy_id]=cur.fetchone()[0]
        counts['supplier_offers']=len(offers)

        for p in prices:
            unit=(p.get('break_unit') or '').upper()
            break_unit=None if p['level_type']=='BASE' or unit=='BASE' else unit
            cur.execute("""
                INSERT INTO prices(offer_id,price_state,effective_month,level_type,break_qty,break_unit,case_price,unit_price,
                    source_file,source_page,extraction_confidence,verified,notes)
                VALUES (%s,'current',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
            """,(offer_map[p['offer_id']],p['effective_month'],p['level_type'],num(p['break_qty']),break_unit,num(p['case_price']),
                  num(p['unit_price']),p['source_file'] or 'August v0.1 seed',integer(p['source_page']),null(p['extraction_confidence']),
                  boolean(p['verified']),null(p['notes'])))
        counts['current_prices']=len(prices)

        # Preserve old open exceptions as historical context but de-duplicate exact messages.
        for e in exceptions:
            if e.get('exception_type') == 'CATALOG_SYNC':
                # Superseded by the v1.3 CATALOG_SYNC readiness gate and fresh live reconciliation.
                continue
            vendor_id=vendor_ids.get(e.get('vendor_name')) if e.get('vendor_name') else None
            new_offer=offer_map.get(e.get('offer_id')) if e.get('offer_id') else None
            cur.execute("SELECT 1 FROM exceptions WHERE exception_type=%s AND message=%s AND status='OPEN' LIMIT 1",(e['exception_type'],e['message']))
            if cur.fetchone(): continue
            cur.execute("""
                INSERT INTO exceptions(exception_type,severity,variant_id,offer_id,vendor_id,supplier_sku,message,status,resolution)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'OPEN',%s)
            """,(e['exception_type'],e['severity'],null(e['variant_id']),new_offer,vendor_id,null(e['supplier_sku']),e['message'],null(e['resolution'])))
        counts['source_open_exceptions']=len(exceptions)
        counts['active_migrated_exceptions']=sum(e.get('exception_type') != 'CATALOG_SYNC' for e in exceptions)

        cur.execute("INSERT INTO meta(key,value) VALUES ('catalog_reconciled','false') ON CONFLICT(key) DO UPDATE SET value='false',updated_at=now()")
        cur.execute("INSERT INTO meta(key,value) VALUES ('migration_source','Buffalo Procurement OS v0.1 verified August seed CSV') ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now()")
        cur.execute("UPDATE readiness_gates SET status='FAIL',checked_at=now() WHERE gate_name IN ('CATALOG_SYNC','SALES_BACKFILL')")
    return counts


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--seed-dir',required=True,type=Path); ap.add_argument('--database-url',required=True)
    args=ap.parse_args(); print(import_seed(args.seed_dir,args.database_url))

if __name__=='__main__': main()

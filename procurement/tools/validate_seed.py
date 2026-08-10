#!/usr/bin/env python3
from __future__ import annotations
import csv, json, sys
from pathlib import Path


def rows(path):
    with path.open(newline='',encoding='utf-8') as f: return list(csv.DictReader(f))


def validate(seed: Path):
    v=rows(seed/'variants.csv'); a=rows(seed/'variant_aliases.csv'); o=rows(seed/'supplier_offers.csv'); p=rows(seed/'current_prices.csv')
    variant_ids={r['variant_id'] for r in v}
    offer_ids={r['offer_id'] for r in o}
    errors=[]
    for r in a:
        if r['variant_id'] not in variant_ids: errors.append(f"alias {r['alias_id']} missing canonical variant {r['variant_id']}")
    for r in o:
        if r['variant_id'] not in variant_ids: errors.append(f"offer {r['offer_id']} missing variant {r['variant_id']}")
    for r in p:
        if r['offer_id'] not in offer_ids: errors.append(f"price {r['price_id']} missing offer {r['offer_id']}")
        if not r['unit_price'] or float(r['unit_price'])<=0: errors.append(f"price {r['price_id']} invalid unit price")
        if r['level_type']=='BREAK' and r['break_unit'] not in {'BT','CS','EA'}: errors.append(f"price {r['price_id']} invalid break unit {r['break_unit']}")
    result={
        'variants':len(v),'aliases':len(a),'offers':len(o),'prices':len(p),
        'verified_prices':sum(r.get('verified') in {'1','true','True'} for r in p),
        'errors':errors,
        'valid':not errors,
    }
    return result

if __name__=='__main__':
    seed=Path(sys.argv[1] if len(sys.argv)>1 else 'seed')
    result=validate(seed); print(json.dumps(result,indent=2)); raise SystemExit(0 if result['valid'] else 1)

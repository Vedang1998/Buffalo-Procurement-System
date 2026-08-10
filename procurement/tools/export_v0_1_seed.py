#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

TABLES = {
    "variants": "SELECT * FROM variants ORDER BY variant_id",
    "variant_aliases": "SELECT * FROM variant_aliases ORDER BY alias_id",
    "vendors": "SELECT * FROM vendors ORDER BY vendor_name",
    "supplier_offers": "SELECT * FROM supplier_offers ORDER BY offer_id",
    "current_prices": "SELECT * FROM prices WHERE price_state='current' ORDER BY price_id",
    "open_exceptions": "SELECT * FROM exceptions WHERE status='OPEN' ORDER BY exception_id",
}


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def export(source: Path, dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    conn=sqlite3.connect(source); conn.row_factory=sqlite3.Row
    manifest={"source":source.name,"source_sha256":sha256(source),"files":{}}
    try:
        for name,sql in TABLES.items():
            rows=[dict(r) for r in conn.execute(sql)]
            path=dest/f"{name}.csv"
            fields=list(rows[0].keys()) if rows else []
            with path.open('w',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
            manifest["files"][path.name]={"rows":len(rows),"sha256":sha256(path)}
    finally: conn.close()
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',required=True,type=Path); ap.add_argument('--dest',required=True,type=Path)
    args=ap.parse_args(); print(json.dumps(export(args.source,args.dest),indent=2))

if __name__=='__main__': main()

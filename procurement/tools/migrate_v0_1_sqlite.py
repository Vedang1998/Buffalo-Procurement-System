#!/usr/bin/env python3
"""Compatibility migration: export v0.1 SQLite to canonical seed CSVs, then import to v1.3 Postgres."""
from __future__ import annotations
import argparse, tempfile
from pathlib import Path
from export_v0_1_seed import export
from import_seed_csv import import_seed

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',required=True,type=Path); ap.add_argument('--database-url',required=True); ap.add_argument('--keep-seed-dir',type=Path)
    args=ap.parse_args()
    if args.keep_seed_dir:
        seed=args.keep_seed_dir; export(args.source,seed); print(import_seed(seed,args.database_url))
    else:
        with tempfile.TemporaryDirectory() as td:
            seed=Path(td); export(args.source,seed); print(import_seed(seed,args.database_url))
if __name__=='__main__': main()

#!/usr/bin/env python3
"""Validate an imported seed bundle against manifest.json and persist an
auditable seed_import_records row.

Checks (owner requirements 5-6):
  - seed file SHA-256 hashes match manifest.json;
  - exact expected row counts vs actual DB counts;
  - foreign-key / orphan integrity (zero orphans required);
  - result persisted with manifest, timestamps, counts, validation detail.

This tool never mutates seed rows. It only reads and records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

# manifest file -> (table, where-clause or None)
COUNT_CHECKS = {
    "variants.csv": ("variants", None),
    "variant_aliases.csv": ("variant_aliases", "source LIKE 'v0.1 seed:%'"),
    "vendors.csv": ("vendors", None),
    "supplier_offers.csv": ("supplier_offers", "notes LIKE 'v0.1 seed migration:%'"),
    "current_prices.csv": ("prices", "price_state='current'"),
}

ORPHAN_CHECKS = {
    "aliases_without_variant":
        "SELECT count(*) FROM variant_aliases a LEFT JOIN variants v ON v.variant_id=a.variant_id WHERE v.variant_id IS NULL",
    "offers_without_variant":
        "SELECT count(*) FROM supplier_offers o LEFT JOIN variants v ON v.variant_id=o.variant_id WHERE v.variant_id IS NULL",
    "offers_without_vendor":
        "SELECT count(*) FROM supplier_offers o LEFT JOIN vendors vd ON vd.vendor_id=o.vendor_id WHERE vd.vendor_id IS NULL",
    "prices_without_offer":
        "SELECT count(*) FROM prices p LEFT JOIN supplier_offers o ON o.offer_id=p.offer_id WHERE o.offer_id IS NULL",
    "nonpositive_current_prices":
        "SELECT count(*) FROM prices WHERE price_state='current' AND unit_price<=0",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(seed_dir: Path, database_url: str, package_version: str) -> dict:
    import psycopg

    manifest = json.loads((seed_dir / "manifest.json").read_text(encoding="utf-8"))

    hash_results = {}
    for fname, info in manifest["files"].items():
        actual = sha256(seed_dir / fname)
        hash_results[fname] = {
            "expected_sha256": info["sha256"],
            "actual_sha256": actual,
            "match": actual == info["sha256"],
        }

    expected_counts = {f: info["rows"] for f, info in manifest["files"].items()}
    actual_counts = {}
    orphan_results = {}
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        for fname, (table, where) in COUNT_CHECKS.items():
            q = f"SELECT count(*) FROM {table}"
            if where:
                q += f" WHERE {where}"
            cur.execute(q)
            actual_counts[fname] = cur.fetchone()[0]
        for name, q in ORPHAN_CHECKS.items():
            cur.execute(q)
            orphan_results[name] = cur.fetchone()[0]

        count_ok = all(
            actual_counts[f] == expected_counts[f] for f in COUNT_CHECKS
        )
        hashes_ok = all(r["match"] for r in hash_results.values())
        orphans_ok = all(v == 0 for v in orphan_results.values())
        result = "PASS" if (count_ok and hashes_ok and orphans_ok) else "FAIL"

        cur.execute(
            """
            INSERT INTO seed_import_records(
                manifest_source, manifest_source_sha256, manifest_json,
                seed_package_version, expected_counts_json, actual_counts_json,
                file_hash_validation_json, fk_orphan_validation_json,
                validation_result, notes)
            VALUES (%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
            RETURNING import_id, imported_at
            """,
            (
                manifest.get("source", "unknown"),
                manifest.get("source_sha256", ""),
                json.dumps(manifest),
                package_version,
                json.dumps(expected_counts),
                json.dumps(actual_counts),
                json.dumps(hash_results),
                json.dumps(orphan_results),
                result,
                "Phase 2 seed import verification (open_exceptions.csv is migrated selectively by design;"
                " superseded CATALOG_SYNC exception intentionally not migrated).",
            ),
        )
        import_id, imported_at = cur.fetchone()
        conn.commit()

    return {
        "import_id": import_id,
        "imported_at": str(imported_at),
        "validation_result": result,
        "hashes_ok": hashes_ok,
        "counts_ok": count_ok,
        "orphans_ok": orphans_ok,
        "expected_counts": expected_counts,
        "actual_counts": actual_counts,
        "orphans": orphan_results,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True, type=Path)
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--package-version", default="v1.3")
    args = ap.parse_args()
    print(json.dumps(verify(args.seed_dir, args.database_url, args.package_version), indent=2))


if __name__ == "__main__":
    main()

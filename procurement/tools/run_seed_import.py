#!/usr/bin/env python3
"""Fail-closed seed import orchestrator.

Order of operations:
  1. Pre-validate every seed file's SHA-256 against manifest.json BEFORE any
     database mutation. Any mismatch aborts with no DB contact.
  2. Open ONE connection/transaction: run the v1.3 importer, then row-count and
     FK/orphan validation, then insert the PASS audit record — all atomic.
     Any validation failure raises, rolling back the entire import.
  3. If the transaction rolled back on validation failure, persist a FAIL audit
     record in a separate connection (deliberate failure-audit path) and exit 1.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_seed_csv import import_seed  # noqa: E402
from verify_seed_import import (  # noqa: E402
    COUNT_CHECKS, ORPHAN_CHECKS, sha256,
)


class SeedValidationError(Exception):
    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.detail = detail


def prevalidate_hashes(seed_dir: Path) -> dict:
    manifest = json.loads((seed_dir / "manifest.json").read_text(encoding="utf-8"))
    results = {}
    for fname, info in manifest["files"].items():
        actual = sha256(seed_dir / fname)
        results[fname] = {"expected_sha256": info["sha256"], "actual_sha256": actual,
                          "match": actual == info["sha256"]}
    if not all(r["match"] for r in results.values()):
        bad = [f for f, r in results.items() if not r["match"]]
        raise SeedValidationError(f"manifest hash mismatch: {bad}", {"hash_results": results})
    return {"manifest": manifest, "hash_results": results}


def validate_db_state(cur, expected_counts: dict) -> dict:
    actual_counts, orphans = {}, {}
    for fname, (table, where) in COUNT_CHECKS.items():
        q = f"SELECT count(*) FROM {table}" + (f" WHERE {where}" if where else "")
        cur.execute(q)
        actual_counts[fname] = cur.fetchone()[0]
    for name, q in ORPHAN_CHECKS.items():
        cur.execute(q)
        orphans[name] = cur.fetchone()[0]
    detail = {"expected_counts": expected_counts, "actual_counts": actual_counts, "orphans": orphans}
    if any(actual_counts[f] != expected_counts[f] for f in COUNT_CHECKS):
        raise SeedValidationError("row count mismatch after import", detail)
    if any(v != 0 for v in orphans.values()):
        raise SeedValidationError("orphaned rows detected after import", detail)
    return detail


def insert_audit(cur, manifest, hash_results, detail, result: str, package_version: str, notes: str):
    cur.execute(
        """
        INSERT INTO seed_import_records(
            manifest_source, manifest_source_sha256, manifest_json, seed_package_version,
            expected_counts_json, actual_counts_json, file_hash_validation_json,
            fk_orphan_validation_json, validation_result, notes)
        VALUES (%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
        RETURNING import_id
        """,
        (manifest.get("source", "unknown"), manifest.get("source_sha256", ""),
         json.dumps(manifest), package_version,
         json.dumps(detail.get("expected_counts", {})), json.dumps(detail.get("actual_counts", {})),
         json.dumps(hash_results), json.dumps(detail.get("orphans", {})), result, notes),
    )
    return cur.fetchone()[0]


def run(seed_dir: Path, database_url: str, package_version: str = "v1.3") -> dict:
    import psycopg

    pre = prevalidate_hashes(seed_dir)  # aborts before any DB mutation
    manifest, hash_results = pre["manifest"], pre["hash_results"]
    expected_counts = {f: info["rows"] for f, info in manifest["files"].items()}

    try:
        with psycopg.connect(database_url) as conn:
            with conn.transaction():
                counts = import_seed(seed_dir, conn=conn)
                with conn.cursor() as cur:
                    detail = validate_db_state(cur, expected_counts)
                    import_id = insert_audit(
                        cur, manifest, hash_results, detail, "PASS", package_version,
                        "Fail-closed orchestrated import: hashes pre-validated; import,"
                        " validation and audit committed atomically.")
        return {"validation_result": "PASS", "import_id": import_id,
                "importer_counts": counts, **detail}
    except SeedValidationError as exc:
        # Import rolled back. Record the failure auditably in a fresh connection.
        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            insert_audit(cur, manifest, hash_results, exc.detail, "FAIL", package_version,
                         f"Import rolled back: {exc}")
            conn.commit()
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True, type=Path)
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--package-version", default="v1.3")
    args = ap.parse_args()
    try:
        print(json.dumps(run(args.seed_dir, args.database_url, args.package_version), indent=2))
    except SeedValidationError as exc:
        print(f"SEED IMPORT FAILED (rolled back): {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

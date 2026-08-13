from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_schema import MIGRATION_ORDER, apply_schema


@unittest.skipUnless(os.getenv("DATABASE_URL"), "PostgreSQL integration requires DATABASE_URL")
class SchemaMigrationTests(unittest.TestCase):
    def test_full_migration_chain_is_idempotent_and_audited(self):
        first = apply_schema(ROOT / "db", os.environ["DATABASE_URL"])
        second = apply_schema(ROOT / "db", os.environ["DATABASE_URL"])
        self.assertEqual(first, MIGRATION_ORDER)
        self.assertEqual(second, MIGRATION_ORDER)

        import psycopg

        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("SELECT key,value FROM meta WHERE key LIKE 'migration:%'")
            records = dict(cur.fetchall())
        self.assertEqual(len(records), len(MIGRATION_ORDER))
        self.assertTrue(all(records.get(f"migration:{name}") == "applied" for name in MIGRATION_ORDER))


if __name__ == "__main__":
    unittest.main()

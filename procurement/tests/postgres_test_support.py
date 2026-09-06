"""Shared fail-closed PostgreSQL integration-test connection support.

This module deliberately reuses the authoritative validation functions in
``procurement/tools/run_tests.py``.  It never reads or falls back to ordinary
``DATABASE_URL``.  Tests may create fixture schemas only after this helper has
validated the URL, connected database identity, and PostgreSQL major version.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


RUNNER_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_tests.py"
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "shared_test_database_runner_contract", RUNNER_PATH
)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner_contract = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner_contract
RUNNER_SPEC.loader.exec_module(runner_contract)


def validated_test_connection() -> tuple[Any, Any, Any]:
    """Return a connection only after the single repository safety contract passes."""

    target = runner_contract._validated_test_database_target()
    runner_contract._clear_libpq_environment()

    import psycopg

    connection = psycopg.connect(target.url, connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT current_database(), current_setting('server_version'),
                          current_setting('server_version_num')::integer"""
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("PostgreSQL identity query returned no row")
        database_info = runner_contract._validate_database_facts(
            target,
            database=row[0],
            server_version=row[1],
            server_version_num=row[2],
        )
    except BaseException:
        connection.close()
        raise
    return connection, target, database_info

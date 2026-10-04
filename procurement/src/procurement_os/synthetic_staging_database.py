"""Versioned least-privilege contract for the Railway synthetic database."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from pathlib import Path

from psycopg import sql


CONTRACT_VERSION = "BUFFALO_SYNTHETIC_STAGING_DATABASE_V1"
SCHEMA = "qa_mapping_test"
OBJECT_OWNER = "buffalo_synthetic_owner"
PROVISIONER = "buffalo_synthetic_provisioner"
RUNTIME_LOGIN = "buffalo_synthetic_runtime"
EXPECTED_DATABASE = "buffalo_synthetic_staging"
EXPECTED_POSTGRES_MAJOR = 16
FIXTURE_CONTRACT = "BUFFALO_SYNTHETIC_OWNER_DEMO_V1"

# Every entry is deliberate.  There are no future-object/default privileges.
READ_RELATIONS = (
    "catalog_reconciliation_items", "catalog_sync_runs", "change_log",
    "combo_components", "combo_usage", "combos", "daily_inventory_snapshots",
    "events", "exceptions", "forecast_results", "inventory_snapshot_run_rows",
    "inventory_snapshot_runs", "inventory_snapshots", "legacy_price_seed_events",
    "manual_overrides", "mapping_rejections", "meta",
    "monday_material_edit_confirmations", "monday_packet_build_events",
    "monday_run_artifacts", "monday_run_blocker_exclusions",
    "monday_stale_forecast_retirements", "po_operational_events",
    "po_reconciliation_events", "price_book_batches",
    "price_book_disposition_events", "price_book_promotion_events",
    "price_book_scope_memberships", "price_book_staging_rows",
    "price_book_validation_issues", "prices", "procurement_recommendations",
    "purchase_order_lines", "purchase_orders", "readiness_gates",
    "review_decisions", "run_price_snapshots", "runs", "sales_daily",
    "shopify_sales_daily_raw", "supplier_aliases", "supplier_offers",
    "supplier_price_authority_events", "supplier_price_authority_heads",
    "supplier_price_schedule_policies", "variant_aliases", "variant_policies",
    "variants", "vendor_operating_rules", "vendor_rule_revisions", "vendors",
)

WRITE_RELATIONS: Mapping[str, tuple[str, ...]] = {
    "change_log": ("INSERT",),
    "exceptions": ("INSERT", "UPDATE"),
    "forecast_results": ("INSERT",),
    "inventory_snapshots": ("INSERT",),
    "monday_material_edit_confirmations": ("INSERT",),
    "monday_packet_build_events": ("INSERT",),
    "monday_run_artifacts": ("INSERT",),
    "monday_run_blocker_exclusions": ("INSERT",),
    "monday_stale_forecast_retirements": ("INSERT",),
    "price_book_batches": ("INSERT", "UPDATE"),
    "price_book_disposition_events": ("INSERT",),
    "price_book_promotion_events": ("INSERT",),
    "price_book_scope_memberships": ("INSERT",),
    "price_book_staging_rows": ("INSERT", "DELETE"),
    "price_book_validation_issues": ("INSERT", "UPDATE"),
    "prices": ("INSERT", "UPDATE", "DELETE"),
    "procurement_recommendations": ("INSERT",),
    "purchase_order_lines": ("INSERT",),
    "purchase_orders": ("INSERT", "UPDATE"),
    "review_decisions": ("INSERT",),
    "run_price_snapshots": ("INSERT",),
    "runs": ("INSERT", "UPDATE"),
}

EXECUTE_ROUTINES = (
    "assert_monday_forecast_v2_retirement_contract()",
    "assert_persistent_mapping_foundation_contract()",
    "assert_synthetic_price_replacement_contract()",
    "assert_synthetic_staging_contract()",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


PERMISSION_MATRIX_SHA256 = hashlib.sha256(
    _canonical({"read": READ_RELATIONS, "write": WRITE_RELATIONS, "execute": EXECUTE_ROUTINES})
).hexdigest()


class SyntheticStagingDatabaseError(RuntimeError):
    """The connected database is not the reviewed synthetic staging target."""


@dataclass(frozen=True)
class SyntheticStagingTarget:
    database_url: str
    expected_private_host: str
    project_id: str
    environment_id: str
    app_service_id: str
    postgres_service_id: str

    def validate_static(self) -> None:
        from .staging_config import (
            EXPECTED_APP_SERVICE_ID, EXPECTED_ENVIRONMENT_ID,
            EXPECTED_POSTGRES_SERVICE_ID, EXPECTED_PROJECT_ID,
        )
        parsed = urlparse(self.database_url)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname != self.expected_private_host
            or parsed.username != RUNTIME_LOGIN
            or parsed.password is not None
            or parsed.path != f"/{EXPECTED_DATABASE}"
            or parsed.params or parsed.query or parsed.fragment
            or not re.fullmatch(r"[a-z0-9.-]+\.railway\.internal", self.expected_private_host)
        ):
            raise SyntheticStagingDatabaseError("synthetic staging database destination differs")
        if (
            self.project_id != EXPECTED_PROJECT_ID
            or self.environment_id != EXPECTED_ENVIRONMENT_ID
            or self.app_service_id != EXPECTED_APP_SERVICE_ID
            or self.postgres_service_id != EXPECTED_POSTGRES_SERVICE_ID
        ):
            raise SyntheticStagingDatabaseError("synthetic staging Railway scope differs")


def permission_records() -> tuple[dict[str, str], ...]:
    records: list[dict[str, str]] = []
    for relation in READ_RELATIONS:
        records.append({"object": f"{SCHEMA}.{relation}", "operation": "SELECT", "service": "synthetic-worker"})
    for relation, operations in WRITE_RELATIONS.items():
        for operation in operations:
            records.append({"object": f"{SCHEMA}.{relation}", "operation": operation, "service": "synthetic-worker"})
    for routine in EXECUTE_ROUTINES:
        records.append({"object": f"{SCHEMA}.{routine}", "operation": "EXECUTE", "service": "synthetic-worker"})
    return tuple(records)


def _role_flags(conn: Any, role: str) -> tuple[bool, ...]:
    row = conn.execute(
        "SELECT rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls "
        "FROM pg_catalog.pg_roles WHERE rolname=%s", (role,),
    ).fetchone()
    if row is None:
        raise SyntheticStagingDatabaseError("synthetic staging role is absent")
    return tuple(bool(value) for value in row)


def attest_runtime_connection(conn: Any, target: SyntheticStagingTarget) -> None:
    """Verify identity, topology, markers and the exact effective ACL matrix."""
    target.validate_static()
    row = conn.execute(
        "SELECT current_database(),current_setting('server_version_num')::int,"
        "session_user::text,current_user::text,current_schema()"
    ).fetchone()
    if not row or row[0] != EXPECTED_DATABASE or int(row[1]) // 10000 != 16 or tuple(row[2:]) != (RUNTIME_LOGIN, RUNTIME_LOGIN, SCHEMA):
        raise SyntheticStagingDatabaseError("synthetic staging connected identity differs")
    if _role_flags(conn, RUNTIME_LOGIN) != (False, False, False, False, True, False, False):
        raise SyntheticStagingDatabaseError("synthetic staging runtime role flags differ")
    if _role_flags(conn, OBJECT_OWNER) != (False, False, False, False, False, False, False):
        raise SyntheticStagingDatabaseError("synthetic staging owner role flags differ")
    membership = conn.execute(
        "WITH RECURSIVE path(roleid) AS (SELECT oid FROM pg_roles WHERE rolname=%s "
        "UNION SELECT m.roleid FROM pg_auth_members m JOIN path p ON m.member=p.roleid) "
        "SELECT EXISTS(SELECT 1 FROM path p JOIN pg_roles r ON r.oid=p.roleid "
        "WHERE r.rolname IN (%s,%s) OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls)",
        (RUNTIME_LOGIN, OBJECT_OWNER, PROVISIONER),
    ).fetchone()[0]
    if membership:
        raise SyntheticStagingDatabaseError("synthetic staging runtime has a privileged role path")
    markers = dict(conn.execute(
        sql.SQL("SELECT key,value FROM {}.meta WHERE key=ANY(%s)").format(sql.Identifier(SCHEMA)),
        (["synthetic_owner_demo_contract", "synthetic_staging_contract", "synthetic_staging_permission_matrix_sha256"],),
    ).fetchall())
    if markers != {
        "synthetic_owner_demo_contract": FIXTURE_CONTRACT,
        "synthetic_staging_contract": CONTRACT_VERSION,
        "synthetic_staging_permission_matrix_sha256": PERMISSION_MATRIX_SHA256,
    }:
        raise SyntheticStagingDatabaseError("synthetic staging fixture or contract marker differs")
    conn.execute(sql.SQL("SELECT {}.assert_synthetic_staging_contract()").format(sql.Identifier(SCHEMA)))
    for relation in READ_RELATIONS:
        if not conn.execute("SELECT has_table_privilege(%s,%s,%s)", (RUNTIME_LOGIN, f"{SCHEMA}.{relation}", "SELECT")).fetchone()[0]:
            raise SyntheticStagingDatabaseError("synthetic staging required SELECT privilege is absent")
    for relation in READ_RELATIONS:
        expected = set(WRITE_RELATIONS.get(relation, ()))
        for operation in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
            actual = conn.execute("SELECT has_table_privilege(%s,%s,%s)", (RUNTIME_LOGIN, f"{SCHEMA}.{relation}", operation)).fetchone()[0]
            if bool(actual) != (operation in expected):
                raise SyntheticStagingDatabaseError("synthetic staging effective relation privileges differ")


def provision_contract(conn: Any, *, sql_path: Path | None = None) -> None:
    """Operator-only transition of an initialized disposable synthetic database."""
    if conn.execute("SELECT current_user = session_user AND rolsuper FROM pg_roles WHERE rolname=current_user").fetchone() != (True,):
        raise SyntheticStagingDatabaseError("synthetic staging provisioning requires an administrative session")
    if conn.execute("SELECT current_database()").fetchone() != (EXPECTED_DATABASE,):
        raise SyntheticStagingDatabaseError("synthetic staging provisioning database differs")
    for role, login in ((OBJECT_OWNER, False), (PROVISIONER, True), (RUNTIME_LOGIN, True)):
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is None:
            conn.execute(sql.SQL("CREATE ROLE {} {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role), sql.SQL("LOGIN" if login else "NOLOGIN")))
    conn.execute(sql.SQL("GRANT {} TO {} WITH SET TRUE, INHERIT FALSE, ADMIN FALSE").format(sql.Identifier(OBJECT_OWNER), sql.Identifier(PROVISIONER)))
    conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(EXPECTED_DATABASE)))
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(EXPECTED_DATABASE), sql.Identifier(RUNTIME_LOGIN)))
    conn.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
    # The canonical fixture is restored with this legacy owner.  In the
    # dedicated database, transfer every restored object before enumerating
    # the runtime ACL; this does not grant the runtime membership.
    conn.execute(sql.SQL("REASSIGN OWNED BY {} TO {}").format(sql.Identifier("qa_mapping_owner"), sql.Identifier(OBJECT_OWNER)))
    conn.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(SCHEMA), sql.Identifier(OBJECT_OWNER)))
    conn.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC, {}").format(sql.Identifier(SCHEMA), sql.Identifier(RUNTIME_LOGIN)))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(SCHEMA), sql.Identifier(RUNTIME_LOGIN)))
    for relation in READ_RELATIONS:
        conn.execute(sql.SQL("REVOKE ALL ON TABLE {}.{} FROM PUBLIC, {}").format(sql.Identifier(SCHEMA), sql.Identifier(relation), sql.Identifier(RUNTIME_LOGIN)))
        conn.execute(sql.SQL("GRANT SELECT ON TABLE {}.{} TO {}").format(sql.Identifier(SCHEMA), sql.Identifier(relation), sql.Identifier(RUNTIME_LOGIN)))
        operations = WRITE_RELATIONS.get(relation)
        if operations:
            conn.execute(sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(sql.SQL(",").join(map(sql.SQL, operations)), sql.Identifier(SCHEMA), sql.Identifier(relation), sql.Identifier(RUNTIME_LOGIN)))
    conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE ALL ON TABLES FROM PUBLIC, {}").format(sql.Identifier(OBJECT_OWNER), sql.Identifier(SCHEMA), sql.Identifier(RUNTIME_LOGIN)))
    conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE ALL ON FUNCTIONS FROM PUBLIC, {}").format(sql.Identifier(OBJECT_OWNER), sql.Identifier(SCHEMA), sql.Identifier(RUNTIME_LOGIN)))
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OBJECT_OWNER)))
    conn.execute(sql.SQL("INSERT INTO {}.meta(key,value) VALUES (%s,%s),(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value").format(sql.Identifier(SCHEMA)), ("synthetic_staging_contract", CONTRACT_VERSION, "synthetic_staging_permission_matrix_sha256", PERMISSION_MATRIX_SHA256))
    contract_sql = (sql_path or Path(__file__).resolve().parents[2] / "db" / "017_synthetic_staging_contract.sql").read_text(encoding="utf-8")
    conn.execute(contract_sql)
    conn.execute("RESET ROLE")

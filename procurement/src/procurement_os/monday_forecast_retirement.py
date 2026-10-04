"""Independent installed-contract verification for Monday forecast V2.

Migration 015 is a post-mapping application release.  Its own SQL assertion is
useful only after a Python-owned catalog projection has matched this module's
literal reviewed digest; a forged assertion can therefore never define the
expected contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import psycopg
from psycopg import sql


MIGRATION_NAME = "015_monday_forecast_v2_retirement.sql"
CONTRACT_VERSION = "v1"
RETIRED_METHOD_VERSION = "EMERGENCY_TRANSPARENT_V1"
CURRENT_METHOD_VERSION = "EMERGENCY_TRANSPARENT_V2"
TARGET_SCHEMA = "qa_mapping_test"
MIGRATION_SHA256 = "e3f69e23cf6fce0760add5ec6a329426444dd44b681e338aded9d833e89ba56b"
CATALOG_SHA256 = "2fafe14a6dd9394fbebb471f84b768f77bd9e2675cd8e3746576a8a9d3099e7d"
# Metadata written by the pre-canonical f2bdd169 catalog projection remains a
# recognized provenance value.  It never substitutes for recomputing and
# matching the current stable catalog digest below.
LEGACY_CATALOG_SHA256 = (
    "0d151f70f6eec2965428e0bec64ab573962a8aad344b14a9d44332edff284cd9"
)
ACCEPTED_CATALOG_METADATA_SHA256 = frozenset(
    {CATALOG_SHA256, LEGACY_CATALOG_SHA256}
)

_FUNCTION_IDENTITIES = (
    ("assert_monday_forecast_v2_retirement_contract", ""),
    ("assert_monday_stale_forecast_retirement_commit", ""),
    ("guard_monday_forecast_v1_child_evidence", ""),
    ("guard_monday_forecast_v1_run", ""),
    (
        "monday_stale_forecast_retirement_confirmation_sha256",
        "target_run_id uuid, target_input_fingerprint text, "
        "target_prior_workflow_stage text, target_actor text, target_reason text",
    ),
    ("protect_monday_stale_forecast_retirement_audit", ""),
    ("validate_monday_stale_forecast_retirement", ""),
)

_TRIGGER_NAMES = (
    "trg_assert_monday_stale_forecast_retirement_audit_commit",
    "trg_assert_monday_stale_forecast_retirement_event_commit",
    "trg_guard_monday_forecast_v1_run",
    "trg_guard_v1_exceptions",
    "trg_guard_v1_forecast_results",
    "trg_guard_v1_inventory_snapshots",
    "trg_guard_v1_monday_material_edit_confirmations",
    "trg_guard_v1_monday_run_blocker_exclusions",
    "trg_guard_v1_po_operational_events",
    "trg_guard_v1_po_reconciliation_events",
    "trg_guard_v1_procurement_recommendations",
    "trg_guard_v1_purchase_order_lines",
    "trg_guard_v1_purchase_orders",
    "trg_guard_v1_review_decisions",
    "trg_guard_v1_run_price_snapshots",
    "trg_protect_monday_stale_forecast_retirement_audit",
    "trg_validate_monday_stale_forecast_retirement",
)
_TRIGGER_IDENTITIES = (
    ("trg_assert_monday_stale_forecast_retirement_audit_commit", "change_log"),
    (
        "trg_assert_monday_stale_forecast_retirement_event_commit",
        "monday_stale_forecast_retirements",
    ),
    ("trg_guard_monday_forecast_v1_run", "runs"),
    ("trg_guard_v1_exceptions", "exceptions"),
    ("trg_guard_v1_forecast_results", "forecast_results"),
    ("trg_guard_v1_inventory_snapshots", "inventory_snapshots"),
    (
        "trg_guard_v1_monday_material_edit_confirmations",
        "monday_material_edit_confirmations",
    ),
    ("trg_guard_v1_monday_run_blocker_exclusions", "monday_run_blocker_exclusions"),
    ("trg_guard_v1_po_operational_events", "po_operational_events"),
    ("trg_guard_v1_po_reconciliation_events", "po_reconciliation_events"),
    ("trg_guard_v1_procurement_recommendations", "procurement_recommendations"),
    ("trg_guard_v1_purchase_order_lines", "purchase_order_lines"),
    ("trg_guard_v1_purchase_orders", "purchase_orders"),
    ("trg_guard_v1_review_decisions", "review_decisions"),
    ("trg_guard_v1_run_price_snapshots", "run_price_snapshots"),
    ("trg_protect_monday_stale_forecast_retirement_audit", "change_log"),
    (
        "trg_validate_monday_stale_forecast_retirement",
        "monday_stale_forecast_retirements",
    ),
)


class MondayForecastRetirementContractError(RuntimeError):
    """The source or installed retirement contract differs from reviewed V1."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize_catalog_text(value: Any, schema: str) -> Any:
    if value is None:
        return "<NULL>"
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_catalog_text(item, schema) for item in value)
    return str(value)


def _normalize_function_config(value: Any) -> Any:
    """Canonicalize the two pg_dump spellings of the reviewed search path."""

    if value is None:
        return "<NULL>"
    reviewed_spellings = {
        'search_path="qa_mapping_test",pg_catalog',
        "search_path=qa_mapping_test, pg_catalog",
    }
    return tuple(
        "search_path=qa_mapping_test,pg_catalog"
        if str(setting) in reviewed_spellings
        else str(setting)
        for setting in value
    )


def _normalize_trigger_qual(value: Any) -> str:
    """Remove parser offsets while retaining the complete trigger predicate tree."""

    if value is None:
        return "<NULL>"
    return re.sub(r":location -?\d+", ":location <NORMALIZED>", str(value))


def retirement_catalog_projection(conn: Any, schema: str) -> dict[str, Any]:
    """Return the exact normalized pg_catalog facts owned by migration 015."""

    if schema != TARGET_SCHEMA:
        raise MondayForecastRetirementContractError("retirement target schema differs")
    conn.execute(
        "SELECT pg_catalog.set_config('search_path',%s,true)",
        (f'"{TARGET_SCHEMA}",pg_catalog',),
    )
    effective_path = tuple(
        str(item)
        for item in conn.execute(
            "SELECT pg_catalog.current_schemas(false)"
        ).fetchone()[0]
    )
    if effective_path != (TARGET_SCHEMA, "pg_catalog"):
        raise MondayForecastRetirementContractError(
            "retirement catalog search path differs"
        )
    schema_oid_row = conn.execute(
        "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s", (schema,)
    ).fetchone()
    if schema_oid_row is None:
        raise MondayForecastRetirementContractError("retirement target schema is absent")
    schema_oid = int(schema_oid_row[0])
    relations = conn.execute(
        """SELECT c.relname,c.relkind,c.relpersistence,
                  pg_catalog.pg_get_userbyid(c.relowner),c.relrowsecurity,
                  c.relforcerowsecurity,c.relreplident,
                  COALESCE(c.relacl::text,'<NULL>')
             FROM pg_catalog.pg_class c
            WHERE c.relnamespace=%s
              AND c.relname='monday_stale_forecast_retirements'
            ORDER BY c.relname""",
        (schema_oid,),
    ).fetchall()
    columns = conn.execute(
        """SELECT c.relname,a.attname,a.attnum,
                  pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
                  a.attidentity,a.attgenerated,
                  COALESCE(coll.collname,'<NULL>'),
                  COALESCE(pg_catalog.pg_get_expr(d.adbin,d.adrelid),'<NULL>'),
                  COALESCE(a.attacl::text,'<NULL>')
             FROM pg_catalog.pg_class c
             JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
             LEFT JOIN pg_catalog.pg_attrdef d
               ON d.adrelid=a.attrelid AND d.adnum=a.attnum
             LEFT JOIN pg_catalog.pg_collation coll ON coll.oid=a.attcollation
            WHERE c.relnamespace=%s AND a.attnum>0 AND NOT a.attisdropped
              AND (c.relname='monday_stale_forecast_retirements'
                   OR (c.relname='change_log' AND a.attname='evidence_json'))
            ORDER BY c.relname,a.attnum""",
        (schema_oid,),
    ).fetchall()
    constraints = conn.execute(
        """SELECT r.relname,c.conname,c.contype,c.condeferrable,c.condeferred,
                  c.convalidated,c.connoinherit,
                  pg_catalog.pg_get_constraintdef(c.oid,true)
             FROM pg_catalog.pg_constraint c
             JOIN pg_catalog.pg_class r ON r.oid=c.conrelid
            WHERE r.relnamespace=%s
              AND r.relname='monday_stale_forecast_retirements'
            ORDER BY c.conname""",
        (schema_oid,),
    ).fetchall()
    indexes = conn.execute(
        """SELECT r.relname,i.relname,pg_catalog.pg_get_userbyid(i.relowner),
                  COALESCE(i.relacl::text,'<NULL>'),x.indisunique,x.indisprimary,
                  x.indisvalid,x.indisready,x.indislive,x.indisreplident,
                  pg_catalog.pg_get_indexdef(i.oid),
                  COALESCE(pg_catalog.pg_get_expr(x.indpred,x.indrelid),'<NULL>')
             FROM pg_catalog.pg_class i
             JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid
             JOIN pg_catalog.pg_class r ON r.oid=x.indrelid
            WHERE r.relnamespace=%s
              AND (r.relname='monday_stale_forecast_retirements'
                   OR i.relname='uq_change_log_stale_forecast_retirement')
            ORDER BY i.relname""",
        (schema_oid,),
    ).fetchall()
    functions = conn.execute(
        """SELECT p.proname,
                  pg_catalog.pg_get_function_identity_arguments(p.oid),
                  pg_catalog.pg_get_function_result(p.oid),p.prokind,l.lanname,
                  p.provolatile,p.proisstrict,p.prosecdef,p.proleakproof,
                  p.proparallel,pg_catalog.pg_get_userbyid(p.proowner),
                  p.proretset,p.pronargs,p.pronargdefaults,p.procost,p.prorows,
                  COALESCE(pg_catalog.pg_get_expr(p.proargdefaults,0),'<NULL>'),
                  p.proconfig,p.prosrc,
                  COALESCE(p.probin,'<NULL>'),COALESCE(p.proacl::text,'<NULL>')
             FROM pg_catalog.pg_proc p
             JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE p.pronamespace=%s
              AND p.proname=ANY(%s)
            ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)""",
        (
            schema_oid,
            [name for name, _ in _FUNCTION_IDENTITIES],
        ),
    ).fetchall()
    triggers = conn.execute(
        """SELECT t.tgname,c.relname,t.tgenabled,t.tgtype,t.tgdeferrable,t.tginitdeferred,
                  pg_catalog.pg_get_triggerdef(t.oid,true),p.proname,
                  pg_catalog.pg_get_function_identity_arguments(p.oid),
                  COALESCE(t.tgqual::text,'<NULL>'),
                  pg_catalog.encode(t.tgargs,'hex')
             FROM pg_catalog.pg_trigger t
             JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
             JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            WHERE c.relnamespace=%s AND NOT t.tgisinternal AND t.tgname=ANY(%s)
            ORDER BY t.tgname""",
        (schema_oid, list(_TRIGGER_NAMES)),
    ).fetchall()
    found_functions = tuple((str(row[0]), str(row[1])) for row in functions)
    found_triggers = tuple((str(row[0]), str(row[1])) for row in triggers)
    if found_functions != _FUNCTION_IDENTITIES:
        raise MondayForecastRetirementContractError(
            "retirement function identity inventory differs"
        )
    if found_triggers != _TRIGGER_IDENTITIES:
        raise MondayForecastRetirementContractError(
            "retirement trigger inventory differs"
        )
    projection = {
        "columns": columns,
        "constraints": constraints,
        "functions": functions,
        "indexes": indexes,
        "relations": relations,
        "triggers": triggers,
    }
    normalized: dict[str, list[list[Any]]] = {}
    for key, rows in projection.items():
        normalized[key] = []
        for row in rows:
            values = list(row)
            if key == "functions":
                values[17] = _normalize_function_config(values[17])
            elif key == "triggers":
                values[9] = _normalize_trigger_qual(values[9])
            normalized[key].append(
                [_normalize_catalog_text(value, schema) for value in values]
            )
    return normalized


def compute_retirement_catalog_sha256(conn: Any, schema: str) -> str:
    return hashlib.sha256(
        _canonical_json(retirement_catalog_projection(conn, schema))
    ).hexdigest()


def _verify_monday_forecast_v2_retirement_contract(
    conn: Any,
    *,
    schema: str | None = None,
    require_marker: bool = True,
) -> str:
    """Verify marker/catalog first, invoke SQL assertion, then reverify."""

    target_schema = schema or str(conn.execute("SELECT current_schema()").fetchone()[0])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target_schema):
        raise MondayForecastRetirementContractError("retirement schema name is malformed")
    if target_schema != TARGET_SCHEMA:
        raise MondayForecastRetirementContractError("retirement target schema differs")
    from .synthetic_staging_database import (
        STAGING_RETIREMENT_CATALOG_SHA256,
        is_staging_runtime_connection,
    )

    if is_staging_runtime_connection(conn):
        computed = compute_retirement_catalog_sha256(conn, target_schema)
        if computed != STAGING_RETIREMENT_CATALOG_SHA256:
            raise MondayForecastRetirementContractError(
                "staging retirement successor catalog differs"
            )
        conn.execute(
            sql.SQL("SELECT {}.assert_synthetic_staging_contract()").format(
                sql.Identifier(target_schema)
            )
        )
        if compute_retirement_catalog_sha256(
            conn, target_schema
        ) != STAGING_RETIREMENT_CATALOG_SHA256:
            raise MondayForecastRetirementContractError(
                "staging retirement successor changed during assertion"
            )
        return computed
    expected_marker = f"sha256:{MIGRATION_SHA256}"
    wanted = [
        "monday_forecast_v2_retirement_contract",
        "monday_forecast_v2_retirement_catalog_sha256",
    ]
    if require_marker:
        wanted.append(f"migration:{MIGRATION_NAME}")
    metadata = dict(
        conn.execute(
            sql.SQL("SELECT key,value FROM {}.meta WHERE key=ANY(%s)").format(
                sql.Identifier(target_schema)
            ),
            (wanted,),
        ).fetchall()
    )
    if metadata.get("monday_forecast_v2_retirement_contract") != CONTRACT_VERSION:
        raise MondayForecastRetirementContractError(
            "retirement contract metadata differs"
        )
    if (
        metadata.get("monday_forecast_v2_retirement_catalog_sha256")
        not in ACCEPTED_CATALOG_METADATA_SHA256
    ):
        raise MondayForecastRetirementContractError(
            "retirement catalog metadata differs"
        )
    if require_marker and metadata.get(f"migration:{MIGRATION_NAME}") != expected_marker:
        raise MondayForecastRetirementContractError("retirement migration marker differs")
    computed = compute_retirement_catalog_sha256(conn, target_schema)
    if computed != CATALOG_SHA256:
        raise MondayForecastRetirementContractError(
            "retirement installed catalog differs"
        )
    conn.execute(
        sql.SQL("SELECT {}.assert_monday_forecast_v2_retirement_contract()").format(
            sql.Identifier(target_schema)
        )
    )
    if compute_retirement_catalog_sha256(conn, target_schema) != CATALOG_SHA256:
        raise MondayForecastRetirementContractError(
            "retirement installed catalog changed during assertion"
        )
    return computed


def verify_monday_forecast_v2_retirement_contract(
    conn: Any,
    *,
    schema: str | None = None,
    require_marker: bool = True,
) -> str:
    """Return a typed refusal for either catalog or database invariant drift."""

    try:
        return _verify_monday_forecast_v2_retirement_contract(
            conn, schema=schema, require_marker=require_marker
        )
    except MondayForecastRetirementContractError:
        raise
    except psycopg.Error as exc:
        raise MondayForecastRetirementContractError(
            "retirement database contract assertion refused"
        ) from exc

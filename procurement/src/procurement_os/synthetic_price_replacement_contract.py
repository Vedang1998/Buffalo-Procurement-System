"""Independent source/catalog verification for synthetic price replacement V1."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import psycopg
from psycopg import sql


MIGRATION_NAME = "016_synthetic_price_replacement.sql"
CONTRACT_VERSION = "v1-complete-vendor-monthly-synthetic"
TARGET_SCHEMA = "qa_mapping_test"
MIGRATION_SHA256 = "5d74074b7c2f79eb8e9b92bbb3302923c80003b6bce25803671078b0645ea35d"
CATALOG_SHA256 = "00e01c90bdc324544b2746880d5c8d6821dad3a6b1698b761a78d91c8b9c1a88"
FIXTURE_REGISTRATION_REF = "config/synthetic_price_replacement_fixture.json"
FIXTURE_REGISTRATION_CANONICAL_SHA256 = (
    "4ac0137a42e79f560fbab6a4f6073324e553f2ca924d0e3a8c5956f51dd79659"
)

_RELATIONS = (
    "price_book_scope_memberships",
    "supplier_price_authority_events",
    "supplier_price_authority_heads",
    "supplier_price_schedule_policies",
)
_VIEWS = ("v_current_prices", "v_verified_current_prices")
_FUNCTIONS = (
    "assert_supplier_price_authority_commit",
    "assert_synthetic_price_replacement_contract",
    "guard_price_book_batch_update",
    "prevent_supplier_price_authority_mutation",
    "protect_promoted_price",
    "supplier_price_membership_sha256",
    "supplier_price_require_enabled_capability",
    "supplier_price_scope_current_payload",
    "supplier_price_scope_current_sha256",
    "supplier_price_scope_batch_as_current_payload",
    "supplier_price_scope_batch_as_current_sha256",
    "supplier_price_text_sha256",
    "supplier_price_unaffected_state_sha256",
    "validate_declared_price_confirmation_event",
    "validate_declared_price_promotion_event",
    "validate_price_book_scope_membership_insert",
    "validate_price_book_price_provenance",
    "validate_supplier_price_authority_event",
    "validate_supplier_price_authority_head",
    "validate_supplier_price_schedule_policy_insert",
)
_TRIGGERS = (
    "trg_assert_supplier_price_authority_commit",
    "trg_protect_price_book_scope_memberships",
    "trg_protect_supplier_price_authority_events",
    "trg_protect_supplier_price_authority_head_delete",
    "trg_protect_supplier_price_schedule_policies",
    "trg_validate_declared_price_confirmation",
    "trg_validate_declared_price_promotion",
    "trg_validate_price_book_promotion_event",
    "trg_validate_price_book_scope_membership_insert",
    "trg_validate_supplier_price_authority_event",
    "trg_validate_supplier_price_authority_head",
    "trg_validate_supplier_price_schedule_policy_insert",
)
_ALTERED_COLUMNS = {
    "price_book_batches": (
        "declaration_sha256",
        "operational_effective_from",
        "operational_effective_through",
        "price_scope_key",
        "replacement_contract",
        "schedule_policy_ref",
        "scope_membership_sha256",
        "source_period_label",
        "source_valid_from",
        "source_valid_through",
        "source_validity_basis",
        "supplier_verified_at",
    ),
    "price_book_promotion_events": (
        "confirmation_idempotency_key",
        "confirmation_payload_sha256",
        "confirmation_sha256",
        "human_authn_context_sha256",
        "human_principal_ref",
        "human_role_ref",
        "preview_sha256",
        "raw_content_sha256",
        "declaration_sha256",
        "policy_sha256",
        "price_scope_key",
        "replacement_contract",
        "schedule_policy_ref",
        "scope_membership_sha256",
    ),
    "run_price_snapshots": (
        "source_price_book_batch_id",
        "source_price_book_row_number",
        "source_price_id",
        "supplier_price_authority_event_id",
    ),
}


class SyntheticPriceReplacementContractError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _normalize(value: Any) -> Any:
    if value is None:
        return "<NULL>"
    if isinstance(value, (list, tuple)):
        return tuple(_normalize(item) for item in value)
    text = str(value)
    # PostgreSQL stores parser byte offsets in pg_node_tree values. pg_dump
    # reconstructs the same trigger expression with different whitespace, so
    # offsets are transport noise rather than executable catalog identity.
    return re.sub(r":location -?\d+", ":location <NORMALIZED>", text)


def synthetic_price_catalog_projection(conn: Any, schema: str) -> dict[str, Any]:
    if schema != TARGET_SCHEMA:
        raise SyntheticPriceReplacementContractError(
            "synthetic price replacement target schema differs"
        )
    schema_row = conn.execute(
        "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s", (schema,)
    ).fetchone()
    if schema_row is None:
        raise SyntheticPriceReplacementContractError(
            "synthetic price replacement target schema is absent"
        )
    schema_oid = int(schema_row[0])
    relations = conn.execute(
        """SELECT c.relname,c.relkind,c.relpersistence,
                  pg_catalog.pg_get_userbyid(c.relowner),c.relrowsecurity,
                  c.relforcerowsecurity,c.relreplident,COALESCE(c.relacl::text,'<NULL>')
             FROM pg_catalog.pg_class c
            WHERE c.relnamespace=%s AND c.relname=ANY(%s)
            ORDER BY c.relname""",
        (schema_oid, list(_RELATIONS + _VIEWS)),
    ).fetchall()
    columns = conn.execute(
        """SELECT c.relname,a.attname,a.attnum,
                  pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
                  a.attidentity,a.attgenerated,COALESCE(coll.collname,'<NULL>'),
                  COALESCE(pg_catalog.pg_get_expr(d.adbin,d.adrelid),'<NULL>'),
                  COALESCE(a.attacl::text,'<NULL>')
             FROM pg_catalog.pg_class c
             JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
             LEFT JOIN pg_catalog.pg_attrdef d
               ON d.adrelid=a.attrelid AND d.adnum=a.attnum
             LEFT JOIN pg_catalog.pg_collation coll ON coll.oid=a.attcollation
            WHERE c.relnamespace=%s AND a.attnum>0 AND NOT a.attisdropped
              AND (c.relname=ANY(%s)
                OR (c.relname='price_book_batches' AND a.attname=ANY(%s))
                OR (c.relname='price_book_promotion_events' AND a.attname=ANY(%s))
                OR (c.relname='run_price_snapshots' AND a.attname=ANY(%s)))
            ORDER BY c.relname,a.attnum""",
        (
            schema_oid,
            list(_RELATIONS + _VIEWS),
            list(_ALTERED_COLUMNS["price_book_batches"]),
            list(_ALTERED_COLUMNS["price_book_promotion_events"]),
            list(_ALTERED_COLUMNS["run_price_snapshots"]),
        ),
    ).fetchall()
    constraints = conn.execute(
        """SELECT r.relname,c.conname,c.contype,c.condeferrable,c.condeferred,
                  c.convalidated,c.connoinherit,pg_catalog.pg_get_constraintdef(c.oid,true)
             FROM pg_catalog.pg_constraint c
             JOIN pg_catalog.pg_class r ON r.oid=c.conrelid
            WHERE r.relnamespace=%s
              AND (r.relname=ANY(%s)
                OR c.conname=ANY(%s))
            ORDER BY r.relname,c.conname""",
        (
            schema_oid,
            list(_RELATIONS),
            [
                "ck_declared_price_confirmation",
                "ck_declared_price_replacement",
                "ck_price_book_batch_state",
                "ck_run_price_authority_lineage",
                "price_book_batches_status_check",
            ],
        ),
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
              AND (r.relname=ANY(%s) OR i.relname=ANY(%s))
            ORDER BY r.relname,i.relname""",
        (
            schema_oid,
            list(_RELATIONS),
            [
                "uq_declared_price_confirmation_idempotency",
                "uq_price_book_scope_membership_ladder",
            ],
        ),
    ).fetchall()
    functions = conn.execute(
        """SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),
                  pg_catalog.pg_get_function_result(p.oid),p.prokind,l.lanname,
                  p.provolatile,p.proisstrict,p.prosecdef,p.proleakproof,p.proparallel,
                  pg_catalog.pg_get_userbyid(p.proowner),p.proretset,p.pronargs,
                  p.pronargdefaults,p.procost,p.prorows,
                  COALESCE(pg_catalog.pg_get_expr(p.proargdefaults,0),'<NULL>'),
                  p.proconfig,p.prosrc,COALESCE(p.probin,'<NULL>'),
                  COALESCE(p.proacl::text,'<NULL>')
             FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE p.pronamespace=%s AND p.proname=ANY(%s)
            ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)""",
        (schema_oid, list(_FUNCTIONS)),
    ).fetchall()
    triggers = conn.execute(
        """SELECT t.tgname,c.relname,t.tgenabled,t.tgtype,t.tgdeferrable,
                  t.tginitdeferred,pg_catalog.pg_get_triggerdef(t.oid,true),
                  p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),
                  COALESCE(t.tgqual::text,'<NULL>'),pg_catalog.encode(t.tgargs,'hex')
             FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
             JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            WHERE c.relnamespace=%s AND NOT t.tgisinternal AND t.tgname=ANY(%s)
            ORDER BY t.tgname""",
        (schema_oid, list(_TRIGGERS)),
    ).fetchall()
    views = conn.execute(
        """SELECT c.relname,pg_catalog.pg_get_viewdef(c.oid,true)
             FROM pg_catalog.pg_class c
            WHERE c.relnamespace=%s AND c.relname=ANY(%s)
            ORDER BY c.relname""",
        (schema_oid, list(_VIEWS)),
    ).fetchall()
    found_relations = tuple(str(row[0]) for row in relations)
    if found_relations != tuple(sorted(_RELATIONS + _VIEWS)):
        raise SyntheticPriceReplacementContractError(
            "synthetic price replacement relation inventory differs"
        )
    found_functions = tuple(sorted({str(row[0]) for row in functions}))
    if found_functions != tuple(sorted(_FUNCTIONS)):
        raise SyntheticPriceReplacementContractError(
            "synthetic price replacement function inventory differs"
        )
    found_triggers = tuple(str(row[0]) for row in triggers)
    if found_triggers != tuple(sorted(_TRIGGERS)):
        raise SyntheticPriceReplacementContractError(
            "synthetic price replacement trigger inventory differs"
        )
    projection = {
        "columns": columns,
        "constraints": constraints,
        "functions": functions,
        "indexes": indexes,
        "relations": relations,
        "triggers": triggers,
        "views": views,
    }
    return {
        key: [[_normalize(value) for value in row] for row in rows]
        for key, rows in projection.items()
    }


def compute_synthetic_price_catalog_sha256(conn: Any, schema: str) -> str:
    return hashlib.sha256(
        _canonical_json(synthetic_price_catalog_projection(conn, schema))
    ).hexdigest()


def verify_synthetic_price_replacement_contract(
    conn: Any,
    *,
    schema: str = TARGET_SCHEMA,
    require_marker: bool = True,
) -> str:
    try:
        wanted = [
            "synthetic_price_replacement_contract",
            "synthetic_price_replacement_catalog_sha256",
        ]
        if require_marker:
            wanted.append(f"migration:{MIGRATION_NAME}")
        metadata = dict(
            conn.execute(
                sql.SQL("SELECT key,value FROM {}.meta WHERE key=ANY(%s)").format(
                    sql.Identifier(schema)
                ),
                (wanted,),
            ).fetchall()
        )
        if metadata.get("synthetic_price_replacement_contract") != CONTRACT_VERSION:
            raise SyntheticPriceReplacementContractError(
                "synthetic price replacement metadata differs"
            )
        if metadata.get("synthetic_price_replacement_catalog_sha256") != CATALOG_SHA256:
            raise SyntheticPriceReplacementContractError(
                "synthetic price replacement catalog metadata differs"
            )
        if require_marker and metadata.get(f"migration:{MIGRATION_NAME}") != (
            f"sha256:{MIGRATION_SHA256}"
        ):
            raise SyntheticPriceReplacementContractError(
                "synthetic price replacement marker differs"
            )
        computed = compute_synthetic_price_catalog_sha256(conn, schema)
        if computed != CATALOG_SHA256:
            raise SyntheticPriceReplacementContractError(
                "synthetic price replacement installed catalog differs"
            )
        conn.execute(
            sql.SQL("SELECT {}.assert_synthetic_price_replacement_contract()").format(
                sql.Identifier(schema)
            )
        )
        if compute_synthetic_price_catalog_sha256(conn, schema) != CATALOG_SHA256:
            raise SyntheticPriceReplacementContractError(
                "synthetic price replacement catalog changed during assertion"
            )
        return computed
    except SyntheticPriceReplacementContractError:
        raise
    except psycopg.Error as exc:
        raise SyntheticPriceReplacementContractError(
            "synthetic price replacement database assertion refused"
        ) from exc


def source_constants_are_well_formed() -> None:
    if MIGRATION_SHA256 != "TO_BE_COMPUTED" and not re.fullmatch(
        r"[0-9a-f]{64}", MIGRATION_SHA256
    ):
        raise SyntheticPriceReplacementContractError("migration digest is malformed")
    if CATALOG_SHA256 != "TO_BE_COMPUTED" and not re.fullmatch(
        r"[0-9a-f]{64}", CATALOG_SHA256
    ):
        raise SyntheticPriceReplacementContractError("catalog digest is malformed")

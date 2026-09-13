"""Persistent supplier-mapping intake, decision, and selection services.

The database migration owns validation.  These services create its exact
canonical JSONB values, enter SERIALIZABLE transactions, install only
server-derived human context, serialize idempotency keys, and never activate an
offer, create a price, alter a recommendation, call Shopify, contact a
supplier, or create an order.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import copy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
import time
from typing import Any, TypeVar
from urllib.parse import parse_qsl, urlparse
from uuid import UUID, uuid5, NAMESPACE_URL

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


SCHEMA = "qa_mapping_test"
SELECTION_SCOPE = "ROUTINE_PROCUREMENT_STANDARD"
_RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})
_IDEMPOTENCY_CONSTRAINTS = frozenset(
    {
        "uq_mapping_review_batches_idempotency",
        "uq_supplier_mapping_decisions_idempotency",
        "uq_supplier_offer_selection_events_idempotency",
    }
)
_T = TypeVar("_T")
SESSION_LOCK_WAIT_SECONDS = 5.0
TOTAL_OPERATION_SECONDS = 30.0
MAX_TRANSACTION_ATTEMPTS = 3


class PersistentMappingError(ValueError):
    def __init__(self, message: str, *, code: str = "MAPPING_REFUSED"):
        super().__init__(message)
        self.code = code


_SYNTHETIC_DEMO_CAPABILITIES = frozenset(
    {
        "review_intake_writes_enabled",
        "human_mapping_writes_enabled",
        "routine_selection_writes_enabled",
        "selected_offer_shadow_reads_enabled",
    }
)


def require_synthetic_mapping_capability(capability: str) -> None:
    """Authorize the fixed local overlay before database or storage access.

    The repository policy stays disabled.  Only an explicitly launched
    AUTOMATED_TEST or SYNTHETIC_DEMO process may exercise these reviewed
    shadow-only boundaries; activation and recommendation cutover have no
    overlay.
    """

    if capability not in _SYNTHETIC_DEMO_CAPABILITIES:
        raise PersistentMappingError("persistent mapping capability is unsupported")
    mode = os.getenv("BUFFALO_RUNTIME_MODE", "UNCONFIGURED").strip().upper()
    enabled = os.getenv("BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO", "") == "1"
    if mode not in {"AUTOMATED_TEST", "SYNTHETIC_DEMO"} or not enabled:
        raise PersistentMappingError("synthetic persistent mapping capability is disabled")

    from .config import load_rules

    policy = load_rules().get("persistent_mapping", {})
    if (
        policy.get(capability) is not False
        or policy.get("recommendation_cutover_enabled") is not False
        or policy.get("offer_activation_enabled") is not False
    ):
        raise PersistentMappingError("repository persistent mapping policy differs")


@dataclass(frozen=True)
class Principal:
    principal_ref: str
    role_ref: str
    authn_context_sha256: str

    def validate(self) -> None:
        if not self.principal_ref.strip() or not self.role_ref.strip():
            raise PersistentMappingError("named principal and role are required")
        if len(self.authn_context_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.authn_context_sha256
        ):
            raise PersistentMappingError("authentication context hash is malformed")


def authentication_context_sha256(*, principal_ref: str, role_ref: str, session_ref: str) -> str:
    """Bind a server-created session to its fixed named principal and role."""

    payload = json.dumps(
        {
            "contract": "BUFFALO_LOCAL_NAMED_SESSION_V1",
            "principal_ref": principal_ref,
            "role_ref": role_ref,
            "session_ref": session_ref,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


_SYNTHETIC_DATABASE_OPTIONS = (
    "-c role=qa_mapping_owner -c search_path=qa_mapping_test,pg_catalog"
)


def _validate_synthetic_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise PersistentMappingError("synthetic mapping database port is malformed") from exc
    database = parsed.path.removeprefix("/")
    try:
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=1,
        )
    except ValueError as exc:
        raise PersistentMappingError(
            "synthetic mapping database query is malformed"
        ) from exc
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or parsed.username != "qa_release_login"
        or not database.endswith(("_test", "_demo"))
        or not database.replace("_", "").isalnum()
        or "/" in database
        or parsed.password is not None
        or parsed.fragment
        or parsed.params
        or query != [("options", _SYNTHETIC_DATABASE_OPTIONS)]
    ):
        raise PersistentMappingError(
            "persistent mapping writes require an owned loopback *_test/*_demo database"
        )
    return database


def _verify_connected_synthetic_database(conn: Any, expected_database: str) -> None:
    row = conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.inet_server_addr()::text,"
        "pg_catalog.current_setting('server_version_num')::integer,"
        "session_user::text,current_user::text"
    ).fetchone()
    if row is None:
        raise PersistentMappingError("synthetic mapping database identity is absent")
    try:
        from ipaddress import ip_interface

        loopback = ip_interface(str(row[1])).ip.is_loopback
    except ValueError:
        loopback = False
    if (
        str(row[0]) != expected_database
        or not loopback
        or int(row[2]) // 10000 != 16
        or (str(row[3]), str(row[4]))
        != ("qa_release_login", "qa_mapping_owner")
    ):
        raise PersistentMappingError(
            "connected synthetic mapping database identity differs"
        )


def _try_session_lock(conn: Any, lock_name: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        locked = conn.execute(
            "SELECT pg_catalog.pg_try_advisory_lock("
            "pg_catalog.hashtextextended(%s,0))",
            (lock_name,),
        ).fetchone()[0]
        if locked:
            return
        time.sleep(0.01)
    raise PersistentMappingError(
        "persistent mapping session lock timed out", code="SESSION_LOCK_TIMEOUT"
    )


def _unlock_session_locks(conn: Any, lock_names: Sequence[str]) -> None:
    for lock_name in reversed(lock_names):
        unlocked = conn.execute(
            "SELECT pg_catalog.pg_advisory_unlock("
            "pg_catalog.hashtextextended(%s,0))",
            (lock_name,),
        ).fetchone()[0]
        if not unlocked:
            raise PersistentMappingError(
                "persistent mapping session lock ownership was lost",
                code="SESSION_LOCK_CLEANUP_FAILED",
            )


def _execute_write(
    database_url: str,
    *,
    operation_name: str,
    capability: str,
    principal: Principal,
    idempotency_key: UUID,
    domain_locks: Callable[[Any], Sequence[tuple[int, str]]],
    operation: Callable[[Any], _T],
    recovery_lookup: Callable[[Any], _T | None] | None = None,
) -> _T:
    """Execute one authenticated write behind balanced session locks.

    Every retry begins a genuinely fresh SERIALIZABLE snapshot while the
    payload-neutral contention keys remain held.  A lost COMMIT response closes
    the uncertain backend and reacquires the same locks before attempting the
    idempotent operation again; it is never reported as a rollback.
    """

    require_synthetic_mapping_capability(capability)
    principal.validate()
    expected_database = _validate_synthetic_database_url(database_url)
    started = time.monotonic()
    deadline = started + TOTAL_OPERATION_SECONDS
    frozen_domain_locks: tuple[str, ...] | None = None
    transaction_attempts = 0
    last_error: BaseException | None = None
    recovery_mode: str | None = None
    conn: Any | None = None
    acquired: list[str] = []

    def discard_connection() -> None:
        nonlocal conn, acquired
        if conn is not None:
            conn.close()
        conn = None
        acquired = []

    try:
        while transaction_attempts < MAX_TRANSACTION_ATTEMPTS:
            if time.monotonic() >= deadline:
                code = (
                    "COMMIT_OUTCOME_UNKNOWN"
                    if recovery_mode == "unknown_commit"
                    else (
                        "IDEMPOTENCY_PROTOCOL_VIOLATION"
                        if recovery_mode == "unique_violation"
                        else "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED"
                    )
                )
                raise PersistentMappingError(
                    "persistent mapping operation deadline expired", code=code
                ) from last_error
            if conn is None:
                # Recovery is a new authenticated operation boundary; never let
                # possession of an idempotency UUID disclose or recreate state.
                require_synthetic_mapping_capability(capability)
                principal.validate()
                try:
                    connect_timeout = max(1, min(5, int(deadline - time.monotonic())))
                    conn = psycopg.connect(
                        database_url, autocommit=True, connect_timeout=connect_timeout
                    )
                    _verify_connected_synthetic_database(conn, expected_database)
                    if frozen_domain_locks is None:
                        resolved = sorted(
                            domain_locks(conn),
                            key=lambda item: (item[0], item[1].encode("utf-8")),
                        )
                        if len({item[1] for item in resolved}) != len(resolved):
                            raise PersistentMappingError("mapping domain locks are duplicated")
                        frozen_domain_locks = tuple(item[1] for item in resolved)
                    all_locks = (
                        f"persistent-mapping:{operation_name}:idempotency:{idempotency_key}",
                        *frozen_domain_locks,
                    )
                    lock_deadline = min(
                        deadline, time.monotonic() + SESSION_LOCK_WAIT_SECONDS
                    )
                    for lock_name in all_locks:
                        _try_session_lock(conn, lock_name, lock_deadline)
                        acquired.append(lock_name)
                except BaseException as exc:
                    discard_connection()
                    if recovery_mode == "unknown_commit":
                        raise PersistentMappingError(
                            "persistent mapping commit outcome is unknown; resubmit the same idempotency key",
                            code="COMMIT_OUTCOME_UNKNOWN",
                        ) from exc
                    if recovery_mode == "unique_violation":
                        raise PersistentMappingError(
                            "persistent mapping idempotency recovery was unavailable",
                            code="IDEMPOTENCY_PROTOCOL_VIOLATION",
                        ) from exc
                    raise

            transaction_attempts += 1
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise PersistentMappingError(
                    "persistent mapping operation deadline expired",
                    code=(
                        "COMMIT_OUTCOME_UNKNOWN"
                        if recovery_mode == "unknown_commit"
                        else (
                            "IDEMPOTENCY_PROTOCOL_VIOLATION"
                            if recovery_mode == "unique_violation"
                            else "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED"
                        )
                    ),
                )
            try:
                conn.execute("BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                conn.execute(
                    sql.SQL("SET LOCAL search_path = pg_catalog, {}, pg_temp").format(
                        sql.Identifier(SCHEMA)
                    )
                )
                conn.execute(
                    "SELECT pg_catalog.set_config('statement_timeout',%s,true)",
                    (str(remaining_ms),),
                )
                conn.execute(
                    "SELECT pg_catalog.set_config('lock_timeout',%s,true)",
                    (str(min(5000, remaining_ms)),),
                )
                if recovery_mode is not None:
                    active_recovery = recovery_mode
                    if recovery_lookup is None:
                        conn.rollback()
                        discard_connection()
                        raise PersistentMappingError(
                            "persistent mapping idempotency recovery is unavailable",
                            code=(
                                "COMMIT_OUTCOME_UNKNOWN"
                                if active_recovery == "unknown_commit"
                                else "IDEMPOTENCY_PROTOCOL_VIOLATION"
                            ),
                        )
                    recovered = recovery_lookup(conn)
                    if recovered is not None:
                        conn.rollback()
                        return recovered
                    if active_recovery == "unique_violation":
                        conn.rollback()
                        raise PersistentMappingError(
                            "named idempotency constraint has no recoverable row",
                            code="IDEMPOTENCY_PROTOCOL_VIOLATION",
                        )
                    else:
                        # The prior backend is gone and these exact session
                        # locks were reacquired.  An absent idempotency row now
                        # proves the prior transaction did not commit, so one
                        # ordinary attempt is safe.
                        recovery_mode = None
                        result = operation(conn)
                else:
                    result = operation(conn)
                if time.monotonic() >= deadline:
                    conn.rollback()
                    raise PersistentMappingError(
                        "persistent mapping operation deadline expired",
                        code=(
                            "COMMIT_OUTCOME_UNKNOWN"
                            if recovery_mode == "unknown_commit"
                            else "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED"
                        ),
                    )
            except PersistentMappingError as exc:
                if conn is not None and not conn.closed:
                    conn.rollback()
                if (
                    recovery_mode == "unknown_commit"
                    and exc.code not in {"IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"}
                ):
                    discard_connection()
                    raise PersistentMappingError(
                        "persistent mapping commit outcome is unknown; resubmit the same idempotency key",
                        code="COMMIT_OUTCOME_UNKNOWN",
                    ) from exc
                raise
            except psycopg.Error as exc:
                last_error = exc
                if conn is not None and not conn.closed:
                    conn.rollback()
                if recovery_mode == "unknown_commit":
                    discard_connection()
                    raise PersistentMappingError(
                        "persistent mapping commit outcome is unknown; resubmit the same idempotency key",
                        code="COMMIT_OUTCOME_UNKNOWN",
                    ) from exc
                if recovery_mode == "unique_violation":
                    discard_connection()
                    raise PersistentMappingError(
                        "persistent mapping idempotency recovery failed",
                        code="IDEMPOTENCY_PROTOCOL_VIOLATION",
                    ) from exc
                if (
                    exc.sqlstate in _RETRYABLE_SQLSTATES
                    and transaction_attempts < MAX_TRANSACTION_ATTEMPTS
                ):
                    continue
                constraint_name = getattr(getattr(exc, "diag", None), "constraint_name", None)
                if (
                    exc.sqlstate == "23505"
                    and constraint_name in _IDEMPOTENCY_CONSTRAINTS
                ):
                    recovery_mode = "unique_violation"
                    discard_connection()
                    transaction_attempts -= 1
                    continue
                raise PersistentMappingError(
                    "persistent mapping database operation refused",
                    code=(
                        "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED"
                        if exc.sqlstate in _RETRYABLE_SQLSTATES
                        else "DATABASE_VALIDATION_REFUSED"
                    ),
                ) from exc
            except BaseException:
                if conn is not None and not conn.closed:
                    conn.rollback()
                raise

            try:
                conn.commit()
            except psycopg.Error as exc:
                last_error = exc
                if exc.sqlstate in _RETRYABLE_SQLSTATES:
                    if conn is not None and not conn.closed:
                        conn.rollback()
                    if transaction_attempts < MAX_TRANSACTION_ATTEMPTS:
                        continue
                    raise PersistentMappingError(
                        "persistent mapping retry budget exhausted",
                        code="CONCURRENT_TRANSACTION_RETRY_EXHAUSTED",
                    ) from exc
                constraint_name = getattr(
                    getattr(exc, "diag", None), "constraint_name", None
                )
                if (
                    exc.sqlstate == "23505"
                    and constraint_name in _IDEMPOTENCY_CONSTRAINTS
                ):
                    if conn is not None and not conn.closed:
                        conn.rollback()
                    recovery_mode = "unique_violation"
                    discard_connection()
                    transaction_attempts -= 1
                    continue
                if not isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
                    if conn is not None and not conn.closed:
                        conn.rollback()
                    raise PersistentMappingError(
                        "persistent mapping database operation refused",
                        code="DATABASE_VALIDATION_REFUSED",
                    ) from exc
                # The original backend is discarded, which releases its locks.
                # The next attempt must reauthenticate and reacquire the same
                # payload-neutral locks before the operation's idempotency-first
                # lookup can establish whether the commit landed.
                recovery_mode = "unknown_commit"
                discard_connection()
                if transaction_attempts >= MAX_TRANSACTION_ATTEMPTS:
                    raise PersistentMappingError(
                        "persistent mapping commit outcome is unknown; resubmit the same idempotency key",
                        code="COMMIT_OUTCOME_UNKNOWN",
                    ) from exc
                continue
            except BaseException:
                # Cancellation and process-level exceptions are not evidence of
                # an ambiguous database commit and are never replayed.
                raise
            return result
        raise PersistentMappingError(
            "persistent mapping retry budget exhausted",
            code=(
                "COMMIT_OUTCOME_UNKNOWN"
                if recovery_mode == "unknown_commit"
                else (
                    "IDEMPOTENCY_PROTOCOL_VIOLATION"
                    if recovery_mode == "unique_violation"
                    else "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED"
                )
            ),
        ) from last_error
    finally:
        if conn is not None:
            cleanup_error: BaseException | None = None
            try:
                if conn.info.transaction_status.name != "IDLE":
                    conn.rollback()
                _unlock_session_locks(conn, acquired)
            except BaseException as exc:
                cleanup_error = exc
            finally:
                conn.close()
            if cleanup_error is not None:
                raise PersistentMappingError(
                    "persistent mapping session-lock cleanup failed",
                    code="SESSION_LOCK_CLEANUP_FAILED",
                ) from cleanup_error


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (UUID, date, datetime, Decimal)):
        return str(value)
    return value


def _set_human_context(conn: Any, principal: Principal, *, action: str, capability: str) -> None:
    principal.validate()
    for key, value in (
        ("procurement.principal_kind", "HUMAN"),
        ("procurement.principal_ref", principal.principal_ref),
        ("procurement.authorized_role_ref", principal.role_ref),
        ("procurement.authn_context_sha256", principal.authn_context_sha256),
        ("procurement.authorized_action", action),
        ("procurement.enabled_capability", capability),
    ):
        conn.execute("SELECT pg_catalog.set_config(%s,%s,true)", (key, value))


def _qualified(name: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(name))


def _json_hash(conn: Any, value: Mapping[str, Any] | Sequence[Any]) -> str:
    return str(
        conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_json_sha256(%s::jsonb)").format(
                sql.Identifier(SCHEMA)
            ),
            (Jsonb(_jsonable(value)),),
        ).fetchone()[0]
    )


def _text_hash(conn: Any, value: str) -> str:
    return str(
        conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_text_sha256(%s)").format(
                sql.Identifier(SCHEMA)
            ),
            (value,),
        ).fetchone()[0]
    )


def _project_record(
    conn: Any, *, table: str, record: Mapping[str, Any], omit: Sequence[str]
) -> tuple[dict[str, Any], str]:
    row = conn.execute(
        sql.SQL(
            "SELECT payload,{}.persistent_mapping_json_sha256(payload) FROM ("
            "SELECT pg_catalog.to_jsonb(value)-%s::text[] AS payload "
            "FROM pg_catalog.jsonb_populate_record(NULL::{},%s::jsonb) AS value"
            ") AS projected"
        ).format(sql.Identifier(SCHEMA), _qualified(table)),
        (list(omit), Jsonb(_jsonable(record))),
    ).fetchone()
    if (
        row is None
        or not isinstance(row[0], dict)
        or not isinstance(row[1], str)
        or len(row[1]) != 64
    ):
        raise PersistentMappingError(f"could not canonicalize {table}")
    return row[0], row[1]


def _composite_value(
    conn: Any, *, table: str, function: str, record: Mapping[str, Any]
) -> Any:
    return conn.execute(
        sql.SQL(
            "SELECT {}.{}(value) FROM "
            "pg_catalog.jsonb_populate_record(NULL::{},%s::jsonb) AS value"
        ).format(
            sql.Identifier(SCHEMA),
            sql.Identifier(function),
            _qualified(table),
        ),
        (Jsonb(_jsonable(record)),),
    ).fetchone()[0]


def _insert_record(conn: Any, *, table: str, record: Mapping[str, Any]) -> None:
    table_contracts = {
        "supplier_mapping_review_batches": (
            "created_at",
            "created_txid",
            "payload_sha256",
            ("review_batch_id", "intake_idempotency_key", "canonical_payload", "payload_sha256", "created_at", "created_txid"),
        ),
        "supplier_mapping_review_candidates": (
            "created_at",
            "created_txid",
            "candidate_sha256",
            ("candidate_id", "canonical_payload", "candidate_sha256", "created_at", "created_txid"),
        ),
        "supplier_mapping_decisions": (
            "decided_at",
            "decided_txid",
            "payload_sha256",
            ("mapping_decision_id", "decision_idempotency_key", "canonical_payload", "payload_sha256", "decided_at", "decided_txid"),
        ),
        "supplier_offer_selection_events": (
            "selected_at",
            "selected_txid",
            "payload_sha256",
            ("selection_event_id", "selection_idempotency_key", "canonical_payload", "payload_sha256", "selected_at", "selected_txid"),
        ),
    }
    if table not in table_contracts:
        raise PersistentMappingError("persistent mapping insert table is unsupported")
    timestamp_column, txid_column, hash_column, omit = table_contracts[table]
    completed = dict(record)
    completed[timestamp_column], completed[txid_column] = conn.execute(
        "SELECT pg_catalog.clock_timestamp(),pg_catalog.txid_current()"
    ).fetchone()
    # Populate the same PostgreSQL composite type used by the canonical-payload
    # projection.  Sending individual Python values through heterogeneous type
    # adapters can produce a different JSONB representation (notably for fixed
    # scale NUMERIC fields) than jsonb_populate_record(), which correctly makes
    # the database type the single canonicalization authority.
    result = conn.execute(
        sql.SQL(
            "WITH populated AS ("
            " SELECT value FROM pg_catalog.jsonb_populate_record(NULL::{},%s::jsonb) AS value"
            "), canonical AS ("
            " SELECT value,pg_catalog.to_jsonb(value)-%s::text[] AS payload FROM populated"
            "), finalized AS ("
            " SELECT pg_catalog.jsonb_populate_record(NULL::{},"
            "   pg_catalog.to_jsonb(value)||pg_catalog.jsonb_build_object("
            "     'canonical_payload',payload,%s::text,{}.persistent_mapping_json_sha256(payload)"
            "   )"
            " ) AS value FROM canonical"
            " WHERE (value).canonical_payload IS NOT DISTINCT FROM payload"
            "   AND pg_catalog.to_jsonb(value)->>%s::text={}.persistent_mapping_json_sha256(payload)"
            ") INSERT INTO {} SELECT (value).* FROM finalized"
        ).format(
            _qualified(table),
            _qualified(table),
            sql.Identifier(SCHEMA),
            sql.Identifier(SCHEMA),
            _qualified(table),
        ),
        (
            Jsonb(_jsonable(completed)),
            list(omit),
            hash_column,
            hash_column,
        ),
    )
    if result.rowcount != 1:
        raise PersistentMappingError(f"{table} canonical payload or fingerprint differs")


def _candidate_base(
    *, review_batch_id: UUID, candidate_id: UUID, occurrence_index: int, value: Mapping[str, Any]
) -> dict[str, Any]:
    def state(name: str) -> tuple[str, Any]:
        raw_state = str(value.get(f"{name}_state", "ABSENT")).upper()
        raw_value = value.get(f"{name}_value")
        if raw_state not in {"ABSENT", "EXPLICIT_NULL", "VALUE"}:
            raise PersistentMappingError(f"{name} state is unsupported")
        if (raw_state == "VALUE") != (raw_value is not None):
            raise PersistentMappingError(f"{name} state/value pair differs")
        return raw_state, raw_value

    fields = {
        name: state(name)
        for name in (
            "distributor_product_id", "supplier_code", "package_type", "size",
            "raw_pack", "physical_units", "retail_pack_units", "shopify_units",
            "qualifying_units", "assortment_scope", "assortment_group", "assortable",
        )
    }
    source_file_sha256 = str(value.get("source_file_sha256", ""))
    if len(source_file_sha256) != 64:
        raise PersistentMappingError("candidate source-file hash is malformed")
    record: dict[str, Any] = {
        "review_batch_id": str(review_batch_id),
        "candidate_id": str(candidate_id),
        "occurrence_index": occurrence_index,
        "occurrence_key": str(value["occurrence_key"]),
        "printed_occurrence_sha256": str(value["printed_occurrence_sha256"]),
        "supplier_identity_key_sha256": None,
        "operational_offer_key_sha256": None,
        "decision_scope_sha256": "0" * 64,
        "source_table_name": str(value.get("source_table_name", "supplier_offers_v5")),
        "source_row_key": str(value.get("source_row_key", value["occurrence_key"])),
        "source_file_name": str(value.get("source_file_name", "synthetic-review.jsonl")),
        "source_file_sha256": source_file_sha256,
        "source_page_start": value.get("source_page_start"),
        "source_page_end": value.get("source_page_end"),
        "source_locator": dict(value.get("source_locator", {})),
        "proposed_variant_id": value.get("proposed_variant_id"),
        "proposed_vendor_id": value.get("proposed_vendor_id"),
        "source_vendor_identity": str(value["source_vendor_identity"]),
        "offer_class": str(value.get("offer_class", "UNKNOWN")).upper(),
        "occurrence_role": str(value.get("occurrence_role", "UNKNOWN")).upper(),
        "identity_qualifiers": dict(value.get("identity_qualifiers", {})),
        "component_relationships": list(value.get("component_relationships", [])),
        "independent_linkage_evidence": list(value.get("independent_linkage_evidence", [])),
        "owner_clarifications": list(value.get("owner_clarifications", [])),
        "historical_capture_scope": dict(value.get("historical_capture_scope", {})),
        "related_artifact_hashes": dict(value.get("related_artifact_hashes", {})),
        "blockers": list(value.get("blockers", [])),
        "canonical_payload": {},
        "candidate_sha256": "0" * 64,
    }
    for name, (raw_state, raw_value) in fields.items():
        record[f"{name}_state"] = raw_state
        record[f"{name}_value"] = raw_value
    return record


def _canonical_source_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_supported_intake(
    *, package: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> None:
    """Reprove the one supported sealed synthetic packet contract.

    Private real-source adapters remain read-only and are deliberately not
    accepted by this write service.  The fixed synthetic packet loader verifies
    its source bytes; this second boundary independently checks the derived seal,
    batch, relationship, payload, page, and candidate/source bindings before a
    connection can be opened.
    """

    from .synthetic_mapping_packet import PACKET_CONTRACT, PACKET_SHA256

    required_hashes = (
        "source_artifact_sha256",
        "source_root_sha256",
        "source_seal_sha256",
        "relationship_table_sha256",
        "source_batch_sha256",
        "source_payload_sha256",
    )
    if any(
        not isinstance(package.get(name), str)
        or len(str(package[name])) != 64
        or any(character not in "0123456789abcdef" for character in str(package[name]))
        for name in required_hashes
    ):
        raise PersistentMappingError("review package hash contract is malformed")
    prerequisites = package.get("prerequisites")
    if prerequisites != {
        "packet_contract": PACKET_CONTRACT,
        "packet_sha256": PACKET_SHA256,
    }:
        raise PersistentMappingError("review package prerequisite proof differs")
    if (
        package.get("structural_state") != "READY"
        or package.get("source_evidence_state") != "READY"
        or package.get("semantic_state") != "READY"
        or not candidates
    ):
        raise PersistentMappingError("review package readiness or candidate set differs")
    artifact_sha = str(package["source_artifact_sha256"])
    occurrences: list[str] = []
    relationships: list[Any] = []
    for candidate in candidates:
        occurrence = str(candidate.get("occurrence_key", "")).strip()
        start = candidate.get("source_page_start")
        end = candidate.get("source_page_end")
        if (
            not occurrence
            or candidate.get("source_file_sha256") != artifact_sha
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            raise PersistentMappingError("review candidate source binding differs")
        occurrences.append(occurrence)
        component_rows = candidate.get("component_relationships", [])
        if not isinstance(component_rows, list):
            raise PersistentMappingError("review candidate relationships are malformed")
        relationships.extend(component_rows)
    if len(set(occurrences)) != len(occurrences):
        raise PersistentMappingError("review candidate occurrence keys are duplicated")
    payload_sha = _canonical_source_sha256(list(candidates))
    root_sha = _canonical_source_sha256(
        {
            "source_artifact_sha256": artifact_sha,
            "source_payload_sha256": payload_sha,
        }
    )
    relationship_sha = _canonical_source_sha256(relationships)
    batch_sha = _canonical_source_sha256(
        {
            "source_package_id": package.get("source_package_id"),
            "source_revision": package.get("source_revision"),
            "occurrence_keys": occurrences,
        }
    )
    seal_sha = _canonical_source_sha256(
        {
            "source_root_sha256": root_sha,
            "relationship_table_sha256": relationship_sha,
            "source_batch_sha256": batch_sha,
        }
    )
    if (
        package["source_payload_sha256"] != payload_sha
        or package["source_root_sha256"] != root_sha
        or package["relationship_table_sha256"] != relationship_sha
        or package["source_batch_sha256"] != batch_sha
        or package["source_seal_sha256"] != seal_sha
    ):
        raise PersistentMappingError("review package seal or member fingerprint differs")


def intake_supplier_mapping_review(
    conn: Any,
    *,
    package: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    principal: Principal,
    intake_idempotency_key: UUID,
) -> dict[str, Any]:
    """Persist one complete immutable review batch and candidate set."""

    _validate_supported_intake(package=package, candidates=candidates)
    require_synthetic_mapping_capability("review_intake_writes_enabled")
    _set_human_context(
        conn,
        principal,
        action="MAPPING_REVIEW_INTAKE",
        capability="review_intake_writes_enabled",
    )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
        (f"supplier-review-intake:{intake_idempotency_key}",),
    )
    batch_id = uuid5(NAMESPACE_URL, f"buffalo-review-batch:{intake_idempotency_key}")
    candidate_records: list[dict[str, Any]] = []
    seen_occurrences: set[str] = set()
    for index, candidate in enumerate(candidates, 1):
        occurrence = str(candidate.get("occurrence_key", "")).strip()
        if not occurrence or occurrence in seen_occurrences:
            raise PersistentMappingError("candidate occurrence keys must be nonblank and unique")
        seen_occurrences.add(occurrence)
        candidate_id = uuid5(batch_id, occurrence)
        record = _candidate_base(
            review_batch_id=batch_id,
            candidate_id=candidate_id,
            occurrence_index=index,
            value=candidate,
        )
        for field, function in (
            ("supplier_identity_key_sha256", "persistent_mapping_candidate_supplier_identity_key"),
            ("operational_offer_key_sha256", "persistent_mapping_candidate_operational_offer_key"),
            ("decision_scope_sha256", "persistent_mapping_candidate_decision_scope"),
        ):
            record[field] = _composite_value(
                conn,
                table="supplier_mapping_review_candidates",
                function=function,
                record=record,
            )
        payload, payload_sha256 = _project_record(
            conn,
            table="supplier_mapping_review_candidates",
            record=record,
            omit=("candidate_id", "canonical_payload", "candidate_sha256", "created_at", "created_txid"),
        )
        record["canonical_payload"] = payload
        record["candidate_sha256"] = payload_sha256
        candidate_records.append(record)

    candidate_set_sha256 = _text_hash(
        conn, "".join(f"{record['candidate_sha256']}\n" for record in candidate_records)
    )
    batch: dict[str, Any] = {
        "review_batch_id": str(batch_id),
        "intake_idempotency_key": str(intake_idempotency_key),
        "source_package_kind": "SEALED_V5_REVIEW_PACKAGE",
        "source_package_id": str(package["source_package_id"]),
        "source_revision": str(package["source_revision"]),
        "source_artifact_ref": str(package["source_artifact_ref"]),
        "source_artifact_sha256": str(package["source_artifact_sha256"]),
        "source_root_sha256": str(package["source_root_sha256"]),
        "source_seal_sha256": str(package["source_seal_sha256"]),
        "relationship_table_sha256": str(package["relationship_table_sha256"]),
        "source_batch_sha256": str(package["source_batch_sha256"]),
        "source_payload_sha256": str(package["source_payload_sha256"]),
        "candidate_set_sha256": candidate_set_sha256,
        "candidate_count": len(candidate_records),
        "supplier_period_scope": dict(package.get("supplier_period_scope", {})),
        "prerequisites": dict(package.get("prerequisites", {})),
        "structural_state": str(package.get("structural_state", "READY")),
        "source_evidence_state": str(package.get("source_evidence_state", "READY")),
        "semantic_state": str(package.get("semantic_state", "READY")),
        "source_authority_state": "NOT_APPROVED",
        "source_import_state": "NOT_IMPORT_READY",
        "source_is_simulation": bool(package.get("source_is_simulation", True)),
        "creator_principal_ref": principal.principal_ref,
        "creator_role_ref": principal.role_ref,
        "creator_authn_context_sha256": principal.authn_context_sha256,
        "canonical_payload": {},
        "payload_sha256": "0" * 64,
    }
    batch_payload, batch_payload_sha256 = _project_record(
        conn,
        table="supplier_mapping_review_batches",
        record=batch,
        omit=("review_batch_id", "intake_idempotency_key", "canonical_payload", "payload_sha256", "created_at", "created_txid"),
    )
    batch["canonical_payload"] = batch_payload
    batch["payload_sha256"] = batch_payload_sha256

    with conn.cursor(row_factory=dict_row) as cursor:
        existing = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE intake_idempotency_key=%s").format(
                _qualified("supplier_mapping_review_batches")
            ),
            (intake_idempotency_key,),
        ).fetchone()
    if existing is not None:
        if existing["payload_sha256"] != batch["payload_sha256"]:
            raise PersistentMappingError(
                "intake idempotency key was used for different payload",
                code="IDEMPOTENCY_CONFLICT",
            )
        return {
            "review_batch_id": str(existing["review_batch_id"]),
            "candidate_ids": [
                str(row[0])
                for row in conn.execute(
                    sql.SQL("SELECT candidate_id FROM {} WHERE review_batch_id=%s ORDER BY occurrence_index").format(
                        _qualified("supplier_mapping_review_candidates")
                    ),
                    (existing["review_batch_id"],),
                ).fetchall()
            ],
            "replayed": True,
        }

    _insert_record(conn, table="supplier_mapping_review_batches", record=batch)
    for record in candidate_records:
        _insert_record(conn, table="supplier_mapping_review_candidates", record=record)
    return {
        "review_batch_id": str(batch_id),
        "candidate_ids": [str(record["candidate_id"]) for record in candidate_records],
        "candidate_set_sha256": candidate_set_sha256,
        "replayed": False,
    }


def list_mapping_candidates(
    conn: Any,
    *,
    query: str = "",
    supplier: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    if limit < 1 or limit > 100 or offset < 0:
        raise PersistentMappingError("candidate page bounds are invalid")
    predicates = ["1=1"]
    parameters: list[Any] = []
    if query.strip():
        predicates.append(
            "(c.occurrence_key ILIKE %s OR c.source_vendor_identity ILIKE %s "
            "OR COALESCE(c.supplier_code_value,'') ILIKE %s "
            "OR COALESCE(c.proposed_variant_id,'') ILIKE %s)"
        )
        token = f"%{query.strip()}%"
        parameters.extend((token, token, token, token))
    if supplier.strip():
        predicates.append("c.source_vendor_identity=%s")
        parameters.append(supplier.strip())
    if status == "PENDING":
        predicates.append("d.mapping_decision_id IS NULL")
    elif status == "DECIDED":
        predicates.append("d.mapping_decision_id IS NOT NULL")
    elif status:
        predicates.append("d.action=%s")
        parameters.append(status)
    where = " AND ".join(predicates)
    base = sql.SQL(
        " FROM {} c JOIN {} b USING(review_batch_id) "
        "LEFT JOIN {} d ON d.candidate_id=c.candidate_id WHERE " + where
    ).format(
        _qualified("supplier_mapping_review_candidates"),
        _qualified("supplier_mapping_review_batches"),
        _qualified("v_effective_supplier_mapping_decisions"),
    )
    total = conn.execute(sql.SQL("SELECT count(*)") + base, parameters).fetchone()[0]
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            sql.SQL(
                "SELECT c.*,b.source_package_id,b.source_revision,b.structural_state,"
                "b.source_evidence_state,b.semantic_state,b.source_authority_state,"
                "b.source_import_state,d.mapping_decision_id,d.action AS decision_action,"
                "d.result_offer_id,d.decided_at"
            )
            + base
            + sql.SQL(" ORDER BY c.source_vendor_identity,c.occurrence_index,c.candidate_id LIMIT %s OFFSET %s"),
            [*parameters, limit, offset],
        ).fetchall()
    return {"total": int(total), "limit": limit, "offset": offset, "items": rows}


def _candidate_decision_facts(conn: Any, candidate_id: UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cursor:
        candidate = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE candidate_id=%s").format(
                _qualified("supplier_mapping_review_candidates")
            ),
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise PersistentMappingError("mapping candidate does not exist")
        batch = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE review_batch_id=%s").format(
                _qualified("supplier_mapping_review_batches")
            ),
            (candidate["review_batch_id"],),
        ).fetchone()
    assert batch is not None
    return candidate, batch


_OFFER_PACKAGE_TYPES = {
    "REGULAR": "STANDARD",
    "GIFT": "GIFT",
    "SPECIAL": "SPECIAL",
    "ALTERNATE": "ALTERNATE",
    "COMPONENT": "COMPONENT",
    "COMBO": "COMBO",
}


def _prospective_offer_contract_sha256(
    conn: Any, *, candidate: Mapping[str, Any], package_type: str
) -> str:
    """Hash the exact PostgreSQL values a CREATED_INACTIVE row will contain."""

    value = conn.execute(
        sql.SQL(
            "SELECT {}.persistent_mapping_json_sha256(pg_catalog.jsonb_build_object("
            "'contract_version','SUPPLIER_OFFER_CONTRACT_V1',"
            "'variant_id',%s::text,'vendor_id',%s::uuid,"
            "'supplier_sku',%s::text,'package_type',%s::text,"
            "'size_text',%s::text,'raw_pack',%s::text,"
            "'shopify_units_per_case',%s::numeric(12,4),"
            "'qualifying_units_per_case',%s::numeric(12,4),"
            "'assortment_scope',%s::text,'assortment_group',%s::text,"
            "'assortable',%s::boolean,'valid_from',NULL::date,"
            "'valid_to',NULL::date,'replaces_offer_id',NULL::bigint,"
            "'source_file',%s::text,'source_page',%s::integer,"
            "'confidence','VERIFIED','active',false))"
        ).format(sql.Identifier(SCHEMA)),
        (
            candidate["proposed_variant_id"],
            candidate["proposed_vendor_id"],
            candidate["supplier_code_value"]
            if candidate["supplier_code_state"] == "VALUE"
            else None,
            package_type,
            candidate["size_value"],
            candidate["raw_pack_value"],
            candidate["shopify_units_value"],
            candidate["qualifying_units_value"],
            candidate["assortment_scope_value"],
            candidate["assortment_group_value"],
            candidate["assortable_value"],
            candidate["source_file_name"],
            candidate["source_page_start"],
        ),
    ).fetchone()[0]
    if not isinstance(value, str) or len(value) != 64:
        raise PersistentMappingError("created offer contract could not be established")
    return value


def _rejection_evidence(
    candidate: Mapping[str, Any], *, evidence_set_sha256: str
) -> dict[str, Any]:
    return {
        "contract": "PERSISTENT_MAPPING_REJECTION_EVIDENCE_V1",
        "review_batch_id": str(candidate["review_batch_id"]),
        "candidate_id": str(candidate["candidate_id"]),
        "candidate_sha256": candidate["candidate_sha256"],
        "printed_occurrence_sha256": candidate["printed_occurrence_sha256"],
        "evidence_set_sha256": evidence_set_sha256,
    }


def _prospective_rejection_contract_sha256(
    conn: Any,
    *,
    candidate: Mapping[str, Any],
    principal: Principal,
    evidence_set_sha256: str,
) -> str:
    source_key = f"persistent-mapping:{candidate['supplier_identity_key_sha256']}"
    evidence = _rejection_evidence(candidate, evidence_set_sha256=evidence_set_sha256)
    value = conn.execute(
        sql.SQL(
            "SELECT {}.persistent_mapping_json_sha256(pg_catalog.jsonb_build_object("
            "'contract_version','PERSISTENT_MAPPING_REJECTION_V1',"
            "'mapping_type','SUPPLIER_OFFER','source_key',%s::text,"
            "'rejected_variant_id',%s::text,'vendor_id',%s::uuid,"
            "'source_text',%s::text,'evidence_json',%s::jsonb,"
            "'rejected_by',%s::text,'active',true))"
        ).format(sql.Identifier(SCHEMA)),
        (
            source_key,
            candidate["proposed_variant_id"],
            candidate["proposed_vendor_id"],
            candidate["distributor_product_id_value"],
            Jsonb(evidence),
            principal.principal_ref,
        ),
    ).fetchone()[0]
    if not isinstance(value, str) or len(value) != 64:
        raise PersistentMappingError("rejection contract could not be established")
    return value


def _seal_mapping_decision_record(conn: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(record)
    sealed["preview_sha256"] = _composite_value(
        conn,
        table="supplier_mapping_decisions",
        function="persistent_mapping_decision_preview_sha256",
        record=sealed,
    )
    sealed["confirmation_sha256"] = _composite_value(
        conn,
        table="supplier_mapping_decisions",
        function="persistent_mapping_decision_confirmation_sha256",
        record=sealed,
    )
    sealed["request_sha256"] = _composite_value(
        conn,
        table="supplier_mapping_decisions",
        function="persistent_mapping_decision_request_sha256",
        record=sealed,
    )
    payload, payload_sha256 = _project_record(
        conn,
        table="supplier_mapping_decisions",
        record=sealed,
        omit=(
            "mapping_decision_id",
            "decision_idempotency_key",
            "canonical_payload",
            "payload_sha256",
            "decided_at",
            "decided_txid",
        ),
    )
    sealed["canonical_payload"] = payload
    sealed["payload_sha256"] = payload_sha256
    return sealed


def _mapping_decision_record(
    conn: Any,
    *,
    candidate_id: UUID,
    action: str,
    reason: str,
    principal: Principal,
    decision_idempotency_key: UUID,
    existing_offer_id: int | None,
    offer_link_kind: str | None,
) -> dict[str, Any]:
    candidate, batch = _candidate_decision_facts(conn, candidate_id)
    action = action.strip().upper()
    if action not in {"DEFER", "APPROVE_MAPPING", "REJECT_MAPPING"}:
        raise PersistentMappingError("mapping disposition is unsupported")
    reviewed_facts = _composite_value(
        conn,
        table="supplier_mapping_review_candidates",
        function="persistent_mapping_candidate_reviewed_facts",
        record=candidate,
    )
    evidence_sha = _composite_value(
        conn,
        table="supplier_mapping_review_candidates",
        function="persistent_mapping_candidate_evidence_set_sha256",
        record=candidate,
    )
    with conn.cursor(row_factory=dict_row) as cursor:
        prior = cursor.execute(
            sql.SQL(
                "SELECT * FROM {} WHERE decision_scope_sha256=%s"
            ).format(_qualified("v_effective_supplier_mapping_decisions")),
            (candidate["decision_scope_sha256"],),
        ).fetchone()
    approval = action == "APPROVE_MAPPING"
    rejection = action == "REJECT_MAPPING"
    targeted = approval or rejection
    normalized_link_kind = (
        offer_link_kind.strip().upper() if isinstance(offer_link_kind, str) else None
    )
    if approval and normalized_link_kind not in {"CREATED_INACTIVE", "LINKED_EXISTING"}:
        raise PersistentMappingError("approval must fix its exact offer result disposition")
    if approval and normalized_link_kind == "LINKED_EXISTING" and existing_offer_id is None:
        raise PersistentMappingError("linked approval requires an exact existing offer")
    if approval and normalized_link_kind == "CREATED_INACTIVE" and existing_offer_id is not None:
        raise PersistentMappingError("created-inactive approval cannot name an existing offer")
    if not approval and (existing_offer_id is not None or normalized_link_kind is not None):
        raise PersistentMappingError("non-approval disposition cannot name an operational offer")
    offer_contract = None
    catalog_sha = vendor_sha = rejection_sha = None
    result_package_type = _OFFER_PACKAGE_TYPES.get(candidate["offer_class"]) if approval else None
    if targeted:
        catalog_sha = conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_catalog_fingerprint(%s)").format(
                sql.Identifier(SCHEMA)
            ),
            (candidate["proposed_variant_id"],),
        ).fetchone()[0]
        vendor_sha = conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_vendor_fingerprint(%s)").format(
                sql.Identifier(SCHEMA)
            ),
            (candidate["proposed_vendor_id"],),
        ).fetchone()[0]
        rejection_sha = conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_rejection_fingerprint(%s,%s,NULL)").format(
                sql.Identifier(SCHEMA)
            ),
            (
                candidate["proposed_vendor_id"],
                f"persistent-mapping:{candidate['supplier_identity_key_sha256']}",
            ),
        ).fetchone()[0]
        if None in (catalog_sha, vendor_sha, rejection_sha):
            raise PersistentMappingError("mapping target fingerprints are incomplete")
    if approval:
        if result_package_type is None:
            raise PersistentMappingError("approval package class is unsupported")
        historical = conn.execute(
            sql.SQL(
                "SELECT result_offer_id,result_offer_contract_sha256 FROM {} "
                "WHERE action='APPROVE_MAPPING' AND operational_offer_key_sha256=%s "
                "ORDER BY decided_at,mapping_decision_id LIMIT 1"
            ).format(_qualified("supplier_mapping_decisions")),
            (candidate["operational_offer_key_sha256"],),
        ).fetchone()
        if historical is not None:
            if normalized_link_kind == "CREATED_INACTIVE":
                raise PersistentMappingError(
                    "an equivalent offer now exists; generate a new linked-offer preview",
                    code="RECONFIRMATION_REQUIRED",
                )
            if int(historical[0]) != existing_offer_id:
                raise PersistentMappingError(
                    "historical equivalent occurrences require the same exact offer"
                )
        if normalized_link_kind == "LINKED_EXISTING":
            offer_contract = conn.execute(
                sql.SQL("SELECT {}.persistent_mapping_offer_fingerprint(%s)").format(
                    sql.Identifier(SCHEMA)
                ),
                (existing_offer_id,),
            ).fetchone()[0]
        else:
            offer_contract = _prospective_offer_contract_sha256(
                conn, candidate=candidate, package_type=result_package_type
            )
        if offer_contract is None:
            raise PersistentMappingError("approval offer contract is incomplete")
        if historical is not None and historical[1] != offer_contract:
            raise PersistentMappingError("historical operational-offer contract differs")
    rejection_contract = (
        _prospective_rejection_contract_sha256(
            conn,
            candidate=candidate,
            principal=principal,
            evidence_set_sha256=evidence_sha,
        )
        if rejection
        else None
    )
    record: dict[str, Any] = {
        "mapping_decision_id": str(uuid5(NAMESPACE_URL, f"buffalo-mapping-decision:{decision_idempotency_key}")),
        "decision_idempotency_key": str(decision_idempotency_key),
        "request_sha256": "0" * 64,
        "review_batch_id": str(candidate["review_batch_id"]),
        "candidate_id": str(candidate["candidate_id"]),
        "decision_scope_sha256": candidate["decision_scope_sha256"],
        "supplier_identity_key_sha256": candidate["supplier_identity_key_sha256"] if targeted else None,
        "operational_offer_key_sha256": candidate["operational_offer_key_sha256"] if targeted else None,
        "action": action,
        "decision_origin": "HUMAN",
        "authority_kind": "HUMAN_APPROVED" if approval else None,
        "variant_id": candidate["proposed_variant_id"] if targeted else None,
        "vendor_id": str(candidate["proposed_vendor_id"]) if targeted else None,
        "printed_occurrence_sha256": candidate["printed_occurrence_sha256"],
        "distributor_product_id_state": candidate["distributor_product_id_state"],
        "distributor_product_id_value": candidate["distributor_product_id_value"],
        "supplier_code_state": candidate["supplier_code_state"],
        "supplier_code_value": candidate["supplier_code_value"],
        "offer_class": candidate["offer_class"],
        "result_offer_package_type": result_package_type,
        "size_state": candidate["size_state"],
        "size_value": candidate["size_value"],
        "raw_pack_state": candidate["raw_pack_state"],
        "raw_pack_value": candidate["raw_pack_value"],
        "shopify_units_state": candidate["shopify_units_state"],
        "shopify_units_value": candidate["shopify_units_value"],
        "qualifying_units_state": candidate["qualifying_units_state"],
        "qualifying_units_value": candidate["qualifying_units_value"],
        "assortment_scope_state": candidate["assortment_scope_state"],
        "assortment_scope_value": candidate["assortment_scope_value"],
        "assortment_group_state": candidate["assortment_group_state"],
        "assortment_group_value": candidate["assortment_group_value"],
        "assortable_state": candidate["assortable_state"],
        "assortable_value": candidate["assortable_value"],
        "reviewed_facts": reviewed_facts,
        "component_relationships": candidate["component_relationships"],
        "owner_clarifications": candidate["owner_clarifications"],
        "historical_capture_scope": candidate["historical_capture_scope"],
        "expected_batch_payload_sha256": batch["payload_sha256"],
        "expected_candidate_sha256": candidate["candidate_sha256"],
        "expected_catalog_sha256": catalog_sha,
        "expected_vendor_sha256": vendor_sha,
        "expected_rejection_memory_sha256": rejection_sha,
        "evidence_set_sha256": evidence_sha,
        "human_principal_ref": principal.principal_ref,
        "human_role_ref": principal.role_ref,
        "human_authn_context_sha256": principal.authn_context_sha256,
        "preview_sha256": "0" * 64,
        "confirmation_sha256": "0" * 64,
        "service_principal_ref": None,
        "policy_ref": None,
        "policy_version": None,
        "policy_publication_sha256": None,
        "policy_predicate_version": None,
        "policy_predicate_result_sha256": None,
        "reason": reason.strip(),
        "result_offer_id": existing_offer_id,
        "offer_link_kind": normalized_link_kind,
        "result_offer_contract_sha256": offer_contract,
        "result_rejection_id": None,
        "result_rejection_contract_sha256": rejection_contract,
        "supersedes_mapping_decision_id": str(prior["mapping_decision_id"]) if prior else None,
        "canonical_payload": {},
        "payload_sha256": "0" * 64,
    }
    if not record["reason"]:
        raise PersistentMappingError("mapping decision reason is required")
    return _seal_mapping_decision_record(conn, record)


def _insert_created_inactive_offer(
    conn: Any, *, candidate: Mapping[str, Any], package_type: str
) -> int:
    row = conn.execute(
        sql.SQL(
            "INSERT INTO {}(variant_id,vendor_id,supplier_sku,package_type,"
            "size_text,raw_pack,shopify_units_per_case,qualifying_units_per_case,"
            "assortment_scope,assortment_group,assortable,valid_from,valid_to,"
            "replaces_offer_id,source_file,source_page,confidence,active) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,%s,%s,"
            "'VERIFIED',false) RETURNING offer_id"
        ).format(_qualified("supplier_offers")),
        (
            candidate["proposed_variant_id"],
            candidate["proposed_vendor_id"],
            candidate["supplier_code_value"]
            if candidate["supplier_code_state"] == "VALUE"
            else None,
            package_type,
            candidate["size_value"],
            candidate["raw_pack_value"],
            candidate["shopify_units_value"],
            candidate["qualifying_units_value"],
            candidate["assortment_scope_value"],
            candidate["assortment_group_value"],
            candidate["assortable_value"],
            candidate["source_file_name"],
            candidate["source_page_start"],
        ),
    ).fetchone()
    if row is None:
        raise PersistentMappingError("inactive supplier offer was not created")
    return int(row[0])


def _insert_mapping_rejection(
    conn: Any,
    *,
    candidate: Mapping[str, Any],
    principal: Principal,
    evidence_set_sha256: str,
) -> int:
    row = conn.execute(
        sql.SQL(
            "INSERT INTO {}(mapping_type,source_key,rejected_variant_id,vendor_id,"
            "source_text,evidence_json,rejected_by,active) "
            "VALUES('SUPPLIER_OFFER',%s,%s,%s,%s,%s::jsonb,%s,true) "
            "RETURNING rejection_id"
        ).format(_qualified("mapping_rejections")),
        (
            f"persistent-mapping:{candidate['supplier_identity_key_sha256']}",
            candidate["proposed_variant_id"],
            candidate["proposed_vendor_id"],
            candidate["distributor_product_id_value"],
            Jsonb(_rejection_evidence(candidate, evidence_set_sha256=evidence_set_sha256)),
            principal.principal_ref,
        ),
    ).fetchone()
    if row is None:
        raise PersistentMappingError("mapping rejection was not created")
    return int(row[0])


def preview_mapping_decision(
    conn: Any, *, candidate_id: UUID, action: str, reason: str, principal: Principal,
    decision_idempotency_key: UUID, existing_offer_id: int | None = None,
    offer_link_kind: str | None = None,
) -> dict[str, Any]:
    require_synthetic_mapping_capability("human_mapping_writes_enabled")
    principal.validate()
    return _mapping_decision_record(
        conn, candidate_id=candidate_id, action=action, reason=reason,
        principal=principal, decision_idempotency_key=decision_idempotency_key,
        existing_offer_id=existing_offer_id, offer_link_kind=offer_link_kind,
    )


def record_mapping_decision(
    conn: Any, *, candidate_id: UUID, action: str, reason: str, principal: Principal,
    decision_idempotency_key: UUID, expected_preview_sha256: str,
    existing_offer_id: int | None = None,
    offer_link_kind: str | None = None,
) -> dict[str, Any]:
    require_synthetic_mapping_capability("human_mapping_writes_enabled")
    _set_human_context(
        conn, principal, action="SUPPLIER_MAPPING_DECIDE",
        capability="human_mapping_writes_enabled",
    )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
        (f"supplier-mapping-decision-idempotency:{decision_idempotency_key}",),
    )
    with conn.cursor(row_factory=dict_row) as cursor:
        existing = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE decision_idempotency_key=%s").format(
                _qualified("supplier_mapping_decisions")
            ),
            (decision_idempotency_key,),
        ).fetchone()
    if existing is not None:
        if (
            str(existing["candidate_id"]) != str(candidate_id)
            or existing["action"] != action.strip().upper()
            or existing["reason"] != reason.strip()
            or existing["preview_sha256"] != expected_preview_sha256
            or existing["offer_link_kind"] != (
                offer_link_kind.strip().upper()
                if isinstance(offer_link_kind, str)
                else None
            )
            or (
                existing["offer_link_kind"] == "LINKED_EXISTING"
                and existing["result_offer_id"] != existing_offer_id
            )
            or existing["human_principal_ref"] != principal.principal_ref
            or existing["human_role_ref"] != principal.role_ref
            or existing["human_authn_context_sha256"] != principal.authn_context_sha256
        ):
            raise PersistentMappingError(
                "decision idempotency key was used for different intent",
                code="IDEMPOTENCY_CONFLICT",
            )
        return {**existing, "replayed": True}
    record = _mapping_decision_record(
        conn, candidate_id=candidate_id, action=action, reason=reason,
        principal=principal, decision_idempotency_key=decision_idempotency_key,
        existing_offer_id=existing_offer_id, offer_link_kind=offer_link_kind,
    )
    if record["preview_sha256"] != expected_preview_sha256:
        raise PersistentMappingError("mapping preview is stale")
    candidate, _batch = _candidate_decision_facts(conn, candidate_id)
    initial_request_sha256 = record["request_sha256"]
    if record["action"] == "APPROVE_MAPPING":
        lock_ids = sorted(
            {
                int(value)
                for value in (record["result_offer_id"],)
                if value is not None
            }
        )
        if lock_ids:
            locked_ids = [
                int(row[0])
                for row in conn.execute(
                    sql.SQL(
                        "SELECT offer_id FROM {} WHERE offer_id=ANY(%s) "
                        "ORDER BY offer_id FOR UPDATE"
                    ).format(_qualified("supplier_offers")),
                    (lock_ids,),
                ).fetchall()
            ]
            if locked_ids != lock_ids:
                raise PersistentMappingError("linked supplier offer does not exist")
        if record["offer_link_kind"] == "CREATED_INACTIVE":
            record["result_offer_id"] = _insert_created_inactive_offer(
                conn,
                candidate=candidate,
                package_type=record["result_offer_package_type"],
            )
        actual_offer_contract = conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_offer_fingerprint(%s)").format(
                sql.Identifier(SCHEMA)
            ),
            (record["result_offer_id"],),
        ).fetchone()[0]
        if actual_offer_contract != record["result_offer_contract_sha256"]:
            raise PersistentMappingError("resulting supplier offer contract differs")
    elif record["action"] == "REJECT_MAPPING":
        record["result_rejection_id"] = _insert_mapping_rejection(
            conn,
            candidate=candidate,
            principal=principal,
            evidence_set_sha256=record["evidence_set_sha256"],
        )
        actual_rejection_contract = conn.execute(
            sql.SQL(
                "SELECT {}.persistent_mapping_rejection_contract_fingerprint(%s)"
            ).format(sql.Identifier(SCHEMA)),
            (record["result_rejection_id"],),
        ).fetchone()[0]
        if actual_rejection_contract != record["result_rejection_contract_sha256"]:
            raise PersistentMappingError("resulting rejection contract differs")
    record = _seal_mapping_decision_record(conn, record)
    if (
        record["preview_sha256"] != expected_preview_sha256
        or record["request_sha256"] != initial_request_sha256
    ):
        raise PersistentMappingError("generated mapping result changed the confirmed request")
    _insert_record(conn, table="supplier_mapping_decisions", record=record)
    return {**record, "replayed": False}


def _selection_record(
    conn: Any, *, mapping_decision_id: UUID | None, principal: Principal,
    selection_idempotency_key: UUID, reason: str, effective_from: date,
    action: str = "SELECT", variant_id: str | None = None,
) -> dict[str, Any]:
    action = action.strip().upper()
    if action not in {"SELECT", "CLEAR"}:
        raise PersistentMappingError("routine selection action is unsupported")
    mapping: dict[str, Any] | None = None
    with conn.cursor(row_factory=dict_row) as cursor:
        if action == "SELECT":
            if mapping_decision_id is None or variant_id is not None:
                raise PersistentMappingError("selection requires one mapping decision")
            mapping = cursor.execute(
                sql.SQL("SELECT * FROM {} WHERE mapping_decision_id=%s AND action='APPROVE_MAPPING'").format(
                    _qualified("v_effective_supplier_mapping_decisions")
                ),
                (mapping_decision_id,),
            ).fetchone()
            if mapping is None:
                raise PersistentMappingError("effective approved mapping does not exist")
            variant_id = str(mapping["variant_id"])
        elif mapping_decision_id is not None or not variant_id:
            raise PersistentMappingError("clear requires one exact Variant ID")
        head = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE variant_id=%s AND selection_scope=%s").format(
                _qualified("supplier_offer_selection_heads")
            ),
            (variant_id, SELECTION_SCOPE),
        ).fetchone()
    if action == "CLEAR" and head is None:
        raise PersistentMappingError("routine selection is already absent")
    selected_offer_id = int(mapping["result_offer_id"]) if mapping is not None else None
    offer_sha = (
        conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_offer_fingerprint(%s)").format(sql.Identifier(SCHEMA)),
            (selected_offer_id,),
        ).fetchone()[0]
        if selected_offer_id is not None
        else None
    )
    catalog_sha = conn.execute(
        sql.SQL("SELECT {}.persistent_mapping_catalog_fingerprint(%s)").format(sql.Identifier(SCHEMA)),
        (variant_id,),
    ).fetchone()[0]
    vendor_sha = (
        conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_vendor_fingerprint(%s)").format(sql.Identifier(SCHEMA)),
            (mapping["vendor_id"],),
        ).fetchone()[0]
        if mapping is not None
        else None
    )
    rejection_sha = (
        conn.execute(
            sql.SQL("SELECT {}.persistent_mapping_rejection_fingerprint(%s,%s,NULL)").format(sql.Identifier(SCHEMA)),
            (mapping["vendor_id"], f"persistent-mapping:{mapping['supplier_identity_key_sha256']}"),
        ).fetchone()[0]
        if mapping is not None
        else None
    )
    record: dict[str, Any] = {
        "selection_event_id": str(uuid5(NAMESPACE_URL, f"buffalo-offer-selection:{selection_idempotency_key}")),
        "selection_idempotency_key": str(selection_idempotency_key),
        "variant_id": variant_id,
        "selection_scope": SELECTION_SCOPE,
        "action": action,
        "selected_offer_id": selected_offer_id,
        "mapping_decision_id": str(mapping["mapping_decision_id"]) if mapping else None,
        "expected_prior_event_id": str(head["selection_event_id"]) if head else None,
        "expected_prior_head_version": int(head["head_version"]) if head else 0,
        # The insert trigger already proved this exact database-side JSONB text
        # digest.  Re-encoding canonical_payload in Python would lose NUMERIC
        # scale and could turn an unchanged mapping into a false stale preview.
        "expected_mapping_decision_sha256": mapping["payload_sha256"] if mapping else None,
        "expected_offer_contract_sha256": offer_sha,
        "expected_catalog_sha256": catalog_sha,
        "expected_vendor_sha256": vendor_sha,
        "expected_rejection_memory_sha256": rejection_sha,
        "effective_from": str(effective_from),
        "effective_through": None,
        "human_principal_ref": principal.principal_ref,
        "human_role_ref": principal.role_ref,
        "human_authn_context_sha256": principal.authn_context_sha256,
        "preview_sha256": "0" * 64,
        "confirmation_sha256": "0" * 64,
        "reason": reason.strip(),
        "canonical_payload": {},
        "payload_sha256": "0" * 64,
    }
    if not record["reason"]:
        raise PersistentMappingError("selection reason is required")
    record["preview_sha256"] = _composite_value(
        conn, table="supplier_offer_selection_events",
        function="persistent_mapping_selection_preview_sha256", record=record,
    )
    record["confirmation_sha256"] = _composite_value(
        conn, table="supplier_offer_selection_events",
        function="persistent_mapping_selection_confirmation_sha256", record=record,
    )
    payload, payload_sha256 = _project_record(
        conn, table="supplier_offer_selection_events", record=record,
        omit=("selection_event_id", "selection_idempotency_key", "canonical_payload", "payload_sha256", "selected_at", "selected_txid"),
    )
    record["canonical_payload"] = payload
    record["payload_sha256"] = payload_sha256
    return record


def preview_routine_offer_selection(
    conn: Any, *, mapping_decision_id: UUID, principal: Principal,
    selection_idempotency_key: UUID, reason: str, effective_from: date,
) -> dict[str, Any]:
    require_synthetic_mapping_capability("routine_selection_writes_enabled")
    principal.validate()
    return _selection_record(
        conn, mapping_decision_id=mapping_decision_id, principal=principal,
        selection_idempotency_key=selection_idempotency_key,
        reason=reason, effective_from=effective_from,
    )


def preview_routine_offer_clear(
    conn: Any,
    *,
    variant_id: str,
    principal: Principal,
    selection_idempotency_key: UUID,
    reason: str,
    effective_from: date,
) -> dict[str, Any]:
    require_synthetic_mapping_capability("routine_selection_writes_enabled")
    principal.validate()
    return _selection_record(
        conn,
        mapping_decision_id=None,
        variant_id=variant_id,
        action="CLEAR",
        principal=principal,
        selection_idempotency_key=selection_idempotency_key,
        reason=reason,
        effective_from=effective_from,
    )


def record_routine_offer_selection(
    conn: Any, *, mapping_decision_id: UUID, principal: Principal,
    selection_idempotency_key: UUID, reason: str, effective_from: date,
    expected_preview_sha256: str,
) -> dict[str, Any]:
    require_synthetic_mapping_capability("routine_selection_writes_enabled")
    _set_human_context(
        conn, principal, action="ROUTINE_OFFER_SELECT",
        capability="routine_selection_writes_enabled",
    )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
        (f"supplier-offer-selection-idempotency:{selection_idempotency_key}",),
    )
    with conn.cursor(row_factory=dict_row) as cursor:
        existing = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE selection_idempotency_key=%s").format(
                _qualified("supplier_offer_selection_events")
            ),
            (selection_idempotency_key,),
        ).fetchone()
    if existing is not None:
        if (
            str(existing["mapping_decision_id"]) != str(mapping_decision_id)
            or existing["reason"] != reason.strip()
            or existing["effective_from"] != effective_from
            or existing["preview_sha256"] != expected_preview_sha256
            or existing["human_principal_ref"] != principal.principal_ref
            or existing["human_role_ref"] != principal.role_ref
            or existing["human_authn_context_sha256"] != principal.authn_context_sha256
        ):
            raise PersistentMappingError(
                "selection idempotency key was used for different intent",
                code="IDEMPOTENCY_CONFLICT",
            )
        return {**existing, "replayed": True}
    record = _selection_record(
        conn, mapping_decision_id=mapping_decision_id, principal=principal,
        selection_idempotency_key=selection_idempotency_key,
        reason=reason, effective_from=effective_from,
    )
    if record["preview_sha256"] != expected_preview_sha256:
        raise PersistentMappingError("selection preview is stale")
    _insert_record(conn, table="supplier_offer_selection_events", record=record)
    if record["expected_prior_event_id"] is None:
        advanced = conn.execute(
            sql.SQL(
                "INSERT INTO {}"
                "(variant_id,selection_scope,selection_event_id,head_version) "
                "VALUES (%s,%s,%s,1) ON CONFLICT(variant_id,selection_scope) "
                "DO NOTHING RETURNING head_version"
            ).format(_qualified("supplier_offer_selection_heads")),
            (record["variant_id"], SELECTION_SCOPE, record["selection_event_id"]),
        ).fetchone()
    else:
        advanced = conn.execute(
            sql.SQL(
                "UPDATE {} SET selection_event_id=%s,head_version=head_version+1,"
                "updated_at=pg_catalog.clock_timestamp(),"
                "updated_txid=pg_catalog.txid_current() "
                "WHERE variant_id=%s AND selection_scope=%s "
                "AND selection_event_id=%s AND head_version=%s RETURNING head_version"
            ).format(_qualified("supplier_offer_selection_heads")),
            (
                record["selection_event_id"],
                record["variant_id"],
                SELECTION_SCOPE,
                record["expected_prior_event_id"],
                record["expected_prior_head_version"],
            ),
        ).fetchone()
    if advanced is None:
        raise PersistentMappingError("routine offer selection preview is stale")
    return {**record, "replayed": False}


def record_routine_offer_clear(
    conn: Any,
    *,
    variant_id: str,
    principal: Principal,
    selection_idempotency_key: UUID,
    reason: str,
    effective_from: date,
    expected_preview_sha256: str,
) -> dict[str, Any]:
    require_synthetic_mapping_capability("routine_selection_writes_enabled")
    _set_human_context(
        conn,
        principal,
        action="ROUTINE_OFFER_SELECT",
        capability="routine_selection_writes_enabled",
    )
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
        (f"supplier-offer-selection-idempotency:{selection_idempotency_key}",),
    )
    with conn.cursor(row_factory=dict_row) as cursor:
        existing = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE selection_idempotency_key=%s").format(
                _qualified("supplier_offer_selection_events")
            ),
            (selection_idempotency_key,),
        ).fetchone()
    if existing is not None:
        if (
            existing["action"] != "CLEAR"
            or str(existing["variant_id"]) != str(variant_id)
            or existing["reason"] != reason.strip()
            or existing["effective_from"] != effective_from
            or existing["preview_sha256"] != expected_preview_sha256
            or existing["human_principal_ref"] != principal.principal_ref
            or existing["human_role_ref"] != principal.role_ref
            or existing["human_authn_context_sha256"] != principal.authn_context_sha256
        ):
            raise PersistentMappingError(
                "selection idempotency key was used for different intent",
                code="IDEMPOTENCY_CONFLICT",
            )
        return {**existing, "replayed": True}
    record = _selection_record(
        conn,
        mapping_decision_id=None,
        variant_id=variant_id,
        action="CLEAR",
        principal=principal,
        selection_idempotency_key=selection_idempotency_key,
        reason=reason,
        effective_from=effective_from,
    )
    if record["preview_sha256"] != expected_preview_sha256:
        raise PersistentMappingError("selection preview is stale")
    _insert_record(conn, table="supplier_offer_selection_events", record=record)
    advanced = conn.execute(
        sql.SQL(
            "UPDATE {} SET selection_event_id=%s,head_version=head_version+1,"
            "updated_at=pg_catalog.clock_timestamp(),updated_txid=pg_catalog.txid_current() "
            "WHERE variant_id=%s AND selection_scope=%s AND selection_event_id=%s "
            "AND head_version=%s RETURNING head_version"
        ).format(_qualified("supplier_offer_selection_heads")),
        (
            record["selection_event_id"],
            variant_id,
            SELECTION_SCOPE,
            record["expected_prior_event_id"],
            record["expected_prior_head_version"],
        ),
    ).fetchone()
    if advanced is None:
        raise PersistentMappingError("routine offer selection preview is stale")
    return {**record, "replayed": False}


def execute_supplier_mapping_intake(
    database_url: str,
    *,
    package: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    principal: Principal,
    intake_idempotency_key: UUID,
) -> dict[str, Any]:
    frozen_package = copy.deepcopy(dict(package))
    frozen_candidates = tuple(copy.deepcopy(dict(item)) for item in candidates)
    _validate_supported_intake(
        package=frozen_package, candidates=frozen_candidates
    )
    def operation(conn: Any) -> dict[str, Any]:
        return intake_supplier_mapping_review(
            conn,
            package=frozen_package,
            candidates=frozen_candidates,
            principal=principal,
            intake_idempotency_key=intake_idempotency_key,
        )

    return _execute_write(
        database_url,
        operation_name="review-intake",
        capability="review_intake_writes_enabled",
        principal=principal,
        idempotency_key=intake_idempotency_key,
        domain_locks=lambda _conn: (),
        operation=operation,
        recovery_lookup=lambda conn: (
            operation(conn)
            if _idempotency_row_exists(
                conn,
                table="supplier_mapping_review_batches",
                column="intake_idempotency_key",
                value=intake_idempotency_key,
            )
            else None
        ),
    )


def _mapping_domain_locks(
    conn: Any, *, candidate_id: UUID, include_offer_key: bool
) -> Sequence[tuple[int, str]]:
    row = conn.execute(
        sql.SQL(
            "SELECT decision_scope_sha256,operational_offer_key_sha256 FROM {} "
            "WHERE candidate_id=%s"
        ).format(_qualified("supplier_mapping_review_candidates")),
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise PersistentMappingError("mapping candidate does not exist")
    locks: list[tuple[int, str]] = [
        (0, f"persistent-mapping:decision-scope:{row[0]}")
    ]
    if include_offer_key and row[1] is not None:
        locks.append((1, f"persistent-mapping:operational-offer-key:{row[1]}"))
    return locks


def _idempotency_row_exists(
    conn: Any, *, table: str, column: str, value: UUID
) -> bool:
    if (table, column) not in {
        ("supplier_mapping_review_batches", "intake_idempotency_key"),
        ("supplier_mapping_decisions", "decision_idempotency_key"),
        ("supplier_offer_selection_events", "selection_idempotency_key"),
    }:
        raise PersistentMappingError("idempotency recovery target is unsupported")
    return (
        conn.execute(
            sql.SQL("SELECT 1 FROM {} WHERE {}=%s").format(
                _qualified(table), sql.Identifier(column)
            ),
            (value,),
        ).fetchone()
        is not None
    )


def execute_mapping_decision(
    database_url: str,
    *,
    candidate_id: UUID,
    action: str,
    reason: str,
    principal: Principal,
    decision_idempotency_key: UUID,
    expected_preview_sha256: str,
    existing_offer_id: int | None = None,
    offer_link_kind: str | None = None,
) -> dict[str, Any]:
    normalized_action = action.strip().upper()
    def operation(conn: Any) -> dict[str, Any]:
        return record_mapping_decision(
            conn,
            candidate_id=candidate_id,
            action=normalized_action,
            reason=reason,
            principal=principal,
            decision_idempotency_key=decision_idempotency_key,
            expected_preview_sha256=expected_preview_sha256,
            existing_offer_id=existing_offer_id,
            offer_link_kind=offer_link_kind,
        )

    return _execute_write(
        database_url,
        operation_name="mapping-decision",
        capability="human_mapping_writes_enabled",
        principal=principal,
        idempotency_key=decision_idempotency_key,
        domain_locks=lambda conn: _mapping_domain_locks(
            conn,
            candidate_id=candidate_id,
            include_offer_key=normalized_action in {"APPROVE_MAPPING", "REJECT_MAPPING"},
        ),
        operation=operation,
        recovery_lookup=lambda conn: (
            operation(conn)
            if _idempotency_row_exists(
                conn,
                table="supplier_mapping_decisions",
                column="decision_idempotency_key",
                value=decision_idempotency_key,
            )
            else None
        ),
    )


def _selection_domain_locks(
    conn: Any, *, mapping_decision_id: UUID | None, variant_id: str | None
) -> Sequence[tuple[int, str]]:
    if mapping_decision_id is not None:
        row = conn.execute(
            sql.SQL(
                "SELECT variant_id FROM {} WHERE mapping_decision_id=%s "
                "AND action='APPROVE_MAPPING'"
            ).format(_qualified("supplier_mapping_decisions")),
            (mapping_decision_id,),
        ).fetchone()
        if row is None:
            raise PersistentMappingError("approved mapping decision does not exist")
        variant_id = str(row[0])
    if not variant_id:
        raise PersistentMappingError("routine selection Variant ID is absent")
    return ((2, f"persistent-mapping:selection-variant:{variant_id}"),)


def execute_routine_offer_selection(
    database_url: str,
    *,
    mapping_decision_id: UUID,
    principal: Principal,
    selection_idempotency_key: UUID,
    reason: str,
    effective_from: date,
    expected_preview_sha256: str,
) -> dict[str, Any]:
    def operation(conn: Any) -> dict[str, Any]:
        return record_routine_offer_selection(
            conn,
            mapping_decision_id=mapping_decision_id,
            principal=principal,
            selection_idempotency_key=selection_idempotency_key,
            reason=reason,
            effective_from=effective_from,
            expected_preview_sha256=expected_preview_sha256,
        )

    return _execute_write(
        database_url,
        operation_name="routine-selection",
        capability="routine_selection_writes_enabled",
        principal=principal,
        idempotency_key=selection_idempotency_key,
        domain_locks=lambda conn: _selection_domain_locks(
            conn, mapping_decision_id=mapping_decision_id, variant_id=None
        ),
        operation=operation,
        recovery_lookup=lambda conn: (
            operation(conn)
            if _idempotency_row_exists(
                conn,
                table="supplier_offer_selection_events",
                column="selection_idempotency_key",
                value=selection_idempotency_key,
            )
            else None
        ),
    )


def execute_routine_offer_clear(
    database_url: str,
    *,
    variant_id: str,
    principal: Principal,
    selection_idempotency_key: UUID,
    reason: str,
    effective_from: date,
    expected_preview_sha256: str,
) -> dict[str, Any]:
    def operation(conn: Any) -> dict[str, Any]:
        return record_routine_offer_clear(
            conn,
            variant_id=variant_id,
            principal=principal,
            selection_idempotency_key=selection_idempotency_key,
            reason=reason,
            effective_from=effective_from,
            expected_preview_sha256=expected_preview_sha256,
        )

    return _execute_write(
        database_url,
        operation_name="routine-selection",
        capability="routine_selection_writes_enabled",
        principal=principal,
        idempotency_key=selection_idempotency_key,
        domain_locks=lambda conn: _selection_domain_locks(
            conn, mapping_decision_id=None, variant_id=variant_id
        ),
        operation=operation,
        recovery_lookup=lambda conn: (
            operation(conn)
            if _idempotency_row_exists(
                conn,
                table="supplier_offer_selection_events",
                column="selection_idempotency_key",
                value=selection_idempotency_key,
            )
            else None
        ),
    )


def mapping_shadow(conn: Any, *, variant_id: str | None = None) -> list[dict[str, Any]]:
    query = sql.SQL("SELECT * FROM {} ").format(
        _qualified("v_supplier_offer_selection_shadow")
    )
    parameters: tuple[Any, ...] = ()
    if variant_id is not None:
        query += sql.SQL("WHERE variant_id=%s ")
        parameters = (variant_id,)
    query += sql.SQL("ORDER BY variant_id")
    with conn.cursor(row_factory=dict_row) as cursor:
        return list(cursor.execute(query, parameters).fetchall())


def mapping_status(conn: Any) -> dict[str, Any]:
    """Return bounded counts and explicit shadow-only state for the browser."""

    row = conn.execute(
        sql.SQL(
            "SELECT "
            "(SELECT count(*) FROM {}),"
            "(SELECT count(*) FROM {}),"
            "(SELECT count(*) FROM {}),"
            "(SELECT count(*) FROM {}),"
            "(SELECT count(*) FROM {}),"
            "(SELECT count(*) FROM {} WHERE shadow_comparison='MATCH'),"
            "(SELECT count(*) FROM {} WHERE shadow_comparison<>'MATCH')"
        ).format(
            _qualified("supplier_mapping_review_batches"),
            _qualified("supplier_mapping_review_candidates"),
            _qualified("supplier_mapping_decisions"),
            _qualified("supplier_offer_selection_events"),
            _qualified("supplier_offer_selection_heads"),
            _qualified("v_supplier_offer_selection_shadow"),
            _qualified("v_supplier_offer_selection_shadow"),
        )
    ).fetchone()
    assert row is not None
    return {
        "review_batch_count": int(row[0]),
        "candidate_count": int(row[1]),
        "decision_count": int(row[2]),
        "selection_event_count": int(row[3]),
        "selection_head_count": int(row[4]),
        "shadow_match_count": int(row[5]),
        "shadow_nonmatch_count": int(row[6]),
        "authority": "SHADOW_ONLY",
        "recommendation_cutover_enabled": False,
        "offer_activation_enabled": False,
    }


def get_mapping_candidate_detail(conn: Any, candidate_id: UUID) -> dict[str, Any]:
    """Read one candidate with immutable evidence, history, offers, and shadow."""

    candidate, batch = _candidate_decision_facts(conn, candidate_id)
    with conn.cursor(row_factory=dict_row) as cursor:
        decisions = list(
            cursor.execute(
                sql.SQL(
                    "SELECT * FROM {} WHERE candidate_id=%s "
                    "ORDER BY decided_at,mapping_decision_id"
                ).format(_qualified("supplier_mapping_decisions")),
                (candidate_id,),
            ).fetchall()
        )
        effective = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE candidate_id=%s").format(
                _qualified("v_effective_supplier_mapping_decisions")
            ),
            (candidate_id,),
        ).fetchone()
        offers: list[dict[str, Any]] = []
        if candidate["proposed_variant_id"] and candidate["proposed_vendor_id"]:
            offers = list(
                cursor.execute(
                    sql.SQL(
                        "SELECT o.*,{}.persistent_mapping_offer_fingerprint(o.offer_id) "
                        "AS offer_contract_sha256 FROM {} o "
                        "WHERE o.variant_id=%s AND o.vendor_id=%s ORDER BY o.offer_id"
                    ).format(
                        sql.Identifier(SCHEMA), _qualified("supplier_offers")
                    ),
                    (
                        candidate["proposed_variant_id"],
                        candidate["proposed_vendor_id"],
                    ),
                ).fetchall()
            )
        rejections: list[dict[str, Any]] = []
        if candidate["supplier_identity_key_sha256"]:
            rejections = list(
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE mapping_type='SUPPLIER_OFFER' "
                        "AND source_key=%s ORDER BY rejection_id"
                    ).format(_qualified("mapping_rejections")),
                    (
                        "persistent-mapping:"
                        + candidate["supplier_identity_key_sha256"],
                    ),
                ).fetchall()
            )
    comparisons: list[dict[str, Any]] = []
    package_type = {
        "REGULAR": "STANDARD",
        "GIFT": "GIFT",
        "SPECIAL": "SPECIAL",
        "ALTERNATE": "ALTERNATE",
        "COMPONENT": "COMPONENT",
        "COMBO": "COMBO",
    }.get(candidate["offer_class"])
    for offer in offers:
        fields = {
            "supplier_sku": (
                candidate["supplier_code_value"]
                if candidate["supplier_code_state"] == "VALUE"
                else None,
                offer["supplier_sku"],
            ),
            "package_type": (package_type, offer["package_type"]),
            "size_text": (candidate["size_value"], offer["size_text"]),
            "raw_pack": (candidate["raw_pack_value"], offer["raw_pack"]),
            "shopify_units_per_case": (
                candidate["shopify_units_value"],
                offer["shopify_units_per_case"],
            ),
            "qualifying_units_per_case": (
                candidate["qualifying_units_value"],
                offer["qualifying_units_per_case"],
            ),
            "assortment_scope": (
                candidate["assortment_scope_value"],
                offer["assortment_scope"],
            ),
            "assortment_group": (
                candidate["assortment_group_value"],
                offer["assortment_group"],
            ),
            "assortable": (candidate["assortable_value"], offer["assortable"]),
        }
        comparisons.append(
            {
                "offer": offer,
                "fields": {
                    name: {
                        "candidate": values[0],
                        "offer": values[1],
                        "matches": values[0] == values[1],
                    }
                    for name, values in fields.items()
                },
                "exact_contract_match": all(left == right for left, right in fields.values())
                and offer["confidence"] == "VERIFIED",
            }
        )
    return {
        "candidate": candidate,
        "batch": batch,
        "decisions": decisions,
        "effective_decision": effective,
        "offer_comparisons": comparisons,
        "rejections": rejections,
        "shadow": mapping_shadow(
            conn, variant_id=candidate["proposed_variant_id"]
        )
        if candidate["proposed_variant_id"]
        else [],
        "authority": "SHADOW_ONLY",
    }

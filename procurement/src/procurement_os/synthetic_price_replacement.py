"""Attested synthetic-only supplier price replacement services.

The legacy price-book API remains FUTURE-only and byte-compatible.  This module
adds a separately gated path for one checksum-pinned fabricated monthly book;
it grants no real supplier authority and never accepts a client-supplied clock,
database identity, policy, or recovery path.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from .monday_forecast_retirement import (
    MondayForecastRetirementContractError,
    verify_monday_forecast_v2_retirement_contract,
)
from .persistent_mapping import (
    PersistentMappingError,
    Principal,
    SCHEMA,
    _set_human_context,
    _try_session_lock,
    _unlock_session_locks,
    _validate_synthetic_database_url,
    _verify_connected_synthetic_database,
)
from .price_book import (
    PriceBookError,
    _candidate_future_fingerprint,
    _database_validation,
    _future_generation_fingerprint,
    _future_predecessor_batch_id,
    _parsed_from_staging,
    _transaction_lock,
    _verified_storage_read,
    _verified_storage_write,
    parse_price_book_csv,
)
from .storage import StorageAdapter
from .local_backup_v2 import (
    database_state_evidence,
    LocalBackupV2Error,
    VerifiedPriceApplyBackup,
    verify_bound_price_apply_backup,
)
from .synthetic_selected_offer import (
    SyntheticSelectedOfferError,
    acquire_input_locks,
    release_input_locks,
)
from .synthetic_price_replacement_contract import (
    CATALOG_SHA256,
    FIXTURE_REGISTRATION_CANONICAL_SHA256,
    MIGRATION_SHA256,
    REGISTERED_OPERATOR_BOOK_BYTES,
    REGISTERED_OPERATOR_BOOK_REF,
    REGISTERED_OPERATOR_BOOK_SHA256,
    STOPPED_SERVICE_PRICE_STAGE_AUTHN_SHA256,
    STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
    STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL,
    STOPPED_SERVICE_PRICE_STAGE_ROLE,
    verify_synthetic_price_replacement_contract,
)


CONTRACT = "SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1"
DECLARATION_CONTRACT = "BUFFALO_SYNTHETIC_PRICE_DECLARATION_V1"
CAPABILITY_ENV = "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT"
CAPABILITY_POLICY = "synthetic_price_replacement_enabled"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "synthetic_price_replacement_fixture.json"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_APPLY_OPERATION_SECONDS = 20.0
_APPLY_SCOPE_LOCK_PREFIX = "synthetic-price-replacement:scope:"
_APPLY_KEY_LOCK_PREFIX = "synthetic-price-replacement:idempotency:"
_POLICY_VERSION = 1
_POLICY_CADENCE = "MONTHLY"
_POLICY_TIMEZONE = "America/New_York"
_OBSERVATION_WINDOW_START_DAY = 15
_OBSERVATION_WINDOW_END_DAY = 20
_EFFECTIVE_BOUNDARY_DAY = 1
_POLICY_PRINCIPAL_REF = "synthetic:price-fixture-registration:01"
_REGISTERED_OBSERVATION_BASIS = "REGISTERED_OBSERVATION"


class SyntheticPriceReplacementError(PriceBookError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _fixture() -> dict[str, Any]:
    try:
        value = json.loads(FIXTURE_PATH.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
        ) from exc
    if (
        not isinstance(value, dict)
        or _sha256(value) != FIXTURE_REGISTRATION_CANONICAL_SHA256
        or value.get("contract")
        != "BUFFALO_SYNTHETIC_PRICE_REPLACEMENT_FIXTURE_V1"
        or value.get("schema") != SCHEMA
        or not isinstance(value.get("policies"), list)
        or len(value["policies"]) != 2
    ):
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
        )
    return value


def require_process_policy() -> tuple[str, str, int]:
    """Refuse before database/storage access unless the launcher asserted mode."""

    try:
        if os.getenv(CAPABILITY_ENV) != "1":
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        if os.getenv("BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO") != "1":
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        mode = os.getenv("BUFFALO_RUNTIME_MODE", "").strip().upper()
        if mode not in {"AUTOMATED_TEST", "SYNTHETIC_DEMO"}:
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        from .config import load_rules

        rules = load_rules()
        policy = rules.get("persistent_mapping", {})
        pricing_policy = rules.get("pricing", {})
        disabled = (
            "review_intake_writes_enabled",
            "human_mapping_writes_enabled",
            "policy_mapping_writes_enabled",
            "routine_selection_writes_enabled",
            "selected_offer_shadow_reads_enabled",
            "synthetic_selected_offer_inputs_enabled",
            "recommendation_cutover_enabled",
            "offer_activation_enabled",
        )
        if (
            policy.get("contract_version") != "v1-shadow-only"
            or any(policy.get(key) is not False for key in disabled)
            or pricing_policy.get(CAPABILITY_POLICY) is not False
        ):
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        variable = "TEST_DATABASE_URL" if mode == "AUTOMATED_TEST" else "DATABASE_URL"
        database_url = os.getenv(variable, "")
        expected_database = _validate_synthetic_database_url(database_url)
        expected_suffix = "_test" if mode == "AUTOMATED_TEST" else "_demo"
        if not expected_database.endswith(expected_suffix):
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        parsed = urlparse(database_url)
        expected_port = parsed.port or 5432
        if not 1 <= expected_port <= 65535:
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        _fixture()
        return mode, expected_database, expected_port
    except SyntheticPriceReplacementError:
        raise
    except (PersistentMappingError, OSError, ValueError) as exc:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
        ) from exc


def require_attested_database(conn: Any) -> None:
    """Re-attest the exact installed synthetic database on every service call."""

    try:
        mode, expected_database, expected_port = require_process_policy()
        _verify_connected_synthetic_database(conn, expected_database)
        from .synthetic_staging_database import (
            is_staging_runtime_connection,
            verify_staging_persistent_mapping_contract,
        )

        staging_runtime = is_staging_runtime_connection(conn)
        facts = conn.execute(
            "SELECT current_schema(),current_database(),inet_server_port(),"
            "session_user::text,current_user::text"
        ).fetchone()
        if facts is None or (
            str(facts[0]),
            str(facts[1]),
            int(facts[2]),
            str(facts[3]),
            str(facts[4]),
        ) != (
            SCHEMA,
            expected_database,
            expected_port,
            "buffalo_synthetic_runtime" if staging_runtime else "qa_release_login",
            "buffalo_synthetic_runtime" if staging_runtime else "qa_mapping_owner",
        ):
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        if staging_runtime:
            verify_staging_persistent_mapping_contract(conn)
        else:
            conn.execute(
                f'SELECT "{SCHEMA}".assert_persistent_mapping_foundation_contract()'
            )
        verify_monday_forecast_v2_retirement_contract(conn)
        verify_synthetic_price_replacement_contract(conn)
        markers = dict(
            conn.execute(
                f'SELECT key,value FROM "{SCHEMA}".meta WHERE key=ANY(%s)',
                (["synthetic_owner_demo_contract"],),
            ).fetchall()
        )
        if mode == "SYNTHETIC_DEMO" and markers.get(
            "synthetic_owner_demo_contract"
        ) != "BUFFALO_SYNTHETIC_OWNER_DEMO_V1":
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        policies = conn.execute(
            f'''SELECT count(*),bool_and(fixture_database_name=current_database()),
                       bool_and(fixture_policy_config_sha256=%s)
                  FROM "{SCHEMA}".supplier_price_schedule_policies''',
            (FIXTURE_REGISTRATION_CANONICAL_SHA256,),
        ).fetchone()
        if policies != (2, True, True):
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
    except SyntheticPriceReplacementError:
        raise
    except (
        PersistentMappingError,
        MondayForecastRetirementContractError,
        Exception,
    ) as exc:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
        ) from exc


def _registered_policy_evidence(
    conn: Any,
    *,
    vendor_name: str,
) -> dict[str, Any]:
    """Return one exact DB-owned policy after verifying its full registration."""

    fixture = _fixture()
    matches = [
        item
        for item in fixture["policies"]
        if item.get("vendor_name") == vendor_name
        and item.get("target_for_replacement") is True
    ]
    if len(matches) != 1:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
        )
    registered = matches[0]
    row = conn.execute(
        f'''SELECT policy_ref,policy_version,vendor_id::text,price_scope_key,
                   cadence,currency,policy_timezone,observation_window_start_day,
                   observation_window_end_day,effective_boundary_day,
                   source_validity_required,fixture_policy_config_sha256,
                   fixture_database_name,policy_principal_ref,observation_at,
                   application_at,monday_evaluation_at,policy_sha256
              FROM "{SCHEMA}".supplier_price_schedule_policies
             WHERE policy_ref=%s''',
        (registered["policy_ref"],),
    ).fetchone()
    try:
        expected_policy_sha256 = conn.execute(
            f'SELECT "{SCHEMA}".persistent_mapping_json_sha256(%s::jsonb)',
            (json.dumps(registered, sort_keys=True, separators=(",", ":")),),
        ).fetchone()[0]
        expected_observation = datetime.fromisoformat(fixture["observation_at"])
        expected_application = datetime.fromisoformat(fixture["application_at"])
        expected_monday = datetime.fromisoformat(fixture["monday_evaluation_at"])
        database_name = str(conn.info.dbname)
        if row is None or (
            str(row[0]),
            int(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6]),
            int(row[7]),
            int(row[8]),
            int(row[9]),
            bool(row[10]),
            str(row[11]),
            str(row[12]),
            str(row[13]),
            row[14],
            row[15],
            row[16],
            str(row[17]),
        ) != (
            registered["policy_ref"],
            _POLICY_VERSION,
            registered["vendor_id"],
            registered["price_scope_key"],
            _POLICY_CADENCE,
            registered["currency"],
            _POLICY_TIMEZONE,
            _OBSERVATION_WINDOW_START_DAY,
            _OBSERVATION_WINDOW_END_DAY,
            _EFFECTIVE_BOUNDARY_DAY,
            True,
            FIXTURE_REGISTRATION_CANONICAL_SHA256,
            database_name,
            _POLICY_PRINCIPAL_REF,
            expected_observation,
            expected_application,
            expected_monday,
            str(expected_policy_sha256),
        ):
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
            )
        timezone = ZoneInfo(str(row[6]))
        observation_local = row[14].astimezone(timezone)
        application_local = row[15].astimezone(timezone)
        monday_local = row[16].astimezone(timezone)
        next_month = (
            date(observation_local.year + 1, 1, 1)
            if observation_local.month == 12
            else date(observation_local.year, observation_local.month + 1, 1)
        )
        if (
            not int(row[7]) <= observation_local.day <= int(row[8])
            or application_local.date() != next_month
            or application_local.day != int(row[9])
            or date.fromisoformat(fixture["source_valid_from"])
            != application_local.date()
            or monday_local.weekday() != 0
            or row[16] <= row[15]
            or row[15] <= row[14]
        ):
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
            )
    except (
        AttributeError,
        IndexError,
        TypeError,
        ValueError,
        ZoneInfoNotFoundError,
    ) as exc:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
        ) from exc
    return {
        "registered": registered,
        "fixture": fixture,
        "policy_ref": str(row[0]),
        "vendor_id": str(row[2]),
        "price_scope_key": str(row[3]),
        "currency": str(row[5]),
        "policy_timezone": str(row[6]),
        "observation_window_start_day": int(row[7]),
        "observation_window_end_day": int(row[8]),
        "effective_boundary_day": int(row[9]),
        "observation_at": row[14],
        "application_at": row[15],
        "monday_evaluation_at": row[16],
        "policy_sha256": str(row[17]),
    }


def _registered_declaration(
    conn: Any,
    *,
    vendor_name: str,
    effective_from: date,
    effective_through: date | None,
) -> dict[str, Any]:
    policy = _registered_policy_evidence(conn, vendor_name=vendor_name)
    fixture = policy["fixture"]
    source_from = date.fromisoformat(fixture["source_valid_from"])
    source_through = date.fromisoformat(fixture["source_valid_through"])
    if effective_from != source_from or effective_through != source_through:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_DATES_DIFFER"
        )
    declaration = {
        "contract": DECLARATION_CONTRACT,
        "fixture_registration_sha256": FIXTURE_REGISTRATION_CANONICAL_SHA256,
        "schedule_policy_ref": policy["policy_ref"],
        "policy_sha256": policy["policy_sha256"],
        "vendor_id": policy["vendor_id"],
        "vendor_name": vendor_name,
        "price_scope_key": policy["price_scope_key"],
        "currency": policy["currency"],
        "source_period_label": fixture["source_period_label"],
        "source_valid_from": source_from.isoformat(),
        "source_valid_through": source_through.isoformat(),
        "source_validity_basis": fixture["source_validity_basis"],
        "supplier_verified_at": fixture["supplier_verified_at"],
        "operational_effective_from": effective_from.isoformat(),
        "operational_effective_through": effective_through.isoformat(),
    }
    return {**declaration, "declaration_sha256": _sha256(declaration)}


def registered_declaration_preview(
    conn: Any, *, vendor_name: str, effective_from: date, effective_through: date
) -> dict[str, Any]:
    require_attested_database(conn)
    return _registered_declaration(
        conn,
        vendor_name=vendor_name,
        effective_from=effective_from,
        effective_through=effective_through,
    )


def registered_target_declaration(conn: Any) -> dict[str, Any]:
    """Return the sole server-registered replacement declaration."""

    fixture = _fixture()
    targets = [
        item
        for item in fixture["policies"]
        if item.get("target_for_replacement") is True
    ]
    if len(targets) != 1:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
        )
    return registered_declaration_preview(
        conn,
        vendor_name=targets[0]["vendor_name"],
        effective_from=date.fromisoformat(fixture["source_valid_from"]),
        effective_through=date.fromisoformat(fixture["source_valid_through"]),
    )


def registered_monday_evaluation_at(conn: Any) -> datetime:
    """Return the sole server-owned Monday instant for this fabricated fixture."""

    require_attested_database(conn)
    fixture = _fixture()
    targets = [
        item for item in fixture["policies"] if item.get("target_for_replacement") is True
    ]
    if len(targets) != 1:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
        )
    policy = _registered_policy_evidence(
        conn, vendor_name=str(targets[0]["vendor_name"])
    )
    return policy["monday_evaluation_at"]


def _registered_observation_operational_status(
    *,
    durable_status: str,
    effective_from: date,
    observation_at: datetime,
    policy_timezone: str,
) -> str:
    """Evaluate declared FUTURE timing without consulting the host clock."""

    try:
        observation_local = observation_at.astimezone(ZoneInfo(policy_timezone))
    except (AttributeError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
        ) from exc
    registered_effective_month = (
        date(observation_local.year + 1, 1, 1)
        if observation_local.month == 12
        else date(observation_local.year, observation_local.month + 1, 1)
    )
    if (
        durable_status in {"VALIDATED", "VERIFIED_FUTURE"}
        and effective_from.replace(day=1) != registered_effective_month
    ):
        return "TEMPORAL_BLOCKED"
    return durable_status


def _apply_registered_observation_projection(
    conn: Any,
    batch: dict[str, Any],
    declared: Mapping[str, Any],
) -> dict[str, Any]:
    """Cross-bind one declared row and derive its temporal status from policy."""

    policy = _registered_policy_evidence(
        conn, vendor_name=str(batch["vendor_name"])
    )
    declaration = _registered_declaration(
        conn,
        vendor_name=str(batch["vendor_name"]),
        effective_from=batch["effective_from"],
        effective_through=batch["effective_through"],
    )
    fixture = policy["fixture"]
    expected = {
        "replacement_contract": CONTRACT,
        "schedule_policy_ref": policy["policy_ref"],
        "price_scope_key": policy["price_scope_key"],
        "source_period_label": fixture["source_period_label"],
        "source_valid_from": date.fromisoformat(fixture["source_valid_from"]),
        "source_valid_through": date.fromisoformat(fixture["source_valid_through"]),
        "source_validity_basis": fixture["source_validity_basis"],
        "supplier_verified_at": policy["observation_at"],
        "operational_effective_from": date.fromisoformat(
            fixture["source_valid_from"]
        ),
        "operational_effective_through": date.fromisoformat(
            fixture["source_valid_through"]
        ),
        "declaration_sha256": declaration["declaration_sha256"],
        "vendor_id": policy["vendor_id"],
    }
    if any(declared.get(key) != value for key, value in expected.items()):
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
        )
    application_local = policy["application_at"].astimezone(
        ZoneInfo(policy["policy_timezone"])
    )
    if (
        batch["effective_from"] != application_local.date()
        or batch["effective_from"].day != policy["effective_boundary_day"]
        or batch["effective_through"]
        != date.fromisoformat(fixture["source_valid_through"])
    ):
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
    )
    batch.update(declared)
    batch["operational_status"] = _registered_observation_operational_status(
        durable_status=str(batch["status"]),
        effective_from=batch["effective_from"],
        observation_at=policy["observation_at"],
        policy_timezone=policy["policy_timezone"],
    )
    batch["temporal_basis"] = _REGISTERED_OBSERVATION_BASIS
    batch["registered_observation_at"] = policy["observation_at"].isoformat()
    batch["registered_application_at"] = policy["application_at"].isoformat()
    batch["registered_monday_evaluation_at"] = policy[
        "monday_evaluation_at"
    ].isoformat()
    batch["policy_timezone"] = policy["policy_timezone"]
    batch["policy_sha256"] = policy["policy_sha256"]
    return batch


def list_declared_price_book_batches(conn: Any) -> list[dict[str, Any]]:
    """List batches while evaluating declared rows only at registered time."""

    require_attested_database(conn)
    rows = conn.execute(
        f'''SELECT price_book_batch_id,batch_generation,vendor_name,batch_ref,
                   target_price_state,effective_from,effective_through,status,
                   CASE WHEN replacement_contract IS NULL
                              AND status IN ('VALIDATED','VERIFIED_FUTURE')
                              AND date_trunc('month',effective_from)::date <>
                                  (date_trunc('month',(clock_timestamp() AT TIME ZONE
                                     'America/New_York')) + interval '1 month')::date
                        THEN 'TEMPORAL_BLOCKED' ELSE status END,
                   row_count,valid_row_count,error_count,warning_count,
                   expected_offer_count,covered_offer_count,missing_offer_count,
                   validation_fingerprint,future_predecessor_sha256,
                   future_predecessor_batch_id,staged_at,promoted_at,disposition_at
              FROM "{SCHEMA}".price_book_batches
             ORDER BY staged_at DESC,price_book_batch_id DESC'''
    ).fetchall()
    base_keys = (
        "price_book_batch_id", "batch_generation", "vendor_name", "batch_ref",
        "target_price_state", "effective_from", "effective_through", "status",
        "operational_status", "row_count", "valid_row_count", "error_count",
        "warning_count", "expected_offer_count", "covered_offer_count",
        "missing_offer_count", "validation_fingerprint",
        "future_predecessor_sha256", "future_predecessor_batch_id", "staged_at",
        "promoted_at", "disposition_at",
    )
    batches = [dict(zip(base_keys, row, strict=True)) for row in rows]
    declared_rows = conn.execute(
        f'''SELECT price_book_batch_id::text,replacement_contract,
                   schedule_policy_ref,price_scope_key,source_period_label,
                   source_valid_from,source_valid_through,source_validity_basis,
                   supplier_verified_at,operational_effective_from,
                   operational_effective_through,scope_membership_sha256,
                   declaration_sha256,vendor_id::text
              FROM "{SCHEMA}".price_book_batches
             WHERE replacement_contract IS NOT NULL'''
    ).fetchall()
    keys = (
        "replacement_contract",
        "schedule_policy_ref",
        "price_scope_key",
        "source_period_label",
        "source_valid_from",
        "source_valid_through",
        "source_validity_basis",
        "supplier_verified_at",
        "operational_effective_from",
        "operational_effective_through",
        "scope_membership_sha256",
        "declaration_sha256",
        "vendor_id",
    )
    declared = {
        str(row[0]): dict(zip(keys, row[1:], strict=True))
        for row in declared_rows
    }
    for batch in batches:
        details = declared.get(str(batch["price_book_batch_id"]))
        if details is not None:
            _apply_registered_observation_projection(conn, batch, details)
    return batches


def _declared_validation(
    conn: Any, parsed: dict[str, Any], declaration: Mapping[str, Any]
) -> dict[str, Any]:
    validation = _database_validation(conn, parsed)
    current_structure = sorted(
        (
            int(row[0]),
            str(row[1]),
            (
                None
                if row[2] is None
                else format(Decimal(row[2]).normalize(), "f")
            ),
            row[3],
        )
        for row in conn.execute(
            """SELECT p.offer_id,p.level_type,p.break_qty,p.break_unit
                 FROM prices p JOIN supplier_offers o USING(offer_id)
                WHERE o.vendor_id=%s AND p.price_state='current' AND p.verified
                ORDER BY p.offer_id,p.level_type,p.break_qty NULLS FIRST,
                         p.break_unit NULLS FIRST""",
            (validation["vendor_id"],),
        ).fetchall()
    )
    candidate_structure = sorted(
        (
            int(row["offer_id"]),
            str(row["level_type"]),
            (
                None
                if row["break_quantity"] is None
                else format(Decimal(row["break_quantity"]).normalize(), "f")
            ),
            row["break_unit"],
        )
        for row in validation["rows"]
        if row["offer_id"] is not None and row["level_type"] is not None
    )
    structure_evidence = {
        "contract": "SYNTHETIC_COMPLETE_VENDOR_LADDER_STRUCTURE_V1",
        "current": current_structure,
        "candidate": candidate_structure,
    }
    if candidate_structure != current_structure:
        validation["issues"].append(
            {
                "code": "DECLARED_COMPLETE_LADDER_STRUCTURE_REQUIRED",
                "severity": "ERROR",
                "message": (
                    "declared complete-vendor replacement must retain every "
                    "existing offer/tier structural key"
                ),
                "source_row_number": None,
                "vendor_id": validation["vendor_id"],
                "variant_id": None,
                "offer_id": None,
            }
        )
        validation["issues"].sort(
            key=lambda item: (
                item["severity"],
                item["source_row_number"] or -1,
                item["offer_id"] or -1,
                item["code"],
            )
        )
        validation["error_count"] += 1
        validation["validation_evidence"]["error_codes"] = sorted(
            {
                *validation["validation_evidence"]["error_codes"],
                "DECLARED_COMPLETE_LADDER_STRUCTURE_REQUIRED",
            }
        )
    strict_fingerprint = validation["validation_fingerprint"]
    fingerprint = _sha256(
        {
            "contract": "STRICT_NORMALIZED_DECLARED_PRICE_BOOK_V1",
            "strict_v1_validation_fingerprint": strict_fingerprint,
            "declaration_sha256": declaration["declaration_sha256"],
            "complete_ladder_structure_sha256": _sha256(structure_evidence),
        }
    )
    validation["validation_fingerprint"] = fingerprint
    validation["validation_evidence"] = {
        **validation["validation_evidence"],
        "contract": "STRICT_NORMALIZED_DECLARED_PRICE_BOOK_V1",
        "strict_v1_validation_fingerprint": strict_fingerprint,
        "declaration_sha256": declaration["declaration_sha256"],
        "complete_ladder_structure_sha256": _sha256(structure_evidence),
    }
    return validation


def _verified_storage_write_once(
    storage: StorageAdapter, key: str, data: bytes
) -> None:
    publish = getattr(storage, "put_bytes_once", None)
    if not callable(publish):
        raise SyntheticPriceReplacementError(
            "DECLARED_PRICE_IMMUTABLE_STORAGE_REQUIRED"
        )
    try:
        publish(key, data)
    except FileExistsError:
        pass
    read_once = getattr(storage, "read_bytes_once", None)
    if not callable(read_once):
        raise SyntheticPriceReplacementError(
            "DECLARED_PRICE_IMMUTABLE_STORAGE_REQUIRED"
        )
    try:
        observed = read_once(
            key,
            hashlib.sha256(data).hexdigest(),
            len(data),
        )
    except (OSError, ValueError) as exc:
        raise SyntheticPriceReplacementError(
            "DECLARED_PRICE_IMMUTABLE_STORAGE_DIFFERS"
        ) from exc
    if observed != data:
        raise SyntheticPriceReplacementError(
            "DECLARED_PRICE_IMMUTABLE_STORAGE_DIFFERS"
        )


def _verified_operator_storage_read(
    storage: StorageAdapter,
    key: str,
    expected_sha256: str,
) -> bytes:
    read_once = getattr(storage, "read_bytes_once", None)
    if not callable(read_once):
        raise SyntheticPriceReplacementError(
            "DECLARED_PRICE_IMMUTABLE_STORAGE_REQUIRED"
        )
    try:
        return read_once(
            key,
            expected_sha256,
            REGISTERED_OPERATOR_BOOK_BYTES,
        )
    except (OSError, ValueError) as exc:
        raise SyntheticPriceReplacementError(
            "DECLARED_PRICE_IMMUTABLE_STORAGE_DIFFERS"
        ) from exc


def _verified_declared_raw_read(
    storage: StorageAdapter,
    key: str,
    expected_sha256: str,
    *,
    staged_by: str,
    validation_evidence: Any,
) -> bytes:
    observed = (
        validation_evidence.get("stopped_service_operator_stage")
        if isinstance(validation_evidence, dict)
        else None
    )
    operator_staged = staged_by == STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL
    if operator_staged != isinstance(observed, dict):
        raise SyntheticPriceReplacementError(
            "stopped-service price-stage evidence differs"
        )
    if operator_staged:
        if (
            key
            != f"price-books/raw/{REGISTERED_OPERATOR_BOOK_SHA256}.csv"
            or expected_sha256 != REGISTERED_OPERATOR_BOOK_SHA256
            or observed.get("contract")
            != STOPPED_SERVICE_PRICE_STAGE_CONTRACT
            or observed.get("source_ref") != REGISTERED_OPERATOR_BOOK_REF
            or observed.get("source_bytes") != REGISTERED_OPERATOR_BOOK_BYTES
            or observed.get("raw_sha256")
            != REGISTERED_OPERATOR_BOOK_SHA256
        ):
            raise SyntheticPriceReplacementError(
                "stopped-service price-stage evidence differs"
            )
        return _verified_operator_storage_read(
            storage,
            key,
            expected_sha256,
        )
    return _verified_storage_read(storage, key, expected_sha256)


def _operator_stage_evidence(
    *,
    principal: Principal,
    declaration_sha256: str,
    validation_fingerprint: str,
    proposed_scope_membership_sha256: str,
    staging_rows_sha256: str,
    validation_issues_sha256: str,
) -> dict[str, Any]:
    from .synthetic_staging_database import EXPECTED_RUNTIME_ATTESTATION_IDENTITY

    evidence = {
        "contract": STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
        "source_ref": REGISTERED_OPERATOR_BOOK_REF,
        "source_bytes": REGISTERED_OPERATOR_BOOK_BYTES,
        "raw_sha256": REGISTERED_OPERATOR_BOOK_SHA256,
        "principal_ref": principal.principal_ref,
        "role_ref": principal.role_ref,
        "authn_context_sha256": principal.authn_context_sha256,
        "declaration_sha256": declaration_sha256,
        "validation_fingerprint": validation_fingerprint,
        "proposed_scope_membership_sha256": (
            proposed_scope_membership_sha256
        ),
        "staging_rows_sha256": staging_rows_sha256,
        "validation_issues_sha256": validation_issues_sha256,
        "target_attestation_sha256": EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        "commercial_authority": False,
        "real_price_approval": False,
    }
    return {**evidence, "evidence_sha256": _sha256(evidence)}


def _operator_validation_evidence(
    validation: Mapping[str, Any],
    *,
    principal: Principal,
    declaration_sha256: str,
    proposed_scope_membership_sha256: str,
    staging_rows_sha256: str,
    validation_issues_sha256: str,
) -> dict[str, Any]:
    operator = _operator_stage_evidence(
        principal=principal,
        declaration_sha256=declaration_sha256,
        validation_fingerprint=str(validation["validation_fingerprint"]),
        proposed_scope_membership_sha256=proposed_scope_membership_sha256,
        staging_rows_sha256=staging_rows_sha256,
        validation_issues_sha256=validation_issues_sha256,
    )
    return {
        **dict(validation["validation_evidence"]),
        "proposed_scope_membership_sha256": (
            proposed_scope_membership_sha256
        ),
        "stopped_service_operator_stage": operator,
    }


def _operator_projection_hashes(conn: Any, batch_id: str) -> tuple[str, str]:
    row = conn.execute(
        """SELECT
             persistent_mapping_json_sha256(COALESCE((
               SELECT jsonb_agg(to_jsonb(s)-'price_book_staging_row_id'
                                ORDER BY s.source_row_number)
                 FROM price_book_staging_rows s
                WHERE s.price_book_batch_id=%s),'[]'::jsonb)),
             persistent_mapping_json_sha256(COALESCE((
               SELECT jsonb_agg(to_jsonb(i)-'price_book_validation_issue_id'
                                ORDER BY i.severity,i.source_row_number NULLS FIRST,
                                         i.offer_id NULLS FIRST,i.issue_code,
                                         i.vendor_id NULLS FIRST,
                                         i.variant_id NULLS FIRST,i.message)
                 FROM price_book_validation_issues i
                WHERE i.price_book_batch_id=%s),'[]'::jsonb))""",
        (batch_id, batch_id),
    ).fetchone()
    if (
        row is None
        or _SHA256.fullmatch(str(row[0])) is None
        or _SHA256.fullmatch(str(row[1])) is None
    ):
        raise SyntheticPriceReplacementError(
            "STOPPED_SERVICE_PRICE_STAGE_PROJECTION_DIFFERS"
        )
    return str(row[0]), str(row[1])


def _validation_issue_records(
    validation: Mapping[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            issue["source_row_number"],
            issue["code"],
            issue["severity"],
            None if issue["vendor_id"] is None else str(issue["vendor_id"]),
            None if issue["variant_id"] is None else str(issue["variant_id"]),
            issue["offer_id"],
            issue["message"],
            None,
        )
        for issue in validation["issues"]
    )


def _assert_operator_replay(
    conn: Any,
    storage: StorageAdapter,
    *,
    existing: Mapping[str, Any],
    parsed: Mapping[str, Any],
    declaration: Mapping[str, Any],
    validation: Mapping[str, Any],
    principal: Principal,
    raw_key: str,
    csv_bytes: bytes,
) -> str:
    batch_id = str(existing["batch_id"])
    if (
        str(existing["vendor_id"]) != str(validation["vendor_id"])
        or existing["vendor_name"] != parsed["vendor_name"]
        or existing["batch_ref"] != parsed["batch_ref"]
        or existing["content_sha256"] != parsed["content_sha256"]
        or existing["raw_storage_key"] != raw_key
        or existing["status"] != "VALIDATED"
        or existing["validation_fingerprint"]
        != validation["validation_fingerprint"]
        or existing["target_price_state"] != parsed["target_price_state"]
        or existing["effective_from"] != parsed["effective_from"]
        or existing["effective_through"] != parsed["effective_through"]
        or existing["replacement_contract"] != CONTRACT
        or existing["schedule_policy_ref"]
        != declaration["schedule_policy_ref"]
        or existing["price_scope_key"] != declaration["price_scope_key"]
        or existing["declaration_sha256"]
        != declaration["declaration_sha256"]
        or existing["future_predecessor_sha256"]
        != validation["future_predecessor_sha256"]
        or (
            None
            if existing["future_predecessor_batch_id"] is None
            else str(existing["future_predecessor_batch_id"])
        )
        != validation["future_predecessor_batch_id"]
        or existing["source_period_label"]
        != declaration["source_period_label"]
        or existing["source_valid_from"]
        != date.fromisoformat(str(declaration["source_valid_from"]))
        or existing["source_valid_through"]
        != date.fromisoformat(str(declaration["source_valid_through"]))
        or existing["source_validity_basis"]
        != declaration["source_validity_basis"]
        or existing["supplier_verified_at"].isoformat()
        != declaration["supplier_verified_at"]
        or existing["operational_effective_from"]
        != parsed["effective_from"]
        or existing["operational_effective_through"]
        != parsed["effective_through"]
        or existing["staged_by"] != principal.principal_ref
        or int(existing["row_count"]) != len(parsed["rows"])
        or int(existing["valid_row_count"])
        != int(validation["valid_row_count"])
        or int(existing["error_count"]) != 0
        or int(existing["warning_count"])
        != int(validation["warning_count"])
        or int(existing["expected_offer_count"])
        != int(validation["expected_offer_count"])
        or int(existing["covered_offer_count"])
        != int(validation["covered_offer_count"])
        or int(existing["missing_offer_count"]) != 0
        or existing["scope_membership_sha256"] is not None
        or any(
            existing[name] is not None
            for name in (
                "promoted_by",
                "promoted_at",
                "disposition_by",
                "disposition_reason",
                "disposition_at",
            )
        )
    ):
        raise SyntheticPriceReplacementError(
            "STOPPED_SERVICE_PRICE_STAGE_REPLAY_DIFFERS"
        )
    if _verified_operator_storage_read(
        storage,
        raw_key,
        str(parsed["content_sha256"]),
    ) != csv_bytes:
        raise SyntheticPriceReplacementError(
            "STOPPED_SERVICE_PRICE_STAGE_REPLAY_DIFFERS"
        )
    typed = _parsed_from_staging(
        conn,
        (
            existing["batch_id"],
            None,
            parsed["vendor_name"],
            parsed["batch_ref"],
            parsed["target_price_state"],
            parsed["effective_from"],
            parsed["content_sha256"],
            parsed["effective_through"],
            None,
            len(parsed["rows"]),
        ),
    )
    typed_validation = _declared_validation(conn, typed, declaration)
    proposed = _proposed_membership_sha256(conn, batch_id)
    staging_rows_sha256, validation_issues_sha256 = _operator_projection_hashes(
        conn, batch_id
    )
    expected_evidence = _operator_validation_evidence(
        validation,
        principal=principal,
        declaration_sha256=str(declaration["declaration_sha256"]),
        proposed_scope_membership_sha256=proposed,
        staging_rows_sha256=staging_rows_sha256,
        validation_issues_sha256=validation_issues_sha256,
    )
    actual_issues = tuple(
        (
            row[0],
            row[1],
            row[2],
            None if row[3] is None else str(row[3]),
            None if row[4] is None else str(row[4]),
            row[5],
            row[6],
            row[7],
        )
        for row in conn.execute(
            """SELECT source_row_number,issue_code,severity,vendor_id,variant_id,
                      offer_id,message,resolved_at
                 FROM price_book_validation_issues
                WHERE price_book_batch_id=%s
                ORDER BY severity,source_row_number NULLS FIRST,
                         offer_id NULLS FIRST,issue_code,
                         vendor_id NULLS FIRST,variant_id NULLS FIRST,message""",
            (batch_id,),
        ).fetchall()
    )
    downstream = conn.execute(
        """SELECT
             (SELECT count(*) FROM price_book_scope_memberships
               WHERE price_book_batch_id=%s),
             (SELECT count(*) FROM price_book_promotion_events
               WHERE price_book_batch_id=%s),
             (SELECT count(*) FROM prices
               WHERE source_price_book_batch_id=%s)""",
        (batch_id, batch_id, batch_id),
    ).fetchone()
    if (
        typed_validation["validation_fingerprint"]
        != validation["validation_fingerprint"]
        or existing["validation_evidence"] != expected_evidence
        or actual_issues != _validation_issue_records(validation)
        or downstream != (0, 0, 0)
    ):
        raise SyntheticPriceReplacementError(
            "STOPPED_SERVICE_PRICE_STAGE_REPLAY_DIFFERS"
        )
    return batch_id


def _stage_and_validate_declared_price_book(
    conn: Any,
    storage: StorageAdapter,
    *,
    csv_bytes: bytes,
    principal: Principal,
    expected_declaration_sha256: str,
    operator_stage: bool,
    operator_lock_name: str | None,
) -> dict[str, Any]:
    require_process_policy()
    principal.validate()
    if operator_stage:
        if (
            principal.principal_ref != STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL
            or principal.role_ref != STOPPED_SERVICE_PRICE_STAGE_ROLE
            or principal.authn_context_sha256
            != STOPPED_SERVICE_PRICE_STAGE_AUTHN_SHA256
        ):
            raise SyntheticPriceReplacementError("DECLARED_PRICE_PRINCIPAL_DIFFERS")
    elif principal.role_ref != "procurement.price.approve":
        raise SyntheticPriceReplacementError("DECLARED_PRICE_PRINCIPAL_DIFFERS")
    parsed = parse_price_book_csv(csv_bytes)
    if operator_stage and (
        len(csv_bytes) != REGISTERED_OPERATOR_BOOK_BYTES
        or parsed["content_sha256"] != REGISTERED_OPERATOR_BOOK_SHA256
    ):
        raise SyntheticPriceReplacementError("STOPPED_SERVICE_PRICE_SOURCE_DIFFERS")
    raw_key = f"price-books/raw/{parsed['content_sha256']}.csv"
    replay = False
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        require_attested_database(conn)
        if operator_stage:
            if not operator_lock_name:
                raise SyntheticPriceReplacementError(
                    "STOPPED_SERVICE_PRICE_STAGE_LOCK_ABSENT"
                )
            from .database_lifecycle import assert_database_lifecycle_lock
            from .synthetic_staging_database import (
                EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
                attest_runtime_connection,
                target_from_environment,
            )

            operator_target = target_from_environment(os.environ)
            if (
                attest_runtime_connection(conn, operator_target)
                != EXPECTED_RUNTIME_ATTESTATION_IDENTITY
            ):
                raise SyntheticPriceReplacementError(
                    "STOPPED_SERVICE_PRICE_STAGE_TARGET_DIFFERS"
                )
            conn.execute(
                f'SET LOCAL search_path = "{SCHEMA}",pg_catalog'
            )

            assert_database_lifecycle_lock(
                conn,
                lock_name=operator_lock_name,
            )
        elif operator_lock_name is not None:
            raise SyntheticPriceReplacementError(
                "STOPPED_SERVICE_PRICE_STAGE_LOCK_UNEXPECTED"
            )
        _transaction_lock(conn)
        declaration = _registered_declaration(
            conn,
            vendor_name=parsed["vendor_name"],
            effective_from=parsed["effective_from"],
            effective_through=parsed["effective_through"],
        )
        if expected_declaration_sha256 != declaration["declaration_sha256"]:
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_DECLARATION_HASH_DIFFERS"
            )
        validation = _declared_validation(conn, parsed, declaration)
        if operator_stage and validation["error_count"] != 0:
            raise SyntheticPriceReplacementError(
                "STOPPED_SERVICE_PRICE_STAGE_NOT_VALIDATED"
            )
        existing_row = conn.execute(
            """SELECT price_book_batch_id,vendor_id,vendor_name,batch_ref,
                      content_sha256,raw_storage_key,status,validation_fingerprint,
                      target_price_state,effective_from,effective_through,
                      replacement_contract,schedule_policy_ref,price_scope_key,
                      declaration_sha256,staged_by,validation_evidence,row_count,
                      valid_row_count,error_count,warning_count,
                      expected_offer_count,covered_offer_count,missing_offer_count,
                      scope_membership_sha256,future_predecessor_sha256,
                      future_predecessor_batch_id,source_period_label,
                      source_valid_from,source_valid_through,source_validity_basis,
                      supplier_verified_at,operational_effective_from,
                      operational_effective_through,promoted_by,promoted_at,
                      disposition_by,disposition_reason,disposition_at
                 FROM price_book_batches
                WHERE vendor_name=%s AND batch_ref=%s""",
            (parsed["vendor_name"], parsed["batch_ref"]),
        ).fetchone()
        existing_keys = (
            "batch_id", "vendor_id", "vendor_name", "batch_ref",
            "content_sha256", "raw_storage_key", "status",
            "validation_fingerprint", "target_price_state", "effective_from",
            "effective_through", "replacement_contract", "schedule_policy_ref",
            "price_scope_key", "declaration_sha256", "staged_by",
            "validation_evidence", "row_count", "valid_row_count", "error_count",
            "warning_count", "expected_offer_count", "covered_offer_count",
            "missing_offer_count", "scope_membership_sha256",
            "future_predecessor_sha256", "future_predecessor_batch_id",
            "source_period_label", "source_valid_from", "source_valid_through",
            "source_validity_basis", "supplier_verified_at",
            "operational_effective_from", "operational_effective_through",
            "promoted_by", "promoted_at", "disposition_by",
            "disposition_reason", "disposition_at",
        )
        existing = (
            None
            if existing_row is None
            else dict(zip(existing_keys, existing_row, strict=True))
        )
        if operator_stage:
            inventory = conn.execute(
                """SELECT price_book_batch_id::text
                     FROM price_book_batches
                    WHERE replacement_contract=%s
                      AND schedule_policy_ref=%s
                      AND price_scope_key=%s
                    ORDER BY price_book_batch_id""",
                (
                    CONTRACT,
                    declaration["schedule_policy_ref"],
                    declaration["price_scope_key"],
                ),
            ).fetchall()
            if (
                (existing is None and inventory)
                or (
                    existing is not None
                    and inventory != [(str(existing["batch_id"]),)]
                )
            ):
                raise SyntheticPriceReplacementError(
                    "STOPPED_SERVICE_PRICE_STAGE_INVENTORY_DIFFERS"
                )
        if existing is not None:
            if operator_stage:
                batch_id = _assert_operator_replay(
                    conn,
                    storage,
                    existing=existing,
                    parsed=parsed,
                    declaration=declaration,
                    validation=validation,
                    principal=principal,
                    raw_key=raw_key,
                    csv_bytes=csv_bytes,
                )
                replay = True
            else:
                if (
                    existing["content_sha256"] != parsed["content_sha256"]
                    or existing["target_price_state"]
                    != parsed["target_price_state"]
                    or existing["effective_from"] != parsed["effective_from"]
                    or existing["effective_through"]
                    != parsed["effective_through"]
                    or existing["replacement_contract"] != CONTRACT
                    or existing["schedule_policy_ref"]
                    != declaration["schedule_policy_ref"]
                    or existing["price_scope_key"]
                    != declaration["price_scope_key"]
                    or existing["declaration_sha256"]
                    != declaration["declaration_sha256"]
                ):
                    raise SyntheticPriceReplacementError(
                        "batch_ref already exists with different immutable declared input"
                    )
                _verified_storage_read(
                    storage,
                    existing["raw_storage_key"],
                    existing["content_sha256"],
                )
                batch_id = str(existing["batch_id"])
                replay = True
        else:
            if operator_stage:
                _verified_storage_write_once(storage, raw_key, csv_bytes)
            else:
                _verified_storage_write(storage, raw_key, csv_bytes)
            batch_id = str(
                conn.execute(
                    """INSERT INTO price_book_batches(
                           vendor_id,vendor_name,batch_ref,target_price_state,
                           effective_from,effective_through,content_sha256,
                           raw_storage_key,future_predecessor_sha256,
                           future_predecessor_batch_id,status,row_count,staged_by,
                           replacement_contract,schedule_policy_ref,price_scope_key,
                           source_period_label,source_valid_from,source_valid_through,
                           source_validity_basis,supplier_verified_at,
                           operational_effective_from,operational_effective_through,
                           declaration_sha256)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'STAGING',%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING price_book_batch_id""",
                    (
                        validation["vendor_id"],
                        parsed["vendor_name"],
                        parsed["batch_ref"],
                        parsed["target_price_state"],
                        parsed["effective_from"],
                        parsed["effective_through"],
                        parsed["content_sha256"],
                        raw_key,
                        validation["future_predecessor_sha256"],
                        validation["future_predecessor_batch_id"],
                        len(parsed["rows"]),
                        principal.principal_ref,
                        CONTRACT,
                        declaration["schedule_policy_ref"],
                        declaration["price_scope_key"],
                        declaration["source_period_label"],
                        declaration["source_valid_from"],
                        declaration["source_valid_through"],
                        declaration["source_validity_basis"],
                        declaration["supplier_verified_at"],
                        declaration["operational_effective_from"],
                        declaration["operational_effective_through"],
                        declaration["declaration_sha256"],
                    ),
                ).fetchone()[0]
            )
            for row in validation["rows"]:
                conn.execute(
                    """INSERT INTO price_book_staging_rows(
                           price_book_batch_id,source_row_number,vendor_name,
                           supplier_sku,supplier_description,canonical_variant_id,
                           offer_id,package_type,size_text,raw_pack,
                           shopify_units_per_case,qualifying_units_per_case,
                           assortment_scope,assortment_group,assortable,
                           assortment_evidence,level_type,break_quantity,break_unit,
                           case_price,unit_price,source_file,source_page,
                           source_evidence,extraction_confidence,review_note,
                           validation_status,validation_errors,validation_warnings,
                           raw_payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                               %s::jsonb)""",
                    (
                        batch_id,
                        row["source_row_number"],
                        row["vendor_name"],
                        row["supplier_sku"],
                        row["supplier_description"],
                        row["canonical_variant_id"],
                        row["offer_id"],
                        row["package_type"],
                        row["size_text"],
                        row["raw_pack"],
                        row["shopify_units_per_case"],
                        row["qualifying_units_per_case"],
                        row["assortment_scope"],
                        row["assortment_group"],
                        row["assortable"],
                        row["assortment_evidence"],
                        row["level_type"],
                        row["break_quantity"],
                        row["break_unit"],
                        row["case_price"],
                        row["unit_price"],
                        row["source_file"],
                        row["source_page"],
                        row["source_evidence"],
                        row["extraction_confidence"],
                        row["review_note"],
                        "INVALID" if row["errors"] else "VALID",
                        json.dumps(row["errors"], sort_keys=True),
                        json.dumps(row["warnings"], sort_keys=True),
                        json.dumps(row["raw_payload"], sort_keys=True),
                    ),
                )
            for issue in validation["issues"]:
                conn.execute(
                    """INSERT INTO price_book_validation_issues(
                           price_book_batch_id,source_row_number,issue_code,severity,
                           vendor_id,variant_id,offer_id,message)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        batch_id,
                        issue["source_row_number"],
                        issue["code"],
                        issue["severity"],
                        issue["vendor_id"],
                        issue["variant_id"],
                        issue["offer_id"],
                        issue["message"],
                    ),
                )
            status = "INVALID" if validation["error_count"] else "VALIDATED"
            validation_evidence = dict(validation["validation_evidence"])
            if operator_stage:
                proposed = _proposed_membership_sha256(conn, batch_id)
                (
                    staging_rows_sha256,
                    validation_issues_sha256,
                ) = _operator_projection_hashes(conn, batch_id)
                validation_evidence = _operator_validation_evidence(
                    validation,
                    principal=principal,
                    declaration_sha256=str(declaration["declaration_sha256"]),
                    proposed_scope_membership_sha256=proposed,
                    staging_rows_sha256=staging_rows_sha256,
                    validation_issues_sha256=validation_issues_sha256,
                )
            conn.execute(
                """UPDATE price_book_batches SET
                       status=%s,valid_row_count=%s,error_count=%s,warning_count=%s,
                       expected_offer_count=%s,covered_offer_count=%s,
                       missing_offer_count=%s,validation_fingerprint=%s,
                       validation_evidence=%s::jsonb
                   WHERE price_book_batch_id=%s""",
                (
                    status,
                    validation["valid_row_count"],
                    validation["error_count"],
                    validation["warning_count"],
                    validation["expected_offer_count"],
                    validation["covered_offer_count"],
                    validation["missing_offer_count"],
                    validation["validation_fingerprint"],
                    json.dumps(validation_evidence, sort_keys=True),
                    batch_id,
                ),
            )
    with conn.transaction():
        result = get_declared_price_book_batch(conn, batch_id)
    result["idempotent_replay"] = replay
    return result


def stage_and_validate_declared_price_book(
    conn: Any,
    storage: StorageAdapter,
    *,
    csv_bytes: bytes,
    principal: Principal,
    expected_declaration_sha256: str,
) -> dict[str, Any]:
    """Persist one browser-originated candidate; CURRENT remains unchanged."""

    principal.validate()
    if principal.role_ref != "procurement.price.approve":
        raise SyntheticPriceReplacementError("DECLARED_PRICE_PRINCIPAL_DIFFERS")
    return _stage_and_validate_declared_price_book(
        conn,
        storage,
        csv_bytes=csv_bytes,
        principal=principal,
        expected_declaration_sha256=expected_declaration_sha256,
        operator_stage=False,
        operator_lock_name=None,
    )


def get_declared_price_book_batch(conn: Any, batch_id: str) -> dict[str, Any]:
    require_attested_database(conn)
    row = conn.execute(
        f'''SELECT price_book_batch_id,batch_generation,vendor_id,vendor_name,
                   batch_ref,target_price_state,effective_from,effective_through,
                   content_sha256,raw_storage_key,future_predecessor_sha256,
                   future_predecessor_batch_id,status,status,row_count,
                   valid_row_count,error_count,warning_count,expected_offer_count,
                   covered_offer_count,missing_offer_count,validation_fingerprint,
                   validation_evidence,staged_by,staged_at,promoted_by,promoted_at,
                   disposition_by,disposition_reason,disposition_at
              FROM "{SCHEMA}".price_book_batches
             WHERE price_book_batch_id=%s''',
        (batch_id,),
    ).fetchone()
    if row is None:
        raise SyntheticPriceReplacementError("declared price-book batch is absent")
    base_keys = (
        "price_book_batch_id", "batch_generation", "vendor_id", "vendor_name",
        "batch_ref", "target_price_state", "effective_from", "effective_through",
        "content_sha256", "raw_storage_key", "future_predecessor_sha256",
        "future_predecessor_batch_id", "status", "operational_status", "row_count",
        "valid_row_count", "error_count", "warning_count", "expected_offer_count",
        "covered_offer_count", "missing_offer_count", "validation_fingerprint",
        "validation_evidence", "staged_by", "staged_at", "promoted_by",
        "promoted_at", "disposition_by", "disposition_reason", "disposition_at",
    )
    base = dict(zip(base_keys, row, strict=True))
    base["issues"] = [
        {
            "source_row_number": issue[0],
            "issue_code": issue[1],
            "severity": issue[2],
            "variant_id": issue[3],
            "offer_id": issue[4],
            "message": issue[5],
            "resolved_at": issue[6],
        }
        for issue in conn.execute(
            f'''SELECT source_row_number,issue_code,severity,variant_id,offer_id,
                       message,resolved_at
                  FROM "{SCHEMA}".price_book_validation_issues
                 WHERE price_book_batch_id=%s
                 ORDER BY severity DESC,source_row_number NULLS FIRST,
                          issue_code,offer_id NULLS FIRST''',
            (batch_id,),
        ).fetchall()
    ]
    declared = conn.execute(
        f'''SELECT replacement_contract,schedule_policy_ref,price_scope_key,
                  source_period_label,source_valid_from,source_valid_through,
                  source_validity_basis,supplier_verified_at,
                  operational_effective_from,operational_effective_through,
                  scope_membership_sha256,declaration_sha256
             FROM "{SCHEMA}".price_book_batches WHERE price_book_batch_id=%s''',
        (batch_id,),
    ).fetchone()
    if declared is None or declared[0] != CONTRACT:
        raise SyntheticPriceReplacementError("declared price-book batch is absent")
    keys = (
        "replacement_contract",
        "schedule_policy_ref",
        "price_scope_key",
        "source_period_label",
        "source_valid_from",
        "source_valid_through",
        "source_validity_basis",
        "supplier_verified_at",
        "operational_effective_from",
        "operational_effective_through",
        "scope_membership_sha256",
        "declaration_sha256",
    )
    details = dict(zip(keys, declared, strict=True))
    details["vendor_id"] = str(base["vendor_id"])
    _apply_registered_observation_projection(conn, base, details)
    staged_tiers = [
        {
            "source_row_number": row[0],
            "supplier_sku": row[1],
            "offer_id": row[2],
            "variant_id": row[3],
            "level_type": row[4],
            "break_quantity": row[5],
            "break_unit": row[6],
            "case_price": row[7],
            "unit_price": row[8],
            "source_file": row[9],
            "source_page": row[10],
        }
        for row in conn.execute(
            """SELECT source_row_number,supplier_sku,offer_id,canonical_variant_id,
                      level_type,break_quantity,break_unit,case_price,unit_price,
                      source_file,source_page
                 FROM price_book_staging_rows
                WHERE price_book_batch_id=%s ORDER BY source_row_number""",
            (batch_id,),
        ).fetchall()
    ]
    if staged_tiers:
        base["tiers"] = staged_tiers
    else:
        base["tiers"] = [
            {
                "source_row_number": row[0],
                "supplier_sku": row[1],
                "offer_id": row[2],
                "variant_id": row[3],
                "level_type": row[4],
                "break_quantity": row[5],
                "break_unit": row[6],
                "case_price": row[7],
                "unit_price": row[8],
                "source_file": row[9],
                "source_page": row[10],
            }
            for row in conn.execute(
                """SELECT p.source_price_book_row_number,o.supplier_sku,p.offer_id,
                          o.variant_id,p.level_type,p.break_qty,p.break_unit,
                          p.case_price,p.unit_price,p.source_file,p.source_page
                     FROM prices p JOIN supplier_offers o USING(offer_id)
                    WHERE p.source_price_book_batch_id=%s
                    ORDER BY p.source_price_book_row_number""",
                (batch_id,),
            ).fetchall()
        ]
    return base


def _verify_declared_replay_boundary(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    accepted_statuses: frozenset[str],
) -> dict[str, Any]:
    """Re-attest policy, declaration, and immutable raw before replay success."""

    observed = get_declared_price_book_batch(conn, batch_id)
    if observed["status"] not in accepted_statuses:
        raise SyntheticPriceReplacementError("declared replay state differs")
    _verified_declared_raw_read(
        storage,
        observed["raw_storage_key"],
        observed["content_sha256"],
        staged_by=observed["staged_by"],
        validation_evidence=observed["validation_evidence"],
    )
    return observed


def _set_price_context(conn: Any, principal: Principal, capability: str) -> None:
    _set_human_context(
        conn,
        principal,
        action=(
            "PRICE_REPLACEMENT_CONFIRM"
            if capability == "synthetic_price_confirmation_enabled"
            else "PRICE_REPLACEMENT_APPLY"
        ),
        capability=capability,
    )
    conn.execute(
        "SELECT set_config('procurement.synthetic_price_replacement','enabled',true)"
    )


def _proposed_membership_sha256(conn: Any, batch_id: str) -> str:
    return str(
        conn.execute(
            """SELECT persistent_mapping_json_sha256(COALESCE(jsonb_agg(
                       jsonb_build_array(
                         s.source_row_number,o.vendor_id,'COMPLETE_VENDOR',o.offer_id,
                         o.variant_id,o.supplier_sku,o.package_type,o.size_text,o.raw_pack,
                         o.shopify_units_per_case,o.qualifying_units_per_case,
                         s.level_type,s.break_quantity,s.break_unit,s.source_file,
                         s.source_page,persistent_mapping_json_sha256(s.raw_payload)
                       ) ORDER BY s.source_row_number),'[]'::jsonb))
                  FROM price_book_staging_rows s
                  JOIN supplier_offers o ON o.offer_id=s.offer_id
                 WHERE s.price_book_batch_id=%s AND s.validation_status='VALID'""",
            (batch_id,),
        ).fetchone()[0]
    )


def _require_operator_stage_evidence_for_confirmation(
    conn: Any,
    batch_id: str,
    validation_evidence: Any,
    *,
    declaration_sha256: str,
    validation_fingerprint: str,
    proposed_scope_membership_sha256: str,
    staged_by: str,
) -> None:
    if not isinstance(validation_evidence, dict):
        raise SyntheticPriceReplacementError(
            "stopped-service price-stage evidence differs"
        )
    observed = validation_evidence.get("stopped_service_operator_stage")
    operator_staged = staged_by == STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL
    if operator_staged != isinstance(observed, dict):
        raise SyntheticPriceReplacementError(
            "stopped-service price-stage evidence differs"
        )
    if not operator_staged:
        return
    staging_rows_sha256, validation_issues_sha256 = _operator_projection_hashes(
        conn, batch_id
    )
    expected = _operator_stage_evidence(
        principal=Principal(
            principal_ref=STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL,
            role_ref=STOPPED_SERVICE_PRICE_STAGE_ROLE,
            authn_context_sha256=STOPPED_SERVICE_PRICE_STAGE_AUTHN_SHA256,
        ),
        declaration_sha256=declaration_sha256,
        validation_fingerprint=validation_fingerprint,
        proposed_scope_membership_sha256=proposed_scope_membership_sha256,
        staging_rows_sha256=staging_rows_sha256,
        validation_issues_sha256=validation_issues_sha256,
    )
    if (
        observed != expected
        or validation_evidence.get("proposed_scope_membership_sha256")
        != proposed_scope_membership_sha256
    ):
        raise SyntheticPriceReplacementError(
            "stopped-service price-stage evidence differs"
        )


def _confirmation_preview_in_transaction(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    confirmation_idempotency_key: str,
    warning_review_reason: str | None,
    principal: Principal,
) -> dict[str, Any]:
    batch = conn.execute(
        """SELECT price_book_batch_id,vendor_id,vendor_name,batch_ref,
                  target_price_state,effective_from,content_sha256,effective_through,
                  validation_fingerprint,row_count,status,expected_offer_count,
                  covered_offer_count,missing_offer_count,raw_storage_key,warning_count,
                  validation_evidence,batch_generation,future_predecessor_sha256,
                  future_predecessor_batch_id,replacement_contract,schedule_policy_ref,
                  price_scope_key,source_period_label,source_valid_from,
                  source_valid_through,source_validity_basis,supplier_verified_at,
                  operational_effective_from,operational_effective_through,
                  scope_membership_sha256,declaration_sha256,staged_by
             FROM price_book_batches
            WHERE price_book_batch_id=%s FOR UPDATE""",
        (batch_id,),
    ).fetchone()
    if batch is None or batch[20] != CONTRACT:
        raise SyntheticPriceReplacementError("declared price-book batch is absent")
    if batch[10] not in {"VALIDATED", "VERIFIED_FUTURE"}:
        raise SyntheticPriceReplacementError(
            "only a complete declared VALIDATED batch can be confirmed"
        )
    if batch[13] != 0:
        raise SyntheticPriceReplacementError("declared price-book scope is incomplete")
    review_reason = (
        warning_review_reason.strip()
        if isinstance(warning_review_reason, str)
        else None
    )
    if review_reason == "":
        review_reason = None
    if (batch[15] > 0) != (review_reason is not None):
        raise SyntheticPriceReplacementError(
            "warning acknowledgement does not match declared batch"
        )
    raw = _verified_declared_raw_read(
        storage,
        batch[14],
        batch[6],
        staged_by=batch[32],
        validation_evidence=batch[16],
    )
    parsed = parse_price_book_csv(raw)
    declaration = _registered_declaration(
        conn,
        vendor_name=batch[2],
        effective_from=batch[5],
        effective_through=batch[7],
    )
    if declaration["declaration_sha256"] != batch[31]:
        raise SyntheticPriceReplacementError("declared price-book declaration changed")
    temporal = _apply_registered_observation_projection(
        conn,
        {
            "vendor_name": str(batch[2]),
            "effective_from": batch[5],
            "effective_through": batch[7],
            "status": str(batch[10]),
        },
        {
            "replacement_contract": batch[20],
            "schedule_policy_ref": batch[21],
            "price_scope_key": batch[22],
            "source_period_label": batch[23],
            "source_valid_from": batch[24],
            "source_valid_through": batch[25],
            "source_validity_basis": batch[26],
            "supplier_verified_at": batch[27],
            "operational_effective_from": batch[28],
            "operational_effective_through": batch[29],
            "scope_membership_sha256": batch[30],
            "declaration_sha256": batch[31],
            "vendor_id": str(batch[1]),
        },
    )
    if temporal["operational_status"] not in {"VALIDATED", "VERIFIED_FUTURE"}:
        raise SyntheticPriceReplacementError(
            "only a complete declared VALIDATED batch can be confirmed"
        )
    validation = _declared_validation(conn, parsed, declaration)
    typed = _parsed_from_staging(conn, batch)
    typed_validation = _declared_validation(conn, typed, declaration)
    if (
        validation["validation_fingerprint"] != batch[8]
        or typed_validation["validation_fingerprint"] != batch[8]
        or validation["error_count"] != 0
        or typed_validation["error_count"] != 0
        or validation["future_predecessor_sha256"] != batch[18]
        or validation["future_predecessor_batch_id"]
        != (str(batch[19]) if batch[19] is not None else None)
        or _future_generation_fingerprint(conn, str(batch[1])) != batch[18]
        or _future_predecessor_batch_id(conn, str(batch[1]))
        != (str(batch[19]) if batch[19] is not None else None)
    ):
        raise SyntheticPriceReplacementError(
            "locked declared price-book revalidation differs"
        )
    membership_sha256 = _proposed_membership_sha256(conn, batch_id)
    _require_operator_stage_evidence_for_confirmation(
        conn,
        batch_id,
        batch[16],
        declaration_sha256=str(batch[31]),
        validation_fingerprint=str(batch[8]),
        proposed_scope_membership_sha256=membership_sha256,
        staged_by=str(batch[32]),
    )
    current_rows = conn.execute(
        """SELECT o.supplier_sku,p.level_type,p.break_qty,p.break_unit,
                  p.case_price,p.unit_price,p.price_id
             FROM prices p JOIN supplier_offers o USING(offer_id)
            WHERE o.vendor_id=%s AND p.price_state='current'
            ORDER BY o.supplier_sku,(p.level_type<>'BASE'),p.break_qty NULLS FIRST""",
        (batch[1],),
    ).fetchall()
    tiers = [
        {
            "source_row_number": int(row["source_row_number"]),
            "supplier_sku": row["supplier_sku"],
            "supplier_description": row["supplier_description"],
            "offer_id": int(row["offer_id"]),
            "variant_id": row["canonical_variant_id"],
            "package_type": row["package_type"],
            "size_text": row["size_text"],
            "raw_pack": row["raw_pack"],
            "shopify_units_per_case": format(
                Decimal(row["shopify_units_per_case"]), "f"
            ),
            "qualifying_units_per_case": format(
                Decimal(row["qualifying_units_per_case"]), "f"
            ),
            "assortment_scope": row["assortment_scope"],
            "assortment_group": row["assortment_group"],
            "assortable": row["assortable"],
            "level_type": row["level_type"],
            "break_quantity": (
                None
                if row["break_quantity"] is None
                else format(Decimal(row["break_quantity"]), "f")
            ),
            "break_unit": row["break_unit"],
            "case_price": (
                None
                if row["case_price"] is None
                else format(Decimal(row["case_price"]), "f")
            ),
            "unit_price": format(Decimal(row["unit_price"]), "f"),
            "source_file": row["source_file"],
            "source_page": row["source_page"],
            "source_evidence": row["source_evidence"],
            "extraction_confidence": row["extraction_confidence"],
            "review_note": row["review_note"],
        }
        for row in typed_validation["rows"]
    ]
    intent = {
        "contract": "BUFFALO_SYNTHETIC_PRICE_CONFIRMATION_PREVIEW_V1",
        "price_book_batch_id": str(batch[0]),
        "confirmation_idempotency_key": str(confirmation_idempotency_key),
        "principal_ref": principal.principal_ref,
        "role_ref": principal.role_ref,
        "raw_content_sha256": batch[6],
        "validation_fingerprint": batch[8],
        "declaration_sha256": batch[31],
        "policy_ref": batch[21],
        "policy_sha256": temporal["policy_sha256"],
        "scope_membership_sha256": membership_sha256,
        "predecessor_future_sha256": batch[18],
        "predecessor_batch_id": (
            str(batch[19]) if batch[19] is not None else None
        ),
        "warning_review_reason": review_reason,
        "declaration": declaration,
        "candidate_tiers": tiers,
        "current_tiers": [
            [
                row[0],
                row[1],
                None if row[2] is None else format(Decimal(row[2]), "f"),
                row[3],
                None if row[4] is None else format(Decimal(row[4]), "f"),
                format(Decimal(row[5]), "f"),
                int(row[6]),
            ]
            for row in current_rows
        ],
        "observation_at": temporal["registered_observation_at"],
        "application_at": temporal["registered_application_at"],
        "monday_evaluation_at": temporal["registered_monday_evaluation_at"],
        "policy_timezone": temporal["policy_timezone"],
        "temporal_basis": temporal["temporal_basis"],
        "commercial_authority": False,
        "real_price_approval": False,
    }
    return {**intent, "preview_sha256": _sha256(intent)}


def preview_declared_price_confirmation(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    confirmation_idempotency_key: str,
    warning_review_reason: str | None,
    principal: Principal,
) -> dict[str, Any]:
    require_process_policy()
    principal.validate()
    if principal.role_ref != "procurement.price.approve":
        raise SyntheticPriceReplacementError("DECLARED_PRICE_PRINCIPAL_DIFFERS")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        require_attested_database(conn)
        _transaction_lock(conn)
        return _confirmation_preview_in_transaction(
            conn,
            storage,
            batch_id=batch_id,
            confirmation_idempotency_key=confirmation_idempotency_key,
            warning_review_reason=warning_review_reason,
            principal=principal,
        )


def confirm_declared_price_book(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    confirmation_idempotency_key: str,
    expected_preview_sha256: str,
    confirm: str,
    warning_review_reason: str | None,
    principal: Principal,
) -> dict[str, Any]:
    require_process_policy()
    principal.validate()
    if principal.role_ref != "procurement.price.approve":
        raise SyntheticPriceReplacementError("DECLARED_PRICE_PRINCIPAL_DIFFERS")
    if confirm != "CONFIRM" or not _SHA256.fullmatch(expected_preview_sha256):
        raise SyntheticPriceReplacementError("separate exact confirmation is required")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        require_attested_database(conn)
        _transaction_lock(conn)
        confirmation = {
            "contract": "BUFFALO_SYNTHETIC_PRICE_CONFIRMATION_V1",
            "preview_sha256": expected_preview_sha256,
            "confirmation": "CONFIRM",
            "principal_ref": principal.principal_ref,
            "role_ref": principal.role_ref,
            "authn_context_sha256": principal.authn_context_sha256,
        }
        confirmation_sha256 = _sha256(confirmation)
        existing = conn.execute(
            """SELECT e.price_book_batch_id::text,e.preview_sha256,
                      e.confirmation_sha256,e.scope_membership_sha256,
                      e.promoted_row_count,b.status,
                      supplier_price_membership_sha256(e.price_book_batch_id)
                 FROM price_book_promotion_events e
                 JOIN price_book_batches b USING(price_book_batch_id)
                WHERE e.confirmation_idempotency_key=%s""",
            (confirmation_idempotency_key,),
        ).fetchone()
        if existing is not None:
            if (
                existing[0] != str(batch_id)
                or existing[1] != expected_preview_sha256
                or existing[2] != confirmation_sha256
                or existing[3] != existing[6]
                or existing[5] not in {"VERIFIED_FUTURE", "APPLIED_CURRENT"}
            ):
                raise SyntheticPriceReplacementError(
                    "price confirmation idempotency conflict"
                )
            _verify_declared_replay_boundary(
                conn,
                storage,
                batch_id=str(batch_id),
                accepted_statuses=frozenset(
                    {"VERIFIED_FUTURE", "APPLIED_CURRENT"}
                ),
            )
            return {
                "price_book_batch_id": str(batch_id),
                "status": existing[5],
                "idempotent_replay": True,
                "promoted_price_rows": int(existing[4]),
                "confirmation_sha256": confirmation_sha256,
            }
        preview = _confirmation_preview_in_transaction(
            conn,
            storage,
            batch_id=batch_id,
            confirmation_idempotency_key=confirmation_idempotency_key,
            warning_review_reason=warning_review_reason,
            principal=principal,
        )
        if preview["preview_sha256"] != expected_preview_sha256:
            raise SyntheticPriceReplacementError("price confirmation preview is stale")
        batch = conn.execute(
            """SELECT price_book_batch_id,vendor_id,effective_from,effective_through,
                      content_sha256,validation_fingerprint,row_count,status,
                      warning_count,validation_evidence,batch_generation,
                      future_predecessor_sha256,future_predecessor_batch_id,
                      replacement_contract,schedule_policy_ref,price_scope_key,
                      declaration_sha256,staged_by
                 FROM price_book_batches WHERE price_book_batch_id=%s FOR UPDATE""",
            (batch_id,),
        ).fetchone()
        if batch is None or batch[7] != "VALIDATED" or batch[13] != CONTRACT:
            raise SyntheticPriceReplacementError("declared batch is not confirmable")
        raw = _verified_declared_raw_read(
            storage,
            f"price-books/raw/{batch[4]}.csv",
            batch[4],
            staged_by=batch[17],
            validation_evidence=batch[9],
        )
        parsed = parse_price_book_csv(raw)
        declaration = _registered_declaration(
            conn,
            vendor_name=parsed["vendor_name"],
            effective_from=batch[2],
            effective_through=batch[3],
        )
        validation = _declared_validation(conn, parsed, declaration)
        warning_codes = sorted(validation["validation_evidence"]["warning_codes"])
        promoted_future_sha256 = _candidate_future_fingerprint(
            validation,
            batch[2],
            batch[3],
        )
        promoted_semantic_md5 = conn.execute(
            "SELECT price_book_staging_semantic_md5(%s)", (batch_id,)
        ).fetchone()[0]
        _set_price_context(conn, principal, "synthetic_price_confirmation_enabled")
        conn.execute(
            """INSERT INTO price_book_scope_memberships(
                   price_book_batch_id,source_row_number,vendor_id,price_scope_key,
                   offer_id,variant_id,supplier_sku,package_type,size_text,raw_pack,
                   shopify_units_per_case,qualifying_units_per_case,level_type,
                   break_qty,break_unit,source_file,source_page,source_row_sha256)
               SELECT s.price_book_batch_id,s.source_row_number,o.vendor_id,%s,
                      o.offer_id,o.variant_id,o.supplier_sku,o.package_type,o.size_text,
                      o.raw_pack,o.shopify_units_per_case,o.qualifying_units_per_case,
                      s.level_type,s.break_quantity,s.break_unit,s.source_file,s.source_page,
                      persistent_mapping_json_sha256(s.raw_payload)
                 FROM price_book_staging_rows s JOIN supplier_offers o ON o.offer_id=s.offer_id
                WHERE s.price_book_batch_id=%s AND s.validation_status='VALID'
                ORDER BY s.source_row_number""",
            (batch[15], batch_id),
        )
        membership_sha256 = str(
            conn.execute(
                "SELECT supplier_price_membership_sha256(%s)", (batch_id,)
            ).fetchone()[0]
        )
        if membership_sha256 != preview["scope_membership_sha256"]:
            raise SyntheticPriceReplacementError("confirmed membership hash differs")
        conn.execute(
            "UPDATE price_book_batches SET scope_membership_sha256=%s "
            "WHERE price_book_batch_id=%s",
            (membership_sha256, batch_id),
        )
        event_payload = {
            "contract": "BUFFALO_SYNTHETIC_PRICE_CONFIRMATION_EVENT_V1",
            "price_book_batch_id": str(batch_id),
            "raw_content_sha256": batch[4],
            "validation_fingerprint": batch[5],
            "declaration_sha256": batch[16],
            "policy_ref": batch[14],
            "scope_membership_sha256": membership_sha256,
            "preview_sha256": preview["preview_sha256"],
            "confirmation_sha256": confirmation_sha256,
            "commercial_authority": False,
            "real_price_approval": False,
        }
        promotion_evidence = {
            "source": "LOCKED_DECLARED_PRICE_REVALIDATION",
            "content_sha256": batch[4],
            "validation_fingerprint": batch[5],
            "vendor_id": str(batch[1]),
            "target_price_state": "future",
            "predecessor_future_sha256": batch[11],
            "predecessor_batch_id": (
                str(batch[12]) if batch[12] is not None else None
            ),
            "promoted_future_sha256": promoted_future_sha256,
            "promoted_semantic_md5": promoted_semantic_md5,
            "acknowledged_warning_codes": warning_codes,
            "warning_review_reason": warning_review_reason,
            "confirmation_payload_sha256": _sha256(event_payload),
        }
        promotion_time = conn.execute(
            """INSERT INTO price_book_promotion_events(
                   price_book_batch_id,prior_status,new_status,
                   validation_fingerprint,predecessor_future_sha256,
                   predecessor_batch_id,promoted_future_sha256,
                   promoted_semantic_md5,promoted_row_count,
                   acknowledged_warning_codes,review_reason,evidence_json,recorded_by,
                   replacement_contract,confirmation_idempotency_key,
                   schedule_policy_ref,price_scope_key,human_principal_ref,
                   human_role_ref,human_authn_context_sha256,preview_sha256,
                   confirmation_sha256,confirmation_payload_sha256,
                   scope_membership_sha256,raw_content_sha256,
                   declaration_sha256,policy_sha256,recorded_at)
               VALUES (%s,'VALIDATED','VERIFIED_FUTURE',%s,%s,%s,%s,%s,%s,%s::jsonb,
                       %s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING recorded_at""",
            (
                batch_id,
                batch[5],
                batch[11],
                batch[12],
                promoted_future_sha256,
                promoted_semantic_md5,
                len(validation["rows"]),
                json.dumps(warning_codes),
                warning_review_reason,
                json.dumps(promotion_evidence, sort_keys=True),
                principal.principal_ref,
                CONTRACT,
                confirmation_idempotency_key,
                batch[14],
                batch[15],
                principal.principal_ref,
                principal.role_ref,
                principal.authn_context_sha256,
                preview["preview_sha256"],
                confirmation_sha256,
                _sha256(event_payload),
                membership_sha256,
                batch[4],
                batch[16],
                preview["policy_sha256"],
                datetime.fromisoformat(preview["observation_at"]),
            ),
        ).fetchone()[0]
        conn.execute(
            """DELETE FROM prices p USING supplier_offers o
                WHERE p.offer_id=o.offer_id AND o.vendor_id=%s
                  AND p.price_state='future'""",
            (batch[1],),
        )
        effective_month = batch[2].replace(day=1)
        for row in sorted(
            validation["rows"],
            key=lambda item: (
                int(item["offer_id"]),
                item["level_type"] != "BASE",
                item["break_unit"] or "",
                item["break_quantity"] or Decimal("0"),
                item["source_row_number"],
            ),
        ):
            conn.execute(
                """INSERT INTO prices(
                       offer_id,price_state,effective_month,level_type,break_qty,
                       break_unit,case_price,unit_price,source_file,source_page,
                       extraction_confidence,verified,notes,
                       source_price_book_batch_id,source_price_book_row_number,
                       effective_from,effective_through)
                   VALUES (%s,'future',%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s,%s)""",
                (
                    row["offer_id"],
                    effective_month,
                    row["level_type"],
                    row["break_quantity"],
                    row["break_unit"],
                    row["case_price"],
                    row["unit_price"],
                    row["source_file"],
                    row["source_page"],
                    row["extraction_confidence"],
                    row["review_note"],
                    batch_id,
                    row["source_row_number"],
                    batch[2],
                    batch[3],
                ),
            )
        inserted = int(
            conn.execute(
                "SELECT count(*) FROM prices WHERE source_price_book_batch_id=%s",
                (batch_id,),
            ).fetchone()[0]
        )
        if inserted != len(validation["rows"]):
            raise SyntheticPriceReplacementError("confirmed FUTURE row count differs")
        conn.execute(
            "UPDATE price_book_validation_issues SET resolved_at=now() "
            "WHERE price_book_batch_id=%s AND resolved_at IS NULL",
            (batch_id,),
        )
        conn.execute(
            "DELETE FROM price_book_staging_rows WHERE price_book_batch_id=%s",
            (batch_id,),
        )
        conn.execute(
            """UPDATE price_book_batches
                  SET status='VERIFIED_FUTURE',promoted_by=%s,promoted_at=%s
                WHERE price_book_batch_id=%s""",
            (principal.principal_ref, promotion_time, batch_id),
        )
    return {
        "price_book_batch_id": str(batch_id),
        "status": "VERIFIED_FUTURE",
        "idempotent_replay": False,
        "promoted_price_rows": inserted,
        "promoted_future_sha256": promoted_future_sha256,
        "scope_membership_sha256": membership_sha256,
        "confirmation_sha256": confirmation_sha256,
    }


def _prospective_current_scope_sha256(conn: Any, batch_id: str) -> str:
    row = conn.execute(
        """SELECT supplier_price_scope_batch_as_current_sha256(
                      b.vendor_id,b.price_book_batch_id)
              FROM price_book_batches b WHERE b.price_book_batch_id=%s""",
        (batch_id,),
    ).fetchone()
    if row is None or not isinstance(row[0], str) or _SHA256.fullmatch(row[0]) is None:
        raise SyntheticPriceReplacementError("replacement FUTURE scope is absent")
    return row[0]


def _reverify_bound_backup(expected: VerifiedPriceApplyBackup) -> None:
    """Close the preflight-to-lock file race before any deciding snapshot."""

    try:
        current = verify_bound_price_apply_backup()
    except LocalBackupV2Error as exc:
        raise SyntheticPriceReplacementError(
            "price APPLY recovery proof is unavailable"
        ) from exc
    if current != expected:
        raise SyntheticPriceReplacementError("price APPLY recovery proof changed")


def _apply_snapshot(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    apply_idempotency_key: str,
    principal: Principal,
    backup: VerifiedPriceApplyBackup,
    for_update: bool,
) -> dict[str, Any]:
    locking = " FOR UPDATE OF b,h" if for_update else ""
    rows = conn.execute(
        """SELECT b.price_book_batch_id::text,b.vendor_id::text,b.status,
                  b.content_sha256,b.raw_storage_key,b.validation_fingerprint,
                  b.row_count,b.effective_from,b.effective_through,
                  b.replacement_contract,b.schedule_policy_ref,b.price_scope_key,
                  b.scope_membership_sha256,b.declaration_sha256,
                  p.policy_sha256,p.policy_timezone,p.application_at,
                  p.effective_boundary_day,p.fixture_database_name,
                  e.price_book_promotion_event_id,e.promoted_future_sha256,
                  e.scope_membership_sha256,e.confirmation_payload_sha256,
                  h.supplier_price_authority_event_id::text,h.head_version,
                  h.current_scope_sha256,h.active_price_book_batch_id::text,
                  b.staged_by,b.validation_evidence,b.vendor_name
             FROM price_book_batches b
             JOIN supplier_price_schedule_policies p
               ON p.policy_ref=b.schedule_policy_ref
             JOIN price_book_promotion_events e
               ON e.price_book_batch_id=b.price_book_batch_id
              AND e.replacement_contract=b.replacement_contract
             JOIN supplier_price_authority_heads h
               ON h.vendor_id=b.vendor_id AND h.price_scope_key=b.price_scope_key
            WHERE b.price_book_batch_id=%s"""
        + locking,
        (batch_id,),
    ).fetchall()
    if len(rows) != 1:
        raise SyntheticPriceReplacementError("confirmed replacement scope is absent")
    keys = (
        "batch_id","vendor_id","status","content_sha256","raw_storage_key",
        "validation_fingerprint","row_count","effective_from","effective_through",
        "replacement_contract","policy_ref","scope_key","membership_sha256",
        "declaration_sha256","policy_sha256","policy_timezone","application_at",
        "boundary_day","fixture_database","promotion_event_id","future_sha256",
        "promotion_membership_sha256","confirmation_payload_sha256",
        "prior_event_id","head_version","current_scope_sha256","active_batch_id",
        "staged_by","validation_evidence","vendor_name",
    )
    state = dict(zip(keys, rows[0], strict=True))
    policy = _registered_policy_evidence(
        conn, vendor_name=str(state["vendor_name"])
    )
    application_local = policy["application_at"].astimezone(
        ZoneInfo(policy["policy_timezone"])
    )
    if (
        state["status"] != "VERIFIED_FUTURE"
        or state["replacement_contract"] != CONTRACT
        or state["scope_key"] != "COMPLETE_VENDOR"
        or state["fixture_database"] != conn.info.dbname
        or state["policy_ref"] != policy["policy_ref"]
        or state["vendor_id"] != policy["vendor_id"]
        or state["scope_key"] != policy["price_scope_key"]
        or state["policy_sha256"] != policy["policy_sha256"]
        or state["policy_timezone"] != policy["policy_timezone"]
        or state["application_at"] != policy["application_at"]
        or state["boundary_day"] != policy["effective_boundary_day"]
        or state["effective_from"] != application_local.date()
        or state["effective_from"].day != state["boundary_day"]
        or state["effective_through"] is None
        or not state["effective_from"] <= application_local.date() <= state["effective_through"]
        or state["membership_sha256"] != state["promotion_membership_sha256"]
        or state["membership_sha256"]
        != conn.execute(
            "SELECT supplier_price_membership_sha256(%s)", (batch_id,)
        ).fetchone()[0]
        or state["current_scope_sha256"]
        != conn.execute(
            "SELECT supplier_price_scope_current_sha256(%s)", (state["vendor_id"],)
        ).fetchone()[0]
        or state["future_sha256"]
        != _future_generation_fingerprint(conn, state["vendor_id"])
        or str(_future_predecessor_batch_id(conn, state["vendor_id"]))
        != state["batch_id"]
        or int(
            conn.execute(
                "SELECT count(*) FROM price_book_scope_memberships "
                "WHERE price_book_batch_id=%s", (batch_id,)
            ).fetchone()[0]
        ) != int(state["row_count"])
        or int(
            conn.execute(
                "SELECT count(*) FROM prices WHERE source_price_book_batch_id=%s "
                "AND price_state='future' AND verified", (batch_id,)
            ).fetchone()[0]
        ) != int(state["row_count"])
    ):
        raise SyntheticPriceReplacementError("replacement scope no longer matches confirmation")
    _verified_declared_raw_read(
        storage,
        state["raw_storage_key"],
        state["content_sha256"],
        staged_by=state["staged_by"],
        validation_evidence=state["validation_evidence"],
    )
    if (
        backup.database != conn.info.dbname
        or backup.batch_id != state["batch_id"]
        or backup.vendor_id != state["vendor_id"]
        or backup.price_scope_key != state["scope_key"]
        or backup.prior_event_id != state["prior_event_id"]
        or backup.prior_head_version != int(state["head_version"])
        or backup.raw_content_sha256 != state["content_sha256"]
        or backup.raw_storage_key != state["raw_storage_key"]
        or backup.prechange_scope_sha256 != state["current_scope_sha256"]
        or backup.migration_sha256 != MIGRATION_SHA256
        or backup.catalog_sha256 != CATALOG_SHA256
    ):
        raise SyntheticPriceReplacementError("price APPLY recovery proof differs")
    try:
        current_state = database_state_evidence(
            conn,
            schema=SCHEMA,
            staging_runtime=backup.target_kind == "staging",
        )
    except LocalBackupV2Error as exc:
        raise SyntheticPriceReplacementError(
            "price APPLY recovery proof differs"
        ) from exc
    if current_state != backup.state:
        raise SyntheticPriceReplacementError("price APPLY recovery proof differs")
    variant_ids = tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT variant_id FROM price_book_scope_memberships "
            "WHERE price_book_batch_id=%s ORDER BY variant_id", (batch_id,)
        ).fetchall()
    )
    if not variant_ids:
        raise SyntheticPriceReplacementError("replacement scope has no Variant members")
    expected_current_row_count = int(
        conn.execute(
            """SELECT count(*) FROM prices p JOIN supplier_offers o USING(offer_id)
                 WHERE o.vendor_id=%s AND p.price_state='current'""",
            (state["vendor_id"],),
        ).fetchone()[0]
    )
    resulting_current_row_count = int(
        conn.execute(
            "SELECT count(*) FROM prices WHERE source_price_book_batch_id=%s",
            (batch_id,),
        ).fetchone()[0]
    )
    unaffected_price_state_sha256 = str(
        conn.execute(
            "SELECT supplier_price_unaffected_state_sha256(%s)",
            (state["vendor_id"],),
        ).fetchone()[0]
    )
    if (
        expected_current_row_count < 1
        or resulting_current_row_count != int(state["row_count"])
        or _SHA256.fullmatch(unaffected_price_state_sha256) is None
    ):
        raise SyntheticPriceReplacementError("replacement row controls differ")
    resulting_sha256 = _prospective_current_scope_sha256(conn, batch_id)
    intent = {
        "contract": "BUFFALO_SYNTHETIC_PRICE_APPLY_PREVIEW_V1",
        "price_book_batch_id": state["batch_id"],
        "vendor_id": state["vendor_id"],
        "price_scope_key": state["scope_key"],
        "apply_idempotency_key": apply_idempotency_key,
        "prior_event_id": state["prior_event_id"],
        "expected_prior_head_version": int(state["head_version"]),
        "expected_current_scope_sha256": state["current_scope_sha256"],
        "resulting_current_scope_sha256": resulting_sha256,
        "scope_membership_sha256": state["membership_sha256"],
        "raw_content_sha256": state["content_sha256"],
        "declaration_sha256": state["declaration_sha256"],
        "policy_ref": state["policy_ref"],
        "policy_sha256": state["policy_sha256"],
        "confirmation_payload_sha256": state["confirmation_payload_sha256"],
        "price_book_promotion_event_id": int(state["promotion_event_id"]),
        "expected_current_row_count": expected_current_row_count,
        "resulting_current_row_count": resulting_current_row_count,
        "unaffected_price_state_sha256": unaffected_price_state_sha256,
        "backup_manifest_sha256": backup.manifest_sha256,
        "backup_prechange_scope_sha256": backup.prechange_scope_sha256,
        "application_at": state["application_at"].isoformat(),
        "variant_ids": list(variant_ids),
        "principal_ref": principal.principal_ref,
        "role_ref": principal.role_ref,
        "commercial_authority": False,
        "real_price_approval": False,
    }
    if backup.target_kind == "staging":
        intent.update(
            staging_backup_release_sha256=backup.staging_release_sha256,
            staging_runtime_attestation_identity=(
                backup.runtime_attestation_identity
            ),
        )
    state.update(
        variant_ids=variant_ids,
        expected_current_row_count=expected_current_row_count,
        resulting_current_row_count=resulting_current_row_count,
        unaffected_price_state_sha256=unaffected_price_state_sha256,
        resulting_scope_sha256=resulting_sha256,
        intent=intent,
        preview_sha256=_sha256(intent),
    )
    return state


@contextmanager
def _price_apply_locks(
    conn: Any,
    *,
    variant_ids: tuple[str, ...],
    vendor_id: str,
    scope_key: str,
    apply_idempotency_key: str,
    deadline: float,
):
    from .recommendations import MONDAY_ANALYSIS_LOCK

    input_locks: tuple[str, ...] = ()
    additional: list[str] = []
    acquisition_started = False
    acquired = False
    try:
        acquisition_started = True
        input_locks = acquire_input_locks(
            conn,
            variant_ids,
            global_lock_id=MONDAY_ANALYSIS_LOCK,
            operation_deadline=deadline,
        )
        for lock_name in (
            f"{_APPLY_SCOPE_LOCK_PREFIX}{vendor_id}:{scope_key}",
            f"{_APPLY_KEY_LOCK_PREFIX}{apply_idempotency_key}",
        ):
            _try_session_lock(conn, lock_name, deadline)
            additional.append(lock_name)
        conn.rollback()
        acquired = True
        yield
    except PersistentMappingError as exc:
        raise SyntheticPriceReplacementError("PRICE_REPLACEMENT_RETRY_REQUIRED") from exc
    finally:
        if acquisition_started and not acquired and not getattr(conn, "closed", False):
            conn.close()
        elif acquired and not getattr(conn, "closed", False):
            try:
                if conn.info.transaction_status.name != "IDLE":
                    conn.rollback()
                _unlock_session_locks(conn, additional)
                conn.rollback()
                release_input_locks(
                    conn, input_locks, global_lock_id=MONDAY_ANALYSIS_LOCK
                )
            except BaseException:
                conn.close()
                raise


def _attest_apply_target(
    conn: Any, backup: VerifiedPriceApplyBackup
) -> None:
    from .synthetic_staging_database import (
        STAGING_BACKUP_RELEASE_SHA256,
        SyntheticStagingDatabaseError,
        attest_runtime_connection,
        is_staging_runtime_connection,
        target_from_environment,
    )

    staging_runtime = is_staging_runtime_connection(conn)
    runtime_attestation_identity: str | None = None
    if staging_runtime:
        try:
            runtime_attestation_identity = attest_runtime_connection(
                conn, target_from_environment(os.environ)
            )
        except (SyntheticStagingDatabaseError, ValueError) as exc:
            raise SyntheticPriceReplacementError(
                "price APPLY recovery target differs"
            ) from exc
    if (
        staging_runtime
        and (
            backup.target_kind != "staging"
            or backup.staging_release_sha256
            != STAGING_BACKUP_RELEASE_SHA256
            or backup.runtime_attestation_identity
            != runtime_attestation_identity
        )
    ) or (
        not staging_runtime
        and (
            backup.target_kind != "legacy-local"
            or backup.staging_release_sha256 is not None
            or backup.runtime_attestation_identity is not None
        )
    ):
        raise SyntheticPriceReplacementError(
            "price APPLY recovery target differs"
        )


def _apply_preflight(
    conn: Any,
    *,
    batch_id: str,
    backup: VerifiedPriceApplyBackup,
) -> tuple[str, tuple[str, ...]]:
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        require_attested_database(conn)
        _attest_apply_target(conn, backup)
        rows = conn.execute(
            "SELECT vendor_id::text,variant_id FROM price_book_scope_memberships "
            "WHERE price_book_batch_id=%s ORDER BY variant_id", (batch_id,)
        ).fetchall()
        if not rows or any(str(row[0]) != backup.vendor_id for row in rows):
            raise SyntheticPriceReplacementError("replacement scope preflight differs")
        variants = tuple(sorted({str(row[1]) for row in rows}, key=lambda v: v.encode()))
    return backup.vendor_id, variants


def preview_price_replacement(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    apply_idempotency_key: str,
    principal: Principal,
) -> dict[str, Any]:
    require_process_policy()
    principal.validate()
    if principal.role_ref != "procurement.price.approve" or not apply_idempotency_key.strip():
        raise SyntheticPriceReplacementError("price APPLY principal/key differs")
    try:
        backup = verify_bound_price_apply_backup()
    except LocalBackupV2Error as exc:
        raise SyntheticPriceReplacementError("price APPLY recovery proof is unavailable") from exc
    if backup.batch_id != str(batch_id):
        raise SyntheticPriceReplacementError("price APPLY recovery batch differs")
    vendor_id, variants = _apply_preflight(conn, batch_id=batch_id, backup=backup)
    deadline = time.monotonic() + _APPLY_OPERATION_SECONDS
    with _price_apply_locks(
        conn,
        variant_ids=variants,
        vendor_id=vendor_id,
        scope_key=backup.price_scope_key,
        apply_idempotency_key=apply_idempotency_key,
        deadline=deadline,
    ):
        _reverify_bound_backup(backup)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            require_attested_database(conn)
            state = _apply_snapshot(
                conn,
                storage,
                batch_id=batch_id,
                apply_idempotency_key=apply_idempotency_key,
                principal=principal,
                backup=backup,
                for_update=False,
            )
            return {**state["intent"], "preview_sha256": state["preview_sha256"]}


def _load_apply_replay(
    conn: Any,
    *,
    batch_id: str,
    vendor_id: str,
    scope_key: str,
    apply_idempotency_key: str,
    expected_preview_sha256: str,
    confirmation_sha256: str,
    principal: Principal,
) -> dict[str, Any] | None:
    existing = conn.execute(
        """SELECT e.supplier_price_authority_event_id::text,
                  e.payload_sha256,e.resulting_current_scope_sha256,
                  e.price_book_batch_id::text,e.preview_sha256,
                  e.confirmation_sha256,e.human_principal_ref,
                  e.human_role_ref,e.human_authn_context_sha256,
                  e.evidence_json,h.supplier_price_authority_event_id::text,
                  h.active_price_book_batch_id::text,h.current_scope_sha256,
                  b.status,persistent_mapping_json_sha256(e.evidence_json),
                  supplier_price_scope_current_sha256(e.vendor_id)
             FROM supplier_price_authority_events e
             JOIN supplier_price_authority_heads h
               ON h.vendor_id=e.vendor_id AND h.price_scope_key=e.price_scope_key
             JOIN price_book_batches b ON b.price_book_batch_id=e.price_book_batch_id
            WHERE e.vendor_id=%s AND e.price_scope_key=%s
              AND e.apply_idempotency_key=%s""",
        (vendor_id, scope_key, apply_idempotency_key),
    ).fetchone()
    if existing is None:
        return None
    if (
        existing[3] != str(batch_id)
        or existing[4] != expected_preview_sha256
        or existing[5] != confirmation_sha256
        or existing[6] != principal.principal_ref
        or existing[7] != principal.role_ref
        or existing[8] != principal.authn_context_sha256
        or existing[0] != existing[10]
        or existing[3] != existing[11]
        or existing[2] != existing[12]
        or existing[13] != "APPLIED_CURRENT"
        or existing[1] != existing[14]
        or existing[2] != existing[15]
    ):
        raise SyntheticPriceReplacementError("price APPLY idempotency conflict")
    return {
        "supplier_price_authority_event_id": existing[0],
        "price_book_batch_id": str(batch_id),
        "status": "APPLIED_CURRENT",
        "idempotent_replay": True,
        "current_scope_sha256": existing[2],
    }


def _exception_sqlstate(exc: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        value = getattr(current, "sqlstate", None)
        if isinstance(value, str):
            return value
        nested = current.__cause__ or current.__context__
        current = nested if isinstance(nested, BaseException) else None
    return None


@contextmanager
def _price_apply_transaction(conn: Any):
    """Separate retryable failures from an unknowable COMMIT result."""

    transaction = conn.transaction()
    transaction.__enter__()
    try:
        yield
    except BaseException as exc:
        suppress = transaction.__exit__(type(exc), exc, exc.__traceback__)
        if not suppress:
            raise
    else:
        try:
            transaction.__exit__(None, None, None)
        except BaseException as exc:
            if _exception_sqlstate(exc) in {"40001", "40P01"}:
                raise
            ambiguous = isinstance(
                exc, (psycopg.OperationalError, psycopg.InterfaceError)
            ) or not isinstance(exc, Exception)
            if not ambiguous:
                raise
            try:
                conn.close()
            finally:
                raise SyntheticPriceReplacementError(
                    "PRICE_REPLACEMENT_COMMIT_OUTCOME_UNKNOWN"
                ) from exc


def _run_price_apply_with_retry(
    conn: Any, operation: Any, *, deadline: float
) -> dict[str, Any]:
    for attempt in range(3):
        try:
            if time.monotonic() >= deadline:
                raise SyntheticPriceReplacementError(
                    "PRICE_REPLACEMENT_RETRY_REQUIRED"
                )
            with _price_apply_transaction(conn):
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                conn.execute(f"SET LOCAL statement_timeout = '{remaining_ms}ms'")
                conn.execute(
                    f"SET LOCAL lock_timeout = '{min(5000, remaining_ms)}ms'"
                )
                result = operation()
                if time.monotonic() >= deadline:
                    raise SyntheticPriceReplacementError(
                        "PRICE_REPLACEMENT_RETRY_REQUIRED"
                    )
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                conn.execute(f"SET LOCAL statement_timeout = '{remaining_ms}ms'")
            return result
        except BaseException as exc:
            sqlstate = _exception_sqlstate(exc)
            if sqlstate in {"40001", "40P01"}:
                if attempt < 2 and time.monotonic() < deadline:
                    continue
                raise SyntheticPriceReplacementError(
                    "PRICE_REPLACEMENT_RETRY_REQUIRED"
                ) from exc
            if sqlstate in {"55P03", "57014"}:
                raise SyntheticPriceReplacementError(
                    "PRICE_REPLACEMENT_RETRY_REQUIRED"
                ) from exc
            raise
    raise SyntheticPriceReplacementError("PRICE_REPLACEMENT_RETRY_REQUIRED")


def apply_price_replacement(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    apply_idempotency_key: str,
    expected_preview_sha256: str,
    confirm: str,
    principal: Principal,
    _inject_failure_after_mutation: bool = False,
) -> dict[str, Any]:
    """Atomically replace one attested fabricated CURRENT vendor scope."""

    require_process_policy()
    principal.validate()
    if (
        principal.role_ref != "procurement.price.approve"
        or not apply_idempotency_key.strip()
        or confirm != "CONFIRM"
        or _SHA256.fullmatch(expected_preview_sha256) is None
    ):
        raise SyntheticPriceReplacementError("separate exact price APPLY confirmation is required")
    try:
        backup = verify_bound_price_apply_backup()
    except LocalBackupV2Error as exc:
        raise SyntheticPriceReplacementError("price APPLY recovery proof is unavailable") from exc
    if backup.batch_id != str(batch_id):
        raise SyntheticPriceReplacementError("price APPLY recovery batch differs")
    vendor_id, variants = _apply_preflight(conn, batch_id=batch_id, backup=backup)
    deadline = time.monotonic() + _APPLY_OPERATION_SECONDS
    with _price_apply_locks(
        conn,
        variant_ids=variants,
        vendor_id=vendor_id,
        scope_key=backup.price_scope_key,
        apply_idempotency_key=apply_idempotency_key,
        deadline=deadline,
    ):
        _reverify_bound_backup(backup)

        def operation() -> dict[str, Any]:
            _reverify_bound_backup(backup)
            require_attested_database(conn)
            _attest_apply_target(conn, backup)
            confirmation = {
                "contract": "BUFFALO_SYNTHETIC_PRICE_APPLY_CONFIRMATION_V1",
                "preview_sha256": expected_preview_sha256,
                "confirmation": "CONFIRM",
                "principal_ref": principal.principal_ref,
                "role_ref": principal.role_ref,
                "authn_context_sha256": principal.authn_context_sha256,
            }
            confirmation_sha256 = _sha256(confirmation)
            replay = _load_apply_replay(
                conn,
                batch_id=batch_id,
                vendor_id=vendor_id,
                scope_key=backup.price_scope_key,
                apply_idempotency_key=apply_idempotency_key,
                expected_preview_sha256=expected_preview_sha256,
                confirmation_sha256=confirmation_sha256,
                principal=principal,
            )
            if replay is not None:
                _verify_declared_replay_boundary(
                    conn,
                    storage,
                    batch_id=str(batch_id),
                    accepted_statuses=frozenset({"APPLIED_CURRENT"}),
                )
                return replay
            state = _apply_snapshot(
                conn,
                storage,
                batch_id=batch_id,
                apply_idempotency_key=apply_idempotency_key,
                principal=principal,
                backup=backup,
                for_update=True,
            )
            if state["preview_sha256"] != expected_preview_sha256:
                raise SyntheticPriceReplacementError("price APPLY preview is stale")
            _set_price_context(conn, principal, "synthetic_price_apply_enabled")
            event_id = str(uuid4())
            event_evidence = {
                **state["intent"],
                "contract": "BUFFALO_SYNTHETIC_PRICE_APPLY_EVENT_V1",
                "supplier_price_authority_event_id": event_id,
                "preview_sha256": state["preview_sha256"],
                "confirmation_sha256": confirmation_sha256,
                "backup_contract": "BUFFALO_LOCAL_CANDIDATE_BACKUP_V2",
                "backup_manifest_ref": backup.manifest_ref,
                "backup_manifest_sha256": backup.manifest_sha256,
                "backup_dump_sha256": backup.dump_sha256,
                "backup_storage_sha256": backup.storage_sha256,
                "backup_prechange_scope_sha256": backup.prechange_scope_sha256,
                "human_authn_context_sha256": principal.authn_context_sha256,
            }
            payload_sha256 = str(
                conn.execute(
                    "SELECT persistent_mapping_json_sha256(%s::jsonb)",
                    (json.dumps(event_evidence, sort_keys=True),),
                ).fetchone()[0]
            )
            event = conn.execute(
                """INSERT INTO supplier_price_authority_events(
                       supplier_price_authority_event_id,action,vendor_id,
                       price_scope_key,schedule_policy_ref,
                       price_book_batch_id,prior_event_id,expected_prior_head_version,
                       expected_current_scope_sha256,resulting_current_scope_sha256,
                       scope_membership_sha256,raw_content_sha256,backup_contract,
                       backup_manifest_ref,backup_manifest_sha256,backup_dump_sha256,
                       backup_storage_sha256,backup_prechange_scope_sha256,
                       apply_idempotency_key,human_principal_ref,human_role_ref,
                       human_authn_context_sha256,preview_sha256,confirmation_sha256,
                       payload_sha256,evidence_json,recorded_at)
                   VALUES (%s,'APPLY_REPLACEMENT',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           'BUFFALO_LOCAL_CANDIDATE_BACKUP_V2',%s,%s,%s,%s,%s,%s,
                           %s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   RETURNING supplier_price_authority_event_id::text""",
                (
                    event_id,vendor_id,state["scope_key"],state["policy_ref"],batch_id,
                    state["prior_event_id"],state["head_version"],
                    state["current_scope_sha256"],state["resulting_scope_sha256"],
                    state["membership_sha256"],state["content_sha256"],
                    backup.manifest_ref,backup.manifest_sha256,backup.dump_sha256,
                    backup.storage_sha256,backup.prechange_scope_sha256,
                    apply_idempotency_key,principal.principal_ref,principal.role_ref,
                    principal.authn_context_sha256,state["preview_sha256"],
                    confirmation_sha256,payload_sha256,
                    json.dumps(event_evidence, sort_keys=True),
                    state["application_at"],
                ),
            ).fetchone()[0]
            conn.execute(
                """DELETE FROM prices p USING supplier_offers o
                    WHERE p.offer_id=o.offer_id AND o.vendor_id=%s
                      AND p.price_state='current'""",
                (vendor_id,),
            )
            conn.execute(
                """UPDATE prices SET price_state='current'
                    WHERE source_price_book_batch_id=%s AND price_state='future'""",
                (batch_id,),
            )
            conn.execute(
                "UPDATE price_book_batches SET status='APPLIED_CURRENT' "
                "WHERE price_book_batch_id=%s", (batch_id,)
            )
            conn.execute(
                """UPDATE supplier_price_authority_heads SET
                         supplier_price_authority_event_id=%s,head_version=head_version+1,
                         active_price_book_batch_id=%s,current_scope_sha256=%s,
                         updated_at=%s,updated_txid=txid_current()
                    WHERE vendor_id=%s AND price_scope_key=%s""",
                (
                    event,batch_id,state["resulting_scope_sha256"],
                    state["application_at"],vendor_id,state["scope_key"],
                ),
            )
            if _inject_failure_after_mutation:
                raise SyntheticPriceReplacementError("INJECTED_PRICE_APPLY_FAILURE")
            if conn.execute(
                "SELECT supplier_price_scope_current_sha256(%s)", (vendor_id,)
            ).fetchone()[0] != state["resulting_scope_sha256"]:
                raise SyntheticPriceReplacementError("applied CURRENT scope differs")
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return {
                "supplier_price_authority_event_id": event,
                "price_book_batch_id": str(batch_id),
                "status": "APPLIED_CURRENT",
                "idempotent_replay": False,
                "current_scope_sha256": state["resulting_scope_sha256"],
            }

        return _run_price_apply_with_retry(conn, operation, deadline=deadline)

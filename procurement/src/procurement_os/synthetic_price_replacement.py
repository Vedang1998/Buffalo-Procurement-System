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
    get_price_book_batch,
    parse_price_book_csv,
)
from .storage import StorageAdapter
from .local_backup_v2 import (
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
        parsed = urlparse(database_url)
        if parsed.port is None:
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        _fixture()
        return mode, expected_database, parsed.port
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
            "qa_release_login",
            "qa_mapping_owner",
        ):
            raise SyntheticPriceReplacementError(
                "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
            )
        conn.execute(f'SELECT "{SCHEMA}".assert_persistent_mapping_foundation_contract()')
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


def _registered_declaration(
    conn: Any,
    *,
    vendor_name: str,
    effective_from: date,
    effective_through: date | None,
) -> dict[str, Any]:
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
    policy = conn.execute(
        f'''SELECT policy_ref,vendor_id::text,price_scope_key,currency,
                   policy_timezone,observation_at,application_at,
                   monday_evaluation_at,policy_sha256,fixture_database_name
              FROM "{SCHEMA}".supplier_price_schedule_policies
             WHERE policy_ref=%s''',
        (registered["policy_ref"],),
    ).fetchone()
    if policy is None or (
        str(policy[0]),
        str(policy[1]),
        str(policy[2]),
        str(policy[3]),
        str(policy[4]),
        policy[5].isoformat(),
        policy[6].isoformat(),
        policy[7].isoformat(),
        str(policy[9]),
    ) != (
        registered["policy_ref"],
        registered["vendor_id"],
        registered["price_scope_key"],
        registered["currency"],
        fixture["policy_timezone"],
        fixture["observation_at"],
        fixture["application_at"],
        fixture["monday_evaluation_at"],
        conn.info.dbname,
    ):
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_NOT_REGISTERED"
        )
    source_from = date.fromisoformat(fixture["source_valid_from"])
    source_through = date.fromisoformat(fixture["source_valid_through"])
    if effective_from != source_from or effective_through != source_through:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_DECLARATION_DATES_DIFFER"
        )
    declaration = {
        "contract": DECLARATION_CONTRACT,
        "fixture_registration_sha256": FIXTURE_REGISTRATION_CANONICAL_SHA256,
        "schedule_policy_ref": policy[0],
        "policy_sha256": policy[8],
        "vendor_id": policy[1],
        "vendor_name": vendor_name,
        "price_scope_key": policy[2],
        "currency": policy[3],
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
    row = conn.execute(
        f'''SELECT monday_evaluation_at
              FROM "{SCHEMA}".supplier_price_schedule_policies
             WHERE policy_ref=%s AND fixture_database_name=current_database()''',
        (targets[0]["policy_ref"],),
    ).fetchone()
    expected = datetime.fromisoformat(fixture["monday_evaluation_at"])
    if row is None or row[0] != expected:
        raise SyntheticPriceReplacementError(
            "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED"
        )
    return row[0]


def _declared_validation(
    conn: Any, parsed: dict[str, Any], declaration: Mapping[str, Any]
) -> dict[str, Any]:
    validation = _database_validation(conn, parsed)
    strict_fingerprint = validation["validation_fingerprint"]
    fingerprint = _sha256(
        {
            "contract": "STRICT_NORMALIZED_DECLARED_PRICE_BOOK_V1",
            "strict_v1_validation_fingerprint": strict_fingerprint,
            "declaration_sha256": declaration["declaration_sha256"],
        }
    )
    validation["validation_fingerprint"] = fingerprint
    validation["validation_evidence"] = {
        **validation["validation_evidence"],
        "contract": "STRICT_NORMALIZED_DECLARED_PRICE_BOOK_V1",
        "strict_v1_validation_fingerprint": strict_fingerprint,
        "declaration_sha256": declaration["declaration_sha256"],
    }
    return validation


def stage_and_validate_declared_price_book(
    conn: Any,
    storage: StorageAdapter,
    *,
    csv_bytes: bytes,
    principal: Principal,
    expected_declaration_sha256: str,
) -> dict[str, Any]:
    """Persist one declared candidate; operational CURRENT remains unchanged."""

    require_process_policy()
    principal.validate()
    if principal.role_ref != "procurement.price.approve":
        raise SyntheticPriceReplacementError("DECLARED_PRICE_PRINCIPAL_DIFFERS")
    parsed = parse_price_book_csv(csv_bytes)
    raw_key = f"price-books/raw/{parsed['content_sha256']}.csv"
    replay = False
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        require_attested_database(conn)
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
        existing = conn.execute(
            """SELECT price_book_batch_id,content_sha256,raw_storage_key,status,
                      validation_fingerprint,target_price_state,effective_from,
                      effective_through,replacement_contract,schedule_policy_ref,
                      price_scope_key,declaration_sha256
                 FROM price_book_batches
                WHERE vendor_name=%s AND batch_ref=%s""",
            (parsed["vendor_name"], parsed["batch_ref"]),
        ).fetchone()
        if existing is not None:
            if (
                existing[1] != parsed["content_sha256"]
                or existing[5] != parsed["target_price_state"]
                or existing[6] != parsed["effective_from"]
                or existing[7] != parsed["effective_through"]
                or existing[8] != CONTRACT
                or existing[9] != declaration["schedule_policy_ref"]
                or existing[10] != declaration["price_scope_key"]
                or existing[11] != declaration["declaration_sha256"]
            ):
                raise SyntheticPriceReplacementError(
                    "batch_ref already exists with different immutable declared input"
                )
            _verified_storage_read(storage, existing[2], existing[1])
            batch_id = str(existing[0])
            replay = True
        else:
            _verified_storage_write(storage, raw_key, csv_bytes)
            validation = _declared_validation(conn, parsed, declaration)
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
                    json.dumps(validation["validation_evidence"], sort_keys=True),
                    batch_id,
                ),
            )
    with conn.transaction():
        result = get_declared_price_book_batch(conn, batch_id)
    result["idempotent_replay"] = replay
    return result


def get_declared_price_book_batch(conn: Any, batch_id: str) -> dict[str, Any]:
    base = get_price_book_batch(conn, batch_id)
    declared = conn.execute(
        """SELECT replacement_contract,schedule_policy_ref,price_scope_key,
                  source_period_label,source_valid_from,source_valid_through,
                  source_validity_basis,supplier_verified_at,
                  operational_effective_from,operational_effective_through,
                  scope_membership_sha256,declaration_sha256
             FROM price_book_batches WHERE price_book_batch_id=%s""",
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
    base.update(dict(zip(keys, declared, strict=True)))
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
                  scope_membership_sha256,declaration_sha256
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
    raw = _verified_storage_read(storage, batch[14], batch[6])
    parsed = parse_price_book_csv(raw)
    declaration = _registered_declaration(
        conn,
        vendor_name=batch[2],
        effective_from=batch[5],
        effective_through=batch[7],
    )
    if declaration["declaration_sha256"] != batch[31]:
        raise SyntheticPriceReplacementError("declared price-book declaration changed")
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
    policy = conn.execute(
        """SELECT policy_sha256,observation_at,application_at,monday_evaluation_at,
                  fixture_database_name
             FROM supplier_price_schedule_policies WHERE policy_ref=%s""",
        (batch[21],),
    ).fetchone()
    if policy is None or policy[4] != conn.info.dbname:
        raise SyntheticPriceReplacementError("declared price policy differs")
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
            "offer_id": int(row["offer_id"]),
            "variant_id": row["canonical_variant_id"],
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
        "policy_sha256": policy[0],
        "scope_membership_sha256": membership_sha256,
        "predecessor_future_sha256": batch[18],
        "predecessor_batch_id": (
            str(batch[19]) if batch[19] is not None else None
        ),
        "warning_review_reason": review_reason,
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
        "observation_at": policy[1].isoformat(),
        "application_at": policy[2].isoformat(),
        "monday_evaluation_at": policy[3].isoformat(),
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
                      declaration_sha256
                 FROM price_book_batches WHERE price_book_batch_id=%s FOR UPDATE""",
            (batch_id,),
        ).fetchone()
        if batch is None or batch[7] != "VALIDATED" or batch[13] != CONTRACT:
            raise SyntheticPriceReplacementError("declared batch is not confirmable")
        raw = _verified_storage_read(storage, f"price-books/raw/{batch[4]}.csv", batch[4])
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
                   declaration_sha256,policy_sha256)
               VALUES (%s,'VALIDATED','VERIFIED_FUTURE',%s,%s,%s,%s,%s,%s,%s::jsonb,
                       %s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                  h.current_scope_sha256,h.active_price_book_batch_id::text
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
    )
    state = dict(zip(keys, rows[0], strict=True))
    application_local = state["application_at"].astimezone(
        __import__("zoneinfo").ZoneInfo(state["policy_timezone"])
    )
    if (
        state["status"] != "VERIFIED_FUTURE"
        or state["replacement_contract"] != CONTRACT
        or state["scope_key"] != "COMPLETE_VENDOR"
        or state["fixture_database"] != conn.info.dbname
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
    _verified_storage_read(storage, state["raw_storage_key"], state["content_sha256"])
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


def _apply_preflight(
    conn: Any,
    *,
    batch_id: str,
    backup: VerifiedPriceApplyBackup,
) -> tuple[str, tuple[str, ...]]:
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        require_attested_database(conn)
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
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            require_attested_database(conn)
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

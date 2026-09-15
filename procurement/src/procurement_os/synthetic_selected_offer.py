"""Attested local-only selected-offer input resolution.

This module does not activate an offer, approve a price, or authorize the
repository's real recommendation-cutover flag.  It exposes a deliberately
narrow synthetic resolver whose output is frozen by the existing Monday input
manifest and fingerprint.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from psycopg.rows import dict_row

from .monday_forecast_retirement import (
    MondayForecastRetirementContractError,
    verify_monday_forecast_v2_retirement_contract,
)
from .persistent_mapping import (
    PersistentMappingError,
    SCHEMA,
    SESSION_LOCK_WAIT_SECONDS,
    TOTAL_OPERATION_SECONDS,
    _try_session_lock,
    _unlock_session_locks,
    _verify_connected_synthetic_database,
    require_synthetic_mapping_capability,
)


CONTRACT = "SYNTHETIC_CONFIRMED_SELECTION_V1"
CAPABILITY_ENV = "BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS"
CAPABILITY_POLICY = "synthetic_selected_offer_inputs_enabled"
SELECTION_LOCK_PREFIX = "persistent-mapping:selection-variant:"
DEMO_CONTRACT = "BUFFALO_SYNTHETIC_OWNER_DEMO_V1"
SELECTED_OPERATION_SECONDS = TOTAL_OPERATION_SECONDS


class SyntheticSelectedOfferError(ValueError):
    pass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _numeric_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _is_positive_whole(value: Any) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed > 0 and parsed == parsed.to_integral_value()


def _is_positive_number(value: Any) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed > 0


def _pg_jsonb_text(value: Any) -> str:
    """Render JSON-compatible data in PostgreSQL jsonb object-key order."""

    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: (len(key.encode("utf-8")), key.encode("utf-8")))
        return "{" + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: {_pg_jsonb_text(value[key])}"
            for key in keys
        ) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_pg_jsonb_text(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _pg_jsonb_sha256(value: Any) -> str:
    return hashlib.sha256(_pg_jsonb_text(value).encode("utf-8")).hexdigest()


def _canonical_record_matches(record: Any, hash_field: str) -> bool:
    if not isinstance(record, dict):
        return False
    payload = record.get("canonical_payload")
    return (
        isinstance(payload, dict)
        and record.get(hash_field) == _pg_jsonb_sha256(payload)
        and all(record.get(key) == value for key, value in payload.items())
    )


def _canonical_fields_match(record: Any) -> bool:
    """Bind projected row fields to its canonical payload after JSON decoding."""

    if not isinstance(record, dict):
        return False
    payload = record.get("canonical_payload")
    return isinstance(payload, dict) and all(
        record.get(key) == value for key, value in payload.items()
    )


def _date_bounds_include(
    business_day: date, lower: Any, upper: Any
) -> bool:
    try:
        lower_day = date.fromisoformat(str(lower)) if lower is not None else None
        upper_day = date.fromisoformat(str(upper)) if upper is not None else None
    except ValueError:
        return False
    return (
        (lower_day is None or lower_day <= business_day)
        and (upper_day is None or business_day <= upper_day)
    )


_REVIEWED_FACT_FIELDS = (
    "package_type_state",
    "package_type_value",
    "physical_units_state",
    "physical_units_value",
    "retail_pack_units_state",
    "retail_pack_units_value",
    "assortment_scope_state",
    "assortment_scope_value",
    "assortment_group_state",
    "assortment_group_value",
    "assortable_state",
    "assortable_value",
    "identity_qualifiers",
    "independent_linkage_evidence",
    "related_artifact_hashes",
    "source_locator",
    "source_file_name",
    "source_file_sha256",
    "source_page_start",
    "source_page_end",
)

_DECISION_CANDIDATE_FIELDS = (
    "component_relationships",
    "distributor_product_id_state",
    "distributor_product_id_value",
    "offer_class",
    "operational_offer_key_sha256",
    "printed_occurrence_sha256",
    "qualifying_units_state",
    "qualifying_units_value",
    "raw_pack_state",
    "raw_pack_value",
    "shopify_units_state",
    "shopify_units_value",
    "size_state",
    "size_value",
    "supplier_code_state",
    "supplier_code_value",
    "supplier_identity_key_sha256",
    "assortment_scope_state",
    "assortment_scope_value",
    "assortment_group_state",
    "assortment_group_value",
    "assortable_state",
    "assortable_value",
)


def _valid_vendor_terms(terms: Any, vendor_id: Any) -> bool:
    """Validate the exact frozen vendor row, not only its self-supplied digest."""

    try:
        from .vendor_rules import validate_vendor_rules_input

        rules = terms["vendor_rules"]
        if (
            str(terms.get("vendor_id")) != str(vendor_id)
            or not isinstance(rules, list)
            or len(rules) != 22
            or not _is_nonblank(rules[0])
            or rules[1] is not True
            or not _is_nonblank(rules[10])
            or not _is_nonblank(rules[11])
            or not isinstance(rules[12], int)
            or rules[12] < 1
            or not _is_nonblank(str(rules[20] or ""))
            or not _is_nonblank(str(rules[21] or ""))
        ):
            return False
        validate_vendor_rules_input(
            {
                "order_days": rules[13],
                "order_cutoff_local": rules[14],
                "timezone_name": rules[15],
                "expected_delivery_days": rules[16],
                "order_cycle_days": rules[2],
                "lead_time_days": rules[3],
                "lead_time_variability_days": rules[4],
                "reliability_pct": rules[17],
                "minimum_type": rules[5],
                "minimum_value": rules[6],
                "below_minimum_fee": rules[7],
                "loose_order_allowed": rules[8],
                "loose_unit_fee": rules[9],
                "special_rules": rules[18],
                "holiday_blackout_notes": rules[19],
                "confirmation_source": rules[10],
            }
        )
    except (KeyError, TypeError, ValueError):
        return False
    return True


def selected_mode_requested() -> bool:
    value = os.getenv(CAPABILITY_ENV)
    if value is None or value == "":
        return False
    if value != "1":
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        )
    return True


def _environment_database_endpoint(mode: str) -> tuple[str, int, str]:
    variable = "TEST_DATABASE_URL" if mode == "AUTOMATED_TEST" else "DATABASE_URL"
    raw = os.getenv(variable, "")
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        ) from exc
    database = parsed.path.removeprefix("/")
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or port is None
        or not database.endswith("_test" if mode == "AUTOMATED_TEST" else "_demo")
    ):
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        )
    return parsed.hostname, port, database


def require_selected_mode_process_policy() -> tuple[str, str, int]:
    """Validate process-owned controls before any database statement."""
    try:
        if not selected_mode_requested():
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
            )
        require_synthetic_mapping_capability("selected_offer_shadow_reads_enabled")
        from .config import load_rules

        policy = load_rules().get("persistent_mapping", {})
        disabled_flags = (
            "review_intake_writes_enabled",
            "human_mapping_writes_enabled",
            "policy_mapping_writes_enabled",
            "routine_selection_writes_enabled",
            "selected_offer_shadow_reads_enabled",
            CAPABILITY_POLICY,
            "recommendation_cutover_enabled",
            "offer_activation_enabled",
        )
        if (
            policy.get("contract_version") != "v1-shadow-only"
            or policy.get("routine_selection_scope")
            != "ROUTINE_PROCUREMENT_STANDARD"
            or any(policy.get(flag) is not False for flag in disabled_flags)
        ):
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
            )
        mode = os.getenv("BUFFALO_RUNTIME_MODE", "").strip().upper()
        if mode not in {"AUTOMATED_TEST", "SYNTHETIC_DEMO"}:
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
            )
        _host, port, expected_database = _environment_database_endpoint(mode)
        return mode, expected_database, port
    except SyntheticSelectedOfferError:
        raise
    except PersistentMappingError as exc:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        ) from exc
    except Exception as exc:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        ) from exc


def require_attested_selected_mode(conn: Any) -> None:
    """Re-attest every selected read; a stored run label grants no authority."""

    try:
        mode, expected_database, expected_port = require_selected_mode_process_policy()
        _verify_connected_synthetic_database(conn, expected_database)
        facts = conn.execute(
            "SELECT pg_catalog.current_schema(),pg_catalog.current_database(),"
            "pg_catalog.inet_server_port()"
        ).fetchone()
        if (
            facts is None
            or str(facts[0]) != SCHEMA
            or str(facts[1]) != expected_database
            or int(facts[2]) != expected_port
        ):
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
            )
        conn.execute(f'SELECT "{SCHEMA}".assert_persistent_mapping_foundation_contract()')
        verify_monday_forecast_v2_retirement_contract(conn)
        if mode == "SYNTHETIC_DEMO":
            marker = conn.execute(
                f'SELECT value FROM "{SCHEMA}".meta WHERE key=%s',
                ("synthetic_owner_demo_contract",),
            ).fetchone()
            if marker is None or marker[0] != DEMO_CONTRACT:
                raise SyntheticSelectedOfferError(
                    "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
                )
    except SyntheticSelectedOfferError:
        raise
    except (PersistentMappingError, MondayForecastRetirementContractError) as exc:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        ) from exc
    except Exception as exc:
        if (
            exc.__class__.__name__ == "MondayRecommendationError"
            and str(exc) == "MONDAY_PREPARATION_RETRY_REQUIRED"
        ):
            raise
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        ) from exc


def acquire_input_locks(
    conn: Any,
    variant_ids: Iterable[str],
    *,
    global_lock_id: int,
    operation_deadline: float | None = None,
) -> tuple[str, ...]:
    """Acquire global then byte-sorted selection locks before a fresh snapshot."""

    names = tuple(
        SELECTION_LOCK_PREFIX + value
        for value in sorted(set(variant_ids), key=lambda v: v.encode("utf-8"))
    )
    acquired: list[str] = []
    global_acquired = False
    ownership_uncertain = False
    deadline = min(
        operation_deadline if operation_deadline is not None else float("inf"),
        time.monotonic() + SESSION_LOCK_WAIT_SECONDS,
    )
    try:
        if conn.info.transaction_status.name != "IDLE":
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
            )
        while time.monotonic() < deadline:
            ownership_uncertain = True
            locked = conn.execute(
                "SELECT pg_catalog.pg_try_advisory_lock(%s)", (global_lock_id,)
            ).fetchone()[0]
            if locked:
                global_acquired = True
            ownership_uncertain = False
            if locked:
                break
            time.sleep(0.01)
        else:
            raise SyntheticSelectedOfferError("MONDAY_PREPARATION_RETRY_REQUIRED")
        for name in names:
            ownership_uncertain = True
            _try_session_lock(conn, name, deadline)
            acquired.append(name)
            ownership_uncertain = False
        conn.rollback()
        return tuple(acquired)
    except BaseException as exc:
        if ownership_uncertain:
            try:
                conn.close()
            finally:
                if isinstance(exc, PersistentMappingError):
                    raise SyntheticSelectedOfferError(
                        "MONDAY_PREPARATION_RETRY_REQUIRED"
                    ) from exc
                raise
        try:
            conn.rollback()
            if acquired:
                _unlock_session_locks(conn, acquired)
            if global_acquired:
                unlocked = conn.execute(
                    "SELECT pg_catalog.pg_advisory_unlock(%s)", (global_lock_id,)
                ).fetchone()[0]
                if not unlocked:
                    raise SyntheticSelectedOfferError(
                        "SYNTHETIC_SELECTED_OFFER_LOCK_CLEANUP_FAILED"
                    )
            conn.rollback()
        except BaseException:
            conn.close()
        if isinstance(exc, PersistentMappingError):
            raise SyntheticSelectedOfferError(
                "MONDAY_PREPARATION_RETRY_REQUIRED"
            ) from exc
        raise


def release_input_locks(
    conn: Any, lock_names: Iterable[str], *, global_lock_id: int
) -> None:
    names = tuple(lock_names)
    try:
        if conn.info.transaction_status.name != "IDLE":
            conn.rollback()
        _unlock_session_locks(conn, names)
        unlocked = conn.execute(
            "SELECT pg_catalog.pg_advisory_unlock(%s)", (global_lock_id,)
        ).fetchone()[0]
        if not unlocked:
            raise SyntheticSelectedOfferError("SYNTHETIC_SELECTED_OFFER_LOCK_CLEANUP_FAILED")
        conn.rollback()
        if conn.info.transaction_status.name != "IDLE":
            raise SyntheticSelectedOfferError("SYNTHETIC_SELECTED_OFFER_LOCK_CLEANUP_FAILED")
    except BaseException as exc:
        try:
            conn.close()
        finally:
            if isinstance(exc, SyntheticSelectedOfferError):
                raise
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_LOCK_CLEANUP_FAILED"
            ) from exc


def acquire_variant_locks(conn: Any, variant_ids: Iterable[str]) -> tuple[str, ...]:
    """Acquire the selection writer's exact Variant locks outside a snapshot."""

    names = tuple(
        SELECTION_LOCK_PREFIX + value
        for value in sorted(set(variant_ids), key=lambda value: value.encode("utf-8"))
    )
    acquired: list[str] = []
    ownership_uncertain = False
    deadline = time.monotonic() + SESSION_LOCK_WAIT_SECONDS
    try:
        if conn.info.transaction_status.name != "IDLE":
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        )
        for name in names:
            ownership_uncertain = True
            _try_session_lock(conn, name, deadline)
            acquired.append(name)
            ownership_uncertain = False
        conn.rollback()
        return tuple(acquired)
    except BaseException as exc:
        if ownership_uncertain:
            try:
                conn.close()
            finally:
                if isinstance(exc, PersistentMappingError):
                    raise SyntheticSelectedOfferError(
                        "SYNTHETIC_SELECTED_OFFER_INPUT_LOCK_UNAVAILABLE"
                    ) from exc
                raise
        try:
            conn.rollback()
            if acquired:
                _unlock_session_locks(conn, acquired)
            conn.rollback()
        except BaseException:
            conn.close()
        if isinstance(exc, PersistentMappingError):
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_INPUT_LOCK_UNAVAILABLE"
            ) from exc
        raise


def release_variant_locks(conn: Any, lock_names: Iterable[str]) -> None:
    """Release selected-input Variant locks and prove the connection is idle."""

    names = tuple(lock_names)
    try:
        if conn.info.transaction_status.name != "IDLE":
            conn.rollback()
        _unlock_session_locks(conn, names)
        conn.rollback()
        if conn.info.transaction_status.name != "IDLE":
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_LOCK_CLEANUP_FAILED"
            )
    except BaseException as exc:
        try:
            conn.close()
        finally:
            if isinstance(exc, SyntheticSelectedOfferError):
                raise
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_LOCK_CLEANUP_FAILED"
            ) from exc


def classify_frozen_manifest(raw: Any) -> tuple[str | None, tuple[str, ...]]:
    """Classify one frozen manifest without letting a stored label grant authority."""

    try:
        manifest = json.loads(raw)
        contexts = manifest["contexts"]
        raw_variant_ids = manifest["variant_ids"]
        variant_ids = tuple(raw_variant_ids)
        business_date = str(manifest["business_date"])
        evaluation_at = str(manifest["evaluation_at"])
        business_day = date.fromisoformat(business_date)
        datetime.fromisoformat(evaluation_at)
    except (
        AttributeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or not isinstance(contexts, list)
        or not isinstance(raw_variant_ids, list)
        or any(not isinstance(value, str) for value in raw_variant_ids)
        or not variant_ids
        or variant_ids != tuple(sorted(set(variant_ids)))
        or any(not value.strip() for value in variant_ids)
        or len(contexts) != len(variant_ids)
    ):
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
        )
    contract = manifest.get("offer_resolution_contract")
    selected_only_context_keys = {
        "selected_offer_input_evidence",
        "selected_offer_input_evidence_sha256",
    }
    selected_fields = [
        selected_only_context_keys.intersection(context)
        if isinstance(context, dict)
        else set()
        for context in contexts
    ]
    if contract is None:
        if "offer_resolution_contract" in manifest or any(selected_fields):
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
            )
        return None, variant_ids
    if contract != CONTRACT or any(
        fields != selected_only_context_keys for fields in selected_fields
    ):
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
        )
    for expected_variant, context in zip(variant_ids, contexts, strict=True):
        evidence = context["selected_offer_input_evidence"]
        if (
            context.get("variant_id") != expected_variant
            or not isinstance(evidence, dict)
            or evidence.get("contract") != CONTRACT
            or evidence.get("authority") != "SYNTHETIC_TEST_ONLY"
            or evidence.get("temporal_semantics")
            != "CURRENT_STATE_OBSERVED_IN_RUN_TRANSACTION"
            or evidence.get("historical_reconstruction") is not False
            or str(evidence.get("business_date")) != business_date
            or evidence.get("variant_id") != expected_variant
            or str(evidence.get("selection_observed_at")) != evaluation_at
            or any(
                evidence.get(key) is not False
                for key in (
                    "offer_activation_performed",
                    "price_mutation_performed",
                    "shopify_action_performed",
                    "po_release_performed",
                    "real_or_commercial_authority",
                )
            )
        ):
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
            )
        expected_sha = context.get("selected_offer_input_evidence_sha256")
        actual_sha = hashlib.sha256(
            json.dumps(
                evidence, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest()
        if expected_sha != actual_sha:
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
            )
        blockers = context.get("blockers")
        if not isinstance(blockers, list):
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
            )
        if blockers:
            normalized_blockers = sorted({str(value) for value in blockers})
            if (
                not isinstance(blockers, list)
                or blockers != normalized_blockers
                or not normalized_blockers
                or any(not _is_nonblank(value) for value in blockers)
                or evidence.get("resolution_blockers") != normalized_blockers
                or evidence.get("blocker") != normalized_blockers[0]
                or evidence.get("recommendation_effect")
                != "NO_RECOMMENDATION_BLOCKED"
            ):
                raise SyntheticSelectedOfferError(
                    "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
                )
            continue
        required = {
            "selection_head",
            "selection_event",
            "mapping_decision",
            "mapping_candidate",
            "review_batch",
            "selected_offer",
            "current_fingerprints",
            "applicable_vendor_terms",
            "applicable_price_ladder",
            "initial_applicable_price_tier",
            "legacy_active_standard_offer_comparison",
            "commercial_source_authority",
            "source_import_state",
        }
        head = evidence.get("selection_head")
        event = evidence.get("selection_event")
        decision = evidence.get("mapping_decision")
        candidate = evidence.get("mapping_candidate")
        batch = evidence.get("review_batch")
        offer = evidence.get("selected_offer")
        fingerprints = evidence.get("current_fingerprints")
        terms = evidence.get("applicable_vendor_terms")
        ladder = evidence.get("applicable_price_ladder")
        initial_tier = evidence.get("initial_applicable_price_tier")
        comparison = evidence.get("legacy_active_standard_offer_comparison")
        sha256_fields = (
            "mapping_decision_sha256",
            "offer_sha256",
            "catalog_sha256",
            "vendor_sha256",
            "rejection_memory_sha256",
        )
        event_id = head.get("selection_event_id") if isinstance(head, dict) else None
        decision_id = event.get("mapping_decision_id") if isinstance(event, dict) else None
        candidate_id = decision.get("candidate_id") if isinstance(decision, dict) else None
        batch_id = decision.get("review_batch_id") if isinstance(decision, dict) else None
        head_version = head.get("head_version") if isinstance(head, dict) else None
        expected_reviewed_facts = (
            {field: candidate.get(field) for field in _REVIEWED_FACT_FIELDS}
            if isinstance(candidate, dict)
            else None
        )
        linkage_evidence = (
            candidate.get("independent_linkage_evidence")
            if isinstance(candidate, dict)
            else None
        )
        expected_evidence_set_sha256 = (
            _pg_jsonb_sha256(
                {
                    "contract_version": "INDEPENDENT_LINKAGE_EVIDENCE_V1",
                    "printed_source_sha256": candidate.get("source_file_sha256"),
                    "evidence": linkage_evidence,
                }
            )
            if isinstance(candidate, dict) and isinstance(linkage_evidence, list)
            else None
        )
        decision_candidate_fields_match = (
            isinstance(decision, dict)
            and isinstance(candidate, dict)
            and all(
                decision.get(field) == candidate.get(field)
                for field in _DECISION_CANDIDATE_FIELDS
            )
        )
        candidate_offer_fields_match = (
            isinstance(candidate, dict)
            and isinstance(offer, dict)
            and candidate.get("supplier_code_state") == "VALUE"
            and candidate.get("supplier_code_value") == offer.get("supplier_sku")
            and candidate.get("assortment_scope_state") == "VALUE"
            and candidate.get("assortment_scope_value")
            == offer.get("assortment_scope")
            and (
                (
                    offer.get("assortment_group") is None
                    and candidate.get("assortment_group_state") == "EXPLICIT_NULL"
                    and candidate.get("assortment_group_value") is None
                )
                or (
                    offer.get("assortment_group") is not None
                    and candidate.get("assortment_group_state") == "VALUE"
                    and candidate.get("assortment_group_value")
                    == offer.get("assortment_group")
                )
            )
            and candidate.get("assortable_state") == "VALUE"
            and candidate.get("assortable_value") is offer.get("assortable")
        )
        ladder_rows = ladder.get("rows") if isinstance(ladder, dict) else None
        exact_tier_rows = []
        if isinstance(ladder_rows, list) and isinstance(initial_tier, dict):
            exact_tier_rows = [
                row
                for row in ladder_rows
                if isinstance(row, list)
                and len(row) == 10
                and row[0] == initial_tier.get("price_id")
                and row[1] == initial_tier.get("offer_id")
                and row[3] == initial_tier.get("level_type")
                and row[4] == initial_tier.get("break_qty")
                and row[5] == initial_tier.get("break_unit")
                and row[6] == initial_tier.get("case_price")
                and row[7] == initial_tier.get("unit_price")
            ]
        if (
            not required.issubset(evidence)
            or evidence.get("recommendation_effect")
            != "SELECTED_OFFER_INPUT_AUTHORITY"
            or evidence.get("commercial_source_authority") != "NOT_APPROVED"
            or evidence.get("source_import_state") != "NOT_IMPORT_READY"
            or not isinstance(head, dict)
            or head.get("variant_id") != expected_variant
            or head.get("scope") != "ROUTINE_PROCUREMENT_STANDARD"
            or not _is_nonblank(event_id)
            or not _canonical_record_matches(event, "payload_sha256")
            or not isinstance(head.get("head_version"), int)
            or head["head_version"] < 1
            or not all(
                isinstance(item, dict) and item
                for item in (event, decision, candidate, batch)
            )
            or str(event.get("selection_event_id")) != event_id
            or event.get("variant_id") != expected_variant
            or event.get("selection_scope") != "ROUTINE_PROCUREMENT_STANDARD"
            or event.get("action") != "SELECT"
            or event.get("expected_prior_head_version") != head_version - 1
            or (
                head_version == 1
                and event.get("expected_prior_event_id") is not None
            )
            or (
                head_version > 1
                and (
                    not _is_nonblank(event.get("expected_prior_event_id"))
                    or event.get("expected_prior_event_id") == event_id
                )
            )
            or not _is_nonblank(decision_id)
            or not _is_sha256(event.get("payload_sha256"))
            or not _is_sha256(event.get("preview_sha256"))
            or not _is_sha256(event.get("confirmation_sha256"))
            or not _is_sha256(event.get("human_authn_context_sha256"))
            or not _is_nonblank(str(event.get("selected_at") or ""))
            or not isinstance(event.get("selected_txid"), int)
            or not _is_nonblank(event.get("human_principal_ref"))
            or not _is_nonblank(event.get("human_role_ref"))
            or not _date_bounds_include(
                business_day,
                event.get("effective_from"),
                event.get("effective_through"),
            )
            or str(decision.get("mapping_decision_id")) != str(decision_id)
            # NUMERIC scale is not retained by the JSON decoder for these two
            # records, so their database hash cannot be reconstructed from the
            # manifest. Bind every canonical field and retain the exact stored
            # database hash instead.
            or not _canonical_fields_match(decision)
            or decision.get("action") != "APPROVE_MAPPING"
            or decision.get("decision_origin") != "HUMAN"
            or decision.get("authority_kind") != "HUMAN_APPROVED"
            or decision.get("variant_id") != expected_variant
            or not _is_nonblank(str(decision.get("vendor_id") or ""))
            or not _is_nonblank(str(candidate_id or ""))
            or not _is_nonblank(str(batch_id or ""))
            or not _is_sha256(decision.get("payload_sha256"))
            or not _is_sha256(decision.get("preview_sha256"))
            or not _is_sha256(decision.get("confirmation_sha256"))
            or not _is_sha256(decision.get("human_authn_context_sha256"))
            or not _is_nonblank(decision.get("human_principal_ref"))
            or not _is_nonblank(decision.get("human_role_ref"))
            or not _is_nonblank(str(decision.get("decided_at") or ""))
            or not isinstance(decision.get("decided_txid"), int)
            or decision.get("offer_class") != "REGULAR"
            or decision.get("offer_link_kind")
            not in {"LINKED_EXISTING", "CREATED_INACTIVE"}
            or decision.get("result_offer_package_type") != "STANDARD"
            or not _is_sha256(decision.get("printed_occurrence_sha256"))
            or not _is_sha256(decision.get("supplier_identity_key_sha256"))
            or not _is_sha256(decision.get("operational_offer_key_sha256"))
            or not _is_sha256(decision.get("evidence_set_sha256"))
            or decision.get("evidence_set_sha256") != expected_evidence_set_sha256
            or decision.get("reviewed_facts") != expected_reviewed_facts
            or not decision_candidate_fields_match
            or str(candidate.get("candidate_id")) != str(candidate_id)
            or not _canonical_fields_match(candidate)
            or str(candidate.get("review_batch_id")) != str(batch_id)
            or candidate.get("proposed_variant_id") != expected_variant
            or str(candidate.get("proposed_vendor_id"))
            != str(decision.get("vendor_id"))
            or not _is_sha256(candidate.get("candidate_sha256"))
            or not isinstance(candidate.get("occurrence_index"), int)
            or candidate.get("occurrence_index") < 1
            or not _is_nonblank(candidate.get("occurrence_key"))
            or not _is_sha256(candidate.get("printed_occurrence_sha256"))
            or not _is_sha256(candidate.get("supplier_identity_key_sha256"))
            or not _is_sha256(candidate.get("operational_offer_key_sha256"))
            or not _is_sha256(candidate.get("decision_scope_sha256"))
            or candidate.get("blockers") != []
            or candidate.get("offer_class") != "REGULAR"
            or candidate.get("package_type_state") != "VALUE"
            or candidate.get("size_state") != "VALUE"
            or candidate.get("raw_pack_state") != "VALUE"
            or candidate.get("shopify_units_state") != "VALUE"
            or candidate.get("qualifying_units_state") != "VALUE"
            or not _is_nonblank(candidate.get("source_table_name"))
            or not _is_nonblank(candidate.get("source_row_key"))
            or not _is_nonblank(candidate.get("source_file_name"))
            or not _is_sha256(candidate.get("source_file_sha256"))
            or not isinstance(candidate.get("source_locator"), dict)
            or not isinstance(candidate.get("source_page_start"), int)
            or not isinstance(candidate.get("source_page_end"), int)
            or candidate.get("source_page_start") < 1
            or candidate.get("source_page_end")
            < candidate.get("source_page_start")
            or not isinstance(linkage_evidence, list)
            or not linkage_evidence
            or any(
                not isinstance(item, dict)
                or not _is_nonblank(item.get("evidence_mode"))
                or not _is_sha256(item.get("observation_sha256"))
                or not _is_sha256(item.get("origin_sha256"))
                for item in linkage_evidence
            )
            or str(batch.get("review_batch_id")) != str(batch_id)
            or not _canonical_record_matches(batch, "payload_sha256")
            or batch.get("source_authority_state") != "NOT_APPROVED"
            or batch.get("source_import_state") != "NOT_IMPORT_READY"
            or batch.get("source_package_kind") != "SEALED_V5_REVIEW_PACKAGE"
            or batch.get("structural_state") != "READY"
            or batch.get("source_evidence_state") != "READY"
            or batch.get("semantic_state") != "READY"
            or batch.get("source_is_simulation") is not False
            or not isinstance(batch.get("candidate_count"), int)
            or batch.get("candidate_count") < 1
            or not _is_nonblank(batch.get("source_package_id"))
            or not _is_nonblank(batch.get("source_revision"))
            or not _is_sha256(batch.get("payload_sha256"))
            or any(
                not _is_sha256(batch.get(key))
                for key in (
                    "source_artifact_sha256",
                    "source_root_sha256",
                    "source_seal_sha256",
                    "relationship_table_sha256",
                    "source_batch_sha256",
                    "source_payload_sha256",
                    "candidate_set_sha256",
                )
            )
            or not isinstance(batch.get("prerequisites"), dict)
            or not _is_nonblank(batch["prerequisites"].get("packet_contract"))
            or not _is_sha256(batch["prerequisites"].get("packet_sha256"))
            or decision.get("expected_candidate_sha256")
            != candidate.get("candidate_sha256")
            or decision.get("expected_batch_payload_sha256")
            != batch.get("payload_sha256")
            or not isinstance(offer, dict)
            or offer.get("variant_id") != expected_variant
            or not isinstance(offer.get("offer_id"), int)
            or not str(offer.get("vendor_id") or "").strip()
            or not str(offer.get("supplier_sku") or "").strip()
            or offer.get("package_type") != "STANDARD"
            or offer.get("confidence") != "VERIFIED"
            or offer.get("active") is not True
            or not _date_bounds_include(
                business_day,
                offer.get("valid_from"),
                offer.get("valid_to"),
            )
            or not _is_positive_whole(offer.get("shopify_units_per_case"))
            or not _is_positive_whole(offer.get("qualifying_units_per_case"))
            or candidate.get("package_type_value") != offer.get("package_type")
            or candidate.get("size_value") != offer.get("size_text")
            or candidate.get("raw_pack_value") != offer.get("raw_pack")
            or not candidate_offer_fields_match
            or not _numeric_equal(
                candidate.get("shopify_units_value"),
                offer.get("shopify_units_per_case"),
            )
            or not _numeric_equal(
                candidate.get("qualifying_units_value"),
                offer.get("qualifying_units_per_case"),
            )
            or decision.get("result_offer_id") != offer.get("offer_id")
            or str(decision.get("vendor_id")) != str(offer.get("vendor_id"))
            or event.get("selected_offer_id") != offer.get("offer_id")
            or not isinstance(fingerprints, dict)
            or any(
                not _is_sha256(fingerprints.get(key))
                for key in sha256_fields
            )
            or decision.get("payload_sha256")
            != fingerprints.get("mapping_decision_sha256")
            or event.get("expected_mapping_decision_sha256")
            != fingerprints.get("mapping_decision_sha256")
            or event.get("expected_offer_contract_sha256")
            != fingerprints.get("offer_sha256")
            or decision.get("result_offer_contract_sha256")
            != fingerprints.get("offer_sha256")
            or event.get("expected_catalog_sha256")
            != fingerprints.get("catalog_sha256")
            or decision.get("expected_catalog_sha256")
            != fingerprints.get("catalog_sha256")
            or event.get("expected_vendor_sha256")
            != fingerprints.get("vendor_sha256")
            or decision.get("expected_vendor_sha256")
            != fingerprints.get("vendor_sha256")
            or event.get("expected_rejection_memory_sha256")
            != fingerprints.get("rejection_memory_sha256")
            or decision.get("expected_rejection_memory_sha256")
            != fingerprints.get("rejection_memory_sha256")
            or not isinstance(terms, dict)
            or not _valid_vendor_terms(terms, offer.get("vendor_id"))
            or str(terms.get("vendor_id")) != str(offer.get("vendor_id"))
            or not isinstance(terms.get("vendor_rules"), list)
            or len(terms["vendor_rules"]) != 22
            or not _is_sha256(terms.get("sha256"))
            or hashlib.sha256(
                json.dumps(
                    terms.get("vendor_rules"),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            != terms.get("sha256")
            or not isinstance(ladder, dict)
            or ladder.get("contract")
            != "FROZEN_SELECTED_OFFER_PRICE_LADDER_V1"
            or str(ladder.get("effective_month"))
            != business_day.replace(day=1).isoformat()
            or not isinstance(ladder.get("rows"), list)
            or not ladder["rows"]
            or not _is_sha256(ladder.get("sha256"))
            or any(
                not isinstance(row, list)
                or len(row) != 10
                or not isinstance(row[0], int)
                or row[1] != offer.get("offer_id")
                or str(row[2]) != str(ladder.get("effective_month"))
                or row[3] not in {"BASE", "BREAK"}
                or not _is_positive_number(row[6])
                or not _is_positive_number(row[7])
                or (row[3] == "BASE" and (row[4] is not None or row[5] is not None))
                or (
                    row[3] == "BREAK"
                    and (
                        not _is_positive_number(row[4])
                        or row[5] not in {"CS", "BT"}
                    )
                )
                or not _is_nonblank(row[8])
                for row in ladder["rows"]
            )
            or len({row[0] for row in ladder["rows"]}) != len(ladder["rows"])
            or sum(row[3] == "BASE" for row in ladder["rows"]) != 1
            or hashlib.sha256(
                json.dumps(
                    ladder["rows"],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            != ladder.get("sha256")
            or not isinstance(initial_tier, dict)
            or initial_tier.get("price_ladder_sha256") != ladder.get("sha256")
            or initial_tier.get("offer_id") != offer.get("offer_id")
            or not isinstance(initial_tier.get("price_id"), int)
            or len(exact_tier_rows) != 1
            or not isinstance(comparison, dict)
            or comparison.get("authority") != "COMPARISON_ONLY_NO_AUTHORITY"
            or not isinstance(comparison.get("active_standard_offer_ids"), list)
            or comparison.get("active_standard_offer_count")
            != len(comparison["active_standard_offer_ids"])
            or any(
                not isinstance(offer_id, int)
                for offer_id in comparison["active_standard_offer_ids"]
            )
        ):
            raise SyntheticSelectedOfferError(
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
            )
    return CONTRACT, variant_ids


@contextmanager
def selected_run_input_lock_scope(
    conn: Any,
    *,
    run_id: str | None = None,
    recommendation_id: int | None = None,
):
    """Lock selected inputs before a review/DRAFT validation transaction.

    Legacy/default calls retain their existing path.  When the explicit
    synthetic overlay is asserted, process policy is checked before the
    preliminary immutable-run read, that read is ended, and selected Variant
    locks are held across the caller's fresh validation/commit transaction.
    """

    try:
        requested = selected_mode_requested()
        if requested:
            require_selected_mode_process_policy()
    except SyntheticSelectedOfferError:
        raise
    if (run_id is None) == (recommendation_id is None):
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        )
    initially_idle = conn.info.transaction_status.name == "IDLE"
    if requested and not initially_idle:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        )
    try:
        if recommendation_id is not None:
            row = conn.execute(
                """SELECT ru.procurement_input_manifest,ru.input_fingerprint,
                          ru.workflow_stage
                     FROM procurement_recommendations r
                     JOIN runs ru ON ru.run_id=r.run_id
                    WHERE r.recommendation_id=%s""",
                (recommendation_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT procurement_input_manifest,input_fingerprint,workflow_stage
                      FROM runs
                    WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'""",
                (run_id,),
            ).fetchone()
        if initially_idle:
            conn.rollback()
    except BaseException:
        if initially_idle:
            try:
                conn.rollback()
            except BaseException:
                conn.close()
        raise
    if row is None:
        yield
        return
    if hashlib.sha256(str(row[0]).encode("utf-8")).hexdigest() != row[1]:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID"
        )
    contract, variant_ids = classify_frozen_manifest(row[0])
    if contract is None:
        yield
        return
    if contract != CONTRACT:
        raise SyntheticSelectedOfferError("UNKNOWN_OFFER_RESOLUTION_CONTRACT")
    if not requested:
        raise SyntheticSelectedOfferError(
            "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
        )
    if row[2] in {"DRAFTS_BUILT", "PACKET_BUILT"}:
        with conn.transaction():
            require_attested_selected_mode(conn)
        yield
        return
    acquired: tuple[str, ...] = ()
    acquisition_started = False
    ownership_recorded = False
    try:
        acquisition_started = True
        acquired = acquire_variant_locks(conn, variant_ids)
        ownership_recorded = True
        # Re-attest after the locks are held; the write path re-attests again
        # when it reconstructs the selected inputs in its fresh transaction.
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            require_attested_selected_mode(conn)
        yield
    finally:
        if (
            acquisition_started
            and not ownership_recorded
            and not getattr(conn, "closed", False)
        ):
            conn.close()
        elif acquired and not getattr(conn, "closed", False):
            release_variant_locks(conn, acquired)


def selected_blocker_evidence(
    *, blocker: str, variant_id: str, business_date: date, evaluation_at: datetime
) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "authority": "SYNTHETIC_TEST_ONLY",
        "recommendation_effect": "NO_RECOMMENDATION_BLOCKED",
        "temporal_semantics": "CURRENT_STATE_OBSERVED_IN_RUN_TRANSACTION",
        "historical_reconstruction": False,
        "business_date": business_date,
        "selection_observed_at": evaluation_at,
        "variant_id": variant_id,
        "blocker": blocker,
        "offer_activation_performed": False,
        "price_mutation_performed": False,
        "shopify_action_performed": False,
        "po_release_performed": False,
        "real_or_commercial_authority": False,
    }


def resolve_selected_offer(
    conn: Any, *, variant_id: str, business_date: date, evaluation_at: datetime
) -> tuple[tuple[Any, ...] | None, dict[str, Any]]:
    """Resolve and revalidate one immutable selected lineage in this snapshot."""

    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f'''SELECT h.head_version,h.selection_event_id::text,h.updated_at,h.updated_txid,
                       e.variant_id AS event_variant_id,e.selection_scope AS event_scope,
                       e.effective_from AS event_effective_from,
                       e.effective_through AS event_effective_through,
                       to_jsonb(e) AS selection_event,to_jsonb(d) AS mapping_decision,
                       to_jsonb(c) AS mapping_candidate,to_jsonb(b) AS review_batch,
                       o.offer_id,o.vendor_id::text,o.supplier_sku,o.package_type,o.size_text,
                       o.raw_pack,o.shopify_units_per_case,o.qualifying_units_per_case,
                       o.assortment_scope,o.assortment_group,o.assortable,o.confidence,
                       o.source_file,o.source_page,o.notes,o.valid_from,o.valid_to,o.active,
                       o.variant_id AS offer_variant_id,v.active AS vendor_active,
                       "{SCHEMA}".is_procurement_eligible_variant(h.variant_id)
                           AS variant_eligible,
                       "{SCHEMA}".persistent_mapping_json_sha256(e.canonical_payload)
                           AS event_payload_sha256,
                       "{SCHEMA}".persistent_mapping_json_sha256(c.canonical_payload)
                           AS candidate_payload_sha256,
                       "{SCHEMA}".persistent_mapping_json_sha256(b.canonical_payload)
                           AS batch_payload_sha256,
                       "{SCHEMA}".persistent_mapping_candidate_reviewed_facts(c)
                           AS current_reviewed_facts,
                       "{SCHEMA}".persistent_mapping_candidate_evidence_set_sha256(c)
                           AS current_evidence_set_sha256,
                       "{SCHEMA}".persistent_mapping_json_sha256(d.canonical_payload)
                           AS mapping_sha256,
                       "{SCHEMA}".persistent_mapping_offer_fingerprint(o.offer_id)
                           AS offer_sha256,
                       "{SCHEMA}".persistent_mapping_catalog_fingerprint(h.variant_id)
                           AS catalog_sha256,
                       "{SCHEMA}".persistent_mapping_vendor_fingerprint(d.vendor_id)
                           AS vendor_sha256,
                       "{SCHEMA}".persistent_mapping_rejection_fingerprint(
                           d.vendor_id,'persistent-mapping:'||d.supplier_identity_key_sha256,NULL
                       ) AS rejection_sha256,
                       EXISTS (
                           SELECT 1 FROM "{SCHEMA}".mapping_rejections r
                            WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
                              AND r.vendor_id=d.vendor_id
                              AND r.rejected_variant_id=d.variant_id
                              AND r.source_key='persistent-mapping:'||d.supplier_identity_key_sha256
                       ) AS active_rejection
                  FROM "{SCHEMA}".supplier_offer_selection_heads h
                  JOIN "{SCHEMA}".supplier_offer_selection_events e
                    ON e.selection_event_id=h.selection_event_id
                   AND e.variant_id=h.variant_id
                   AND e.selection_scope=h.selection_scope
             LEFT JOIN "{SCHEMA}".v_effective_supplier_mapping_decisions d
                    ON d.mapping_decision_id=e.mapping_decision_id
                   AND d.action='APPROVE_MAPPING'
             LEFT JOIN "{SCHEMA}".supplier_mapping_review_candidates c
                    ON c.candidate_id=d.candidate_id
                   AND c.review_batch_id=d.review_batch_id
             LEFT JOIN "{SCHEMA}".supplier_mapping_review_batches b
                    ON b.review_batch_id=d.review_batch_id
             LEFT JOIN "{SCHEMA}".supplier_offers o ON o.offer_id=e.selected_offer_id
             LEFT JOIN "{SCHEMA}".vendors v
                    ON v.vendor_id=o.vendor_id AND v.vendor_id=d.vendor_id
                 WHERE h.variant_id=%s AND h.selection_scope='ROUTINE_PROCUREMENT_STANDARD' ''',
            (variant_id,),
        ).fetchone()
    if row is None:
        return None, selected_blocker_evidence(
            blocker="ROUTINE_SELECTED_OFFER_HEAD_REQUIRED",
            variant_id=variant_id,
            business_date=business_date,
            evaluation_at=evaluation_at,
        )
    event = row["selection_event"] or {}
    decision = row["mapping_decision"] or {}
    candidate = row["mapping_candidate"] or {}
    batch = row["review_batch"] or {}
    stale = (
        event.get("action") != "SELECT"
        or str(event.get("selection_event_id")) != row["selection_event_id"]
        or event.get("payload_sha256") != row["event_payload_sha256"]
        or row["event_variant_id"] != variant_id
        or row["event_scope"] != "ROUTINE_PROCUREMENT_STANDARD"
        or not decision
        or not candidate
        or not batch
        or event.get("selected_offer_id") != row["offer_id"]
        or str(event.get("mapping_decision_id")) != str(decision.get("mapping_decision_id"))
        or decision.get("variant_id") != variant_id
        or decision.get("result_offer_id") != row["offer_id"]
        or decision.get("offer_link_kind") not in {"LINKED_EXISTING", "CREATED_INACTIVE"}
        or decision.get("candidate_id") != candidate.get("candidate_id")
        or decision.get("review_batch_id") != batch.get("review_batch_id")
        or candidate.get("review_batch_id") != decision.get("review_batch_id")
        or decision.get("payload_sha256") != row["mapping_sha256"]
        or candidate.get("candidate_sha256") != row["candidate_payload_sha256"]
        or batch.get("payload_sha256") != row["batch_payload_sha256"]
        or decision.get("expected_batch_payload_sha256") != row["batch_payload_sha256"]
        or decision.get("expected_candidate_sha256") != row["candidate_payload_sha256"]
        or decision.get("reviewed_facts") != row["current_reviewed_facts"]
        or decision.get("evidence_set_sha256") != row["current_evidence_set_sha256"]
        or event.get("expected_mapping_decision_sha256") != row["mapping_sha256"]
        or event.get("expected_offer_contract_sha256") != row["offer_sha256"]
        or event.get("expected_catalog_sha256") != row["catalog_sha256"]
        or event.get("expected_vendor_sha256") != row["vendor_sha256"]
        or event.get("expected_rejection_memory_sha256") != row["rejection_sha256"]
        or decision.get("expected_catalog_sha256") != row["catalog_sha256"]
        or decision.get("expected_vendor_sha256") != row["vendor_sha256"]
        or decision.get("expected_rejection_memory_sha256") != row["rejection_sha256"]
        or decision.get("result_offer_contract_sha256") != row["offer_sha256"]
        or bool(row["active_rejection"])
        or row["offer_id"] is None
        or row["offer_variant_id"] != variant_id
        or row["active"] is not True
        or row["vendor_active"] is not True
        or row["variant_eligible"] is not True
        or row["package_type"] != "STANDARD"
        or row["confidence"] != "VERIFIED"
        or decision.get("offer_class") != "REGULAR"
        or decision.get("supplier_code_state") != "VALUE"
        or decision.get("supplier_code_value") != row["supplier_sku"]
        or decision.get("vendor_id") != row["vendor_id"]
        or decision.get("result_offer_package_type") != row["package_type"]
        or candidate.get("proposed_variant_id") != variant_id
        or candidate.get("proposed_vendor_id") != row["vendor_id"]
        or candidate.get("supplier_code_state") != "VALUE"
        or candidate.get("supplier_code_value") != row["supplier_sku"]
        or candidate.get("offer_class") != "REGULAR"
        or candidate.get("package_type_state") != "VALUE"
        or candidate.get("package_type_value") != row["package_type"]
        or candidate.get("size_value") != row["size_text"]
        or candidate.get("raw_pack_value") != row["raw_pack"]
        or candidate.get("shopify_units_value") != row["shopify_units_per_case"]
        or candidate.get("qualifying_units_value") != row["qualifying_units_per_case"]
        or candidate.get("assortment_scope_value") != row["assortment_scope"]
        or candidate.get("assortment_group_value") != row["assortment_group"]
        or candidate.get("assortable_value") != row["assortable"]
        or candidate.get("blockers") != []
        or decision.get("shopify_units_value") != row["shopify_units_per_case"]
        or decision.get("qualifying_units_value") != row["qualifying_units_per_case"]
        or batch.get("source_authority_state") != "NOT_APPROVED"
        or batch.get("source_import_state") != "NOT_IMPORT_READY"
        or batch.get("structural_state") != "READY"
        or batch.get("source_evidence_state") != "READY"
        or batch.get("semantic_state") != "READY"
    )
    if stale:
        return None, selected_blocker_evidence(
            blocker="ROUTINE_SELECTED_OFFER_LINEAGE_INVALID",
            variant_id=variant_id,
            business_date=business_date,
            evaluation_at=evaluation_at,
        )
    if row["event_effective_from"] is None or business_date < row["event_effective_from"] or (
        row["event_effective_through"] is not None
        and business_date > row["event_effective_through"]
    ):
        return None, selected_blocker_evidence(
            blocker="ROUTINE_SELECTED_OFFER_NOT_VALID_FOR_BUSINESS_DATE",
            variant_id=variant_id,
            business_date=business_date,
            evaluation_at=evaluation_at,
        )
    offer = (
        row["offer_id"],row["vendor_id"],row["supplier_sku"],row["package_type"],
        row["size_text"],row["raw_pack"],row["shopify_units_per_case"],
        row["qualifying_units_per_case"],row["assortment_scope"],
        row["assortment_group"],row["assortable"],row["confidence"],
        row["source_file"],row["source_page"],row["notes"],row["valid_from"],
        row["valid_to"],
    )
    evidence = {
        "contract": CONTRACT,
        "authority": "SYNTHETIC_TEST_ONLY",
        "recommendation_effect": "SELECTED_OFFER_INPUT_AUTHORITY",
        "temporal_semantics": "CURRENT_STATE_OBSERVED_IN_RUN_TRANSACTION",
        "historical_reconstruction": False,
        "business_date": business_date,
        "selection_observed_at": evaluation_at,
        "variant_id": variant_id,
        "selection_head": {
            "variant_id": variant_id,
            "scope": "ROUTINE_PROCUREMENT_STANDARD",
            "head_version": row["head_version"],
            "selection_event_id": row["selection_event_id"],
            "updated_at": row["updated_at"],
            "updated_txid": row["updated_txid"],
        },
        "selection_event": event,
        "mapping_decision": decision,
        "mapping_candidate": candidate,
        "review_batch": batch,
        "selected_offer": {
            "offer_id": row["offer_id"],
            "variant_id": row["offer_variant_id"],
            "vendor_id": row["vendor_id"],
            "supplier_sku": row["supplier_sku"],
            "package_type": row["package_type"],
            "size_text": row["size_text"],
            "raw_pack": row["raw_pack"],
            "shopify_units_per_case": row["shopify_units_per_case"],
            "qualifying_units_per_case": row["qualifying_units_per_case"],
            "assortment_scope": row["assortment_scope"],
            "assortment_group": row["assortment_group"],
            "assortable": row["assortable"],
            "confidence": row["confidence"],
            "active": row["active"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
        },
        "current_fingerprints": {
            "mapping_decision_sha256": row["mapping_sha256"],
            "offer_sha256": row["offer_sha256"],
            "catalog_sha256": row["catalog_sha256"],
            "vendor_sha256": row["vendor_sha256"],
            "rejection_memory_sha256": row["rejection_sha256"],
        },
        "commercial_source_authority": batch.get("source_authority_state"),
        "source_import_state": batch.get("source_import_state"),
        "offer_activation_performed": False,
        "price_mutation_performed": False,
        "shopify_action_performed": False,
        "po_release_performed": False,
        "real_or_commercial_authority": False,
    }
    return offer, evidence

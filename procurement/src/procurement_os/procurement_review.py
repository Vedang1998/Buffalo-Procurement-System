"""Run-scoped, append-only human review for Monday recommendations."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
import hashlib
import json
from typing import Any

from .monday_controls import (
    MondayControlError,
    classify_material_edit,
    load_material_edit_policy,
)
from .recommendations import (
    MondayRunInputValidationState,
    _fingerprint,
    validate_monday_run_inputs,
)
from .synthetic_selected_offer import (
    CONTRACT as SYNTHETIC_SELECTED_OFFER_CONTRACT,
    SyntheticSelectedOfferError,
    _valid_applicable_price_authority,
    selected_run_input_lock_scope,
)


REVIEW_LOCK = 5_920_230_601


class ProcurementReviewError(ValueError):
    pass


def _with_selected_input_locks(identifier: str):
    def decorate(function: Any) -> Any:
        @wraps(function)
        def locked(conn: Any, *args: Any, **kwargs: Any) -> Any:
            scope = (
                {"run_id": kwargs.get(identifier)}
                if identifier == "run_id"
                else {"recommendation_id": kwargs.get(identifier)}
            )
            try:
                with selected_run_input_lock_scope(
                    conn, **scope
                ):
                    return function(conn, *args, **kwargs)
            except SyntheticSelectedOfferError as exc:
                raise ProcurementReviewError(str(exc)) from exc

        return locked

    return decorate


def _whole(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ProcurementReviewError(f"{field} must be a nonnegative whole number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProcurementReviewError(f"{field} must be a nonnegative whole number") from exc
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
        raise ProcurementReviewError(f"{field} must be a nonnegative whole number")
    return int(parsed)


def _decision_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


_REVIEW_CONTEXT_SQL = """SELECT r.run_id,r.input_fingerprint,r.recommended_cases,
          r.recommended_loose_units,r.units_per_case,r.vendor_id,ru.workflow_stage,
          r.frozen_loose_order_allowed,r.offer_id,r.frozen_qualifying_units_per_case,
          r.frozen_loose_unit_fee,ru.input_fingerprint,ru.procurement_output_mode,r.metrics,
          r.baseline_units,r.recommended_units
     FROM procurement_recommendations r
     JOIN runs ru ON ru.run_id=r.run_id
    WHERE r.recommendation_id=%s"""


def _locked_review_context(conn: Any, recommendation_id: int) -> Any:
    from .synthetic_staging_database import is_staging_runtime_connection

    if is_staging_runtime_connection(conn):
        locked = conn.execute(
            "SELECT synthetic_staging_lock_recommendation(%s)",
            (recommendation_id,),
        ).fetchone()[0]
        row = conn.execute(
            _REVIEW_CONTEXT_SQL + " FOR UPDATE OF ru", (recommendation_id,)
        ).fetchone()
        if bool(locked) != (row is not None):
            raise ProcurementReviewError(
                "recommendation changed while acquiring its review lock"
            )
        return row
    return conn.execute(
        _REVIEW_CONTEXT_SQL + " FOR UPDATE OF r,ru", (recommendation_id,)
    ).fetchone()


def _validate_review_request(
    *, action: str, actor: str, expected_input_fingerprint: str, comment: str
) -> tuple[str, str, str]:
    selected_action = str(action).strip().upper()
    reviewer = str(actor).strip()
    note = str(comment).strip()
    if selected_action not in {"ACCEPT", "EDIT_QUANTITY", "REJECT"}:
        raise ProcurementReviewError("action must be ACCEPT, EDIT_QUANTITY, or REJECT")
    if not reviewer:
        raise ProcurementReviewError("review actor is required")
    if not isinstance(expected_input_fingerprint, str) or len(expected_input_fingerprint) != 64:
        raise ProcurementReviewError("expected frozen input fingerprint is required")
    return selected_action, reviewer, note


def _validate_review_context(
    conn: Any, row: Any, *, expected_input_fingerprint: str
) -> None:
    if row is None:
        raise ProcurementReviewError("unknown recommendation")
    if row[12] != "INTERNAL_DRAFT_ONLY":
        raise ProcurementReviewError("recommendation inputs changed; review is stale")
    validation = validate_monday_run_inputs(conn, str(row[0]), row[1])
    if (
        validation.state
        is MondayRunInputValidationState.FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED
    ):
        raise ProcurementReviewError(validation.state.value)
    if row[1] != expected_input_fingerprint or row[11] != row[1]:
        raise ProcurementReviewError("recommendation inputs changed; review is stale")
    if not validation.matches:
        raise ProcurementReviewError(
            "material recommendation inputs changed; prepare a new run"
        )


def _line_economics(
    conn: Any, row: Any, *, cases: int, loose: int
) -> dict[str, Any]:
    if loose and Decimal(row[10] or 0) > 0:
        raise ProcurementReviewError("LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED")
    price = conn.execute(
        """SELECT run_price_snapshot_id,offer_id,effective_month,level_type,
                  break_qty,break_unit,case_price,unit_price,source_file,source_page,
                  (to_jsonb(s)->>'source_price_id')::bigint,
                  to_jsonb(s)->>'source_price_book_batch_id',
                  (to_jsonb(s)->>'source_price_book_row_number')::integer,
                  to_jsonb(s)->>'supplier_price_authority_event_id'
             FROM run_price_snapshots s
            WHERE run_id=%s AND offer_id=%s
              AND (
                  level_type='BASE'
                  OR (level_type='BREAK' AND break_unit='CS' AND break_qty<=%s)
                  OR (level_type='BREAK' AND break_unit='BT' AND break_qty<=%s)
              )
            ORDER BY unit_price ASC,run_price_snapshot_id DESC LIMIT 1""",
        (row[0], row[8], cases, Decimal(cases) * Decimal(row[9])),
    ).fetchone()
    if price is None:
        raise ProcurementReviewError("reviewed quantity has no frozen applicable price")
    unit_cost = Decimal(price[7])
    case_price = Decimal(price[6]) if price[6] is not None else None
    case_merchandise = (
        Decimal(cases) * case_price
        if case_price is not None
        else Decimal(cases * int(row[4])) * unit_cost
    )
    merchandise_total = (
        case_merchandise + Decimal(loose) * unit_cost
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    result = {
        "unit_cost": unit_cost,
        "case_price": case_price,
        "merchandise_total": merchandise_total,
        "loose_order_fee": Decimal("0.00"),
        "line_total": merchandise_total,
    }
    metrics = row[13] or {}
    selected_evidence = metrics.get("selected_offer_input_evidence")
    if selected_evidence is not None:
        if selected_evidence.get("contract") != SYNTHETIC_SELECTED_OFFER_CONTRACT:
            raise ProcurementReviewError("selected offer price evidence is malformed")
        ladder = selected_evidence.get("applicable_price_ladder")
        rows = ladder.get("rows") if isinstance(ladder, dict) else None
        if (
            not isinstance(rows, list)
            or ladder.get("sha256") != _fingerprint(rows)
            or ladder.get("sha256") != metrics.get("selected_price_ladder_sha256")
        ):
            raise ProcurementReviewError("selected offer price ladder is not bound")

        def normalized(values: Any) -> tuple[Any, ...]:
            return tuple(
                None if value is None else value if isinstance(value, (bool, int)) else str(value)
                for value in values
            )

        snapshot_signature = normalized(price[1:10])
        matches = [source for source in rows if normalized(source[1:]) == snapshot_signature]
        if len(matches) != 1:
            raise ProcurementReviewError(
                "selected final price tier has no unique frozen source identity"
            )
        source = matches[0]
        result["final_price_tier"] = {
            "price_id": int(source[0]),
            "run_price_snapshot_id": int(price[0]),
            "level_type": str(price[3]),
            "break_qty": str(price[4]) if price[4] is not None else None,
            "break_unit": str(price[5]) if price[5] is not None else None,
            "unit_price": str(price[7]),
            "case_price": str(price[6]) if price[6] is not None else None,
            "price_ladder_sha256": ladder["sha256"],
        }
        authority_present = "applicable_price_authority" in selected_evidence
        authority = selected_evidence.get("applicable_price_authority")
        snapshot_lineage = price[10:14]
        if authority_present:
            selected_offer = selected_evidence.get("selected_offer")
            if not _valid_applicable_price_authority(
                authority, ladder, selected_offer
            ):
                raise ProcurementReviewError(
                    "selected offer price authority is malformed"
                )
            bindings = [
                item
                for item in authority["prices"]
                if item.get("price_id") == int(source[0])
            ]
            if (
                len(bindings) != 1
                or any(value is None for value in snapshot_lineage)
                or int(price[10]) != int(source[0])
                or str(price[11])
                != authority["source_batch"]["price_book_batch_id"]
                or int(price[12]) != int(bindings[0]["source_row_number"])
                or str(price[13]) != authority["apply_event"]["event_id"]
            ):
                raise ProcurementReviewError(
                    "selected final price tier authority is not bound"
                )
            result["final_price_tier"].update(
                {
                    "source_price_book_batch_id": str(price[11]),
                    "source_price_book_row_number": int(price[12]),
                    "supplier_price_authority_event_id": str(price[13]),
                    "applicable_price_authority_sha256": authority["sha256"],
                }
            )
        elif any(value is not None for value in snapshot_lineage):
            raise ProcurementReviewError(
                "selected final price tier has unbound authority lineage"
            )
    return result


def _calculate_review(
    conn: Any,
    row: Any,
    *,
    recommendation_id: int,
    selected_action: str,
    reviewer: str,
    note: str,
    approved_cases: Any | None,
    approved_loose_units: Any | None,
) -> dict[str, Any]:
    """Calculate the exact immutable decision payload from frozen run evidence."""

    if selected_action == "ACCEPT":
        cases = int(row[2])
        loose = int(row[3])
    elif selected_action == "REJECT":
        if not note:
            raise ProcurementReviewError("REJECT requires a comment")
        cases = loose = 0
    else:
        if not note:
            raise ProcurementReviewError("EDIT_QUANTITY requires a comment")
        cases = _whole(approved_cases, "approved_cases")
        loose = _whole(approved_loose_units, "approved_loose_units")
        if loose and row[7] is not True:
            raise ProcurementReviewError("vendor rules do not permit loose ordering")
    pack = int(row[4])
    if loose >= pack:
        raise ProcurementReviewError("loose units must be normalized below one full case")
    approved_units = cases * pack + loose
    if selected_action == "REJECT":
        approved_unit_cost = Decimal("0")
        approved_case_price = None
        approved_merchandise_total = Decimal("0")
        approved_loose_order_fee = Decimal("0")
        approved_line_total = Decimal("0")
        final_price_tier = None
    else:
        economics = _line_economics(conn, row, cases=cases, loose=loose)
        approved_unit_cost = Decimal(economics["unit_cost"])
        approved_case_price = economics["case_price"]
        approved_merchandise_total = Decimal(economics["merchandise_total"])
        approved_loose_order_fee = Decimal(economics["loose_order_fee"])
        approved_line_total = Decimal(economics["line_total"])
        final_price_tier = economics.get("final_price_tier")
    metrics = row[13] or {}
    try:
        resulting_inventory_units = (
            Decimal(str(metrics["available_units"]))
            + Decimal(str(metrics["trusted_incoming_units"]))
            + Decimal(approved_units)
        )
        forecast_daily_velocity = Decimal(str(metrics["forecast_daily_velocity"]))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise ProcurementReviewError(
            "frozen inventory and forecast evidence is incomplete"
        ) from exc
    if (
        not resulting_inventory_units.is_finite()
        or resulting_inventory_units < 0
        or not forecast_daily_velocity.is_finite()
        or forecast_daily_velocity < 0
    ):
        raise ProcurementReviewError("frozen inventory and forecast evidence is invalid")
    if forecast_daily_velocity == 0:
        resulting_days_supply = None
        days_supply_status = "UNDEFINED_ZERO_FORECAST"
    else:
        resulting_days_supply = (
            resulting_inventory_units / forecast_daily_velocity
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        days_supply_status = "CALCULATED_FROM_FROZEN_FORECAST"
    try:
        policy = load_material_edit_policy()
    except MondayControlError as exc:
        raise ProcurementReviewError(str(exc)) from exc
    if metrics.get("material_edit_policy") != policy.evidence():
        raise ProcurementReviewError("frozen material-edit policy is stale or invalid")
    recommended_economics = _line_economics(
        conn, row, cases=int(row[2]), loose=int(row[3])
    )
    materiality = classify_material_edit(
        action=selected_action,
        baseline_units=Decimal(row[14]),
        recommended_units=Decimal(row[15]),
        edited_units=Decimal(approved_units),
        resulting_days_supply=resulting_days_supply,
        days_supply_status=days_supply_status,
        recommended_line_cash=Decimal(recommended_economics["line_total"]),
        final_line_cash=approved_line_total,
        policy=policy,
    )
    payload = {
        "recommendation_id": int(recommendation_id),
        "run_id": str(row[0]),
        "input_fingerprint": row[1],
        "action": selected_action,
        "approved_cases": cases,
        "approved_loose_units": loose,
        "approved_units": approved_units,
        "approved_unit_cost": str(approved_unit_cost),
        "approved_case_price": (
            str(approved_case_price) if approved_case_price is not None else None
        ),
        "approved_merchandise_total": str(approved_merchandise_total),
        "approved_loose_order_fee": str(approved_loose_order_fee),
        "approved_line_total": str(approved_line_total),
        "resulting_inventory_units": str(resulting_inventory_units),
        "resulting_days_supply": (
            str(resulting_days_supply) if resulting_days_supply is not None else None
        ),
        "days_supply_status": days_supply_status,
        "materiality_tier": materiality["materiality_tier"],
        "materiality_reason_codes": materiality["materiality_reason_codes"],
        "baseline_units": str(materiality["baseline_units"]),
        "recommended_units": str(materiality["recommended_units"]),
        "baseline_multiplier": (
            str(materiality["baseline_multiplier"])
            if materiality["baseline_multiplier"] is not None else None
        ),
        "baseline_multiplier_status": materiality["baseline_multiplier_status"],
        "recommended_line_cash": str(materiality["recommended_line_cash"]),
        "incremental_line_cash": str(materiality["incremental_line_cash"]),
        "final_line_cash": str(materiality["final_line_cash"]),
        "material_edit_policy": materiality["policy"],
        "comment": note,
        "actor": reviewer,
    }
    if metrics.get("selected_offer_input_evidence") is not None:
        payload["final_price_tier"] = final_price_tier
    result = {
        "run_id": str(row[0]),
        "action": selected_action,
        "approved_cases": cases,
        "approved_loose_units": loose,
        "approved_units": approved_units,
        "approved_unit_cost": approved_unit_cost,
        "approved_case_price": approved_case_price,
        "approved_merchandise_total": approved_merchandise_total,
        "approved_loose_order_fee": approved_loose_order_fee,
        "approved_line_total": approved_line_total,
        "resulting_inventory_units": resulting_inventory_units,
        "resulting_days_supply": resulting_days_supply,
        "days_supply_status": days_supply_status,
        "materiality": materiality,
        "decision_fingerprint": _decision_fingerprint(payload),
        "payload": payload,
    }
    if metrics.get("selected_offer_input_evidence") is not None:
        result["final_price_tier"] = final_price_tier
    return result


@_with_selected_input_locks("recommendation_id")
def preview_recommendation_review(
    conn: Any,
    *,
    recommendation_id: int,
    action: str,
    actor: str,
    expected_input_fingerprint: str,
    approved_cases: Any | None = None,
    approved_loose_units: Any | None = None,
    comment: str = "",
) -> dict[str, Any]:
    """Read-only preview of ACCEPT/EDIT economics before append-only confirmation."""

    selected_action, reviewer, note = _validate_review_request(
        action=action,
        actor=actor,
        expected_input_fingerprint=expected_input_fingerprint,
        comment=comment,
    )
    if selected_action not in {"ACCEPT", "EDIT_QUANTITY"}:
        raise ProcurementReviewError("ACCEPT or EDIT_QUANTITY is required for economics preview")
    with conn.transaction():
        row = conn.execute(_REVIEW_CONTEXT_SQL, (recommendation_id,)).fetchone()
        _validate_review_context(
            conn, row, expected_input_fingerprint=expected_input_fingerprint
        )
        if row[6] != "AWAITING_REVIEW":
            raise ProcurementReviewError("recommendation is not awaiting review")
        if conn.execute(
            "SELECT 1 FROM review_decisions WHERE recommendation_id=%s",
            (recommendation_id,),
        ).fetchone() is not None:
            raise ProcurementReviewError(
                "recommendation already has an immutable review decision"
            )
        calculated = _calculate_review(
            conn,
            row,
            recommendation_id=recommendation_id,
            selected_action=selected_action,
            reviewer=reviewer,
            note=note,
            approved_cases=approved_cases,
            approved_loose_units=approved_loose_units,
        )
    return {
        **{key: value for key, value in calculated.items() if key != "payload"},
        "preview_fingerprint": calculated["decision_fingerprint"],
        "input_fingerprint": expected_input_fingerprint,
        "metrics": row[13],
    }


def _advance_when_review_complete(conn: Any, run_id: str) -> None:
    pending = conn.execute(
        """SELECT count(*) FROM procurement_recommendations r
            WHERE r.run_id=%s AND NOT EXISTS (
                SELECT 1 FROM review_decisions d
                 WHERE d.recommendation_id=r.recommendation_id
            )""",
        (run_id,),
    ).fetchone()[0]
    blockers = conn.execute(
        "SELECT monday_effective_material_blocker_count(%s)", (run_id,)
    ).fetchone()[0]
    if int(pending) == 0 and int(blockers) == 0:
        conn.execute(
            "UPDATE runs SET workflow_stage='REVIEWED' WHERE run_id=%s", (run_id,)
        )


@_with_selected_input_locks("run_id")
def acknowledge_and_exclude_blocked_item(
    conn: Any,
    *,
    run_id: str,
    exception_id: int,
    actor: str,
    reason: str,
    expected_input_fingerprint: str,
) -> dict[str, Any]:
    """Append one fingerprint-bound RUN_ONLY exclusion without altering its blocker."""

    operator = str(actor).strip()
    note = str(reason).strip()
    if not operator or not note:
        raise ProcurementReviewError("exclusion actor and reason are required")
    if not isinstance(expected_input_fingerprint, str) or len(
        expected_input_fingerprint
    ) != 64:
        raise ProcurementReviewError("expected frozen input fingerprint is required")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if not conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (REVIEW_LOCK,)
        ).fetchone()[0]:
            raise ProcurementReviewError("review lock is unavailable")
        run = conn.execute(
            """SELECT workflow_stage,input_fingerprint,procurement_output_mode
                 FROM runs WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'
                 FOR UPDATE""",
            (run_id,),
        ).fetchone()
        if run is None or run[2] != "INTERNAL_DRAFT_ONLY":
            raise ProcurementReviewError("blocked-item exclusion is stale or has no run")
        validation = validate_monday_run_inputs(conn, str(run_id), run[1])
        if (
            validation.state
            is MondayRunInputValidationState.FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED
        ):
            raise ProcurementReviewError(validation.state.value)
        if run[1] != expected_input_fingerprint:
            raise ProcurementReviewError("blocked-item exclusion is stale or has no run")
        if not validation.matches:
            raise ProcurementReviewError(
                "material recommendation inputs changed; prepare a new run"
            )
        from .synthetic_staging_database import is_staging_runtime_connection

        if is_staging_runtime_connection(conn):
            locked = conn.execute(
                "SELECT synthetic_staging_lock_exception(%s)", (exception_id,)
            ).fetchone()[0]
            blocker = conn.execute(
                """SELECT exception_id,variant_id,vendor_id,exception_type,severity,
                          message,status
                     FROM exceptions WHERE exception_id=%s""",
                (exception_id,),
            ).fetchone()
            if bool(locked) != (blocker is not None):
                raise ProcurementReviewError(
                    "blocked item changed while acquiring its review lock"
                )
        else:
            blocker = conn.execute(
                """SELECT exception_id,variant_id,vendor_id,exception_type,severity,
                          message,status
                     FROM exceptions WHERE exception_id=%s FOR UPDATE""",
                (exception_id,),
            ).fetchone()
        if (
            blocker is None
            or blocker[3] != "MONDAY_INPUT_BLOCKER"
            or blocker[4] not in {"HIGH", "CRITICAL"}
            or blocker[6] != "OPEN"
        ):
            raise ProcurementReviewError(
                "only an immutable open Monday input blocker can be excluded"
            )
        existing = conn.execute(
            """SELECT exclusion_id,run_id,variant_id,input_fingerprint,actor,reason,
                      created_at
                 FROM monday_run_blocker_exclusions WHERE exception_id=%s""",
            (exception_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing[1]) == str(run_id)
                and existing[2] == blocker[1]
                and existing[3] == expected_input_fingerprint
                and existing[4] == operator
                and existing[5] == note
            ):
                return {
                    "exclusion_id": int(existing[0]),"run_id": str(run_id),
                    "exception_id": int(exception_id),"variant_id": blocker[1],
                    "actor": operator,"reason": note,"created_at": existing[6],
                    "idempotent_replay": True,
                }
            raise ProcurementReviewError(
                "blocked item already has a different immutable exclusion"
            )
        if run[0] != "AWAITING_REVIEW":
            raise ProcurementReviewError(
                "blocked-item exclusion must occur before DRAFT review completes"
            )
        evidence = {
            "original_exception_type": blocker[3],
            "original_severity": blocker[4],
            "original_message": blocker[5],
            "original_vendor_id": str(blocker[2]) if blocker[2] else None,
            "original_variant_id": blocker[1],
            "action_effect": "EXCLUDED_FROM_THIS_RUN_ONLY",
            "global_rules_changed": False,
        }
        exclusion = conn.execute(
            """INSERT INTO monday_run_blocker_exclusions(
                       run_id,exception_id,variant_id,input_fingerprint,action,scope,
                       actor,reason,evidence_json)
                VALUES (%s,%s,%s,%s,'ACKNOWLEDGE_AND_EXCLUDE','RUN_ONLY',%s,%s,%s::jsonb)
                RETURNING exclusion_id,created_at""",
            (
                run_id,exception_id,blocker[1],expected_input_fingerprint,
                operator,note,json.dumps(evidence,sort_keys=True),
            ),
        ).fetchone()
        _advance_when_review_complete(conn, str(run_id))
    return {
        "exclusion_id": int(exclusion[0]),"run_id": str(run_id),
        "exception_id": int(exception_id),"variant_id": blocker[1],
        "actor": operator,"reason": note,"created_at": exclusion[1],
        "idempotent_replay": False,
    }


@_with_selected_input_locks("recommendation_id")
def confirm_material_recommendation_edit(
    conn: Any,
    *,
    recommendation_id: int,
    actor: str,
    expected_input_fingerprint: str,
    expected_review_preview_fingerprint: str,
    approved_cases: Any,
    approved_loose_units: Any,
    comment: str,
    confirmation_reason: str,
) -> dict[str, Any]:
    """Record the distinct audited confirmation required by a MATERIAL edit."""

    reason = str(confirmation_reason).strip()
    if not reason:
        raise ProcurementReviewError("MATERIAL edit confirmation reason is required")
    selected_action, reviewer, note = _validate_review_request(
        action="EDIT_QUANTITY", actor=actor,
        expected_input_fingerprint=expected_input_fingerprint, comment=comment,
    )
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if not conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (REVIEW_LOCK,)
        ).fetchone()[0]:
            raise ProcurementReviewError("review lock is unavailable")
        row = _locked_review_context(conn, recommendation_id)
        _validate_review_context(
            conn, row, expected_input_fingerprint=expected_input_fingerprint
        )
        calculated = _calculate_review(
            conn,row,recommendation_id=recommendation_id,
            selected_action=selected_action,reviewer=reviewer,note=note,
            approved_cases=approved_cases,approved_loose_units=approved_loose_units,
        )
        if calculated["materiality"]["materiality_tier"] != "MATERIAL":
            raise ProcurementReviewError(
                "a distinct confirmation is accepted only for a MATERIAL edit"
            )
        if calculated["decision_fingerprint"] != expected_review_preview_fingerprint:
            raise ProcurementReviewError("MATERIAL edit preview changed before confirmation")
        if row[6] != "AWAITING_REVIEW":
            raise ProcurementReviewError("recommendation is not awaiting review")
        existing_decision = conn.execute(
            "SELECT 1 FROM review_decisions WHERE recommendation_id=%s",
            (recommendation_id,),
        ).fetchone()
        if existing_decision is not None:
            raise ProcurementReviewError(
                "recommendation already has an immutable review decision"
            )
        existing = conn.execute(
            """SELECT material_edit_confirmation_id,confirmed_by,reason,approved_cases,
                      approved_loose_units,approved_units,created_at
                 FROM monday_material_edit_confirmations
                WHERE recommendation_id=%s AND review_preview_fingerprint=%s""",
            (recommendation_id,expected_review_preview_fingerprint),
        ).fetchone()
        if existing is not None:
            if (
                existing[1] == reviewer and existing[2] == reason
                and int(existing[3]) == calculated["approved_cases"]
                and int(existing[4]) == calculated["approved_loose_units"]
                and int(existing[5]) == calculated["approved_units"]
            ):
                return {
                    "material_edit_confirmation_id": int(existing[0]),
                    "run_id": calculated["run_id"],
                    "review_preview_fingerprint": expected_review_preview_fingerprint,
                    "materiality": calculated["materiality"],
                    "created_at": existing[6],"idempotent_replay": True,
                }
            raise ProcurementReviewError(
                "MATERIAL edit preview already has a different immutable confirmation"
            )
        evidence = {
            **calculated["materiality"],
            "materiality_tier": "MATERIAL",
            "review_comment": note,
            "confirmation_reason": reason,
        }
        if "final_price_tier" in calculated:
            evidence["final_price_tier"] = calculated["final_price_tier"]
        confirmation = conn.execute(
            """INSERT INTO monday_material_edit_confirmations(
                       run_id,recommendation_id,input_fingerprint,
                       review_preview_fingerprint,approved_cases,approved_loose_units,
                       approved_units,action,confirmed_by,reason,evidence_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'CONFIRM_MATERIAL_EDIT',%s,%s,%s::jsonb)
                RETURNING material_edit_confirmation_id,created_at""",
            (
                calculated["run_id"],recommendation_id,expected_input_fingerprint,
                expected_review_preview_fingerprint,calculated["approved_cases"],
                calculated["approved_loose_units"],calculated["approved_units"],
                reviewer,reason,json.dumps(evidence,default=str,sort_keys=True),
            ),
        ).fetchone()
    return {
        "material_edit_confirmation_id": int(confirmation[0]),
        "run_id": calculated["run_id"],
        "review_preview_fingerprint": expected_review_preview_fingerprint,
        "materiality": calculated["materiality"],
        "created_at": confirmation[1],"idempotent_replay": False,
    }


@_with_selected_input_locks("recommendation_id")
def record_recommendation_review(
    conn: Any,
    *,
    recommendation_id: int,
    action: str,
    actor: str,
    expected_input_fingerprint: str,
    approved_cases: Any | None = None,
    approved_loose_units: Any | None = None,
    comment: str = "",
    expected_review_preview_fingerprint: str | None = None,
    material_edit_confirmation_id: int | None = None,
) -> dict[str, Any]:
    """Record exactly one ACCEPT, EDIT_QUANTITY, or REJECT decision."""

    selected_action, reviewer, note = _validate_review_request(
        action=action,
        actor=actor,
        expected_input_fingerprint=expected_input_fingerprint,
        comment=comment,
    )
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (REVIEW_LOCK,)).fetchone()[0]:
            raise ProcurementReviewError("review lock is unavailable")
        row = _locked_review_context(conn, recommendation_id)
        _validate_review_context(
            conn, row, expected_input_fingerprint=expected_input_fingerprint
        )
        existing = conn.execute(
            """SELECT decision_id,action,approved_cases,approved_loose_units,
                      decision_fingerprint,approved_unit_cost,approved_line_total,
                      material_edit_confirmation_id
                 FROM review_decisions
                WHERE recommendation_id=%s""",
            (recommendation_id,),
        ).fetchone()

        calculated = _calculate_review(
            conn,
            row,
            recommendation_id=recommendation_id,
            selected_action=selected_action,
            reviewer=reviewer,
            note=note,
            approved_cases=approved_cases,
            approved_loose_units=approved_loose_units,
        )
        cases = calculated["approved_cases"]
        loose = calculated["approved_loose_units"]
        approved_units = calculated["approved_units"]
        approved_unit_cost = calculated["approved_unit_cost"]
        approved_case_price = calculated["approved_case_price"]
        approved_merchandise_total = calculated["approved_merchandise_total"]
        approved_loose_order_fee = calculated["approved_loose_order_fee"]
        approved_line_total = calculated["approved_line_total"]
        payload = calculated["payload"]
        fingerprint = calculated["decision_fingerprint"]
        if existing is not None:
            if (
                existing[1] == selected_action
                and int(existing[2]) == cases
                and int(existing[3]) == loose
                and existing[4] == fingerprint
                and Decimal(existing[5]) == approved_unit_cost
                and Decimal(existing[6]) == approved_line_total
                and existing[7] == material_edit_confirmation_id
            ):
                return {
                    "decision_id": int(existing[0]),"run_id": str(row[0]),"action": selected_action,
                    "approved_cases": cases,"approved_loose_units": loose,"approved_units": approved_units,
                    "approved_unit_cost": approved_unit_cost,
                    "approved_case_price": approved_case_price,
                    "approved_merchandise_total": approved_merchandise_total,
                    "approved_loose_order_fee": approved_loose_order_fee,
                    "approved_line_total": approved_line_total,
                    "resulting_inventory_units": calculated["resulting_inventory_units"],
                    "resulting_days_supply": calculated["resulting_days_supply"],
                    "days_supply_status": calculated["days_supply_status"],
                    "materiality": calculated["materiality"],
                    "material_edit_confirmation_id": existing[7],
                    "idempotent_replay": True,
                } | (
                    {"final_price_tier": calculated["final_price_tier"]}
                    if "final_price_tier" in calculated
                    else {}
                )
            raise ProcurementReviewError("recommendation already has a different immutable review decision")
        if (
            selected_action in {"ACCEPT", "EDIT_QUANTITY"}
            and expected_review_preview_fingerprint != fingerprint
        ):
            raise ProcurementReviewError(
                "accepted or edited economics must be previewed and confirmed without changes"
            )
        materiality_tier = calculated["materiality"]["materiality_tier"]
        if selected_action == "EDIT_QUANTITY" and materiality_tier == "MATERIAL":
            confirmation = conn.execute(
                """SELECT run_id,recommendation_id,input_fingerprint,
                          review_preview_fingerprint,approved_cases,
                          approved_loose_units,approved_units
                     FROM monday_material_edit_confirmations
                    WHERE material_edit_confirmation_id=%s""",
                (material_edit_confirmation_id,),
            ).fetchone()
            if (
                confirmation is None
                or str(confirmation[0]) != str(row[0])
                or int(confirmation[1]) != int(recommendation_id)
                or confirmation[2] != row[1]
                or confirmation[3] != fingerprint
                or int(confirmation[4]) != cases
                or int(confirmation[5]) != loose
                or int(confirmation[6]) != approved_units
            ):
                raise ProcurementReviewError(
                    "MATERIAL EDIT_QUANTITY requires its exact distinct confirmation"
                )
        elif material_edit_confirmation_id is not None:
            raise ProcurementReviewError(
                "NORMAL review cannot consume a material-edit confirmation"
            )
        if row[6] != "AWAITING_REVIEW":
            raise ProcurementReviewError("recommendation is not awaiting review")
        decision_evidence = dict(payload)
        decision_evidence["material_edit_confirmation_id"] = (
            int(material_edit_confirmation_id)
            if material_edit_confirmation_id is not None else None
        )
        decision_id = conn.execute(
            """INSERT INTO review_decisions(
                       run_id,recommendation_id,decision_type,scope,action,comment,decided_by,
                       writeback_type,input_fingerprint,decision_fingerprint,approved_cases,
                       approved_loose_units,approved_units,approved_unit_cost,
                       approved_line_total,evidence_json,material_edit_confirmation_id)
                VALUES (%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY',%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING decision_id""",
            (
                row[0],recommendation_id,selected_action,note or None,reviewer,
                "EDIT_QUANTITY" if selected_action == "EDIT_QUANTITY" else "RUN_DECISION",
                row[1],fingerprint,cases,loose,approved_units,approved_unit_cost,
                approved_line_total,
                json.dumps({"review": decision_evidence, "safety_label": "TEST DATA — NOT FOR ORDERING"}, sort_keys=True),
                material_edit_confirmation_id,
            ),
        ).fetchone()[0]
        _advance_when_review_complete(conn, str(row[0]))
    return {
        "decision_id": int(decision_id),"run_id": str(row[0]),"action": selected_action,
        "approved_cases": cases,"approved_loose_units": loose,"approved_units": approved_units,
        "approved_unit_cost": approved_unit_cost,
        "approved_case_price": approved_case_price,
        "approved_merchandise_total": approved_merchandise_total,
        "approved_loose_order_fee": approved_loose_order_fee,
        "approved_line_total": approved_line_total,
        "resulting_inventory_units": calculated["resulting_inventory_units"],
        "resulting_days_supply": calculated["resulting_days_supply"],
        "days_supply_status": calculated["days_supply_status"],
        "materiality": calculated["materiality"],
        "material_edit_confirmation_id": material_edit_confirmation_id,
        "idempotent_replay": False,
    } | (
        {"final_price_tier": calculated["final_price_tier"]}
        if "final_price_tier" in calculated
        else {}
    )


def list_review_queue(conn: Any, run_id: str) -> dict[str, Any]:
    with conn.transaction():
        run = conn.execute(
            """SELECT workflow_stage,input_fingerprint,business_date FROM runs
                WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'
                  AND procurement_output_mode='INTERNAL_DRAFT_ONLY'""",
            (run_id,),
        ).fetchone()
        if run is None:
            raise ProcurementReviewError("unknown Monday run")
        rows = conn.execute(
            """SELECT r.recommendation_id,r.variant_id,v.product_title,v.variant_title,
                      COALESCE(r.metrics->>'frozen_vendor_name',ve.vendor_name),r.recommended_cases,r.recommended_loose_units,r.recommended_units,
                      r.recommended_unit_cost,r.metrics,d.decision_id,d.action,d.approved_cases,
                      d.approved_loose_units,d.comment,d.approved_units,
                      d.approved_unit_cost,d.approved_line_total,d.evidence_json
                 FROM procurement_recommendations r JOIN variants v ON v.variant_id=r.variant_id
                 JOIN vendors ve ON ve.vendor_id=r.vendor_id
                 LEFT JOIN review_decisions d ON d.recommendation_id=r.recommendation_id
                WHERE r.run_id=%s ORDER BY r.vendor_id,v.product_title,v.variant_title,r.variant_id""",
            (run_id,),
        ).fetchall()
    return {
        "run_id": str(run_id),"workflow_stage": run[0],"input_fingerprint": run[1],
        "business_date": run[2],"safety_label": "TEST DATA — NOT FOR ORDERING",
        "items": [
            {
                "recommendation_id": int(row[0]),"variant_id": row[1],"product_title": row[2],
                "variant_title": row[3],"vendor_name": row[4],"recommended_cases": int(row[5]),
                "recommended_loose_units": int(row[6]),"recommended_units": int(row[7]),
                "unit_cost": Decimal(row[8]),"metrics": row[9],"decision_id": int(row[10]) if row[10] else None,
                "action": row[11],"approved_cases": int(row[12]) if row[12] is not None else None,
                "approved_loose_units": int(row[13]) if row[13] is not None else None,
                "comment": row[14],
                "approved_units": int(row[15]) if row[15] is not None else None,
                "approved_unit_cost": Decimal(row[16]) if row[16] is not None else None,
                "approved_line_total": Decimal(row[17]) if row[17] is not None else None,
                "approved_case_price": (
                    Decimal((row[18] or {}).get("review", {})["approved_case_price"])
                    if (row[18] or {}).get("review", {}).get("approved_case_price")
                    is not None
                    else None
                ),
                "approved_merchandise_total": (
                    Decimal(
                        (row[18] or {}).get("review", {})[
                            "approved_merchandise_total"
                        ]
                    )
                    if (row[18] or {}).get("review", {}).get(
                        "approved_merchandise_total"
                    ) is not None
                    else None
                ),
                "approved_loose_order_fee": (
                    Decimal(
                        (row[18] or {}).get("review", {})[
                            "approved_loose_order_fee"
                        ]
                    )
                    if (row[18] or {}).get("review", {}).get(
                        "approved_loose_order_fee"
                    ) is not None
                    else None
                ),
                "resulting_inventory_units": (
                    (row[18] or {}).get("review", {}).get("resulting_inventory_units")
                ),
                "resulting_days_supply": (
                    (row[18] or {}).get("review", {}).get("resulting_days_supply")
                ),
                "days_supply_status": (
                    (row[18] or {}).get("review", {}).get("days_supply_status")
                ),
                "materiality_tier": (
                    (row[18] or {}).get("review", {}).get("materiality_tier")
                ),
                "materiality_reason_codes": (
                    (row[18] or {}).get("review", {}).get(
                        "materiality_reason_codes", []
                    )
                ),
                "baseline_multiplier": (
                    (row[18] or {}).get("review", {}).get("baseline_multiplier")
                ),
                "incremental_line_cash": (
                    (row[18] or {}).get("review", {}).get("incremental_line_cash")
                ),
                "material_edit_confirmation_id": (
                    (row[18] or {}).get("review", {}).get(
                        "material_edit_confirmation_id"
                    )
                ),
                "final_price_tier": (
                    (row[18] or {}).get("review", {}).get("final_price_tier")
                ),
            }
            for row in rows
        ],
    }

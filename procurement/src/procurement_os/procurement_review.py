"""Run-scoped, append-only human review for Monday recommendations."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from .recommendations import monday_run_inputs_match


REVIEW_LOCK = 5_920_230_601


class ProcurementReviewError(ValueError):
    pass


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
          r.frozen_loose_unit_fee,ru.input_fingerprint,ru.procurement_output_mode,r.metrics
     FROM procurement_recommendations r
     JOIN runs ru ON ru.run_id=r.run_id
    WHERE r.recommendation_id=%s"""


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
    if (
        row[1] != expected_input_fingerprint
        or row[11] != row[1]
        or row[12] != "INTERNAL_DRAFT_ONLY"
    ):
        raise ProcurementReviewError("recommendation inputs changed; review is stale")
    if not monday_run_inputs_match(conn, str(row[0]), row[1]):
        raise ProcurementReviewError(
            "material recommendation inputs changed; prepare a new run"
        )


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
    else:
        price = conn.execute(
            """SELECT unit_price,case_price FROM run_price_snapshots
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
        approved_unit_cost = Decimal(price[0])
        approved_case_price = Decimal(price[1]) if price[1] is not None else None
        case_merchandise = (
            Decimal(cases) * approved_case_price
            if approved_case_price is not None
            else Decimal(cases * pack) * approved_unit_cost
        )
        approved_merchandise_total = (
            case_merchandise + Decimal(loose) * approved_unit_cost
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        approved_loose_order_fee = (
            Decimal(row[10]) if loose else Decimal("0")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        approved_line_total = (
            approved_merchandise_total + approved_loose_order_fee
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
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
        "comment": note,
        "actor": reviewer,
    }
    return {
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
        "decision_fingerprint": _decision_fingerprint(payload),
        "payload": payload,
    }


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
        row = conn.execute(
            _REVIEW_CONTEXT_SQL + " FOR UPDATE OF r,ru", (recommendation_id,)
        ).fetchone()
        _validate_review_context(
            conn, row, expected_input_fingerprint=expected_input_fingerprint
        )
        existing = conn.execute(
            """SELECT decision_id,action,approved_cases,approved_loose_units,
                      decision_fingerprint,approved_unit_cost,approved_line_total
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
                    "idempotent_replay": True,
                }
            raise ProcurementReviewError("recommendation already has a different immutable review decision")
        if (
            selected_action in {"ACCEPT", "EDIT_QUANTITY"}
            and expected_review_preview_fingerprint != fingerprint
        ):
            raise ProcurementReviewError(
                "accepted or edited economics must be previewed and confirmed without changes"
            )
        if row[6] != "AWAITING_REVIEW":
            raise ProcurementReviewError("recommendation is not awaiting review")
        decision_id = conn.execute(
            """INSERT INTO review_decisions(
                       run_id,recommendation_id,decision_type,scope,action,comment,decided_by,
                       writeback_type,input_fingerprint,decision_fingerprint,approved_cases,
                       approved_loose_units,approved_units,approved_unit_cost,
                       approved_line_total,evidence_json)
                VALUES (%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY',%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING decision_id""",
            (
                row[0],recommendation_id,selected_action,note or None,reviewer,
                "EDIT_QUANTITY" if selected_action == "EDIT_QUANTITY" else "RUN_DECISION",
                row[1],fingerprint,cases,loose,approved_units,approved_unit_cost,
                approved_line_total,
                json.dumps({"review": payload, "safety_label": "TEST DATA — NOT FOR ORDERING"}, sort_keys=True),
            ),
        ).fetchone()[0]
        pending = conn.execute(
            """SELECT count(*) FROM procurement_recommendations r
                WHERE r.run_id=%s AND NOT EXISTS (
                    SELECT 1 FROM review_decisions d WHERE d.recommendation_id=r.recommendation_id
                )""",
            (row[0],),
        ).fetchone()[0]
        blockers = conn.execute(
            """SELECT count(*) FROM exceptions
                WHERE run_id=%s AND status='OPEN' AND severity IN ('HIGH','CRITICAL')""",
            (row[0],),
        ).fetchone()[0]
        if int(pending) == 0 and int(blockers) == 0:
            conn.execute("UPDATE runs SET workflow_stage='REVIEWED' WHERE run_id=%s", (row[0],))
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
        "idempotent_replay": False,
    }


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
            }
            for row in rows
        ],
    }

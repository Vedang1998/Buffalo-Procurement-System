"""Build immutable, vendor-separated internal Procurement DRAFTs only."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from .readiness import po_readiness
from .recommendations import monday_run_inputs_match


DRAFT_BUILD_LOCK = 5_920_230_701
DRAFT_GATES = frozenset(
    {"INVENTORY_HISTORY", "MAPPING_INTEGRITY", "OPEN_PO_RECONCILIATION", "PRICE_COVERAGE", "VENDOR_RULES"}
)
MONEY = Decimal("0.01")
SAFETY_LABEL = "TEST DATA — NOT FOR ORDERING"


class DraftPoError(ValueError):
    pass


def _draft_rows(conn: Any, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.recommendation_id,r.variant_id,r.vendor_id,r.offer_id,r.frozen_supplier_sku,
                  d.decision_id,d.approved_cases,d.approved_loose_units,d.approved_units,
                  d.approved_unit_cost,d.approved_line_total,r.input_fingerprint,
                  r.frozen_minimum_type,r.frozen_minimum_value,r.frozen_below_minimum_fee,
                  r.frozen_loose_unit_fee,d.action,d.evidence_json
             FROM procurement_recommendations r
             JOIN review_decisions d ON d.recommendation_id=r.recommendation_id
            WHERE r.run_id=%s AND d.action IN ('ACCEPT','EDIT_QUANTITY') AND d.approved_units>0
            ORDER BY r.vendor_id,r.variant_id,r.recommendation_id""",
        (run_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        review_evidence = (row[17] or {}).get("review", {})
        try:
            merchandise_total = Decimal(
                str(review_evidence["approved_merchandise_total"])
            )
            loose_order_fee = Decimal(
                str(review_evidence["approved_loose_order_fee"])
            )
            case_price = (
                Decimal(str(review_evidence["approved_case_price"]))
                if review_evidence.get("approved_case_price") is not None
                else None
            )
        except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
            raise DraftPoError(
                "reviewed line is missing frozen merchandise or fee evidence"
            ) from exc
        if (
            not merchandise_total.is_finite()
            or merchandise_total < 0
            or not loose_order_fee.is_finite()
            or loose_order_fee < 0
            or Decimal(row[10]) != merchandise_total + loose_order_fee
            or (
                case_price is not None
                and (not case_price.is_finite() or case_price <= 0)
            )
        ):
            raise DraftPoError("reviewed line has invalid frozen economics")
        result.append({
            "recommendation_id": int(row[0]),"variant_id": row[1],"vendor_id": str(row[2]),
            "offer_id": int(row[3]),"supplier_sku": row[4],"decision_id": int(row[5]),
            "cases": int(row[6]),"loose_units": int(row[7]),"ordered_units": int(row[8]),
            "unit_cost": Decimal(row[9]),"line_total": Decimal(row[10]),
            "case_price": case_price,"merchandise_total": merchandise_total,
            "loose_order_fee": loose_order_fee,
            "input_fingerprint": row[11],"minimum_type": row[12],
            "minimum_value": Decimal(row[13]) if row[13] is not None else None,
            "below_minimum_fee": Decimal(row[14]),"loose_unit_fee": Decimal(row[15] or 0),
            "action": row[16],
        })
    return result


def _reviewed_lines_by_vendor(
    conn: Any, run_id: str
) -> dict[str, list[dict[str, Any]]]:
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for line in _draft_rows(conn, run_id):
        readiness = po_readiness(
            conn,
            vendor_id=line["vendor_id"],
            variant_id=line["variant_id"],
            run_id=run_id,
            applicable_gate_names=DRAFT_GATES,
        )
        if not readiness["po_generation_enabled"]:
            raise DraftPoError(
                f"readiness blocks DRAFT for Variant {line['variant_id']}"
            )
        line["readiness_evidence"] = {
            "variant_id": line["variant_id"],
            "vendor_id": line["vendor_id"],
            "run_id": str(run_id),
            "applicable_gate_names": readiness["applicable_gate_names"],
            "blockers": readiness["blockers"],
            "gates": readiness["gates"],
        }
        by_vendor.setdefault(line["vendor_id"], []).append(line)
    return by_vendor


def _vendor_economics(
    vendor_id: str, vendor_lines: list[dict[str, Any]]
) -> dict[str, Any]:
    if not vendor_lines:
        raise DraftPoError("vendor DRAFT economics require at least one reviewed line")
    rule = vendor_lines[0]
    rule_key = (
        rule["minimum_type"],
        rule["minimum_value"],
        rule["below_minimum_fee"],
    )
    if any(
        (
            line["minimum_type"],
            line["minimum_value"],
            line["below_minimum_fee"],
        )
        != rule_key
        for line in vendor_lines
    ):
        raise DraftPoError("frozen vendor minimum terms disagree across reviewed lines")
    merchandise = sum(
        (line["merchandise_total"] for line in vendor_lines), Decimal("0")
    )
    loose_order_fee_total = sum(
        (line["loose_order_fee"] for line in vendor_lines), Decimal("0")
    )
    case_count = sum(line["cases"] for line in vendor_lines)
    if rule["minimum_type"] == "DOLLAR":
        minimum_value = rule["minimum_value"] or Decimal("0")
        shortfall = max(minimum_value - merchandise, Decimal("0"))
    elif rule["minimum_type"] == "CASE":
        minimum_value = rule["minimum_value"] or Decimal("0")
        shortfall = max(minimum_value - Decimal(case_count), Decimal("0"))
    elif rule["minimum_type"] == "NONE":
        minimum_value = None
        shortfall = Decimal("0")
    else:
        raise DraftPoError("unsupported frozen vendor minimum type")
    below = shortfall > 0
    below_minimum_fee = rule["below_minimum_fee"] if below else Decimal("0")
    delivery_fee = loose_order_fee_total + below_minimum_fee
    return {
        "vendor_id": str(vendor_id),
        "line_count": len(vendor_lines),
        "case_count": case_count,
        "merchandise_total": merchandise,
        "minimum_type": rule["minimum_type"],
        "minimum_value": minimum_value,
        "minimum_shortfall": shortfall,
        "below_vendor_minimum": below,
        "minimum_disposition": "PAY_FEE" if below else "NOT_APPLICABLE",
        "loose_order_fee_total": loose_order_fee_total,
        "below_minimum_fee": below_minimum_fee,
        "delivery_fee": delivery_fee,
        "po_total": (merchandise + delivery_fee).quantize(MONEY),
        "lines": [
            {
                "recommendation_id": line["recommendation_id"],
                "decision_id": line["decision_id"],
                "variant_id": line["variant_id"],
                "offer_id": line["offer_id"],
                "cases": line["cases"],
                "loose_units": line["loose_units"],
                "ordered_units": line["ordered_units"],
                "unit_cost": line["unit_cost"],
                "case_price": line["case_price"],
                "merchandise_total": line["merchandise_total"],
                "loose_order_fee": line["loose_order_fee"],
                "line_total": line["line_total"],
            }
            for line in vendor_lines
        ],
    }


def _draft_preview_payload(
    *,
    run_id: str,
    input_fingerprint: str,
    actor: str,
    by_vendor: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    vendors = [
        _vendor_economics(vendor_id, vendor_lines)
        for vendor_id, vendor_lines in sorted(by_vendor.items())
    ]
    minimum_disposition = (
        "PAY_FEE"
        if any(item["below_vendor_minimum"] for item in vendors)
        else "NOT_APPLICABLE"
    )
    return {
        "run_id": str(run_id),
        "input_fingerprint": input_fingerprint,
        "actor": actor,
        "minimum_disposition": minimum_disposition,
        "vendors": vendors,
        "safety_label": SAFETY_LABEL,
    }


def _preview_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def _require_reviewed_run(conn: Any, run_id: str, *, for_update: bool) -> Any:
    suffix = " FOR UPDATE" if for_update else ""
    run = conn.execute(
        """SELECT workflow_stage,input_fingerprint,status,procurement_output_mode FROM runs
            WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'""" + suffix,
        (run_id,),
    ).fetchone()
    if run is None or run[3] != "INTERNAL_DRAFT_ONLY":
        raise DraftPoError("unknown Monday run")
    return run


def _require_complete_reviews(conn: Any, run_id: str) -> None:
    pending = conn.execute(
        """SELECT count(*) FROM procurement_recommendations r
            WHERE r.run_id=%s AND NOT EXISTS (
                SELECT 1 FROM review_decisions d WHERE d.recommendation_id=r.recommendation_id
            )""",
        (run_id,),
    ).fetchone()[0]
    blockers = conn.execute(
        """SELECT count(*) FROM exceptions
            WHERE run_id=%s AND status='OPEN' AND severity IN ('HIGH','CRITICAL')""",
        (run_id,),
    ).fetchone()[0]
    if pending or blockers:
        raise DraftPoError(
            "all recommendations must be reviewed and all material blockers resolved"
        )


def preview_vendor_drafts(
    conn: Any, *, run_id: str, actor: str
) -> dict[str, Any]:
    """Read-only exact vendor economics preview required before first DRAFT build."""

    operator = str(actor).strip()
    if not operator:
        raise DraftPoError("DRAFT builder actor is required")
    with conn.transaction():
        run = _require_reviewed_run(conn, run_id, for_update=False)
        if run[0] in {"DRAFTS_BUILT", "PACKET_BUILT"}:
            return {
                "run_id": str(run_id),
                "input_fingerprint": run[1],
                "actor": operator,
                "already_built": True,
                "release_performed": False,
            }
        if run[0] != "REVIEWED" or run[2] != "RUNNING":
            raise DraftPoError("DRAFT preview requires one clean fully reviewed run")
        if not monday_run_inputs_match(conn, str(run_id), run[1]):
            raise DraftPoError(
                "material recommendation inputs changed; prepare and review a new run"
            )
        _require_complete_reviews(conn, run_id)
        payload = _draft_preview_payload(
            run_id=run_id,
            input_fingerprint=run[1],
            actor=operator,
            by_vendor=_reviewed_lines_by_vendor(conn, run_id),
        )
    return {
        **payload,
        "preview_fingerprint": _preview_fingerprint(payload),
        "release_performed": False,
    }


def build_vendor_drafts(
    conn: Any,
    *,
    run_id: str,
    actor: str,
    expected_preview_fingerprint: str | None = None,
    minimum_disposition: str | None = None,
) -> dict[str, Any]:
    """Create one DRAFT per vendor; no FINAL/release/import action exists here."""

    operator = str(actor).strip()
    if not operator:
        raise DraftPoError("DRAFT builder actor is required")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (DRAFT_BUILD_LOCK,)).fetchone()[0]:
            raise DraftPoError("DRAFT build lock is unavailable")
        run = _require_reviewed_run(conn, run_id, for_update=True)
        existing = conn.execute(
            "SELECT count(*) FROM purchase_orders WHERE run_id=%s AND po_status='DRAFT'",
            (run_id,),
        ).fetchone()[0]
        if run[0] in {"DRAFTS_BUILT", "PACKET_BUILT"}:
            result = get_vendor_drafts(conn, run_id)
            result["idempotent_replay"] = True
            return result
        if run[0] != "REVIEWED" or run[2] != "RUNNING" or existing:
            raise DraftPoError("DRAFT build requires one clean fully reviewed run")
        if not monday_run_inputs_match(conn, str(run_id), run[1]):
            raise DraftPoError("material recommendation inputs changed; prepare and review a new run")
        _require_complete_reviews(conn, run_id)
        by_vendor = _reviewed_lines_by_vendor(conn, run_id)
        preview_payload = _draft_preview_payload(
            run_id=run_id,
            input_fingerprint=run[1],
            actor=operator,
            by_vendor=by_vendor,
        )
        preview_fingerprint = _preview_fingerprint(preview_payload)
        if (
            expected_preview_fingerprint != preview_fingerprint
            or minimum_disposition != preview_payload["minimum_disposition"]
        ):
            raise DraftPoError(
                "vendor DRAFT economics must be previewed and explicitly confirmed"
            )
        economics_by_vendor = {
            item["vendor_id"]: item for item in preview_payload["vendors"]
        }
        for vendor_id, vendor_lines in sorted(by_vendor.items()):
            po_id = conn.execute(
                """INSERT INTO purchase_orders(
                           run_id,vendor_id,po_status,input_fingerprint,receipt_status,
                           shopify_import_status,notes)
                    VALUES (%s,%s,'DRAFT',%s,'UNKNOWN','NOT_IMPORTED',%s)
                    RETURNING po_id""",
                (run_id,vendor_id,run[1],f"{SAFETY_LABEL}; built by {operator}"),
            ).fetchone()[0]
            has_loose = False
            for line in vendor_lines:
                line_total = line["line_total"]
                if line["loose_units"]:
                    has_loose = True
                conn.execute(
                    """INSERT INTO purchase_order_lines(
                               po_id,variant_id,offer_id,recommendation_id,review_decision_id,
                               supplier_sku,cases,loose_units,ordered_units,unit_cost,line_total,
                               reason_code,comment,input_fingerprint,line_status,reconciliation_status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'HUMAN_REVIEWED_BASELINE',
                                %s,%s,'DRAFT','UNKNOWN')""",
                    (
                        po_id,line["variant_id"],line["offer_id"],line["recommendation_id"],
                        line["decision_id"],line["supplier_sku"],line["cases"],line["loose_units"],
                        line["ordered_units"],line["unit_cost"],line_total,
                        f"{SAFETY_LABEL}; {line['action']}",line["input_fingerprint"],
                    ),
                )
            economics = economics_by_vendor[vendor_id]
            conn.execute(
                """UPDATE purchase_orders SET merchandise_total=%s,delivery_fee=%s,po_total=%s,
                          below_vendor_minimum=%s,reconciliation_evidence=%s::jsonb
                    WHERE po_id=%s""",
                (
                    economics["merchandise_total"],economics["delivery_fee"],
                    economics["po_total"],economics["below_vendor_minimum"],
                    json.dumps(
                        {
                            "safety_label": SAFETY_LABEL,
                            "line_count": len(vendor_lines),
                            "has_loose": has_loose,
                            "minimum_disposition": economics["minimum_disposition"],
                            "minimum_shortfall": str(economics["minimum_shortfall"]),
                            "loose_order_fee_total": str(
                                economics["loose_order_fee_total"]
                            ),
                            "below_minimum_fee": str(
                                economics["below_minimum_fee"]
                            ),
                            "draft_preview_fingerprint": preview_fingerprint,
                            "economics_confirmed_by": operator,
                            "readiness_by_variant": [
                                line["readiness_evidence"] for line in vendor_lines
                            ],
                        },
                        default=str,
                        sort_keys=True,
                    ),
                    po_id,
                ),
            )
        conn.execute("UPDATE runs SET workflow_stage='DRAFTS_BUILT' WHERE run_id=%s", (run_id,))
    result = get_vendor_drafts(conn, run_id)
    result["idempotent_replay"] = False
    return result


def get_vendor_drafts(conn: Any, run_id: str) -> dict[str, Any]:
    with conn.transaction():
        run = conn.execute(
            """SELECT workflow_stage,input_fingerprint FROM runs
                WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'
                  AND procurement_output_mode='INTERNAL_DRAFT_ONLY'""",
            (run_id,),
        ).fetchone()
        if run is None:
            raise DraftPoError("unknown Monday run")
        po_rows = conn.execute(
            """SELECT p.po_id,p.vendor_id,
                      COALESCE((SELECT r.metrics->>'frozen_vendor_name'
                                  FROM procurement_recommendations r
                                 WHERE r.run_id=p.run_id AND r.vendor_id=p.vendor_id
                                 ORDER BY r.recommendation_id LIMIT 1),v.vendor_name),
                      p.po_status,p.merchandise_total,
                      p.delivery_fee,p.po_total,p.below_vendor_minimum
                      ,p.reconciliation_evidence
                 FROM purchase_orders p JOIN vendors v ON v.vendor_id=p.vendor_id
                WHERE p.run_id=%s ORDER BY p.vendor_id,p.po_id""",
            (run_id,),
        ).fetchall()
        drafts = []
        for row in po_rows:
            build_evidence = row[8] or {}
            line_rows = conn.execute(
                """SELECT l.po_line_id,l.variant_id,l.supplier_sku,l.cases,l.loose_units,
                          l.ordered_units,l.unit_cost,l.line_total,l.comment,
                          d.evidence_json #>> '{review,approved_case_price}',
                          d.evidence_json #>> '{review,approved_merchandise_total}',
                          d.evidence_json #>> '{review,approved_loose_order_fee}'
                     FROM purchase_order_lines l
                     JOIN review_decisions d ON d.decision_id=l.review_decision_id
                    WHERE l.po_id=%s ORDER BY l.variant_id,l.po_line_id""",
                (row[0],),
            ).fetchall()
            drafts.append(
                {
                    "po_id": str(row[0]),"vendor_id": str(row[1]),"vendor_name": row[2],"po_status": row[3],
                    "merchandise_total": Decimal(row[4] or 0),"delivery_fee": Decimal(row[5] or 0),
                    "po_total": Decimal(row[6] or 0),"below_vendor_minimum": bool(row[7]),
                    "minimum_disposition": build_evidence.get("minimum_disposition"),
                    "minimum_shortfall": Decimal(
                        build_evidence.get("minimum_shortfall") or 0
                    ),
                    "loose_order_fee_total": Decimal(
                        build_evidence.get("loose_order_fee_total") or 0
                    ),
                    "below_minimum_fee": Decimal(
                        build_evidence.get("below_minimum_fee") or 0
                    ),
                    "draft_preview_fingerprint": build_evidence.get(
                        "draft_preview_fingerprint"
                    ),
                    "economics_confirmed_by": build_evidence.get(
                        "economics_confirmed_by"
                    ),
                    "readiness_evidence": build_evidence.get(
                        "readiness_by_variant", []
                    ),
                    "lines": [
                        {"po_line_id": int(line[0]),"variant_id": line[1],"supplier_sku": line[2],
                         "cases": int(line[3]),"loose_units": int(line[4]),"ordered_units": int(line[5]),
                         "unit_cost": Decimal(line[6]),"line_total": Decimal(line[7]),"comment": line[8],
                         "case_price": Decimal(line[9]) if line[9] is not None else None,
                         "merchandise_total": Decimal(line[10]),
                         "loose_order_fee": Decimal(line[11])}
                        for line in line_rows
                    ],
                }
            )
    return {
        "run_id": str(run_id),"workflow_stage": run[0],"input_fingerprint": run[1],
        "drafts": drafts,"safety_label": SAFETY_LABEL,"release_performed": False,
    }

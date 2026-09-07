"""Deterministic internal emergency review packet assembly."""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
import hashlib
import io
import json
from typing import Any
import zipfile

from .draft_po import SAFETY_LABEL, get_vendor_drafts
from .po_csv import FORMAT_WARNING, write_vendor_draft_csvs
from .storage import StorageAdapter


PACKET_BUILD_LOCK = 5_920_230_801


class EmergencyPacketError(ValueError):
    pass


def read_monday_artifact(
    conn: Any, *, storage: StorageAdapter, run_id: str, artifact_id: int
) -> dict[str, Any]:
    """Read and hash-check one registered internal run artifact."""

    with conn.transaction():
        row = conn.execute(
            """SELECT a.storage_key,a.sha256,a.size_bytes,a.payload,a.content_type,
                      a.artifact_type,a.vendor_id,r.procurement_output_mode,r.workflow_stage
                 FROM monday_run_artifacts a JOIN runs r ON r.run_id=a.run_id
                 JOIN monday_packet_build_events e
                   ON e.run_id=a.run_id AND e.transaction_id=a.packet_build_transaction_id
                WHERE a.run_id=%s AND a.monday_run_artifact_id=%s""",
            (run_id, artifact_id),
        ).fetchone()
    if row is None or row[7] != "INTERNAL_DRAFT_ONLY" or row[8] != "PACKET_BUILT":
        raise EmergencyPacketError("unknown Monday run artifact")
    evidence_payload = bytes(row[3])
    payload = storage.get_bytes(row[0]) if storage.exists(row[0]) else evidence_payload
    if (
        payload != evidence_payload
        or hashlib.sha256(payload).hexdigest() != row[1]
        or len(payload) != row[2]
    ):
        raise EmergencyPacketError("frozen run artifact hash mismatch")
    suffix = "review.zip" if row[5] == "EMERGENCY_REVIEW_PACKET" else "internal.csv"
    return {
        "data": payload, "content_type": row[4], "filename": f"monday-{run_id}-{artifact_id}.{suffix}",
        "sha256": row[1], "safety_label": SAFETY_LABEL,
    }


def list_monday_artifacts(conn: Any, run_id: str) -> list[dict[str, Any]]:
    with conn.transaction():
        rows = conn.execute(
            """SELECT a.monday_run_artifact_id,a.artifact_type,a.vendor_id,a.sha256,
                      a.size_bytes,a.content_type
                 FROM monday_run_artifacts a JOIN runs r ON r.run_id=a.run_id
                 JOIN monday_packet_build_events e
                   ON e.run_id=a.run_id AND e.transaction_id=a.packet_build_transaction_id
                WHERE a.run_id=%s AND r.procurement_output_mode='INTERNAL_DRAFT_ONLY'
                  AND r.workflow_stage='PACKET_BUILT'
                ORDER BY a.artifact_type,a.vendor_id""",
            (run_id,),
        ).fetchall()
    return [
        {
            "artifact_id": int(row[0]), "artifact_type": row[1],
            "vendor_id": str(row[2]) if row[2] else None, "sha256": row[3],
            "size_bytes": int(row[4]), "content_type": row[5],
        }
        for row in rows
    ]


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(type(value).__name__)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name])
    return output.getvalue()


def _review_csv(conn: Any, run_id: str) -> bytes:
    rows = conn.execute(
        """SELECT r.variant_id,COALESCE(r.metrics->>'frozen_vendor_name',v.vendor_name),d.action,d.approved_cases,
                  d.approved_loose_units,d.approved_units,d.approved_unit_cost,
                  d.approved_line_total,d.comment,d.decided_by,d.evidence_json
             FROM review_decisions d JOIN procurement_recommendations r ON r.recommendation_id=d.recommendation_id
             JOIN vendors v ON v.vendor_id=r.vendor_id WHERE d.run_id=%s
             ORDER BY r.vendor_id,r.variant_id""",
        (run_id,),
    ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow((
        "safety_label","variant_id","vendor_name","action","approved_cases",
        "approved_loose_units","approved_units","approved_unit_cost",
        "approved_case_price","approved_merchandise_total",
        "approved_loose_order_fee","approved_line_total","comment","decided_by",
        "resulting_inventory_units","resulting_days_supply","days_supply_status",
    ))
    for row in rows:
        reviewed = (row[10] or {}).get("review", {})
        writer.writerow(
            (
                SAFETY_LABEL,
                *(_spreadsheet_cell(value) for value in row[:7]),
                reviewed.get("approved_case_price"),
                reviewed.get("approved_merchandise_total"),
                reviewed.get("approved_loose_order_fee"),
                *(_spreadsheet_cell(value) for value in row[7:10]),
                reviewed.get("resulting_inventory_units"),
                reviewed.get("resulting_days_supply"),
                reviewed.get("days_supply_status"),
            )
        )
    return output.getvalue().encode()


def _spreadsheet_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _json_entry(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, default=_json_default, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def build_emergency_review_packet(
    conn: Any, *, storage: StorageAdapter, run_id: str, actor: str,
    _inject_failure_before_stage: bool = False,
) -> dict[str, Any]:
    operator = str(actor).strip()
    if not operator:
        raise EmergencyPacketError("packet actor is required")
    with conn.transaction():
        snapshot = get_vendor_drafts(conn, run_id)
        artifact_rows = conn.execute(
            """SELECT storage_key,sha256,size_bytes,payload,artifact_type,vendor_id,content_type,
                      packet_build_transaction_id
                 FROM monday_run_artifacts
                WHERE run_id=%s ORDER BY artifact_type,vendor_id""",
            (run_id,),
        ).fetchall()
        existing = conn.execute(
            """SELECT storage_key,sha256,size_bytes,payload FROM monday_run_artifacts
                WHERE run_id=%s AND artifact_type='EMERGENCY_REVIEW_PACKET' AND vendor_id IS NULL""",
            (run_id,),
        ).fetchone()
        build_event = conn.execute(
            """SELECT artifact_set_sha256,packet_sha256,csv_count,artifact_count,
                      verification_method,transaction_id
                 FROM monday_packet_build_events WHERE run_id=%s""",
            (run_id,),
        ).fetchone()
        db_artifact_sha = (
            conn.execute("SELECT monday_artifact_set_sha256(%s)", (run_id,)).fetchone()[0]
            if build_event is not None
            else None
        )
    if snapshot["workflow_stage"] == "PACKET_BUILT":
        if existing is None or build_event is None:
            raise EmergencyPacketError("frozen packet build authority is missing")
        if (
            build_event[1] != existing[1]
            or int(build_event[2]) != sum(row[4] == "VENDOR_INTERNAL_CSV" for row in artifact_rows)
            or int(build_event[3]) != len(artifact_rows)
            or build_event[4] != "DB_PAYLOAD_AND_STORAGE_READBACK_SHA256_V1"
            or any(row[7] != build_event[5] for row in artifact_rows)
        ):
            raise EmergencyPacketError("frozen packet build authority does not match its artifacts")
        if db_artifact_sha != build_event[0]:
            raise EmergencyPacketError("frozen packet artifact-set fingerprint mismatch")
        for artifact in artifact_rows:
            evidence_payload = bytes(artifact[3])
            payload = (
                storage.get_bytes(artifact[0])
                if storage.exists(artifact[0])
                else evidence_payload
            )
            if (
                payload != evidence_payload
                or hashlib.sha256(payload).hexdigest() != artifact[1]
                or len(payload) != artifact[2]
            ):
                raise EmergencyPacketError("frozen run artifact hash mismatch")
        return {"run_id": run_id,"storage_key": existing[0],"sha256": existing[1],"size_bytes": existing[2],"idempotent_replay": True}
    if snapshot["workflow_stage"] != "DRAFTS_BUILT":
        raise EmergencyPacketError("packet requires a completed DRAFT review stage")
    csv_artifacts = write_vendor_draft_csvs(conn, storage=storage, run_id=run_id, actor=operator)
    entries: dict[str, bytes] = {}
    for artifact in csv_artifacts:
        payload = storage.get_bytes(artifact["storage_key"])
        if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
            raise EmergencyPacketError("vendor CSV changed before packet assembly")
        entries[f"vendor-{artifact['vendor_id']}.internal.csv"] = payload
    with conn.transaction():
        input_manifest_text = conn.execute(
            """SELECT procurement_input_manifest FROM runs
                WHERE run_id=%s AND procurement_output_mode='INTERNAL_DRAFT_ONLY'""",
            (run_id,),
        ).fetchone()
        if input_manifest_text is None or not input_manifest_text[0]:
            raise EmergencyPacketError("frozen input manifest is missing")
        entries["human-review-decisions.csv"] = _review_csv(conn, run_id)
        exceptions = [
            {"exception_id": int(row[0]),"type": row[1],"severity": row[2],"variant_id": row[3],"message": row[4],"status": row[5]}
            for row in conn.execute(
                "SELECT exception_id,exception_type,severity,variant_id,message,status FROM exceptions WHERE run_id=%s ORDER BY exception_id",
                (run_id,),
            ).fetchall()
        ]
        recommendations = [
            {
                "recommendation_id": int(row[0]), "variant_id": row[1],
                "vendor_id": str(row[2]), "offer_id": int(row[3]),
                "recommended_cases": row[4], "recommended_loose_units": row[5],
                "recommended_units": row[6], "unit_cost": row[7],
                "reason_code": row[8], "metrics": row[9],
            }
            for row in conn.execute(
                """SELECT recommendation_id,variant_id,vendor_id,offer_id,recommended_cases,
                          recommended_loose_units,recommended_units,recommended_unit_cost,
                          reason_code,metrics
                     FROM procurement_recommendations WHERE run_id=%s
                    ORDER BY vendor_id,variant_id,recommendation_id""",
                (run_id,),
            ).fetchall()
        ]
        prices = [
            {
                "offer_id": int(row[0]), "price_state": row[1], "effective_month": row[2],
                "level_type": row[3], "break_qty": row[4], "break_unit": row[5],
                "case_price": row[6], "unit_price": row[7], "source_file": row[8],
                "source_page": row[9],
            }
            for row in conn.execute(
                """SELECT offer_id,price_state,effective_month,level_type,break_qty,break_unit,
                          case_price,unit_price,source_file,source_page
                     FROM run_price_snapshots WHERE run_id=%s
                    ORDER BY offer_id,level_type,break_unit,break_qty,run_price_snapshot_id""",
                (run_id,),
            ).fetchall()
        ]
        mappings = [
            {
                "variant_id": row[0], "vendor_id": str(row[1]), "offer_id": int(row[2]),
                "supplier_sku": row[3], "units_per_case": int(row[4]),
                "qualifying_units_per_case": int(row[5]),
                "mapping_evidence": row[6].get("frozen_offer_evidence", {}),
            }
            for row in conn.execute(
                """SELECT variant_id,vendor_id,offer_id,frozen_supplier_sku,units_per_case,
                          frozen_qualifying_units_per_case,metrics
                     FROM procurement_recommendations WHERE run_id=%s
                    ORDER BY vendor_id,variant_id""",
                (run_id,),
            ).fetchall()
        ]
        open_ledger = [
            {"variant_id": item["variant_id"], "position": item["metrics"]["frozen_open_po_position"]}
            for item in recommendations
        ]
    evidence_label = {"safety_label": SAFETY_LABEL}
    entries["frozen-input-manifest.json"] = _json_entry(
        {**evidence_label, "input_manifest": json.loads(input_manifest_text[0])}
    )
    entries["recommendations-and-reasons.json"] = _json_entry({**evidence_label, "items": recommendations})
    entries["frozen-price-economics.json"] = _json_entry({**evidence_label, "items": prices})
    entries["supplier-mapping-evidence.json"] = _json_entry({**evidence_label, "items": mappings})
    entries["open-po-ledger-evidence.json"] = _json_entry({**evidence_label, "items": open_ledger})
    entries["draft-readiness-evidence.json"] = _json_entry(
        {
            **evidence_label,
            "items": [
                {
                    "vendor_id": draft["vendor_id"],
                    "draft_po_id": draft["po_id"],
                    "readiness_by_variant": draft["readiness_evidence"],
                }
                for draft in snapshot["drafts"]
            ],
        }
    )
    summary = {
        "safety_label": SAFETY_LABEL,
        "format_status": FORMAT_WARNING,
        "run_id": run_id,
        "input_fingerprint": snapshot["input_fingerprint"],
        "vendor_draft_count": len(snapshot["drafts"]),
        "draft_po_ids": [draft["po_id"] for draft in snapshot["drafts"]],
        "merchandise_total": sum((draft["merchandise_total"] for draft in snapshot["drafts"]), Decimal("0")),
        "po_total": sum((draft["po_total"] for draft in snapshot["drafts"]), Decimal("0")),
        "vendor_draft_economics": [
            {
                "vendor_id": draft["vendor_id"], "vendor_name": draft["vendor_name"],
                "draft_po_id": draft["po_id"], "merchandise_total": draft["merchandise_total"],
                "delivery_fee": draft["delivery_fee"], "po_total": draft["po_total"],
                "below_vendor_minimum": draft["below_vendor_minimum"],
                "minimum_shortfall": draft["minimum_shortfall"],
                "minimum_disposition": draft["minimum_disposition"],
                "loose_order_fee_total": draft["loose_order_fee_total"],
                "below_minimum_fee": draft["below_minimum_fee"],
                "economics_confirmed_by": draft["economics_confirmed_by"],
                "draft_preview_fingerprint": draft["draft_preview_fingerprint"],
            }
            for draft in snapshot["drafts"]
        ],
        "exceptions": exceptions,
        "release_performed": False,
        "shopify_calls": 0,
    }
    entries["packet-summary.json"] = _json_entry(summary)
    manifest = {
        "safety_label": SAFETY_LABEL,
        "entries": {name: hashlib.sha256(payload).hexdigest() for name,payload in sorted(entries.items())},
    }
    entries["manifest.json"] = _json_entry(manifest)
    packet = _zip_bytes(entries)
    digest = hashlib.sha256(packet).hexdigest()
    key = f"monday-runs/{run_id}/packet/{digest}.review.zip"
    if storage.exists(key):
        if storage.get_bytes(key) != packet:
            raise EmergencyPacketError("packet content-address collision")
    else:
        storage.put_bytes(key,packet)
        if storage.get_bytes(key) != packet:
            raise EmergencyPacketError("packet read-back mismatch")
    artifacts = [
        *csv_artifacts,
        {
            "artifact_type": "EMERGENCY_REVIEW_PACKET", "vendor_id": None,
            "storage_key": key, "sha256": digest, "size_bytes": len(packet),
            "content_type": "application/zip", "data": packet,
        },
    ]
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if not conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (PACKET_BUILD_LOCK,)
        ).fetchone()[0]:
            raise EmergencyPacketError("packet build lock is unavailable")
        run = conn.execute(
            "SELECT workflow_stage,input_fingerprint FROM runs WHERE run_id=%s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if run is None or run[0] != "DRAFTS_BUILT" or run[1] != snapshot["input_fingerprint"]:
            raise EmergencyPacketError("run changed before packet commit")
        if conn.execute(
            """SELECT (SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s),
                      (SELECT count(*) FROM monday_packet_build_events WHERE run_id=%s)""",
            (run_id,run_id),
        ).fetchone() != (0,0):
            raise EmergencyPacketError("packet build evidence already exists for a nonterminal run")
        for artifact in artifacts:
            if (
                not storage.exists(artifact["storage_key"])
                or storage.get_bytes(artifact["storage_key"]) != artifact["data"]
            ):
                raise EmergencyPacketError("artifact changed before atomic packet commit")
            conn.execute(
                """INSERT INTO monday_run_artifacts(
                           run_id,artifact_type,vendor_id,storage_key,sha256,size_bytes,
                           payload,content_type,safety_label,input_fingerprint,created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,artifact["artifact_type"],artifact["vendor_id"],
                    artifact["storage_key"],artifact["sha256"],artifact["size_bytes"],
                    artifact["data"],artifact["content_type"],SAFETY_LABEL,run[1],operator,
                ),
            )
        artifact_set_sha = conn.execute(
            "SELECT monday_artifact_set_sha256(%s)", (run_id,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO monday_packet_build_events(
                       run_id,input_fingerprint,artifact_set_sha256,packet_sha256,
                       csv_count,artifact_count,verification_method,actor)
                VALUES (%s,%s,%s,%s,%s,%s,'DB_PAYLOAD_AND_STORAGE_READBACK_SHA256_V1',%s)""",
            (run_id,run[1],artifact_set_sha,digest,len(csv_artifacts),len(artifacts),operator),
        )
        if _inject_failure_before_stage:
            raise RuntimeError("synthetic packet transaction failure")
        conn.execute("UPDATE runs SET workflow_stage='PACKET_BUILT' WHERE run_id=%s", (run_id,))
    return {"run_id": run_id,"storage_key": key,"sha256": digest,"size_bytes": len(packet),"idempotent_replay": False}

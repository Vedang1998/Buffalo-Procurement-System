"""Dormant identity/economics reconciliation of captured synthetic artifacts.

Uses the actual browser observations and independent registry-bound bytes. This
does not authenticate the registry, re-run forecast/need models, certify readiness
or prove browser/restart/restore execution. Those remain separate required gates.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import re

from hosted_synthetic_artifacts import (
    CapturedArtifact, HostedArtifactError, SAFETY_LABEL, _UUID, _HASH,
    _require, bounded_json, packet_members,
)

ORIGIN = "https://staging.example.test"
FORMAT_WARNING = "SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED"
CSV_HEADERS = (
    "safety_label","format_status","run_id","draft_po_id","vendor_name","variant_id",
    "supplier_sku","cases","loose_units","ordered_units","unit_cost","case_price",
    "line_merchandise_total","line_loose_order_fee","line_total",
    "vendor_merchandise_total","vendor_loose_order_fee_total",
    "vendor_below_minimum_fee","vendor_delivery_fee","vendor_po_total",
    "below_vendor_minimum","minimum_shortfall","minimum_disposition",
    "economics_confirmed_by","draft_preview_fingerprint",
    "internal_output_contract","captured_available_quantity",
    "inventory_captured_at","source_inventory_snapshot_run_id",
    "inventory_location_scope_json",
)
_LINES = {
    "1001": ("SUP-001", "6", "36", "5.0000", "30.0000", "180.00"),
    "4001": ("WEST-MULTI-4001", "2", "8", "10.5000", "42.0000", "84.00"),
    "4002": ("WEST-BTL-4002", "1", "4", "4.5000", "18.0000", "18.00"),
}


def _money(value):
    _require(type(value) is str and re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,4})?", value))
    return Decimal(value)


def _captured_utc(value):
    _require(type(value) is str)
    parsed = datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed.astimezone(timezone.utc).isoformat()


def _equal(actual, expected):
    _require(type(actual) is dict and all(type(actual.get(k)) is type(v) and actual[k] == v for k, v in expected.items()))


def _by(items, key, expected):
    _require(type(items) is list and len(items) == len(expected))
    result = {}
    for item in items:
        _require(type(item) is dict and type(item.get(key)) is str and item[key] in expected and item[key] not in result)
        result[item[key]] = item
    _require(set(result) == set(expected))
    return result


def _rows(data):
    _require(type(data) is bytes and len(data) <= 1024 * 1024)
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True))
    _require(1 < len(parsed) <= 4 and tuple(parsed[0]) == CSV_HEADERS
             and all(len(row) == len(CSV_HEADERS) for row in parsed[1:]))
    return [dict(zip(CSV_HEADERS, row, strict=True)) for row in parsed[1:]]


def validate_packet_controls(preparation_json: bytes, review_json: bytes,
                             artifacts: tuple[CapturedArtifact, CapturedArtifact, CapturedArtifact],
                             *, commit: str, tree: str) -> dict:
    """Reconcile observed source/run/vendor/DRAFT/stock and canonical cash totals.

    preparation_json must retain the exact JSON.stringify(observation) bytes whose
    SHA256 the review component consumed. Expectations are never taken from packet
    identity fields themselves. Registry authenticity remains the outer caller's
    same-generation obligation; all output is non-authorizing.
    """
    try:
        return _validate(preparation_json, review_json, artifacts, commit=commit, tree=tree)
    except (ValueError, KeyError, TypeError, UnicodeError, csv.Error, AttributeError) as exc:
        if isinstance(exc, HostedArtifactError):
            raise
        raise HostedArtifactError("hosted_packet_controls_refused") from None


def _validate(preparation_json, review_json, artifacts, *, commit, tree):
    _require(type(commit) is str and re.fullmatch(r"[0-9a-f]{40}", commit)
             and type(tree) is str and re.fullmatch(r"[0-9a-f]{40}", tree))
    preparation, review = bounded_json(preparation_json), bounded_json(review_json)
    _equal(preparation, {"contract":"BUFFALO_STAGING_PREPARATION_STEP_OBSERVATION_V1",
                        "commit":commit, "tree":tree, "commercial_authority":False,
                        "execution_authority":False, "scope":"PREPARATION_DOM_COMPONENT_ONLY"})
    _equal(review, {"contract":"BUFFALO_STAGING_REVIEW_DRAFT_STEP_OBSERVATION_V1",
                   "commit":commit, "tree":tree, "commercial_authority":False, "execution_authority":False,
                   "scope":"REVIEW_DRAFT_DOM_COMPONENT_ONLY",
                   "preparation_proof_sha256":hashlib.sha256(preparation_json).hexdigest()})
    run = preparation["run_id"]
    _require(type(run) is str and _UUID.fullmatch(run) and review["run_id"] == run
             and type(preparation["input_fingerprint"]) is str and _HASH.fullmatch(preparation["input_fingerprint"])
             and type(preparation["principal_ref"]) is str and re.fullmatch(r"owner:[A-Za-z0-9:-]{1,100}", preparation["principal_ref"])
             and type(review["draft_preview_fingerprint"]) is str and _HASH.fullmatch(review["draft_preview_fingerprint"]))
    vendors = preparation["vendors"]
    _require(type(vendors) is dict and set(vendors) == {"southern", "western"}
             and all(type(v) is str and _UUID.fullmatch(v) for v in vendors.values())
             and vendors["southern"] != vendors["western"])
    drafts = _by(review["draft_bindings"], "vendor_id", vendors.values())
    draft_ids = [item["draft_po_id"] for item in drafts.values()]
    _require(len(set(draft_ids)) == 2 and all(type(v) is str and _UUID.fullmatch(v) for v in draft_ids)
             and type(review["draft_ids"]) is list and sorted(review["draft_ids"]) == sorted(draft_ids))
    _require(type(artifacts) is tuple and len(artifacts) == 3)
    for artifact in artifacts:
        _require(type(artifact) is CapturedArtifact); artifact.validate()
        _require(artifact.registry.run_id == run)
    _require(len({x.registry.artifact_id for x in artifacts}) == 3 and len({x.guid for x in artifacts}) == 3)
    packets = [x for x in artifacts if x.registry.kind == "EMERGENCY_REVIEW_PACKET"]
    csvs = [x for x in artifacts if x.registry.kind == "VENDOR_INTERNAL_CSV"]
    _require(len(packets) == 1 and len(csvs) == 2 and {x.registry.vendor_id for x in csvs} == set(vendors.values()))
    observed_artifacts = _by(review["artifacts"], "url", [ORIGIN+f"/monday-runs/{run}/artifacts/{x.registry.artifact_id}" for x in artifacts])
    for artifact in artifacts:
        observed = observed_artifacts[ORIGIN+f"/monday-runs/{run}/artifacts/{artifact.registry.artifact_id}"]
        _require(type(observed["cells"]) is list and len(observed["cells"]) == 4
                 and observed["cells"][:3] == [artifact.registry.kind, artifact.registry.vendor_id or "—", str(artifact.registry.size_bytes)])
    entries = dict(packet_members(packets[0], (vendors["southern"], vendors["western"])))
    documents = {name:bounded_json(data) for name, data in entries.items() if name.endswith(".json")}
    for document in documents.values():
        _equal(document, {"safety_label":SAFETY_LABEL})
    frozen = documents["frozen-input-manifest.json"]["input_manifest"]
    _require(type(frozen) is dict and hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(",", ":")).encode()).hexdigest() == preparation["input_fingerprint"])
    _equal(frozen, {"business_date":"2026-10-05", "method_version":"DEVELOPMENT_ROLLING_ORIGIN_V2",
                    "development_forecast_contract":"BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2"})
    variants = ["1001", "4001", "4002", "4003", "4004", "4005"]
    _require(frozen["variant_ids"] == variants)
    contexts = _by(frozen["contexts"], "variant_id", variants)
    summary = documents["packet-summary.json"]
    _equal(summary, {"run_id":run, "input_fingerprint":preparation["input_fingerprint"],
                     "vendor_draft_count":2, "release_performed":False, "shopify_calls":0,
                     "format_status":FORMAT_WARNING})
    _require(type(summary["draft_po_ids"]) is list and sorted(summary["draft_po_ids"]) == sorted(draft_ids)
             and _money(summary["merchandise_total"]) == 282 and _money(summary["po_total"]) == 289)
    economics = _by(summary["vendor_draft_economics"], "vendor_id", vendors.values())
    line_merchandise = Decimal(0); fee_total = Decimal(0); draft_total = Decimal(0); line_count = 0
    for artifact in csvs:
        vendor = artifact.registry.vendor_id; southern = vendor == vendors["southern"]
        _require(entries[f"vendor-{vendor}.internal.csv"] == artifact.data)
        vendor_name = "Synthetic Southern" if southern else "Synthetic Western Acceptance"
        expected_variants = ["1001"] if southern else ["4001", "4002"]
        rows = _by(_rows(artifact.data), "variant_id", expected_variants)
        merchandise, fee, total, shortfall = ("180.00", "0.00", "180.00", "0.00") if southern else ("102.00", "7.00", "109.00", "18.00")
        disposition = "NOT_APPLICABLE" if southern else "PAY_FEE"
        economic = economics[vendor]
        _equal(economic, {"vendor_id":vendor, "vendor_name":vendor_name, "draft_po_id":drafts[vendor]["draft_po_id"],
                          "below_vendor_minimum":not southern, "minimum_disposition":disposition,
                          "economics_confirmed_by":preparation["principal_ref"], "draft_preview_fingerprint":review["draft_preview_fingerprint"]})
        for key,value in {"merchandise_total":merchandise,"delivery_fee":fee,"po_total":total,"minimum_shortfall":shortfall,
                          "loose_order_fee_total":"0", "below_minimum_fee":fee}.items():
            _require(_money(economic[key]) == _money(value))
        fee_total += _money(economic["delivery_fee"]); draft_total += _money(economic["po_total"])
        for variant,row in rows.items():
            sku,cases,units,unit_price,case_price,line = _LINES[variant]
            _equal(row, {"safety_label":SAFETY_LABEL,"format_status":FORMAT_WARNING,"run_id":run,"draft_po_id":drafts[vendor]["draft_po_id"],
                         "vendor_name":vendor_name,"supplier_sku":sku,"cases":cases,"loose_units":"0","ordered_units":units,
                         "unit_cost":unit_price,"case_price":case_price,"line_merchandise_total":line,"line_loose_order_fee":"0.00","line_total":line,
                         "vendor_merchandise_total":merchandise,"vendor_loose_order_fee_total":"0.00","vendor_below_minimum_fee":fee,"vendor_delivery_fee":fee,
                         "vendor_po_total":total,"minimum_shortfall":shortfall,"below_vendor_minimum":str(not southern).upper(),"minimum_disposition":disposition,
                         "economics_confirmed_by":preparation["principal_ref"],"draft_preview_fingerprint":review["draft_preview_fingerprint"],
                         "internal_output_contract":"BUFFALO_INTERNAL_DRAFT_LINE_V2","captured_available_quantity":"0.0000","inventory_captured_at":"2026-10-05T12:00:00+00:00"})
            _require(_money(row["ordered_units"])*_money(row["unit_cost"]) == _money(row["line_merchandise_total"])
                     and _money(row["cases"])*_money(row["case_price"]) == _money(row["line_merchandise_total"]))
            context = contexts[variant]; capture = context["inventory_capture"]
            physical_pack, qualifying_pack = (6, 6) if southern else ((4, 12) if variant == "4001" else (4, 4))
            _equal(context, {"vendor_id":vendor,"supplier_sku":row["supplier_sku"],"units_per_case":physical_pack,"qualifying_units_per_case":qualifying_pack})
            _require(context["vendor_id"] == vendor and type(capture) is list and len(capture) >= 3
                     and capture[1] == "2026-10-05" and type(capture[0]) is str and _UUID.fullmatch(capture[0])
                     and row["source_inventory_snapshot_run_id"] == capture[0]
                     and _captured_utc(capture[2]) == row["inventory_captured_at"]
                     and _money(str(context["available_units"])) == 0 and _money(str(context["trusted_incoming_units"])) == 0)
            locations = context["inventory_rows"]
            _require(type(locations) is list and len(locations) == 1 and type(locations[0]) is list and len(locations[0]) >= 5)
            location = locations[0]
            _require(type(location[0]) is str and location[0] and _money(str(location[1])) == 0
                     and _money(str(location[2])) == 0 and location[3] == "VALID" and location[4] == capture[0])
            _require(bounded_json(row["inventory_location_scope_json"].encode()) == [{"location_gid":location[0],"available_quantity":str(Decimal(str(location[1]))),
                     "captured_incoming_quantity":str(Decimal(str(location[2]))),"validation_status":"VALID"}])
            line_merchandise += _money(row["line_merchandise_total"]); line_count += 1
    _require(line_merchandise == 282 and fee_total == 7 and draft_total == 289
             and line_merchandise + fee_total == draft_total and line_count == 3)
    return {"contract":"BUFFALO_HOSTED_PACKET_IDENTITY_ECONOMICS_V1", "commit":commit,"tree":tree,"run_id":run,
            "registered_artifacts":[{"artifact_id":x.registry.artifact_id,"sha256":x.registry.sha256,"size_bytes":x.registry.size_bytes} for x in artifacts],
            "observed_controls":{"drafts":len(drafts),"lines":line_count,"packet_members":len(entries),"merchandise_cents":int(line_merchandise*100),"fee_cents":int(fee_total*100),"total_cents":int(draft_total*100)},
            "scope":"PACKET_IDENTITY_ECONOMICS_COMPONENT_ONLY","forecast_need_readiness_validation":"REQUIRED_SEPARATE_GATE",
            "registry_authenticity":"CALLER_SAME_GENERATION_PREREQUISITE","restart_restore":"NOT_RUN","execution_authority":False}

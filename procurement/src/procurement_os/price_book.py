"""Strict normalized supplier price-book staging and promotion.

Staging is never operational pricing. Promotion revalidates the complete batch
inside one SERIALIZABLE transaction, and successful promotion removes the typed
staging rows so the database does not become a reusable price archive.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re
from typing import Any

from .config import load_rules
from .storage import StorageAdapter


class PriceBookError(ValueError):
    pass


MAX_PRICE_BOOK_BYTES = 5_000_000
PRICE_BOOK_HEADERS = (
    "batch_ref",
    "target_price_state",
    "effective_from",
    "effective_through",
    "vendor_name",
    "supplier_sku",
    "supplier_description",
    "canonical_variant_id",
    "package_type",
    "size_text",
    "raw_pack",
    "shopify_units_per_case",
    "qualifying_units_per_case",
    "assortment_scope",
    "assortment_group",
    "assortable",
    "assortment_evidence",
    "level_type",
    "break_quantity",
    "break_unit",
    "case_price",
    "unit_price",
    "source_file",
    "source_page",
    "source_evidence",
    "extraction_confidence",
    "review_note",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCK_ID = 71_140_004
_MAX_QUANTITY = Decimal("99999999.9999")  # NUMERIC(12,4)
_MAX_PRICE = Decimal("9999999999.9999")  # NUMERIC(14,4)
_MAX_POSTGRES_INTEGER = 2_147_483_647


def normalized_price_book_template() -> bytes:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerow(PRICE_BOOK_HEADERS)
    return output.getvalue().encode("utf-8")


def _required_text(raw: dict[str, str], field: str, errors: list[dict]) -> str | None:
    value = (raw.get(field) or "").strip()
    if not value:
        errors.append(_issue(f"MISSING_{field.upper()}", f"{field} is required"))
        return None
    return value


def _optional_text(raw: dict[str, str], field: str) -> str | None:
    value = (raw.get(field) or "").strip()
    return value or None


def _issue(code: str, message: str, *, severity: str = "ERROR") -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _positive_decimal(
    raw: dict[str, str],
    field: str,
    errors: list[dict],
    *,
    required: bool = True,
    maximum: Decimal = _MAX_QUANTITY,
) -> Decimal | None:
    text = (raw.get(field) or "").strip()
    if not text:
        if required:
            errors.append(_issue(f"MISSING_{field.upper()}", f"{field} is required"))
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        errors.append(_issue(f"INVALID_{field.upper()}", f"{field} must be numeric"))
        return None
    if not value.is_finite():
        errors.append(
            _issue(
                f"INVALID_{field.upper()}",
                f"{field} must be positive, finite, and have at most four decimals",
            )
        )
        return None
    scale = max(-value.as_tuple().exponent, 0)
    if value <= 0 or scale > 4 or value > maximum:
        errors.append(
            _issue(
                f"INVALID_{field.upper()}",
                f"{field} must fit its positive four-decimal database field",
            )
        )
        return None
    return value


def _source_page(raw: dict[str, str], errors: list[dict]) -> int | None:
    text = (raw.get("source_page") or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value <= 0 or value > _MAX_POSTGRES_INTEGER or str(value) != text:
        errors.append(_issue("INVALID_SOURCE_PAGE", "source_page must be a positive integer"))
        return None
    return value


def _boolean(raw: dict[str, str], field: str, errors: list[dict]) -> bool | None:
    text = (raw.get(field) or "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    errors.append(_issue(f"INVALID_{field.upper()}", f"{field} must be true or false"))
    return None


def _normalize_row(raw: dict[str, str], source_row_number: int) -> dict[str, Any]:
    errors: list[dict] = []
    warnings: list[dict] = []
    level_type = (_required_text(raw, "level_type", errors) or "").upper() or None
    if level_type not in {None, "BASE", "BREAK"}:
        errors.append(_issue("INVALID_LEVEL_TYPE", "level_type must be BASE or BREAK"))
        level_type = None
    break_unit = (_optional_text(raw, "break_unit") or "").upper() or None
    break_quantity = _positive_decimal(raw, "break_quantity", errors, required=False)
    if level_type == "BASE":
        if break_unit is not None or break_quantity is not None:
            errors.append(_issue("BASE_HAS_BREAK", "BASE rows cannot have break fields"))
    elif level_type == "BREAK":
        if break_unit not in {"BT", "CS"}:
            errors.append(_issue("BREAK_UNIT_REQUIRED", "BREAK rows require BT or CS"))
        if break_quantity is None:
            errors.append(_issue("BREAK_QUANTITY_REQUIRED", "BREAK rows require a quantity"))
        elif break_quantity != break_quantity.to_integral_value():
            errors.append(_issue("BREAK_QUANTITY_NOT_WHOLE", "break quantity must be whole"))
    elif break_unit is not None or break_quantity is not None:
        errors.append(_issue("BREAK_WITHOUT_LEVEL", "break fields require a BREAK row"))

    assortment_scope = (
        _required_text(raw, "assortment_scope", errors) or ""
    ).upper() or None
    if assortment_scope not in {None, "PRODUCT", "EXPLICIT_CROSS_PRODUCT", "NONE"}:
        errors.append(_issue("INVALID_ASSORTMENT_SCOPE", "invalid assortment_scope"))
        assortment_scope = None
    assortment_group = _optional_text(raw, "assortment_group")
    assortment_evidence = _optional_text(raw, "assortment_evidence")
    assortable = _boolean(raw, "assortable", errors)
    if assortment_scope == "EXPLICIT_CROSS_PRODUCT" and (
        not assortment_group or not assortment_evidence
    ):
        errors.append(
            _issue(
                "CROSS_PRODUCT_ASSORTMENT_EVIDENCE_REQUIRED",
                "cross-product assortment requires group and explicit evidence",
            )
        )

    shopify_units = _positive_decimal(raw, "shopify_units_per_case", errors)
    qualifying_units = _positive_decimal(raw, "qualifying_units_per_case", errors)
    for field, value in (
        ("shopify_units_per_case", shopify_units),
        ("qualifying_units_per_case", qualifying_units),
    ):
        if value is not None and value != value.to_integral_value():
            errors.append(_issue(f"{field.upper()}_NOT_WHOLE", f"{field} must be whole"))
    unit_price = _positive_decimal(
        raw, "unit_price", errors, maximum=_MAX_PRICE
    )
    case_price = _positive_decimal(
        raw, "case_price", errors, required=False, maximum=_MAX_PRICE
    )
    if level_type in {"BASE", "BREAK"} and (level_type == "BASE" or break_unit == "CS"):
        if case_price is None:
            errors.append(
                _issue("CASE_PRICE_REQUIRED", "BASE and CS rows require case_price")
            )
    if case_price is not None and shopify_units is not None and unit_price is not None:
        expected_unit = case_price / shopify_units
        delta = abs(expected_unit - unit_price)
        if delta > Decimal("0.01"):
            errors.append(
                _issue(
                    "CASE_UNIT_PRICE_MISMATCH",
                    "case_price / Shopify units per case differs from unit_price by more than one cent",
                )
            )
        elif delta > 0:
            warnings.append(
                _issue(
                    "CASE_UNIT_ROUNDING_DELTA",
                    "case/unit arithmetic differs only within the canonical one-cent tolerance",
                    severity="WARN",
                )
            )

    package_type = (_required_text(raw, "package_type", errors) or "").upper() or None
    extraction_confidence = (
        _required_text(raw, "extraction_confidence", errors) or ""
    ).upper() or None
    if extraction_confidence is not None and extraction_confidence != "VERIFIED":
        errors.append(
            _issue(
                "EXTRACTION_NOT_VERIFIED",
                "candidate price rows require human-verified source extraction",
            )
        )

    return {
        "source_row_number": source_row_number,
        "raw_payload": {name: raw.get(name, "") for name in PRICE_BOOK_HEADERS},
        "vendor_name": _required_text(raw, "vendor_name", errors),
        "supplier_sku": _required_text(raw, "supplier_sku", errors),
        "supplier_description": _required_text(raw, "supplier_description", errors),
        "canonical_variant_id": _required_text(raw, "canonical_variant_id", errors),
        "package_type": package_type,
        "size_text": _required_text(raw, "size_text", errors),
        "raw_pack": _required_text(raw, "raw_pack", errors),
        "shopify_units_per_case": shopify_units,
        "qualifying_units_per_case": qualifying_units,
        "assortment_scope": assortment_scope,
        "assortment_group": assortment_group,
        "assortable": assortable,
        "assortment_evidence": assortment_evidence,
        "level_type": level_type,
        "break_quantity": break_quantity,
        "break_unit": break_unit,
        "case_price": case_price,
        "unit_price": unit_price,
        "source_file": _required_text(raw, "source_file", errors),
        "source_page": _source_page(raw, errors),
        "source_evidence": _required_text(raw, "source_evidence", errors),
        "extraction_confidence": extraction_confidence,
        "review_note": _optional_text(raw, "review_note"),
        "offer_id": None,
        "errors": errors,
        "warnings": warnings,
    }


def _parse_date(text: str, field: str) -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise PriceBookError(f"{field} must be an ISO date") from exc


def parse_price_book_csv(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes) or not data:
        raise PriceBookError("price-book CSV is required")
    if len(data) > MAX_PRICE_BOOK_BYTES:
        raise PriceBookError("price-book CSV exceeds the 5,000,000 byte limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PriceBookError("price-book CSV must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != list(PRICE_BOOK_HEADERS):
        raise PriceBookError("price-book CSV header does not match the strict normalized contract")
    raw_rows = list(reader)
    if not raw_rows:
        raise PriceBookError("price-book CSV must contain at least one data row")
    if any(None in row for row in raw_rows):
        raise PriceBookError("price-book CSV row has more values than the strict header")

    envelope_fields = (
        "batch_ref",
        "target_price_state",
        "effective_from",
        "effective_through",
        "vendor_name",
    )
    envelope: dict[str, str] = {}
    for field in envelope_fields:
        values = {(row.get(field) or "").strip() for row in raw_rows}
        if len(values) != 1 or not next(iter(values)):
            raise PriceBookError(f"every row must share one nonblank {field}")
        envelope[field] = next(iter(values))
    target_state = envelope["target_price_state"]
    if target_state != "future" or any(
        row.get("target_price_state") != "future" for row in raw_rows
    ):
        raise PriceBookError(
            "price-book uploads may target FUTURE only; CURRENT changes require guarded rollover"
        )
    effective_from = _parse_date(envelope["effective_from"], "effective_from")
    effective_through = (
        _parse_date(envelope["effective_through"], "effective_through")
        if envelope["effective_through"]
        else None
    )
    if effective_through is not None and effective_through < effective_from:
        raise PriceBookError("effective_through cannot precede effective_from")
    rows = [_normalize_row(row, number) for number, row in enumerate(raw_rows, start=2)]
    return {
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "batch_ref": envelope["batch_ref"],
        "target_price_state": target_state,
        "effective_from": effective_from,
        "effective_through": effective_through,
        "vendor_name": envelope["vendor_name"],
        "rows": rows,
    }


def _text_equal(first: Any, second: Any) -> bool:
    left = None if first is None or not str(first).strip() else " ".join(str(first).split())
    right = None if second is None or not str(second).strip() else " ".join(str(second).split())
    return left == right


def _decimal_equal(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return Decimal(first) == Decimal(second)


def _append_row_issue(row: dict[str, Any], issue: dict[str, str]) -> None:
    destination = "warnings" if issue["severity"] == "WARN" else "errors"
    if issue["code"] not in {existing["code"] for existing in row[destination]}:
        row[destination].append(issue)


def _stable_decimal(value: Any) -> str | None:
    return None if value is None else format(Decimal(value).normalize(), "f")


def _future_generation_fingerprint(conn: Any, vendor_id: str | None) -> str:
    """Hash the complete operational FUTURE set for one vendor.

    The hash deliberately excludes surrogate IDs and batch provenance.  It is
    used as an optimistic predecessor token: a validated candidate cannot
    replace a FUTURE set that changed after validation.
    """
    if vendor_id is None:
        rows: list[tuple] = []
    else:
        rows = conn.execute(
            """SELECT p.offer_id,p.effective_month,p.level_type,p.break_qty,
                      p.break_unit,p.case_price,p.unit_price,p.source_file,
                      p.source_page,p.extraction_confidence,p.notes,
                      p.effective_from,p.effective_through
                 FROM prices p
                 JOIN supplier_offers o ON o.offer_id=p.offer_id
                WHERE o.vendor_id=%s AND p.price_state='future'
                ORDER BY p.offer_id,(p.level_type <> 'BASE'),p.break_unit NULLS FIRST,
                         p.break_qty NULLS FIRST,p.price_id""",
            (vendor_id,),
        ).fetchall()
    payload = [
        {
            "offer_id": int(row[0]),
            "effective_month": row[1].isoformat(),
            "level_type": row[2],
            "break_quantity": _stable_decimal(row[3]),
            "break_unit": row[4],
            "case_price": _stable_decimal(row[5]),
            "unit_price": _stable_decimal(row[6]),
            "source_file": row[7],
            "source_page": row[8],
            "extraction_confidence": row[9],
            "review_note": row[10],
            "effective_from": row[11].isoformat() if row[11] else None,
            "effective_through": row[12].isoformat() if row[12] else None,
        }
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _future_predecessor_batch_id(conn: Any, vendor_id: str | None) -> str | None:
    if vendor_id is None:
        return None
    rows = conn.execute(
        """SELECT price_book_batch_id
             FROM price_book_batches
            WHERE vendor_id=%s AND status='VERIFIED_FUTURE'""",
        (vendor_id,),
    ).fetchall()
    if len(rows) > 1:
        raise PriceBookError("multiple active VERIFIED FUTURE batches violate authority")
    return None if not rows else str(rows[0][0])


def _candidate_future_fingerprint(validation: dict[str, Any], effective_from: date,
                                  effective_through: date | None) -> str:
    month = effective_from.replace(day=1).isoformat()
    payload = []
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
        payload.append(
            {
                "offer_id": int(row["offer_id"]),
                "effective_month": month,
                "level_type": row["level_type"],
                "break_quantity": _stable_decimal(row["break_quantity"]),
                "break_unit": row["break_unit"],
                "case_price": _stable_decimal(row["case_price"]),
                "unit_price": _stable_decimal(row["unit_price"]),
                "source_file": row["source_file"],
                "source_page": row["source_page"],
                "extraction_confidence": row["extraction_confidence"],
                "review_note": row["review_note"],
                "effective_from": effective_from.isoformat(),
                "effective_through": (
                    effective_through.isoformat() if effective_through else None
                ),
            }
        )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _database_validation(conn: Any, parsed: dict[str, Any]) -> dict[str, Any]:
    vendor = conn.execute(
        "SELECT vendor_id,active FROM vendors WHERE vendor_name=%s",
        (parsed["vendor_name"],),
    ).fetchone()
    vendor_id = str(vendor[0]) if vendor is not None else None
    vendor_active = bool(vendor[1]) if vendor is not None else False
    future_predecessor_sha256 = _future_generation_fingerprint(conn, vendor_id)
    future_predecessor_batch_id = _future_predecessor_batch_id(conn, vendor_id)
    offers: list[tuple] = []
    if vendor_id is not None:
        offers = conn.execute(
            """SELECT o.offer_id,o.supplier_sku,o.variant_id,o.package_type,
                      o.size_text,o.raw_pack,o.shopify_units_per_case,
                      o.qualifying_units_per_case,o.assortment_scope,
                      o.assortment_group,o.assortable,o.confidence,o.active,
                      is_procurement_eligible_variant(o.variant_id)
                 FROM supplier_offers o
                WHERE o.vendor_id=%s AND o.active
                ORDER BY o.offer_id""",
            (vendor_id,),
        ).fetchall()
    offer_by_sku = {
        str(row[1]): row for row in offers if row[1] is not None and str(row[1]).strip()
    }
    expected_offers = {int(row[0]): row for row in offers if bool(row[13])}
    issues: list[dict[str, Any]] = []
    if vendor is None:
        issues.append(
            {
                **_issue("UNKNOWN_VENDOR", "vendor_name is not an existing vendor"),
                "source_row_number": None,
                "vendor_id": None,
                "variant_id": None,
                "offer_id": None,
            }
        )
    elif not vendor_active:
        issues.append(
            {
                **_issue("INACTIVE_VENDOR", "vendor is inactive"),
                "source_row_number": None,
                "vendor_id": vendor_id,
                "variant_id": None,
                "offer_id": None,
            }
        )

    for row in parsed["rows"]:
        sku = row["supplier_sku"]
        offer = offer_by_sku.get(sku) if sku is not None else None
        if offer is None:
            _append_row_issue(
                row,
                _issue("MAPPING_NOT_FOUND", "supplier SKU has no active exact vendor offer"),
            )
            continue
        row["offer_id"] = int(offer[0])
        if offer[11] != "VERIFIED":
            _append_row_issue(
                row,
                _issue("MAPPING_NOT_VERIFIED", "supplier offer mapping is not VERIFIED"),
            )
        if not bool(offer[13]):
            _append_row_issue(
                row,
                _issue("VARIANT_NOT_ELIGIBLE", "mapped variant is not CURRENT, active, and LIVE"),
            )
        if row["canonical_variant_id"] != str(offer[2]):
            _append_row_issue(
                row,
                _issue("MAPPING_VARIANT_MISMATCH", "CSV Variant ID differs from supplier offer"),
            )
        comparisons = (
            ("package_type", row["package_type"], str(offer[3]).upper() if offer[3] else None),
            ("size_text", row["size_text"], offer[4]),
            ("raw_pack", row["raw_pack"], offer[5]),
            ("assortment_scope", row["assortment_scope"], offer[8]),
            ("assortment_group", row["assortment_group"], offer[9]),
        )
        for field, supplied, authoritative in comparisons:
            if not _text_equal(supplied, authoritative):
                _append_row_issue(
                    row,
                    _issue(
                        f"OFFER_{field.upper()}_MISMATCH",
                        f"{field} differs from the existing supplier offer",
                    ),
                )
        for field, supplied, authoritative in (
            ("shopify_units_per_case", row["shopify_units_per_case"], offer[6]),
            ("qualifying_units_per_case", row["qualifying_units_per_case"], offer[7]),
        ):
            if not _decimal_equal(supplied, authoritative):
                _append_row_issue(
                    row,
                    _issue(
                        f"OFFER_{field.upper()}_MISMATCH",
                        f"{field} differs from the existing supplier offer",
                    ),
                )
        if offer[10] is None:
            _append_row_issue(
                row,
                _issue("OFFER_ASSORTMENT_UNCONFIRMED", "offer assortable value is not confirmed"),
            )
        elif row["assortable"] is not bool(offer[10]):
            _append_row_issue(
                row,
                _issue("OFFER_ASSORTABLE_MISMATCH", "assortable differs from supplier offer"),
            )

    rows_by_offer: dict[int, list[dict[str, Any]]] = {}
    for row in parsed["rows"]:
        if row["offer_id"] is not None:
            rows_by_offer.setdefault(row["offer_id"], []).append(row)
    covered_offers: set[int] = set()
    for offer_id, rows in rows_by_offer.items():
        natural_keys: set[tuple] = set()
        base_rows = [row for row in rows if row["level_type"] == "BASE"]
        if len(base_rows) != 1:
            for row in rows:
                _append_row_issue(
                    row,
                    _issue("LADDER_BASE_COUNT", "each offer requires exactly one BASE row"),
                )
        for row in rows:
            key = (row["level_type"], row["break_quantity"], row["break_unit"])
            if key in natural_keys:
                _append_row_issue(
                    row,
                    _issue("DUPLICATE_LADDER_LEVEL", "duplicate structural ladder level"),
                )
            natural_keys.add(key)
        if len(base_rows) == 1 and base_rows[0]["unit_price"] is not None:
            # BT and CS are distinct qualification programs.  A supplier may
            # document both for one offer; compare thresholds only within the
            # same typed program and always against the common BASE cost.
            for break_unit in ("BT", "CS"):
                ordered_breaks = sorted(
                    (
                        row
                        for row in rows
                        if row["level_type"] == "BREAK"
                        and row["break_unit"] == break_unit
                    ),
                    key=lambda row: (
                        row["break_quantity"] is None,
                        row["break_quantity"] or Decimal("0"),
                        row["source_row_number"],
                    ),
                )
                previous = base_rows[0]["unit_price"]
                for row in ordered_breaks:
                    if row["unit_price"] is not None and row["unit_price"] > previous:
                        _append_row_issue(
                            row,
                            _issue(
                                "LADDER_PRICE_INCREASE",
                                "deeper levels within a typed ladder cannot cost more",
                            ),
                        )
                    if row["unit_price"] is not None:
                        previous = row["unit_price"]
        if (
            offer_id in expected_offers
            and len(base_rows) == 1
            and not any(row["errors"] for row in rows)
        ):
            covered_offers.add(offer_id)

        # Structural and numeric changes are always explicit diagnostics.  No
        # store-specific percentage threshold has been owner-approved, so the
        # validator does not invent one; explicit promotion authority is still
        # required after the operator reviews these warnings.
        current_rows = conn.execute(
            """SELECT level_type,break_qty,break_unit,unit_price
                 FROM prices
                WHERE offer_id=%s AND price_state='current' AND verified
                ORDER BY level_type,break_qty NULLS FIRST,break_unit NULLS FIRST""",
            (offer_id,),
        ).fetchall()
        if current_rows and rows:
            current_ladder = {
                (item[0], item[1], item[2]): Decimal(item[3]) for item in current_rows
            }
            candidate_ladder = {
                (item["level_type"], item["break_quantity"], item["break_unit"]): item
                for item in rows
                if item["level_type"] is not None and item["unit_price"] is not None
            }
            if set(current_ladder) != set(candidate_ladder):
                _append_row_issue(
                    rows[0],
                    _issue(
                        "CURRENT_LADDER_STRUCTURE_CHANGE",
                        "candidate ladder structure differs from verified CURRENT pricing",
                        severity="WARN",
                    ),
                )
            for key in sorted(
                set(current_ladder) & set(candidate_ladder), key=lambda value: str(value)
            ):
                candidate = candidate_ladder[key]
                if candidate["unit_price"] != current_ladder[key]:
                    _append_row_issue(
                        candidate,
                        _issue(
                            "CURRENT_UNIT_PRICE_CHANGE",
                            (
                                f"verified CURRENT unit price {current_ladder[key]} changes "
                                f"to {candidate['unit_price']}"
                            ),
                            severity="WARN",
                        ),
                    )

    missing_offer_ids = sorted(set(expected_offers) - covered_offers)
    for offer_id in missing_offer_ids:
        offer = expected_offers[offer_id]
        issues.append(
            {
                **_issue(
                    "MISSING_REQUIRED_OFFER",
                    f"eligible supplier offer {offer_id} has no complete valid ladder",
                ),
                "source_row_number": None,
                "vendor_id": vendor_id,
                "variant_id": str(offer[2]),
                "offer_id": offer_id,
            }
        )
    for row in parsed["rows"]:
        for issue in row["errors"] + row["warnings"]:
            issues.append(
                {
                    **issue,
                    "source_row_number": row["source_row_number"],
                    "vendor_id": vendor_id,
                    "variant_id": None,
                    "offer_id": row["offer_id"],
                }
            )
    issues.sort(
        key=lambda item: (
            item["severity"],
            item["source_row_number"] or -1,
            item["offer_id"] or -1,
            item["code"],
        )
    )
    semantic_rows = []
    for row in parsed["rows"]:
        semantic_rows.append(
            {
                key: (
                    format(value, "f") if isinstance(value, Decimal) else value
                )
                for key, value in row.items()
                if key not in {"raw_payload"}
            }
        )
    payload = {
        "contract": "STRICT_NORMALIZED_PRICE_BOOK_V1",
        "content_sha256": parsed["content_sha256"],
        "vendor_id": vendor_id,
        "vendor_name": parsed["vendor_name"],
        "target_price_state": parsed["target_price_state"],
        "future_predecessor_sha256": future_predecessor_sha256,
        "future_predecessor_batch_id": future_predecessor_batch_id,
        "effective_from": parsed["effective_from"].isoformat(),
        "effective_through": (
            parsed["effective_through"].isoformat()
            if parsed["effective_through"] is not None else None
        ),
        "rows": semantic_rows,
        "expected_offer_ids": sorted(expected_offers),
        "covered_offer_ids": sorted(covered_offers),
        "missing_offer_ids": missing_offer_ids,
        "issues": issues,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    errors = [issue for issue in issues if issue["severity"] == "ERROR"]
    warnings = [issue for issue in issues if issue["severity"] == "WARN"]
    return {
        "vendor_id": vendor_id,
        "rows": parsed["rows"],
        "issues": issues,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "valid_row_count": sum(not row["errors"] for row in parsed["rows"]),
        "expected_offer_count": len(expected_offers),
        "covered_offer_count": len(covered_offers),
        "missing_offer_count": len(missing_offer_ids),
        "validation_fingerprint": fingerprint,
        "future_predecessor_sha256": future_predecessor_sha256,
        "future_predecessor_batch_id": future_predecessor_batch_id,
        "validation_evidence": {
            "contract": payload["contract"],
            "expected_offer_ids": payload["expected_offer_ids"],
            "covered_offer_ids": payload["covered_offer_ids"],
            "missing_offer_ids": missing_offer_ids,
            "error_codes": sorted({issue["code"] for issue in errors}),
            "warning_codes": sorted({issue["code"] for issue in warnings}),
            "future_predecessor_sha256": future_predecessor_sha256,
            "future_predecessor_batch_id": future_predecessor_batch_id,
        },
    }


def _transaction_lock(conn: Any) -> None:
    locked = conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (_LOCK_ID,)).fetchone()[0]
    if not locked:
        raise PriceBookError("another price-book transaction is already active")


def _verified_storage_write(storage: StorageAdapter, key: str, data: bytes) -> None:
    if storage.exists(key):
        existing = storage.get_bytes(key)
        if existing != data:
            raise PriceBookError("content-addressed raw price-book object does not match")
        return
    storage.put_bytes(key, data)
    if not storage.exists(key) or storage.get_bytes(key) != data:
        raise PriceBookError("raw price-book storage verification failed")


def _verified_storage_read(storage: StorageAdapter, key: str, expected_hash: str) -> bytes:
    if not storage.exists(key):
        raise PriceBookError("raw price-book evidence is missing")
    data = storage.get_bytes(key)
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise PriceBookError("raw price-book evidence hash does not match")
    return data


def _existing_batch(conn: Any, parsed: dict[str, Any]) -> tuple | None:
    return conn.execute(
        """SELECT price_book_batch_id,content_sha256,raw_storage_key,status,
                  validation_fingerprint,target_price_state,effective_from,effective_through
             FROM price_book_batches
            WHERE vendor_name=%s AND batch_ref=%s""",
        (parsed["vendor_name"], parsed["batch_ref"]),
    ).fetchone()


def _assert_existing_identity(existing: tuple, parsed: dict[str, Any]) -> None:
    if (
        existing[1] != parsed["content_sha256"]
        or existing[5] != parsed["target_price_state"]
        or existing[6] != parsed["effective_from"]
        or existing[7] != parsed["effective_through"]
    ):
        raise PriceBookError("batch_ref already exists with different immutable input")


def stage_and_validate_price_book(
    conn: Any,
    storage: StorageAdapter,
    *,
    csv_bytes: bytes,
    actor: str,
) -> dict[str, Any]:
    if not isinstance(actor, str) or not actor.strip():
        raise PriceBookError("staging actor is required")
    parsed = parse_price_book_csv(csv_bytes)
    raw_key = f"price-books/raw/{parsed['content_sha256']}.csv"
    replay_batch_id: str | None = None
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        existing = _existing_batch(conn, parsed)
        if existing is not None:
            _assert_existing_identity(existing, parsed)
            _verified_storage_read(storage, existing[2], existing[1])
            batch_id = str(existing[0])
            replay_batch_id = batch_id
        else:
            _verified_storage_write(storage, raw_key, csv_bytes)
            validation = _database_validation(conn, parsed)
            batch_row = conn.execute(
                """INSERT INTO price_book_batches(
                       vendor_id,vendor_name,batch_ref,target_price_state,
                       effective_from,effective_through,content_sha256,
                       raw_storage_key,future_predecessor_sha256,
                       future_predecessor_batch_id,status,
                       row_count,staged_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'STAGING',%s,%s)
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
                    actor.strip(),
                ),
            ).fetchone()
            batch_id = str(batch_row[0])
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
                           raw_payload
                       ) VALUES (
                           %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                           %s::jsonb,%s::jsonb
                       )""",
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
                           vendor_id,variant_id,offer_id,message
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
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
        result = get_price_book_batch(conn, batch_id)
    result["idempotent_replay"] = replay_batch_id is not None
    return result


def list_price_book_batches(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT price_book_batch_id,batch_generation,vendor_name,batch_ref,target_price_state,
                  effective_from,effective_through,status,
                  CASE WHEN status IN ('VALIDATED','VERIFIED_FUTURE')
                         AND date_trunc('month',effective_from)::date <>
                             (date_trunc('month',(clock_timestamp() AT TIME ZONE
                                'America/New_York')) + interval '1 month')::date
                       THEN 'TEMPORAL_BLOCKED' ELSE status END AS operational_status,
                  row_count,valid_row_count,
                  error_count,warning_count,expected_offer_count,covered_offer_count,
                  missing_offer_count,validation_fingerprint,future_predecessor_sha256,
                  future_predecessor_batch_id,
                  staged_at,promoted_at,disposition_at
             FROM price_book_batches
            ORDER BY staged_at DESC,price_book_batch_id DESC"""
    ).fetchall()
    keys = (
        "price_book_batch_id", "batch_generation", "vendor_name", "batch_ref", "target_price_state",
        "effective_from", "effective_through", "status", "operational_status", "row_count",
        "valid_row_count", "error_count", "warning_count", "expected_offer_count",
        "covered_offer_count", "missing_offer_count", "validation_fingerprint",
        "future_predecessor_sha256", "future_predecessor_batch_id",
        "staged_at", "promoted_at", "disposition_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def get_price_book_batch(conn: Any, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT price_book_batch_id,batch_generation,vendor_id,vendor_name,batch_ref,
                  target_price_state,effective_from,effective_through,content_sha256,
                  raw_storage_key,future_predecessor_sha256,future_predecessor_batch_id,
                  status,
                  CASE WHEN status IN ('VALIDATED','VERIFIED_FUTURE')
                         AND date_trunc('month',effective_from)::date <>
                             (date_trunc('month',(clock_timestamp() AT TIME ZONE
                                'America/New_York')) + interval '1 month')::date
                       THEN 'TEMPORAL_BLOCKED' ELSE status END AS operational_status,
                  row_count,valid_row_count,error_count,
                  warning_count,expected_offer_count,covered_offer_count,
                  missing_offer_count,validation_fingerprint,validation_evidence,
                  staged_by,staged_at,promoted_by,promoted_at,
                  disposition_by,disposition_reason,disposition_at
             FROM price_book_batches WHERE price_book_batch_id=%s""",
        (batch_id,),
    ).fetchone()
    if row is None:
        raise PriceBookError("unknown price-book batch")
    keys = (
        "price_book_batch_id", "batch_generation", "vendor_id", "vendor_name", "batch_ref",
        "target_price_state", "effective_from", "effective_through", "content_sha256",
        "raw_storage_key", "future_predecessor_sha256", "future_predecessor_batch_id",
        "status", "operational_status", "row_count", "valid_row_count", "error_count",
        "warning_count", "expected_offer_count", "covered_offer_count",
        "missing_offer_count", "validation_fingerprint", "validation_evidence",
        "staged_by", "staged_at", "promoted_by", "promoted_at",
        "disposition_by", "disposition_reason", "disposition_at",
    )
    result = dict(zip(keys, row, strict=True))
    result["issues"] = [
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
            """SELECT source_row_number,issue_code,severity,variant_id,offer_id,
                      message,resolved_at
                 FROM price_book_validation_issues
                WHERE price_book_batch_id=%s
                ORDER BY severity DESC,source_row_number NULLS FIRST,
                         issue_code,offer_id NULLS FIRST""",
            (batch_id,),
        ).fetchall()
    ]
    return result


def read_raw_price_book(
    conn: Any, storage: StorageAdapter, *, batch_id: str
) -> bytes:
    row = conn.execute(
        """SELECT raw_storage_key,content_sha256 FROM price_book_batches
            WHERE price_book_batch_id=%s""",
        (batch_id,),
    ).fetchone()
    if row is None:
        raise PriceBookError("unknown price-book batch")
    return _verified_storage_read(storage, row[0], row[1])


def _parsed_from_staging(conn: Any, batch: tuple) -> dict[str, Any]:
    raw_rows = conn.execute(
        """SELECT source_row_number,raw_payload
             FROM price_book_staging_rows
            WHERE price_book_batch_id=%s
            ORDER BY source_row_number""",
        (batch[0],),
    ).fetchall()
    if len(raw_rows) != batch[9]:
        raise PriceBookError("validated staging row count drifted")
    rows = [_normalize_row(dict(raw), int(number)) for number, raw in raw_rows]
    return {
        "content_sha256": batch[6],
        "batch_ref": batch[3],
        "target_price_state": batch[4],
        "effective_from": batch[5],
        "effective_through": batch[7],
        "vendor_name": batch[2],
        "rows": rows,
    }


def _current_coverage(conn: Any, vendor_id: str) -> dict[str, Any]:
    expected = {
        int(row[0])
        for row in conn.execute(
            """SELECT o.offer_id FROM supplier_offers o
                JOIN vendors v ON v.vendor_id=o.vendor_id
               WHERE o.vendor_id=%s AND o.active AND o.confidence='VERIFIED'
                 AND v.active AND is_procurement_eligible_variant(o.variant_id)""",
            (vendor_id,),
        ).fetchall()
    }
    covered = {
        int(row[0])
        for row in conn.execute(
            """SELECT DISTINCT offer_id FROM v_verified_current_prices
                WHERE vendor_id=%s AND level_type='BASE'""",
            (vendor_id,),
        ).fetchall()
    }
    missing = sorted(expected - covered)
    return {
        "expected_offer_ids": sorted(expected),
        "covered_offer_ids": sorted(expected & covered),
        "missing_offer_ids": missing,
        "status": "PASS" if expected and not missing else "FAIL",
    }


def _record_batch_disposition(
    conn: Any,
    *,
    batch: tuple,
    new_status: str,
    actor: str,
    reason: str,
) -> None:
    batch_id, prior_status, fingerprint = str(batch[0]), str(batch[1]), str(batch[2])
    evidence = {
        "source": "PRICE_BOOK_BATCH_DISPOSITION",
        "prior_status": prior_status,
        "new_status": new_status,
        "validation_fingerprint": fingerprint,
        "typed_rows_purged": int(
            conn.execute(
                "SELECT count(*) FROM price_book_staging_rows WHERE price_book_batch_id=%s",
                (batch_id,),
            ).fetchone()[0]
        ),
    }
    event_time = conn.execute(
        """INSERT INTO price_book_disposition_events(
               price_book_batch_id,prior_status,new_status,validation_fingerprint,
               reason,evidence_json,recorded_by
           ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
           RETURNING recorded_at""",
        (
            batch_id,
            prior_status,
            new_status,
            fingerprint,
            reason,
            json.dumps(evidence, sort_keys=True),
            actor,
        ),
    ).fetchone()[0]
    conn.execute(
        """UPDATE price_book_validation_issues SET resolved_at=now()
            WHERE price_book_batch_id=%s AND resolved_at IS NULL""",
        (batch_id,),
    )
    conn.execute(
        "DELETE FROM price_book_staging_rows WHERE price_book_batch_id=%s",
        (batch_id,),
    )
    conn.execute(
        """UPDATE price_book_batches
              SET status=%s,disposition_by=%s,disposition_reason=%s,
                  disposition_at=%s
            WHERE price_book_batch_id=%s""",
        (new_status, actor, reason, event_time, batch_id),
    )


def reject_price_book_batch(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    expected_validation_fingerprint: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    fingerprint = str(expected_validation_fingerprint)
    if not _SHA256.fullmatch(fingerprint):
        raise PriceBookError("exact lowercase validation fingerprint is required")
    if not isinstance(actor, str) or not actor.strip():
        raise PriceBookError("disposition actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise PriceBookError("disposition reason is required")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        batch = conn.execute(
            """SELECT price_book_batch_id,status,validation_fingerprint,
                      raw_storage_key,content_sha256
                 FROM price_book_batches
                WHERE price_book_batch_id=%s FOR UPDATE""",
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise PriceBookError("unknown price-book batch")
        if batch[2] != fingerprint:
            raise PriceBookError("stale or incorrect validation fingerprint")
        _verified_storage_read(storage, batch[3], batch[4])
        if batch[1] == "REJECTED":
            return {
                "price_book_batch_id": str(batch[0]),
                "status": "REJECTED",
                "idempotent_replay": True,
            }
        if batch[1] not in {"INVALID", "VALIDATED"}:
            raise PriceBookError("only INVALID or VALIDATED batches may be rejected")
        _record_batch_disposition(
            conn,
            batch=(batch[0], batch[1], batch[2]),
            new_status="REJECTED",
            actor=actor.strip(),
            reason=reason.strip(),
        )
        conn.execute(
            """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
               VALUES ('price_book_batches',%s,'REJECT',%s::jsonb,%s)""",
            (
                batch_id,
                json.dumps(
                    {"status": "REJECTED", "reason": reason.strip()},
                    sort_keys=True,
                ),
                actor.strip(),
            ),
        )
    return {
        "price_book_batch_id": str(batch[0]),
        "status": "REJECTED",
        "idempotent_replay": False,
    }


def promote_price_book_batch(
    conn: Any,
    storage: StorageAdapter,
    *,
    batch_id: str,
    expected_validation_fingerprint: str,
    actor: str,
    warning_review_reason: str | None = None,
) -> dict[str, Any]:
    fingerprint = str(expected_validation_fingerprint)
    if not _SHA256.fullmatch(fingerprint):
        raise PriceBookError("exact lowercase validation fingerprint is required")
    if not isinstance(actor, str) or not actor.strip():
        raise PriceBookError("promotion actor is required")
    review_reason = warning_review_reason.strip() if isinstance(warning_review_reason, str) else None
    if review_reason == "":
        review_reason = None
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        batch = conn.execute(
            """SELECT price_book_batch_id,vendor_id,vendor_name,batch_ref,
                      target_price_state,effective_from,content_sha256,
                      effective_through,validation_fingerprint,row_count,status,
                      expected_offer_count,covered_offer_count,missing_offer_count,
                      raw_storage_key,warning_count,validation_evidence,
                      batch_generation,future_predecessor_sha256,
                      future_predecessor_batch_id
                 FROM price_book_batches
                WHERE price_book_batch_id=%s FOR UPDATE""",
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise PriceBookError("unknown price-book batch")
        if batch[8] != fingerprint:
            raise PriceBookError("stale or incorrect validation fingerprint")
        _verified_storage_read(storage, batch[14], batch[6])
        expected_future_month = conn.execute(
            """SELECT (date_trunc('month',(clock_timestamp() AT TIME ZONE
                         'America/New_York')) + interval '1 month')::date"""
        ).fetchone()[0]
        if batch[5].replace(day=1) != expected_future_month:
            raise PriceBookError(
                "FUTURE promotion requires exactly the next Buffalo business month"
            )
        if batch[10] == "VERIFIED_FUTURE":
            return {
                "price_book_batch_id": str(batch[0]),
                "status": "VERIFIED_FUTURE",
                "idempotent_replay": True,
                "promoted_price_rows": int(
                    conn.execute(
                        "SELECT count(*) FROM prices WHERE source_price_book_batch_id=%s",
                        (batch_id,),
                    ).fetchone()[0]
                ),
            }
        if batch[4] != "future" or batch[10] != "VALIDATED" or batch[1] is None or batch[13] != 0:
            raise PriceBookError("only a complete FUTURE VALIDATED batch can be promoted")
        if batch[15] and not review_reason:
            raise PriceBookError("warning-bearing batch requires an explicit review reason")
        if not batch[15] and review_reason is not None:
            raise PriceBookError("warning review reason is only valid when warnings exist")
        newer = conn.execute(
            """SELECT price_book_batch_id FROM price_book_batches
                WHERE vendor_id=%s AND price_book_batch_id<>%s
                  AND status IN ('VALIDATED','VERIFIED_FUTURE')
                  AND (
                       date_trunc('month',effective_from) > date_trunc('month',%s::date)
                       OR (date_trunc('month',effective_from)=date_trunc('month',%s::date)
                           AND batch_generation>%s)
                  )
                ORDER BY effective_from DESC,batch_generation DESC
                LIMIT 1 FOR UPDATE""",
            (batch[1], batch[0], batch[5], batch[5], batch[17]),
        ).fetchone()
        if newer is not None:
            raise PriceBookError("a newer validated FUTURE batch supersedes this candidate")
        observed_predecessor = _future_generation_fingerprint(conn, str(batch[1]))
        if observed_predecessor != batch[18]:
            raise PriceBookError("operational FUTURE predecessor changed after validation")
        observed_predecessor_batch = _future_predecessor_batch_id(conn, str(batch[1]))
        if observed_predecessor_batch != (str(batch[19]) if batch[19] is not None else None):
            raise PriceBookError("operational FUTURE predecessor batch changed after validation")

        raw_bytes = _verified_storage_read(storage, batch[14], batch[6])
        raw_parsed = parse_price_book_csv(raw_bytes)
        parsed = _parsed_from_staging(conn, batch)
        validation = _database_validation(conn, parsed)
        raw_validation = _database_validation(conn, raw_parsed)
        if (
            validation["error_count"] != 0
            or validation["validation_fingerprint"] != fingerprint
            or raw_validation["validation_fingerprint"] != fingerprint
            or validation["future_predecessor_sha256"] != batch[18]
            or validation["future_predecessor_batch_id"]
               != (str(batch[19]) if batch[19] is not None else None)
            or validation["expected_offer_count"] != batch[11]
            or validation["covered_offer_count"] != batch[12]
            or validation["missing_offer_count"] != 0
        ):
            raise PriceBookError("locked price-book revalidation does not match authority")
        offer_ids = sorted({int(row["offer_id"]) for row in validation["rows"]})
        if not offer_ids:
            raise PriceBookError("validated batch has no promotable supplier offers")
        warning_codes = sorted(validation["validation_evidence"]["warning_codes"])
        promoted_future_sha256 = _candidate_future_fingerprint(
            validation, batch[5], batch[7]
        )
        promoted_semantic_md5 = conn.execute(
            "SELECT price_book_staging_semantic_md5(%s)", (batch_id,)
        ).fetchone()[0]
        promotion_evidence = {
            "source": "LOCKED_PRICE_BOOK_REVALIDATION",
            "content_sha256": batch[6],
            "validation_fingerprint": fingerprint,
            "vendor_id": str(batch[1]),
            "target_price_state": "future",
            "offer_ids": offer_ids,
            "predecessor_future_sha256": batch[18],
            "predecessor_batch_id": (
                str(batch[19]) if batch[19] is not None else None
            ),
            "promoted_future_sha256": promoted_future_sha256,
            "promoted_semantic_md5": promoted_semantic_md5,
            "acknowledged_warning_codes": warning_codes,
            "warning_review_reason": review_reason,
        }
        promotion_time = conn.execute(
            """INSERT INTO price_book_promotion_events(
                   price_book_batch_id,prior_status,new_status,
                   validation_fingerprint,predecessor_future_sha256,
                   predecessor_batch_id,
                   promoted_future_sha256,promoted_semantic_md5,promoted_row_count,
                   acknowledged_warning_codes,review_reason,evidence_json,recorded_by
               ) VALUES (%s,'VALIDATED','VERIFIED_FUTURE',%s,%s,%s,%s,%s,%s,%s::jsonb,
                         %s,%s::jsonb,%s)
               RETURNING recorded_at""",
            (
                batch_id,
                fingerprint,
                batch[18],
                batch[19],
                promoted_future_sha256,
                promoted_semantic_md5,
                len(validation["rows"]),
                json.dumps(warning_codes),
                review_reason,
                json.dumps(promotion_evidence, sort_keys=True),
                actor.strip(),
            ),
        ).fetchone()[0]

        prior_batches = conn.execute(
            """SELECT price_book_batch_id,status,validation_fingerprint
                 FROM price_book_batches
                WHERE vendor_id=%s AND price_book_batch_id<>%s
                  AND status IN ('VALIDATED','VERIFIED_FUTURE')
                  AND (
                       date_trunc('month',effective_from) < date_trunc('month',%s::date)
                       OR (date_trunc('month',effective_from)=date_trunc('month',%s::date)
                           AND batch_generation<%s)
                  )
                ORDER BY batch_generation FOR UPDATE""",
            (batch[1], batch_id, batch[5], batch[5], batch[17]),
        ).fetchall()
        for prior in prior_batches:
            _record_batch_disposition(
                conn,
                batch=prior,
                new_status="SUPERSEDED",
                actor=actor.strip(),
                reason=f"Superseded by verified FUTURE batch {batch_id}",
            )

        # Replace the vendor's complete FUTURE state, including legacy/stale
        # rows whose offers are no longer candidates. CURRENT is never touched.
        conn.execute(
            """DELETE FROM prices p USING supplier_offers o
                WHERE p.offer_id=o.offer_id AND o.vendor_id=%s
                  AND p.price_state='future'""",
            (batch[1],),
        )
        effective_month = batch[5].replace(day=1)
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
                       effective_from,effective_through
                   ) VALUES (%s,'future',%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s,%s)""",
                (
                    row["offer_id"], effective_month, row["level_type"],
                    row["break_quantity"], row["break_unit"], row["case_price"],
                    row["unit_price"], row["source_file"], row["source_page"],
                    row["extraction_confidence"], row["review_note"], batch_id,
                    row["source_row_number"], batch[5], batch[7],
                ),
            )
        inserted = int(
            conn.execute(
                "SELECT count(*) FROM prices WHERE source_price_book_batch_id=%s",
                (batch_id,),
            ).fetchone()[0]
        )
        observed_future_sha256 = _future_generation_fingerprint(conn, str(batch[1]))
        if inserted != len(validation["rows"]) or observed_future_sha256 != promoted_future_sha256:
            raise PriceBookError("promoted FUTURE controls differ from validated candidate")
        conn.execute(
            """UPDATE price_book_validation_issues SET resolved_at=now()
                WHERE price_book_batch_id=%s AND resolved_at IS NULL""",
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
            (actor.strip(), promotion_time, batch_id),
        )
        conn.execute(
            """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
               VALUES ('price_book_batches',%s,'APPROVE',%s::jsonb,%s)""",
            (
                batch_id,
                json.dumps(
                    {
                        "status": "VERIFIED_FUTURE",
                        "validation_fingerprint": fingerprint,
                        "promoted_price_rows": inserted,
                        "promoted_future_sha256": promoted_future_sha256,
                        "warning_review_reason": review_reason,
                    },
                    sort_keys=True,
                ),
                actor.strip(),
            ),
        )
        coverage = _current_coverage(conn, str(batch[1]))
    return {
        "price_book_batch_id": str(batch[0]),
        "status": "VERIFIED_FUTURE",
        "idempotent_replay": False,
        "promoted_price_rows": inserted,
        "promoted_future_sha256": promoted_future_sha256,
        "current_price_coverage": coverage,
    }


def price_book_policy() -> dict[str, Any]:
    """Expose the canonical static safety policy for presentation/tests."""
    rules = load_rules()
    return {
        "strict_normalized_import_contract": bool(
            rules["pricing"]["strict_normalized_import_contract"]
        ),
        "staging_required": bool(rules["pricing"]["staging_required"]),
        "transactional_promotion_required": bool(
            rules["pricing"]["transactional_promotion_required"]
        ),
        "archive_enabled": bool(rules["pricing"]["archive_enabled"]),
    }

"""Content-addressed private Development Forecast V2 research connection.

The module consumes two immutable read-only analytics captures, applies only
frozen historical identity authority, and passes exact 138-day observations to
the registered V2 planner.  It deliberately stops before net need, case quantity,
economics, DRAFT, PO, or order authority.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping, Sequence

from .development_forecast import (
    V2_CONTRACT,
    DevelopmentForecastError,
    development_forecast_definition,
    load_development_forecast_policy,
    plan_development_forecast,
)
from .forecasting import DemandObservation
from .private_research_intake import (
    INTAKE_AUTHORITY,
    PRIVATE_RESEARCH_INTAKE_CONTRACT,
    ZERO_AUTHORITY,
    canonical_json_bytes,
    read_private_research_intake,
    validate_private_root,
)
from .private_research_projection import (
    AUTHORITY_LABEL,
    CALCULATED_RESEARCH_ONLY,
    REAL_NUMERICAL_EVALUATION_NOT_RUN,
    build_private_research_projection,
    private_research_projection_sha256,
)
from .storage import LocalFilesystemStorage


INPUT_CONTRACT = "BUFFALO_PRIVATE_DEVELOPMENT_FORECAST_RESEARCH_INPUT_V2"
PROJECTION_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2"
SOURCE_IDENTITY_CONTRACT = "BUFFALO_PRIVATE_SHOP_SOURCE_IDENTITY_V1"
COMPOSITE_HISTORY_CONTRACT = "BUFFALO_PRIVATE_FORECAST_HISTORY_COMPOSITE_V2"
DATA_MODE = "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY"
SCENARIO_CONTRACT = "BUFFALO_PRIVATE_FORECAST_RESEARCH_SCENARIO_V1"
SCENARIO_HORIZONS = (3, 10, 17)
EXPECTED_START = date(2026, 5, 4)
EXPECTED_END = date(2026, 9, 18)
EXTENSION_CONTRACT = "BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_EXTENSION_V2"
REPLACEMENT_CONTRACT = "BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_REPLACEMENT_V2"
REGISTERED_SOURCE_IDENTITY_ID = "bd8d6dfc014b3899366ac1378aeb92b21ac17164b84132a367e7f61a34db39d1"
REGISTERED_SOURCE_IDENTITY_FILE_SHA256 = "35055e3453367f44781f3929f9532ed3b6fb27e87ac8041c9a78775de079a245"
REGISTERED_SOURCE_EVIDENCE_SHA256 = "8ad9476283fad379910c4c03f68d5c63179e50128440671410d45cf115de618a"
SEED_ALIAS_SHA256 = "b9a2e862fcdff9e204f889ec277ae18201b78dc1197e53bb8876f5b520a68a21"
ORIGINAL_AUTHORITY_SHA256 = "95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287"
TERMINAL_AUTHORITY_SHA256 = "fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff"
REQUIRED_SOURCE_EVIDENCE = frozenset(
    {
        "ADMIN_SHOP_IDENTITY",
        "CONNECTED_SHOP_INFO",
        "CONNECTED_ANALYTICS_PROBE",
        "HISTORY_EXTENSION_54D",
        "HISTORY_REPLACEMENT_84D",
    }
)
REQUIRED_LIMITATIONS = frozenset(
    {
        "DEVELOPMENT_RESEARCH_ONLY_NOT_PRODUCTION_POLICY",
        "H3_H10_H17_ARE_ASSUMPTIONS_NOT_VENDOR_SCHEDULES",
        "AVAILABILITY_UNKNOWN_NO_STOCKOUT_OR_LATENT_DEMAND_INFERENCE",
        "RAW_INCOMING_NOT_TRUSTED_OR_USED",
        "PURE_FORECAST_ONLY_DOWNSTREAM_PURCHASING_STAGES_BLOCKED",
    }
)
_INPUT_PREFIX = "private-research/v2-inputs"
_SOURCE_PREFIX = "private-research/v2-sources/sha256"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VARIANT_ID = re.compile(r"^[1-9][0-9]*$")
_SHOP_GID = re.compile(r"^gid://shopify/Shop/[1-9][0-9]*$")
_COLUMNS = (
    ("day", "DAY_TIMESTAMP"),
    ("product_id", "IDENTITY"),
    ("product_variant_id", "IDENTITY"),
    ("product_title", "STRING"),
    ("product_variant_title", "STRING"),
    ("net_items_sold", "INTEGER"),
    ("gross_sales", "MONEY"),
    ("returns", "MONEY"),
    ("net_sales", "MONEY"),
    ("cost_of_goods_sold", "MONEY"),
    ("gross_profit", "MONEY"),
)
_QUERY_PREFIX = (
    "FROM sales SHOW net_items_sold, gross_sales, returns, net_sales, "
    "cost_of_goods_sold, gross_profit GROUP BY product_id, product_variant_id, "
    "product_title, product_variant_title TIMESERIES day SINCE "
)


class PrivateResearchV2Error(ValueError):
    """The V2 research source, input, or projection is not trustworthy."""


def _canonical(value: Any) -> bytes:
    return canonical_json_bytes(value)


def _projection_canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


_COMPOSITE_SOURCE_PROOF = object()
_INPUT_SOURCE_PROOF = object()
_SOURCE_VERIFIED_REGISTRY: dict[
    int, tuple["_SourceVerifiedMapping", object, bytes]
] = {}


class _SourceVerifiedMapping(dict[str, Any]):
    """In-process capability proving that raw-source replay created this value."""

    def __init__(self, value: Mapping[str, Any]) -> None:
        super().__init__(_json_clone(value))


def _seal_source_verified(
    value: Mapping[str, Any], *, proof: object
) -> _SourceVerifiedMapping:
    sealed = _SourceVerifiedMapping(value)
    _SOURCE_VERIFIED_REGISTRY[id(sealed)] = (
        sealed,
        proof,
        _canonical(dict(sealed)),
    )
    return sealed


def _source_verified(
    value: Mapping[str, Any], *, proof: object, field: str
) -> dict[str, Any]:
    registered = _SOURCE_VERIFIED_REGISTRY.get(id(value))
    if (
        not isinstance(value, _SourceVerifiedMapping)
        or registered is None
        or registered[0] is not value
        or registered[1] is not proof
        or registered[2] != _canonical(dict(value))
    ):
        raise PrivateResearchV2Error(f"{field} is not source-authenticated")
    return _json_clone(value)


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise PrivateResearchV2Error("research value is not canonical JSON") from exc


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PrivateResearchV2Error(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PrivateResearchV2Error(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise PrivateResearchV2Error(f"{field} must be a finite decimal")
    return parsed


def _decimal_text(value: Any, *, field: str) -> str:
    return format(_decimal(value, field=field), "f")


def _iso_date(value: Any, *, field: str) -> date:
    if not isinstance(value, str):
        raise PrivateResearchV2Error(f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PrivateResearchV2Error(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise PrivateResearchV2Error(f"{field} must be canonical")
    return parsed


def _utc(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise PrivateResearchV2Error(f"{field} must be UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PrivateResearchV2Error(f"{field} must be UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PrivateResearchV2Error(f"{field} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_file(path: str | Path, *, field: str) -> tuple[Path, bytes]:
    value = Path(path)
    if not value.is_absolute() or value.is_symlink() or value.resolve() != value:
        raise PrivateResearchV2Error(f"{field} path differs")
    try:
        info = value.stat(follow_symlinks=False)
        raw = value.read_bytes()
    except OSError as exc:
        raise PrivateResearchV2Error(f"{field} is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise PrivateResearchV2Error(f"{field} ownership or mode differs")
    return value, raw


def _registered_policy_identity() -> dict[str, Any]:
    definition = development_forecast_definition(V2_CONTRACT)
    policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
    if (
        policy.contract != definition.policy_contract
        or policy.method_version != definition.method_version
        or policy.source_sha256 != definition.policy_source_sha256
        or policy.canonical_sha256 != definition.policy_canonical_sha256
    ):
        raise PrivateResearchV2Error("registered V2 policy tuple differs")
    return {
        "evidence_contract": definition.evidence_contract,
        "policy_contract": definition.policy_contract,
        "method_version": definition.method_version,
        "profile": definition.profile,
        "policy_source_sha256": definition.policy_source_sha256,
        "policy_canonical_sha256": definition.policy_canonical_sha256,
        "commercial_authority": False,
        "production_activation": False,
    }


def _base_intake_identity(intake: Mapping[str, Any]) -> dict[str, str]:
    intake_id = intake.get("intake_id")
    if (
        intake.get("contract") != PRIVATE_RESEARCH_INTAKE_CONTRACT
        or not isinstance(intake_id, str)
        or not _HEX64.fullmatch(intake_id)
    ):
        raise PrivateResearchV2Error("base private intake differs")
    identity_basis = _json_clone(intake)
    identity_basis["intake_id"] = None
    if _sha(identity_basis) != intake_id:
        raise PrivateResearchV2Error("base private intake content address differs")
    return {
        "contract": PRIVATE_RESEARCH_INTAKE_CONTRACT,
        "intake_id": intake_id,
        "sha256": _sha_bytes(_canonical(intake)),
    }


def build_source_identity_record(
    *,
    verdict: str,
    observed_at_utc: str,
    shop: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    limitations: Sequence[str],
) -> dict[str, Any]:
    payload = {
        "contract": SOURCE_IDENTITY_CONTRACT,
        "verdict": verdict,
        "observed_at_utc": _utc(observed_at_utc, field="source identity time"),
        "shop": _json_clone(shop),
        "evidence": _json_clone(list(evidence)),
        "evidence_sha256": _sha(list(evidence)),
        "limitations": _json_clone(list(limitations)),
        "identity_id": None,
    }
    payload["identity_id"] = _sha(payload)
    return validate_source_identity_record(payload)


def validate_source_identity_record(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract",
        "verdict",
        "observed_at_utc",
        "shop",
        "evidence",
        "evidence_sha256",
        "limitations",
        "identity_id",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PrivateResearchV2Error("source identity contract differs")
    shop = value.get("shop")
    if not isinstance(shop, Mapping) or set(shop) != {
        "shop_gid",
        "myshopify_domain",
        "primary_domain",
        "currency_code",
        "timezone",
    }:
        raise PrivateResearchV2Error("source identity shop differs")
    if (
        value.get("contract") != SOURCE_IDENTITY_CONTRACT
        or value.get("verdict") not in {"VERIFIED_SAME_SHOP", "DIFFERENT_SHOP", "UNRESOLVED"}
        or not isinstance(shop.get("shop_gid"), str)
        or not _SHOP_GID.fullmatch(shop["shop_gid"])
        or any(
            not isinstance(shop.get(key), str) or not str(shop[key]).strip()
            for key in ("myshopify_domain", "primary_domain", "currency_code", "timezone")
        )
    ):
        raise PrivateResearchV2Error("source identity shop binding differs")
    _utc(value.get("observed_at_utc"), field="source identity time")
    evidence = value.get("evidence")
    if not isinstance(evidence, list):
        raise PrivateResearchV2Error("source identity evidence is absent")
    normalized: list[dict[str, Any]] = []
    kinds: set[str] = set()
    for item in evidence:
        if not isinstance(item, Mapping) or set(item) != {
            "kind",
            "observed_at_utc",
            "reference",
            "reference_sha256",
            "observed_domain",
            "binding_basis",
        }:
            raise PrivateResearchV2Error("source identity evidence differs")
        kind = item.get("kind")
        if (
            not isinstance(kind, str)
            or kind in kinds
            or not isinstance(item.get("reference"), str)
            or not item["reference"].strip()
            or not isinstance(item.get("reference_sha256"), str)
            or not _HEX64.fullmatch(item["reference_sha256"])
            or item.get("observed_domain") not in {
                shop["myshopify_domain"],
                shop["primary_domain"],
            }
            or not isinstance(item.get("binding_basis"), str)
            or not item["binding_basis"].strip()
        ):
            raise PrivateResearchV2Error("source identity evidence binding differs")
        _utc(item.get("observed_at_utc"), field="source evidence time")
        kinds.add(kind)
        normalized.append(dict(item))
    if kinds != REQUIRED_SOURCE_EVIDENCE or value.get("evidence_sha256") != _sha(normalized):
        raise PrivateResearchV2Error("source identity evidence set differs")
    limitations = value.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise PrivateResearchV2Error("source identity limitations differ")
    identity_basis = _json_clone(value)
    supplied = identity_basis["identity_id"]
    identity_basis["identity_id"] = None
    if not isinstance(supplied, str) or not _HEX64.fullmatch(supplied) or supplied != _sha(identity_basis):
        raise PrivateResearchV2Error("source identity content address differs")
    return _json_clone(value)


def _require_registered_source_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Accept only the owner-authorized, independently evidenced shop binding.

    Content addressing by itself is not source authentication.  This registration
    binds the private record produced from the retained connector receipts to the
    implementation reviewed for this bounded research run.
    """

    normalized = validate_source_identity_record(value)
    if (
        normalized["identity_id"] != REGISTERED_SOURCE_IDENTITY_ID
        or normalized["evidence_sha256"] != REGISTERED_SOURCE_EVIDENCE_SHA256
        or normalized["verdict"] != "VERIFIED_SAME_SHOP"
    ):
        raise PrivateResearchV2Error("source identity is not the registered binding")
    return normalized


def read_registered_source_identity(path: str | Path) -> dict[str, Any]:
    _, raw = _private_file(path, field="registered source identity")
    if _sha_bytes(raw) != REGISTERED_SOURCE_IDENTITY_FILE_SHA256:
        raise PrivateResearchV2Error("registered source identity bytes differ")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateResearchV2Error("registered source identity is unreadable") from exc
    return _require_registered_source_identity(value)


def _capture_reference(identity: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    return next(item for item in identity["evidence"] if item["kind"] == kind)


def _capture_rows(
    path: str | Path,
    *,
    identity: Mapping[str, Any],
    kind: str,
    contract: str,
    expected_start: date,
    expected_days: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_path, raw = _private_file(path, field=kind)
    reference = _capture_reference(identity, kind)
    if reference["reference_sha256"] != _sha_bytes(raw):
        raise PrivateResearchV2Error(f"{kind} source reference differs")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateResearchV2Error(f"{kind} source is unreadable") from exc
    top_keys = (
        {"contract", "authority", "approval_state", "source", "days"}
        if contract == EXTENSION_CONTRACT
        else {
            "contract",
            "authority",
            "approval_state",
            "source",
            "days",
            "controls",
            "limitations",
        }
    )
    if (
        not isinstance(payload, Mapping)
        or set(payload) != top_keys
        or payload.get("contract") != contract
    ):
        raise PrivateResearchV2Error(f"{kind} contract differs")
    expected_authority = (
        "PRIVATE_REAL_SOURCE_REVIEW_ONLY"
        if contract == EXTENSION_CONTRACT
        else "PRIVATE_REAL_SOURCE_REVIEW_ONLY_NO_OPERATIONAL_AUTHORITY"
    )
    if (
        payload.get("authority") != expected_authority
        or payload.get("approval_state") != "PROPOSED_UNAPPROVED_REVIEW_ONLY"
    ):
        raise PrivateResearchV2Error(f"{kind} authority differs")
    source = payload.get("source")
    source_keys = (
        {"connector", "shop"}
        if contract == EXTENSION_CONTRACT
        else {
            "connector",
            "shop",
            "query_template",
            "first_complete_business_date",
            "last_complete_business_date",
            "business_days",
            "one_query_per_business_date",
            "population",
            "absent_variant_day_semantics",
        }
    )
    if (
        not isinstance(source, Mapping)
        or set(source) != source_keys
        or source.get("connector") != "ALREADY_CONNECTED_SHOPIFY_READ_ONLY_ANALYTICS"
    ):
        raise PrivateResearchV2Error(f"{kind} connector differs")
    shop = source.get("shop")
    shop_keys = (
        {"domain", "currency_code", "timezone", "country"}
        if contract == EXTENSION_CONTRACT
        else {
            "shop_gid",
            "myshopify_domain",
            "primary_domain",
            "currency_code",
            "timezone",
        }
    )
    if not isinstance(shop, Mapping) or set(shop) != shop_keys:
        raise PrivateResearchV2Error(f"{kind} shop differs")
    domains = {
        shop.get("domain"),
        shop.get("myshopify_domain"),
        shop.get("primary_domain"),
    } - {None}
    if not domains.issubset(
        {identity["shop"]["myshopify_domain"], identity["shop"]["primary_domain"]}
    ):
        raise PrivateResearchV2Error(f"{kind} shop binding differs")
    if shop.get("currency_code") != identity["shop"]["currency_code"]:
        raise PrivateResearchV2Error(f"{kind} currency differs")
    if contract == EXTENSION_CONTRACT:
        if (
            shop.get("domain") != identity["shop"]["primary_domain"]
            or shop.get("timezone") != "EDT"
            or shop.get("country") != "United States"
        ):
            raise PrivateResearchV2Error(f"{kind} shop metadata differs")
    else:
        if (
            shop != identity["shop"]
            or source.get("query_template")
            != _QUERY_PREFIX + "{business_date} UNTIL {business_date}"
            or source.get("first_complete_business_date") != expected_start.isoformat()
            or source.get("last_complete_business_date")
            != (expected_start + timedelta(days=expected_days - 1)).isoformat()
            or source.get("business_days") != expected_days
            or source.get("one_query_per_business_date") is not True
            or source.get("population")
            != "ALL_VARIANT_GROUPS_RETURNED_BY_UNFILTERED_DAILY_SALES_QUERY"
            or source.get("absent_variant_day_semantics")
            != "OBSERVED_ZERO_ONLY_FOR_DAYS_WITH_A_RECORDED_COMPLETE_QUERY_AND_ESTABLISHED_ITEM_SCOPE; NEVER IMPUTE AN UNQUERIED_OR_PRE_EXISTENCE_DAY"
            or not isinstance(payload.get("controls"), Mapping)
            or set(payload["controls"])
            != {
                "day_count",
                "row_count",
                "every_response_success",
                "every_response_same_shop_domain",
                "every_declared_row_count_matches",
            }
            or payload["controls"].get("day_count") != expected_days
            or any(
                payload["controls"].get(key) is not True
                for key in (
                    "every_response_success",
                    "every_response_same_shop_domain",
                    "every_declared_row_count_matches",
                )
            )
            or payload.get("limitations")
            != [
                "READ_ONLY_PRIVATE_DEVELOPMENT_RESEARCH_SOURCE",
                "SEPARATE_REPLACEMENT_CAPTURE_NOT_A_REWRITE_OF_V1",
                "NO_STOCKOUT_OR_INCOMING_EVIDENCE",
                "NO_PRODUCTION_FORECAST_OR_PURCHASING_AUTHORITY",
            ]
        ):
            raise PrivateResearchV2Error(f"{kind} capture metadata differs")
    days = payload.get("days")
    if not isinstance(days, list) or len(days) != expected_days:
        raise PrivateResearchV2Error(f"{kind} day coverage differs")
    normalized_rows: list[dict[str, Any]] = []
    query_digests: list[str] = []
    daily_evidence: list[dict[str, Any]] = []
    for offset, day in enumerate(days):
        if not isinstance(day, Mapping) or set(day) != {
            "business_date",
            "requested_at_utc",
            "received_at_utc",
            "result",
        }:
            raise PrivateResearchV2Error(f"{kind} daily envelope differs")
        expected_date = expected_start + timedelta(days=offset)
        business_date = _iso_date(day.get("business_date"), field="capture business date")
        if business_date != expected_date:
            raise PrivateResearchV2Error(f"{kind} dates are not contiguous")
        requested = _utc(day.get("requested_at_utc"), field="capture request time")
        received = _utc(day.get("received_at_utc"), field="capture receive time")
        requested_instant = datetime.fromisoformat(
            requested.replace("Z", "+00:00")
        )
        received_instant = datetime.fromisoformat(received.replace("Z", "+00:00"))
        if received_instant < requested_instant:
            raise PrivateResearchV2Error(f"{kind} request chronology differs")
        result = day.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "query",
            "columns",
            "rows",
            "rowCount",
            "chartHint",
            "summaryMetric",
            "shopDomain",
        }:
            raise PrivateResearchV2Error(f"{kind} response envelope differs")
        query = result.get("query")
        expected_query = _QUERY_PREFIX + f"{business_date.isoformat()} UNTIL {business_date.isoformat()}"
        if query != expected_query:
            raise PrivateResearchV2Error(f"{kind} query differs")
        query_digests.append(_sha_bytes(query.encode("utf-8")))
        columns = result.get("columns")
        if (
            not isinstance(columns, list)
            or len(columns) != len(_COLUMNS)
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"name", "dataType"}
                for item in columns
            )
            or [(item["name"], item["dataType"]) for item in columns]
            != list(_COLUMNS)
        ):
            raise PrivateResearchV2Error(f"{kind} response columns differ")
        rows = result.get("rows")
        row_count = result.get("rowCount")
        if (
            isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or not isinstance(rows, list)
            or row_count != len(rows)
            or row_count >= 1000
            or result.get("shopDomain") != identity["shop"]["myshopify_domain"]
        ):
            raise PrivateResearchV2Error(f"{kind} response completeness differs")
        daily_evidence.append(
            {
                "business_date": business_date.isoformat(),
                "requested_at_utc": requested,
                "received_at_utc": received,
                "response_sha256": _sha(result),
                "row_count": row_count,
            }
        )
        seen: set[str] = set()
        for index, raw_row in enumerate(rows):
            if not isinstance(raw_row, list) or len(raw_row) != len(_COLUMNS):
                raise PrivateResearchV2Error(f"{kind} source row differs")
            if raw_row[0] != business_date.isoformat():
                raise PrivateResearchV2Error(f"{kind} source row date differs")
            source_variant = str(raw_row[2])
            if source_variant not in {"", "0"} and not _VARIANT_ID.fullmatch(source_variant):
                raise PrivateResearchV2Error(f"{kind} source Variant identity differs")
            if source_variant in seen:
                raise PrivateResearchV2Error(f"{kind} source Variant/date is duplicated")
            seen.add(source_variant)
            source_product = str(raw_row[1])
            if source_product not in {"", "0"} and not _VARIANT_ID.fullmatch(source_product):
                raise PrivateResearchV2Error(f"{kind} source product identity differs")
            for title in raw_row[3:5]:
                if not isinstance(title, str):
                    raise PrivateResearchV2Error(f"{kind} source title differs")
            net_units = _decimal(raw_row[5], field="source net units")
            if net_units != net_units.to_integral_value():
                raise PrivateResearchV2Error("source net units must be integral")
            decimals = [
                _decimal(raw_row[position], field=f"source metric {position}")
                for position in range(6, 11)
            ]
            normalized_rows.append(
                {
                    "source_row_id": _sha(
                        {
                            "capture_sha256": _sha_bytes(raw),
                            "business_date": business_date.isoformat(),
                            "position": index,
                            "raw_row": raw_row,
                        }
                    ),
                    "partition": kind,
                    "business_date": business_date.isoformat(),
                    "source_product_id": source_product,
                    "source_variant_id": source_variant,
                    "source_row_sha256": _sha(raw_row),
                    "net_units": format(net_units, "f"),
                    "gross_sales": format(decimals[0], "f"),
                    "returns": format(decimals[1], "f"),
                    "net_revenue": format(decimals[2], "f"),
                    "historical_cogs": format(decimals[3], "f"),
                    "gross_profit": format(decimals[4], "f"),
                }
            )
    partition = {
        "partition": kind,
        "contract": contract,
        "source_file": (
            "shopify-history-extension-raw.json"
            if kind == "HISTORY_EXTENSION_54D"
            else "shopify-history-replacement-84d-raw.json"
        ),
        "source_sha256": _sha_bytes(raw),
        "source_storage_key": f"{_SOURCE_PREFIX}/{_sha_bytes(raw)}.json",
        "start_date": expected_start.isoformat(),
        "end_date": (expected_start + timedelta(days=expected_days - 1)).isoformat(),
        "complete_day_count": expected_days,
        "raw_row_count": len(normalized_rows),
        "shop_domain": identity["shop"]["myshopify_domain"],
        "currency_code": identity["shop"]["currency_code"],
        "query_sha256": _sha(query_digests),
        "columns_sha256": _sha([{"name": name, "dataType": kind} for name, kind in _COLUMNS]),
        "daily_evidence_count": len(daily_evidence),
        "daily_evidence_sha256": _sha(daily_evidence),
    }
    if (
        contract == REPLACEMENT_CONTRACT
        and payload["controls"].get("row_count") != len(normalized_rows)
    ):
        raise PrivateResearchV2Error(f"{kind} declared row count differs")
    return partition, normalized_rows


def _authority_aliases(
    seed_path: str | Path,
    original_path: str | Path,
    terminal_path: str | Path,
) -> tuple[dict[str, str], set[str], dict[str, Any]]:
    paths = [Path(seed_path), Path(original_path), Path(terminal_path)]
    expected = [SEED_ALIAS_SHA256, ORIGINAL_AUTHORITY_SHA256, TERMINAL_AUTHORITY_SHA256]
    raw = []
    for path, expected_sha in zip(paths, expected):
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PrivateResearchV2Error("historical identity authority is unavailable") from exc
        if _sha_bytes(data) != expected_sha:
            raise PrivateResearchV2Error("historical identity authority bytes differ")
        raw.append(data)
    aliases: dict[str, str] = {}
    approved_self_identities: set[str] = set()
    seed_rows = list(csv.DictReader(io.StringIO(raw[0].decode("utf-8-sig"))))
    if len(seed_rows) != 3301:
        raise PrivateResearchV2Error("seed alias authority count differs")
    for row in seed_rows:
        old_id, target = row.get("old_variant_id", ""), row.get("variant_id", "")
        if not _VARIANT_ID.fullmatch(old_id) or not _VARIANT_ID.fullmatch(target):
            raise PrivateResearchV2Error("seed alias identity differs")
        prior = aliases.get(old_id)
        if prior not in (None, target):
            raise PrivateResearchV2Error("seed alias family conflicts")
        if old_id == target:
            approved_self_identities.add(old_id)
        else:
            aliases[old_id] = target
    original_rows = list(csv.DictReader(io.StringIO(raw[1].decode("utf-8-sig"))))
    terminal_rows = list(csv.DictReader(io.StringIO(raw[2].decode("utf-8-sig"))))
    overrides = {row["source_identity_key"]: row for row in terminal_rows}
    families: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in original_rows:
        source_key = row["source_identity_key"]
        old_id = source_key.split("|", 1)[0]
        if old_id in {"", "0"}:
            continue
        override = overrides.get(source_key)
        action = (
            {
                "MAP_TO_CANONICAL": "MAP",
                "RESTORE_HISTORICAL_IDENTITY": "RESTORE",
                "EXCLUDE_UNATTRIBUTABLE": "EXCLUDE",
            }[override["final_disposition"]]
            if override
            else row["review_disposition"]
        )
        target = (override or row).get("canonical_variant_id", "")
        families[old_id].append((action, target))
    safe: dict[str, str] = {}
    for old_id, outcomes in families.items():
        targets = {target for action, target in outcomes if action == "MAP" and target}
        if len(targets) == 1 and all(action == "MAP" for action, _ in outcomes):
            target = next(iter(targets))
            if target != old_id:
                safe[old_id] = target
    if len(safe) != 56:
        raise PrivateResearchV2Error("terminal safe alias family count differs")
    for old_id, target in safe.items():
        prior = aliases.get(old_id)
        if prior not in (None, target):
            raise PrivateResearchV2Error("approved alias authorities conflict")
        aliases[old_id] = target
    return aliases, approved_self_identities, {
        "seed_aliases_sha256": SEED_ALIAS_SHA256,
        "seed_alias_row_count": len(seed_rows),
        "original_phase4_sha256": ORIGINAL_AUTHORITY_SHA256,
        "original_phase4_row_count": len(original_rows),
        "terminal_phase4_sha256": TERMINAL_AUTHORITY_SHA256,
        "terminal_phase4_row_count": len(terminal_rows),
        "terminal_safe_alias_family_count": len(safe),
        "combined_conflict_free_alias_count": len(aliases),
        "approved_self_identity_count": len(approved_self_identities),
        "source_key_specific_decisions_applied_to_extension": 0,
    }


def _totals(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = list(rows)
    return {
        "row_count": len(values),
        "net_units": format(sum((_decimal(row["net_units"], field="net units") for row in values), Decimal(0)), "f"),
        "net_revenue": format(sum((_decimal(row["net_revenue"], field="net revenue") for row in values), Decimal(0)), "f"),
        "historical_cogs": format(sum((_decimal(row["historical_cogs"], field="historical COGS") for row in values), Decimal(0)), "f"),
    }


def build_private_v2_composite_history(
    base_intake: Mapping[str, Any],
    *,
    extension_capture_path: str | Path,
    replacement_capture_path: str | Path,
    source_identity: Mapping[str, Any],
    seed_alias_path: str | Path,
    original_authority_path: str | Path,
    terminal_authority_path: str | Path,
) -> dict[str, Any]:
    """Validate raw captures and derive an exact-once historical allocation ledger."""

    base_identity = _base_intake_identity(base_intake)
    identity = _require_registered_source_identity(source_identity)
    if identity["verdict"] != "VERIFIED_SAME_SHOP":
        raise PrivateResearchV2Error("real source identity is not verified")
    extension, extension_rows = _capture_rows(
        extension_capture_path,
        identity=identity,
        kind="HISTORY_EXTENSION_54D",
        contract=EXTENSION_CONTRACT,
        expected_start=EXPECTED_START,
        expected_days=54,
    )
    replacement, replacement_rows = _capture_rows(
        replacement_capture_path,
        identity=identity,
        kind="HISTORY_REPLACEMENT_84D",
        contract=REPLACEMENT_CONTRACT,
        expected_start=date(2026, 6, 27),
        expected_days=84,
    )
    if extension["end_date"] != "2026-06-26" or replacement["start_date"] != "2026-06-27":
        raise PrivateResearchV2Error("history partitions are not adjacent")
    current_rows = base_intake.get("variants")
    if not isinstance(current_rows, list):
        raise PrivateResearchV2Error("base intake current Variant rows are absent")
    current_ids = {
        str(row.get("shopify_variant_id"))
        for row in current_rows
        if isinstance(row, Mapping)
        and isinstance(row.get("shopify_variant_id"), str)
        and _VARIANT_ID.fullmatch(str(row.get("shopify_variant_id")))
    }
    if len(current_ids) != len(current_rows):
        raise PrivateResearchV2Error("base intake current Variant identity differs")
    aliases, approved_self_identities, authority = _authority_aliases(
        seed_alias_path, original_authority_path, terminal_authority_path
    )
    raw_rows = extension_rows + replacement_rows
    ledger: list[dict[str, Any]] = []
    disposition_counts: defaultdict[str, int] = defaultdict(int)
    first_supported: dict[str, date] = {}
    by_target_date: dict[tuple[str, str], dict[str, Any]] = {}
    seen_row_ids: set[str] = set()
    for row in raw_rows:
        row_id = row["source_row_id"]
        if row_id in seen_row_ids:
            raise PrivateResearchV2Error("composite source row is duplicated")
        seen_row_ids.add(row_id)
        source_id = row["source_variant_id"]
        if source_id in current_ids:
            disposition, target, authority_basis = "DIRECT_CURRENT", source_id, "EXACT_CURRENT_VARIANT_ID"
        elif source_id in aliases and aliases[source_id] in current_ids:
            disposition, target, authority_basis = "HISTORICALLY_ALLOCATED", aliases[source_id], "APPROVED_CONFLICT_FREE_OLD_ID_ALIAS"
        elif source_id in aliases:
            disposition, target, authority_basis = "ABSENT_CURRENT_TARGET", aliases[source_id], "APPROVED_TARGET_NOT_IN_CURRENT_CAPTURE"
        elif source_id in approved_self_identities:
            disposition, target, authority_basis = "ABSENT_CURRENT_TARGET", source_id, "APPROVED_HISTORICAL_SELF_IDENTITY_NOT_IN_CURRENT_CAPTURE"
        else:
            disposition, target, authority_basis = "QUARANTINED", None, "NO_APPLICABLE_APPROVED_CURRENT_TARGET"
        disposition_counts[disposition] += 1
        ledger_row = {
            "source_row_id": row_id,
            "source_row_sha256": row["source_row_sha256"],
            "partition": row["partition"],
            "business_date": row["business_date"],
            "source_product_id": row["source_product_id"],
            "source_variant_id": source_id,
            "disposition": disposition,
            "target_shopify_variant_id": target,
            "authority_basis": authority_basis,
            "net_units": row["net_units"],
            "net_revenue": row["net_revenue"],
            "historical_cogs": row["historical_cogs"],
        }
        ledger.append(ledger_row)
        if disposition not in {"DIRECT_CURRENT", "HISTORICALLY_ALLOCATED"}:
            continue
        row_date = date.fromisoformat(row["business_date"])
        first_supported[target] = min(first_supported.get(target, row_date), row_date)
        key = (target, row["business_date"])
        aggregate = by_target_date.setdefault(
            key,
            {
                "net_units": Decimal(0),
                "net_revenue": Decimal(0),
                "historical_cogs": Decimal(0),
                "source_row_count": 0,
            },
        )
        for metric in ("net_units", "net_revenue", "historical_cogs"):
            aggregate[metric] += _decimal(row[metric], field=metric)
        aggregate["source_row_count"] += 1
    if len(ledger) != len(raw_rows):
        raise PrivateResearchV2Error("allocation ledger is not exact-once")
    eligible_ids = sorted(
        (variant_id for variant_id, first in first_supported.items() if first == EXPECTED_START),
        key=lambda item: (len(item), item),
    )
    preexistence_unproven = sorted(set(first_supported) - set(eligible_ids), key=lambda item: (len(item), item))
    observations: list[dict[str, Any]] = []
    for variant_id in eligible_ids:
        daily = []
        for offset in range(138):
            day = EXPECTED_START + timedelta(days=offset)
            aggregate = by_target_date.get((variant_id, day.isoformat()))
            daily.append(
                {
                    "business_date": day.isoformat(),
                    "net_units": format(aggregate["net_units"], "f") if aggregate else "0",
                    "net_revenue": format(aggregate["net_revenue"], "f") if aggregate else "0",
                    "historical_cogs": format(aggregate["historical_cogs"], "f") if aggregate else "0",
                    "source_row_count": aggregate["source_row_count"] if aggregate else 0,
                    "inventory_state": "UNKNOWN",
                    "observed_zero_basis": None if aggregate else "ATTESTED_COMPLETE_QUERY_AND_ITEM_EXISTENCE_SCOPE",
                }
            )
        observations.append(
            {
                "shopify_variant_id": variant_id,
                "existence_basis": "ALLOCATED_SOURCE_ROW_PRESENT_ON_2026_05_04",
                "observations": daily,
                "observations_sha256": _sha(daily),
            }
        )
    raw_totals = _totals(raw_rows)
    ledger_totals = _totals(ledger)
    if raw_totals != ledger_totals:
        raise PrivateResearchV2Error("allocation ledger signed totals differ")
    controls = {
        "raw_row_count": len(raw_rows),
        "direct_current_row_count": disposition_counts["DIRECT_CURRENT"],
        "historically_allocated_row_count": disposition_counts["HISTORICALLY_ALLOCATED"],
        "absent_current_target_row_count": disposition_counts["ABSENT_CURRENT_TARGET"],
        "quarantined_row_count": disposition_counts["QUARANTINED"],
        "eligible_variant_count": len(eligible_ids),
        "preexistence_unproven_variant_count": len(preexistence_unproven),
        "raw_signed_totals": raw_totals,
        "ledger_signed_totals": ledger_totals,
        "ledger_sha256": _sha(ledger),
    }
    payload = {
        "contract": COMPOSITE_HISTORY_CONTRACT,
        "composite_id": None,
        "base_intake": base_identity,
        "source_identity_id": identity["identity_id"],
        "start_date": EXPECTED_START.isoformat(),
        "end_date": EXPECTED_END.isoformat(),
        "complete_day_count": 138,
        "partitions": [extension, replacement],
        "identity_authority": authority,
        "allocation_controls": controls,
        "allocation_ledger": ledger,
        "preexistence_unproven_variant_ids": preexistence_unproven,
        "variants": observations,
    }
    payload["composite_id"] = _sha(payload)
    return _seal_source_verified(
        validate_private_v2_composite_history(payload),
        proof=_COMPOSITE_SOURCE_PROOF,
    )


def validate_private_v2_composite_history(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract",
        "composite_id",
        "base_intake",
        "source_identity_id",
        "start_date",
        "end_date",
        "complete_day_count",
        "partitions",
        "identity_authority",
        "allocation_controls",
        "allocation_ledger",
        "preexistence_unproven_variant_ids",
        "variants",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PrivateResearchV2Error("composite history contract differs")
    if (
        value.get("contract") != COMPOSITE_HISTORY_CONTRACT
        or value.get("start_date") != EXPECTED_START.isoformat()
        or value.get("end_date") != EXPECTED_END.isoformat()
        or value.get("complete_day_count") != 138
        or not isinstance(value.get("source_identity_id"), str)
        or not _HEX64.fullmatch(value["source_identity_id"])
    ):
        raise PrivateResearchV2Error("composite history boundary differs")
    partitions = value.get("partitions")
    partition_keys = {
        "partition",
        "contract",
        "source_file",
        "source_sha256",
        "source_storage_key",
        "start_date",
        "end_date",
        "complete_day_count",
        "raw_row_count",
        "shop_domain",
        "currency_code",
        "query_sha256",
        "columns_sha256",
        "daily_evidence_count",
        "daily_evidence_sha256",
    }
    if (
        not isinstance(partitions, list)
        or len(partitions) != 2
        or any(not isinstance(item, Mapping) or set(item) != partition_keys for item in partitions)
        or [item.get("partition") for item in partitions if isinstance(item, Mapping)]
        != ["HISTORY_EXTENSION_54D", "HISTORY_REPLACEMENT_84D"]
        or partitions[0].get("end_date") != "2026-06-26"
        or partitions[1].get("start_date") != "2026-06-27"
        or any(
            not isinstance(item.get("source_sha256"), str)
            or not _HEX64.fullmatch(item["source_sha256"])
            or item.get("source_storage_key")
            != f"{_SOURCE_PREFIX}/{item['source_sha256']}.json"
            or item.get("daily_evidence_count") != item.get("complete_day_count")
            or not isinstance(item.get("daily_evidence_sha256"), str)
            or not _HEX64.fullmatch(item["daily_evidence_sha256"])
            for item in partitions
        )
    ):
        raise PrivateResearchV2Error("composite history partitions differ")
    controls = value.get("allocation_controls")
    ledger = value.get("allocation_ledger")
    variants = value.get("variants")
    if not isinstance(controls, Mapping) or not isinstance(ledger, list) or not isinstance(variants, list):
        raise PrivateResearchV2Error("composite history allocation differs")
    if controls.get("raw_row_count") != len(ledger) or controls.get("ledger_sha256") != _sha(ledger):
        raise PrivateResearchV2Error("composite allocation ledger differs")
    disposition_sum = sum(
        controls.get(key, -1)
        for key in (
            "direct_current_row_count",
            "historically_allocated_row_count",
            "absent_current_target_row_count",
            "quarantined_row_count",
        )
    )
    if disposition_sum != len(ledger) or controls.get("raw_signed_totals") != controls.get("ledger_signed_totals"):
        raise PrivateResearchV2Error("composite allocation controls differ")
    expected_dates = [(EXPECTED_START + timedelta(days=index)).isoformat() for index in range(138)]
    seen: set[str] = set()
    for row in variants:
        if not isinstance(row, Mapping) or set(row) != {
            "shopify_variant_id",
            "existence_basis",
            "observations",
            "observations_sha256",
        }:
            raise PrivateResearchV2Error("composite Variant history differs")
        variant_id = row.get("shopify_variant_id")
        observations = row.get("observations")
        if (
            not isinstance(variant_id, str)
            or not _VARIANT_ID.fullmatch(variant_id)
            or variant_id in seen
            or row.get("existence_basis") != "ALLOCATED_SOURCE_ROW_PRESENT_ON_2026_05_04"
            or not isinstance(observations, list)
            or len(observations) != 138
            or row.get("observations_sha256") != _sha(observations)
            or [item.get("business_date") for item in observations if isinstance(item, Mapping)] != expected_dates
        ):
            raise PrivateResearchV2Error("composite Variant identity or coverage differs")
        seen.add(variant_id)
        for item in observations:
            if not isinstance(item, Mapping) or set(item) != {
                "business_date",
                "net_units",
                "net_revenue",
                "historical_cogs",
                "source_row_count",
                "inventory_state",
                "observed_zero_basis",
            }:
                raise PrivateResearchV2Error("composite observation differs")
            for metric in ("net_units", "net_revenue", "historical_cogs"):
                _decimal(item[metric], field=f"observation {metric}")
            count = item.get("source_row_count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise PrivateResearchV2Error("composite observation row count differs")
            if (
                item.get("inventory_state") != "UNKNOWN"
                or (count == 0) != (item.get("observed_zero_basis") == "ATTESTED_COMPLETE_QUERY_AND_ITEM_EXISTENCE_SCOPE")
                or (count > 0 and item.get("observed_zero_basis") is not None)
            ):
                raise PrivateResearchV2Error("composite observation semantics differ")
    if controls.get("eligible_variant_count") != len(variants):
        raise PrivateResearchV2Error("composite eligible Variant count differs")
    basis = _json_clone(value)
    supplied = basis["composite_id"]
    basis["composite_id"] = None
    if not isinstance(supplied, str) or not _HEX64.fullmatch(supplied) or supplied != _sha(basis):
        raise PrivateResearchV2Error("composite history content address differs")
    return _json_clone(value)


def _scenarios(history_end: date) -> list[dict[str, Any]]:
    origin = history_end + timedelta(days=1)
    return [
        {
            "contract": SCENARIO_CONTRACT,
            "scenario_id": f"H{horizon}",
            "horizon_days": horizon,
            "basis": "RESEARCH_SCENARIO_ASSUMPTION_NOT_VENDOR_SCHEDULE",
            "history_end": history_end.isoformat(),
            "forecast_origin": origin.isoformat(),
            "target_start": origin.isoformat(),
            "target_end_exclusive": (origin + timedelta(days=horizon)).isoformat(),
            "real_supplier_confirmation": False,
            "commercial_authority": False,
        }
        for horizon in SCENARIO_HORIZONS
    ]


def build_private_v2_research_input(
    base_intake: Mapping[str, Any],
    *,
    composite_history: Mapping[str, Any],
    source_identity: Mapping[str, Any],
) -> dict[str, Any]:
    base_identity = _base_intake_identity(base_intake)
    identity = _require_registered_source_identity(source_identity)
    composite = validate_private_v2_composite_history(
        _source_verified(
            composite_history,
            proof=_COMPOSITE_SOURCE_PROOF,
            field="composite history",
        )
    )
    if (
        identity["verdict"] != "VERIFIED_SAME_SHOP"
        or composite["source_identity_id"] != identity["identity_id"]
        or composite["base_intake"] != base_identity
    ):
        raise PrivateResearchV2Error("V2 research source binding differs")
    variants = [
        {
            "shopify_variant_id": row["shopify_variant_id"],
            "existence_basis": row["existence_basis"],
            "observations": row["observations"],
            "observations_sha256": row["observations_sha256"],
        }
        for row in composite["variants"]
    ]
    payload = {
        "contract": INPUT_CONTRACT,
        "data_mode": DATA_MODE,
        "authority": dict(INTAKE_AUTHORITY),
        "input_id": None,
        "base_intake": base_identity,
        "source_identity": identity,
        "policy": _registered_policy_identity(),
        "history": {
            "contract": COMPOSITE_HISTORY_CONTRACT,
            "composite_id": composite["composite_id"],
            "start_date": EXPECTED_START.isoformat(),
            "end_date": EXPECTED_END.isoformat(),
            "complete_day_count": 138,
            "availability_basis": "UNKNOWN_NOT_OBSERVED",
            "signed_sales_transform": "ENGINE_MAX_NET_UNITS_ZERO_WITH_NEGATIVE_DAY_EVIDENCE_RETAINED",
            "partitions": composite["partitions"],
            "identity_authority": composite["identity_authority"],
            "allocation_controls": composite["allocation_controls"],
            "allocation_ledger": composite["allocation_ledger"],
            "preexistence_unproven_variant_ids": composite["preexistence_unproven_variant_ids"],
        },
        "scenarios": _scenarios(EXPECTED_END),
        "variants": variants,
        "limitations": sorted(REQUIRED_LIMITATIONS),
        "zero_authority": dict(ZERO_AUTHORITY),
    }
    payload["input_id"] = _sha(payload)
    return _seal_source_verified(
        validate_private_v2_research_input(payload),
        proof=_INPUT_SOURCE_PROOF,
    )


def validate_private_v2_research_input(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract",
        "data_mode",
        "authority",
        "input_id",
        "base_intake",
        "source_identity",
        "policy",
        "history",
        "scenarios",
        "variants",
        "limitations",
        "zero_authority",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PrivateResearchV2Error("V2 research input key inventory differs")
    if (
        value.get("contract") != INPUT_CONTRACT
        or value.get("data_mode") != DATA_MODE
        or value.get("authority") != INTAKE_AUTHORITY
        or value.get("zero_authority") != ZERO_AUTHORITY
        or value.get("policy") != _registered_policy_identity()
        or value.get("limitations") != sorted(REQUIRED_LIMITATIONS)
    ):
        raise PrivateResearchV2Error("V2 research input contract differs")
    identity = _require_registered_source_identity(value.get("source_identity"))
    if identity["verdict"] != "VERIFIED_SAME_SHOP":
        raise PrivateResearchV2Error("V2 research source is not authenticated")
    base = value.get("base_intake")
    if (
        not isinstance(base, Mapping)
        or set(base) != {"contract", "intake_id", "sha256"}
        or base.get("contract") != PRIVATE_RESEARCH_INTAKE_CONTRACT
        or not all(isinstance(base.get(key), str) and _HEX64.fullmatch(base[key]) for key in ("intake_id", "sha256"))
    ):
        raise PrivateResearchV2Error("V2 base intake identity differs")
    history = value.get("history")
    required_history = {
        "contract",
        "composite_id",
        "start_date",
        "end_date",
        "complete_day_count",
        "availability_basis",
        "signed_sales_transform",
        "partitions",
        "identity_authority",
        "allocation_controls",
        "allocation_ledger",
        "preexistence_unproven_variant_ids",
    }
    if not isinstance(history, Mapping) or set(history) != required_history:
        raise PrivateResearchV2Error("V2 research history contract differs")
    if (
        history.get("contract") != COMPOSITE_HISTORY_CONTRACT
        or history.get("start_date") != EXPECTED_START.isoformat()
        or history.get("end_date") != EXPECTED_END.isoformat()
        or history.get("complete_day_count") != 138
        or history.get("availability_basis") != "UNKNOWN_NOT_OBSERVED"
        or history.get("signed_sales_transform") != "ENGINE_MAX_NET_UNITS_ZERO_WITH_NEGATIVE_DAY_EVIDENCE_RETAINED"
        or not isinstance(history.get("composite_id"), str)
        or not _HEX64.fullmatch(history["composite_id"])
    ):
        raise PrivateResearchV2Error("V2 research history boundary differs")
    references = {
        item["kind"]: item["reference_sha256"] for item in identity["evidence"]
    }
    partitions = history.get("partitions")
    if (
        not isinstance(partitions, list)
        or len(partitions) != 2
        or partitions[0].get("source_sha256")
        != references["HISTORY_EXTENSION_54D"]
        or partitions[1].get("source_sha256")
        != references["HISTORY_REPLACEMENT_84D"]
    ):
        raise PrivateResearchV2Error("V2 research capture binding differs")
    if value.get("scenarios") != _scenarios(EXPECTED_END):
        raise PrivateResearchV2Error("V2 research scenarios differ")
    rows = value.get("variants")
    if not isinstance(rows, list) or not rows:
        raise PrivateResearchV2Error("V2 research Variant observations are absent")
    expected_dates = [(EXPECTED_START + timedelta(days=index)).isoformat() for index in range(138)]
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "shopify_variant_id",
            "existence_basis",
            "observations",
            "observations_sha256",
        }:
            raise PrivateResearchV2Error("V2 research Variant row differs")
        variant_id = row.get("shopify_variant_id")
        observations = row.get("observations")
        if (
            not isinstance(variant_id, str)
            or not _VARIANT_ID.fullmatch(variant_id)
            or variant_id in seen
            or row.get("existence_basis") != "ALLOCATED_SOURCE_ROW_PRESENT_ON_2026_05_04"
            or not isinstance(observations, list)
            or len(observations) != 138
            or row.get("observations_sha256") != _sha(observations)
            or [item.get("business_date") for item in observations if isinstance(item, Mapping)] != expected_dates
        ):
            raise PrivateResearchV2Error("V2 research Variant identity differs")
        seen.add(variant_id)
        for item in observations:
            if (
                not isinstance(item, Mapping)
                or item.get("inventory_state") != "UNKNOWN"
                or set(item) != {
                    "business_date",
                    "net_units",
                    "net_revenue",
                    "historical_cogs",
                    "source_row_count",
                    "inventory_state",
                    "observed_zero_basis",
                }
            ):
                raise PrivateResearchV2Error("V2 research observation differs")
            for metric in ("net_units", "net_revenue", "historical_cogs"):
                _decimal(item[metric], field=f"observation {metric}")
    controls = history.get("allocation_controls")
    ledger = history.get("allocation_ledger")
    if (
        not isinstance(controls, Mapping)
        or not isinstance(ledger, list)
        or controls.get("raw_row_count") != len(ledger)
        or controls.get("ledger_sha256") != _sha(ledger)
        or controls.get("eligible_variant_count") != len(rows)
    ):
        raise PrivateResearchV2Error("V2 research allocation binding differs")
    identity_basis = _json_clone(value)
    supplied = identity_basis["input_id"]
    identity_basis["input_id"] = None
    if not isinstance(supplied, str) or not _HEX64.fullmatch(supplied) or supplied != _sha(identity_basis):
        raise PrivateResearchV2Error("V2 research input content address differs")
    return _json_clone(value)


def build_private_v2_research_projection(
    value: Mapping[str, Any], base_intake: Mapping[str, Any]
) -> dict[str, Any]:
    research_input = validate_private_v2_research_input(
        _source_verified(
            value,
            proof=_INPUT_SOURCE_PROOF,
            field="V2 research input",
        )
    )
    if research_input["base_intake"] != _base_intake_identity(base_intake):
        raise PrivateResearchV2Error("V2 research input is bound to another base intake")
    policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
    sidecars: dict[str, Any] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for item in research_input["variants"]:
        variant_id = item["shopify_variant_id"]
        observations = tuple(
            DemandObservation(date.fromisoformat(row["business_date"]), Decimal(row["net_units"]), "UNKNOWN")
            for row in item["observations"]
        )
        scenario_summaries: dict[str, Any] = {}
        for scenario in research_input["scenarios"]:
            scenario_id = scenario["scenario_id"]
            calendar = {
                "basis": "CALLER_VALIDATED_HORIZON",
                "horizon_days": scenario["horizon_days"],
            }
            try:
                plan = plan_development_forecast(
                    observations,
                    horizon_days=scenario["horizon_days"],
                    protection_calendar=calendar,
                    policy=policy,
                )
            except DevelopmentForecastError as exc:
                scenario_summaries[scenario_id] = {
                    "status": "BLOCKED_RESEARCH_EVALUATION",
                    "calculator_status": "REFUSED",
                    "selected_model": None,
                    "point_forecast_units": None,
                    "protection_units": None,
                    "protection_status": None,
                    "target_units": None,
                    "confidence": "LOW",
                    "reason_codes": [f"FORECAST_EVIDENCE_REJECTED:{type(exc).__name__}"],
                    "evidence_sha256": None,
                    "target_start": scenario["target_start"],
                    "target_end_exclusive": scenario["target_end_exclusive"],
                }
                continue
            evidence = plan.to_json_dict()
            calculated = plan.status in {"READY", "NO_ORDER"}
            summary = {
                "status": CALCULATED_RESEARCH_ONLY if calculated else "BLOCKED_RESEARCH_EVALUATION",
                "calculator_status": plan.status,
                "selected_model": plan.selected_model,
                "point_forecast_units": evidence.get("point_forecast_units"),
                "protection_units": evidence.get("protection_units"),
                "protection_status": (evidence.get("protection") or {}).get("status") if isinstance(evidence.get("protection"), Mapping) else None,
                "target_units": evidence.get("target_units"),
                "confidence": plan.confidence,
                "reason_codes": list(plan.reason_codes),
                "evidence_sha256": plan.evidence_sha256,
                "target_start": scenario["target_start"],
                "target_end_exclusive": scenario["target_end_exclusive"],
            }
            scenario_summaries[scenario_id] = summary
            sidecar_key = ":".join(
                (
                    variant_id,
                    scenario_id,
                    research_input["input_id"],
                    research_input["policy"]["policy_canonical_sha256"],
                )
            )
            sidecar = {
                "variant_id": variant_id,
                "scenario": scenario,
                "input_id": research_input["input_id"],
                "policy": research_input["policy"],
                "forecast_evidence": evidence,
            }
            sidecar["sidecar_sha256"] = _sha(sidecar)
            sidecars[sidecar_key] = sidecar
        summaries[variant_id] = scenario_summaries

    base = deepcopy(build_private_research_projection(base_intake))
    base.pop("projection_sha256", None)
    base_owner_by_id = {
        row["shopify_variant_id"]: row for row in base["owner_worksheet"]
    }
    input_variant_by_id = {
        row["shopify_variant_id"]: row for row in research_input["variants"]
    }
    base["contract"] = PROJECTION_CONTRACT
    base["data_mode"] = DATA_MODE
    base["intake"] = {
        "contract": INPUT_CONTRACT,
        "data_mode": DATA_MODE,
        "intake_id": research_input["input_id"],
        "base_intake_id": research_input["base_intake"]["intake_id"],
    }
    base["forecast_research"] = {
        "input_id": research_input["input_id"],
        "policy": research_input["policy"],
        "source_identity": research_input["source_identity"],
        "history": research_input["history"],
        "variant_observations_sha256": {
            variant_id: row["observations_sha256"]
            for variant_id, row in sorted(input_variant_by_id.items())
        },
        "scenarios": research_input["scenarios"],
        "sidecars_sha256": _sha(sidecars),
    }
    base["forecast_sidecars"] = sidecars
    counts = {
        scenario: {"calculated": 0, "blocked": 0, "missing": 0, "unprocessed": 0, "numerical_zero": 0}
        for scenario in ("H3", "H10", "H17")
    }
    coverage_by_id = {row["shopify_variant_id"]: row for row in base["coverage_rows"]}
    preexistence_unproven = set(
        research_input["history"]["preexistence_unproven_variant_ids"]
    )
    sidecar_keys_by_variant: defaultdict[str, list[str]] = defaultdict(list)
    for key in sidecars:
        sidecar_keys_by_variant[key.split(":", 1)[0]].append(key)
    scenario_results_by_variant: dict[str, dict[str, Any]] = {}
    for variant_id, row in coverage_by_id.items():
        row["abc_status"] = REAL_NUMERICAL_EVALUATION_NOT_RUN
        if variant_id in summaries:
            statuses = summaries[variant_id]
            row["forecast_status"] = (
                CALCULATED_RESEARCH_ONLY
                if all(item["status"] == CALCULATED_RESEARCH_ONLY for item in statuses.values())
                else "PARTIALLY_BLOCKED_RESEARCH_EVALUATION"
            )
            scenario_results_by_variant[variant_id] = statuses
            reasons = sorted({reason for result in statuses.values() for reason in result["reason_codes"]})
            row["missing_data_reasons"] = sorted(
                {reason for reason in row["missing_data_reasons"] if not str(reason).startswith("FORECAST_")}.union(reasons)
            )
            for scenario_id, result in statuses.items():
                bucket = "calculated" if result["status"] == CALCULATED_RESEARCH_ONLY else "blocked"
                counts[scenario_id][bucket] += 1
                if result.get("point_forecast_units") is not None and Decimal(str(result["point_forecast_units"])) == 0:
                    counts[scenario_id]["numerical_zero"] += 1
        else:
            missing_reason = (
                "PREEXISTENCE_NOT_ESTABLISHED_FOR_FULL_138_DAY_WINDOW"
                if variant_id in preexistence_unproven
                else "NO_SUPPORTED_SOURCE_OBSERVATIONS_IN_138_DAY_WINDOW"
            )
            row["forecast_status"] = REAL_NUMERICAL_EVALUATION_NOT_RUN
            scenario_results_by_variant[variant_id] = {
                key: {
                    "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
                    "reason_codes": [missing_reason],
                }
                for key in ("H3", "H10", "H17")
            }
            row["missing_data_reasons"] = sorted(
                set(row["missing_data_reasons"]) | {missing_reason}
            )
            for key in counts:
                counts[key]["missing"] += 1
        row["forecast_sidecar_keys"] = sorted(
            sidecar_keys_by_variant.get(variant_id, [])
        )
    for row in base["research_rows"]:
        variant_id = row["shopify_variant_id"]
        scenarios = scenario_results_by_variant[variant_id]
        row["forecast"] = {
            "status": coverage_by_id[variant_id]["forecast_status"],
            "reason_codes": sorted({reason for result in scenarios.values() for reason in result.get("reason_codes", [])}),
            "sidecar_keys": sorted(sidecar_keys_by_variant.get(variant_id, [])),
        }
        row["abc"] = {
            "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
            "abc_class": None,
            "authority": AUTHORITY_LABEL,
            "reason_codes": ["V2_ABC_STAGE_NOT_EVALUATED"],
        }
        row["economics"] = {
            "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
            "calculation_scope": "NOT_EVALUATED",
            "authority": AUTHORITY_LABEL,
            "commercial_authority": False,
            "reason_codes": ["V2_ECONOMICS_STAGE_NOT_EVALUATED"],
        }
    base["shared_stage_blockers"] = {
        "ABC": "EXACT_COHORT_AND_COST_AUTHORITY_REMAINS_INCOMPLETE",
        "NET_NEED": "TRUSTED_INCOMING_OPEN_ORDERS_AND_OPERATIONAL_POLICY_NOT_SUPPLIED",
        "CASE_QUANTITY": "APPROVED_PACK_NOT_SUPPLIED",
        "ECONOMICS": "APPROVED_SELECTED_PRICE_MARGIN_AND_FEES_NOT_SUPPLIED",
        "ORDER": "PRODUCTION_AND_ORDER_AUTHORITY_NOT_GRANTED",
    }
    base["owner_worksheet"] = [
        {
            "authority": AUTHORITY_LABEL,
            "research_only": True,
            "shopify_variant_id": row["shopify_variant_id"],
            "product_title": row["product_title"],
            "variant_title": row["variant_title"],
            "supplier_names": deepcopy(
                base_owner_by_id[row["shopify_variant_id"]]["supplier_names"]
            ),
            "question": "",
            "reason_codes": row["missing_data_reasons"],
            "recorded_sales_coverage": {
                "start_date": research_input["history"]["start_date"],
                "end_date": research_input["history"]["end_date"],
                "complete_day_count": research_input["history"][
                    "complete_day_count"
                ],
                "availability_basis": research_input["history"][
                    "availability_basis"
                ],
                "status": (
                    "COMPLETE_138_DAY_RESEARCH_HISTORY"
                    if row["shopify_variant_id"] in input_variant_by_id
                    else "NO_SUPPORTED_FULL_WINDOW_HISTORY"
                ),
                "observations_sha256": (
                    input_variant_by_id[row["shopify_variant_id"]][
                        "observations_sha256"
                    ]
                    if row["shopify_variant_id"] in input_variant_by_id
                    else None
                ),
            },
            "captured_stock_provenance": {
                "status": "POINT_IN_TIME_CAPTURE_ONLY_NOT_DAILY_AVAILABILITY",
                "available": row["available"],
                "on_hand": row["on_hand"],
                "committed": row["committed"],
                "trusted_incoming": row["incoming"],
                "raw_incoming": row["raw_incoming"],
                "raw_incoming_trust": row["raw_incoming_trust"],
                "raw_incoming_operational_use": row[
                    "raw_incoming_operational_use"
                ],
                "inventory_evidence": row["inventory_evidence"],
            },
            "scenario_results": scenario_results_by_variant[
                row["shopify_variant_id"]
            ],
            "sidecar_keys": sorted(
                sidecar_keys_by_variant.get(row["shopify_variant_id"], [])
            ),
            "stage_status": {
                "CAPTURE": "SUPPORTED" if row["shopify_variant_id"] in summaries else "BLOCKED:CAPTURE",
                "IDENTITY": "SUPPORTED_CURRENT_OR_APPROVED_HISTORICAL_IDENTITY",
                "FORECAST": row["forecast_status"],
                "ABC": "BLOCKED:ABC",
                "NET_NEED": "BLOCKED:NET_NEED",
                "CASE_QUANTITY": "BLOCKED:CASE_QUANTITY",
                "ECONOMICS": "BLOCKED:ECONOMICS",
                "ORDER": "BLOCKED:ORDER",
            },
            "stage_blocker_refs": [
                "ABC",
                "NET_NEED",
                "CASE_QUANTITY",
                "ECONOMICS",
                "ORDER",
            ],
            "owner_response": "",
            "operational_effect": "NONE",
        }
        for row in base["coverage_rows"]
    ]
    base["coverage_summary"]["forecast_calculated_research_only_count"] = sum(
        row["forecast_status"] == CALCULATED_RESEARCH_ONLY for row in base["coverage_rows"]
    )
    base["coverage_summary"]["abc_calculated_research_only_count"] = 0
    base["coverage_summary"]["economics_calculated_research_only_count"] = 0
    base["abc_evaluation"] = {
        "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
        "authority": AUTHORITY_LABEL,
        "basis": "V2_STAGE_NOT_EVALUATED",
        "reason_codes": ["EXACT_COHORT_AND_COST_AUTHORITY_REMAINS_INCOMPLETE"],
    }
    base["forecast_research_counts"] = counts
    base["limitations"] = sorted(set(base["limitations"]) | set(research_input["limitations"]))
    normalized = _json_clone(base)
    normalized["projection_sha256"] = _sha_bytes(_projection_canonical(normalized))
    if private_research_projection_sha256(normalized) != normalized["projection_sha256"]:
        raise PrivateResearchV2Error("private V2 projection validation differs")
    return normalized


def input_manifest_key(input_id: str) -> str:
    if not isinstance(input_id, str) or not _HEX64.fullmatch(input_id):
        raise PrivateResearchV2Error("V2 research input ID differs")
    return f"{_INPUT_PREFIX}/{input_id}.json"


def _authority_paths() -> tuple[Path, Path, Path]:
    repository = Path(__file__).resolve().parents[3]
    return (
        repository / "procurement" / "seed" / "variant_aliases.csv",
        repository / "procurement" / "review" / "phase4_identity_manifest_corrected.csv",
        repository / "procurement" / "review" / "phase4_terminal_disposition_manifest.csv",
    )


def _ensure_private_parent(root: Path, key: str) -> None:
    parent = (root / key).parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = parent
    while current != root:
        current.chmod(0o700)
        current = current.parent


def _replay_private_v2_input(
    root: Path, value: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = validate_private_v2_research_input(value)
    partitions = normalized["history"]["partitions"]
    storage = LocalFilesystemStorage(root)
    source_paths: dict[str, Path] = {}
    for partition in partitions:
        key = partition["source_storage_key"]
        raw = storage.get_bytes(key)
        if _sha_bytes(raw) != partition["source_sha256"]:
            raise PrivateResearchV2Error("immutable V2 source capture differs")
        source_paths[partition["partition"]] = root / key
    try:
        base_intake = read_private_research_intake(
            root, normalized["base_intake"]["intake_id"]
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchV2Error("V2 base intake semantic replay failed") from exc
    seed, original, terminal = _authority_paths()
    composite = build_private_v2_composite_history(
        base_intake,
        extension_capture_path=source_paths["HISTORY_EXTENSION_54D"],
        replacement_capture_path=source_paths["HISTORY_REPLACEMENT_84D"],
        source_identity=normalized["source_identity"],
        seed_alias_path=seed,
        original_authority_path=original,
        terminal_authority_path=terminal,
    )
    expected = build_private_v2_research_input(
        base_intake,
        composite_history=composite,
        source_identity=normalized["source_identity"],
    )
    if expected != normalized:
        raise PrivateResearchV2Error("V2 research input semantic replay differs")
    return expected, base_intake


def write_private_v2_research_input(
    private_root: str | Path,
    value: Mapping[str, Any],
    *,
    source_capture_paths: Mapping[str, str | Path],
) -> dict[str, Any]:
    root = validate_private_root(Path(private_root))
    normalized = validate_private_v2_research_input(
        _source_verified(
            value,
            proof=_INPUT_SOURCE_PROOF,
            field="V2 research input",
        )
    )
    storage = LocalFilesystemStorage(root)
    if set(source_capture_paths) != {
        "HISTORY_EXTENSION_54D",
        "HISTORY_REPLACEMENT_84D",
    }:
        raise PrivateResearchV2Error("V2 source capture inventory differs")
    for partition in normalized["history"]["partitions"]:
        kind = partition["partition"]
        _, raw = _private_file(source_capture_paths[kind], field=kind)
        if _sha_bytes(raw) != partition["source_sha256"]:
            raise PrivateResearchV2Error("V2 source capture bytes differ")
        key = partition["source_storage_key"]
        _ensure_private_parent(root, key)
        if storage.exists(key):
            if storage.get_bytes(key) != raw:
                raise PrivateResearchV2Error("immutable V2 source capture differs")
        else:
            storage.put_bytes_once(key, raw)
    data = _canonical(normalized)
    key = input_manifest_key(normalized["input_id"])
    _ensure_private_parent(root, key)
    if storage.exists(key):
        if storage.get_bytes(key) != data:
            raise PrivateResearchV2Error("immutable V2 research input differs")
    else:
        storage.put_bytes_once(key, data)
    return read_private_v2_research_input(root, normalized["input_id"])


def read_private_v2_research_input(private_root: str | Path, input_id: str) -> dict[str, Any]:
    return read_private_v2_research_bundle(private_root, input_id)[0]


def read_private_v2_research_bundle(
    private_root: str | Path, input_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Semantically replay one V2 input and return it with its verified V1 base."""

    root = validate_private_root(Path(private_root))
    data = LocalFilesystemStorage(root).get_bytes(input_manifest_key(input_id))
    try:
        value = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateResearchV2Error("V2 research input is unreadable") from exc
    normalized, base_intake = _replay_private_v2_input(root, value)
    if data != _canonical(normalized):
        raise PrivateResearchV2Error("V2 research input is not canonical")
    return normalized, base_intake

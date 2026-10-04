"""Additive corrected-V3 private research contracts and semantic replay.

The accepted V3 input, projection, workspace, and artifacts are immutable
parents.  This module binds them, ingests the reviewed exact-ID creation delta,
and publishes a compact coherent-horizon child with no operational authority.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Mapping, Sequence
import weakref
from zoneinfo import ZoneInfo

from .development_forecast_joint_horizon import (
    EVIDENCE_CONTRACT as JOINT_EVIDENCE_CONTRACT,
    POLICY_CANONICAL_SHA256 as JOINT_POLICY_CANONICAL_SHA256,
    POLICY_CONTRACT as JOINT_POLICY_CONTRACT,
    POLICY_SOURCE_SHA256 as JOINT_POLICY_SOURCE_SHA256,
    JointHorizonForecastError,
    _context as _joint_decimal_context,
    joint_horizon_policy_descriptor,
    plan_joint_horizon_forecast,
    validate_joint_horizon_forecast_evidence,
)
from .forecasting import DemandObservation
from .private_research_intake import canonical_json_bytes, validate_private_root
from .private_research_v2 import (
    INPUT_CONTRACT as PARENT_V2_INPUT_CONTRACT,
    PrivateResearchV2Error,
    _json_clone as _v2_json_clone,
    _projection_canonical,
    _sha,
    _sha_bytes,
    validate_private_v2_research_input,
)
from .private_research_v3 import (
    INPUT_CONTRACT as PARENT_V3_INPUT_CONTRACT,
    PROJECTION_CONTRACT as PARENT_V3_PROJECTION_CONTRACT,
    PrivateResearchV3Error,
    validate_private_v3_projection,
    validate_private_v3_research_input,
    _read_exact_file,
)
from .storage import LocalFilesystemStorage


DELTA_CONTRACT = "BUFFALO_PRIVATE_VARIANT_CREATION_EVIDENCE_DELTA_V1"
INPUT_CONTRACT = "BUFFALO_PRIVATE_DEVELOPMENT_FORECAST_RESEARCH_INPUT_V3_CORRECTED_V1"
PROJECTION_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3_CORRECTED_V1"
COVERAGE_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V3_CORRECTED_V1"
WORKSPACE_CONTRACT = "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3_CORRECTED_V1"
DATA_MODE = "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY"

ACCEPTED_COMMIT = "008f8b5f22184a2e43247636e1b7cb4ba3523696"
ACCEPTED_TREE = "855e3d73ada509327d7cfa02f50da9260912ad07"
RUNTIME_COMMIT = "a438a69cb05dd4f7dfb990e14de33f208dab7ccf"
RUNTIME_TREE = "2dacc029f088a7d44feb537d9784dc9f6a6924dd"
TEST_COMMIT = "62383f48aae9bbd401754bba543659c2b6543023"
TEST_TREE = "686f8e5744f4f40bd916fb8f29c502a36508ca0b"
DESIGN_COMMIT = "d41a886aa84ccddba8e097aa9d9b47c8f6ad15cf"
DESIGN_TREE = "8f9653813993355d8a696db724ebc2228a18eeef"
PARENT_V3_INPUT_ID = "f4f881df40ef5b7275a3ae2b15f3e7004e08e629916a40f71c651687abebe410"
PARENT_V3_INPUT_SHA256 = "6aaa17df12634df2532e07b3945680e9d7a8d5ef3b79ab49d6eafb2b3eaf71c0"
PARENT_V2_INPUT_ID = "71da1905d6871490b171ae2d5326fa9158c41ee0b9d66b29b47f7397a738dae1"
PARENT_V2_INPUT_SHA256 = "b1a5f9a77efcb704aa0cb51f7417c2f8e6f687c1001a7741df698714f63c4de8"
BASE_INTAKE_ID = "9a40f2d661570c27ff3b30eff8d20002e9aa47af6d19d7af9be2133e127f1a6b"
BASE_INTAKE_SHA256 = "712f59fc9ac27b75305db22099457d94195b3e18ae1ee8a11cb544245d257441"
PARENT_WORKSPACE_ID = "c5a71f4798eb2ed3e948f2bbc20097151ad92199b3b72f30276992e8150f2968"
PARENT_WORKSPACE_MANIFEST_SHA256 = "6c86e26bd49983ceacc4898b98e2180ec63846c7aaf84cb95930d4af328f1a0a"
PARENT_PROJECTION_SHA256 = "b200deb0b6fd0a2f1c133114bf6b3a6db1eccc7d6e44deaf738dcdd864c11ae4"
PARENT_PROJECTION_ARTIFACT_SHA256 = "f47e81ad868a8dfada60055e3a3ac9b61aebf9a733c28aacea4af910388d727b"
PARENT_SIDECARS_SHA256 = "2b4b1c39fec4bd259d8b7ff52be580e4823d9723fbdddd7008fcff786dfed8be"
SOURCE_IDENTITY_ID = "bd8d6dfc014b3899366ac1378aeb92b21ac17164b84132a367e7f61a34db39d1"
SOURCE_IDENTITY_FILE_SHA256 = "35055e3453367f44781f3929f9532ed3b6fb27e87ac8041c9a78775de079a245"
SOURCE_EVIDENCE_SHA256 = "8ad9476283fad379910c4c03f68d5c63179e50128440671410d45cf115de618a"

DELTA_RAW_BYTES = 10777
DELTA_RAW_SHA256 = "97e29c2896bc98470e11b99518aee729d0febf751f09d20319ce2362473f9d8e"
DELTA_ROW_SET_SHA256 = "8c1057dc35e234608fedd9c77fba3bd5c44389ddab43753d1ec93453b4f60de8"
DELTA_ROW_COUNT = 43
CURRENT_VARIANT_COUNT = 2009
ELIGIBLE_VARIANT_COUNT = 1365
PARENT_NOT_APPLICABLE_COUNT = 601
CORRECTED_NOT_APPLICABLE_COUNT = 644
CURRENT_MEMBERSHIP_SHA256 = "1b1a308c472ea28f4d675efc8bb3f59090266684747c8a584a24c3ba1ba4bf66"
ELIGIBLE_MEMBERSHIP_SHA256 = "efb12d99862910406191237d55b5411c163eb27036f76dd5c39df98ec89eded4"
PARENT_NOT_APPLICABLE_MEMBERSHIP_SHA256 = "3a1887f9f90cf31a23af5d5ec10be6f5eadec3f4a4e0568bb9dbe3c82cbf16a4"
REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256 = "1d862a029f9474466ea9671f4ff1538cfef4b46ec799e0e6965824575eea8c2c"
CORRECTED_NOT_APPLICABLE_MEMBERSHIP_SHA256 = "596f8242da41ae7b9a068d8957e52f53889becd8ad7e74acfe0357220555e2e7"
EMPTY_MEMBERSHIP_SHA256 = "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570"

RAW_POINT_VIOLATIONS = 38
RAW_TARGET_VIOLATIONS = 136
RAW_UNIQUE_VIOLATION_VARIANTS = 151

_ACCEPTED_PARENT_FILES = {
    f"private-research/v3-inputs/{PARENT_V3_INPUT_ID}.json": (
        43_004_111,
        PARENT_V3_INPUT_SHA256,
    ),
    f"private-research/v2-inputs/{PARENT_V2_INPUT_ID}.json": (
        9_626_244,
        PARENT_V2_INPUT_SHA256,
    ),
    f"private-research/intakes/{BASE_INTAKE_ID}.json": (
        50_387_524,
        BASE_INTAKE_SHA256,
    ),
    f"private-research/workspaces/{PARENT_WORKSPACE_ID}/manifest.json": (
        4_984,
        PARENT_WORKSPACE_MANIFEST_SHA256,
    ),
    f"private-research/workspaces/{PARENT_WORKSPACE_ID}/coverage.json": (
        11_151_899,
        "1919e2465f7fe8e559a940eb66f435d18dfcd06537df1accc75ac38098592982",
    ),
    f"private-research/workspaces/{PARENT_WORKSPACE_ID}/owner-preview.html": (
        21_248_489,
        "df8ffb0f4ff635c319eab5d848bb71808b68395e12a113e0ce4252d294bb668d",
    ),
    f"private-research/workspaces/{PARENT_WORKSPACE_ID}/owner-worksheet.csv": (
        24_717_127,
        "0c4adf709cd18b68a64642df5fbaf1b6ab5083a274552a7b42f39164db594b55",
    ),
    f"private-research/workspaces/{PARENT_WORKSPACE_ID}/projection.json": (
        230_669_882,
        PARENT_PROJECTION_ARTIFACT_SHA256,
    ),
}
_ACCEPTED_PARENT_DIRECTORY_MODES = {
    ".": 0o700,
    "private-research": 0o700,
    "private-research/intakes": 0o755,
    "private-research/v2-inputs": 0o700,
    "private-research/v3-inputs": 0o700,
    "private-research/workspaces": 0o700,
    f"private-research/workspaces/{PARENT_WORKSPACE_ID}": 0o700,
}

_DELTA_PREFIX = "private-research/corrected-v3-deltas"
_SOURCE_PREFIX = "private-research/corrected-v3-sources/sha256"
_INPUT_PREFIX = "private-research/corrected-v3-inputs"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40}$")
_VARIANT_ID = re.compile(r"^[1-9][0-9]*$")
_UTC_SECOND = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

_DELTA_FIELDS = [
    "shopify_variant_id",
    "product_title",
    "variant_title",
    "original_primary_status",
    "original_reason",
    "live_exact_id_created_at",
    "live_product_status",
    "history_start_date",
    "reviewed_primary_status",
    "reviewed_reason",
    "forecast_recalculation_required_for_138_day_full_window",
    "owner_review_required",
]
_AUTHORITY = {
    "status": "REVIEW_ONLY",
    "approval_status": "UNAPPROVED",
    "operational_use": "PROHIBITED",
    "mapping_authority": False,
    "price_authority": False,
    "selection_authority": False,
    "inventory_authority": False,
    "forecast_authority": False,
    "procurement_authority": False,
    "shopify_write_authority": False,
    "po_authority": False,
}
ZERO_AUTHORITY = {
    "database_writes": 0,
    "mapping_approvals": 0,
    "price_approvals": 0,
    "selected_offers": 0,
    "activated_prices": 0,
    "inventory_writes": 0,
    "forecast_authorizations": 0,
    "shopify_writes": 0,
    "supplier_messages": 0,
    "po_actions": 0,
}
_DELTA_LIMITATIONS = [
    "DESCRIPTIVE_FIELDS_ARE_NOT_IDENTITY_OR_ELIGIBILITY_AUTHORITY",
    "NO_OPERATIONAL_OR_PURCHASING_AUTHORITY",
    "NO_PRECREATION_ZEROS_OR_FULL_WINDOW_FORECASTS_FOR_DELTA_ROWS",
    "OWNER_SUPPLIED_REVIEWED_EXACT_ID_EVIDENCE_NOT_RAW_CONNECTOR_RESPONSE",
]
_INPUT_LIMITATIONS = sorted(
    set(_DELTA_LIMITATIONS)
    | {
        "ACCEPTED_V3_PARENT_REMAINS_IMMUTABLE",
        "CORRECTED_TARGET_UNITS_ARE_NOT_PURCHASE_QUANTITIES",
        "UNKNOWN_AVAILABILITY_IS_NOT_ASSUMED_IN_STOCK",
    }
)
_CORRECTED_EXISTENCE_BASIS = (
    "REVIEWED_OWNER_SUPPLIED_EXACT_ID_SHOPIFY_CREATION_TIMESTAMP_AFTER_HISTORY_START"
)
_PRIOR_ABSENT_REASON = "EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE"
_POST_START_REASON = "VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED"
_FIRST_DAY_REASON = "VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED"


class PrivateResearchV3CorrectedError(ValueError):
    """Corrected-V3 source, projection, or replay evidence differs."""


def _json_clone(value: Any) -> Any:
    try:
        return _v2_json_clone(value)
    except (TypeError, ValueError, PrivateResearchV2Error) as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected research value is not canonical JSON"
        ) from exc


class _VerifiedCorrected(dict[str, Any]):
    pass


_DELTA_PROOF = object()
_INPUT_PROOF = object()
_PROJECTION_PROOF = object()
_VERIFIED: dict[
    int,
    tuple[weakref.ReferenceType[_VerifiedCorrected], object, str],
] = {}
_RELEASED: dict[
    int,
    tuple[weakref.ReferenceType[_VerifiedCorrected], object],
] = {}


def _register(
    result: _VerifiedCorrected, proof: object
) -> _VerifiedCorrected:
    try:
        digest = hashlib.sha256(canonical_json_bytes(dict(result))).hexdigest()
    except (TypeError, ValueError, PrivateResearchV2Error) as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected source capability bytes differ"
        ) from exc
    object_id = id(result)

    def release(reference: weakref.ReferenceType[_VerifiedCorrected]) -> None:
        record = _VERIFIED.get(object_id)
        if record is not None and record[0] is reference:
            _VERIFIED.pop(object_id, None)
        released = _RELEASED.get(object_id)
        if released is not None and released[0] is reference:
            _RELEASED.pop(object_id, None)

    reference = weakref.ref(result, release)
    _VERIFIED[object_id] = (reference, proof, digest)
    return result


def _seal(value: Mapping[str, Any], proof: object) -> _VerifiedCorrected:
    return _register(_VerifiedCorrected(_json_clone(value)), proof)


def _seal_owned(
    value: dict[str, Any], proof: object
) -> _VerifiedCorrected:
    """Transfer a newly constructed, unaliased mapping without a deep clone."""

    if type(value) is not dict:
        raise PrivateResearchV3CorrectedError(
            "owned corrected capability shape differs"
        )
    result = _VerifiedCorrected(value)
    value.clear()
    return _register(result, proof)


def _release(value: Mapping[str, Any], *, proof: object, field: str) -> None:
    """Release one proof-bound corrected capability before the next phase."""

    record = _VERIFIED.get(id(value))
    if (
        isinstance(value, _VerifiedCorrected)
        and record is not None
        and record[0]() is value
        and record[1] is proof
    ):
        _VERIFIED.pop(id(value), None)
        _RELEASED[id(value)] = (record[0], proof)
        return
    released = _RELEASED.get(id(value))
    if (
        isinstance(value, _VerifiedCorrected)
        and released is not None
        and released[0]() is value
        and released[1] is proof
    ):
        return
    raise PrivateResearchV3CorrectedError(
        f"{field} is not an owned corrected capability"
    )


def _authenticated(
    value: Mapping[str, Any], proof: object, field: str
) -> _VerifiedCorrected:
    record = _VERIFIED.get(id(value))
    if (
        not isinstance(value, _VerifiedCorrected)
        or record is None
        or record[0]() is not value
        or record[1] is not proof
    ):
        raise PrivateResearchV3CorrectedError(
            f"{field} is not source-authenticated"
        )
    try:
        digest = hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()
    except (TypeError, ValueError, PrivateResearchV2Error) as exc:
        raise PrivateResearchV3CorrectedError(
            f"{field} is not source-authenticated"
        ) from exc
    if (
        record[2] != digest
    ):
        raise PrivateResearchV3CorrectedError(f"{field} is not source-authenticated")
    return value


def _unseal(value: Mapping[str, Any], proof: object, field: str) -> dict[str, Any]:
    authenticated = _authenticated(value, proof, field)
    try:
        return _json_clone(authenticated)
    except (TypeError, ValueError, PrivateResearchV2Error) as exc:
        raise PrivateResearchV3CorrectedError(
            f"{field} is not source-authenticated"
        ) from exc


def _content_id(value: Mapping[str, Any], field: str) -> str:
    basis = _json_clone(value)
    basis[field] = None
    return hashlib.sha256(canonical_json_bytes(basis)).hexdigest()


def _logical_sha(value: Mapping[str, Any], field: str | None = None) -> str:
    basis = _json_clone(value)
    if field is not None:
        basis.pop(field, None)
    return hashlib.sha256(_projection_canonical(basis)).hexdigest()


def _logical_sha_borrowed(
    value: Mapping[str, Any], field: str | None = None
) -> str:
    basis = dict(value)
    if field is not None:
        basis.pop(field, None)
    try:
        return hashlib.sha256(_projection_canonical(basis)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected logical bytes differ"
        ) from exc


def _strict_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError):
        return False


def _validated_parent_v3_input(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return validate_private_v3_research_input(value)
    except MemoryError:
        raise
    except (PrivateResearchV3Error, PrivateResearchV2Error, TypeError, ValueError) as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected parent V3 input differs"
        ) from exc


def _validated_parent_v2_input(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return validate_private_v2_research_input(value)
    except MemoryError:
        raise
    except (PrivateResearchV2Error, TypeError, ValueError) as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected parent V2 input differs"
        ) from exc


def _validated_parent_v3_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return validate_private_v3_projection(value)
    except MemoryError:
        raise
    except (PrivateResearchV3Error, PrivateResearchV2Error, TypeError, ValueError) as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected parent V3 projection differs"
        ) from exc


def _membership_sha(ids: Sequence[str]) -> str:
    ordered = sorted(ids, key=int)
    return hashlib.sha256(canonical_json_bytes(ordered)).hexdigest()


def _require_hex(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise PrivateResearchV3CorrectedError(f"{field} differs")
    return value


def _parent_projection_descriptor() -> dict[str, str]:
    return {
        "contract": PARENT_V3_PROJECTION_CONTRACT,
        "projection_sha256": PARENT_PROJECTION_SHA256,
        "artifact_sha256": PARENT_PROJECTION_ARTIFACT_SHA256,
        "artifact_path": f"private-research/workspaces/{PARENT_WORKSPACE_ID}/projection.json",
        "workspace_id": PARENT_WORKSPACE_ID,
        "workspace_manifest_sha256": PARENT_WORKSPACE_MANIFEST_SHA256,
        "sidecars_sha256": PARENT_SIDECARS_SHA256,
    }


def _implementation_lineage(repo_root: str | Path) -> dict[str, str]:
    root = Path(repo_root).resolve(strict=True)
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD^{commit}"], cwd=root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ACCEPTED_COMMIT, commit],
            cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", DESIGN_COMMIT, commit],
            cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PrivateResearchV3CorrectedError("implementation lineage is unverifiable") from exc
    if status or not _GIT_OID.fullmatch(commit) or not _GIT_OID.fullmatch(tree):
        raise PrivateResearchV3CorrectedError("implementation worktree is not clean")
    return {
        "accepted_commit": ACCEPTED_COMMIT,
        "accepted_tree": ACCEPTED_TREE,
        "runtime_commit": RUNTIME_COMMIT,
        "runtime_tree": RUNTIME_TREE,
        "test_commit": TEST_COMMIT,
        "test_tree": TEST_TREE,
        "implementation_commit": commit,
        "implementation_tree": tree,
    }


def _validate_lineage(value: Any) -> dict[str, str]:
    keys = {
        "accepted_commit",
        "accepted_tree",
        "runtime_commit",
        "runtime_tree",
        "test_commit",
        "test_tree",
        "implementation_commit",
        "implementation_tree",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PrivateResearchV3CorrectedError("implementation lineage differs")
    expected = {
        "accepted_commit": ACCEPTED_COMMIT,
        "accepted_tree": ACCEPTED_TREE,
        "runtime_commit": RUNTIME_COMMIT,
        "runtime_tree": RUNTIME_TREE,
        "test_commit": TEST_COMMIT,
        "test_tree": TEST_TREE,
    }
    if any(not _strict_equal(value.get(key), item) for key, item in expected.items()) or any(
        not isinstance(value.get(key), str) or not _GIT_OID.fullmatch(value[key])
        for key in ("implementation_commit", "implementation_tree")
    ):
        raise PrivateResearchV3CorrectedError("implementation lineage differs")
    return dict(value)


def _parse_delta_bytes(raw: bytes, parent_v3_input: Mapping[str, Any]) -> list[dict[str, str]]:
    if (
        len(raw) != DELTA_RAW_BYTES
        or hashlib.sha256(raw).hexdigest() != DELTA_RAW_SHA256
        or not raw.startswith(b"\xef\xbb\xbf")
        or raw.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
        or raw.count(b"\r\n") != DELTA_ROW_COUNT + 1
        or raw.replace(b"\r\n", b"").find(b"\n") >= 0
        or not raw.endswith(b"\r\n")
    ):
        raise PrivateResearchV3CorrectedError("creation delta raw transport differs")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise PrivateResearchV3CorrectedError("creation delta is unreadable") from exc
    if reader.fieldnames != _DELTA_FIELDS or len(rows) != DELTA_ROW_COUNT:
        raise PrivateResearchV3CorrectedError("creation delta schema differs")
    parent = _validated_parent_v3_input(parent_v3_input)
    prior_absent = {
        str(item["shopify_variant_id"])
        for item in parent["eligibility_ledger"]
        if item["blocker_reason"] == _PRIOR_ABSENT_REASON
    }
    seed_ids = {
        str(item["shopify_variant_id"])
        for item in parent["eligibility_ledger"]
        if item.get("seed_row_sha256") is not None
    }
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    shop_tz = ZoneInfo("America/New_York")
    for raw_row in rows:
        if set(raw_row) != set(_DELTA_FIELDS) or any(value is None for value in raw_row.values()):
            raise PrivateResearchV3CorrectedError("creation delta row differs")
        row = {key: str(raw_row[key]) for key in _DELTA_FIELDS}
        variant_id = row["shopify_variant_id"]
        timestamp = row["live_exact_id_created_at"]
        if (
            not _VARIANT_ID.fullmatch(variant_id)
            or variant_id in seen
            or any(row[key] != row[key].strip() or not row[key] for key in ("product_title", "variant_title", "live_product_status"))
            or not _UTC_SECOND.fullmatch(timestamp)
            or row["original_primary_status"] != "BLOCKED"
            or row["original_reason"] != _PRIOR_ABSENT_REASON
            or row["history_start_date"] != "2026-05-04"
            or row["reviewed_primary_status"] != "NOT_APPLICABLE"
            or row["reviewed_reason"] != _POST_START_REASON
            or row["forecast_recalculation_required_for_138_day_full_window"] != "NO"
            or row["owner_review_required"] != "NO"
        ):
            raise PrivateResearchV3CorrectedError("creation delta control differs")
        try:
            created = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=ZoneInfo("UTC")
            )
        except ValueError as exc:
            raise PrivateResearchV3CorrectedError("creation delta timestamp differs") from exc
        if created.astimezone(shop_tz).date() <= date(2026, 5, 4):
            raise PrivateResearchV3CorrectedError("creation delta date is not post-window-start")
        seen.add(variant_id)
        normalized.append(row)
    normalized.sort(key=lambda item: int(item["shopify_variant_id"]))
    ids = [item["shopify_variant_id"] for item in normalized]
    if (
        set(ids) != prior_absent
        or set(ids) & seed_ids
        or _membership_sha(ids) != REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256
        or hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()
        != DELTA_ROW_SET_SHA256
    ):
        raise PrivateResearchV3CorrectedError("creation delta membership differs")
    return normalized


def build_creation_evidence_delta(
    raw: bytes, parent_v3_input: Mapping[str, Any]
) -> _VerifiedCorrected:
    rows = _parse_delta_bytes(raw, parent_v3_input)
    payload: dict[str, Any] = {
        "contract": DELTA_CONTRACT,
        "data_mode": DATA_MODE,
        "authority": dict(_AUTHORITY),
        "delta_id": None,
        "raw_csv_key": f"{_SOURCE_PREFIX}/{DELTA_RAW_SHA256}.csv",
        "raw_csv_sha256": DELTA_RAW_SHA256,
        "raw_csv_bytes": DELTA_RAW_BYTES,
        "raw_transport": {
            "encoding": "UTF-8",
            "utf8_bom_count": 1,
            "newline": "CRLF",
            "header_row_count": 1,
            "data_row_count": DELTA_ROW_COUNT,
            "line_terminator_count": DELTA_ROW_COUNT + 1,
            "terminal_line_terminator": True,
            "rfc4180_quoting": True,
        },
        "schema": list(_DELTA_FIELDS),
        "normalized_rows": rows,
        "normalized_row_set_sha256": DELTA_ROW_SET_SHA256,
        "parent_v3_input": {
            "contract": PARENT_V3_INPUT_CONTRACT,
            "input_id": PARENT_V3_INPUT_ID,
            "sha256": PARENT_V3_INPUT_SHA256,
            "storage_key": f"private-research/v3-inputs/{PARENT_V3_INPUT_ID}.json",
        },
        "controls": {
            "row_count": DELTA_ROW_COUNT,
            "unique_variant_id_count": DELTA_ROW_COUNT,
            "variant_id_set_sha256": REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256,
            "seed_overlap_count": 0,
            "history_start_date": "2026-05-04",
            "prior_primary_status": "BLOCKED",
            "prior_reason": _PRIOR_ABSENT_REASON,
            "reviewed_primary_status": "NOT_APPLICABLE",
            "reviewed_reason": _POST_START_REASON,
            "forecast_recalculation_required_for_138_day_full_window": "NO",
            "owner_review_required": "NO",
            "created_after_history_start_count": DELTA_ROW_COUNT,
            "shop_timezone": "America/New_York",
        },
        "limitations": list(_DELTA_LIMITATIONS),
        "zero_authority": dict(ZERO_AUTHORITY),
    }
    payload["delta_id"] = _content_id(payload, "delta_id")
    return _seal(validate_creation_evidence_delta(payload, parent_v3_input), _DELTA_PROOF)


def validate_creation_evidence_delta(
    value: Mapping[str, Any], parent_v3_input: Mapping[str, Any]
) -> dict[str, Any]:
    expected_keys = {
        "contract", "data_mode", "authority", "delta_id", "raw_csv_key",
        "raw_csv_sha256", "raw_csv_bytes", "raw_transport", "schema",
        "normalized_rows", "normalized_row_set_sha256", "parent_v3_input",
        "controls", "limitations", "zero_authority",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise PrivateResearchV3CorrectedError("creation delta key inventory differs")
    normalized = _json_clone(value)
    parent = _validated_parent_v3_input(parent_v3_input)
    expected_parent = {
        "contract": PARENT_V3_INPUT_CONTRACT,
        "input_id": PARENT_V3_INPUT_ID,
        "sha256": PARENT_V3_INPUT_SHA256,
        "storage_key": f"private-research/v3-inputs/{PARENT_V3_INPUT_ID}.json",
    }
    if (
        normalized["contract"] != DELTA_CONTRACT
        or normalized["data_mode"] != DATA_MODE
        or not _strict_equal(normalized["authority"], _AUTHORITY)
        or not _strict_equal(normalized["zero_authority"], ZERO_AUTHORITY)
        or not _strict_equal(normalized["limitations"], _DELTA_LIMITATIONS)
        or not _strict_equal(normalized["parent_v3_input"], expected_parent)
        or parent.get("input_id") != PARENT_V3_INPUT_ID
        or _sha(parent) != PARENT_V3_INPUT_SHA256
        or normalized["raw_csv_key"] != f"{_SOURCE_PREFIX}/{DELTA_RAW_SHA256}.csv"
        or normalized["raw_csv_sha256"] != DELTA_RAW_SHA256
        or not _strict_equal(normalized["raw_csv_bytes"], DELTA_RAW_BYTES)
        or normalized["schema"] != _DELTA_FIELDS
        or normalized["normalized_row_set_sha256"] != DELTA_ROW_SET_SHA256
        or normalized["delta_id"] != _content_id(normalized, "delta_id")
    ):
        raise PrivateResearchV3CorrectedError("creation delta identity differs")
    # Re-validate normalized rows independently of CSV transport.
    rows = normalized["normalized_rows"]
    if (
        not isinstance(rows, list)
        or len(rows) != DELTA_ROW_COUNT
        or hashlib.sha256(canonical_json_bytes(rows)).hexdigest() != DELTA_ROW_SET_SHA256
        or [item.get("shopify_variant_id") for item in rows if isinstance(item, Mapping)]
        != sorted(
            [item.get("shopify_variant_id") for item in rows if isinstance(item, Mapping)],
            key=int,
        )
    ):
        raise PrivateResearchV3CorrectedError("creation delta normalized rows differ")
    expected_controls = {
        "row_count": DELTA_ROW_COUNT,
        "unique_variant_id_count": DELTA_ROW_COUNT,
        "variant_id_set_sha256": REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256,
        "seed_overlap_count": 0,
        "history_start_date": "2026-05-04",
        "prior_primary_status": "BLOCKED",
        "prior_reason": _PRIOR_ABSENT_REASON,
        "reviewed_primary_status": "NOT_APPLICABLE",
        "reviewed_reason": _POST_START_REASON,
        "forecast_recalculation_required_for_138_day_full_window": "NO",
        "owner_review_required": "NO",
        "created_after_history_start_count": DELTA_ROW_COUNT,
        "shop_timezone": "America/New_York",
    }
    expected_transport = {
        "encoding": "UTF-8", "utf8_bom_count": 1, "newline": "CRLF",
        "header_row_count": 1, "data_row_count": DELTA_ROW_COUNT,
        "line_terminator_count": DELTA_ROW_COUNT + 1, "terminal_line_terminator": True,
        "rfc4180_quoting": True,
    }
    if not _strict_equal(normalized["controls"], expected_controls) or not _strict_equal(normalized["raw_transport"], expected_transport):
        raise PrivateResearchV3CorrectedError("creation delta controls differ")
    return normalized


def _membership_controls(
    current: Sequence[str], eligible: Sequence[str], parent_na: Sequence[str], delta: Sequence[str]
) -> dict[str, Any]:
    corrected_na = sorted(set(parent_na) | set(delta), key=int)
    controls = {
        "current_catalog": {"count": len(current), "sha256": _membership_sha(current)},
        "eligible": {"count": len(eligible), "sha256": _membership_sha(eligible)},
        "parent_not_applicable": {"count": len(parent_na), "sha256": _membership_sha(parent_na)},
        "reviewed_prior_blocked": {"count": len(delta), "sha256": _membership_sha(delta)},
        "corrected_not_applicable": {"count": len(corrected_na), "sha256": _membership_sha(corrected_na)},
        "corrected_blocked": {"count": 0, "sha256": _membership_sha([])},
    }
    expected = {
        "current_catalog": (CURRENT_VARIANT_COUNT, CURRENT_MEMBERSHIP_SHA256),
        "eligible": (ELIGIBLE_VARIANT_COUNT, ELIGIBLE_MEMBERSHIP_SHA256),
        "parent_not_applicable": (PARENT_NOT_APPLICABLE_COUNT, PARENT_NOT_APPLICABLE_MEMBERSHIP_SHA256),
        "reviewed_prior_blocked": (DELTA_ROW_COUNT, REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256),
        "corrected_not_applicable": (CORRECTED_NOT_APPLICABLE_COUNT, CORRECTED_NOT_APPLICABLE_MEMBERSHIP_SHA256),
        "corrected_blocked": (0, EMPTY_MEMBERSHIP_SHA256),
    }
    if any(controls[key] != {"count": count, "sha256": digest} for key, (count, digest) in expected.items()):
        raise PrivateResearchV3CorrectedError("corrected membership controls differ")
    return controls


def _parent_population(parent_v3_input: Mapping[str, Any]) -> tuple[list[str], list[str], list[str], list[str]]:
    parent = _validated_parent_v3_input(parent_v3_input)
    current = [str(item["shopify_variant_id"]) for item in parent["eligibility_ledger"]]
    eligible = [
        str(item["shopify_variant_id"])
        for item in parent["eligibility_ledger"] if item["status"] == "ELIGIBLE"
    ]
    parent_na = [
        str(item["shopify_variant_id"])
        for item in parent["eligibility_ledger"]
        if item["blocker_reason"] in {_POST_START_REASON, _FIRST_DAY_REASON}
    ]
    blocked = [
        str(item["shopify_variant_id"])
        for item in parent["eligibility_ledger"] if item["blocker_reason"] == _PRIOR_ABSENT_REASON
    ]
    return current, eligible, parent_na, blocked


def _build_corrected_input(
    parent_v3_input: Mapping[str, Any],
    delta: Mapping[str, Any],
    implementation_lineage: Mapping[str, Any],
) -> _VerifiedCorrected:
    parent = _validated_parent_v3_input(parent_v3_input)
    delta_value = validate_creation_evidence_delta(delta, parent)
    current, eligible, parent_na, blocked = _parent_population(parent)
    delta_ids = [item["shopify_variant_id"] for item in delta_value["normalized_rows"]]
    if set(delta_ids) != set(blocked):
        raise PrivateResearchV3CorrectedError("corrected delta does not resolve exact parent blockers")
    membership = _membership_controls(current, eligible, parent_na, delta_ids)
    lineage = _validate_lineage(implementation_lineage)
    policy = _sidecar_policy_descriptor()
    payload: dict[str, Any] = {
        "contract": INPUT_CONTRACT,
        "data_mode": DATA_MODE,
        "authority": dict(_AUTHORITY),
        "input_id": None,
        "parent_v3_input": {
            "contract": PARENT_V3_INPUT_CONTRACT,
            "input_id": PARENT_V3_INPUT_ID,
            "sha256": PARENT_V3_INPUT_SHA256,
            "storage_key": f"private-research/v3-inputs/{PARENT_V3_INPUT_ID}.json",
        },
        "parent_v2_input": {
            "contract": PARENT_V2_INPUT_CONTRACT,
            "input_id": PARENT_V2_INPUT_ID,
            "sha256": PARENT_V2_INPUT_SHA256,
            "storage_key": f"private-research/v2-inputs/{PARENT_V2_INPUT_ID}.json",
        },
        "base_intake": {
            "contract": "BUFFALO_PRIVATE_RESEARCH_INTAKE_V1",
            "intake_id": BASE_INTAKE_ID,
            "sha256": BASE_INTAKE_SHA256,
            "storage_key": f"private-research/intakes/{BASE_INTAKE_ID}.json",
        },
        "parent_projection": _parent_projection_descriptor(),
        "creation_evidence_delta": {
            "contract": DELTA_CONTRACT,
            "delta_id": delta_value["delta_id"],
            "raw_csv_sha256": DELTA_RAW_SHA256,
            "normalized_row_set_sha256": DELTA_ROW_SET_SHA256,
            "raw_storage_key": f"{_SOURCE_PREFIX}/{DELTA_RAW_SHA256}.csv",
            "envelope_storage_key": f"{_DELTA_PREFIX}/{delta_value['delta_id']}.json",
        },
        "joint_policy": policy,
        "coverage_controls": {
            "current_catalog_count": len(current),
            "eligible_count": len(eligible),
            "not_applicable_count": len(parent_na) + len(delta_ids),
            "blocked_count": 0,
            "not_processed_count": 0,
            "prior_blocked_reclassified_count": len(delta_ids),
            "noneligible_reason_counts": {
                _POST_START_REASON: len(parent_na) - 1 + len(delta_ids),
                _FIRST_DAY_REASON: 1,
            },
            "membership_controls": membership,
        },
        "implementation_lineage": lineage,
        "limitations": list(_INPUT_LIMITATIONS),
        "zero_authority": dict(ZERO_AUTHORITY),
    }
    payload["input_id"] = _content_id(payload, "input_id")
    return _seal(validate_private_v3_corrected_input(payload), _INPUT_PROOF)


def _sidecar_policy_descriptor() -> dict[str, str]:
    value = joint_horizon_policy_descriptor()
    return {
        key: str(value[key])
        for key in (
            "evidence_contract", "policy_contract", "method_version",
            "policy_source_sha256", "policy_canonical_sha256",
        )
    }


def validate_private_v3_corrected_input(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = {
        "contract", "data_mode", "authority", "input_id", "parent_v3_input",
        "parent_v2_input", "base_intake", "parent_projection",
        "creation_evidence_delta", "joint_policy", "coverage_controls",
        "implementation_lineage", "limitations", "zero_authority",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PrivateResearchV3CorrectedError("corrected input key inventory differs")
    result = _json_clone(value)
    if (
        result["contract"] != INPUT_CONTRACT
        or result["data_mode"] != DATA_MODE
        or not _strict_equal(result["authority"], _AUTHORITY)
        or not _strict_equal(result["zero_authority"], ZERO_AUTHORITY)
        or not _strict_equal(result["limitations"], _INPUT_LIMITATIONS)
        or not _strict_equal(result["joint_policy"], _sidecar_policy_descriptor())
        or not _strict_equal(result["parent_projection"], _parent_projection_descriptor())
        or result["input_id"] != _content_id(result, "input_id")
    ):
        raise PrivateResearchV3CorrectedError("corrected input identity differs")
    _validate_lineage(result["implementation_lineage"])
    descriptors = (
        (result["parent_v3_input"], PARENT_V3_INPUT_CONTRACT, "input_id", PARENT_V3_INPUT_ID, PARENT_V3_INPUT_SHA256, f"private-research/v3-inputs/{PARENT_V3_INPUT_ID}.json"),
        (result["parent_v2_input"], PARENT_V2_INPUT_CONTRACT, "input_id", PARENT_V2_INPUT_ID, PARENT_V2_INPUT_SHA256, f"private-research/v2-inputs/{PARENT_V2_INPUT_ID}.json"),
        (result["base_intake"], "BUFFALO_PRIVATE_RESEARCH_INTAKE_V1", "intake_id", BASE_INTAKE_ID, BASE_INTAKE_SHA256, f"private-research/intakes/{BASE_INTAKE_ID}.json"),
    )
    for descriptor, contract, id_key, identifier, digest, storage_key in descriptors:
        if not _strict_equal(descriptor, {"contract": contract, id_key: identifier, "sha256": digest, "storage_key": storage_key}):
            raise PrivateResearchV3CorrectedError("corrected parent descriptor differs")
    delta_descriptor = result["creation_evidence_delta"]
    if (
        not isinstance(delta_descriptor, Mapping)
        or set(delta_descriptor)
        != {
            "contract", "delta_id", "raw_csv_sha256",
            "normalized_row_set_sha256", "raw_storage_key",
            "envelope_storage_key",
        }
        or delta_descriptor.get("contract") != DELTA_CONTRACT
        or not isinstance(delta_descriptor.get("delta_id"), str)
        or not _HEX64.fullmatch(delta_descriptor["delta_id"])
        or delta_descriptor.get("raw_csv_sha256") != DELTA_RAW_SHA256
        or delta_descriptor.get("normalized_row_set_sha256")
        != DELTA_ROW_SET_SHA256
        or delta_descriptor.get("raw_storage_key")
        != f"{_SOURCE_PREFIX}/{DELTA_RAW_SHA256}.csv"
        or delta_descriptor.get("envelope_storage_key")
        != f"{_DELTA_PREFIX}/{delta_descriptor.get('delta_id')}.json"
    ):
        raise PrivateResearchV3CorrectedError("corrected delta descriptor differs")
    controls = result["coverage_controls"]
    expected_membership = {
        "current_catalog": {"count": CURRENT_VARIANT_COUNT, "sha256": CURRENT_MEMBERSHIP_SHA256},
        "eligible": {"count": ELIGIBLE_VARIANT_COUNT, "sha256": ELIGIBLE_MEMBERSHIP_SHA256},
        "parent_not_applicable": {"count": PARENT_NOT_APPLICABLE_COUNT, "sha256": PARENT_NOT_APPLICABLE_MEMBERSHIP_SHA256},
        "reviewed_prior_blocked": {"count": DELTA_ROW_COUNT, "sha256": REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256},
        "corrected_not_applicable": {"count": CORRECTED_NOT_APPLICABLE_COUNT, "sha256": CORRECTED_NOT_APPLICABLE_MEMBERSHIP_SHA256},
        "corrected_blocked": {"count": 0, "sha256": EMPTY_MEMBERSHIP_SHA256},
    }
    expected_controls = {
        "current_catalog_count": CURRENT_VARIANT_COUNT,
        "eligible_count": ELIGIBLE_VARIANT_COUNT,
        "not_applicable_count": CORRECTED_NOT_APPLICABLE_COUNT,
        "blocked_count": 0,
        "not_processed_count": 0,
        "prior_blocked_reclassified_count": DELTA_ROW_COUNT,
        "noneligible_reason_counts": {
            _POST_START_REASON: CORRECTED_NOT_APPLICABLE_COUNT - 1,
            _FIRST_DAY_REASON: 1,
        },
        "membership_controls": expected_membership,
    }
    if not _strict_equal(controls, expected_controls):
        raise PrivateResearchV3CorrectedError("corrected coverage controls differ")
    return result


def _scenario_dates() -> dict[str, Any]:
    return {
        "history_start": "2026-05-04",
        "history_end": "2026-09-18",
        "forecast_origin": "2026-09-19",
        "H3": {"horizon_days": 3, "target_start": "2026-09-19", "target_end": "2026-09-21"},
        "H10": {"horizon_days": 10, "target_start": "2026-09-19", "target_end": "2026-09-28"},
        "H17": {"horizon_days": 17, "target_start": "2026-09-19", "target_end": "2026-10-05"},
    }


def _grouped_queue() -> dict[str, Any]:
    shared = {
        "ABC": ("EXACT_COHORT_AND_COST_AUTHORITY_REMAINS_INCOMPLETE", "SHARED_GATE_AFTER_SUPPORTED_FORECAST"),
        "NET_NEED": ("TRUSTED_INCOMING_OPEN_ORDERS_AND_OPERATIONAL_POLICY_NOT_SUPPLIED", "SHARED_GATE_AFTER_SUPPORTED_FORECAST"),
        "CASE_QUANTITY": ("APPROVED_PACK_NOT_SUPPLIED", "SHARED_GATE_REQUIRING_APPLICABLE_APPROVED_EVIDENCE"),
        "ECONOMICS": ("APPROVED_SELECTED_PRICE_MARGIN_AND_FEES_NOT_SUPPLIED", "SHARED_GATE_REQUIRING_APPLICABLE_APPROVED_EVIDENCE"),
        "ORDER": ("PRODUCTION_AND_ORDER_AUTHORITY_NOT_GRANTED", "FUTURE_RELEASE_AUTHORITY_ONLY"),
    }
    def item(stage: str) -> dict[str, Any]:
        return {"stage": stage, "reason": shared[stage][0], "affected_variant_count": None, "scope": shared[stage][1]}
    return {
        "contract": "BUFFALO_PRIVATE_RESEARCH_DECISION_QUEUE_V1",
        "deduplication_basis": "SHARED_STAGE_OR_EXACT_ELIGIBILITY_REASON",
        "categories": [
            {"category": "MACHINE_FIXABLE_DEFECTS", "item_count": 0, "items": []},
            {"category": "RETRIEVABLE_MISSING_SOURCES", "item_count": 2, "items": [item("ABC"), item("NET_NEED")]},
            {"category": "GENUINELY_OWNER_SPECIFIC_UNANSWERED_FACTS", "item_count": 2, "items": [item("CASE_QUANTITY"), item("ECONOMICS")]},
            {"category": "FUTURE_RELEASE_AUTHORITY", "item_count": 1, "items": [item("ORDER")]},
        ],
    }


def _observations_by_id(parent_v3: Mapping[str, Any], parent_v2: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["shopify_variant_id"]): row
        for row in [*parent_v2["variants"], *parent_v3["added_variants"]]
    }


def _raw_violations(owner_rows: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
    """Count affected Variants per ladder, never adjacent-pair events."""

    point_affected: set[str] = set()
    target_affected: set[str] = set()
    affected: set[str] = set()
    for row in owner_rows:
        results = row.get("scenario_results")
        if not isinstance(results, Mapping) or set(results) != {"H3", "H10", "H17"}:
            continue
        try:
            points = [Decimal(str(results[key]["point_forecast_units"])) for key in ("H3", "H10", "H17")]
            targets = [Decimal(str(results[key]["target_units"])) for key in ("H3", "H10", "H17")]
        except (KeyError, TypeError, InvalidOperation):
            continue
        variant_id = str(row["shopify_variant_id"])
        if points[0] > points[1] or points[1] > points[2]:
            point_affected.add(variant_id)
            affected.add(variant_id)
        if targets[0] > targets[1] or targets[1] > targets[2]:
            target_affected.add(variant_id)
            affected.add(variant_id)
    return len(point_affected), len(target_affected), len(affected)


def _corrected_coherence_controls(
    owner_rows: Sequence[Mapping[str, Any]],
) -> tuple[int, int, int]:
    """Derive per-Variant ladder violations and enforce published identities."""

    point_affected: set[str] = set()
    target_affected: set[str] = set()
    affected: set[str] = set()
    calculated = 0
    for row in owner_rows:
        results = row.get("scenario_results")
        if not isinstance(results, Mapping) or set(results) != {"H3", "H10", "H17"}:
            raise PrivateResearchV3CorrectedError("corrected scenario inventory differs")
        statuses = [results[key].get("primary_status") for key in ("H3", "H10", "H17")]
        if statuses == ["NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE"]:
            continue
        if statuses != ["CALCULATED", "CALCULATED", "CALCULATED"]:
            raise PrivateResearchV3CorrectedError("corrected scenario status differs")
        calculated += 1
        try:
            points = [
                Decimal(str(results[key]["point_forecast_units"]))
                for key in ("H3", "H10", "H17")
            ]
            protections = [
                Decimal(str(results[key]["protection_units"]))
                for key in ("H3", "H10", "H17")
            ]
            targets = [
                Decimal(str(results[key]["target_units"]))
                for key in ("H3", "H10", "H17")
            ]
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise PrivateResearchV3CorrectedError(
                "corrected scenario numeric evidence differs"
            ) from exc
        if (
            any(not item.is_finite() or item < 0 for item in [*points, *protections, *targets])
            or any(target != point + protection for point, protection, target in zip(points, protections, targets, strict=True))
        ):
            raise PrivateResearchV3CorrectedError(
                "corrected point/protection/target identity differs"
            )
        variant_id = str(row.get("shopify_variant_id", ""))
        if any(left > right for left, right in zip(points, points[1:])):
            point_affected.add(variant_id)
            affected.add(variant_id)
        if any(left > right for left, right in zip(targets, targets[1:])):
            target_affected.add(variant_id)
            affected.add(variant_id)
    if calculated != ELIGIBLE_VARIANT_COUNT:
        raise PrivateResearchV3CorrectedError("corrected calculated population differs")
    return len(point_affected), len(target_affected), len(affected)


def _corrected_disposition_ledger(
    parent_v3: Mapping[str, Any], delta_ids: set[str]
) -> list[dict[str, Any]]:
    rows = []
    for decision in parent_v3["eligibility_ledger"]:
        variant_id = str(decision["shopify_variant_id"])
        reason = decision["blocker_reason"]
        if decision["status"] == "ELIGIBLE":
            parent_status = corrected_status = "CALCULATED"
            parent_reason = corrected_reason = None
            basis = decision["existence_basis"]
            source = "ACCEPTED_V3"
        elif variant_id in delta_ids:
            parent_status, corrected_status = "BLOCKED", "NOT_APPLICABLE"
            parent_reason, corrected_reason = _PRIOR_ABSENT_REASON, _POST_START_REASON
            basis = _CORRECTED_EXISTENCE_BASIS
            source = "REVIEWED_43_ROW_CREATION_DELTA"
        else:
            parent_status = corrected_status = "NOT_APPLICABLE"
            parent_reason = corrected_reason = reason
            basis = decision["existence_basis"]
            source = "ACCEPTED_V3"
        rows.append({
            "shopify_variant_id": variant_id,
            "parent_primary_status": parent_status,
            "parent_full_window_reason": parent_reason,
            "corrected_primary_status": corrected_status,
            "corrected_full_window_reason": corrected_reason,
            "existence_basis": basis,
            "disposition_source": source,
        })
    rows.sort(key=lambda item: int(item["shopify_variant_id"]))
    return rows


def _history_controls(parent_projection: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    forecast = parent_projection["forecast_research"]
    history = forecast["history"]
    allocation = deepcopy(history["allocation_controls"])
    eligibility = deepcopy(history["eligibility_controls"])
    return {
        "parent_history_contract": history["contract"],
        "parent_composite_id": history["composite_id"],
        "start_date": history["start_date"],
        "end_date": history["end_date"],
        "complete_day_count": history["complete_day_count"],
        "parent_allocation_controls": allocation,
        "parent_eligibility_controls": eligibility,
        "parent_allocation_ledger_sha256": allocation["ledger_sha256"],
        "parent_eligibility_ledger_sha256": eligibility["eligibility_ledger_sha256"],
        "parent_variant_observations_sha256": _logical_sha_borrowed(
            forecast["variant_observations_sha256"]
        ),
        "parent_recent_observed_sales_sha256": eligibility["recent_observed_sales_sha256"],
        "corrected_disposition_ledger_sha256": hashlib.sha256(
            _projection_canonical(list(ledger))
        ).hexdigest(),
    }


def _build_projection_unsealed_in_context(
    corrected_input: Mapping[str, Any],
    delta: Mapping[str, Any],
    parent_v3: Mapping[str, Any],
    parent_v2: Mapping[str, Any],
    parent_projection: Mapping[str, Any],
) -> dict[str, Any]:
    source_input = validate_private_v3_corrected_input(corrected_input)
    parent_v3_value = _validated_parent_v3_input(parent_v3)
    parent_v2_value = _validated_parent_v2_input(parent_v2)
    parent_projection_value = _validated_parent_v3_projection(parent_projection)
    delta_value = validate_creation_evidence_delta(delta, parent_v3_value)
    expected_delta_descriptor = {
        "contract": DELTA_CONTRACT,
        "delta_id": delta_value["delta_id"],
        "raw_csv_sha256": DELTA_RAW_SHA256,
        "normalized_row_set_sha256": DELTA_ROW_SET_SHA256,
        "raw_storage_key": f"{_SOURCE_PREFIX}/{DELTA_RAW_SHA256}.csv",
        "envelope_storage_key": f"{_DELTA_PREFIX}/{delta_value['delta_id']}.json",
    }
    if (
        not _strict_equal(
            source_input["parent_projection"], _parent_projection_descriptor()
        )
        or not _strict_equal(
            source_input["creation_evidence_delta"], expected_delta_descriptor
        )
        or parent_projection_value["projection_sha256"] != PARENT_PROJECTION_SHA256
        or parent_projection_value["forecast_research"]["sidecars_sha256"] != PARENT_SIDECARS_SHA256
        or parent_v2_value.get("input_id") != PARENT_V2_INPUT_ID
        or _sha(parent_v2_value) != PARENT_V2_INPUT_SHA256
        or not _strict_equal(parent_v3_value.get("parent_v2_input"), {
            "contract": PARENT_V2_INPUT_CONTRACT,
            "input_id": PARENT_V2_INPUT_ID,
            "sha256": PARENT_V2_INPUT_SHA256,
        })
    ):
        raise PrivateResearchV3CorrectedError("corrected parent projection binding differs")
    source_identity = parent_projection_value["forecast_research"].get("source_identity")
    try:
        source_identity_file = (
            json.dumps(source_identity, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected parent source identity differs"
        ) from exc
    if (
        not isinstance(source_identity, Mapping)
        or source_identity.get("identity_id") != SOURCE_IDENTITY_ID
        or source_identity.get("evidence_sha256") != SOURCE_EVIDENCE_SHA256
        or source_identity.get("verdict") != "VERIFIED_SAME_SHOP"
        or hashlib.sha256(source_identity_file).hexdigest()
        != SOURCE_IDENTITY_FILE_SHA256
    ):
        raise PrivateResearchV3CorrectedError(
            "corrected parent source identity differs"
        )
    current, eligible, parent_na, blocked = _parent_population(parent_v3_value)
    delta_ids = {str(row["shopify_variant_id"]) for row in delta_value["normalized_rows"]}
    if delta_ids != set(blocked):
        raise PrivateResearchV3CorrectedError("corrected delta population differs")
    membership = _membership_controls(current, eligible, parent_na, sorted(delta_ids, key=int))
    observation_rows = _observations_by_id(parent_v3_value, parent_v2_value)
    parent_coverage = {str(row["shopify_variant_id"]): row for row in parent_projection_value["coverage_rows"]}
    parent_owners = {str(row["shopify_variant_id"]): row for row in parent_projection_value["owner_worksheet"]}
    parent_decisions = {str(row["shopify_variant_id"]): row for row in parent_v3_value["eligibility_ledger"]}
    parent_observation_hashes = parent_projection_value["forecast_research"].get(
        "variant_observations_sha256"
    )
    if (
        set(parent_coverage) != set(current)
        or set(parent_owners) != set(current)
        or set(observation_rows) != set(eligible)
        or not isinstance(parent_observation_hashes, Mapping)
        or set(parent_observation_hashes) != set(eligible)
        or any(
            observation_rows[variant_id].get("observations_sha256")
            != parent_observation_hashes.get(variant_id)
            for variant_id in eligible
        )
    ):
        raise PrivateResearchV3CorrectedError("corrected parent population differs")
    parent_descriptor = _parent_projection_descriptor()
    delta_descriptor = source_input["creation_evidence_delta"]
    sidecars: dict[str, Any] = {}
    coverage_rows: list[dict[str, Any]] = []
    owner_rows: list[dict[str, Any]] = []
    counts = {
        key: {"CALCULATED": 0, "NOT_APPLICABLE": 0, "BLOCKED": 0, "NOT_PROCESSED": 0, "numerical_zero": 0}
        for key in ("H3", "H10", "H17")
    }
    for variant_id in current:
        coverage = deepcopy(parent_coverage[variant_id])
        owner = deepcopy(parent_owners[variant_id])
        if variant_id in observation_rows:
            item = observation_rows[variant_id]
            observations = tuple(
                DemandObservation(date.fromisoformat(str(row["business_date"])), Decimal(str(row["net_units"])), "UNKNOWN")
                for row in item["observations"]
            )
            try:
                sidecar = plan_joint_horizon_forecast(
                    observations,
                    variant_id=variant_id,
                    corrected_input_id=source_input["input_id"],
                    parent_projection=parent_descriptor,
                    creation_evidence_delta=delta_descriptor,
                    observations_sha256=str(item["observations_sha256"]),
                )
                sidecar = validate_joint_horizon_forecast_evidence(
                    sidecar,
                    observations,
                    expected_variant_id=variant_id,
                    expected_corrected_input_id=source_input["input_id"],
                    expected_parent_projection=parent_descriptor,
                    expected_creation_evidence_delta=delta_descriptor,
                    expected_observations_sha256=str(item["observations_sha256"]),
                )
            except JointHorizonForecastError as exc:
                raise PrivateResearchV3CorrectedError(
                    f"joint forecast refused for eligible Variant: {exc.reason_code or 'UNEXPECTED'}"
                ) from exc
            key = sidecar["summaries"]["H3"]["joint_sidecar_key"]
            if key in sidecars:
                raise PrivateResearchV3CorrectedError("corrected sidecar key is duplicated")
            sidecars[key] = sidecar
            summaries = {
                scenario: {**deepcopy(summary), "joint_sidecar_sha256": sidecar["sidecar_sha256"]}
                for scenario, summary in sidecar["summaries"].items()
            }
            coverage["primary_forecast_status"] = "CALCULATED"
            coverage["forecast_sidecar_keys"] = [key]
            parent_scenario_reasons = {
                reason
                for summary in owner["scenario_results"].values()
                for reason in summary.get("reason_codes", [])
            }
            retained = set(coverage.get("missing_data_reasons", [])) - parent_scenario_reasons
            coverage["missing_data_reasons"] = sorted(
                retained | set(summaries["H3"]["reason_codes"])
            )
            owner["scenario_results"] = summaries
            owner["sidecar_keys"] = [key]
            owner["reason_codes"] = coverage["missing_data_reasons"]
            owner["next_missing_stage"] = {
                "stage": "ABC",
                "reason": "EXACT_COHORT_AND_COST_AUTHORITY_REMAINS_INCOMPLETE",
            }
            owner["stage_status"]["CAPTURE"] = "SUPPORTED"
            owner["stage_status"]["FORECAST"] = "CALCULATED_RESEARCH_ONLY"
            for scenario in ("H3", "H10", "H17"):
                counts[scenario]["CALCULATED"] += 1
                if Decimal(summaries[scenario]["point_forecast_units"]) == 0:
                    counts[scenario]["numerical_zero"] += 1
        elif variant_id in delta_ids:
            decision = parent_decisions[variant_id]
            if decision["blocker_reason"] != _PRIOR_ABSENT_REASON:
                raise PrivateResearchV3CorrectedError("corrected delta parent reason differs")
            coverage["primary_forecast_status"] = "NOT_APPLICABLE"
            coverage["forecast_sidecar_keys"] = []
            coverage["missing_data_reasons"] = sorted(
                (set(coverage.get("missing_data_reasons", [])) - {_PRIOR_ABSENT_REASON})
                | {_POST_START_REASON}
            )
            summaries = {}
            for scenario, horizon, end in (("H3", 3, "2026-09-21"), ("H10", 10, "2026-09-28"), ("H17", 17, "2026-10-05")):
                summaries[scenario] = {
                    "horizon_days": horizon,
                    "target_start": "2026-09-19",
                    "target_end": end,
                    "status": "REAL_NUMERICAL_EVALUATION_NOT_RUN",
                    "primary_status": "NOT_APPLICABLE",
                    "selected_model": None,
                    "point_forecast_units": None,
                    "protection_units": None,
                    "target_units": None,
                    "joint_confidence": None,
                    "horizon_evaluation_wape": None,
                    "reason_codes": [_POST_START_REASON],
                    "joint_sidecar_key": None,
                    "joint_sidecar_sha256": None,
                }
                counts[scenario]["NOT_APPLICABLE"] += 1
            owner["existence_basis"] = _CORRECTED_EXISTENCE_BASIS
            owner["scenario_results"] = summaries
            owner["sidecar_keys"] = []
            owner["reason_codes"] = [_POST_START_REASON]
            owner["stage_status"]["CAPTURE"] = "SUPPORTED_REVIEWED_EXACT_ID_CREATION_EVIDENCE"
            owner["stage_status"]["FORECAST"] = "NOT_APPLICABLE:FULL_138_DAY_WINDOW"
            owner["next_missing_stage"] = {"stage": "FORECAST", "reason": _POST_START_REASON}
        else:
            for scenario in ("H3", "H10", "H17"):
                counts[scenario]["NOT_APPLICABLE"] += 1
        coverage_rows.append(coverage)
        owner_rows.append(owner)
    raw_point, raw_target, raw_unique = _raw_violations(parent_projection_value["owner_worksheet"])
    if (raw_point, raw_target, raw_unique) != (
        RAW_POINT_VIOLATIONS, RAW_TARGET_VIOLATIONS, RAW_UNIQUE_VIOLATION_VARIANTS
    ):
        raise PrivateResearchV3CorrectedError("accepted raw coherence controls differ")
    for scenario in ("H3", "H10", "H17"):
        if counts[scenario]["CALCULATED"] != len(eligible) or counts[scenario]["NOT_APPLICABLE"] != len(parent_na) + len(delta_ids):
            raise PrivateResearchV3CorrectedError("corrected primary counts differ")
    corrected_point, corrected_target, corrected_unique = (
        _corrected_coherence_controls(owner_rows)
    )
    if corrected_point or corrected_target or corrected_unique:
        raise PrivateResearchV3CorrectedError("corrected horizon coherence differs")
    disposition = _corrected_disposition_ledger(parent_v3_value, delta_ids)
    history_controls = _history_controls(parent_projection_value, disposition)
    violations = {
        "raw_point": raw_point,
        "raw_target": raw_target,
        "raw_unique_variants": raw_unique,
        "corrected_point": corrected_point,
        "corrected_target": corrected_target,
        "corrected_unique_variants": corrected_unique,
    }
    joint_research = {
        "input_id": source_input["input_id"],
        "policy": source_input["joint_policy"],
        "parent_projection": parent_descriptor,
        "creation_evidence_delta": delta_descriptor,
        "source_identity": deepcopy(parent_projection_value["forecast_research"]["source_identity"]),
        "history_controls": history_controls,
        "membership_controls": membership,
        "scenario_dates": _scenario_dates(),
        "sidecars_sha256": _logical_sha_borrowed(sidecars),
        "primary_status_counts": counts,
        "violation_counts": violations,
    }
    payload: dict[str, Any] = {
        "contract": PROJECTION_CONTRACT,
        "data_mode": DATA_MODE,
        "status": "RESEARCH_ONLY",
        "authority": "ZERO_AUTHORITY_RESEARCH_ONLY",
        "research_only": True,
        "commercial_authority": False,
        "production_activation": False,
        "zero_authority": dict(ZERO_AUTHORITY),
        "implementation_lineage": deepcopy(source_input["implementation_lineage"]),
        "corrected_input": {
            "contract": INPUT_CONTRACT,
            "input_id": source_input["input_id"],
            "sha256": _sha(source_input),
            "storage_key": f"{_INPUT_PREFIX}/{source_input['input_id']}.json",
        },
        "parent_projection": parent_descriptor,
        "declared_coverage": deepcopy(parent_projection_value["declared_coverage"]),
        "coverage_summary": {
            "variant_count": len(current),
            "sidecar_count": len(sidecars),
            "calculated_variant_count": len(eligible),
            "not_applicable_variant_count": len(parent_na) + len(delta_ids),
            "blocked_variant_count": 0,
            "not_processed_variant_count": 0,
            "raw_point_violation_count": raw_point,
            "raw_target_violation_count": raw_target,
            "corrected_point_violation_count": corrected_point,
            "corrected_target_violation_count": corrected_target,
        },
        "coverage_rows": coverage_rows,
        "owner_worksheet": owner_rows,
        "joint_forecast_research": joint_research,
        "forecast_sidecars": dict(sorted(sidecars.items())),
        "grouped_decision_queue": _grouped_queue(),
        "limitations": sorted(set(parent_projection_value["limitations"]) | set(source_input["limitations"])),
    }
    payload["projection_sha256"] = _logical_sha_borrowed(
        payload, "projection_sha256"
    )
    return payload


def _build_projection_unsealed(
    corrected_input: Mapping[str, Any],
    delta: Mapping[str, Any],
    parent_v3: Mapping[str, Any],
    parent_v2: Mapping[str, Any],
    parent_projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay every corrected and inherited Decimal operation deterministically."""

    with localcontext(_joint_decimal_context()):
        return _build_projection_unsealed_in_context(
            corrected_input,
            delta,
            parent_v3,
            parent_v2,
            parent_projection,
        )


def build_private_v3_corrected_projection(
    corrected_input: Mapping[str, Any],
    delta: Mapping[str, Any],
    parent_v3: Mapping[str, Any],
    parent_v2: Mapping[str, Any],
    parent_projection: Mapping[str, Any],
) -> _VerifiedCorrected:
    source_input: dict[str, Any] | None = None
    source_delta: dict[str, Any] | None = None
    value: dict[str, Any] | None = None
    try:
        source_input = _unseal(corrected_input, _INPUT_PROOF, "corrected input")
        source_delta = _unseal(delta, _DELTA_PROOF, "creation delta")
        value = _build_projection_unsealed(
            source_input, source_delta, parent_v3, parent_v2, parent_projection
        )
        return _seal_owned(value, _PROJECTION_PROOF)
    finally:
        if source_input is not None:
            source_input.clear()
        if source_delta is not None:
            source_delta.clear()
        if value is not None:
            value.clear()


def _validate_private_v3_corrected_projection_borrowed(
    value: Mapping[str, Any],
) -> _VerifiedCorrected:
    """Authenticate an internal read-only projection view without cloning it."""

    result = _authenticated(value, _PROJECTION_PROOF, "corrected projection")
    if (
        result.get("contract") != PROJECTION_CONTRACT
        or result.get("projection_sha256")
        != _logical_sha_borrowed(result, "projection_sha256")
    ):
        raise PrivateResearchV3CorrectedError(
            "corrected projection identity differs"
        )
    return result


def validate_private_v3_corrected_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Accept only a projection produced by authenticated parent replay."""

    return _json_clone(_validate_private_v3_corrected_projection_borrowed(value))


def replay_private_v3_corrected_projection(
    value: Mapping[str, Any],
    corrected_input: Mapping[str, Any],
    delta: Mapping[str, Any],
    parent_v3: Mapping[str, Any],
    parent_v2: Mapping[str, Any],
    parent_projection: Mapping[str, Any],
) -> _VerifiedCorrected:
    expected: dict[str, Any] | None = None
    try:
        expected = _build_projection_unsealed(
            corrected_input, delta, parent_v3, parent_v2, parent_projection
        )
        if (
            not isinstance(value, Mapping)
            or value.get("projection_sha256")
            != _logical_sha_borrowed(value, "projection_sha256")
            or not _strict_equal(value, expected)
        ):
            raise PrivateResearchV3CorrectedError(
                "corrected projection semantic replay differs"
            )
        return _seal_owned(expected, _PROJECTION_PROOF)
    finally:
        if expected is not None:
            expected.clear()


def delta_manifest_key(delta_id: str) -> str:
    _require_hex(delta_id, "creation delta ID")
    return f"{_DELTA_PREFIX}/{delta_id}.json"


def corrected_input_manifest_key(input_id: str) -> str:
    _require_hex(input_id, "corrected input ID")
    return f"{_INPUT_PREFIX}/{input_id}.json"


def _verify_lineage_objects(repo_root: str | Path, lineage: Mapping[str, Any]) -> None:
    value = _validate_lineage(lineage)
    root = Path(repo_root).resolve(strict=True)
    bare = (
        root.name == "source.git"
        and (root / "HEAD").is_file()
        and (root / "objects").is_dir()
        and (root / "refs").is_dir()
        and not (root / ".git").exists()
    )
    executable = "/usr/bin/git"
    command_prefix = [
        executable,
        "-c",
        f"safe.directory={root}",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "protocol.ext.allow=never",
    ]
    cwd = root
    if bare:
        command_prefix.extend(("--git-dir", str(root)))
        cwd = root.parent
    environment = {
        "GIT_ASKPASS": "/bin/false",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }

    def run(*arguments: str, text: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*command_prefix, *arguments],
            cwd=cwd,
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            timeout=30,
        )

    try:
        pairs = (
            ("accepted_commit", "accepted_tree"),
            ("runtime_commit", "runtime_tree"),
            ("test_commit", "test_tree"),
            ("implementation_commit", "implementation_tree"),
        )
        for commit_key, tree_key in pairs:
            commit_type = run("cat-file", "-t", value[commit_key], text=True).stdout.strip()
            tree_type = run("cat-file", "-t", value[tree_key], text=True).stdout.strip()
            resolved = run(
                "rev-parse", f"{value[commit_key]}^{{tree}}", text=True
            ).stdout.strip()
            if commit_type != "commit" or tree_type != "tree" or resolved != value[tree_key]:
                raise PrivateResearchV3CorrectedError(
                    "implementation lineage commit/tree pair differs"
                )
        design_type = run("cat-file", "-t", DESIGN_COMMIT, text=True).stdout.strip()
        design_tree = run(
            "rev-parse", f"{DESIGN_COMMIT}^{{tree}}", text=True
        ).stdout.strip()
        if design_type != "commit" or design_tree != DESIGN_TREE:
            raise PrivateResearchV3CorrectedError("committed design lineage differs")
        run(
            "merge-base", "--is-ancestor", ACCEPTED_COMMIT,
            value["implementation_commit"]
        )
        run(
            "merge-base", "--is-ancestor", DESIGN_COMMIT,
            value["implementation_commit"]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrivateResearchV3CorrectedError("implementation lineage objects are unavailable") from exc


def _private_object_bytes(
    root: Path,
    key: str,
    *,
    field: str,
    expected_sha256: str | None = None,
) -> bytes:
    """Read one retained private object without resolving away path evidence."""

    parts = key.split("/")
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PrivateResearchV3CorrectedError(f"{field} path differs")
    path = root.joinpath(*parts)
    try:
        root_resolved = root.resolve(strict=True)
        if root_resolved != root or path.is_symlink() or path.resolve(strict=True) != path:
            raise PrivateResearchV3CorrectedError(f"{field} path differs")
        ancestor = path.parent
        while True:
            info = ancestor.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or ancestor.is_symlink()
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise PrivateResearchV3CorrectedError(
                    f"{field} private ancestor mode differs"
                )
            if ancestor == root:
                break
            if root not in ancestor.parents:
                raise PrivateResearchV3CorrectedError(f"{field} path differs")
            ancestor = ancestor.parent
        info = path.stat(follow_symlinks=False)
        raw = path.read_bytes()
    except PrivateResearchV3CorrectedError:
        raise
    except OSError as exc:
        raise PrivateResearchV3CorrectedError(f"{field} is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or (
            expected_sha256 is not None
            and hashlib.sha256(raw).hexdigest() != expected_sha256
        )
    ):
        raise PrivateResearchV3CorrectedError(f"{field} bytes or mode differ")
    return raw


def _ensure_corrected_private_parent(root: Path, key: str) -> None:
    """Create only owned, nonsymlinked 0700 parents beneath the private root."""

    parts = key.split("/")
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PrivateResearchV3CorrectedError(
            "corrected private object path differs"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        current_fd = os.open(root, flags)
    except OSError as exc:
        raise PrivateResearchV3CorrectedError(
            "corrected private root is unavailable"
        ) from exc
    try:
        root_info = os.fstat(current_fd)
        if (
            root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            raise PrivateResearchV3CorrectedError(
                "corrected private root mode differs"
            )
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            try:
                child_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as exc:
                raise PrivateResearchV3CorrectedError(
                    "corrected private ancestor differs"
                ) from exc
            child_info = os.fstat(child_fd)
            if (
                child_info.st_uid != os.getuid()
                or stat.S_IMODE(child_info.st_mode) != 0o700
            ):
                os.close(child_fd)
                raise PrivateResearchV3CorrectedError(
                    "corrected private ancestor mode differs"
                )
            os.close(current_fd)
            current_fd = child_fd
    finally:
        os.close(current_fd)


def _private_object_metadata(
    root: Path,
    key: str,
    *,
    field: str,
    expected_bytes: int,
    expected_sha256: str,
) -> dict[str, Any]:
    """Stream-hash an immutable parent object while retaining path/mode evidence."""

    parts = key.split("/")
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PrivateResearchV3CorrectedError(f"{field} path differs")
    path = root.joinpath(*parts)
    try:
        if root.resolve(strict=True) != root or path.is_symlink() or path.resolve(strict=True) != path:
            raise PrivateResearchV3CorrectedError(f"{field} path differs")
        ancestor = path.parent
        while True:
            info = ancestor.stat(follow_symlinks=False)
            relative = "." if ancestor == root else ancestor.relative_to(root).as_posix()
            expected_mode = _ACCEPTED_PARENT_DIRECTORY_MODES.get(relative)
            if (
                not stat.S_ISDIR(info.st_mode)
                or ancestor.is_symlink()
                or info.st_uid != os.getuid()
                or expected_mode is None
                or stat.S_IMODE(info.st_mode) != expected_mode
            ):
                raise PrivateResearchV3CorrectedError(
                    f"{field} private ancestor mode differs"
                )
            if ancestor == root:
                break
            if root not in ancestor.parents:
                raise PrivateResearchV3CorrectedError(f"{field} path differs")
            ancestor = ancestor.parent
        info = path.stat(follow_symlinks=False)
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                count += len(chunk)
    except PrivateResearchV3CorrectedError:
        raise
    except OSError as exc:
        raise PrivateResearchV3CorrectedError(f"{field} is unavailable") from exc
    actual_sha = digest.hexdigest()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or count != expected_bytes
        or info.st_size != expected_bytes
        or actual_sha != expected_sha256
    ):
        raise PrivateResearchV3CorrectedError(f"{field} bytes or mode differ")
    return {
        "path": key,
        "bytes": count,
        "sha256": actual_sha,
        "uid": info.st_uid,
        "mode": "0600",
    }


def accepted_parent_snapshot(private_root: str | Path) -> dict[str, Any]:
    """Rehash the exact accepted parent file tuple without exposing contents."""

    root = validate_private_root(Path(private_root))
    directories = []
    for relative, expected_mode in sorted(_ACCEPTED_PARENT_DIRECTORY_MODES.items()):
        path = root if relative == "." else root.joinpath(*relative.split("/"))
        try:
            info = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise PrivateResearchV3CorrectedError(
                "accepted parent directory is unavailable"
            ) from exc
        if (
            path.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != expected_mode
        ):
            raise PrivateResearchV3CorrectedError(
                "accepted parent directory mode differs"
            )
        directories.append(
            {
                "path": relative,
                "uid": info.st_uid,
                "mode": f"{expected_mode:04o}",
            }
        )
    files = [
        _private_object_metadata(
            root,
            key,
            field=f"accepted parent {key}",
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha,
        )
        for key, (expected_bytes, expected_sha) in sorted(
            _ACCEPTED_PARENT_FILES.items()
        )
    ]
    return {
        "contract": "BUFFALO_PRIVATE_V3_CORRECTED_PARENT_SNAPSHOT_V1",
        "workspace_id": PARENT_WORKSPACE_ID,
        "projection_sha256": PARENT_PROJECTION_SHA256,
        "sidecars_sha256": PARENT_SIDECARS_SHA256,
        "source_identity_id": SOURCE_IDENTITY_ID,
        "source_identity_file_sha256": SOURCE_IDENTITY_FILE_SHA256,
        "source_evidence_sha256": SOURCE_EVIDENCE_SHA256,
        "directories": directories,
        "files": files,
    }


def write_private_v3_corrected_input(
    private_root: str | Path,
    *,
    parent_v3_input: Mapping[str, Any],
    delta_csv_path: str | Path,
    repo_root: str | Path,
) -> tuple[_VerifiedCorrected, _VerifiedCorrected]:
    """Store the reviewed delta and corrected child input exactly once."""

    root = validate_private_root(Path(private_root))
    lineage = _implementation_lineage(repo_root)
    raw = _read_exact_file(
        delta_csv_path,
        DELTA_RAW_SHA256,
        field="reviewed creation evidence delta",
    )
    delta = build_creation_evidence_delta(raw, parent_v3_input)
    corrected_input = _build_corrected_input(parent_v3_input, delta, lineage)
    storage = LocalFilesystemStorage(root)
    objects = (
        (delta["raw_csv_key"], raw, "reviewed creation delta source"),
        (delta_manifest_key(delta["delta_id"]), canonical_json_bytes(delta), "creation delta envelope"),
        (corrected_input_manifest_key(corrected_input["input_id"]), canonical_json_bytes(corrected_input), "corrected V3 input"),
    )
    for key, data, field in objects:
        _ensure_corrected_private_parent(root, key)
        if storage.exists(key):
            if storage.get_bytes(key) != data:
                raise PrivateResearchV3CorrectedError(f"immutable {field} differs")
        else:
            storage.put_bytes_once(key, data)
        if _private_object_bytes(
            root,
            key,
            field=f"stored {field}",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        ) != data:
            raise PrivateResearchV3CorrectedError(f"stored {field} differs")
    return delta, corrected_input


def read_private_v3_corrected_bundle(
    private_root: str | Path,
    input_id: str,
    *,
    parent_v3_input: Mapping[str, Any],
    repo_root: str | Path,
) -> tuple[_VerifiedCorrected, _VerifiedCorrected]:
    """Read and semantically replay the corrected delta/input package."""

    root = validate_private_root(Path(private_root))
    try:
        input_key = corrected_input_manifest_key(input_id)
        input_bytes = _private_object_bytes(
            root, input_key, field="corrected input"
        )
        value = json.loads(input_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateResearchV3CorrectedError("corrected input is unreadable") from exc
    normalized = validate_private_v3_corrected_input(value)
    if (
        normalized.get("input_id") != input_id
        or input_key != corrected_input_manifest_key(normalized["input_id"])
        or input_bytes != canonical_json_bytes(normalized)
    ):
        raise PrivateResearchV3CorrectedError("corrected input is not canonical")
    delta_id = normalized["creation_evidence_delta"]["delta_id"]
    try:
        delta_key = delta_manifest_key(delta_id)
        raw_key = normalized["creation_evidence_delta"]["raw_storage_key"]
        delta_bytes = _private_object_bytes(
            root, delta_key, field="creation delta envelope"
        )
        delta_value = json.loads(delta_bytes)
        raw = _private_object_bytes(
            root,
            raw_key,
            field="reviewed creation delta source",
            expected_sha256=DELTA_RAW_SHA256,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateResearchV3CorrectedError("corrected source package is unreadable") from exc
    delta = validate_creation_evidence_delta(delta_value, parent_v3_input)
    if (
        delta.get("delta_id") != delta_id
        or delta_key != normalized["creation_evidence_delta"]["envelope_storage_key"]
        or raw_key != delta["raw_csv_key"]
        or delta_bytes != canonical_json_bytes(delta)
    ):
        raise PrivateResearchV3CorrectedError("creation delta envelope is not canonical")
    rows = _parse_delta_bytes(raw, parent_v3_input)
    if not _strict_equal(rows, delta["normalized_rows"]):
        raise PrivateResearchV3CorrectedError("creation delta semantic replay differs")
    _verify_lineage_objects(repo_root, normalized["implementation_lineage"])
    expected = _build_corrected_input(
        parent_v3_input, delta, normalized["implementation_lineage"]
    )
    if not _strict_equal(dict(expected), normalized):
        raise PrivateResearchV3CorrectedError("corrected input semantic replay differs")
    return _seal(delta, _DELTA_PROOF), _seal(normalized, _INPUT_PROOF)


__all__ = [
    "COVERAGE_CONTRACT",
    "DATA_MODE",
    "DELTA_CONTRACT",
    "INPUT_CONTRACT",
    "PROJECTION_CONTRACT",
    "WORKSPACE_CONTRACT",
    "PrivateResearchV3CorrectedError",
    "accepted_parent_snapshot",
    "build_creation_evidence_delta",
    "build_private_v3_corrected_projection",
    "corrected_input_manifest_key",
    "delta_manifest_key",
    "read_private_v3_corrected_bundle",
    "replay_private_v3_corrected_projection",
    "validate_creation_evidence_delta",
    "validate_private_v3_corrected_input",
    "validate_private_v3_corrected_projection",
    "write_private_v3_corrected_input",
]

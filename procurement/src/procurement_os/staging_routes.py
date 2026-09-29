"""Dependency-light, shared public-to-worker route contract for staging."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable

from starlette.routing import compile_path


MAX_DEFAULT_BODY = 2 * 1024 * 1024
MAX_BUFFERED_RESPONSE = 8 * 1024 * 1024
MAX_STREAM_RESPONSE = 64 * 1024 * 1024
MAX_PATH_BYTES = 256
PUBLISHED_RESEARCH_ARTIFACTS = frozenset(
    {
        "coverage.json",
        "owner-preview.html",
        "owner-worksheet.csv",
        "projection.json",
    }
)
PUBLISHED_RESEARCH_ARTIFACT_BYTES = {
    "coverage.json": 10_684_242,
    "owner-preview.html": 21_144_726,
    "owner-worksheet.csv": 17_347_994,
    "projection.json": 191_788_544,
}
PUBLISHED_RESEARCH_ARTIFACT_TYPES = {
    "coverage.json": "application/json",
    "owner-preview.html": "text/html",
    "owner-worksheet.csv": "text/csv",
    "projection.json": "application/json",
}
FORM = frozenset({"application/x-www-form-urlencoded"})
JSON = frozenset({"application/json"})
BUFFERED_RESPONSE_TYPES = frozenset(
    {"", "application/json", "text/html", "text/plain"}
)
JSON_RESPONSE = frozenset({"application/json"})
CSV_RESPONSE = frozenset({"text/csv"})
MONDAY_ARTIFACT_RESPONSE = frozenset({"application/zip", "text/csv"})
_UUID_PARAMETERS = frozenset({"candidate_id", "run_id", "batch_id"})
_INTEGER_PARAMETERS = frozenset(
    {"artifact_id", "exception_id", "recommendation_id"}
)
_CANONICAL_UUID = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)


class StagingRouteError(ValueError):
    """The staging route inventory or a requested route is invalid."""


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    worker_role: str
    method: str
    path_template: str
    capability: str
    body_limit: int = 0
    allowed_content_types: frozenset[str] = field(default_factory=frozenset)
    allowed_query_fields: frozenset[str] = field(default_factory=frozenset)
    stream_response: bool = False
    min_response_bytes: int = 0
    max_response_bytes: int = MAX_BUFFERED_RESPONSE
    allowed_response_content_types: frozenset[str] = BUFFERED_RESPONSE_TYPES
    _regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.worker_role not in {"synthetic", "research"}:
            raise StagingRouteError("staging route worker differs")
        if self.method not in {"GET", "POST"}:
            raise StagingRouteError("staging route method differs")
        if self.method == "GET" and (
            self.body_limit or self.allowed_content_types
        ):
            raise StagingRouteError("safe staging route permits a body")
        if self.method == "POST" and self.body_limit < 0:
            raise StagingRouteError("unsafe staging route body limit differs")
        if (
            self.min_response_bytes < 0
            or self.max_response_bytes < self.min_response_bytes
            or not self.allowed_response_content_types
        ):
            raise StagingRouteError("staging response limit differs")
        regex, _, _ = compile_path(self.path_template)
        object.__setattr__(self, "_regex", regex)

    def matches(self, *, method: str, path: str) -> bool:
        if method != self.method:
            return False
        matched = self._regex.fullmatch(path)
        if matched is None:
            return False
        for name, value in matched.groupdict().items():
            if name in _UUID_PARAMETERS and _CANONICAL_UUID.fullmatch(value) is None:
                return False
            if name in _INTEGER_PARAMETERS and not _canonical_positive_bigint(value):
                return False
        return True


def _get(
    route_id: str,
    path: str,
    capability: str = "procurement.review.read",
    *,
    worker: str = "synthetic",
    query: frozenset[str] = frozenset(),
    stream_response: bool = False,
    min_response_bytes: int | None = None,
    max_response_bytes: int | None = None,
    response_types: frozenset[str] | None = None,
) -> RouteSpec:
    return RouteSpec(
        route_id=route_id,
        worker_role=worker,
        method="GET",
        path_template=path,
        capability=capability,
        allowed_query_fields=query,
        stream_response=stream_response,
        min_response_bytes=(1 if stream_response else 0)
        if min_response_bytes is None
        else min_response_bytes,
        max_response_bytes=(
            MAX_STREAM_RESPONSE if stream_response else MAX_BUFFERED_RESPONSE
        )
        if max_response_bytes is None
        else max_response_bytes,
        allowed_response_content_types=(
            BUFFERED_RESPONSE_TYPES if response_types is None else response_types
        ),
    )


def _post(
    route_id: str,
    path: str,
    capability: str,
    *,
    content_types: frozenset[str] = FORM,
    body_limit: int = MAX_DEFAULT_BODY,
) -> RouteSpec:
    return RouteSpec(
        route_id=route_id,
        worker_role="synthetic",
        method="POST",
        path_template=path,
        capability=capability,
        body_limit=body_limit,
        allowed_content_types=content_types,
    )


ROUTES: tuple[RouteSpec, ...] = (
    _get("synthetic.home", "/"),
    _get(
        "synthetic.inventory_status",
        "/inventory-snapshots/status",
        query=frozenset({"as_of"}),
    ),
    _get("synthetic.vendor_rules", "/vendor-rules"),
    _get("synthetic.mapping_status", "/supplier-mapping/status"),
    _get(
        "synthetic.mapping_list",
        "/supplier-mapping",
        query=frozenset({"query", "supplier", "status", "limit", "offset"}),
    ),
    _post(
        "synthetic.mapping_intake",
        "/supplier-mapping/intake",
        "procurement.review.intake",
        content_types=FORM,
        body_limit=1_024,
    ),
    _get("synthetic.mapping_detail", "/supplier-mapping/{candidate_id}"),
    _get(
        "synthetic.mapping_source",
        "/supplier-mapping/{candidate_id}/source",
        "procurement.evidence.download",
        stream_response=True,
        max_response_bytes=1 * 1024 * 1024,
        response_types=JSON_RESPONSE,
    ),
    _post(
        "synthetic.mapping_decision",
        "/supplier-mapping/{candidate_id}/decision",
        "procurement.mapping.approve",
    ),
    _post(
        "synthetic.mapping_selection",
        "/supplier-mapping/{candidate_id}/selection",
        "procurement.offer.select",
    ),
    _post(
        "synthetic.target_cost",
        "/economics/target-cost",
        "procurement.review.read",
        content_types=JSON,
    ),
    _post(
        "synthetic.qualifying_quantity",
        "/economics/qualifying-quantity",
        "procurement.review.read",
        content_types=JSON,
    ),
    _post(
        "synthetic.matching_score",
        "/matching/score",
        "procurement.review.read",
        content_types=JSON,
    ),
    _get(
        "synthetic.reconciliation_investigation_items",
        "/reconciliation/investigation/items",
    ),
    _get(
        "synthetic.reconciliation_investigation",
        "/reconciliation/investigation",
    ),
    _get("synthetic.monday_list", "/monday-runs"),
    _post(
        "synthetic.monday_prepare",
        "/monday-runs/prepare",
        "procurement.order.approve",
    ),
    _get("synthetic.monday_detail", "/monday-runs/{run_id}"),
    _post(
        "synthetic.monday_retire_stale",
        "/monday-runs/{run_id}/retire-stale-forecast",
        "procurement.order.approve",
    ),
    _post(
        "synthetic.monday_exclude_blocker",
        "/monday-runs/{run_id}/blockers/{exception_id}/exclude",
        "procurement.order.approve",
    ),
    _post(
        "synthetic.monday_review_recommendation",
        "/monday-runs/{run_id}/recommendations/{recommendation_id}/review",
        "procurement.order.approve",
    ),
    _post(
        "synthetic.monday_build",
        "/monday-runs/{run_id}/build",
        "procurement.order.approve",
    ),
    _get(
        "synthetic.monday_artifact",
        "/monday-runs/{run_id}/artifacts/{artifact_id}",
        "procurement.evidence.download",
        stream_response=True,
        max_response_bytes=8 * 1024 * 1024,
        response_types=MONDAY_ARTIFACT_RESPONSE,
    ),
    _get(
        "synthetic.price_template",
        "/price-books/template.csv",
        "procurement.evidence.download",
        stream_response=True,
        min_response_bytes=408,
        max_response_bytes=408,
        response_types=CSV_RESPONSE,
    ),
    _get("synthetic.price_list", "/price-books"),
    _get("synthetic.price_detail", "/price-books/{batch_id}"),
    _get(
        "synthetic.price_raw",
        "/price-books/{batch_id}/raw.csv",
        "procurement.evidence.download",
        stream_response=True,
        max_response_bytes=5_000_000,
        response_types=CSV_RESPONSE,
    ),
    _post(
        "synthetic.price_confirmation_preview",
        "/price-books/{batch_id}/confirmation-preview",
        "procurement.price.approve",
    ),
    _post(
        "synthetic.price_confirm",
        "/price-books/{batch_id}/confirm",
        "procurement.price.approve",
    ),
    _post(
        "synthetic.price_apply_preview",
        "/price-books/{batch_id}/apply-preview",
        "procurement.price.approve",
    ),
    _post(
        "synthetic.price_apply",
        "/price-books/{batch_id}/apply",
        "procurement.price.approve",
    ),
    _get(
        "research.index",
        "/private-research",
        "procurement.private_research.read",
        worker="research",
        query=frozenset({"stockout", "status", "vendor", "page", "q"}),
        max_response_bytes=2 * 1024 * 1024,
    ),
    _get(
        "research.artifact",
        "/private-research/artifacts/{artifact_name}",
        "procurement.private_research.download",
        worker="research",
        stream_response=True,
        max_response_bytes=max(PUBLISHED_RESEARCH_ARTIFACT_BYTES.values()),
        response_types=frozenset(PUBLISHED_RESEARCH_ARTIFACT_TYPES.values()),
    ),
)


def match_route(*, method: str, path: str, raw_path: bytes) -> RouteSpec | None:
    if not canonical_path_is_valid(path=path, raw_path=raw_path):
        return None
    for route in ROUTES:
        if not route.matches(method=method.upper(), path=path):
            continue
        if route.route_id == "research.artifact" and (
            path.rsplit("/", 1)[-1] not in PUBLISHED_RESEARCH_ARTIFACTS
        ):
            return None
        return route
    return None


def response_metadata_is_allowed(
    *, route: RouteSpec, path: str, content_length: int, content_type: str
) -> bool:
    if (
        content_length < route.min_response_bytes
        or content_length > route.max_response_bytes
        or content_type not in route.allowed_response_content_types
    ):
        return False
    if route.route_id != "research.artifact":
        return True
    name = path.rsplit("/", 1)[-1]
    expected = PUBLISHED_RESEARCH_ARTIFACT_BYTES.get(name)
    return (
        expected is not None
        and content_length == expected
        and content_type == PUBLISHED_RESEARCH_ARTIFACT_TYPES.get(name)
    )


def _canonical_positive_bigint(value: str) -> bool:
    if not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        return False
    return int(value) <= 9_223_372_036_854_775_807


def validate_application_routes(
    *, synthetic_routes: Iterable[object], research_routes: Iterable[object]
) -> None:
    route_objects = {
        "synthetic": tuple(synthetic_routes),
        "research": tuple(research_routes),
    }
    actual_by_worker = {
        worker: {
            (method, str(getattr(route, "path", ""))): route
            for route in routes
            for method in (getattr(route, "methods", None) or set())
        }
        for worker, routes in route_objects.items()
    }
    expected_by_worker = {
        worker: {
            (route.method, route.path_template): route
            for route in ROUTES
            if route.worker_role == worker
        }
        for worker in ("synthetic", "research")
    }
    if any(
        not set(expected_by_worker[worker]).issubset(actual_by_worker[worker])
        for worker in expected_by_worker
    ):
        raise StagingRouteError("reviewed staging route inventory differs")
    for worker, expected in expected_by_worker.items():
        for identity, spec in expected.items():
            actual = actual_by_worker[worker][identity]
            dependant = getattr(actual, "dependant", None)
            query_fields = frozenset(
                str(getattr(field, "alias", getattr(field, "name", "")))
                for field in getattr(dependant, "query_params", ())
                if getattr(field, "alias", getattr(field, "name", ""))
            )
            if query_fields != spec.allowed_query_fields:
                raise StagingRouteError("reviewed staging query inventory differs")


def canonical_path_is_valid(*, path: str, raw_path: bytes) -> bool:
    try:
        canonical_raw_path = path.encode("ascii")
    except UnicodeEncodeError:
        return False
    lowered = raw_path.lower()
    return not (
        not path.startswith("/")
        or len(raw_path) > MAX_PATH_BYTES
        or "\\" in path
        or "//" in path
        or (path != "/" and path.endswith("/"))
        or any(segment in {".", ".."} for segment in path.split("/"))
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or b"%" in lowered
        or b"%2f" in lowered
        or b"%5c" in lowered
        or b"%2e" in lowered
        or b"\x00" in lowered
        or raw_path != canonical_raw_path
    )

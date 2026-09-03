from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterable, Iterator, Mapping

from .catalog import numeric_shopify_id
from .matching import extract_size, normalize_text
from .shopify.queries import SHOPIFYQL_WRAPPER_QUERY, historical_sales_shopifyql


@dataclass(frozen=True)
class CurrentIdentity:
    variant_id: str
    sku: str | None
    product_title: str
    variant_title: str
    active: bool = True
    catalog_state: str = "LIVE"


@dataclass(frozen=True)
class HistoricalAlias:
    canonical_variant_id: str
    old_variant_id: str | None
    historical_sku: str | None
    historical_product_title: str | None
    historical_variant_title: str | None
    approved: bool = True


@dataclass(frozen=True)
class HistoricalSourceDecision:
    source_identity_key: str
    decision_action: str
    canonical_variant_id: str | None


@dataclass(frozen=True)
class SalesSourceRow:
    sale_date: date
    source_variant_id: str | None
    source_sku: str | None
    source_product_title: str | None
    source_variant_title: str | None
    net_items_sold: Decimal
    net_sales: Decimal | None


@dataclass(frozen=True)
class IdentityResolution:
    status: str  # RESOLVED | UNRESOLVED | AMBIGUOUS | EXCLUDED
    canonical_variant_id: str | None
    method: str | None
    candidates: tuple[str, ...] = ()
    evidence: dict[str, Any] | None = None


def _text_key(product_title: str | None, variant_title: str | None) -> tuple[str, str]:
    return normalize_text(product_title), normalize_text(variant_title)


def _sku(value: str | None) -> str:
    return (value or "").strip().casefold()


def _source_variant_dimension(value: str | int | None) -> str | None:
    """Preserve ShopifyQL's explicit zero bucket separately from its null bucket."""
    if value in (None, ""):
        return None
    raw = str(value).strip()
    numeric = numeric_shopify_id(raw)
    if raw == "0" or numeric == "0":
        return "0"
    return numeric


def _identity_variant_id(value: str | int | None) -> str | None:
    value = _source_variant_dimension(value)
    return None if value == "0" else value


class HistoricalIdentityIndex:
    """Deterministic historical-sales identity resolver.

    Resolution is intentionally conservative. Fuzzy similarity is *not* used to auto-map
    historical revenue. Any material ambiguity is surfaced for review and can later be
    converted into an approved alias or an explicit exclusion.
    """

    def __init__(
        self,
        current: Iterable[CurrentIdentity],
        aliases: Iterable[HistoricalAlias],
        *,
        excluded_source_keys: Iterable[str] = (),
        exclusion_methods: Mapping[str, str] | None = None,
        source_decisions: Iterable[HistoricalSourceDecision] = (),
    ) -> None:
        self.current_by_id: dict[str, CurrentIdentity] = {}
        self.old_id_to_current: dict[str, set[str]] = {}
        self.alias_full: dict[tuple[str, str, str], set[str]] = {}
        self.alias_sku: dict[str, set[str]] = {}
        self.current_full: dict[tuple[str, str, str], set[str]] = {}
        self.current_sku: dict[str, set[str]] = {}
        self.exclusion_methods = {
            str(source_key): "EXPLICIT_EXCLUSION"
            for source_key in excluded_source_keys
        }
        for source_key, method in (exclusion_methods or {}).items():
            if method not in {
                "EXPLICIT_EXCLUSION",
                "EXPLICIT_UNATTRIBUTABLE_EXCLUSION",
            }:
                raise ValueError("unknown historical-sales exclusion method")
            self.exclusion_methods[str(source_key)] = method
        self.source_key_to_current: dict[str, str] = {}

        for row in current:
            vid = numeric_shopify_id(row.variant_id)
            if not vid:
                continue
            self.current_by_id[vid] = row
            sku = _sku(row.sku)
            p, v = _text_key(row.product_title, row.variant_title)
            if sku and row.active:
                self.current_sku.setdefault(sku, set()).add(vid)
                self.current_full.setdefault((sku, p, v), set()).add(vid)

        for row in aliases:
            if not row.approved:
                continue
            vid = numeric_shopify_id(row.canonical_variant_id)
            if not vid:
                continue
            old = numeric_shopify_id(row.old_variant_id)
            if old:
                self.old_id_to_current.setdefault(old, set()).add(vid)
            sku = _sku(row.historical_sku)
            p, v = _text_key(row.historical_product_title, row.historical_variant_title)
            if sku:
                self.alias_sku.setdefault(sku, set()).add(vid)
                self.alias_full.setdefault((sku, p, v), set()).add(vid)

        for row in source_decisions:
            if str(row.decision_action).strip().upper() != "MAP":
                continue
            source_key = str(row.source_identity_key).strip()
            vid = numeric_shopify_id(row.canonical_variant_id)
            if not source_key or not vid:
                raise ValueError("approved source-key MAP decision is incomplete")
            existing = self.source_key_to_current.get(source_key)
            if existing is not None and existing != vid:
                raise ValueError("conflicting approved source-key MAP decisions")
            self.source_key_to_current[source_key] = vid

    @staticmethod
    def source_key(row: SalesSourceRow) -> str:
        return "|".join([
            _source_variant_dimension(row.source_variant_id) or "",
            _sku(row.source_sku),
            normalize_text(row.source_product_title),
            normalize_text(row.source_variant_title),
        ])

    def resolve(self, row: SalesSourceRow) -> IdentityResolution:
        source_key = self.source_key(row)
        exclusion_method = self.exclusion_methods.get(source_key)
        if exclusion_method is not None:
            return IdentityResolution(
                "EXCLUDED",
                None,
                exclusion_method,
                evidence={"source_key": source_key},
            )

        approved_source_target = self.source_key_to_current.get(source_key)
        if approved_source_target is not None:
            return IdentityResolution(
                "RESOLVED",
                approved_source_target,
                "APPROVED_SOURCE_IDENTITY_DECISION",
                (approved_source_target,),
                {"source_identity_key": source_key},
            )

        sid = _identity_variant_id(row.source_variant_id)
        current = self.current_by_id.get(sid) if sid else None
        if current and current.active:
            return IdentityResolution(
                "RESOLVED", sid, "EXACT_ACTIVE_VARIANT_ID", (sid,),
                {"source_variant_id": sid, "catalog_state": current.catalog_state},
            )

        # A retired/inactive identity is still the exact owner of its historical demand.
        # The one exception is a historical record explicitly resolved as a recreation;
        # its approved continuity alias must identify the canonical replacement.
        if current and current.catalog_state != "RESOLVED_RECREATED":
            return IdentityResolution(
                "RESOLVED", sid, "EXACT_PRESERVED_HISTORICAL_VARIANT_ID", (sid,),
                {"source_variant_id": sid, "catalog_state": current.catalog_state},
            )

        if sid:
            aliases = sorted(self.old_id_to_current.get(sid, set()))
            if len(aliases) == 1:
                return IdentityResolution("RESOLVED", aliases[0], "APPROVED_VARIANT_ID_ALIAS", tuple(aliases), {"old_variant_id": sid})
            if len(aliases) > 1:
                return IdentityResolution("AMBIGUOUS", None, "APPROVED_VARIANT_ID_ALIAS", tuple(aliases), {"old_variant_id": sid})

        if current:
            return IdentityResolution(
                "RESOLVED", sid, "EXACT_PRESERVED_HISTORICAL_VARIANT_ID", (sid,),
                {"source_variant_id": sid, "catalog_state": current.catalog_state},
            )

        sku = _sku(row.source_sku)
        p, v = _text_key(row.source_product_title, row.source_variant_title)
        if sku:
            approved_exact = sorted(self.alias_full.get((sku, p, v), set()))
            if len(approved_exact) == 1:
                return IdentityResolution("RESOLVED", approved_exact[0], "APPROVED_HISTORICAL_IDENTITY", tuple(approved_exact), {
                    "source_sku": row.source_sku, "product_title": row.source_product_title, "variant_title": row.source_variant_title,
                })
            if len(approved_exact) > 1:
                return IdentityResolution("AMBIGUOUS", None, "APPROVED_HISTORICAL_IDENTITY", tuple(approved_exact), {"source_sku": row.source_sku})

            current_exact = sorted(self.current_full.get((sku, p, v), set()))
            live_sku_candidates = sorted(self.current_sku.get(sku, set()))
            if len(current_exact) == 1 and len(live_sku_candidates) == 1:
                return IdentityResolution(
                    "RESOLVED", current_exact[0], "DETERMINISTIC_UNIQUE_CURRENT_IDENTITY",
                    tuple(current_exact), {"source_sku": row.source_sku,
                                           "product_title": row.source_product_title,
                                           "variant_title": row.source_variant_title},
                )
            if current_exact:
                return IdentityResolution(
                    "AMBIGUOUS", None, "DUPLICATE_SKU_CONFLICT",
                    tuple(live_sku_candidates or current_exact),
                    {"source_sku": row.source_sku,
                     "reason": "duplicate live SKU cannot provide unique identity proof"},
                )

            # SKU is mapping evidence, never permanent identity. Even one candidate is
            # insufficient without exact normalized identity evidence, and a size conflict
            # is an explicit blocker rather than something a confidence score can hide.
            candidates = sorted(self.alias_sku.get(sku, set()) | self.current_sku.get(sku, set()))
            if candidates:
                source_size = extract_size(row.source_variant_title) or extract_size(row.source_product_title)
                conflicts: list[str] = []
                for candidate_id in candidates:
                    candidate = self.current_by_id.get(candidate_id)
                    if not candidate:
                        continue
                    candidate_size = extract_size(candidate.variant_title) or extract_size(candidate.product_title)
                    if source_size and candidate_size and source_size != candidate_size:
                        conflicts.append(f"SIZE_CONFLICT:{candidate_id}:{source_size}!={candidate_size}")
                return IdentityResolution(
                    "AMBIGUOUS" if len(candidates) > 1 or conflicts else "UNRESOLVED",
                    None,
                    "SKU_EVIDENCE_ONLY",
                    tuple(candidates),
                    {"source_sku": row.source_sku, "conflicts": conflicts,
                     "reason": "supplier/historical SKU alone is insufficient identity evidence"},
                )

        return IdentityResolution("UNRESOLVED", None, None, (), {
            "source_variant_id": sid,
            "source_sku": row.source_sku,
            "product_title": row.source_product_title,
            "variant_title": row.source_variant_title,
        })


def parse_shopifyql_row(row: dict[str, Any]) -> SalesSourceRow:
    required = {
        "day", "product_variant_id", "product_title_at_time_of_sale",
        "product_variant_title_at_time_of_sale", "product_variant_sku_at_time_of_sale",
        "net_items_sold", "net_sales",
    }
    missing = sorted(required - set(row))
    if missing:
        raise ValueError(f"ShopifyQL sales row missing required fields: {', '.join(missing)}")
    if row["net_items_sold"] in (None, "") or row["net_sales"] in (None, ""):
        raise ValueError("ShopifyQL sales row has a null required metric")
    raw_date = str(row["day"])[:10]
    return SalesSourceRow(
        sale_date=date.fromisoformat(raw_date),
        source_variant_id=_source_variant_dimension(row.get("product_variant_id")),
        source_sku=(str(row.get("product_variant_sku_at_time_of_sale")).strip() if row.get("product_variant_sku_at_time_of_sale") not in (None, "") else None),
        source_product_title=row.get("product_title_at_time_of_sale"),
        source_variant_title=row.get("product_variant_title_at_time_of_sale"),
        net_items_sold=Decimal(str(row["net_items_sold"])),
        net_sales=Decimal(str(row["net_sales"])),
    )


def source_row_hash(row: SalesSourceRow) -> str:
    payload = {
        "sale_date": row.sale_date.isoformat(),
        "source_variant_id": _source_variant_dimension(row.source_variant_id),
        "source_sku": row.source_sku,
        "source_product_title": row.source_product_title,
        "source_variant_title": row.source_variant_title,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def date_chunks(start: date, end: date, *, days: int = 31) -> Iterator[tuple[date, date]]:
    if end < start:
        raise ValueError("end must be >= start")
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def fetch_shopifyql_sales(
    client: Any,
    start: date,
    end: date,
    *,
    limit: int = 1000,
    chunk_days: int = 31,
    max_pages_per_chunk: int = 1000,
) -> list[SalesSourceRow]:
    """Fetch daily net sales facts in bounded date chunks with LIMIT/OFFSET pagination."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    result: list[SalesSourceRow] = []
    seen_hashes: set[str] = set()
    for chunk_start, chunk_end in date_chunks(start, end, days=chunk_days):
        offset = 0
        pages = 0
        while True:
            pages += 1
            if pages > max_pages_per_chunk:
                raise RuntimeError(f"ShopifyQL pagination exceeded {max_pages_per_chunk} pages for {chunk_start}..{chunk_end}")
            q = historical_sales_shopifyql(chunk_start.isoformat(), chunk_end.isoformat(), limit=limit, offset=offset)
            payload = client.query(SHOPIFYQL_WRAPPER_QUERY, {"query": q})["shopifyqlQuery"]
            errors = payload.get("parseErrors") or []
            if errors:
                raise RuntimeError(f"ShopifyQL parse error: {errors}")
            table = payload.get("tableData") or {}
            rows = table.get("rows") or []
            for raw in rows:
                parsed = parse_shopifyql_row(raw)
                h = source_row_hash(parsed)
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                result.append(parsed)
            if len(rows) < limit:
                break
            offset += limit
    return result


HISTORICAL_SALES_CATALOG_SEARCH_MAX_QUERY_LENGTH = 128
HISTORICAL_SALES_CATALOG_SEARCH_MAX_RESULTS = 20


def _catalog_search_pattern(value: str) -> str:
    """Escape PostgreSQL LIKE metacharacters so reviewer input stays literal."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_historical_sales_catalog(
    conn: Any,
    query: str,
    *,
    limit: int = HISTORICAL_SALES_CATALOG_SEARCH_MAX_RESULTS,
) -> list[dict[str, Any]]:
    """Search stored canonical catalog evidence without making an identity decision.

    Exact membership in ``variants`` is the existing MAP_TO_CANONICAL target
    contract. Active status and catalog state are returned as reviewer evidence;
    they are not reinterpreted here as a new eligibility rule.
    """
    query = str(query).strip()
    if not query:
        return []
    if len(query) > HISTORICAL_SALES_CATALOG_SEARCH_MAX_QUERY_LENGTH:
        raise ValueError(
            "Local catalog search query must be 128 characters or fewer"
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("Local catalog search result limit must be a positive integer")
    bounded_limit = min(limit, HISTORICAL_SALES_CATALOG_SEARCH_MAX_RESULTS)
    escaped = _catalog_search_pattern(query)
    prefix = f"{escaped}%"
    contains = f"%{escaped}%"

    with conn.cursor() as cur:
        cur.execute(
            """WITH search_parameters AS (
                 SELECT %s::text AS exact_value,
                        %s::text AS prefix_pattern,
                        %s::text AS contains_pattern
               )
               SELECT v.variant_id,v.product_title,v.variant_title,v.sku,v.barcode,
                      v.active,v.catalog_state
               FROM variants v
               CROSS JOIN search_parameters p
               WHERE v.variant_id ILIKE p.contains_pattern ESCAPE '\\'
                  OR COALESCE(v.sku,'') ILIKE p.contains_pattern ESCAPE '\\'
                  OR COALESCE(v.barcode,'') ILIKE p.contains_pattern ESCAPE '\\'
                  OR v.product_title ILIKE p.contains_pattern ESCAPE '\\'
                  OR v.variant_title ILIKE p.contains_pattern ESCAPE '\\'
                  OR COALESCE(v.handle,'') ILIKE p.contains_pattern ESCAPE '\\'
               ORDER BY CASE
                 WHEN v.variant_id=p.exact_value THEN 0
                 WHEN LOWER(COALESCE(v.sku,''))=LOWER(p.exact_value)
                   OR LOWER(COALESCE(v.barcode,''))=LOWER(p.exact_value) THEN 1
                 WHEN v.variant_id ILIKE p.prefix_pattern ESCAPE '\\'
                   OR COALESCE(v.sku,'') ILIKE p.prefix_pattern ESCAPE '\\'
                   OR COALESCE(v.barcode,'') ILIKE p.prefix_pattern ESCAPE '\\'
                   OR v.product_title ILIKE p.prefix_pattern ESCAPE '\\'
                   OR v.variant_title ILIKE p.prefix_pattern ESCAPE '\\'
                   OR COALESCE(v.handle,'') ILIKE p.prefix_pattern ESCAPE '\\' THEN 2
                 ELSE 3
               END,
               v.variant_id
               LIMIT %s""",
            (query, prefix, contains, bounded_limit),
        )
        rows = cur.fetchall()
    return [
        {
            "variant_id": str(row[0]),
            "product_title": row[1],
            "variant_title": row[2],
            "sku": row[3],
            "barcode": row[4],
            "active": bool(row[5]),
            "catalog_state": row[6],
        }
        for row in rows
    ]


def load_identity_index(
    conn: Any,
    *,
    pending_legacy_exclusion_keys: Iterable[str] = (),
) -> HistoricalIdentityIndex:
    pending_legacy = {str(key) for key in pending_legacy_exclusion_keys}
    with conn.cursor() as cur:
        cur.execute("SELECT variant_id,sku,product_title,variant_title,active,catalog_state FROM variants")
        current = [CurrentIdentity(str(r[0]), r[1], r[2] or "", r[3] or "", bool(r[4]), r[5] or "SEEDED") for r in cur.fetchall()]
        cur.execute("""
            SELECT variant_id,old_variant_id,historical_sku,historical_product_title,historical_variant_title,approved
            FROM variant_aliases
        """)
        aliases = [HistoricalAlias(str(r[0]), r[1], r[2], r[3], r[4], bool(r[5])) for r in cur.fetchall()]
        cur.execute(
            """WITH ranked AS (
                 SELECT historical_sales_review_decision_id,source_identity_key,
                        decision_action,reason_code,decision_schema_version,
                        ROW_NUMBER() OVER (
                          PARTITION BY source_identity_key
                          ORDER BY decided_at DESC,
                                   historical_sales_review_decision_id DESC
                        ) AS row_number
                 FROM historical_sales_review_decisions
               )
               SELECT e.source_key,e.reason_code,e.effective_decision_id,
                      d.historical_sales_review_decision_id,d.decision_action,
                      d.reason_code,d.decision_schema_version
               FROM historical_sales_exclusions e
               LEFT JOIN ranked d
                 ON d.source_identity_key=e.source_key AND d.row_number=1
               WHERE e.active=TRUE"""
        )
        exclusion_methods: dict[str, str] = {}
        unclassifiable_exclusions: list[str] = []
        for row in cur.fetchall():
            (
                source_key,
                exclusion_reason,
                effective_decision_id,
                latest_decision_id,
                latest_action,
                latest_reason,
                latest_schema,
            ) = row
            structured_link_is_exact = (
                effective_decision_id is not None
                and latest_decision_id is not None
                and effective_decision_id == latest_decision_id
                and latest_action == "EXCLUDE"
                and exclusion_reason == latest_reason
            )
            if structured_link_is_exact and exclusion_reason == (
                "PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION"
            ):
                exclusion_methods[str(source_key)] = "EXPLICIT_EXCLUSION"
            elif structured_link_is_exact and exclusion_reason == (
                "HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW"
            ):
                exclusion_methods[str(source_key)] = (
                    "EXPLICIT_UNATTRIBUTABLE_EXCLUSION"
                )
            elif (
                exclusion_reason is None
                and effective_decision_id is None
                and (
                    (
                        latest_decision_id is not None
                        and latest_action == "EXCLUDE"
                        and latest_reason is None
                        and latest_schema == "LEGACY_V1"
                    )
                    or str(source_key) in pending_legacy
                )
            ):
                # A service transaction may prove one legacy exclusion before
                # appending its matching ledger row. Outside that explicitly
                # scoped transaction, an effective legacy EXCLUDE row is
                # required. Terminal exclusions always require their exact link.
                exclusion_methods[str(source_key)] = "EXPLICIT_EXCLUSION"
            else:
                unclassifiable_exclusions.append(str(source_key))
        if unclassifiable_exclusions:
            raise ValueError(
                "active historical exclusion has no approved effective method: "
                + ",".join(sorted(unclassifiable_exclusions))
            )
        cur.execute(
            """WITH ranked AS (
                 SELECT source_identity_key,decision_action,canonical_variant_id,
                        ROW_NUMBER() OVER (
                          PARTITION BY source_identity_key
                          ORDER BY decided_at DESC,
                                   historical_sales_review_decision_id DESC
                        ) AS row_number
                 FROM historical_sales_review_decisions
               )
               SELECT source_identity_key,decision_action,canonical_variant_id
               FROM ranked WHERE row_number=1"""
        )
        source_decisions = [
            HistoricalSourceDecision(str(r[0]), str(r[1]), str(r[2]) if r[2] else None)
            for r in cur.fetchall()
        ]
    return HistoricalIdentityIndex(
        current,
        aliases,
        exclusion_methods=exclusion_methods,
        source_decisions=source_decisions,
    )


def persist_sales_backfill(
    conn: Any,
    rows: list[SalesSourceRow],
    identity: HistoricalIdentityIndex,
    *,
    start_date: date,
    end_date: date,
    query_version: str = "SHOPIFYQL_SALES_V1",
) -> str:
    """Disabled legacy entry point retained only to fail old callers safely."""
    raise RuntimeError(
        "legacy all-in-memory persistence is disabled; use historical_sales.run_historical_sales_backfill"
    )


def approve_historical_sales_mapping(conn: Any, source_row: SalesSourceRow, canonical_variant_id: str, *, actor: str, note: str = "") -> None:
    """Persist a difficult historical identity decision so it never needs to be guessed again."""
    if not note.strip():
        raise ValueError("Mapping reason is required")
    record_historical_sales_review_decision(
        conn, source_key=HistoricalIdentityIndex.source_key(source_row), action="MAP_TO_CANONICAL",
        canonical_variant_id=canonical_variant_id, actor=actor, reason=note,
    )


def exclude_historical_sales_identity(conn: Any, source_row: SalesSourceRow, *, actor: str, reason: str) -> None:
    """Explicitly exclude a historical identity from current-item forecasting; never silently drop it."""
    if not reason.strip():
        raise ValueError("Exclusion reason is required")
    record_historical_sales_review_decision(
        conn, source_key=HistoricalIdentityIndex.source_key(source_row), action="EXCLUDE_HISTORICAL_ITEM",
        canonical_variant_id=None, actor=actor, reason=reason,
    )


def rerun_sales_identity_resolution(conn: Any, *, start_date: date, end_date: date) -> dict[str, Any]:
    """Re-resolve already-fetched raw sales after aliases/exclusions change; no Shopify call required."""
    from .historical_sales import finalize_sales_backfill
    with conn.cursor() as cur:
        cur.execute(
            """SELECT sales_backfill_id FROM sales_backfill_runs
               WHERE start_date=%s AND end_date=%s AND coverage_complete=TRUE AND pages_complete=TRUE
               ORDER BY started_at DESC LIMIT 1""",
            (start_date, end_date),
        )
        run = cur.fetchone()
    if not run:
        raise ValueError("no complete durable Phase 4 run exists for the requested range")
    return finalize_sales_backfill(conn, run_id=str(run[0]))


def get_historical_sales_review_items(conn: Any) -> list[dict[str, Any]]:
    from .historical_sales import get_historical_sales_review_items as service
    return service(conn)


def record_historical_sales_review_decision(
    conn: Any,
    *,
    source_key: str,
    action: str,
    canonical_variant_id: str | None,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    from .historical_sales import record_historical_sales_review_decision as service
    return service(
        conn, source_key=source_key, action=action, canonical_variant_id=canonical_variant_id,
        actor=actor, reason=reason,
    )

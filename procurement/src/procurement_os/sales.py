from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterable, Iterator

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
    ) -> None:
        self.current_by_id: dict[str, CurrentIdentity] = {}
        self.old_id_to_current: dict[str, set[str]] = {}
        self.alias_full: dict[tuple[str, str, str], set[str]] = {}
        self.alias_sku: dict[str, set[str]] = {}
        self.current_full: dict[tuple[str, str, str], set[str]] = {}
        self.current_sku: dict[str, set[str]] = {}
        self.excluded_source_keys = set(excluded_source_keys)

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
        if source_key in self.excluded_source_keys:
            return IdentityResolution("EXCLUDED", None, "EXPLICIT_EXCLUSION", evidence={"source_key": source_key})

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


def load_identity_index(conn: Any) -> HistoricalIdentityIndex:
    with conn.cursor() as cur:
        cur.execute("SELECT variant_id,sku,product_title,variant_title,active,catalog_state FROM variants")
        current = [CurrentIdentity(str(r[0]), r[1], r[2] or "", r[3] or "", bool(r[4]), r[5] or "SEEDED") for r in cur.fetchall()]
        cur.execute("""
            SELECT variant_id,old_variant_id,historical_sku,historical_product_title,historical_variant_title,approved
            FROM variant_aliases
        """)
        aliases = [HistoricalAlias(str(r[0]), r[1], r[2], r[3], r[4], bool(r[5])) for r in cur.fetchall()]
        cur.execute("SELECT source_key FROM historical_sales_exclusions WHERE active=TRUE")
        excluded = [r[0] for r in cur.fetchall()]
    return HistoricalIdentityIndex(current, aliases, excluded_source_keys=excluded)


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

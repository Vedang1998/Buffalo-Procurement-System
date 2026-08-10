from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterable, Iterator

from .catalog import numeric_shopify_id
from .matching import normalize_text
from .shopify.queries import SHOPIFYQL_WRAPPER_QUERY, historical_sales_shopifyql


@dataclass(frozen=True)
class CurrentIdentity:
    variant_id: str
    sku: str | None
    product_title: str
    variant_title: str
    active: bool = True


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
            if sku:
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
            numeric_shopify_id(row.source_variant_id) or "",
            _sku(row.source_sku),
            normalize_text(row.source_product_title),
            normalize_text(row.source_variant_title),
        ])

    def resolve(self, row: SalesSourceRow) -> IdentityResolution:
        source_key = self.source_key(row)
        if source_key in self.excluded_source_keys:
            return IdentityResolution("EXCLUDED", None, "EXPLICIT_EXCLUSION", evidence={"source_key": source_key})

        sid = numeric_shopify_id(row.source_variant_id)
        if sid and sid in self.current_by_id:
            return IdentityResolution("RESOLVED", sid, "CURRENT_VARIANT_ID", (sid,), {"source_variant_id": sid})

        if sid:
            aliases = sorted(self.old_id_to_current.get(sid, set()))
            if len(aliases) == 1:
                return IdentityResolution("RESOLVED", aliases[0], "HISTORICAL_VARIANT_ID_ALIAS", tuple(aliases), {"old_variant_id": sid})
            if len(aliases) > 1:
                return IdentityResolution("AMBIGUOUS", None, "HISTORICAL_VARIANT_ID_ALIAS", tuple(aliases), {"old_variant_id": sid})

        sku = _sku(row.source_sku)
        p, v = _text_key(row.source_product_title, row.source_variant_title)
        if sku:
            exact = sorted(self.alias_full.get((sku, p, v), set()) | self.current_full.get((sku, p, v), set()))
            if len(exact) == 1:
                return IdentityResolution("RESOLVED", exact[0], "SKU_AND_HISTORICAL_TITLE", tuple(exact), {
                    "source_sku": row.source_sku, "product_title": row.source_product_title, "variant_title": row.source_variant_title,
                })
            if len(exact) > 1:
                return IdentityResolution("AMBIGUOUS", None, "SKU_AND_HISTORICAL_TITLE", tuple(exact), {"source_sku": row.source_sku})

            # SKU-only is accepted only when globally unique across approved aliases + current catalog.
            candidates = sorted(self.alias_sku.get(sku, set()) | self.current_sku.get(sku, set()))
            if len(candidates) == 1:
                return IdentityResolution("RESOLVED", candidates[0], "UNIQUE_SKU", tuple(candidates), {"source_sku": row.source_sku})
            if len(candidates) > 1:
                return IdentityResolution("AMBIGUOUS", None, "UNIQUE_SKU", tuple(candidates), {"source_sku": row.source_sku})

        return IdentityResolution("UNRESOLVED", None, None, (), {
            "source_variant_id": sid,
            "source_sku": row.source_sku,
            "product_title": row.source_product_title,
            "variant_title": row.source_variant_title,
        })


def parse_shopifyql_row(row: dict[str, Any]) -> SalesSourceRow:
    raw_date = str(row["day"])[:10]
    return SalesSourceRow(
        sale_date=date.fromisoformat(raw_date),
        source_variant_id=numeric_shopify_id(row.get("product_variant_id")),
        source_sku=(str(row.get("product_variant_sku_at_time_of_sale")).strip() if row.get("product_variant_sku_at_time_of_sale") not in (None, "") else None),
        source_product_title=row.get("product_title_at_time_of_sale"),
        source_variant_title=row.get("product_variant_title_at_time_of_sale"),
        net_items_sold=Decimal(str(row.get("net_items_sold") or 0)),
        net_sales=Decimal(str(row["net_sales"])) if row.get("net_sales") not in (None, "") else None,
    )


def source_row_hash(row: SalesSourceRow) -> str:
    payload = {
        "sale_date": row.sale_date.isoformat(),
        "source_variant_id": numeric_shopify_id(row.source_variant_id),
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


def load_identity_index(conn: Any) -> HistoricalIdentityIndex:
    with conn.cursor() as cur:
        cur.execute("SELECT variant_id,sku,product_title,variant_title,active FROM variants")
        current = [CurrentIdentity(str(r[0]), r[1], r[2] or "", r[3] or "", bool(r[4])) for r in cur.fetchall()]
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
    """Persist source facts first, then rebuild the canonical daily series atomically.

    The SALES_BACKFILL readiness gate passes only when every non-zero raw sales row in the
    requested date range is either RESOLVED or explicitly EXCLUDED.
    """
    stats = {"resolved": 0, "unresolved": 0, "ambiguous": 0, "excluded": 0}
    resolved_units = Decimal("0")
    unresolved_units = Decimal("0")
    source_net_sales = Decimal("0")
    canonical_net_sales = Decimal("0")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sales_backfill_runs(start_date,end_date,query_version,status)
                   VALUES (%s,%s,%s,'RUNNING') RETURNING sales_backfill_id""",
                (start_date, end_date, query_version),
            )
            backfill_id = str(cur.fetchone()[0])

            for row in rows:
                resolution = identity.resolve(row)
                key = resolution.status.lower()
                stats[key] += 1
                if resolution.status == "RESOLVED":
                    resolved_units += row.net_items_sold
                    if row.net_sales is not None:
                        canonical_net_sales += row.net_sales
                elif resolution.status in {"UNRESOLVED", "AMBIGUOUS"}:
                    unresolved_units += abs(row.net_items_sold)
                if row.net_sales is not None:
                    source_net_sales += row.net_sales

                cur.execute(
                    """INSERT INTO shopify_sales_daily_raw(
                         sales_backfill_id,sale_date,source_variant_id,source_sku,source_product_title,source_variant_title,
                         net_items_sold,net_sales,canonical_variant_id,resolution_status,resolution_method,
                         resolution_evidence,source_row_hash
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                       ON CONFLICT(source_row_hash) DO UPDATE SET
                         net_items_sold=EXCLUDED.net_items_sold,
                         net_sales=EXCLUDED.net_sales,
                         canonical_variant_id=EXCLUDED.canonical_variant_id,
                         resolution_status=EXCLUDED.resolution_status,
                         resolution_method=EXCLUDED.resolution_method,
                         resolution_evidence=EXCLUDED.resolution_evidence,
                         sales_backfill_id=EXCLUDED.sales_backfill_id,
                         fetched_at=now()""",
                    (
                        backfill_id, row.sale_date, row.source_variant_id, row.source_sku,
                        row.source_product_title, row.source_variant_title, row.net_items_sold, row.net_sales,
                        resolution.canonical_variant_id, resolution.status, resolution.method,
                        json.dumps({"candidates": resolution.candidates, **(resolution.evidence or {})}), source_row_hash(row),
                    ),
                )

            cur.execute(
                "DELETE FROM sales_daily WHERE source='SHOPIFYQL_SALES' AND sale_date BETWEEN %s AND %s",
                (start_date, end_date),
            )
            cur.execute(
                """INSERT INTO sales_daily(sale_date,variant_id,units_sold,net_sales,distinct_orders,source)
                   SELECT sale_date,canonical_variant_id,SUM(net_items_sold),SUM(net_sales),NULL,'SHOPIFYQL_SALES'
                   FROM shopify_sales_daily_raw
                   WHERE sale_date BETWEEN %s AND %s AND resolution_status='RESOLVED' AND canonical_variant_id IS NOT NULL
                   GROUP BY sale_date,canonical_variant_id""",
                (start_date, end_date),
            )

            unresolved_material = stats["unresolved"] + stats["ambiguous"]
            status = "PASS" if unresolved_material == 0 else "FAIL"
            evidence = {
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "raw_rows": len(rows),
                **stats, "resolved_units": str(resolved_units), "unresolved_abs_units": str(unresolved_units),
                "source_net_sales": str(source_net_sales), "canonical_net_sales": str(canonical_net_sales),
            }
            cur.execute(
                """UPDATE sales_backfill_runs SET completed_at=now(),status='COMPLETED',raw_rows=%s,resolved_rows=%s,
                     unresolved_rows=%s,ambiguous_rows=%s,resolved_units=%s,unresolved_units=%s,
                     source_net_sales=%s,canonical_net_sales=%s WHERE sales_backfill_id=%s""",
                (len(rows), stats["resolved"], stats["unresolved"], stats["ambiguous"], resolved_units,
                 unresolved_units, source_net_sales, canonical_net_sales, backfill_id),
            )
            cur.execute(
                """INSERT INTO readiness_gates(gate_name,status,severity,blocks_po,evidence_json,message,checked_at)
                   VALUES ('SALES_BACKFILL',%s,'CRITICAL',TRUE,%s::jsonb,%s,now())
                   ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                     status=EXCLUDED.status,evidence_json=EXCLUDED.evidence_json,message=EXCLUDED.message,checked_at=now()""",
                (status, json.dumps(evidence),
                 "Historical sales identity resolution passed." if status == "PASS"
                 else f"{unresolved_material} historical sales rows remain unresolved/ambiguous."),
            )
    return backfill_id


def approve_historical_sales_mapping(conn: Any, source_row: SalesSourceRow, canonical_variant_id: str, *, actor: str, note: str = "") -> None:
    """Persist a difficult historical identity decision so it never needs to be guessed again."""
    import json
    canonical_variant_id = str(canonical_variant_id)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM variants WHERE variant_id=%s", (canonical_variant_id,))
            if not cur.fetchone():
                raise ValueError(f"Unknown canonical Variant ID {canonical_variant_id}")
            sid = numeric_shopify_id(source_row.source_variant_id)
            evidence = {
                "source_key": HistoricalIdentityIndex.source_key(source_row),
                "sale_date_example": source_row.sale_date.isoformat(),
                "source_variant_id": sid,
                "source_sku": source_row.source_sku,
                "source_product_title": source_row.source_product_title,
                "source_variant_title": source_row.source_variant_title,
                "human_note": note,
            }
            cur.execute(
                """INSERT INTO variant_aliases(
                     variant_id,old_variant_id,historical_product_title,historical_variant_title,historical_sku,
                     match_method,confidence,source,notes,approved,approved_by,approved_at,evidence_json
                   ) VALUES (%s,%s,%s,%s,%s,'HUMAN_HISTORICAL_SALES_MAPPING',1.0,'SALES_BACKFILL_REVIEW',%s,TRUE,%s,now(),%s::jsonb)""",
                (canonical_variant_id, sid, source_row.source_product_title, source_row.source_variant_title,
                 source_row.source_sku, note or None, actor, json.dumps(evidence)),
            )
            cur.execute("DELETE FROM historical_sales_exclusions WHERE source_key=%s", (HistoricalIdentityIndex.source_key(source_row),))
            cur.execute(
                """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
                   VALUES ('variant_aliases',%s,'APPROVE',%s::jsonb,%s)""",
                (HistoricalIdentityIndex.source_key(source_row), json.dumps({**evidence, "canonical_variant_id": canonical_variant_id}), actor),
            )


def exclude_historical_sales_identity(conn: Any, source_row: SalesSourceRow, *, actor: str, reason: str) -> None:
    """Explicitly exclude a historical identity from current-item forecasting; never silently drop it."""
    if not reason.strip():
        raise ValueError("Exclusion reason is required")
    key = HistoricalIdentityIndex.source_key(source_row)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO historical_sales_exclusions(
                     source_key,source_variant_id,source_sku,source_product_title,source_variant_title,reason,approved_by,approved_at,active
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,now(),TRUE)
                   ON CONFLICT(source_key) DO UPDATE SET reason=EXCLUDED.reason,approved_by=EXCLUDED.approved_by,approved_at=now(),active=TRUE""",
                (key, numeric_shopify_id(source_row.source_variant_id), source_row.source_sku, source_row.source_product_title,
                 source_row.source_variant_title, reason, actor),
            )


def rerun_sales_identity_resolution(conn: Any, *, start_date: date, end_date: date) -> dict[str, Any]:
    """Re-resolve already-fetched raw sales after aliases/exclusions change; no Shopify call required."""
    identity = load_identity_index(conn)
    counts = {"resolved": 0, "unresolved": 0, "ambiguous": 0, "excluded": 0}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """SELECT raw_sales_id,sale_date,source_variant_id,source_sku,source_product_title,source_variant_title,net_items_sold,net_sales
                   FROM shopify_sales_daily_raw WHERE sale_date BETWEEN %s AND %s ORDER BY raw_sales_id""",
                (start_date, end_date),
            )
            raw = cur.fetchall()
            for r in raw:
                source = SalesSourceRow(r[1], r[2], r[3], r[4], r[5], Decimal(str(r[6])), Decimal(str(r[7])) if r[7] is not None else None)
                resolution = identity.resolve(source)
                counts[resolution.status.lower()] += 1
                cur.execute(
                    """UPDATE shopify_sales_daily_raw SET canonical_variant_id=%s,resolution_status=%s,resolution_method=%s,
                         resolution_evidence=%s::jsonb,fetched_at=now() WHERE raw_sales_id=%s""",
                    (resolution.canonical_variant_id, resolution.status, resolution.method,
                     json.dumps({"candidates": resolution.candidates, **(resolution.evidence or {})}), r[0]),
                )
            cur.execute("DELETE FROM sales_daily WHERE source='SHOPIFYQL_SALES' AND sale_date BETWEEN %s AND %s", (start_date, end_date))
            cur.execute(
                """INSERT INTO sales_daily(sale_date,variant_id,units_sold,net_sales,distinct_orders,source)
                   SELECT sale_date,canonical_variant_id,SUM(net_items_sold),SUM(net_sales),NULL,'SHOPIFYQL_SALES'
                   FROM shopify_sales_daily_raw
                   WHERE sale_date BETWEEN %s AND %s AND resolution_status='RESOLVED' AND canonical_variant_id IS NOT NULL
                   GROUP BY sale_date,canonical_variant_id""",
                (start_date, end_date),
            )
            unresolved = counts["unresolved"] + counts["ambiguous"]
            status = "PASS" if unresolved == 0 else "FAIL"
            evidence = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), **counts}
            cur.execute(
                """INSERT INTO readiness_gates(gate_name,status,severity,blocks_po,evidence_json,message,checked_at)
                   VALUES ('SALES_BACKFILL',%s,'CRITICAL',TRUE,%s::jsonb,%s,now())
                   ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                     status=EXCLUDED.status,evidence_json=EXCLUDED.evidence_json,message=EXCLUDED.message,checked_at=now()""",
                (status, json.dumps(evidence), "Historical sales identity resolution passed." if status == "PASS" else f"{unresolved} historical identities remain unresolved/ambiguous."),
            )
    return {"status": status, **counts}

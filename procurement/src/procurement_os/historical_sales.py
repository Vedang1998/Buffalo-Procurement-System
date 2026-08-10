"""Durable Phase 4 ShopifyQL historical-sales ingestion and reconciliation.

The workflow is deliberately raw-first and fail-closed:

1. create a run and deterministic date chunks;
2. fetch and commit one read-only ShopifyQL page at a time;
3. resolve identities from durable local facts;
4. rebuild the canonical daily aggregate;
5. reconcile independent source, raw, resolution, and canonical controls;
6. let the normal readiness evaluator determine SALES_BACKFILL.

No customer dimensions or Shopify mutations are used here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catalog import numeric_shopify_id
from .sales import (
    HistoricalIdentityIndex,
    IdentityResolution,
    SalesSourceRow,
    date_chunks,
    load_identity_index,
    parse_shopifyql_row,
    source_row_hash,
)
from .shopify.queries import (
    HISTORICAL_SALES_METRICS,
    HISTORICAL_SALES_REQUIRED_COLUMNS,
    SHOPIFYQL_WRAPPER_QUERY,
    SHOP_TIMEZONE_QUERY,
    historical_sales_control_totals_shopifyql,
    historical_sales_shopifyql,
)


AUTHORITATIVE_START_DATE = date(2024, 11, 28)
QUERY_VERSION = "SHOPIFYQL_SALES_V2"
DEFAULT_CHUNK_DAYS = 31
DEFAULT_PAGE_SIZE = 1000
_ADVISORY_LOCK_KEY = 4_256_620_240_004
_MONEY_TOLERANCE = Decimal("0.01")
_UNIT_TOLERANCE = Decimal("0.0001")


@dataclass(frozen=True)
class ControlTotals:
    net_items_sold: Decimal
    net_sales: Decimal


@dataclass(frozen=True)
class ReadinessEvaluation:
    passed: bool
    blockers: tuple[str, ...]


def _decimal(value: Any, *, field: str, null_as_zero: bool = False) -> Decimal:
    if value in (None, ""):
        if null_as_zero:
            return Decimal("0")
        raise ValueError(f"ShopifyQL {field} is null")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"ShopifyQL {field} is not a valid decimal") from exc
    if not result.is_finite():
        raise ValueError(f"ShopifyQL {field} is not finite")
    return result


def _close(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def query_contract_hash() -> str:
    sample = historical_sales_shopifyql("2024-11-28", "2024-11-28", limit=1, offset=0)
    control = historical_sales_control_totals_shopifyql("2024-11-28", "2024-11-28")
    return _hash_text(f"{QUERY_VERSION}\n{sample}\n{control}")


def source_identity_key(row: SalesSourceRow) -> str:
    """Stable review key; contains no metrics and no customer data."""
    return HistoricalIdentityIndex.source_key(row)


def source_payload_hash(row: SalesSourceRow) -> str:
    return _hash_text(_json({
        "source_row_hash": source_row_hash(row),
        "net_items_sold": str(row.net_items_sold),
        "net_sales": str(row.net_sales) if row.net_sales is not None else None,
    }))


def sanitize_error(exc: BaseException) -> tuple[str, str]:
    """Return useful failure evidence without persisting credentials or URLs."""
    error_class = type(exc).__name__
    message = str(exc).replace("\n", " ")[:1000]
    message = re.sub(r"(?i)(bearer|token|secret|password|api[_-]?key)\s*[:=]\s*\S+", r"\1=[REDACTED]", message)
    message = re.sub(r"(?i)postgres(?:ql)?://[^\s]+", "postgresql://[REDACTED]", message)
    message = re.sub(r"\b(?:shpat_|shpca_|sk-|sk-ant-)[A-Za-z0-9_\-]+", "[REDACTED]", message)
    for name in (
        "SHOPIFY_CLIENT_SECRET", "SHOPIFY_ACCESS_TOKEN", "RECONCILIATION_REVIEW_TOKEN",
        "DATABASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    ):
        value = os.getenv(name)
        if value:
            message = message.replace(value, "[REDACTED]")
    return error_class, message or error_class


def evaluate_sales_readiness(evidence: dict[str, Any]) -> ReadinessEvaluation:
    """Pure, central fail-closed evaluator for the global SALES_BACKFILL gate."""
    blockers: list[str] = []
    if str(evidence.get("start_date")) != AUTHORITATIVE_START_DATE.isoformat():
        blockers.append("AUTHORITATIVE_START_DATE_NOT_COVERED")
    if not evidence.get("end_date"):
        blockers.append("REQUESTED_END_DATE_MISSING")
    if not evidence.get("end_is_current_store_date"):
        blockers.append("STORE_LOCAL_END_DATE_NOT_CURRENT")
    expected_chunks = int(evidence.get("expected_chunks") or 0)
    completed_chunks = int(evidence.get("completed_chunks") or 0)
    expected_pages = int(evidence.get("expected_pages") or 0)
    completed_pages = int(evidence.get("completed_pages") or 0)
    if expected_chunks <= 0 or completed_chunks != expected_chunks or not evidence.get("coverage_complete"):
        blockers.append("DATE_COVERAGE_INCOMPLETE")
    if expected_pages <= 0 or completed_pages != expected_pages or not evidence.get("pages_complete"):
        blockers.append("PAGE_COVERAGE_INCOMPLETE")
    if int(evidence.get("unique_source_facts") or 0) <= 0 or not evidence.get("source_facts_persisted"):
        blockers.append("SOURCE_FACTS_NOT_DURABLE")
    if not evidence.get("idempotency_verified"):
        blockers.append("SOURCE_FACT_IDEMPOTENCY_FAILED")
    if not evidence.get("control_totals_reconciled"):
        blockers.append("CONTROL_TOTALS_FAILED")
    if not evidence.get("canonical_aggregate_rebuilt"):
        blockers.append("CANONICAL_AGGREGATE_NOT_REBUILT")
    if not evidence.get("resolution_accounting_reconciled"):
        blockers.append("RESOLUTION_ACCOUNTING_FAILED")

    unresolved_abs_units = _decimal(evidence.get("unresolved_ambiguous_abs_units", 0), field="unresolved units", null_as_zero=True)
    unresolved_abs_sales = _decimal(evidence.get("unresolved_ambiguous_abs_sales", 0), field="unresolved sales", null_as_zero=True)
    if unresolved_abs_units != 0 or unresolved_abs_sales != 0:
        blockers.append("MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED")
    return ReadinessEvaluation(not blockers, tuple(blockers))


def _table_payload(payload: dict[str, Any], *, required_columns: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
    errors = payload.get("parseErrors") or []
    if errors:
        raise RuntimeError(f"ShopifyQL parse error ({len(errors)} issue(s))")
    table = payload.get("tableData")
    if not isinstance(table, dict):
        raise ValueError("ShopifyQL response omitted tableData")
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("ShopifyQL response has an invalid table contract")
    names = [str(column.get("name")) for column in columns if isinstance(column, dict)]
    required = tuple(required_columns)
    if set(names) != set(required) or len(names) != len(required):
        raise ValueError("ShopifyQL response columns do not match the authorized contract")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("ShopifyQL response contains a non-object row")
    return rows, names


def parse_detail_payload(payload: dict[str, Any], *, chunk_start: date, chunk_end: date) -> list[SalesSourceRow]:
    rows, _ = _table_payload(payload, required_columns=HISTORICAL_SALES_REQUIRED_COLUMNS)
    parsed: list[SalesSourceRow] = []
    hashes: set[str] = set()
    for raw in rows:
        item = parse_shopifyql_row(raw)
        if not chunk_start <= item.sale_date <= chunk_end:
            raise ValueError("ShopifyQL row falls outside its requested date chunk")
        key = source_row_hash(item)
        if key in hashes:
            raise ValueError("ShopifyQL page contains a duplicate natural source fact")
        hashes.add(key)
        parsed.append(item)
    return parsed


def parse_control_payload(payload: dict[str, Any]) -> ControlTotals:
    rows, _ = _table_payload(payload, required_columns=HISTORICAL_SALES_METRICS)
    if len(rows) != 1:
        raise ValueError("ShopifyQL control query must return exactly one row")
    return ControlTotals(
        _decimal(rows[0].get("net_items_sold"), field="control net_items_sold", null_as_zero=True),
        _decimal(rows[0].get("net_sales"), field="control net_sales", null_as_zero=True),
    )


def probe_shopifyql_sales_access(client: Any, *, probe_date: date = AUTHORITATIVE_START_DATE) -> ControlTotals:
    query = historical_sales_control_totals_shopifyql(probe_date.isoformat(), probe_date.isoformat())
    response = client.query(SHOPIFYQL_WRAPPER_QUERY, {"query": query})
    payload = response.get("shopifyqlQuery")
    if not isinstance(payload, dict):
        raise ValueError("ShopifyQL probe response omitted shopifyqlQuery")
    return parse_control_payload(payload)


def get_store_timezone(client: Any) -> str:
    response = client.query(SHOP_TIMEZONE_QUERY)
    timezone = ((response.get("shop") or {}).get("ianaTimezone") if isinstance(response, dict) else None)
    if not timezone:
        raise ValueError("Shopify store timezone is unavailable")
    try:
        ZoneInfo(str(timezone))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Shopify store timezone is invalid") from exc
    return str(timezone)


def current_store_date(timezone: str, *, now: datetime | None = None) -> date:
    instant = now or datetime.now(tz=ZoneInfo("UTC"))
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return instant.astimezone(ZoneInfo(timezone)).date()


def run_end_was_current_store_date(
    end_date: date,
    store_timezone: str | None,
    started_at: datetime | None,
) -> bool:
    """Validate the requested end against the durable run-creation instant.

    A completed run must remain locally re-resolvable after the store calendar
    advances.  Readiness therefore records whether the end date was current
    when the run was created, rather than comparing it to wall clock time each
    time a reviewer records a decision.
    """
    if not store_timezone or started_at is None:
        return False
    return end_date == current_store_date(str(store_timezone), now=started_at)


def _set_sales_gate(cur: Any, *, status: str, evidence: dict[str, Any], message: str) -> None:
    cur.execute(
        """INSERT INTO readiness_gates(
             gate_name,scope_type,scope_id,status,severity,blocks_po,evidence_json,message,checked_at
           ) VALUES ('SALES_BACKFILL','GLOBAL','',%s,'CRITICAL',TRUE,%s::jsonb,%s,now())
           ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
             status=EXCLUDED.status,severity='CRITICAL',blocks_po=TRUE,
             evidence_json=EXCLUDED.evidence_json,message=EXCLUDED.message,checked_at=now()""",
        (status, _json(evidence), message),
    )


def assert_catalog_ready(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT status FROM readiness_gates
               WHERE gate_name='CATALOG_SYNC' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        row = cur.fetchone()
    if not row or row[0] != "PASS":
        raise RuntimeError("CATALOG_SYNC is not PASS; historical sales backfill is blocked")


def acquire_backfill_lock(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        acquired = bool(cur.fetchone()[0])
    if not acquired:
        raise RuntimeError("another historical sales backfill holds the execution lock")


def acquire_backfill_transaction_lock(conn: Any) -> None:
    """Hold the shared workflow lock until the surrounding transaction ends."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
        acquired = bool(cur.fetchone()[0])
    if not acquired:
        raise RuntimeError("another historical sales backfill holds the execution lock")


def release_backfill_lock(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))


def create_sales_backfill_run(
    conn: Any,
    *,
    start_date: date,
    end_date: date,
    store_timezone: str,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> str:
    if start_date != AUTHORITATIVE_START_DATE:
        raise ValueError(f"historical sales must start at {AUTHORITATIVE_START_DATE.isoformat()}")
    if end_date < start_date:
        raise ValueError("historical sales end date precedes start date")
    if chunk_days <= 0 or page_size <= 0:
        raise ValueError("chunk_days and page_size must be positive")
    ZoneInfo(store_timezone)
    chunks = list(date_chunks(start_date, end_date, days=chunk_days))
    contract_hash = query_contract_hash()
    with conn.transaction():
        assert_catalog_ready(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sales_backfill_runs(
                     start_date,end_date,query_version,status,store_timezone,chunk_days,page_size,
                     expected_chunks,query_contract_hash,last_checkpoint_at,notes
                   ) VALUES (%s,%s,%s,'RUNNING',%s,%s,%s,%s,%s,now(),%s)
                   RETURNING sales_backfill_id""",
                (start_date, end_date, QUERY_VERSION, store_timezone, chunk_days, page_size,
                 len(chunks), contract_hash, "Phase 4 durable raw-first ShopifyQL backfill"),
            )
            run_id = str(cur.fetchone()[0])
            for index, (chunk_start, chunk_end) in enumerate(chunks):
                cur.execute(
                    """INSERT INTO sales_backfill_chunks(
                         sales_backfill_id,chunk_index,requested_start_date,requested_end_date,
                         query_version,query_contract_hash,status,page_size
                       ) VALUES (%s,%s,%s,%s,%s,%s,'PENDING',%s)""",
                    (run_id, index, chunk_start, chunk_end, QUERY_VERSION, contract_hash, page_size),
                )
            _set_sales_gate(
                cur,
                status="FAIL",
                evidence={"sales_backfill_id": run_id, "stage": "FETCHING", "start_date": start_date,
                          "end_date": end_date, "expected_chunks": len(chunks)},
                message="Historical sales backfill is in progress; readiness remains fail-closed.",
            )
    return run_id


def prepare_resume_run(
    conn: Any,
    run_id: str,
    *,
    start_date: date,
    end_date: date | None = None,
) -> dict[str, Any]:
    with conn.transaction():
        assert_catalog_ready(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT start_date,end_date,store_timezone,chunk_days,page_size,query_version,
                          query_contract_hash,status,started_at
                   FROM sales_backfill_runs WHERE sales_backfill_id=%s FOR UPDATE""",
                (run_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("unknown sales backfill run")
            if row[0] != start_date or (end_date is not None and row[1] != end_date):
                raise ValueError("resume range does not match the durable backfill run")
            if not run_end_was_current_store_date(row[1], row[2], row[8]):
                raise ValueError("durable run end date was not the current store-local date at run creation")
            if row[5] != QUERY_VERSION or row[6] != query_contract_hash():
                raise ValueError("resume query contract does not match the durable backfill run")
            if row[7] == "COMPLETED":
                raise ValueError("completed runs are immutable checkpoints; start a new idempotent rerun")
            cur.execute(
                """SELECT sales_backfill_chunk_id FROM sales_backfill_chunks
                   WHERE sales_backfill_id=%s AND status='FAILED' ORDER BY chunk_index""",
                (run_id,),
            )
            requery_chunk_ids = [str(value[0]) for value in cur.fetchall()]
            if row[7] == "FAILED" and not requery_chunk_ids:
                # A full-range control/finalization failure cannot identify one safe
                # chunk to retain, so re-query the complete snapshot on the same run.
                cur.execute(
                    """SELECT sales_backfill_chunk_id FROM sales_backfill_chunks
                       WHERE sales_backfill_id=%s ORDER BY chunk_index""",
                    (run_id,),
                )
                requery_chunk_ids = [str(value[0]) for value in cur.fetchall()]
            for chunk_id in requery_chunk_ids:
                cur.execute(
                    """DELETE FROM sales_backfill_run_facts
                       WHERE sales_backfill_id=%s
                         AND (first_observed_chunk_id=%s OR last_observed_chunk_id=%s)""",
                    (run_id, chunk_id, chunk_id),
                )
                cur.execute(
                    """UPDATE sales_backfill_pages SET
                         status='PENDING',is_terminal=FALSE,row_count=0,unique_fact_count=0,
                         duplicate_observation_count=0,restated_fact_count=0,
                         source_net_items_sold=0,source_net_sales=0,source_hash=NULL,
                         parse_state='PENDING',
                         parse_evidence=parse_evidence || jsonb_build_object(
                           'resumed_after_attempt',attempt_count,'prior_error_class',error_class),
                         error_class=NULL,sanitized_error_message=NULL,requested_at=NULL,
                         fetched_at=NULL,persisted_at=NULL,completed_at=NULL
                       WHERE sales_backfill_chunk_id=%s""",
                    (chunk_id,),
                )
                cur.execute(
                    """UPDATE sales_backfill_chunks SET status='PENDING',expected_pages=NULL,
                         completed_pages=0,row_count=0,unique_fact_count=0,
                         duplicate_observation_count=0,restated_fact_count=0,
                         source_net_items_sold=0,source_net_sales=0,source_hash=NULL,
                         parse_state='PENDING',control_net_items_sold=NULL,control_net_sales=NULL,
                         control_reconciled=FALSE,
                         control_evidence=control_evidence || jsonb_build_object(
                           'resumed_after_attempt',attempt_count,'prior_error_class',error_class),
                         error_class=NULL,sanitized_error_message=NULL,completed_at=NULL,
                         last_checkpoint_at=now()
                       WHERE sales_backfill_chunk_id=%s""",
                    (chunk_id,),
                )
            cur.execute(
                """UPDATE sales_backfill_runs r SET status='RUNNING',completed_at=NULL,
                     completed_pages=s.completed_pages,source_rows=s.source_rows,
                     completed_chunks=s.completed_chunks,
                     control_evidence=r.control_evidence || %s::jsonb,
                     error_class=NULL,sanitized_error_message=NULL,last_checkpoint_at=now()
                   FROM (
                     SELECT COUNT(*) FILTER (WHERE p.status='COMPLETED')::int completed_pages,
                            COALESCE(SUM(p.row_count) FILTER (WHERE p.status='COMPLETED'),0)::int source_rows,
                            COUNT(DISTINCT c.sales_backfill_chunk_id) FILTER
                              (WHERE c.status='COMPLETED' AND c.control_reconciled)::int completed_chunks
                     FROM sales_backfill_chunks c
                     LEFT JOIN sales_backfill_pages p
                       ON p.sales_backfill_chunk_id=c.sales_backfill_chunk_id
                     WHERE c.sales_backfill_id=%s
                   ) s WHERE r.sales_backfill_id=%s""",
                (_json({"resume_requery_chunk_ids": requery_chunk_ids}), run_id, run_id),
            )
            _set_sales_gate(
                cur, status="FAIL",
                evidence={"sales_backfill_id": run_id, "stage": "RESUMING"},
                message="Historical sales backfill is resuming from durable checkpoints.",
            )
    return {
        "start_date": row[0],
        "end_date": row[1],
        "store_timezone": row[2],
        "chunk_days": int(row[3]),
        "page_size": int(row[4]),
    }


def _resolution_values(resolution: IdentityResolution) -> tuple[str | None, str, str | None, str]:
    evidence = {"candidates": list(resolution.candidates), **(resolution.evidence or {})}
    return resolution.canonical_variant_id, resolution.status, resolution.method, _json(evidence)


def _mark_page_running(
    conn: Any,
    *,
    chunk_id: str,
    page_index: int,
    page_size: int,
    chunk_start: date,
    chunk_end: date,
    contract_hash: str,
) -> tuple[str, str]:
    offset = page_index * page_size
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sales_backfill_pages(
                 sales_backfill_chunk_id,page_index,page_offset,page_limit,requested_start_date,
                 requested_end_date,query_version,query_contract_hash,status,attempt_count,
                 parse_state,requested_at
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'RUNNING',1,'PENDING',now())
               ON CONFLICT(sales_backfill_chunk_id,page_index) DO UPDATE SET
                 status=CASE WHEN sales_backfill_pages.status='COMPLETED' THEN 'COMPLETED' ELSE 'RUNNING' END,
                 attempt_count=CASE WHEN sales_backfill_pages.status='COMPLETED'
                                    THEN sales_backfill_pages.attempt_count
                                    ELSE sales_backfill_pages.attempt_count+1 END,
                 parse_state=CASE WHEN sales_backfill_pages.status='COMPLETED' THEN sales_backfill_pages.parse_state ELSE 'PENDING' END,
                 error_class=NULL,sanitized_error_message=NULL,requested_at=now()
               RETURNING sales_backfill_page_id,status""",
            (chunk_id, page_index, offset, page_size, chunk_start, chunk_end,
             QUERY_VERSION, contract_hash),
        )
        page_id, status = cur.fetchone()
        cur.execute(
            """UPDATE sales_backfill_chunks SET
                 attempt_count=attempt_count+CASE WHEN status='PENDING' AND %s=0 THEN 1 ELSE 0 END,
                 status='RUNNING',started_at=COALESCE(started_at,now()),
                 last_checkpoint_at=now(),error_class=NULL,sanitized_error_message=NULL
               WHERE sales_backfill_chunk_id=%s AND status<>'COMPLETED'""",
            (page_index, chunk_id),
        )
    return str(page_id), str(status)


def _persist_page(
    conn: Any,
    *,
    run_id: str,
    chunk_id: str,
    page_id: str,
    rows: list[SalesSourceRow],
    identity: HistoricalIdentityIndex,
    terminal: bool,
) -> dict[str, Any]:
    units = sum((row.net_items_sold for row in rows), Decimal("0"))
    sales = sum((row.net_sales or Decimal("0") for row in rows), Decimal("0"))
    page_hash = _hash_text("\n".join(sorted(source_payload_hash(row) for row in rows)))
    duplicate_observations = 0
    restated_facts = 0

    with conn.transaction(), conn.cursor() as cur:
        for row in rows:
            natural_hash = source_row_hash(row)
            identity_key = source_identity_key(row)
            resolution = identity.resolve(row)
            canonical_id, status, method, evidence_json = _resolution_values(resolution)
            cur.execute(
                """SELECT raw_sales_id,net_items_sold,net_sales
                   FROM shopify_sales_daily_raw WHERE source_row_hash=%s FOR UPDATE""",
                (natural_hash,),
            )
            prior = cur.fetchone()
            restated = bool(prior and (
                Decimal(str(prior[1])) != row.net_items_sold
                or Decimal(str(prior[2] or 0)) != (row.net_sales or Decimal("0"))
            ))
            if prior:
                raw_id = prior[0]
                cur.execute(
                    """UPDATE shopify_sales_daily_raw SET
                         sales_backfill_id=%s,net_items_sold=%s,net_sales=%s,
                         canonical_variant_id=%s,resolution_status=%s,resolution_method=%s,
                         resolution_evidence=%s::jsonb,source_identity_key=%s,
                         fetched_at=now(),last_fetched_at=now(),fetch_count=fetch_count+1
                       WHERE raw_sales_id=%s""",
                    (run_id, row.net_items_sold, row.net_sales, canonical_id, status, method,
                     evidence_json, identity_key, raw_id),
                )
            else:
                cur.execute(
                    """INSERT INTO shopify_sales_daily_raw(
                         sales_backfill_id,sale_date,source_variant_id,source_sku,source_product_title,
                         source_variant_title,net_items_sold,net_sales,canonical_variant_id,
                         resolution_status,resolution_method,resolution_evidence,source_row_hash,
                         source_identity_key,first_fetched_at,last_fetched_at
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,now(),now())
                       RETURNING raw_sales_id""",
                    (run_id, row.sale_date, row.source_variant_id, row.source_sku,
                     row.source_product_title, row.source_variant_title, row.net_items_sold,
                     row.net_sales, canonical_id, status, method, evidence_json, natural_hash,
                     identity_key),
                )
                raw_id = cur.fetchone()[0]

            cur.execute(
                """INSERT INTO sales_backfill_run_facts(
                     sales_backfill_id,raw_sales_id,source_row_hash,first_observed_chunk_id,
                     first_observed_page_id,last_observed_chunk_id,last_observed_page_id,
                     first_observed_net_items_sold,first_observed_net_sales,
                     observed_net_items_sold,observed_net_sales,restatement_detected
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(sales_backfill_id,raw_sales_id) DO UPDATE SET
                     last_observed_chunk_id=EXCLUDED.last_observed_chunk_id,
                     last_observed_page_id=EXCLUDED.last_observed_page_id,
                     observed_net_items_sold=EXCLUDED.observed_net_items_sold,
                     observed_net_sales=EXCLUDED.observed_net_sales,
                     observation_count=sales_backfill_run_facts.observation_count+1,
                     restatement_detected=sales_backfill_run_facts.restatement_detected
                       OR EXCLUDED.restatement_detected
                       OR sales_backfill_run_facts.observed_net_items_sold<>EXCLUDED.observed_net_items_sold
                       OR sales_backfill_run_facts.observed_net_sales IS DISTINCT FROM EXCLUDED.observed_net_sales,
                     last_observed_at=now()
                   RETURNING observation_count,restatement_detected""",
                (run_id, raw_id, natural_hash, chunk_id, page_id, chunk_id, page_id,
                 row.net_items_sold, row.net_sales, row.net_items_sold, row.net_sales, restated),
            )
            observation_count, restatement_seen = cur.fetchone()
            duplicate_observations += int(int(observation_count) > 1)
            restated_facts += int(bool(restatement_seen))

        cur.execute(
            """UPDATE sales_backfill_pages SET status='COMPLETED',is_terminal=%s,row_count=%s,
                 unique_fact_count=%s,duplicate_observation_count=%s,restated_fact_count=%s,
                 source_net_items_sold=%s,source_net_sales=%s,source_hash=%s,
                 parse_state='PASS',parse_evidence=%s::jsonb,fetched_at=now(),persisted_at=now(),completed_at=now()
               WHERE sales_backfill_page_id=%s""",
            (terminal, len(rows), len(rows), duplicate_observations, restated_facts,
             units, sales, page_hash, _json({"required_columns": HISTORICAL_SALES_REQUIRED_COLUMNS}), page_id),
        )
        cur.execute(
            """UPDATE sales_backfill_chunks c SET
                 completed_pages=s.completed_pages,row_count=s.row_count,
                 unique_fact_count=s.unique_fact_count,
                 duplicate_observation_count=s.duplicate_observation_count,
                 restated_fact_count=s.restated_fact_count,
                 source_net_items_sold=s.net_items,source_net_sales=s.net_sales,
                 expected_pages=CASE WHEN %s THEN s.terminal_expected_pages ELSE c.expected_pages END,
                 parse_state='PASS',last_checkpoint_at=now()
               FROM (
                 SELECT COUNT(*) FILTER (WHERE status='COMPLETED')::int completed_pages,
                        COALESCE(SUM(row_count) FILTER (WHERE status='COMPLETED'),0)::int row_count,
                        COALESCE(SUM(unique_fact_count) FILTER (WHERE status='COMPLETED'),0)::int unique_fact_count,
                        COALESCE(SUM(duplicate_observation_count) FILTER (WHERE status='COMPLETED'),0)::int duplicate_observation_count,
                        COALESCE(SUM(restated_fact_count) FILTER (WHERE status='COMPLETED'),0)::int restated_fact_count,
                        COALESCE(SUM(source_net_items_sold) FILTER (WHERE status='COMPLETED'),0) net_items,
                        COALESCE(SUM(source_net_sales) FILTER (WHERE status='COMPLETED'),0) net_sales,
                        MAX(page_index + 1) FILTER
                          (WHERE status='COMPLETED' AND is_terminal) terminal_expected_pages
                 FROM sales_backfill_pages WHERE sales_backfill_chunk_id=%s
               ) s WHERE c.sales_backfill_chunk_id=%s""",
            (terminal, chunk_id, chunk_id),
        )
        cur.execute(
            """UPDATE sales_backfill_runs r SET
                 completed_pages=s.completed_pages,source_rows=s.source_rows,last_checkpoint_at=now()
               FROM (
                 SELECT COUNT(*) FILTER (WHERE p.status='COMPLETED')::int completed_pages,
                        COALESCE(SUM(p.row_count) FILTER (WHERE p.status='COMPLETED'),0)::int source_rows
                 FROM sales_backfill_pages p
                 JOIN sales_backfill_chunks c
                   ON c.sales_backfill_chunk_id=p.sales_backfill_chunk_id
                 WHERE c.sales_backfill_id=%s
               ) s WHERE r.sales_backfill_id=%s""",
            (run_id, run_id),
        )
    return {"row_count": len(rows), "net_items_sold": units, "net_sales": sales,
            "duplicate_observations": duplicate_observations, "restated_facts": restated_facts}


def _record_page_failure(conn: Any, *, run_id: str, chunk_id: str, page_id: str, exc: BaseException) -> None:
    error_class, message = sanitize_error(exc)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """UPDATE sales_backfill_pages SET status='FAILED',parse_state='FAIL',
                 error_class=%s,sanitized_error_message=%s,parse_evidence=%s::jsonb,completed_at=now()
               WHERE sales_backfill_page_id=%s""",
            (error_class, message, _json({"error_class": error_class}), page_id),
        )
        cur.execute(
            """UPDATE sales_backfill_chunks SET status='PARTIAL',parse_state='FAIL',
                 error_class=%s,sanitized_error_message=%s,last_checkpoint_at=now()
               WHERE sales_backfill_chunk_id=%s""",
            (error_class, message, chunk_id),
        )
        cur.execute(
            """UPDATE sales_backfill_runs SET status=CASE WHEN completed_pages>0 THEN 'PARTIAL' ELSE 'FAILED' END,
                 error_class=%s,sanitized_error_message=%s,last_checkpoint_at=now()
               WHERE sales_backfill_id=%s""",
            (error_class, message, run_id),
        )
        _set_sales_gate(
            cur, status="FAIL",
            evidence={"sales_backfill_id": run_id, "stage": "FETCH_FAILED", "error_class": error_class},
            message=f"Historical sales fetch failed ({error_class}); durable completed pages were retained.",
        )


def _page_coverage_evidence(
    pages: Iterable[tuple[Any, ...]],
    *,
    chunk_start: date | None = None,
    chunk_end: date | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Prove that a chunk has one contiguous, correctly offset page sequence.

    Tuple fields are page_index, page_offset, page_limit, requested_start_date,
    requested_end_date, status, is_terminal, row_count, parse_state, source_hash.
    Merely comparing page counts is insufficient because indexes 0 and 2 could
    otherwise conceal a missing page 1.
    """
    records = list(pages)
    completed = [row for row in records if row[5] == "COMPLETED"]
    terminal = [row for row in completed if bool(row[6])]
    terminal_index = int(terminal[0][0]) if len(terminal) == 1 else None
    expected_pages = terminal_index + 1 if terminal_index is not None else 0
    indexes = [int(row[0]) for row in completed]
    contiguous = indexes == list(range(expected_pages))
    all_completed = len(completed) == len(records)
    one_terminal_at_end = (
        len(terminal) == 1
        and bool(completed)
        and terminal_index == int(completed[-1][0])
    )
    offsets_valid = all(
        int(row[2]) > 0
        and (page_size is None or int(row[2]) == int(page_size))
        and int(row[1]) == int(row[0]) * int(page_size or row[2])
        for row in completed
    )
    row_shapes_valid = all(
        (int(row[7]) < int(row[2]) if bool(row[6]) else int(row[7]) == int(row[2]))
        for row in completed
    )
    ranges_valid = all(
        (chunk_start is None or row[3] == chunk_start)
        and (chunk_end is None or row[4] == chunk_end)
        for row in completed
    )
    parse_complete = all(row[8] == "PASS" and bool(row[9]) for row in completed)
    pages_complete = bool(records) and all((
        all_completed,
        one_terminal_at_end,
        contiguous,
        offsets_valid,
        row_shapes_valid,
        ranges_valid,
        parse_complete,
        len(completed) == expected_pages,
    ))
    return {
        "pages_complete": pages_complete,
        "expected_pages": expected_pages,
        "completed_pages": len(completed),
        "page_indexes_contiguous": contiguous,
        "single_terminal_page": one_terminal_at_end,
        "page_offsets_valid": offsets_valid,
        "page_row_shapes_valid": row_shapes_valid,
        "page_ranges_valid": ranges_valid,
        "page_parse_complete": parse_complete,
    }


def _complete_chunk_control(
    conn: Any,
    *,
    run_id: str,
    chunk_id: str,
    totals: ControlTotals,
) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """SELECT page_index,page_offset,page_limit,requested_start_date,
                      requested_end_date,status,is_terminal,row_count,parse_state,source_hash
               FROM sales_backfill_pages
               WHERE sales_backfill_chunk_id=%s
               ORDER BY page_index""",
            (chunk_id,),
        )
        pages = cur.fetchall()
        cur.execute(
            """SELECT source_net_items_sold,source_net_sales,expected_pages,completed_pages,
                      duplicate_observation_count,parse_state,requested_start_date,
                      requested_end_date,page_size
               FROM sales_backfill_chunks WHERE sales_backfill_chunk_id=%s FOR UPDATE""",
            (chunk_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("unknown sales backfill chunk")
        detail_units = Decimal(str(row[0]))
        detail_sales = Decimal(str(row[1]))
        page_coverage = _page_coverage_evidence(
            pages, chunk_start=row[6], chunk_end=row[7], page_size=int(row[8]),
        )
        pages_complete = bool(page_coverage["pages_complete"]) and row[5] == "PASS"
        page_hashes = [str(value[9]) for value in pages if value[5] == "COMPLETED" and value[9]]
        chunk_source_hash = _hash_text("\n".join(page_hashes))
        controls_match = (
            pages_complete
            and int(row[4]) == 0
            and _close(detail_units, totals.net_items_sold, _UNIT_TOLERANCE)
            and _close(detail_sales, totals.net_sales, _MONEY_TOLERANCE)
        )
        evidence = {
            "detail_net_items_sold": str(detail_units),
            "control_net_items_sold": str(totals.net_items_sold),
            "detail_net_sales": str(detail_sales),
            "control_net_sales": str(totals.net_sales),
            **page_coverage,
            "duplicate_observation_count": int(row[4]),
        }
        cur.execute(
            """UPDATE sales_backfill_chunks SET control_net_items_sold=%s,control_net_sales=%s,
                 control_reconciled=%s,control_evidence=%s::jsonb,
                 source_hash=%s,expected_pages=%s,completed_pages=%s,
                 status=%s,completed_at=CASE WHEN %s THEN now() ELSE completed_at END,
                 error_class=CASE WHEN %s THEN NULL ELSE 'ControlTotalMismatch' END,
                 sanitized_error_message=CASE WHEN %s THEN NULL ELSE 'independent chunk controls did not reconcile' END,
                 last_checkpoint_at=now()
               WHERE sales_backfill_chunk_id=%s""",
            (totals.net_items_sold, totals.net_sales, controls_match, _json(evidence), chunk_source_hash,
             page_coverage["expected_pages"], page_coverage["completed_pages"],
             "COMPLETED" if controls_match else "FAILED", controls_match,
             controls_match, controls_match, chunk_id),
        )
        if not controls_match:
            cur.execute(
                """UPDATE sales_backfill_runs SET status='FAILED',error_class='ControlTotalMismatch',
                     sanitized_error_message='independent chunk controls did not reconcile',last_checkpoint_at=now()
                   WHERE sales_backfill_id=%s""",
                (run_id,),
            )
            _set_sales_gate(
                cur, status="FAIL",
                evidence={"sales_backfill_id": run_id, "stage": "CHUNK_CONTROL_FAILED", **evidence},
                message="Historical sales independent chunk controls did not reconcile.",
            )
    if not controls_match:
        raise RuntimeError("independent ShopifyQL chunk control totals did not reconcile")


def _chunk_rows(conn: Any, run_id: str) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT sales_backfill_chunk_id,chunk_index,requested_start_date,requested_end_date,
                      status,page_size,expected_pages,completed_pages,control_reconciled
               FROM sales_backfill_chunks WHERE sales_backfill_id=%s ORDER BY chunk_index""",
            (run_id,),
        )
        return cur.fetchall()


def _fetch_chunk(
    conn: Any,
    client: Any,
    *,
    run_id: str,
    chunk: tuple[Any, ...],
    identity: HistoricalIdentityIndex,
    max_pages_per_chunk: int,
) -> dict[str, int]:
    chunk_id = str(chunk[0])
    chunk_start, chunk_end = chunk[2], chunk[3]
    page_size = int(chunk[5])
    if chunk[4] == "COMPLETED" and chunk[8]:
        return {"pages": 0, "rows": 0}

    with conn.cursor() as cur:
        cur.execute(
            """SELECT page_index,status,is_terminal FROM sales_backfill_pages
               WHERE sales_backfill_chunk_id=%s ORDER BY page_index""",
            (chunk_id,),
        )
        existing = {int(row[0]): (row[1], bool(row[2])) for row in cur.fetchall()}

    page_index = 0
    persisted_rows = 0
    queried_pages = 0
    while page_index < max_pages_per_chunk:
        state = existing.get(page_index)
        if state and state[0] == "COMPLETED":
            if state[1]:
                break
            page_index += 1
            continue

        page_id, page_status = _mark_page_running(
            conn, chunk_id=chunk_id, page_index=page_index, page_size=page_size,
            chunk_start=chunk_start, chunk_end=chunk_end, contract_hash=query_contract_hash(),
        )
        if page_status == "COMPLETED":
            page_index += 1
            continue
        query = historical_sales_shopifyql(
            chunk_start.isoformat(), chunk_end.isoformat(), limit=page_size,
            offset=page_index * page_size,
        )
        try:
            queried_pages += 1
            response = client.query(SHOPIFYQL_WRAPPER_QUERY, {"query": query})
            payload = response.get("shopifyqlQuery") if isinstance(response, dict) else None
            if not isinstance(payload, dict):
                raise ValueError("ShopifyQL detail response omitted shopifyqlQuery")
            rows = parse_detail_payload(payload, chunk_start=chunk_start, chunk_end=chunk_end)
            terminal = len(rows) < page_size
            _persist_page(
                conn, run_id=run_id, chunk_id=chunk_id, page_id=page_id,
                rows=rows, identity=identity, terminal=terminal,
            )
            persisted_rows += len(rows)
        except BaseException as exc:
            _record_page_failure(conn, run_id=run_id, chunk_id=chunk_id, page_id=page_id, exc=exc)
            raise
        if terminal:
            break
        page_index += 1
    else:
        exc = RuntimeError(f"ShopifyQL pagination exceeded {max_pages_per_chunk} pages")
        _record_page_failure(conn, run_id=run_id, chunk_id=chunk_id, page_id=page_id, exc=exc)
        raise exc

    control_query = historical_sales_control_totals_shopifyql(chunk_start.isoformat(), chunk_end.isoformat())
    try:
        response = client.query(SHOPIFYQL_WRAPPER_QUERY, {"query": control_query})
        payload = response.get("shopifyqlQuery") if isinstance(response, dict) else None
        if not isinstance(payload, dict):
            raise ValueError("ShopifyQL control response omitted shopifyqlQuery")
        controls = parse_control_payload(payload)
        _complete_chunk_control(conn, run_id=run_id, chunk_id=chunk_id, totals=controls)
    except BaseException as exc:
        error_class, message = sanitize_error(exc)
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """UPDATE sales_backfill_chunks SET status='FAILED',control_reconciled=FALSE,
                     error_class=%s,sanitized_error_message=%s,last_checkpoint_at=now()
                   WHERE sales_backfill_chunk_id=%s""",
                (error_class, message, chunk_id),
            )
            cur.execute(
                """UPDATE sales_backfill_runs SET status='FAILED',error_class=%s,
                     sanitized_error_message=%s,last_checkpoint_at=now() WHERE sales_backfill_id=%s""",
                (error_class, message, run_id),
            )
            _set_sales_gate(
                cur, status="FAIL",
                evidence={"sales_backfill_id": run_id, "stage": "CONTROL_QUERY_FAILED", "error_class": error_class},
                message=f"Historical sales control reconciliation failed ({error_class}).",
            )
        raise
    return {"pages": queried_pages, "rows": persisted_rows}


def _coverage_evidence(chunks: list[tuple[Any, ...]], start_date: date, end_date: date) -> dict[str, Any]:
    cursor = start_date
    complete = bool(chunks)
    completed_chunks = 0
    expected_pages = 0
    completed_pages = 0
    for chunk in chunks:
        chunk_start, chunk_end = chunk[2], chunk[3]
        if chunk_start != cursor or chunk_end < chunk_start:
            complete = False
        cursor = chunk_end + (date.resolution)
        if chunk[4] == "COMPLETED" and chunk[8]:
            completed_chunks += 1
        else:
            complete = False
        if chunk[6] is None:
            complete = False
        else:
            expected_pages += int(chunk[6])
        completed_pages += int(chunk[7])
        if chunk[6] is None or int(chunk[6]) != int(chunk[7]):
            complete = False
    if cursor != end_date + date.resolution:
        complete = False
    return {
        "coverage_complete": complete,
        "completed_chunks": completed_chunks,
        "expected_pages": expected_pages,
        "completed_pages": completed_pages,
        "pages_complete": complete and expected_pages > 0 and expected_pages == completed_pages,
    }


def _run_page_structure_evidence(
    conn: Any,
    run_id: str,
    chunks: list[tuple[Any, ...]],
) -> dict[str, Any]:
    """Re-prove structural page completeness from durable page checkpoints."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.sales_backfill_chunk_id,p.page_index,p.page_offset,p.page_limit,
                      p.requested_start_date,p.requested_end_date,p.status,p.is_terminal,
                      p.row_count,p.parse_state,p.source_hash
               FROM sales_backfill_pages p
               JOIN sales_backfill_chunks c
                 ON c.sales_backfill_chunk_id=p.sales_backfill_chunk_id
               WHERE c.sales_backfill_id=%s
               ORDER BY c.chunk_index,p.page_index""",
            (run_id,),
        )
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for row in cur.fetchall():
            grouped.setdefault(str(row[0]), []).append(tuple(row[1:]))

    invalid: list[str] = []
    expected_pages = 0
    completed_pages = 0
    stored_counts_match = True
    for chunk in chunks:
        chunk_id = str(chunk[0])
        item = _page_coverage_evidence(
            grouped.get(chunk_id, []),
            chunk_start=chunk[2],
            chunk_end=chunk[3],
            page_size=int(chunk[5]),
        )
        expected_pages += int(item["expected_pages"])
        completed_pages += int(item["completed_pages"])
        if not item["pages_complete"]:
            invalid.append(chunk_id)
        if chunk[6] is None or (
            int(chunk[6]) != int(item["expected_pages"])
            or int(chunk[7]) != int(item["completed_pages"])
        ):
            stored_counts_match = False
    return {
        "page_structure_complete": bool(chunks) and not invalid and stored_counts_match,
        "page_structure_counts_match": stored_counts_match,
        "structural_expected_pages": expected_pages,
        "structural_completed_pages": completed_pages,
        "invalid_page_structure_chunk_ids": invalid,
    }


def _re_resolve_run_facts(conn: Any, run_id: str, identity: HistoricalIdentityIndex) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.raw_sales_id,r.sale_date,r.source_variant_id,r.source_sku,
                      r.source_product_title,r.source_variant_title,
                      rf.observed_net_items_sold,rf.observed_net_sales
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s ORDER BY r.raw_sales_id""",
            (run_id,),
        )
        facts = cur.fetchall()
        for fact in facts:
            source = SalesSourceRow(
                sale_date=fact[1], source_variant_id=fact[2], source_sku=fact[3],
                source_product_title=fact[4], source_variant_title=fact[5],
                net_items_sold=Decimal(str(fact[6])),
                net_sales=Decimal(str(fact[7])) if fact[7] is not None else None,
            )
            resolution = identity.resolve(source)
            canonical_id, status, method, evidence_json = _resolution_values(resolution)
            cur.execute(
                """UPDATE shopify_sales_daily_raw SET canonical_variant_id=%s,
                     resolution_status=%s,resolution_method=%s,resolution_evidence=%s::jsonb
                   WHERE raw_sales_id=%s""",
                (canonical_id, status, method, evidence_json, fact[0]),
            )


def _status_totals(cur: Any, run_id: str) -> dict[str, Any]:
    cur.execute(
        """SELECT
             COUNT(*)::int,
             COUNT(*) FILTER (WHERE r.resolution_status='RESOLVED')::int,
             COUNT(*) FILTER (WHERE r.resolution_status='UNRESOLVED')::int,
             COUNT(*) FILTER (WHERE r.resolution_status='AMBIGUOUS')::int,
             COUNT(*) FILTER (WHERE r.resolution_status='EXCLUDED')::int,
             COALESCE(SUM(rf.observed_net_items_sold),0),
             COALESCE(SUM(rf.observed_net_sales),0),
             COALESCE(SUM(rf.observed_net_items_sold) FILTER (WHERE r.resolution_status='RESOLVED'),0),
             COALESCE(SUM(rf.observed_net_sales) FILTER (WHERE r.resolution_status='RESOLVED'),0),
             COALESCE(SUM(rf.observed_net_items_sold) FILTER (WHERE r.resolution_status='EXCLUDED'),0),
             COALESCE(SUM(rf.observed_net_sales) FILTER (WHERE r.resolution_status='EXCLUDED'),0),
             COALESCE(SUM(rf.observed_net_items_sold) FILTER (WHERE r.resolution_status='UNRESOLVED'),0),
             COALESCE(SUM(rf.observed_net_sales) FILTER (WHERE r.resolution_status='UNRESOLVED'),0),
             COALESCE(SUM(rf.observed_net_items_sold) FILTER (WHERE r.resolution_status='AMBIGUOUS'),0),
             COALESCE(SUM(rf.observed_net_sales) FILTER (WHERE r.resolution_status='AMBIGUOUS'),0),
             COALESCE(SUM(ABS(rf.observed_net_items_sold)) FILTER
               (WHERE r.resolution_status IN ('UNRESOLVED','AMBIGUOUS')),0),
             COALESCE(SUM(ABS(rf.observed_net_sales)) FILTER
               (WHERE r.resolution_status IN ('UNRESOLVED','AMBIGUOUS')),0),
             COALESCE(SUM(GREATEST(rf.observation_count-1,0)),0)::int
           FROM sales_backfill_run_facts rf
           JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
           WHERE rf.sales_backfill_id=%s""",
        (run_id,),
    )
    row = cur.fetchone()
    keys = (
        "unique_source_facts", "resolved_rows", "unresolved_rows", "ambiguous_rows", "excluded_rows",
        "raw_net_items_sold", "raw_net_sales", "resolved_net_items_sold", "resolved_net_sales",
        "excluded_net_items_sold", "excluded_net_sales", "unresolved_net_items_sold",
        "unresolved_net_sales", "ambiguous_net_items_sold", "ambiguous_net_sales",
        "unresolved_ambiguous_abs_units", "unresolved_ambiguous_abs_sales", "duplicate_observations",
    )
    return dict(zip(keys, row, strict=True))


def _finalize_sales_backfill_unlocked(
    conn: Any,
    *,
    run_id: str,
    independent_totals: ControlTotals | None = None,
) -> dict[str, Any]:
    """Resolve and rebuild entirely from local durable facts, then evaluate readiness."""
    assert_catalog_ready(conn)
    identity = load_identity_index(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """SELECT start_date,end_date,store_timezone,expected_chunks,source_net_items_sold,
                      source_net_sales,started_at FROM sales_backfill_runs
               WHERE sales_backfill_id=%s FOR UPDATE""",
            (run_id,),
        )
        run = cur.fetchone()
        if not run:
            raise ValueError("unknown sales backfill run")
        start_date, end_date, store_timezone = run[0], run[1], run[2]
        if independent_totals is None:
            if run[4] is None or run[5] is None:
                raise RuntimeError("stored independent source controls are unavailable")
            independent_totals = ControlTotals(Decimal(str(run[4])), Decimal(str(run[5])))

        _re_resolve_run_facts(conn, run_id, identity)
        totals = _status_totals(cur, run_id)
        cur.execute(
            """SELECT COALESCE(SUM(row_count),0)::int,
                      COALESCE(SUM(control_net_items_sold),0),
                      COALESCE(SUM(control_net_sales),0)
               FROM sales_backfill_chunks WHERE sales_backfill_id=%s""",
            (run_id,),
        )
        source_rows, chunk_control_units, chunk_control_sales = cur.fetchone()
        chunk_control_units = Decimal(str(chunk_control_units))
        chunk_control_sales = Decimal(str(chunk_control_sales))
        chunks = _chunk_rows(conn, run_id)
        coverage = _coverage_evidence(chunks, start_date, end_date)
        page_structure = _run_page_structure_evidence(conn, run_id, chunks)
        coverage["coverage_complete"] = bool(
            coverage["coverage_complete"] and page_structure["page_structure_complete"]
        )
        coverage["pages_complete"] = bool(
            coverage["pages_complete"] and page_structure["page_structure_complete"]
        )
        coverage.update(page_structure)

        raw_units = Decimal(str(totals["raw_net_items_sold"]))
        raw_sales = Decimal(str(totals["raw_net_sales"]))
        accounted_units = sum((Decimal(str(totals[key])) for key in (
            "resolved_net_items_sold", "excluded_net_items_sold",
            "unresolved_net_items_sold", "ambiguous_net_items_sold",
        )), Decimal("0"))
        accounted_sales = sum((Decimal(str(totals[key])) for key in (
            "resolved_net_sales", "excluded_net_sales", "unresolved_net_sales", "ambiguous_net_sales",
        )), Decimal("0"))
        source_controls_match = (
            _close(independent_totals.net_items_sold, chunk_control_units, _UNIT_TOLERANCE)
            and _close(independent_totals.net_sales, chunk_control_sales, _MONEY_TOLERANCE)
            and _close(independent_totals.net_items_sold, raw_units, _UNIT_TOLERANCE)
            and _close(independent_totals.net_sales, raw_sales, _MONEY_TOLERANCE)
        )
        resolution_accounting = (
            _close(raw_units, accounted_units, _UNIT_TOLERANCE)
            and _close(raw_sales, accounted_sales, _MONEY_TOLERANCE)
        )
        idempotency = int(totals["duplicate_observations"]) == 0 and int(source_rows) == int(totals["unique_source_facts"])
        facts_persisted = int(totals["unique_source_facts"]) > 0 and int(source_rows) == int(totals["unique_source_facts"])
        source_valid_for_rebuild = (
            coverage["coverage_complete"] and coverage["pages_complete"]
            and source_controls_match and resolution_accounting
            and idempotency and facts_persisted
        )
        aggregate_rebuilt = False
        if source_valid_for_rebuild:
            cur.execute(
                "DELETE FROM sales_daily WHERE source='SHOPIFYQL_SALES' AND sale_date BETWEEN %s AND %s",
                (start_date, end_date),
            )
            cur.execute(
                """INSERT INTO sales_daily(sale_date,variant_id,units_sold,net_sales,distinct_orders,source)
                   SELECT r.sale_date,r.canonical_variant_id,SUM(rf.observed_net_items_sold),
                          SUM(rf.observed_net_sales),NULL,'SHOPIFYQL_SALES'
                   FROM sales_backfill_run_facts rf
                   JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
                   WHERE rf.sales_backfill_id=%s AND r.resolution_status='RESOLVED'
                     AND r.canonical_variant_id IS NOT NULL
                   GROUP BY r.sale_date,r.canonical_variant_id""",
                (run_id,),
            )
            aggregate_rebuilt = True
        cur.execute(
            """SELECT COALESCE(SUM(units_sold),0),COALESCE(SUM(net_sales),0)
               FROM sales_daily WHERE source='SHOPIFYQL_SALES' AND sale_date BETWEEN %s AND %s""",
            (start_date, end_date),
        )
        canonical_units, canonical_sales = (Decimal(str(value)) for value in cur.fetchone())
        canonical_reconciled = (
            aggregate_rebuilt
            and _close(canonical_units, Decimal(str(totals["resolved_net_items_sold"])), _UNIT_TOLERANCE)
            and _close(canonical_sales, Decimal(str(totals["resolved_net_sales"])), _MONEY_TOLERANCE)
        )
        end_was_current = run_end_was_current_store_date(end_date, store_timezone, run[6])

        evidence: dict[str, Any] = {
            "sales_backfill_id": run_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "store_timezone": store_timezone,
            # Keep the established readiness key, but make it immutable evidence
            # tied to run creation so local review remains possible after midnight.
            "end_is_current_store_date": end_was_current,
            "end_was_current_store_date_at_run_start": end_was_current,
            "run_started_at": run[6].isoformat() if run[6] is not None else None,
            "expected_chunks": int(run[3]),
            **coverage,
            "source_rows": int(source_rows),
            **{key: (int(value) if key.endswith("_rows") or key in {"unique_source_facts", "duplicate_observations"}
                     else str(value)) for key, value in totals.items()},
            "source_net_items_sold": str(independent_totals.net_items_sold),
            "source_net_sales": str(independent_totals.net_sales),
            "chunk_control_net_items_sold": str(chunk_control_units),
            "chunk_control_net_sales": str(chunk_control_sales),
            "canonical_net_items_sold": str(canonical_units),
            "canonical_net_sales": str(canonical_sales),
            "source_facts_persisted": facts_persisted,
            "idempotency_verified": idempotency,
            "source_controls_reconciled": source_controls_match,
            "resolution_accounting_reconciled": resolution_accounting,
            "canonical_controls_reconciled": canonical_reconciled,
            "control_totals_reconciled": source_controls_match and resolution_accounting and canonical_reconciled,
            "canonical_aggregate_rebuilt": aggregate_rebuilt,
        }
        readiness = evaluate_sales_readiness(evidence)
        run_status = "COMPLETED" if coverage["coverage_complete"] and evidence["control_totals_reconciled"] else "FAILED"
        cur.execute(
            """UPDATE sales_backfill_runs SET completed_at=COALESCE(completed_at,now()),status=%s,
                 raw_rows=%s,resolved_rows=%s,unresolved_rows=%s,ambiguous_rows=%s,excluded_rows=%s,
                 source_rows=%s,unique_source_facts=%s,resolved_units=%s,unresolved_units=%s,
                 source_net_items_sold=%s,source_net_sales=%s,raw_net_items_sold=%s,raw_net_sales=%s,
                 canonical_net_items_sold=%s,canonical_net_sales=%s,
                 excluded_net_items_sold=%s,excluded_net_sales=%s,
                 unresolved_net_items_sold=%s,unresolved_net_sales=%s,
                 ambiguous_net_items_sold=%s,ambiguous_net_sales=%s,
                 completed_chunks=%s,expected_pages=%s,completed_pages=%s,
                 coverage_complete=%s,pages_complete=%s,source_facts_persisted=%s,
                 idempotency_verified=%s,control_totals_reconciled=%s,
                 canonical_aggregate_rebuilt=%s,control_evidence=%s::jsonb,
                 error_class=%s,sanitized_error_message=%s,last_checkpoint_at=now()
               WHERE sales_backfill_id=%s""",
            (run_status, totals["unique_source_facts"], totals["resolved_rows"], totals["unresolved_rows"],
             totals["ambiguous_rows"], totals["excluded_rows"], source_rows, totals["unique_source_facts"],
             totals["resolved_net_items_sold"], totals["unresolved_ambiguous_abs_units"],
             independent_totals.net_items_sold, independent_totals.net_sales, raw_units, raw_sales,
             canonical_units, canonical_sales, totals["excluded_net_items_sold"], totals["excluded_net_sales"],
             totals["unresolved_net_items_sold"], totals["unresolved_net_sales"],
             totals["ambiguous_net_items_sold"], totals["ambiguous_net_sales"],
             coverage["completed_chunks"], coverage["expected_pages"], coverage["completed_pages"],
             coverage["coverage_complete"], coverage["pages_complete"], facts_persisted, idempotency,
             evidence["control_totals_reconciled"], aggregate_rebuilt, _json(evidence),
             None if run_status == "COMPLETED" else "ControlOrCoverageFailure",
             None if run_status == "COMPLETED" else "coverage or control reconciliation failed", run_id),
        )
        gate_status = "PASS" if readiness.passed else "FAIL"
        _set_sales_gate(
            cur, status=gate_status, evidence={**evidence, "blockers": readiness.blockers},
            message=("Historical ShopifyQL sales backfill, identity accounting, and controls passed."
                     if readiness.passed else
                     f"Historical sales remains blocked: {', '.join(readiness.blockers)}."),
        )
    return {"status": "PASS" if readiness.passed else "FAIL", "run_status": run_status,
            "blockers": list(readiness.blockers), **evidence}


def finalize_sales_backfill(
    conn: Any,
    *,
    run_id: str,
    independent_totals: ControlTotals | None = None,
) -> dict[str, Any]:
    """Serialize a local rebuild through its actual database commit boundary."""
    with conn.transaction():
        acquire_backfill_transaction_lock(conn)
        return _finalize_sales_backfill_unlocked(
            conn, run_id=run_id, independent_totals=independent_totals,
        )


def _fetch_control_totals(client: Any, start_date: date, end_date: date) -> ControlTotals:
    query = historical_sales_control_totals_shopifyql(start_date.isoformat(), end_date.isoformat())
    response = client.query(SHOPIFYQL_WRAPPER_QUERY, {"query": query})
    payload = response.get("shopifyqlQuery") if isinstance(response, dict) else None
    if not isinstance(payload, dict):
        raise ValueError("ShopifyQL control response omitted shopifyqlQuery")
    return parse_control_payload(payload)


def _record_run_failure(conn: Any, run_id: str, exc: BaseException) -> None:
    error_class, message = sanitize_error(exc)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """UPDATE sales_backfill_runs SET
                 status=CASE WHEN completed_pages>0 THEN 'PARTIAL' ELSE 'FAILED' END,
                 error_class=%s,sanitized_error_message=%s,last_checkpoint_at=now()
               WHERE sales_backfill_id=%s AND status<>'COMPLETED'""",
            (error_class, message, run_id),
        )
        _set_sales_gate(
            cur, status="FAIL",
            evidence={"sales_backfill_id": run_id, "stage": "FAILED", "error_class": error_class},
            message=f"Historical sales backfill failed ({error_class}); readiness remains fail-closed.",
        )


def run_historical_sales_backfill(
    conn: Any,
    client: Any,
    *,
    start_date: date = AUTHORITATIVE_START_DATE,
    end_date: date | None = None,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages_per_chunk: int = 1000,
    resume_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute the authorized read-only ShopifyQL backfill with durable checkpoints."""
    if max_pages_per_chunk <= 0:
        raise ValueError("max_pages_per_chunk must be positive")
    if hasattr(conn, "autocommit") and not conn.autocommit:
        raise ValueError("durable historical backfill requires an autocommit connection")
    acquire_backfill_lock(conn)
    run_id: str | None = None
    try:
        assert_catalog_ready(conn)
        store_timezone: str | None = None
        requested_end: date | None = None

        # Access is proved before a mutable run is created or resumed.  Timezone
        # discovery is part of the same fail-closed external-access stage.  A
        # resume uses its durable range/timezone and does not silently extend at
        # the next store-local midnight.
        try:
            if not resume_run_id:
                store_timezone = get_store_timezone(client)
            probe_shopifyql_sales_access(client)
        except BaseException as exc:
            error_class, message = sanitize_error(exc)
            with conn.transaction(), conn.cursor() as cur:
                _set_sales_gate(
                    cur, status="FAIL",
                    evidence={"stage": "SHOPIFYQL_ACCESS_FAILED", "error_class": error_class},
                    message=f"ShopifyQL historical-sales access failed ({error_class}): {message}",
                )
            raise

        if resume_run_id:
            run_id = str(resume_run_id)
            settings = prepare_resume_run(
                conn, run_id, start_date=start_date, end_date=end_date,
            )
            requested_end = settings["end_date"]
            store_timezone = str(settings["store_timezone"])
            chunk_days = int(settings["chunk_days"])
            page_size = int(settings["page_size"])
        else:
            assert store_timezone is not None
            requested_end = end_date or current_store_date(store_timezone)
            if requested_end != current_store_date(store_timezone):
                raise ValueError("historical sales end date must be the current Shopify store-local date")
            run_id = create_sales_backfill_run(
                conn, start_date=start_date, end_date=requested_end,
                store_timezone=store_timezone, chunk_days=chunk_days, page_size=page_size,
            )

        identity = load_identity_index(conn)
        fetched_pages = 0
        fetched_rows = 0
        for chunk in _chunk_rows(conn, run_id):
            result = _fetch_chunk(
                conn, client, run_id=run_id, chunk=chunk, identity=identity,
                max_pages_per_chunk=max_pages_per_chunk,
            )
            fetched_pages += result["pages"]
            fetched_rows += result["rows"]

        assert requested_end is not None
        full_controls = _fetch_control_totals(client, start_date, requested_end)
        result = finalize_sales_backfill(conn, run_id=run_id, independent_totals=full_controls)
        return {
            **result,
            "sales_backfill_id": run_id,
            "store_timezone": store_timezone,
            "fetched_pages_this_execution": fetched_pages,
            "fetched_rows_this_execution": fetched_rows,
            "chunk_days": chunk_days,
            "page_size": page_size,
        }
    except BaseException as exc:
        if run_id:
            _record_run_failure(conn, run_id, exc)
        raise
    finally:
        release_backfill_lock(conn)


def _latest_reviewable_run(cur: Any) -> tuple[str, date, date] | None:
    cur.execute(
        """SELECT sales_backfill_id,start_date,end_date
           FROM sales_backfill_runs
           WHERE source='SHOPIFYQL_SALES' AND status='COMPLETED'
             AND unique_source_facts>0 AND coverage_complete=TRUE AND pages_complete=TRUE
             AND source_facts_persisted=TRUE AND idempotency_verified=TRUE
             AND control_totals_reconciled=TRUE AND canonical_aggregate_rebuilt=TRUE
           ORDER BY started_at DESC LIMIT 1"""
    )
    row = cur.fetchone()
    return (str(row[0]), row[1], row[2]) if row else None


def get_historical_sales_review_items(conn: Any) -> list[dict[str, Any]]:
    """Return unresolved source identities grouped and ranked by materiality."""
    with conn.cursor() as cur:
        run = _latest_reviewable_run(cur)
        if not run:
            return []
        run_id = run[0]
        cur.execute(
            """SELECT r.source_identity_key,MIN(r.source_variant_id),MIN(r.source_sku),
                      MIN(r.source_product_title),MIN(r.source_variant_title),
                      MIN(r.sale_date),MAX(r.sale_date),COUNT(*)::int,
                      COALESCE(SUM(rf.observed_net_items_sold),0),
                      COALESCE(SUM(ABS(rf.observed_net_items_sold)),0),
                      COALESCE(SUM(rf.observed_net_sales),0),
                      COALESCE(SUM(ABS(rf.observed_net_sales)),0),
                      ARRAY_AGG(DISTINCT r.resolution_status ORDER BY r.resolution_status),
                      ARRAY_AGG(DISTINCT r.resolution_method) FILTER (WHERE r.resolution_method IS NOT NULL),
                      JSONB_AGG(DISTINCT r.resolution_evidence)
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s
                 AND r.resolution_status IN ('UNRESOLVED','AMBIGUOUS')
               GROUP BY r.source_identity_key
               ORDER BY COALESCE(SUM(ABS(rf.observed_net_items_sold)),0) DESC,
                        COALESCE(SUM(ABS(rf.observed_net_sales)),0) DESC,
                        r.source_identity_key""",
            (run_id,),
        )
        groups = cur.fetchall()
        source_keys = [row[0] for row in groups]
        decisions: dict[str, dict[str, Any]] = {}
        if source_keys:
            cur.execute(
                """SELECT DISTINCT ON (source_identity_key) source_identity_key,decision_action,
                          canonical_variant_id,actor,reason,decided_at
                   FROM historical_sales_review_decisions
                   WHERE source_identity_key=ANY(%s)
                   ORDER BY source_identity_key,decided_at DESC""",
                (source_keys,),
            )
            decisions = {
                row[0]: {"action": row[1], "canonical_variant_id": row[2], "actor": row[3],
                         "reason": row[4], "decided_at": str(row[5])}
                for row in cur.fetchall()
            }

        candidate_ids: set[str] = set()
        group_candidates: list[list[str]] = []
        for row in groups:
            candidates: set[str] = set()
            for evidence in row[14] or []:
                if isinstance(evidence, dict):
                    candidates.update(str(value) for value in (evidence.get("candidates") or []) if value)
            ordered = sorted(candidates)
            group_candidates.append(ordered)
            candidate_ids.update(ordered)
        candidate_rows: dict[str, dict[str, Any]] = {}
        if candidate_ids:
            cur.execute(
                """SELECT variant_id,product_title,variant_title,sku,catalog_state,active
                   FROM variants WHERE variant_id=ANY(%s)""",
                (sorted(candidate_ids),),
            )
            candidate_rows = {
                str(row[0]): {"canonical_variant_id": str(row[0]), "product_title": row[1],
                              "variant_title": row[2], "sku": row[3], "catalog_state": row[4],
                              "active": bool(row[5])}
                for row in cur.fetchall()
            }

    items: list[dict[str, Any]] = []
    for row, candidates in zip(groups, group_candidates, strict=True):
        evidence_rows = [value for value in (row[14] or []) if isinstance(value, dict)]
        conflicts: list[Any] = []
        for evidence in evidence_rows:
            conflicts.extend(evidence.get("conflicts") or [])
        abs_units = Decimal(str(row[9]))
        abs_sales = Decimal(str(row[11]))
        material = abs_units != 0 or abs_sales != 0
        items.append({
            "sales_backfill_id": run_id,
            "source_identity_key": row[0],
            "source_key": row[0],
            "source_variant_id": row[1],
            "historical_sku": row[2],
            "historical_product_title": row[3],
            "historical_variant_title": row[4],
            "first_sale_date": row[5].isoformat(),
            "last_sale_date": row[6].isoformat(),
            "affected_raw_rows": int(row[7]),
            "net_units": str(row[8]),
            "absolute_unit_magnitude": str(abs_units),
            "net_sales": str(row[10]),
            "absolute_sales_magnitude": str(abs_sales),
            "resolution_status": "/".join(row[12] or []),
            "resolution_methods": list(row[13] or []),
            "material": material,
            "materiality": "MATERIAL" if material else "NONMATERIAL",
            "candidate_canonical_variants": [candidate_rows.get(value, {"canonical_variant_id": value}) for value in candidates],
            "evidence": evidence_rows,
            "conflicts": conflicts,
            "latest_human_decision": decisions.get(row[0]),
        })
    return items


def _source_for_decision(cur: Any, source_key: str) -> tuple[str, SalesSourceRow]:
    run = _latest_reviewable_run(cur)
    if not run:
        raise ValueError("no complete historical-sales source run is available for review")
    cur.execute(
        """SELECT r.sale_date,r.source_variant_id,r.source_sku,r.source_product_title,
                  r.source_variant_title,rf.observed_net_items_sold,rf.observed_net_sales,
                  r.resolution_status
           FROM sales_backfill_run_facts rf
           JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
           WHERE rf.sales_backfill_id=%s AND r.source_identity_key=%s
           ORDER BY r.sale_date,r.raw_sales_id LIMIT 1 FOR UPDATE OF r""",
        (run[0], source_key),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("historical source identity is not present in the current review run")
    if row[7] not in {"UNRESOLVED", "AMBIGUOUS"}:
        raise ValueError("historical source identity is no longer unresolved")
    source = SalesSourceRow(row[0], row[1], row[2], row[3], row[4],
                            Decimal(str(row[5])), Decimal(str(row[6])) if row[6] is not None else None)
    return run[0], source


def _record_historical_sales_review_decision_unlocked(
    conn: Any,
    *,
    source_key: str,
    action: str,
    canonical_variant_id: str | None,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Append one human decision, then re-resolve locally without Shopify access."""
    action_map = {
        "MAP": "MAP",
        "MAP_TO_CANONICAL": "MAP",
        "EXCLUDE": "EXCLUDE",
        "EXCLUDE_HISTORICAL_ITEM": "EXCLUDE",
        "LEAVE_UNRESOLVED": "LEAVE_UNRESOLVED",
    }
    normalized_action = action_map.get(str(action).strip().upper())
    actor = str(actor).strip()
    reason = str(reason).strip()
    source_key = str(source_key).strip()
    if not normalized_action:
        raise ValueError("unknown historical-sales decision action")
    if not source_key or not actor or not reason:
        raise ValueError("source identity, actor, and reason are required")
    if normalized_action == "MAP" and not canonical_variant_id:
        raise ValueError("canonical Variant ID is required for mapping")
    if normalized_action != "MAP":
        canonical_variant_id = None

    with conn.transaction(), conn.cursor() as cur:
        run_id, source = _source_for_decision(cur, source_key)
        cur.execute(
            """SELECT historical_sales_review_decision_id
               FROM historical_sales_review_decisions WHERE source_identity_key=%s
               ORDER BY decided_at DESC LIMIT 1""",
            (source_key,),
        )
        prior = cur.fetchone()
        evidence = {
            "source_identity_key": source_key,
            "source_variant_id": source.source_variant_id,
            "source_sku": source.source_sku,
            "source_product_title": source.source_product_title,
            "source_variant_title": source.source_variant_title,
            "human_reason": reason,
        }

        if normalized_action == "MAP":
            canonical_variant_id = str(canonical_variant_id).strip()
            cur.execute("SELECT 1 FROM variants WHERE variant_id=%s", (canonical_variant_id,))
            if not cur.fetchone():
                raise ValueError("unknown canonical Variant ID")
            # ShopifyQL's explicit zero bucket means "no usable Variant ID".
            # Persisting it as an old-ID alias would incorrectly bind every
            # unrelated zero-ID history group to the first reviewed target.
            old_id = numeric_shopify_id(source.source_variant_id)
            if old_id == "0":
                old_id = None
            cur.execute(
                """SELECT DISTINCT variant_id FROM variant_aliases
                   WHERE approved=TRUE AND (
                     (old_variant_id IS NOT NULL AND old_variant_id=%s)
                     OR (LOWER(COALESCE(historical_sku,''))=LOWER(COALESCE(%s,''))
                         AND LOWER(COALESCE(historical_product_title,''))=LOWER(COALESCE(%s,''))
                         AND LOWER(COALESCE(historical_variant_title,''))=LOWER(COALESCE(%s,'')))
                   )""",
                (old_id, source.source_sku, source.source_product_title, source.source_variant_title),
            )
            existing_targets = {str(row[0]) for row in cur.fetchall()}
            if existing_targets - {canonical_variant_id}:
                raise ValueError("approved historical identity evidence already points to a different canonical Variant ID")
            if canonical_variant_id not in existing_targets:
                cur.execute(
                    """INSERT INTO variant_aliases(
                         variant_id,old_variant_id,historical_product_title,historical_variant_title,
                         historical_sku,match_method,confidence,source,notes,approved,
                         approved_by,approved_at,evidence_json
                       ) VALUES (%s,%s,%s,%s,%s,'HUMAN_HISTORICAL_SALES_MAPPING',1.0,
                                 'SALES_BACKFILL_REVIEW',%s,TRUE,%s,now(),%s::jsonb)""",
                    (canonical_variant_id, old_id, source.source_product_title,
                     source.source_variant_title, source.source_sku, reason, actor, _json(evidence)),
                )
            cur.execute(
                "UPDATE historical_sales_exclusions SET active=FALSE WHERE source_key=%s AND active=TRUE",
                (source_key,),
            )
            change_table = "variant_aliases"
        elif normalized_action == "EXCLUDE":
            cur.execute(
                """INSERT INTO historical_sales_exclusions(
                     source_key,source_variant_id,source_sku,source_product_title,
                     source_variant_title,reason,approved_by,approved_at,active
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,now(),TRUE)
                   ON CONFLICT(source_key) DO UPDATE SET reason=EXCLUDED.reason,
                     approved_by=EXCLUDED.approved_by,approved_at=now(),active=TRUE""",
                (source_key, source.source_variant_id, source.source_sku,
                 source.source_product_title, source.source_variant_title, reason, actor),
            )
            change_table = "historical_sales_exclusions"
        else:
            change_table = "historical_sales_review_decisions"

        # Prove the persisted decision has the exact resolver effect requested
        # before writing its audit record.  This catches normalization-equivalent
        # conflicting aliases and any future resolver/index drift; an exception
        # rolls the alias/exclusion mutation back with the surrounding transaction.
        decision_resolution = load_identity_index(conn).resolve(source)
        if normalized_action == "MAP" and not (
            decision_resolution.status == "RESOLVED"
            and decision_resolution.canonical_variant_id == canonical_variant_id
        ):
            raise ValueError("approved mapping did not resolve uniquely to the requested canonical Variant ID")
        if normalized_action == "EXCLUDE" and decision_resolution.status != "EXCLUDED":
            raise ValueError("approved exclusion did not produce an excluded historical identity")
        if normalized_action == "LEAVE_UNRESOLVED" and decision_resolution.status not in {
            "UNRESOLVED", "AMBIGUOUS",
        }:
            raise ValueError("historical source identity is no longer unresolved")
        evidence["verified_resolution"] = {
            "status": decision_resolution.status,
            "canonical_variant_id": decision_resolution.canonical_variant_id,
            "method": decision_resolution.method,
        }

        cur.execute(
            """INSERT INTO historical_sales_review_decisions(
                 sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                 source_product_title,source_variant_title,decision_action,canonical_variant_id,
                 actor,reason,evidence_json,supersedes_decision_id
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
               RETURNING historical_sales_review_decision_id""",
            (run_id, source_key, source.source_variant_id, source.source_sku,
             source.source_product_title, source.source_variant_title, normalized_action,
             canonical_variant_id, actor, reason, _json(evidence), prior[0] if prior else None),
        )
        decision_id = str(cur.fetchone()[0])
        cur.execute(
            """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
               VALUES (%s,%s,'APPROVE',%s::jsonb,%s)""",
            (change_table, source_key, _json({**evidence, "decision_id": decision_id,
                                             "action": normalized_action,
                                             "canonical_variant_id": canonical_variant_id}), actor),
        )

    readiness = _finalize_sales_backfill_unlocked(conn, run_id=run_id)
    return {"decision_id": decision_id, "action": normalized_action,
            "source_identity_key": source_key, "readiness": readiness}


def record_historical_sales_review_decision(
    conn: Any,
    *,
    source_key: str,
    action: str,
    canonical_variant_id: str | None,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Commit the decision, local rebuild, and gate update under one xact lock."""
    with conn.transaction():
        acquire_backfill_transaction_lock(conn)
        return _record_historical_sales_review_decision_unlocked(
            conn, source_key=source_key, action=action,
            canonical_variant_id=canonical_variant_id, actor=actor, reason=reason,
        )

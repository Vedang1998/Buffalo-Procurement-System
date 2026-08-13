from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Any, Iterable

from .shopify.queries import ACTIVE_CATALOG_FILTER, CATALOG_COUNT_QUERY, CATALOG_PAGE_QUERY


def numeric_shopify_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == "0":
        return None
    if s.startswith("gid://"):
        s = s.rsplit("/", 1)[-1]
    return s or None


@dataclass(frozen=True)
class LiveVariant:
    variant_id: str
    shopify_gid: str
    product_id: str
    product_gid: str
    product_title: str
    variant_title: str
    handle: str | None
    status: str | None
    sku: str | None
    barcode: str | None
    retail_price: Decimal | None
    shopify_current_cost: Decimal | None
    inventory_quantity: Decimal | None
    inventory_item_gid: str | None
    inventory_tracked: bool | None
    shopify_vendor: str | None
    product_type: str | None
    variant_created_at: str | None
    variant_updated_at: str | None
    product_updated_at: str | None


@dataclass(frozen=True)
class SeedVariant:
    variant_id: str
    product_id: str
    product_title: str
    variant_title: str
    sku: str | None
    barcode: str | None
    active: bool = True


@dataclass(frozen=True)
class CatalogIssue:
    classification: str
    seed_variant_id: str | None
    live_variant_id: str | None
    blocking: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class CatalogReconciliation:
    exact_ids: int
    new_live: int
    missing_seed: int
    potential_recreations: int
    changed_attributes: int
    issues: tuple[CatalogIssue, ...]

    @property
    def blockers(self) -> tuple[CatalogIssue, ...]:
        return tuple(i for i in self.issues if i.blocking)

    @property
    def can_pass_catalog_gate(self) -> bool:
        return not self.blockers


def catalog_gate_blockers(
    *,
    live_rows_received: int,
    exact_current_ids: int,
    new_live_variants: int,
    unresolved_blockers: int,
    pagination_complete: bool,
    source_hash: str | None,
) -> tuple[str, ...]:
    """Evaluate the durable evidence required for CATALOG_SYNC to pass."""
    blockers: list[str] = []
    if not pagination_complete:
        blockers.append("PAGINATION_INCOMPLETE")
    if live_rows_received <= 0:
        blockers.append("NO_LIVE_VARIANTS")
    if exact_current_ids + new_live_variants != live_rows_received:
        blockers.append("LIVE_IDENTITY_ACCOUNTING_MISMATCH")
    if not source_hash:
        blockers.append("CATALOG_SNAPSHOT_HASH_MISSING")
    if unresolved_blockers:
        blockers.append("IDENTITY_BLOCKERS_UNRESOLVED")
    return tuple(blockers)


def _clean(v: str | None) -> str:
    return (v or "").strip().casefold()


def _candidate_index(live: Iterable[LiveVariant]) -> tuple[dict[str, list[LiveVariant]], dict[str, list[LiveVariant]]]:
    by_sku: dict[str, list[LiveVariant]] = {}
    by_barcode: dict[str, list[LiveVariant]] = {}
    for v in live:
        if _clean(v.sku):
            by_sku.setdefault(_clean(v.sku), []).append(v)
        if _clean(v.barcode):
            by_barcode.setdefault(_clean(v.barcode), []).append(v)
    return by_sku, by_barcode


def _strong_recreation_candidates(seed: SeedVariant, by_sku: dict[str, list[LiveVariant]], by_barcode: dict[str, list[LiveVariant]]) -> list[LiveVariant]:
    candidates: dict[str, LiveVariant] = {}
    if _clean(seed.barcode):
        for v in by_barcode.get(_clean(seed.barcode), []):
            candidates[v.variant_id] = v
    if _clean(seed.sku):
        for v in by_sku.get(_clean(seed.sku), []):
            candidates[v.variant_id] = v
    # Title/size similarity alone is intentionally insufficient for auto candidate status here.
    return list(candidates.values())


def _candidate_evidence(seed: SeedVariant, cand: LiveVariant) -> dict[str, Any]:
    """Side-by-side evidence for a recreation candidate (Phase 3 requirement D)."""
    matching, conflicting = [], []
    for field in ("sku", "barcode"):
        old, new = _clean(getattr(seed, field)), _clean(getattr(cand, field))
        if old and new:
            (matching if old == new else conflicting).append(field)
    if _clean(seed.product_title) and _clean(cand.product_title):
        (matching if _clean(seed.product_title) == _clean(cand.product_title) else conflicting).append("product_title")
    if _clean(seed.variant_title) and _clean(cand.variant_title):
        (matching if _clean(seed.variant_title) == _clean(cand.variant_title) else conflicting).append("variant_title")
    if conflicting:
        confidence = "CONFLICTING_EVIDENCE"
    elif "barcode" in matching and "sku" in matching:
        confidence = "STRONG"
    elif matching:
        confidence = "MODERATE"
    else:
        confidence = "WEAK"
    return {
        "new_variant_id": cand.variant_id,
        "new_sku": cand.sku,
        "new_barcode": cand.barcode,
        "new_product_title": cand.product_title,
        "new_variant_title": cand.variant_title,
        "new_handle": cand.handle,
        "new_vendor": cand.shopify_vendor,
        "new_price": str(cand.retail_price) if cand.retail_price is not None else None,
        "new_inventory_item_gid": cand.inventory_item_gid,
        "new_created_at": cand.variant_created_at,
        "new_updated_at": cand.variant_updated_at,
        "matching_evidence": matching,
        "conflicting_evidence": conflicting,
        "confidence": confidence,
    }


def reconcile_catalog(
    seed_rows: Iterable[SeedVariant],
    live_rows: Iterable[LiveVariant],
    *,
    rejected_pairs: set[tuple[str, str]] | None = None,
    approved_aliases: dict[str, str] | None = None,
) -> CatalogReconciliation:
    """Classify seed vs live identities.

    - ``rejected_pairs``: (old_variant_id, new_variant_id) pairs a human explicitly
      rejected; those candidates are never re-suggested.
    - ``approved_aliases``: old_variant_id -> new_variant_id human-approved recreation
      aliases; the old identity is treated as resolved continuity when the new ID is live.
    No confidence score ever auto-promotes an identity decision.
    """
    rejected_pairs = rejected_pairs or set()
    approved_aliases = approved_aliases or {}
    seed = list(seed_rows)
    live = list(live_rows)
    seed_by_id = {v.variant_id: v for v in seed}
    live_by_id = {v.variant_id: v for v in live}
    live_by_sku, live_by_barcode = _candidate_index(live)

    issues: list[CatalogIssue] = []
    exact = 0
    changed = 0

    for sid, s in seed_by_id.items():
        alias_target = approved_aliases.get(sid)
        if alias_target and alias_target in live_by_id and sid not in live_by_id:
            issues.append(CatalogIssue(
                "RESOLVED", sid, alias_target, False,
                {"rule": "Continuity established by previously human-approved recreation alias.",
                 "alias_target": alias_target},
            ))
            continue
        l = live_by_id.get(sid)
        if l:
            exact += 1
            diffs = {}
            for field in ("sku", "barcode", "product_title", "variant_title"):
                old = getattr(s, field)
                new = getattr(l, field)
                if _clean(str(old) if old is not None else None) != _clean(str(new) if new is not None else None):
                    diffs[field] = {"seed": old, "live": new}
            if diffs:
                changed += 1
                issues.append(CatalogIssue(
                    "CHANGED_ATTRIBUTES", sid, sid, False,
                    {"changes": diffs},
                ))
            continue

        if not s.active:
            issues.append(CatalogIssue("INACTIVE", sid, None, False, {"seed_active": False}))
            continue

        candidates = [
            c for c in _strong_recreation_candidates(s, live_by_sku, live_by_barcode)
            if (sid, c.variant_id) not in rejected_pairs
        ]
        if candidates:
            cand_evidence = [_candidate_evidence(s, c) for c in candidates]
            ambiguous = len(candidates) > 1 or any(e["conflicting_evidence"] for e in cand_evidence)
            issues.append(CatalogIssue(
                "AMBIGUOUS_IDENTITY" if ambiguous else "POTENTIAL_RECREATION",
                sid,
                candidates[0].variant_id if len(candidates) == 1 else None,
                True,
                {
                    "old_variant_id": sid,
                    "old_sku": s.sku,
                    "old_barcode": s.barcode,
                    "old_product_title": s.product_title,
                    "old_variant_title": s.variant_title,
                    "candidates": cand_evidence,
                    "candidate_variant_ids": [c.variant_id for c in candidates],
                    "rule": "Exact SKU/barcode is evidence only; never auto-merge historical identity.",
                },
            ))
        else:
            issues.append(CatalogIssue(
                "MISSING", sid, None, True,
                {"seed_sku": s.sku, "seed_barcode": s.barcode, "product": s.product_title, "variant": s.variant_title},
            ))

    new_live = 0
    for lid, l in live_by_id.items():
        if lid in seed_by_id:
            continue
        # If this live ID is already a recreation candidate, still classify it as new-live evidence,
        # but do not allow it to erase the blocker on the old identity.
        new_live += 1
        issues.append(CatalogIssue(
            "NEW", None, lid, False,
            {"sku": l.sku, "barcode": l.barcode, "product": l.product_title, "variant": l.variant_title},
        ))

    potential = sum(i.classification in ("POTENTIAL_RECREATION", "AMBIGUOUS_IDENTITY") for i in issues)
    missing = sum(i.classification == "MISSING" for i in issues)
    return CatalogReconciliation(exact, new_live, missing, potential, changed, tuple(issues))


def parse_live_variant(node: dict[str, Any]) -> LiveVariant:
    product = node.get("product") or {}
    inventory_item = node.get("inventoryItem") or {}
    unit_cost = inventory_item.get("unitCost") or {}
    vid = numeric_shopify_id(node.get("legacyResourceId") or node.get("id"))
    pid = numeric_shopify_id(product.get("legacyResourceId") or product.get("id"))
    if not vid or not pid:
        raise ValueError(f"Catalog row missing Shopify IDs: {node}")
    return LiveVariant(
        variant_id=vid,
        shopify_gid=str(node.get("id") or f"gid://shopify/ProductVariant/{vid}"),
        product_id=pid,
        product_gid=str(product.get("id") or f"gid://shopify/Product/{pid}"),
        product_title=str(product.get("title") or ""),
        variant_title=str(node.get("title") or ""),
        handle=product.get("handle"),
        status=product.get("status"),
        sku=node.get("sku") or inventory_item.get("sku"),
        barcode=node.get("barcode"),
        retail_price=Decimal(str(node["price"])) if node.get("price") is not None else None,
        shopify_current_cost=Decimal(str(unit_cost["amount"])) if unit_cost.get("amount") is not None else None,
        inventory_quantity=Decimal(str(node["inventoryQuantity"])) if node.get("inventoryQuantity") is not None else None,
        inventory_item_gid=inventory_item.get("id"),
        inventory_tracked=inventory_item.get("tracked"),
        shopify_vendor=product.get("vendor"),
        product_type=product.get("productType"),
        variant_created_at=node.get("createdAt"),
        variant_updated_at=node.get("updatedAt"),
        product_updated_at=product.get("updatedAt"),
    )


def fetch_live_catalog(client: Any, *, page_size: int = 250) -> tuple[int | None, list[LiveVariant]]:
    count: int | None = None
    try:
        count_data = client.query(CATALOG_COUNT_QUERY, {"query": ACTIVE_CATALOG_FILTER})
        count = int(count_data["productVariantsCount"]["count"])
    except Exception:
        # Count is a control statistic, not the data source. Pagination remains authoritative.
        count = None

    after = None
    rows: list[LiveVariant] = []
    seen: set[str] = set()
    while True:
        data = client.query(CATALOG_PAGE_QUERY, {"first": page_size, "after": after, "query": ACTIVE_CATALOG_FILTER})
        conn = data["productVariants"]
        for node in conn.get("nodes") or []:
            v = parse_live_variant(node)
            if v.variant_id in seen:
                raise RuntimeError(f"Duplicate live Shopify Variant ID returned: {v.variant_id}")
            seen.add(v.variant_id)
            rows.append(v)
        page = conn["pageInfo"]
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        if not after:
            raise RuntimeError("Shopify catalog pagination says hasNextPage but returned no endCursor")
    return count, rows


def seed_variant_from_mapping(row: dict[str, Any]) -> SeedVariant:
    return SeedVariant(
        variant_id=str(row["variant_id"]),
        product_id=str(row.get("product_id") or ""),
        product_title=str(row.get("product_title") or ""),
        variant_title=str(row.get("variant_title") or ""),
        sku=row.get("sku"),
        barcode=row.get("barcode"),
        active=bool(row.get("active", True)),
    )


def catalog_snapshot_hash(live_rows: list[LiveVariant]) -> str:
    """Deterministic hash of the fetched live catalog for auditability."""
    import hashlib
    import json
    canonical = sorted(
        (
            {k: (str(v) if v is not None else None) for k, v in asdict(r).items()}
            for r in live_rows
        ),
        key=lambda d: d["variant_id"],
    )
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def persist_catalog_sync(conn: Any, reported_count: int | None, live_rows: list[LiveVariant], reconciliation: CatalogReconciliation, *, api_version: str, pagination_complete: bool = True, started_at=None) -> str:
    """Persist a catalog sync atomically. `conn` is a psycopg-compatible connection."""
    import json
    source_hash = catalog_snapshot_hash(live_rows)
    gate_blockers = catalog_gate_blockers(
        live_rows_received=len(live_rows),
        exact_current_ids=reconciliation.exact_ids,
        new_live_variants=reconciliation.new_live,
        unresolved_blockers=len(reconciliation.blockers),
        pagination_complete=pagination_complete,
        source_hash=source_hash,
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO catalog_sync_runs(
                     shopify_api_version,shopify_reported_variant_count,live_rows_received,
                     exact_current_ids,new_live_variants,missing_seed_variants,potential_recreations,unresolved_count,status,
                     source_hash,pagination_complete,started_at,completed_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'COMPLETED',%s,%s,COALESCE(%s,now()),now()) RETURNING catalog_sync_id""",
                (api_version, reported_count, len(live_rows), reconciliation.exact_ids,
                 reconciliation.new_live, reconciliation.missing_seed, reconciliation.potential_recreations,
                 len(reconciliation.blockers), source_hash, pagination_complete, started_at),
            )
            sync_id = str(cur.fetchone()[0])

            for v in live_rows:
                cur.execute(
                    """INSERT INTO variants(
                         variant_id,shopify_gid,product_id,product_gid,product_title,variant_title,handle,status,sku,barcode,
                         retail_price,shopify_current_cost,shopify_vendor,product_type,active,variant_created_at,last_synced_at,
                         inventory_item_gid,inventory_tracked,catalog_state,catalog_last_seen_at,catalog_missing_since
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,'LIVE',now(),NULL)
                       ON CONFLICT(variant_id) DO UPDATE SET
                         shopify_gid=EXCLUDED.shopify_gid,product_id=EXCLUDED.product_id,product_gid=EXCLUDED.product_gid,
                         product_title=EXCLUDED.product_title,variant_title=EXCLUDED.variant_title,handle=EXCLUDED.handle,
                         status=EXCLUDED.status,sku=EXCLUDED.sku,barcode=EXCLUDED.barcode,retail_price=EXCLUDED.retail_price,
                         shopify_current_cost=EXCLUDED.shopify_current_cost,shopify_vendor=EXCLUDED.shopify_vendor,
                         product_type=EXCLUDED.product_type,active=EXCLUDED.active,last_synced_at=now(),
                         inventory_item_gid=EXCLUDED.inventory_item_gid,inventory_tracked=EXCLUDED.inventory_tracked,
                         catalog_state='LIVE',catalog_last_seen_at=now(),catalog_missing_since=NULL""",
                    (v.variant_id,v.shopify_gid,v.product_id,v.product_gid,v.product_title,v.variant_title,v.handle,v.status,
                     v.sku,v.barcode,v.retail_price,v.shopify_current_cost,v.shopify_vendor,v.product_type,
                     str(v.status or '').upper() == 'ACTIVE',v.variant_created_at,v.inventory_item_gid,v.inventory_tracked),
                )

            live_ids = {v.variant_id for v in live_rows}
            cur.execute("SELECT variant_id FROM variants WHERE active=TRUE")
            for (existing_id,) in cur.fetchall():
                if str(existing_id) not in live_ids:
                    cur.execute(
                        """UPDATE variants SET catalog_state='MISSING',catalog_missing_since=COALESCE(catalog_missing_since,now())
                           WHERE variant_id=%s""", (existing_id,)
                    )

            for issue in reconciliation.issues:
                cur.execute(
                    """INSERT INTO catalog_reconciliation_items(
                         catalog_sync_id,variant_id,seed_variant_id,live_variant_id,classification,blocking,evidence_json
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (sync_id, issue.live_variant_id or issue.seed_variant_id, issue.seed_variant_id, issue.live_variant_id,
                     issue.classification, issue.blocking, json.dumps(issue.evidence)),
                )

            status = 'PASS' if not gate_blockers else 'FAIL'
            cur.execute(
                """INSERT INTO readiness_gates(gate_name,status,severity,blocks_po,evidence_json,message,checked_at)
                   VALUES ('CATALOG_SYNC',%s,'CRITICAL',TRUE,%s::jsonb,%s,now())
                   ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                     status=EXCLUDED.status,evidence_json=EXCLUDED.evidence_json,message=EXCLUDED.message,checked_at=now()""",
                (status, json.dumps({
                    'reported_count': reported_count,
                    'live_rows': len(live_rows),
                    'exact_ids': reconciliation.exact_ids,
                    'new_live': reconciliation.new_live,
                    'missing_seed': reconciliation.missing_seed,
                    'potential_recreations': reconciliation.potential_recreations,
                    'blockers': len(reconciliation.blockers),
                    'readiness_blockers': gate_blockers,
                }), 'Catalog reconciliation passed.' if status == 'PASS' else f'Catalog readiness failed: {", ".join(gate_blockers)}.'),
            )
            cur.execute("UPDATE catalog_sync_runs SET completed_at=now() WHERE catalog_sync_id=%s", (sync_id,))
    return sync_id


def approve_recreated_variant(conn: Any, old_variant_id: str, new_variant_id: str, *, actor: str, note: str = "") -> None:
    """Human-approved identity continuity: old Shopify variant -> current canonical variant.

    This is deliberately never called automatically by `reconcile_catalog`.
    """
    import json
    actor = str(actor).strip()
    note = str(note).strip()
    if not actor or not note:
        raise ValueError("Recreation approval requires a nonblank actor and reason")
    old_variant_id = str(old_variant_id)
    new_variant_id = str(new_variant_id)
    if old_variant_id == new_variant_id:
        raise ValueError("old_variant_id and new_variant_id must differ")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT product_title,variant_title,sku,barcode,active,catalog_state FROM variants WHERE variant_id=%s", (old_variant_id,))
            old = cur.fetchone()
            cur.execute("SELECT product_title,variant_title,sku,barcode,active,catalog_state FROM variants WHERE variant_id=%s", (new_variant_id,))
            new = cur.fetchone()
            if not old or not new:
                raise ValueError("Both old and new Variant IDs must exist in the canonical table before approving recreation")
            if not bool(new[4]):
                raise ValueError("The replacement Variant ID must be active/current")
            # Decision must target an unresolved reconciliation blocker for this old ID.
            cur.execute(
                """SELECT evidence_json FROM catalog_reconciliation_items
                   WHERE seed_variant_id=%s AND blocking=TRUE AND resolved_at IS NULL
                   ORDER BY reconciliation_item_id DESC LIMIT 1""", (old_variant_id,))
            item = cur.fetchone()
            if not item:
                raise ValueError(f"No unresolved reconciliation blocker exists for historical Variant ID {old_variant_id}")
            # One approved continuity per historical identity, permanent.
            cur.execute(
                "SELECT variant_id FROM variant_aliases WHERE old_variant_id=%s AND approved AND source='CATALOG_RECONCILIATION'",
                (old_variant_id,))
            existing_alias = cur.fetchone()
            if existing_alias:
                raise ValueError(f"Historical Variant ID {old_variant_id} already has an approved continuity to {existing_alias[0]}")
            # A pair the reviewer explicitly rejected cannot be silently approved later without clearing the rejection.
            cur.execute(
                """SELECT 1 FROM mapping_rejections WHERE mapping_type='HISTORICAL_VARIANT'
                   AND source_key=%s AND rejected_variant_id=%s AND active""",
                (old_variant_id, new_variant_id))
            if cur.fetchone():
                raise ValueError("This candidate pair was explicitly rejected; the rejection must be reviewed before approval")

            evidence = {
                "old": {"variant_id": old_variant_id, "product_title": old[0], "variant_title": old[1], "sku": old[2], "barcode": old[3]},
                "new": {"variant_id": new_variant_id, "product_title": new[0], "variant_title": new[1], "sku": new[2], "barcode": new[3]},
                "human_note": note,
            }
            cur.execute(
                """INSERT INTO variant_aliases(
                     variant_id,old_variant_id,historical_product_title,historical_variant_title,historical_sku,
                     normalized_key,match_method,confidence,source,notes,approved,approved_by,approved_at,evidence_json
                   ) VALUES (%s,%s,%s,%s,%s,NULL,'HUMAN_RECREATION_APPROVAL',1.0,'CATALOG_RECONCILIATION',%s,TRUE,%s,now(),%s::jsonb)""",
                (new_variant_id, old_variant_id, old[0], old[1], old[2], note or None, actor, json.dumps(evidence)),
            )
            cur.execute(
                """UPDATE variants SET active=FALSE,catalog_state='RESOLVED_RECREATED',catalog_resolution_note=%s
                   WHERE variant_id=%s""", (f"Recreated as {new_variant_id}. {note}".strip(), old_variant_id),
            )
            cur.execute(
                """UPDATE catalog_reconciliation_items SET resolution=%s,resolved_by=%s,resolved_at=now(),blocking=FALSE
                   WHERE seed_variant_id=%s AND resolved_at IS NULL""",
                (f"APPROVED_RECREATION->{new_variant_id}", actor, old_variant_id),
            )
            cur.execute(
                """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
                   VALUES ('variant_aliases',%s,'APPROVE',%s::jsonb,%s)""",
                (f"{old_variant_id}->{new_variant_id}", json.dumps(evidence), actor),
            )


def reject_recreation_candidate(conn: Any, old_variant_id: str, new_variant_id: str, *, actor: str, note: str) -> None:
    """Human decision: old and new identities are different products (PERMANENT, audited).

    Persists the rejection so the same candidate pair is never re-suggested, without
    resolving the underlying blocker (the old identity still needs disposition).
    """
    import json
    actor = str(actor).strip()
    note = str(note).strip()
    if not actor or not note:
        raise ValueError("Recreation rejection requires a nonblank actor and reason")
    old_variant_id, new_variant_id = str(old_variant_id), str(new_variant_id)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT product_title,variant_title,sku,barcode FROM variants WHERE variant_id=%s", (old_variant_id,))
            old = cur.fetchone()
            if not old:
                raise ValueError(f"Unknown historical Variant ID {old_variant_id}")
            cur.execute("SELECT 1 FROM variants WHERE variant_id=%s", (new_variant_id,))
            if not cur.fetchone():
                raise ValueError(f"Unknown live Variant ID {new_variant_id}")
            evidence = {"old_variant_id": old_variant_id, "new_variant_id": new_variant_id,
                        "old_product_title": old[0], "old_variant_title": old[1],
                        "old_sku": old[2], "old_barcode": old[3], "note": note}
            cur.execute("SELECT 1 FROM mapping_rejections WHERE mapping_type='HISTORICAL_VARIANT' AND source_key=%s AND rejected_variant_id=%s AND active",
                        (old_variant_id, new_variant_id))
            if not cur.fetchone():
                cur.execute(
                    """INSERT INTO mapping_rejections(mapping_type,source_key,rejected_variant_id,source_text,evidence_json,rejected_by)
                       VALUES ('HISTORICAL_VARIANT',%s,%s,%s,%s::jsonb,%s)""",
                    (old_variant_id, new_variant_id, f"{old[0]} / {old[1]}", json.dumps(evidence), actor),
                )
            cur.execute(
                """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
                   VALUES ('mapping_rejections',%s,'REJECT',%s::jsonb,%s)""",
                (f"{old_variant_id}-x->{new_variant_id}", json.dumps(evidence), actor),
            )


def load_rejected_pairs(conn: Any) -> set[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT source_key,rejected_variant_id FROM mapping_rejections WHERE mapping_type='HISTORICAL_VARIANT' AND active")
        return {(str(a), str(b)) for a, b in cur.fetchall()}


def load_approved_aliases(conn: Any) -> dict[str, str]:
    """old_variant_id -> current canonical variant_id, approved human aliases only.

    Deterministic and fail-closed: if an old ID somehow maps to conflicting targets,
    that is a data-integrity violation and the sync must stop rather than pick one.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT old_variant_id, array_agg(DISTINCT variant_id ORDER BY variant_id)
               FROM variant_aliases WHERE approved AND old_variant_id IS NOT NULL
               GROUP BY old_variant_id""")
        out: dict[str, str] = {}
        conflicts = []
        for old, targets in cur.fetchall():
            if len(targets) > 1:
                conflicts.append(str(old))
            else:
                out[str(old)] = str(targets[0])
        if conflicts:
            raise RuntimeError(
                f"Conflicting approved continuity aliases for old Variant IDs: {conflicts[:10]} — resolve before syncing")
        return out


def retire_missing_variant(conn: Any, variant_id: str, *, actor: str, note: str) -> None:
    """Human confirmation that a missing historical canonical item has no current replacement."""
    import json
    actor = str(actor).strip()
    note = str(note).strip()
    if not actor or not note:
        raise ValueError("Retirement requires a nonblank actor and reason")
    variant_id = str(variant_id)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT product_title,variant_title,sku,barcode FROM variants WHERE variant_id=%s", (variant_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Unknown Variant ID {variant_id}")
            # Retirement is only a valid disposition for an unresolved reconciliation blocker
            # (a historical identity absent from the live catalog) — never for a live variant.
            cur.execute(
                """SELECT classification FROM catalog_reconciliation_items
                   WHERE seed_variant_id=%s AND blocking=TRUE AND resolved_at IS NULL
                   ORDER BY reconciliation_item_id DESC LIMIT 1""", (variant_id,))
            item = cur.fetchone()
            if not item:
                raise ValueError(f"No unresolved reconciliation blocker exists for Variant ID {variant_id}; retirement not applicable")
            cur.execute("SELECT catalog_state FROM variants WHERE variant_id=%s", (variant_id,))
            state = cur.fetchone()[0]
            if state == 'LIVE':
                raise ValueError(f"Variant ID {variant_id} is live in Shopify and cannot be retired")
            evidence = {"variant_id": variant_id, "product_title": row[0], "variant_title": row[1], "sku": row[2], "barcode": row[3], "note": note}
            cur.execute(
                """UPDATE variants SET active=FALSE,catalog_state='RETIRED_CONFIRMED',catalog_resolution_note=%s
                   WHERE variant_id=%s""", (note, variant_id),
            )
            cur.execute(
                """UPDATE catalog_reconciliation_items SET resolution='CONFIRMED_RETIRED',resolved_by=%s,resolved_at=now(),blocking=FALSE
                   WHERE seed_variant_id=%s AND resolved_at IS NULL""", (actor, variant_id),
            )
            cur.execute(
                """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
                   VALUES ('variants',%s,'UPDATE',%s::jsonb,%s)""", (variant_id, json.dumps(evidence), actor),
            )


def recompute_catalog_gate(conn: Any) -> dict[str, Any]:
    """Re-evaluate CATALOG_SYNC after human reconciliation without requiring a network call."""
    import json
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """SELECT catalog_sync_id,status,pagination_complete,live_rows_received,
                          exact_current_ids,new_live_variants,source_hash
                   FROM catalog_sync_runs
                   ORDER BY started_at DESC,completed_at DESC NULLS LAST LIMIT 1"""
            )
            row = cur.fetchone()
            if not row:
                readiness_blockers = ("NO_COMPLETED_CATALOG_SYNC",)
                evidence = {"reason": "no completed catalog sync", "readiness_blockers": readiness_blockers}
            elif row[1] != "COMPLETED":
                readiness_blockers = ("LATEST_CATALOG_SYNC_NOT_COMPLETED",)
                evidence = {
                    "catalog_sync_id": str(row[0]),
                    "latest_run_status": row[1],
                    "readiness_blockers": readiness_blockers,
                }
            else:
                sync_id = row[0]
                cur.execute("SELECT COUNT(*) FROM catalog_reconciliation_items WHERE catalog_sync_id=%s AND blocking=TRUE AND resolved_at IS NULL", (sync_id,))
                unresolved_blockers = int(cur.fetchone()[0])
                readiness_blockers = catalog_gate_blockers(
                    live_rows_received=int(row[3]),
                    exact_current_ids=int(row[4]),
                    new_live_variants=int(row[5]),
                    unresolved_blockers=unresolved_blockers,
                    pagination_complete=bool(row[2]),
                    source_hash=row[6],
                )
                evidence = {
                    "catalog_sync_id": str(sync_id),
                    "unresolved_blockers": unresolved_blockers,
                    "pagination_complete": bool(row[2]),
                    "live_rows_received": int(row[3]),
                    "exact_current_ids": int(row[4]),
                    "new_live_variants": int(row[5]),
                    "source_hash_present": bool(row[6]),
                    "readiness_blockers": readiness_blockers,
                }
            status = "PASS" if not readiness_blockers else "FAIL"
            cur.execute(
                """INSERT INTO readiness_gates(gate_name,status,severity,blocks_po,evidence_json,message,checked_at)
                   VALUES ('CATALOG_SYNC',%s,'CRITICAL',TRUE,%s::jsonb,%s,now())
                   ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                     status=EXCLUDED.status,evidence_json=EXCLUDED.evidence_json,message=EXCLUDED.message,checked_at=now()""",
                (status, json.dumps(evidence), "Catalog reconciliation passed." if status == "PASS" else f"Catalog readiness failed: {', '.join(readiness_blockers)}."),
            )
    return {"status": status, **evidence}

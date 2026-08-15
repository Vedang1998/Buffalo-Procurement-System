"""Phase 3 diagnostic: explain the productVariantsCount (2,003) vs paginated fetch (1,999)
discrepancy with evidence. Read-only against Shopify. Results printed as JSON and
persisted only to the structurally usable authoritative catalog attempt's notes.

Checks performed (per the owner's directive):
 1. Same catalog definition on both queries (identical $query filter string).
 2. Pagination reached hasNextPage=false.
 3. Every endCursor advanced; none repeated.
 4. Raw nodes fetched vs unique Variant IDs.
 5. Duplicate Variant IDs across pages.
 6. GraphQL errors/warnings (client raises on errors; retries logged by client).
 7. Filter-semantics comparison: per-status variant counts vs unfiltered count.
 8. Whether non-ACTIVE-product variants explain the difference.
 9. Repeat count query before/after pagination (mid-run drift check).
10. Attempt to identify the extra records by Variant ID (set difference between
    the search-index listing and the per-product ground truth).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from procurement_os.catalog import (
    require_structurally_usable_authoritative_catalog_run,
)
from procurement_os.shopify.auth import ClientCredentialsTokenProvider, ShopifyConfig
from procurement_os.shopify.graphql import ShopifyGraphQLClient
from procurement_os.shopify.queries import ACTIVE_CATALOG_FILTER, CATALOG_COUNT_QUERY, CATALOG_PAGE_QUERY

PRODUCT_VARIANT_IDS_QUERY = """
query DiagActiveProductVariantIds($first: Int!, $after: String) {
  products(first: $first, after: $after, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes { id status variants(first: 250) { pageInfo { hasNextPage } nodes { id } } }
  }
}
"""


def paginate_variant_ids(client, flt):
    ids = []
    cursors = []
    raw_nodes = 0
    after = None
    pages = 0
    while True:
        data = client.query(CATALOG_PAGE_QUERY, {"first": 250, "after": after, "query": flt})
        conn = data["productVariants"]
        nodes = conn.get("nodes") or []
        raw_nodes += len(nodes)
        ids.extend(n["id"] for n in nodes)
        pages += 1
        page = conn["pageInfo"]
        if not page.get("hasNextPage"):
            return ids, raw_nodes, pages, cursors, True
        cur = page.get("endCursor")
        if cur in cursors or cur == after or cur is None:
            return ids, raw_nodes, pages, cursors, False  # cursor repeated/stuck
        cursors.append(cur)
        after = cur


def count(client, flt=None):
    return int(client.query(CATALOG_COUNT_QUERY, {"query": flt})["productVariantsCount"]["count"])


def persist_diagnostic_report(conn, report) -> str:
    evaluation = require_structurally_usable_authoritative_catalog_run(conn)
    sync_id = str(evaluation["catalog_sync_id"])
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE catalog_sync_runs
               SET notes = coalesce(notes,'') || E'\n\nDIAGNOSTIC: ' || %s
               WHERE catalog_sync_id = %s""",
            (json.dumps(report), sync_id),
        )
    conn.commit()
    return sync_id


def main():
    config = ShopifyConfig.from_env()
    client = ShopifyGraphQLClient(config, ClientCredentialsTokenProvider(config))
    report = {"filter_used_by_both_queries": ACTIVE_CATALOG_FILTER}

    # (9) count before
    report["count_before_pagination"] = count(client, ACTIVE_CATALOG_FILTER)

    # (2)(3)(4)(5) paginate the variant search index
    ids, raw_nodes, pages, cursors, clean = paginate_variant_ids(client, ACTIVE_CATALOG_FILTER)
    uniq = set(ids)
    report.update({
        "pagination_reached_hasNextPage_false": clean,
        "pages_fetched": pages,
        "distinct_end_cursors": len(cursors),
        "cursors_all_advanced": len(cursors) == len(set(cursors)),
        "raw_nodes_fetched": raw_nodes,
        "unique_variant_ids": len(uniq),
        "duplicate_ids_across_pages": sorted(set(i for i in ids if ids.count(i) > 1))[:20] if raw_nodes != len(uniq) else [],
    })

    # (9) count after
    report["count_after_pagination"] = count(client, ACTIVE_CATALOG_FILTER)

    # (7)(8) per-status semantics
    report["variant_counts_by_product_status"] = {
        s: count(client, f"product_status:{s}") for s in ("active", "draft", "archived")
    }
    report["variant_count_unfiltered"] = count(client, None)

    # Ground truth: variant IDs via active products themselves (no variant search index)
    truth = set()
    truncated_products = []
    statuses = {}
    after = None
    while True:
        data = client.query(PRODUCT_VARIANT_IDS_QUERY, {"first": 100, "after": after})
        conn = data["products"]
        for p in conn.get("nodes") or []:
            statuses[p["status"]] = statuses.get(p["status"], 0) + 1
            if p["variants"]["pageInfo"]["hasNextPage"]:
                truncated_products.append(p["id"])
            for v in p["variants"]["nodes"]:
                truth.add(v["id"])
        page = conn["pageInfo"]
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    report["ground_truth_active_product_variant_ids"] = len(truth)
    report["ground_truth_product_statuses"] = statuses
    report["products_with_truncated_variant_lists"] = truncated_products

    # (10) identify discrepant records between the two enumerations
    report["ids_in_search_index_not_in_ground_truth"] = sorted(uniq - truth)
    report["ids_in_ground_truth_not_in_search_index"] = sorted(truth - uniq)

    print(json.dumps(report, indent=2))

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        import psycopg
        with psycopg.connect(database_url) as conn:
            sync_id = persist_diagnostic_report(conn, report)
            print({"diagnostic_persisted": True, "catalog_sync_id": sync_id})


if __name__ == "__main__":
    main()

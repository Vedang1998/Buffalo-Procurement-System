from __future__ import annotations

import os

from procurement_os.catalog import (
    SeedVariant, fetch_live_catalog, load_approved_aliases, load_rejected_pairs,
    persist_catalog_sync, reconcile_catalog,
)
from procurement_os.shopify.auth import ClientCredentialsTokenProvider, ShopifyConfig
from procurement_os.shopify.graphql import ShopifyGraphQLClient
from procurement_os.shopify.queries import ACTIVE_PRODUCT_VARIANT_TOTALS_QUERY, SHOP_INFO_QUERY


def validate_shopify_access(client: ShopifyGraphQLClient) -> dict:
    """Harmless read-only probe. Raises on auth/scope errors; never logs tokens."""
    data = client.query(SHOP_INFO_QUERY)
    shop = data.get("shop") or {}
    if not shop.get("myshopifyDomain"):
        raise RuntimeError("Shopify auth probe returned no shop identity")
    return {"shop": shop.get("myshopifyDomain"), "name": shop.get("name")}


def independent_active_variant_total(client: ShopifyGraphQLClient) -> tuple[int, int]:
    """Independently verify the retrieved record count by paginating active products
    and summing their variantsCount (does not use the productVariantsCount index)."""
    after = None
    products = 0
    total = 0
    while True:
        data = client.query(ACTIVE_PRODUCT_VARIANT_TOTALS_QUERY, {"first": 250, "after": after})
        conn = data["products"]
        for node in conn.get("nodes") or []:
            products += 1
            total += int(node["variantsCount"]["count"])
        page = conn["pageInfo"]
        if not page.get("hasNextPage"):
            return products, total
        after = page.get("endCursor")


def main() -> None:
    import psycopg
    from datetime import datetime, timezone

    database_url = os.environ["DATABASE_URL"]
    config = ShopifyConfig.from_env()
    token_provider = ClientCredentialsTokenProvider(config)
    client = ShopifyGraphQLClient(config, token_provider)

    probe = validate_shopify_access(client)
    print({"auth_probe": probe, "api_version": config.api_version})

    started_at = datetime.now(timezone.utc)
    try:
        _run_sync(database_url, config, client, started_at)
    except Exception as exc:
        # Fail closed: persist a FAILED run and force CATALOG_SYNC=FAIL so a prior
        # gate state can never survive a failed/incomplete sync.
        import json
        with psycopg.connect(database_url) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO catalog_sync_runs(shopify_api_version,status,pagination_complete,started_at,completed_at,notes)
                           VALUES (%s,'FAILED',FALSE,%s,now(),%s)""",
                        (config.api_version, started_at, f"{type(exc).__name__}: {exc}"[:2000]))
                    cur.execute(
                        """INSERT INTO readiness_gates(gate_name,status,severity,blocks_po,evidence_json,message,checked_at)
                           VALUES ('CATALOG_SYNC','FAIL','CRITICAL',TRUE,%s::jsonb,%s,now())
                           ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                             status='FAIL',evidence_json=EXCLUDED.evidence_json,message=EXCLUDED.message,checked_at=now()""",
                        (json.dumps({"failure": f"{type(exc).__name__}: {exc}"[:500]}),
                         "Catalog sync failed or was incomplete; gate forced FAIL."))
        raise


def _run_sync(database_url: str, config: ShopifyConfig, client: ShopifyGraphQLClient, started_at) -> None:
    import psycopg

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            # Seed identities only: rows originating from the historical import.
            cur.execute("SELECT variant_id,product_id,product_title,variant_title,sku,barcode,active FROM variants WHERE catalog_state IN ('SEEDED','LIVE','MISSING','RESOLVED_RECREATED','RETIRED_CONFIRMED')")
            seed = [SeedVariant(str(r[0]), str(r[1] or ''), r[2] or '', r[3] or '', r[4], r[5], bool(r[6])) for r in cur.fetchall()]
        rejected = load_rejected_pairs(conn)
        approved = load_approved_aliases(conn)
        reported_count, live = fetch_live_catalog(client)
        # Independent verification: sum of variantsCount over all active products
        # must equal the number of records actually retrieved by pagination.
        products, verified_total = independent_active_variant_total(client)
        if verified_total != len(live):
            raise RuntimeError(
                f"Pagination verification failed: fetched {len(live)} variants but "
                f"{products} active products report {verified_total} variants in total")
        count_drift = None
        if reported_count is not None and reported_count != len(live):
            count_drift = (f"productVariantsCount index reported {reported_count}; pagination retrieved "
                           f"{len(live)}, independently verified by active-product variant totals "
                           f"({products} products, {verified_total} variants). Pagination is authoritative.")
            print({"count_index_drift": count_drift})
        reconciliation = reconcile_catalog(seed, live, rejected_pairs=rejected, approved_aliases=approved)
        sync_id = persist_catalog_sync(conn, reported_count, live, reconciliation,
                                       api_version=config.api_version, pagination_complete=True,
                                       started_at=started_at)
        if count_drift:
            with conn.cursor() as cur:
                cur.execute("UPDATE catalog_sync_runs SET notes=%s WHERE catalog_sync_id=%s", (count_drift, sync_id))
            conn.commit()
        print({
            "catalog_sync_id": sync_id,
            "reported_count": reported_count,
            "live_rows": len(live),
            "exact_ids": reconciliation.exact_ids,
            "new_live": reconciliation.new_live,
            "missing_seed": reconciliation.missing_seed,
            "potential_recreations": reconciliation.potential_recreations,
            "blockers": len(reconciliation.blockers),
            "catalog_gate_pass": reconciliation.can_pass_catalog_gate,
        })


if __name__ == "__main__":
    main()

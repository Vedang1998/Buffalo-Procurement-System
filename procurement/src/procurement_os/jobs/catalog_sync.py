from __future__ import annotations

import os

from procurement_os.catalog import SeedVariant, fetch_live_catalog, persist_catalog_sync, reconcile_catalog
from procurement_os.shopify.auth import ClientCredentialsTokenProvider, ShopifyConfig
from procurement_os.shopify.graphql import ShopifyGraphQLClient


def main() -> None:
    import psycopg

    database_url = os.environ["DATABASE_URL"]
    config = ShopifyConfig.from_env()
    token_provider = ClientCredentialsTokenProvider(config)
    client = ShopifyGraphQLClient(config, token_provider)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT variant_id,product_id,product_title,variant_title,sku,barcode,active FROM variants")
            seed = [SeedVariant(str(r[0]), str(r[1] or ''), r[2] or '', r[3] or '', r[4], r[5], bool(r[6])) for r in cur.fetchall()]
        reported_count, live = fetch_live_catalog(client)
        reconciliation = reconcile_catalog(seed, live)
        sync_id = persist_catalog_sync(conn, reported_count, live, reconciliation, api_version=config.api_version)
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

from __future__ import annotations

import argparse
from datetime import date
import os

from procurement_os.sales import fetch_shopifyql_sales, load_identity_index, persist_sales_backfill
from procurement_os.shopify.auth import ClientCredentialsTokenProvider, ShopifyConfig
from procurement_os.shopify.graphql import ShopifyGraphQLClient


def main() -> None:
    import psycopg

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=date(2024, 11, 28))
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--chunk-days", type=int, default=31)
    args = ap.parse_args()

    database_url = os.environ["DATABASE_URL"]
    config = ShopifyConfig.from_env()
    client = ShopifyGraphQLClient(config, ClientCredentialsTokenProvider(config))
    rows = fetch_shopifyql_sales(client, args.start, args.end, chunk_days=args.chunk_days)

    with psycopg.connect(database_url) as conn:
        identity = load_identity_index(conn)
        backfill_id = persist_sales_backfill(conn, rows, identity, start_date=args.start, end_date=args.end)
        print({"sales_backfill_id": backfill_id, "raw_rows": len(rows), "start": args.start.isoformat(), "end": args.end.isoformat()})


if __name__ == "__main__":
    main()

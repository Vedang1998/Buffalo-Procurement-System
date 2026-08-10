from __future__ import annotations

import argparse
from datetime import date
import json
import os

from procurement_os.historical_sales import (
    AUTHORITATIVE_START_DATE,
    run_historical_sales_backfill,
)
from procurement_os.shopify.auth import ClientCredentialsTokenProvider, ShopifyConfig
from procurement_os.shopify.graphql import ShopifyGraphQLClient


def main() -> None:
    import psycopg

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=AUTHORITATIVE_START_DATE)
    ap.add_argument("--end", type=date.fromisoformat, default=None,
                    help=("new runs: must equal the current Shopify store-local date and defaults "
                          "to discovery; resumes: omit to use the durable run end"))
    ap.add_argument("--chunk-days", type=int, default=31)
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--resume", default=None, help="resume an existing durable sales_backfill_id")
    args = ap.parse_args()

    database_url = os.environ["DATABASE_URL"]
    config = ShopifyConfig.from_env()
    client = ShopifyGraphQLClient(config, ClientCredentialsTokenProvider(config))
    # Autocommit is intentional: the runner brackets every checkpoint in its own
    # transaction so completed pages remain durable across later interruptions.
    with psycopg.connect(database_url, autocommit=True) as conn:
        result = run_historical_sales_backfill(
            conn, client, start_date=args.start, end_date=args.end,
            chunk_days=args.chunk_days, page_size=args.page_size,
            resume_run_id=args.resume,
        )
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()

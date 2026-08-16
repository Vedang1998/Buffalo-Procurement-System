"""Run Phase 3 identity investigation against the authoritative catalog attempt.
Diagnostic only: read-only Shopify node lookups, no identity decisions, no gate changes."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg

from procurement_os.catalog import (
    require_structurally_usable_authoritative_catalog_run,
)
from procurement_os.catalog_investigation import run_identity_investigation
from procurement_os.shopify.auth import ClientCredentialsTokenProvider, ShopifyConfig
from procurement_os.shopify.graphql import ShopifyGraphQLClient


def authoritative_investigation_catalog_sync_id(conn) -> str:
    evaluation = require_structurally_usable_authoritative_catalog_run(conn)
    return str(evaluation["catalog_sync_id"])


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    config = ShopifyConfig.from_env()
    client = ShopifyGraphQLClient(config, ClientCredentialsTokenProvider(config))
    with psycopg.connect(database_url) as conn:
        sync_id = authoritative_investigation_catalog_sync_id(conn)
        summary = run_identity_investigation(conn, client, sync_id)
    print(json.dumps({"catalog_sync_id": sync_id, **summary}, indent=2))


if __name__ == "__main__":
    main()

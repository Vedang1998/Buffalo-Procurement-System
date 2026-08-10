"""Validated, read-only Shopify GraphQL/ShopifyQL query contracts."""

from datetime import date

# Harmless read-only probe used to validate authentication before any sync work.
SHOP_INFO_QUERY = r"""
query ProcurementAuthProbe {
  shop { name myshopifyDomain currencyCode }
}
"""

# The backfill end date is the current calendar date in the store's timezone,
# not the worker's local timezone. This query contains no commerce/customer data.
SHOP_TIMEZONE_QUERY = r"""
query ProcurementShopTimezone {
  shop { ianaTimezone }
}
"""

# The purchasing catalog is defined by Shopify's explicit active-product filter,
# not by an unfiltered variant count (Phase 3 requirement B).
# Note: Shopify's search syntax requires the lowercase value; "product_status:ACTIVE"
# silently matches nothing on productVariants while the count query tolerates it.
ACTIVE_CATALOG_FILTER = "product_status:active"

CATALOG_PAGE_QUERY = r"""
query ProcurementCatalogPage($first: Int!, $after: String, $query: String) {
  productVariants(first: $first, after: $after, query: $query) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      legacyResourceId
      title
      sku
      barcode
      price
      createdAt
      updatedAt
      inventoryQuantity
      inventoryItem {
        id
        sku
        tracked
        unitCost { amount currencyCode }
      }
      product {
        id
        legacyResourceId
        title
        handle
        status
        vendor
        productType
        updatedAt
      }
    }
  }
}
"""

# Independent verification of the retrieved record count: paginate active products
# and sum their variantsCount. This does not rely on the productVariantsCount
# search index, which can drift.
ACTIVE_PRODUCT_VARIANT_TOTALS_QUERY = r"""
query ProcurementActiveProductTotals($first: Int!, $after: String) {
  products(first: $first, after: $after, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes { id variantsCount { count } }
  }
}
"""

# Direct node lookup of a single historical variant by GID (read-only).
VARIANT_NODE_LOOKUP_QUERY = r"""
query ProcurementVariantNodeLookup($id: ID!) {
  node(id: $id) {
    __typename
    ... on ProductVariant {
      id
      title
      sku
      barcode
      price
      product { id title status vendor productType handle }
    }
  }
}
"""

CATALOG_COUNT_QUERY = r"""
query ProcurementCatalogCount($query: String) {
  productVariantsCount(query: $query) { count }
}
"""

SHOPIFYQL_WRAPPER_QUERY = r"""
query ProcurementShopifyQL($query: String!) {
  shopifyqlQuery(query: $query) {
    tableData {
      columns { name dataType displayName }
      rows
    }
    parseErrors
  }
}
"""


HISTORICAL_SALES_DIMENSIONS = (
    "day",
    "product_variant_id",
    "product_title_at_time_of_sale",
    "product_variant_title_at_time_of_sale",
    "product_variant_sku_at_time_of_sale",
)
HISTORICAL_SALES_METRICS = ("net_items_sold", "net_sales")
HISTORICAL_SALES_REQUIRED_COLUMNS = HISTORICAL_SALES_DIMENSIONS + HISTORICAL_SALES_METRICS


def _validated_date_range(start_date: str, end_date: str) -> tuple[str, str]:
    """Return canonical ISO dates and reject malformed/reversed query ranges."""

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("ShopifyQL dates must use YYYY-MM-DD") from exc
    if start > end:
        raise ValueError("ShopifyQL start_date must not be after end_date")
    return start.isoformat(), end.isoformat()


def historical_sales_shopifyql(
    start_date: str,
    end_date: str,
    *,
    limit: int = 1000,
    offset: int = 0,
) -> str:
    """Build a deterministic page of raw historical sales source facts."""

    start_date, end_date = _validated_date_range(start_date, end_date)
    limit = int(limit)
    offset = int(offset)
    if limit <= 0:
        raise ValueError("ShopifyQL limit must be positive")
    if offset < 0:
        raise ValueError("ShopifyQL offset must not be negative")

    return f"""FROM sales
SHOW {", ".join(HISTORICAL_SALES_METRICS)}
GROUP BY {", ".join(HISTORICAL_SALES_DIMENSIONS)}
SINCE {start_date} UNTIL {end_date}
ORDER BY day ASC, product_title_at_time_of_sale ASC, product_variant_title_at_time_of_sale ASC, product_variant_sku_at_time_of_sale ASC, product_variant_id ASC
LIMIT {limit} OFFSET {offset}"""


def historical_sales_control_totals_shopifyql(start_date: str, end_date: str) -> str:
    """Build the independent, ungrouped source control-total query."""

    start_date, end_date = _validated_date_range(start_date, end_date)
    return f"""FROM sales
SHOW {", ".join(HISTORICAL_SALES_METRICS)}
SINCE {start_date} UNTIL {end_date}"""

"""Validated Shopify GraphQL/ShopifyQL query contracts used by the foundation."""

# Harmless read-only probe used to validate authentication before any sync work.
SHOP_INFO_QUERY = r"""
query ProcurementAuthProbe {
  shop { name myshopifyDomain currencyCode }
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


def historical_sales_shopifyql(start_date: str, end_date: str, *, limit: int = 1000, offset: int = 0) -> str:
    return f"""FROM sales
SHOW net_items_sold, net_sales
GROUP BY day, product_variant_id, product_title_at_time_of_sale, product_variant_title_at_time_of_sale, product_variant_sku_at_time_of_sale
SINCE {start_date} UNTIL {end_date}
ORDER BY day ASC, product_title_at_time_of_sale ASC, product_variant_title_at_time_of_sale ASC, product_variant_sku_at_time_of_sale ASC, product_variant_id ASC
LIMIT {int(limit)} OFFSET {int(offset)}"""

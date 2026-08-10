"""Validated Shopify GraphQL/ShopifyQL query contracts used by the foundation."""

CATALOG_PAGE_QUERY = r"""
query ProcurementCatalogPage($first: Int!, $after: String) {
  productVariants(first: $first, after: $after) {
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

CATALOG_COUNT_QUERY = r"""
query ProcurementCatalogCount {
  productVariantsCount { count }
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

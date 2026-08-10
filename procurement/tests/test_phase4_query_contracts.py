import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest

from procurement_os.shopify.queries import (
    HISTORICAL_SALES_DIMENSIONS,
    HISTORICAL_SALES_METRICS,
    HISTORICAL_SALES_REQUIRED_COLUMNS,
    SHOP_TIMEZONE_QUERY,
    SHOPIFYQL_WRAPPER_QUERY,
    historical_sales_control_totals_shopifyql,
    historical_sales_shopifyql,
)


PROHIBITED_CUSTOMER_FIELDS = ("customer", "email", "phone", "address")


def _comma_separated_clause(query: str, prefix: str) -> tuple[str, ...]:
    line = next(line for line in query.splitlines() if line.startswith(prefix))
    return tuple(part.strip() for part in line.removeprefix(prefix).split(","))


class Phase4ShopifyQueryContractTests(unittest.TestCase):
    def test_detail_query_uses_exact_required_columns(self):
        query = historical_sales_shopifyql(
            "2024-11-28", "2024-12-31", limit=500, offset=1000
        )

        self.assertEqual(
            HISTORICAL_SALES_REQUIRED_COLUMNS,
            (
                "day",
                "product_variant_id",
                "product_title_at_time_of_sale",
                "product_variant_title_at_time_of_sale",
                "product_variant_sku_at_time_of_sale",
                "net_items_sold",
                "net_sales",
            ),
        )
        self.assertEqual(_comma_separated_clause(query, "SHOW "), HISTORICAL_SALES_METRICS)
        self.assertEqual(
            _comma_separated_clause(query, "GROUP BY "), HISTORICAL_SALES_DIMENSIONS
        )
        self.assertIn("SINCE 2024-11-28 UNTIL 2024-12-31", query)
        self.assertIn("LIMIT 500 OFFSET 1000", query)

    def test_detail_query_is_deterministic_and_contains_no_customer_dimension(self):
        first = historical_sales_shopifyql(
            "2024-11-28", "2026-08-10", limit=250, offset=750
        )
        second = historical_sales_shopifyql(
            "2024-11-28", "2026-08-10", limit=250, offset=750
        )

        self.assertEqual(first, second)
        self.assertIn("ORDER BY day ASC", first)
        lowered = first.lower()
        for field in PROHIBITED_CUSTOMER_FIELDS:
            self.assertNotIn(field, lowered)
        self.assertNotIn("mutation", lowered)

    def test_control_totals_query_is_independent_and_ungrouped(self):
        query = historical_sales_control_totals_shopifyql(
            "2024-11-28", "2026-08-10"
        )

        self.assertEqual(_comma_separated_clause(query, "SHOW "), HISTORICAL_SALES_METRICS)
        self.assertIn("FROM sales", query)
        self.assertIn("SINCE 2024-11-28 UNTIL 2026-08-10", query)
        self.assertNotIn("GROUP BY", query)
        self.assertNotIn("LIMIT", query)
        self.assertNotIn("OFFSET", query)
        lowered = query.lower()
        for field in PROHIBITED_CUSTOMER_FIELDS:
            self.assertNotIn(field, lowered)
        self.assertNotIn("mutation", lowered)

    def test_query_inputs_fail_closed(self):
        invalid_ranges = (
            ("2024/11/28", "2024-12-31"),
            ("2025-01-01", "2024-12-31"),
        )
        for start_date, end_date in invalid_ranges:
            with self.subTest(start_date=start_date, end_date=end_date):
                with self.assertRaises(ValueError):
                    historical_sales_shopifyql(start_date, end_date)
                with self.assertRaises(ValueError):
                    historical_sales_control_totals_shopifyql(start_date, end_date)

        for kwargs in ({"limit": 0}, {"limit": -1}, {"offset": -1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    historical_sales_shopifyql(
                        "2024-11-28", "2024-11-28", **kwargs
                    )

    def test_graphql_contracts_are_read_only_and_timezone_is_iana(self):
        self.assertIn("shop { ianaTimezone }", SHOP_TIMEZONE_QUERY)
        for query in (SHOP_TIMEZONE_QUERY, SHOPIFYQL_WRAPPER_QUERY):
            self.assertNotIn("mutation", query.lower())


if __name__ == "__main__":
    unittest.main()

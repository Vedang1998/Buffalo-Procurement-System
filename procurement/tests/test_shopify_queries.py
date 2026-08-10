import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from procurement_os.shopify.queries import historical_sales_shopifyql

class QueryTests(unittest.TestCase):
    def test_sales_query_preserves_historical_identity_evidence(self):
        q=historical_sales_shopifyql('2024-11-28','2024-12-31',limit=500,offset=1000)
        for field in ['product_variant_id','product_title_at_time_of_sale','product_variant_title_at_time_of_sale','product_variant_sku_at_time_of_sale','net_items_sold','net_sales']:
            self.assertIn(field,q)
        self.assertIn('LIMIT 500 OFFSET 1000',q)

if __name__ == '__main__': unittest.main()

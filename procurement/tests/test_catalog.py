import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from decimal import Decimal
import unittest

from procurement_os.catalog import (
    LiveVariant, SeedVariant, approve_recreated_variant, catalog_gate_blockers,
    numeric_shopify_id, parse_live_variant, reconcile_catalog,
    reject_recreation_candidate, retire_missing_variant,
)


def live(vid, *, pid='p1', sku=None, barcode=None, product='Product', variant='750ML'):
    return LiveVariant(
        str(vid), f'gid://shopify/ProductVariant/{vid}', str(pid), f'gid://shopify/Product/{pid}',
        product, variant, 'handle', 'ACTIVE', sku, barcode, Decimal('10'), Decimal('7'), Decimal('2'),
        f'gid://shopify/InventoryItem/{vid}', True, 'Vendor', 'Wine', None, None, None,
    )


class CatalogTests(unittest.TestCase):
    def test_numeric_id(self):
        self.assertEqual(numeric_shopify_id('gid://shopify/ProductVariant/123'), '123')
        self.assertIsNone(numeric_shopify_id('0'))

    def test_exact_identity_passes(self):
        r = reconcile_catalog([SeedVariant('1','p1','A','750ML','S1','B1')], [live('1', sku='S1', barcode='B1', product='A')])
        self.assertEqual(r.exact_ids, 1)
        self.assertTrue(r.can_pass_catalog_gate)

    def test_new_live_does_not_fail_by_itself(self):
        r = reconcile_catalog([], [live('2', sku='S2')])
        self.assertEqual(r.new_live, 1)
        self.assertTrue(r.can_pass_catalog_gate)

    def test_missing_seed_blocks(self):
        r = reconcile_catalog([SeedVariant('1','p1','A','750ML','S1','B1')], [])
        self.assertFalse(r.can_pass_catalog_gate)
        self.assertEqual(r.missing_seed, 1)

    def test_recreation_candidate_blocks_and_never_auto_merges(self):
        r = reconcile_catalog(
            [SeedVariant('1','p1','A','750ML','S1','B1')],
            [live('2', sku='S1', barcode='B1', product='A')],
        )
        self.assertFalse(r.can_pass_catalog_gate)
        self.assertEqual(r.potential_recreations, 1)
        issue = next(i for i in r.issues if i.classification == 'POTENTIAL_RECREATION')
        self.assertEqual(issue.live_variant_id, '2')
        self.assertTrue(issue.blocking)

    def test_attribute_change_is_informational(self):
        r = reconcile_catalog(
            [SeedVariant('1','p1','Old title','750ML','S1','B1')],
            [live('1', sku='S1', barcode='B1', product='New title')],
        )
        self.assertTrue(r.can_pass_catalog_gate)
        self.assertEqual(r.changed_attributes, 1)

    def test_parse_live_variant(self):
        row = {
            'id':'gid://shopify/ProductVariant/123','legacyResourceId':'123','title':'750ML','sku':'SKU1','barcode':'BC',
            'price':'14.99','inventoryQuantity':4,'createdAt':'2026-01-01T00:00:00Z','updatedAt':'2026-01-02T00:00:00Z',
            'inventoryItem':{'id':'gid://shopify/InventoryItem/9','sku':'SKU1','tracked':True,'unitCost':{'amount':'10.00','currencyCode':'USD'}},
            'product':{'id':'gid://shopify/Product/88','legacyResourceId':'88','title':'Wine','handle':'wine','status':'ACTIVE','vendor':'Empire','productType':'Wine','updatedAt':'2026-01-02T00:00:00Z'}
        }
        v = parse_live_variant(row)
        self.assertEqual(v.variant_id, '123')
        self.assertEqual(v.product_id, '88')
        self.assertEqual(v.shopify_current_cost, Decimal('10.00'))
        self.assertEqual(v.inventory_quantity, Decimal('4'))

    def test_catalog_gate_requires_complete_durable_identity_evidence(self):
        complete = {
            "live_rows_received": 2,
            "exact_current_ids": 1,
            "new_live_variants": 1,
            "unresolved_blockers": 0,
            "pagination_complete": True,
            "source_hash": "catalog-hash",
        }
        self.assertEqual(catalog_gate_blockers(**complete), ())
        cases = (
            ({"pagination_complete": False}, "PAGINATION_INCOMPLETE"),
            ({"live_rows_received": 0, "exact_current_ids": 0, "new_live_variants": 0}, "NO_LIVE_VARIANTS"),
            ({"new_live_variants": 0}, "LIVE_IDENTITY_ACCOUNTING_MISMATCH"),
            ({"source_hash": None}, "CATALOG_SNAPSHOT_HASH_MISSING"),
            ({"unresolved_blockers": 1}, "IDENTITY_BLOCKERS_UNRESOLVED"),
        )
        for override, expected in cases:
            with self.subTest(expected=expected):
                evidence = {**complete, **override}
                self.assertIn(expected, catalog_gate_blockers(**evidence))

    def test_permanent_identity_actions_require_actor_and_reason_before_database_access(self):
        calls = (
            (approve_recreated_variant, (object(), "old", "new"), {"actor": "", "note": "reason"}),
            (approve_recreated_variant, (object(), "old", "new"), {"actor": "owner", "note": " "}),
            (reject_recreation_candidate, (object(), "old", "new"), {"actor": "", "note": "reason"}),
            (retire_missing_variant, (object(), "old"), {"actor": "owner", "note": " "}),
        )
        for function, args, kwargs in calls:
            with self.subTest(function=function.__name__, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    function(*args, **kwargs)

if __name__ == '__main__': unittest.main()

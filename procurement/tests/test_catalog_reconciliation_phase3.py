"""Phase 3 adversarial reconciliation tests (approval requirement H)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest

from procurement_os.catalog import LiveVariant, SeedVariant, reconcile_catalog


def live(vid, sku=None, barcode=None, product="Prod", variant="750ml", **kw):
    return LiveVariant(
        variant_id=vid, shopify_gid=f"gid://shopify/ProductVariant/{vid}",
        product_id=f"p{vid}", product_gid=f"gid://shopify/Product/p{vid}",
        product_title=product, variant_title=variant, handle=None, status="ACTIVE",
        sku=sku, barcode=barcode, retail_price=None, shopify_current_cost=None,
        inventory_quantity=None, inventory_item_gid=None, inventory_tracked=None,
        shopify_vendor=None, product_type=None, variant_created_at=None,
        variant_updated_at=None, product_updated_at=None, **kw)


def seed(vid, sku=None, barcode=None, product="Prod", variant="750ml", active=True):
    return SeedVariant(vid, f"p{vid}", product, variant, sku, barcode, active)


def issues_of(rec, cls):
    return [i for i in rec.issues if i.classification == cls]


class Phase3ReconciliationTests(unittest.TestCase):
    def test_exact_variant_id_match(self):
        rec = reconcile_catalog([seed("1", sku="A")], [live("1", sku="A")])
        self.assertEqual(rec.exact_ids, 1)
        self.assertFalse(rec.blockers)

    def test_old_id_missing_with_one_strong_candidate_blocks(self):
        rec = reconcile_catalog([seed("1", sku="A", barcode="B1")], [live("2", sku="A", barcode="B1")])
        items = issues_of(rec, "POTENTIAL_RECREATION")
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].blocking)
        self.assertEqual(items[0].live_variant_id, "2")
        cand = items[0].evidence["candidates"][0]
        self.assertEqual(cand["confidence"], "STRONG")
        # nominated, never auto-approved
        self.assertFalse(rec.can_pass_catalog_gate)

    def test_same_sku_on_multiple_current_variants_is_ambiguous(self):
        rec = reconcile_catalog([seed("1", sku="A")], [live("2", sku="A"), live("3", sku="A")])
        items = issues_of(rec, "AMBIGUOUS_IDENTITY")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].live_variant_id)
        self.assertTrue(items[0].blocking)

    def test_matching_title_but_different_size_is_not_a_candidate(self):
        # Title similarity alone must never establish continuity.
        rec = reconcile_catalog([seed("1", product="Wine X", variant="750ml")],
                                [live("2", product="Wine X", variant="1.5L")])
        self.assertFalse(issues_of(rec, "POTENTIAL_RECREATION"))
        self.assertEqual(len(issues_of(rec, "MISSING")), 1)

    def test_matching_sku_conflicting_barcode_is_ambiguous(self):
        rec = reconcile_catalog([seed("1", sku="A", barcode="B1")], [live("2", sku="A", barcode="B2")])
        items = issues_of(rec, "AMBIGUOUS_IDENTITY")
        self.assertEqual(len(items), 1)
        cand = items[0].evidence["candidates"][0]
        self.assertIn("barcode", cand["conflicting_evidence"])
        self.assertEqual(cand["confidence"], "CONFLICTING_EVIDENCE")

    def test_matching_barcode_changed_sku_is_ambiguous(self):
        rec = reconcile_catalog([seed("1", sku="A", barcode="B1")], [live("2", sku="Z", barcode="B1")])
        items = issues_of(rec, "AMBIGUOUS_IDENTITY")
        self.assertEqual(len(items), 1)
        self.assertIn("sku", items[0].evidence["candidates"][0]["conflicting_evidence"])

    def test_old_id_with_no_replacement_is_missing_blocker(self):
        rec = reconcile_catalog([seed("1", sku="A", barcode="B1")], [live("9", sku="Q", barcode="B9")])
        self.assertEqual(len(issues_of(rec, "MISSING")), 1)
        self.assertTrue(issues_of(rec, "MISSING")[0].blocking)

    def test_genuinely_new_product_not_blocking(self):
        rec = reconcile_catalog([seed("1", sku="A")], [live("1", sku="A"), live("2", sku="N")])
        news = issues_of(rec, "NEW")
        self.assertEqual(len(news), 1)
        self.assertFalse(news[0].blocking)
        self.assertTrue(rec.can_pass_catalog_gate)

    def test_approved_alias_convergence_resolves_old_ids(self):
        rec = reconcile_catalog(
            [seed("1", sku="A"), seed("2", sku="A2")],
            [live("10", sku="A"), live("10x", sku="A2")],
            approved_aliases={"1": "10", "2": "10x"},
        )
        self.assertEqual(len(issues_of(rec, "RESOLVED")), 2)
        self.assertFalse(rec.blockers)

    def test_rejected_candidate_does_not_reappear(self):
        rec = reconcile_catalog(
            [seed("1", sku="A", barcode="B1")],
            [live("2", sku="A", barcode="B1")],
            rejected_pairs={("1", "2")},
        )
        self.assertFalse(issues_of(rec, "POTENTIAL_RECREATION"))
        self.assertFalse(issues_of(rec, "AMBIGUOUS_IDENTITY"))
        # Old identity still requires disposition — remains a MISSING blocker.
        self.assertEqual(len(issues_of(rec, "MISSING")), 1)

    def test_no_confidence_score_auto_promotes(self):
        # Even STRONG evidence must leave the item blocking and unresolved.
        rec = reconcile_catalog([seed("1", sku="A", barcode="B1")], [live("2", sku="A", barcode="B1")])
        item = issues_of(rec, "POTENTIAL_RECREATION")[0]
        self.assertEqual(item.evidence["candidates"][0]["confidence"], "STRONG")
        self.assertTrue(item.blocking)
        self.assertFalse(rec.can_pass_catalog_gate)

    def test_inactive_seed_not_blocking(self):
        rec = reconcile_catalog([seed("1", sku="A", active=False)], [live("9", sku="Q")])
        self.assertFalse([i for i in rec.blockers if i.seed_variant_id == "1"])


if __name__ == "__main__":
    unittest.main()

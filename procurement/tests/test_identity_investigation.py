"""Adversarial tests for the Phase 3 identity investigation layer (Step 7 mandate)."""
from __future__ import annotations

import os
import unittest

from procurement_os.catalog_investigation import (
    classify_deleted_vs_new,
    classify_existence,
    classify_new_variant,
    continuity_sweep_deleted,
    continuity_sweep_new,
    lookup_variant_node,
    normalize_size,
    variant_gid,
)


def seed(**kw):
    base = {"variant_id": "111", "product_title": "Black Velvet", "variant_title": "1.75L",
            "sku": "1001", "barcode": "620213101753", "vendor": "SG", "product_type": "Whisky",
            "retail_price": "21.99"}
    base.update(kw)
    return base


def new(**kw):
    base = {"variant_id": "999", "product_title": "Black Velvet", "variant_title": "1.75L",
            "sku": "1001", "barcode": "620213101753", "vendor": "SG", "product_type": "Whisky",
            "retail_price": "21.99"}
    base.update(kw)
    return base


class FakeClient:
    """Read-only fake: records every operation; refuses anything resembling a mutation."""

    def __init__(self, response):
        self.response = response
        self.operations = []

    def query(self, query_text, variables=None):
        assert "mutation" not in query_text.lower(), "Shopify writes are forbidden"
        self.operations.append((query_text, variables))
        return self.response


class ExistenceTests(unittest.TestCase):
    def test_missing_id_exists_as_draft(self):
        node = {"__typename": "ProductVariant", "id": "gid://shopify/ProductVariant/1",
                "product": {"status": "DRAFT"}}
        self.assertEqual(classify_existence(node), "STILL_EXISTS_DRAFT")

    def test_missing_id_exists_as_archived(self):
        node = {"__typename": "ProductVariant", "product": {"status": "ARCHIVED"}}
        self.assertEqual(classify_existence(node), "STILL_EXISTS_ARCHIVED")

    def test_missing_id_still_active_is_flagged_not_deleted(self):
        node = {"__typename": "ProductVariant", "product": {"status": "ACTIVE"}}
        self.assertEqual(classify_existence(node), "STILL_EXISTS_ACTIVE")

    def test_node_lookup_returning_null(self):
        client = FakeClient({"node": None})
        self.assertIsNone(lookup_variant_node(client, "12345"))
        self.assertEqual(classify_existence(None), "DELETED_OR_NO_LONGER_RESOLVABLE")

    def test_node_lookup_wrong_type_is_not_resolvable(self):
        client = FakeClient({"node": {"__typename": "Product"}})
        self.assertIsNone(lookup_variant_node(client, "12345"))

    def test_lookup_uses_exact_gid_and_read_only(self):
        client = FakeClient({"node": None})
        lookup_variant_node(client, "42")
        self.assertEqual(client.operations[0][1], {"id": "gid://shopify/ProductVariant/42"})
        self.assertEqual(variant_gid("gid://shopify/ProductVariant/7"), "gid://shopify/ProductVariant/7")


class RecreationComparisonTests(unittest.TestCase):
    def test_deleted_with_one_exact_barcode_candidate_is_strong(self):
        v = classify_deleted_vs_new(seed(sku=None), [new(sku=None)], set(), set())
        self.assertEqual(v["classification"], "STRONG_RECREATION_CANDIDATE")
        self.assertIn("human approval", v["reason"])

    def test_exact_sku_and_barcode_still_requires_human_approval(self):
        v = classify_deleted_vs_new(seed(), [new()], set(), set())
        self.assertEqual(v["classification"], "STRONG_RECREATION_CANDIDATE")
        # The verdict must never carry an auto-approve signal — only evidence.
        self.assertNotIn("approved", str(v).lower())
        self.assertIn("requires human approval", v["reason"])

    def test_duplicate_barcode_candidates_are_ambiguous(self):
        n1, n2 = new(variant_id="901", sku=None), new(variant_id="902", sku=None)
        v = classify_deleted_vs_new(seed(sku=None), [n1, n2], set(), set())
        self.assertEqual(v["classification"], "AMBIGUOUS")

    def test_duplicate_sku_candidates_are_ambiguous(self):
        n1, n2 = new(variant_id="901", barcode=None), new(variant_id="902", barcode=None)
        v = classify_deleted_vs_new(seed(barcode=None), [n1, n2], set(), set())
        self.assertEqual(v["classification"], "AMBIGUOUS")

    def test_dup_group_membership_downgrades_unique_evidence(self):
        # barcode matches but that barcode is duplicated in the live catalog -> not identity evidence
        v = classify_deleted_vs_new(seed(sku=None), [new(sku=None)], set(), {"620213101753"})
        self.assertNotEqual(v["classification"], "STRONG_RECREATION_CANDIDATE")

    def test_same_sku_different_bottle_size_never_strong(self):
        v = classify_deleted_vs_new(seed(variant_title="1.5L", barcode=None),
                                    [new(variant_title="1.75L", barcode=None)], set(), set())
        self.assertIn(v["classification"], ("POSSIBLE_RECREATION_CANDIDATE", "AMBIGUOUS"))
        self.assertTrue(any("SIZE DIFFERS" in c for c in v["candidates"][0]["conflicting"]))

    def test_same_title_different_size_no_identifier_no_candidate(self):
        v = classify_deleted_vs_new(seed(sku=None, barcode=None, variant_title="750ML"),
                                    [new(sku=None, barcode=None, variant_title="375ML")], set(), set())
        self.assertEqual(v["classification"], "NO_RECREATION_CANDIDATE")

    def test_gift_pack_like_replacement_not_strong(self):
        v = classify_deleted_vs_new(
            seed(product_title="Glenfiddich 12", barcode=None),
            [new(product_title="Glenfiddich 12 Gift Set w/ Glasses", barcode=None)], set(), set())
        self.assertIn(v["classification"], ("POSSIBLE_RECREATION_CANDIDATE", "AMBIGUOUS"))

    def test_title_similarity_alone_never_nominates(self):
        v = classify_deleted_vs_new(seed(sku="A1", barcode="X1"),
                                    [new(sku="B2", barcode="Y2")], set(), set())
        self.assertEqual(v["classification"], "NO_RECREATION_CANDIDATE")

    def test_size_normalization(self):
        self.assertEqual(normalize_size("1.75L"), "1.75L")
        self.assertEqual(normalize_size("Black Velvet 1.5 Liter"), "1.5L")
        self.assertEqual(normalize_size("12pk-200ML Cans"), "12PK+200ML")
        self.assertIsNone(normalize_size("Default Title"))


class ReverseViewTests(unittest.TestCase):
    def test_new_variant_with_clean_predecessor_is_likely_recreation(self):
        v = classify_new_variant(new(), [seed()], set(), set())
        self.assertEqual(v["classification"], "LIKELY_RECREATION")

    def test_new_variant_without_predecessor_is_genuinely_new(self):
        v = classify_new_variant(new(sku="ZZZ", barcode="000"), [seed()], set(), set())
        self.assertEqual(v["classification"], "GENUINELY_NEW")

    def test_new_variant_in_dup_barcode_group_heightened(self):
        v = classify_new_variant(new(), [], set(), {"620213101753"})
        self.assertTrue(v["heightened_review"])


class ContinuitySweepTests(unittest.TestCase):
    """Extended sweep: full identity evidence beyond SKU/barcode (owner directive)."""

    def test_same_normalized_product_and_size_with_changed_sku_barcode_is_candidate(self):
        v = continuity_sweep_deleted(seed(sku="OLD1", barcode=None),
                                     [new(sku="NEW9", barcode=None)], set(), set())
        self.assertIn(v["classification"], ("HIGH_EVIDENCE_RECREATION_REVIEW", "POSSIBLE_RECREATION_REVIEW"))
        self.assertTrue(v["candidates"])
        self.assertIn("human approval", v["candidates"] and v["reason"])

    def test_changed_barcode_downgrades_from_high_evidence(self):
        v = continuity_sweep_deleted(seed(sku=None, barcode="111111111111"),
                                     [new(sku=None, barcode="222222222222")], set(), set())
        self.assertEqual(v["classification"], "POSSIBLE_RECREATION_REVIEW")

    def test_same_brand_different_expression_is_no_candidate(self):
        v = continuity_sweep_deleted(
            seed(product_title="Glenfiddich 12 Year", sku=None, barcode=None),
            [new(product_title="Glenfiddich 15 Year", sku=None, barcode=None)], set(), set())
        self.assertEqual(v["classification"], "NO_CREDIBLE_CURRENT_COUNTERPART")

    def test_same_title_different_size_is_blocked(self):
        v = continuity_sweep_deleted(seed(variant_title="1.5L", sku=None, barcode=None),
                                     [new(variant_title="1.75L", sku=None, barcode=None)], set(), set())
        self.assertEqual(v["classification"], "NO_CREDIBLE_CURRENT_COUNTERPART")

    def test_conflicting_proof_is_flagged(self):
        v = continuity_sweep_deleted(
            seed(product_title="Old Grand-Dad 80 Proof", sku=None, barcode=None),
            [new(product_title="Old Grand-Dad 100 Proof", sku=None, barcode=None)], set(), set())
        self.assertEqual(v["classification"], "NO_CREDIBLE_CURRENT_COUNTERPART")
        # even with a barcode match, the proof conflict must be surfaced as material
        v2 = continuity_sweep_deleted(
            seed(product_title="Old Grand-Dad 80 Proof", sku=None),
            [new(product_title="Old Grand-Dad 100 Proof", sku=None)], set(), set())
        self.assertEqual(v2["classification"], "CONFLICT_AMBIGUOUS")
        self.assertTrue(any("PROOF CONFLICT" in c for c in v2["candidates"][0]["conflicting"]))

    def test_conflicting_abv_is_flagged(self):
        v = continuity_sweep_deleted(
            seed(product_title="Seltzer 5%", sku=None),
            [new(product_title="Seltzer 8%", sku=None)], set(), set())
        self.assertEqual(v["classification"], "CONFLICT_AMBIGUOUS")

    def test_gift_pack_conflict_is_flagged(self):
        v = continuity_sweep_deleted(
            seed(product_title="Glenfiddich 12", sku=None),
            [new(product_title="Glenfiddich 12 Gift Set", sku=None)], set(), set())
        self.assertIn(v["classification"], ("CONFLICT_AMBIGUOUS", "NO_CREDIBLE_CURRENT_COUNTERPART"))

    def test_multiple_matching_new_products_is_ambiguous(self):
        n1, n2 = new(variant_id="901", sku=None, barcode=None), new(variant_id="902", sku=None, barcode=None)
        v = continuity_sweep_deleted(seed(sku=None, barcode=None), [n1, n2], set(), set())
        self.assertEqual(v["classification"], "CONFLICT_AMBIGUOUS")

    def test_sweep_never_creates_alias_automatically(self):
        import inspect

        import procurement_os.catalog_investigation as mod
        src = inspect.getsource(mod)
        for forbidden in ("variant_aliases", "approve_recreated_variant", "retire_missing_variant"):
            self.assertNotIn(forbidden, src, f"investigation module must never touch {forbidden}")
        v = continuity_sweep_deleted(seed(), [new()], set(), set())
        self.assertNotIn("approved", json_dumps_lower(v))

    def test_reverse_sweep_demonstrates_full_evidence_check(self):
        v = continuity_sweep_new(new(sku="Z", barcode="0"), [seed()], set(), set())
        self.assertEqual(v["historical_ids_checked"], 1)
        self.assertIn("proof", v["evidence_used"])


def json_dumps_lower(obj) -> str:
    import json
    return json.dumps(obj).lower()


class SafetyTests(unittest.TestCase):
    def test_no_permanent_decision_without_review_token(self):
        from fastapi import HTTPException
        from procurement_os.api import _require_review_token
        saved = os.environ.pop("RECONCILIATION_REVIEW_TOKEN", None)
        try:
            with self.assertRaises(HTTPException) as ctx:
                _require_review_token("anything")
            self.assertEqual(ctx.exception.status_code, 503)  # fail-closed when unconfigured
            os.environ["RECONCILIATION_REVIEW_TOKEN"] = "correct-token"
            with self.assertRaises(HTTPException) as ctx:
                _require_review_token("wrong")
            self.assertEqual(ctx.exception.status_code, 403)
            _require_review_token("correct-token")  # must not raise
        finally:
            if saved is None:
                os.environ.pop("RECONCILIATION_REVIEW_TOKEN", None)
            else:
                os.environ["RECONCILIATION_REVIEW_TOKEN"] = saved

    def test_investigation_module_contains_no_shopify_mutations(self):
        import inspect

        import procurement_os.catalog_investigation as mod
        from procurement_os.shopify import queries
        self.assertNotIn("mutation", inspect.getsource(mod).lower())
        self.assertNotIn("mutation", queries.VARIANT_NODE_LOOKUP_QUERY.lower())

    def test_catalog_sync_gate_fails_while_blockers_remain(self):
        # reconcile output with blockers must map to FAIL, never PASS
        from procurement_os.catalog import LiveVariant, SeedVariant, reconcile_catalog
        s = [SeedVariant("1", "p1", "Old Gone", "750ML", "S1", "B1", True)]
        rec = reconcile_catalog(s, [])
        self.assertTrue(rec.blockers)
        status = "PASS" if not rec.blockers else "FAIL"
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()

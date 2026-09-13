"""Authenticated HTTP acceptance tests for the shadow-only mapping surface."""
from __future__ import annotations

from contextlib import nullcontext
from unittest import mock
from uuid import UUID, uuid4
import unittest

from procurement_os import api
from procurement_os.persistent_mapping import PersistentMappingError

from http_auth_support import authenticated_test_client


CANDIDATE_ID = UUID("30000000-0000-4000-8000-000000000001")
DECISION_ID = UUID("40000000-0000-4000-8000-000000000001")
PREVIEW_SHA = "a" * 64


class _Connection:
    def __init__(self) -> None:
        self.executed: list[tuple[object, tuple[object, ...]]] = []
        self.rollbacks = 0

    def execute(self, statement, parameters=()):
        self.executed.append((statement, tuple(parameters)))
        return self

    def rollback(self) -> None:
        self.rollbacks += 1


def _detail(*, approved: bool = False, selected: bool = False) -> dict:
    candidate = {
        "candidate_id": CANDIDATE_ID,
        "occurrence_key": "fabricated-<unsafe>-occurrence",
        "source_vendor_identity": "Synthetic Southern",
        "proposed_variant_id": "1001",
        "proposed_vendor_id": "00000000-0000-4000-8000-000000000001",
        "offer_class": "REGULAR",
        "occurrence_role": "PRIMARY",
        "supplier_identity_key_sha256": "b" * 64,
        "operational_offer_key_sha256": "c" * 64,
        "source_file_name": "../../untrusted-source-name.txt",
        "source_file_sha256": "d" * 64,
        "source_page_start": 1,
        "source_page_end": 1,
        "blockers": [],
        "independent_linkage_evidence": [{"fixture_disclosure": "FABRICATED_TEST_EVIDENCE"}],
        "distributor_product_id_state": "VALUE",
        "distributor_product_id_value": "SUP-001",
        "supplier_code_state": "VALUE",
        "supplier_code_value": "SUP-001",
        "package_type_state": "EXPLICIT_NULL",
        "size_state": "ABSENT",
        "raw_pack_state": "VALUE",
        "raw_pack_value": "6x750ML",
        "physical_units_state": "VALUE",
        "physical_units_value": "6.0000",
        "retail_pack_units_state": "ABSENT",
        "shopify_units_state": "VALUE",
        "shopify_units_value": "6.0000",
        "qualifying_units_state": "VALUE",
        "qualifying_units_value": "6.0000",
        "assortment_scope_state": "VALUE",
        "assortment_scope_value": "PRODUCT",
        "assortment_group_state": "EXPLICIT_NULL",
        "assortable_state": "VALUE",
        "assortable_value": False,
    }
    effective = (
        {
            "mapping_decision_id": DECISION_ID,
            "action": "APPROVE_MAPPING",
            "human_principal_ref": "synthetic:http-test-owner:01",
        }
        if approved
        else None
    )
    return {
        "candidate": candidate,
        "batch": {
            "source_package_id": "fabricated-authoritative-format-review-v1",
            "source_revision": "1",
            "structural_state": "READY",
            "source_evidence_state": "READY",
            "semantic_state": "READY",
            "source_authority_state": "NOT_APPROVED",
            "source_import_state": "NOT_IMPORT_READY",
            "source_is_simulation": False,
        },
        "offer_comparisons": [
            {
                "offer": {
                    "offer_id": 77,
                    "supplier_sku": "SUP-001",
                    "package_type": "STANDARD",
                    "active": True,
                    "confidence": "VERIFIED",
                },
                "exact_contract_match": True,
            }
        ],
        "decisions": [],
        "rejections": [
            {
                "rejected_at": "2026-09-13T06:00:00Z",
                "rejected_variant_id": "1001",
                "vendor_id": "00000000-0000-4000-8000-000000000001",
                "source_text": "SUP-001",
                "rejected_by": "synthetic:http-test-owner:01",
            }
        ],
        "effective_decision": effective,
        "shadow": (
            [
                {
                    "variant_id": "1001",
                    "selection_state": "SELECTED_ACTIVE",
                    "shadow_comparison": "MATCH",
                }
            ]
            if selected
            else []
        ),
    }


class PersistentMappingHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = authenticated_test_client(self, api.app)

    def test_server_paged_list_passes_filters_escapes_rows_and_links_pages(self):
        connection = _Connection()
        item = dict(_detail()["candidate"])
        item["decision_action"] = None
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(
                api,
                "list_mapping_candidates",
                return_value={"items": [item], "total": 80, "limit": 25, "offset": 25},
            ) as listing,
            mock.patch.object(
                api,
                "mapping_status",
                return_value={
                    "review_batch_count": 2,
                    "candidate_count": 80,
                    "decision_count": 1,
                    "selection_head_count": 0,
                    "shadow_match_count": 0,
                },
            ),
        ):
            response = self.client.get(
                "/supplier-mapping?query=%3Cscript%3E&supplier=Southern&status=PENDING&limit=25&offset=25"
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>", response.text)
        self.assertIn("&lt;unsafe&gt;", response.text)
        self.assertIn("rel='prev'", response.text)
        self.assertIn("rel='next'", response.text)
        self.assertIn("REJECT_MAPPING", response.text)
        self.assertEqual(listing.call_args.kwargs["offset"], 25)
        self.assertEqual(listing.call_args.kwargs["limit"], 25)
        with (
            mock.patch.dict(
                "os.environ", {"BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "0"}
            ),
            mock.patch.object(
                api, "_db_conn", side_effect=AssertionError("database reached")
            ) as database,
        ):
            disabled = self.client.get("/supplier-mapping")
        self.assertEqual(disabled.status_code, 400)
        database.assert_not_called()

    def test_detail_preserves_three_state_evidence_and_shadow_only_banner(self):
        connection = _Connection()
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail()),
        ):
            response = self.client.get(f"/supplier-mapping/{CANDIDATE_ID}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("VALUE: SUP-001", response.text)
        self.assertIn("EXPLICIT_NULL", response.text)
        self.assertIn("ABSENT", response.text)
        self.assertIn("SHADOW ONLY", response.text)
        self.assertIn("Active exact rejection memory", response.text)
        self.assertIn("SUP-001", response.text)
        self.assertNotIn("<unsafe>", response.text)
        blocked = _detail()
        blocked["candidate"]["blockers"] = ["IDENTITY_UNRESOLVED"]
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(_Connection())),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=blocked),
        ):
            blocked_response = self.client.get(f"/supplier-mapping/{CANDIDATE_ID}")
        self.assertIn("<td>77</td>", blocked_response.text)
        self.assertNotIn("value='APPROVE_MAPPING'", blocked_response.text)

    def test_source_download_is_bounded_metadata_with_server_fixed_filename(self):
        connection = _Connection()
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail()),
        ):
            response = self.client.get(f"/supplier-mapping/{CANDIDATE_ID}/source")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.headers["content-disposition"],
            f"attachment; filename=mapping-evidence-{CANDIDATE_ID}.json",
        )
        payload = response.json()
        self.assertEqual(payload["safety_label"], "TEST DATA — NOT FOR ORDERING")
        self.assertEqual(payload["data_mode"], "SYNTHETIC_DEMO")
        self.assertIn("fabricated authoritative-format", payload["source_disclosure"])
        self.assertIn("not real supplier evidence or approval", payload["source_disclosure"])
        self.assertEqual(payload["contract"], "BUFFALO_MAPPING_EVIDENCE_METADATA_ONLY_V1")
        self.assertEqual(payload["authority"], "REVIEW_ONLY_NOT_SOURCE_BLOB")
        self.assertEqual(
            set(payload),
            {
                "safety_label",
                "data_mode",
                "source_disclosure",
                "contract",
                "authority",
                "candidate",
                "batch",
            },
        )
        self.assertNotIn("../../untrusted-source-name.txt", response.headers["content-disposition"])

    def test_fixed_synthetic_intake_uses_action_principal_and_exactly_two_packets(self):
        packets = [
            {"package": {"id": value}, "candidates": [{"id": value}], "intake_idempotency_key": uuid4()}
            for value in (1, 2)
        ]
        with (
            mock.patch.object(api, "load_synthetic_mapping_packets", return_value=packets),
            mock.patch.object(api, "_database_url", return_value="synthetic-db-url"),
            mock.patch.object(api, "execute_supplier_mapping_intake", return_value={"created": True}) as execute,
        ):
            response = self.client.post("/supplier-mapping/intake", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(execute.call_count, 2)
        for call in execute.call_args_list:
            principal = call.kwargs["principal"]
            self.assertEqual(principal.principal_ref, "synthetic:http-test-owner:01")
            self.assertEqual(principal.role_ref, "procurement.review.intake")
            self.assertRegex(principal.authn_context_sha256, r"^[0-9a-f]{64}$")

    def test_decision_preview_is_write_free_and_shows_exact_human_intent(self):
        connection = _Connection()
        key = uuid4()
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail()),
            mock.patch.object(api, "preview_mapping_decision", return_value={"preview_sha256": PREVIEW_SHA}) as preview,
            mock.patch.object(api, "execute_mapping_decision") as execute,
        ):
            response = self.client.post(
                f"/supplier-mapping/{CANDIDATE_ID}/decision",
                data={"action": "DEFER", "reason": "Needs human evidence", "idempotency_key": str(key)},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(connection.rollbacks, 1)
        execute.assert_not_called()
        self.assertIn("DEFER", response.text)
        self.assertIn("Needs human evidence", response.text)
        self.assertIn(PREVIEW_SHA, response.text)
        self.assertEqual(preview.call_args.kwargs["principal"].role_ref, "procurement.mapping.approve")

    def test_linked_existing_approval_rejects_an_uncompared_offer_before_preview(self):
        connection = _Connection()
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail()),
            mock.patch.object(api, "preview_mapping_decision") as preview,
            mock.patch.object(api, "execute_mapping_decision") as execute,
        ):
            response = self.client.post(
                f"/supplier-mapping/{CANDIDATE_ID}/decision",
                data={
                    "action": "APPROVE_MAPPING",
                    "offer_link_kind": "LINKED_EXISTING",
                    "existing_offer_id": "999",
                    "reason": "Wrong offer",
                    "idempotency_key": str(uuid4()),
                },
            )
        self.assertEqual(response.status_code, 409)
        preview.assert_not_called()
        execute.assert_not_called()

    def test_confirmed_created_inactive_decision_executes_with_server_principal(self):
        connection = _Connection()
        key = uuid4()
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail()),
            mock.patch.object(api, "preview_mapping_decision", return_value={"preview_sha256": PREVIEW_SHA}),
            mock.patch.object(api, "_database_url", return_value="synthetic-db-url"),
            mock.patch.object(api, "execute_mapping_decision", return_value={"created": True}) as execute,
        ):
            response = self.client.post(
                f"/supplier-mapping/{CANDIDATE_ID}/decision",
                data={
                    "action": "APPROVE_MAPPING",
                    "offer_link_kind": "CREATED_INACTIVE",
                    "reason": "Exact fabricated evidence",
                    "idempotency_key": str(key),
                    "expected_preview_sha256": PREVIEW_SHA,
                    "confirm": "CONFIRM",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(execute.call_args.kwargs["offer_link_kind"], "CREATED_INACTIVE")
        self.assertIsNone(execute.call_args.kwargs["existing_offer_id"])
        self.assertEqual(execute.call_args.kwargs["principal"].role_ref, "procurement.mapping.approve")

    def test_selection_preview_is_separate_write_free_and_action_specific(self):
        connection = _Connection()
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail(approved=True)),
            mock.patch.object(api, "preview_routine_offer_selection", return_value={"preview_sha256": PREVIEW_SHA}) as preview,
            mock.patch.object(api, "execute_routine_offer_selection") as execute,
        ):
            response = self.client.post(
                f"/supplier-mapping/{CANDIDATE_ID}/selection",
                data={
                    "selection_action": "SELECT",
                    "reason": "Separate shadow selection",
                    "effective_from": "2026-09-13",
                    "idempotency_key": str(uuid4()),
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(connection.rollbacks, 1)
        execute.assert_not_called()
        self.assertIn("Separate shadow selection", response.text)
        self.assertEqual(preview.call_args.kwargs["principal"].role_ref, "procurement.offer.select")

    def test_confirmed_select_and_clear_call_distinct_services(self):
        for action in ("SELECT", "CLEAR"):
            with self.subTest(action=action):
                connection = _Connection()
                with (
                    mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
                    mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail(approved=True, selected=True)),
                    mock.patch.object(api, "preview_routine_offer_selection", return_value={"preview_sha256": PREVIEW_SHA}),
                    mock.patch.object(api, "preview_routine_offer_clear", return_value={"preview_sha256": PREVIEW_SHA}),
                    mock.patch.object(api, "_database_url", return_value="synthetic-db-url"),
                    mock.patch.object(api, "execute_routine_offer_selection", return_value={"created": True}) as select,
                    mock.patch.object(api, "execute_routine_offer_clear", return_value={"created": True}) as clear,
                ):
                    response = self.client.post(
                        f"/supplier-mapping/{CANDIDATE_ID}/selection",
                        data={
                            "selection_action": action,
                            "reason": f"Confirmed {action}",
                            "effective_from": "2026-09-13",
                            "idempotency_key": str(uuid4()),
                            "expected_preview_sha256": PREVIEW_SHA,
                            "confirm": "CONFIRM",
                        },
                        follow_redirects=False,
                    )
                self.assertEqual(response.status_code, 303)
                self.assertEqual(select.call_count, int(action == "SELECT"))
                self.assertEqual(clear.call_count, int(action == "CLEAR"))

    def test_mapping_timeout_is_503_while_material_refusal_is_409(self):
        transient_codes = (
            "SESSION_LOCK_TIMEOUT",
            "SESSION_LOCK_CLEANUP_FAILED",
            "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED",
            "COMMIT_OUTCOME_UNKNOWN",
        )
        packets = [
            {"package": {}, "candidates": [{}], "intake_idempotency_key": uuid4()},
            {"package": {}, "candidates": [{}], "intake_idempotency_key": uuid4()},
        ]
        for code in transient_codes:
            with self.subTest(route="intake", code=code):
                with (
                    mock.patch.object(api, "load_synthetic_mapping_packets", return_value=packets),
                    mock.patch.object(
                        api,
                        "execute_supplier_mapping_intake",
                        side_effect=PersistentMappingError("refused safely", code=code),
                    ),
                ):
                    response = self.client.post(
                        "/supplier-mapping/intake", follow_redirects=False
                    )
                self.assertEqual(response.status_code, 503)
            with self.subTest(route="decision", code=code):
                connection = _Connection()
                with (
                    mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
                    mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail()),
                    mock.patch.object(
                        api,
                        "preview_mapping_decision",
                        side_effect=PersistentMappingError("refused safely", code=code),
                    ),
                ):
                    response = self.client.post(
                        f"/supplier-mapping/{CANDIDATE_ID}/decision",
                        data={
                            "action": "DEFER",
                            "reason": "Bounded error",
                            "idempotency_key": str(uuid4()),
                        },
                    )
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("synthetic-db-url", response.text)
            with self.subTest(route="selection", code=code):
                connection = _Connection()
                with (
                    mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
                    mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail(approved=True)),
                    mock.patch.object(api, "preview_routine_offer_selection", return_value={"preview_sha256": PREVIEW_SHA}),
                    mock.patch.object(api, "_database_url", return_value="synthetic-db-url"),
                    mock.patch.object(
                        api,
                        "execute_routine_offer_selection",
                        side_effect=PersistentMappingError("refused safely", code=code),
                    ),
                ):
                    response = self.client.post(
                        f"/supplier-mapping/{CANDIDATE_ID}/selection",
                        data={
                            "selection_action": "SELECT",
                            "reason": "Transient refusal",
                            "effective_from": "2026-09-13",
                            "idempotency_key": str(uuid4()),
                            "expected_preview_sha256": PREVIEW_SHA,
                            "confirm": "CONFIRM",
                        },
                    )
                self.assertEqual(response.status_code, 503)
        connection = _Connection()
        with (
            mock.patch.object(api, "_db_conn", return_value=nullcontext(connection)),
            mock.patch.object(api, "get_mapping_candidate_detail", return_value=_detail()),
            mock.patch.object(
                api,
                "preview_mapping_decision",
                side_effect=PersistentMappingError("material refusal", code="STALE_PREVIEW"),
            ),
        ):
            response = self.client.post(
                f"/supplier-mapping/{CANDIDATE_ID}/decision",
                data={"action": "DEFER", "reason": "Stale", "idempotency_key": str(uuid4())},
            )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()

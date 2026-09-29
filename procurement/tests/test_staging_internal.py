"""Authenticated gateway-to-worker assertion contract."""
from __future__ import annotations

import unittest

from procurement_os.staging_internal import (
    ASSERTION_TTL_SECONDS,
    AssertionError,
    NonceReplayCache,
    mint_assertion,
    verify_assertion,
)


KEY = bytes.fromhex("11" * 32)


class StagingInternalAssertionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.cache = NonceReplayCache(maximum_entries=32)
        self.arguments = {
            "key": KEY,
            "worker_role": "synthetic",
            "request_id": "req-0001",
            "session_digest": "a" * 64,
            "principal_ref": "owner:railway-staging:01",
            "role_ref": "RAILWAY_STAGING_OWNER",
            "capabilities": frozenset(
                {"procurement.review.read", "procurement.order.approve"}
            ),
            "method": "POST",
            "path": "/monday-runs/example/review",
            "query": b"",
            "body": b"quantity=2",
            "origin": "https://staging.example.test",
            "issued_at": self.now,
            "nonce": "b" * 64,
        }

    def _token(self, **changes):
        values = dict(self.arguments)
        values.update(changes)
        return mint_assertion(**values)

    def _verify(self, token, **changes):
        expected = {
            "token": token,
            "key": KEY,
            "expected_worker_role": "synthetic",
            "method": "POST",
            "path": "/monday-runs/example/review",
            "query": b"",
            "body": b"quantity=2",
            "expected_origin": "https://staging.example.test",
            "now": self.now + 1,
            "nonce_cache": self.cache,
        }
        expected.update(changes)
        return verify_assertion(**expected)

    def test_round_trip_binds_server_owned_identity_and_capabilities(self):
        verified = self._verify(self._token())
        self.assertEqual(verified.worker_role, "synthetic")
        self.assertEqual(verified.principal_ref, "owner:railway-staging:01")
        self.assertEqual(verified.role_ref, "RAILWAY_STAGING_OWNER")
        self.assertEqual(
            verified.capabilities,
            frozenset(
                {"procurement.order.approve", "procurement.review.read"}
            ),
        )
        self.assertEqual(verified.request_id, "req-0001")

    def test_assertion_is_single_use(self):
        token = self._token()
        self._verify(token)
        with self.assertRaisesRegex(AssertionError, "replayed"):
            self._verify(token)

    def test_expired_and_future_assertions_are_refused(self):
        with self.assertRaisesRegex(AssertionError, "expired"):
            self._verify(
                self._token(issued_at=self.now - ASSERTION_TTL_SECONDS - 1)
            )
        with self.assertRaisesRegex(AssertionError, "future"):
            self._verify(self._token(issued_at=self.now + 5))

    def test_wrong_key_worker_origin_and_signature_are_refused(self):
        token = self._token()
        cases = (
            {"key": bytes.fromhex("22" * 32)},
            {"expected_worker_role": "research"},
            {"expected_origin": "https://other.example.test"},
        )
        for expected in cases:
            with self.subTest(expected=expected), self.assertRaises(AssertionError):
                self._verify(token, **expected)
        altered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaises(AssertionError):
            self._verify(altered)

    def test_method_path_query_and_body_are_bound(self):
        token = self._token()
        cases = (
            {"method": "GET"},
            {"path": "/monday-runs/other/review"},
            {"query": b"unexpected=1"},
            {"body": b"quantity=3"},
        )
        for expected in cases:
            with self.subTest(expected=expected), self.assertRaises(AssertionError):
                self._verify(token, **expected)

    def test_noncanonical_input_and_client_selected_shape_are_refused(self):
        invalid_changes = (
            {"worker_role": "SYNTHETIC"},
            {"method": "post"},
            {"path": "monday-runs"},
            {"path": "/monday-runs/../other"},
            {"origin": "http://staging.example.test"},
            {"capabilities": frozenset({"bad capability"})},
            {"nonce": "short"},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                self._token(**changes)

    def test_nonce_cache_is_bounded_and_prunes_expired_entries(self):
        cache = NonceReplayCache(maximum_entries=2)
        for index in range(2):
            token = self._token(
                request_id=f"req-{index}",
                nonce=f"{index + 1:064x}",
            )
            self._verify(token, nonce_cache=cache)
        third = self._token(request_id="req-3", nonce=f"{3:064x}")
        with self.assertRaisesRegex(AssertionError, "capacity"):
            self._verify(third, nonce_cache=cache)
        later = self.now + ASSERTION_TTL_SECONDS + 3
        after_expiry = self._token(
            request_id="req-4",
            nonce=f"{4:064x}",
            issued_at=later - 1,
        )
        verified = self._verify(after_expiry, nonce_cache=cache, now=later)
        self.assertEqual(verified.request_id, "req-4")


if __name__ == "__main__":
    unittest.main()

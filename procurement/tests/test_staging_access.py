"""Owner authentication and session primitives for Railway staging."""
from __future__ import annotations

import base64
import unittest

from procurement_os.staging_access import (
    ARGON2_HASH_LEN,
    ARGON2_MEMORY_COST_KIB,
    ARGON2_PARALLELISM,
    ARGON2_SALT_LEN,
    ARGON2_TIME_COST,
    CSRF_COOKIE,
    LOGIN_CHALLENGE_COOKIE,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_COOKIE,
    SESSION_IDLE_SECONDS,
    AuthenticationBusy,
    AuthenticationThrottled,
    LoginChallengeStore,
    OwnerPasswordAuthenticator,
    StagingAccessError,
    StagingSessionStore,
    cookie_settings,
    generate_owner_credential,
    verifier_fingerprint,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class StagingAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.credential = generate_owner_credential()

    def test_generated_credential_has_entropy_and_exact_argon2id_policy(self):
        decoded = base64.urlsafe_b64decode(self.credential.passphrase + "=")
        self.assertEqual(len(decoded), 32)
        self.assertNotIn("=", self.credential.passphrase)
        self.assertTrue(self.credential.verifier.startswith("$argon2id$v=19$"))
        authenticator = OwnerPasswordAuthenticator(
            self.credential.verifier, clock=self.clock
        )
        self.assertEqual(authenticator.parameters.memory_cost, ARGON2_MEMORY_COST_KIB)
        self.assertEqual(authenticator.parameters.time_cost, ARGON2_TIME_COST)
        self.assertEqual(authenticator.parameters.parallelism, ARGON2_PARALLELISM)
        self.assertEqual(authenticator.parameters.salt_len, ARGON2_SALT_LEN)
        self.assertEqual(authenticator.parameters.hash_len, ARGON2_HASH_LEN)
        self.assertNotIn(self.credential.passphrase, repr(self.credential))
        self.assertNotIn(self.credential.verifier, repr(self.credential))

    def test_weaker_or_non_argon2id_verifier_is_refused(self):
        weak = self.credential.verifier.replace(
            f"m={ARGON2_MEMORY_COST_KIB}", "m=1024", 1
        )
        with self.assertRaisesRegex(StagingAccessError, "parameters"):
            OwnerPasswordAuthenticator(weak, clock=self.clock)
        with self.assertRaisesRegex(StagingAccessError, "Argon2id"):
            OwnerPasswordAuthenticator(
                self.credential.verifier.replace("$argon2id$", "$argon2i$", 1),
                clock=self.clock,
            )

    def test_malformed_or_resource_amplifying_verifier_is_refused_at_startup(self):
        verifier = self.credential.verifier
        salt, digest = verifier.rsplit("$", 2)[1:]
        mutations = (
            verifier.replace(salt, "!" + salt[1:], 1),
            verifier.replace(digest, "!" + digest[1:], 1),
            verifier + "garbage",
            verifier.replace("v=19", "v=16", 1),
            verifier.replace(
                f"m={ARGON2_MEMORY_COST_KIB}", "m=999999999", 1
            ),
            verifier.replace(f"t={ARGON2_TIME_COST}", "t=999999999", 1),
            verifier.replace(f"p={ARGON2_PARALLELISM}", "p=999999999", 1),
            verifier.replace(salt, salt[:-1], 1),
            verifier.replace(digest, digest[:-1], 1),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated[:48]), self.assertRaisesRegex(
                StagingAccessError, "exact Argon2id parameters"
            ):
                OwnerPasswordAuthenticator(mutated, clock=self.clock)

    def test_correct_and_wrong_passphrases_return_only_boolean(self):
        authenticator = OwnerPasswordAuthenticator(
            self.credential.verifier, clock=self.clock
        )
        self.assertTrue(authenticator.verify(self.credential.passphrase))
        self.assertFalse(authenticator.verify("wrong-owner-passphrase"))

    def test_password_verification_has_one_slot_and_no_waiting_queue(self):
        authenticator = OwnerPasswordAuthenticator(
            self.credential.verifier, clock=self.clock
        )
        self.assertTrue(authenticator._slot.acquire(blocking=False))
        try:
            with self.assertRaises(AuthenticationBusy):
                authenticator.verify(self.credential.passphrase)
        finally:
            authenticator._slot.release()
        self.assertTrue(authenticator.verify(self.credential.passphrase))

    def test_failures_trigger_global_cooldown_without_client_ip(self):
        authenticator = OwnerPasswordAuthenticator(
            self.credential.verifier,
            clock=self.clock,
            failure_limit=2,
            failure_window_seconds=60,
            base_cooldown_seconds=4,
            maximum_cooldown_seconds=30,
        )
        self.assertFalse(authenticator.verify("wrong-one"))
        self.clock.advance(1)
        self.assertFalse(authenticator.verify("wrong-two"))
        with self.assertRaises(AuthenticationThrottled):
            authenticator.verify(self.credential.passphrase)
        self.clock.advance(5)
        self.assertTrue(authenticator.verify(self.credential.passphrase))

    def test_session_token_is_opaque_and_only_digest_is_retained(self):
        store = StagingSessionStore(clock=self.clock)
        issued = store.create(
            principal_ref="owner:railway-staging:01",
            role_ref="RAILWAY_STAGING_OWNER",
            capabilities=frozenset({"procurement.review.read"}),
            credential_fingerprint=verifier_fingerprint(self.credential.verifier),
        )
        self.assertGreaterEqual(len(issued.token), 40)
        self.assertGreaterEqual(len(issued.csrf_token), 40)
        self.assertNotIn(issued.token, repr(store._sessions))
        session = store.authenticate(
            issued.token,
            credential_fingerprint=verifier_fingerprint(self.credential.verifier),
        )
        self.assertIsNotNone(session)
        self.assertEqual(session.principal_ref, "owner:railway-staging:01")
        self.assertRegex(session.token_digest_hex, r"^[0-9a-f]{64}$")
        self.assertNotIn(issued.token, repr(issued))
        self.assertNotIn(issued.csrf_token, repr(issued))

    def test_idle_absolute_rotation_logout_and_restart_invalidate_sessions(self):
        fingerprint = verifier_fingerprint(self.credential.verifier)
        store = StagingSessionStore(clock=self.clock)
        idle = store.create(
            principal_ref="owner",
            role_ref="owner-role",
            capabilities=frozenset(),
            credential_fingerprint=fingerprint,
        )
        self.clock.advance(SESSION_IDLE_SECONDS + 1)
        self.assertIsNone(
            store.authenticate(idle.token, credential_fingerprint=fingerprint)
        )

        absolute_store = StagingSessionStore(
            clock=self.clock, idle_seconds=100, absolute_seconds=100
        )
        absolute = absolute_store.create(
            principal_ref="owner",
            role_ref="owner-role",
            capabilities=frozenset(),
            credential_fingerprint=fingerprint,
        )
        self.clock.advance(99)
        self.assertIsNotNone(
            absolute_store.authenticate(
                absolute.token, credential_fingerprint=fingerprint
            )
        )
        self.clock.advance(2)
        self.assertIsNone(
            absolute_store.authenticate(
                absolute.token, credential_fingerprint=fingerprint
            )
        )

        rotated = store.create(
            principal_ref="owner",
            role_ref="owner-role",
            capabilities=frozenset(),
            credential_fingerprint=fingerprint,
        )
        self.assertIsNone(
            store.authenticate(
                rotated.token,
                credential_fingerprint=verifier_fingerprint(
                    generate_owner_credential().verifier
                ),
            )
        )

        logout = store.create(
            principal_ref="owner",
            role_ref="owner-role",
            capabilities=frozenset(),
            credential_fingerprint=fingerprint,
        )
        store.destroy(logout.token)
        self.assertIsNone(
            store.authenticate(logout.token, credential_fingerprint=fingerprint)
        )

        restart = store.create(
            principal_ref="owner",
            role_ref="owner-role",
            capabilities=frozenset(),
            credential_fingerprint=fingerprint,
        )
        store.clear()
        self.assertIsNone(
            store.authenticate(restart.token, credential_fingerprint=fingerprint)
        )

    def test_session_csrf_is_exact_and_constant_time_checked(self):
        fingerprint = verifier_fingerprint(self.credential.verifier)
        store = StagingSessionStore(clock=self.clock)
        issued = store.create(
            principal_ref="owner",
            role_ref="owner-role",
            capabilities=frozenset(),
            credential_fingerprint=fingerprint,
        )
        session = store.authenticate(
            issued.token, credential_fingerprint=fingerprint
        )
        self.assertTrue(store.validate_csrf(session, issued.csrf_token))
        self.assertFalse(store.validate_csrf(session, issued.csrf_token + "x"))
        self.assertFalse(store.validate_csrf(None, issued.csrf_token))

    def test_login_challenge_is_bound_single_use_and_expires(self):
        challenges = LoginChallengeStore(clock=self.clock, ttl_seconds=120)
        issued = challenges.create()
        self.assertNotIn(issued.cookie_token, repr(issued))
        self.assertNotIn(issued.form_token, repr(issued))
        self.assertTrue(
            challenges.consume_after_admission(issued.cookie_token, issued.form_token)
        )
        self.assertFalse(
            challenges.consume_after_admission(issued.cookie_token, issued.form_token)
        )
        other = challenges.create()
        self.assertFalse(
            challenges.consume_after_admission(other.cookie_token, issued.form_token)
        )
        self.clock.advance(121)
        self.assertFalse(
            challenges.consume_after_admission(other.cookie_token, other.form_token)
        )

    def test_login_challenge_ttl_uses_exact_integer_milliseconds(self):
        self.clock.value = 10_000_000_000.125
        challenges = LoginChallengeStore(clock=self.clock, ttl_seconds=300)
        issued = challenges.create()
        self.assertTrue(
            challenges.consume_after_admission(
                issued.cookie_token, issued.form_token
            )
        )

    def test_wrong_form_token_does_not_consume_the_valid_challenge(self):
        challenges = LoginChallengeStore(clock=self.clock)
        issued = challenges.create()
        self.assertFalse(
            challenges.consume_after_admission(issued.cookie_token, "wrong-form-token")
        )
        self.assertTrue(
            challenges.consume_after_admission(issued.cookie_token, issued.form_token)
        )

    def test_login_page_get_flood_cannot_evict_an_outstanding_challenge(self):
        challenges = LoginChallengeStore(
            clock=self.clock, maximum_challenges=4
        )
        owner = challenges.create()
        for _ in range(1_000):
            challenges.create()
        self.assertTrue(
            challenges.consume_after_admission(owner.cookie_token, owner.form_token)
        )

    def test_login_challenge_signature_restart_and_consumed_capacity_fail_closed(self):
        challenges = LoginChallengeStore(
            clock=self.clock, maximum_challenges=2
        )
        altered = challenges.create()
        replacement = "A" if altered.cookie_token[-1] != "A" else "B"
        self.assertFalse(
            challenges.consume_after_admission(
                altered.cookie_token[:-1] + replacement,
                altered.form_token,
            )
        )
        first = challenges.create()
        second = challenges.create()
        third = challenges.create()
        self.assertTrue(
            challenges.consume_after_admission(first.cookie_token, first.form_token)
        )
        self.assertTrue(
            challenges.consume_after_admission(second.cookie_token, second.form_token)
        )
        self.assertFalse(
            challenges.consume_after_admission(third.cookie_token, third.form_token)
        )
        self.clock.advance(301)
        after_expiry = challenges.create()
        self.assertTrue(
            challenges.consume_after_admission(
                after_expiry.cookie_token, after_expiry.form_token
            )
        )
        outstanding = challenges.create()
        challenges.clear()
        self.assertFalse(
            challenges.consume_after_admission(
                outstanding.cookie_token, outstanding.form_token
            )
        )

    def test_cookie_contract_is_host_only_secure_and_strict(self):
        session = cookie_settings(SESSION_COOKIE, max_age=SESSION_ABSOLUTE_SECONDS)
        challenge = cookie_settings(LOGIN_CHALLENGE_COOKIE, max_age=120)
        for settings in (session, challenge):
            self.assertTrue(settings["secure"])
            self.assertTrue(settings["httponly"])
            self.assertEqual(settings["samesite"], "strict")
            self.assertEqual(settings["path"], "/")
            self.assertNotIn("domain", settings)
        self.assertTrue(SESSION_COOKIE.startswith("__Host-"))
        self.assertTrue(LOGIN_CHALLENGE_COOKIE.startswith("__Host-"))
        csrf = cookie_settings(
            CSRF_COOKIE, max_age=SESSION_ABSOLUTE_SECONDS, httponly=False
        )
        self.assertFalse(csrf["httponly"])
        self.assertTrue(csrf["secure"])
        self.assertTrue(CSRF_COOKIE.startswith("__Host-"))


if __name__ == "__main__":
    unittest.main()

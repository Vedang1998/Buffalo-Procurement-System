"""Deterministic diagnostic ordering/refusal tests; no hosted capability PASS."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_capability as capability
import hosted_synthetic_source as source


class HostedSyntheticCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.guardian = SimpleNamespace(_owner_token=object())
        self.backend = Mock()
        self.backend._browser_libc.return_value.prctl.return_value = 0
        self.backend._block_browser_projection_signals.return_value = frozenset()
        self.backend._start_stopped_browser_private_proc_guardian.return_value = self.guardian
        self.backend._continue_browser_private_proc_guardian.return_value = ("mounted", 0)
        self.backend._close_browser_prerequisite_guardian_lease.side_effect = self.close
        self.clean = patch.object(capability, "_require_clean_supervisor")
        self.clean.start()
        self.addCleanup(self.clean.stop)
        host = patch('hosted_synthetic_host.observe_host_prerequisites',
                     return_value={'prerequisites_available': True})
        host.start()
        self.addCleanup(host.stop)
        self.generation = hashlib.sha256(b"synthetic namespace diagnostic").hexdigest()

    def close(self, guardian):
        guardian._owner_token = None

    def attempt(self):
        return capability._namespace_attempt(self.backend, self.generation)

    def assert_no_authority(self, observation):
        report = capability._report(observation=observation)
        self.assertEqual(report["HOST_CAPABILITY"], "FAIL")
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["payload_processes"], 0)
        self.assertEqual(report["credential_release"], "WITHHELD")
        self.assertFalse(report["execution_authority"])
        self.assertEqual(report["projection"], "NOT_RUN")
        self.assertEqual(report["cgroup"], "NOT_RUN")

    def test_mount_attempt_success_is_still_incomplete_and_nonauthorizing(self):
        result = self.attempt()
        self.assertEqual(result, dict(stage="mounted", errno=0, cleanup_complete=True,
                                     reason="INCOMPLETE_PREFLIGHT"))
        self.assert_no_authority(result)
        calls = [call[0] for call in self.backend.mock_calls]
        self.assertLess(calls.index("_close_browser_prerequisite_guardian_lease"),
                        calls.index("_restore_browser_projection_signals"))

    def test_mount_refusal_preserves_stage_errno_and_no_release(self):
        self.backend._continue_browser_private_proc_guardian.return_value = ("legacy-mount", 1)
        result = self.attempt()
        self.assertEqual((result["stage"], result["errno"]), ("legacy-mount", 1))
        self.assertTrue(result["cleanup_complete"])
        self.assert_no_authority(result)

    def test_canonical_combined_eperm_and_move_mount_are_preserved(self):
        for stage, number in (("fsmount+legacy-mount", 1), ("move_mount", 13)):
            self.guardian._owner_token = object()
            self.backend._continue_browser_private_proc_guardian.return_value = (stage, number)
            result = self.attempt()
            self.assertEqual((result["stage"], result["errno"]), (stage, number))
            self.assertTrue(result["cleanup_complete"])
            self.assert_no_authority(result)

    def test_failed_signal_restoration_is_cleanup_failure(self):
        for failure in (None, RuntimeError("body"), KeyboardInterrupt()):
            self.guardian._owner_token = object()
            self.backend._continue_browser_private_proc_guardian.side_effect = failure
            self.backend._restore_browser_projection_signals.side_effect = OSError("restore")
            result = self.attempt()
            self.assertFalse(result["cleanup_complete"])
            self.assertEqual(result["reason"], "CLEANUP_FAILED")
            self.assert_no_authority(result)

    def test_preownership_backend_refusals_are_bounded(self):
        for method in ("_browser_libc", "_block_browser_projection_signals"):
            with patch.object(self.backend, method, side_effect=RuntimeError("fabricated-secret")):
                result = self.attempt()
                self.assertEqual(result["reason"], "NAMESPACE_PREREQUISITE_REFUSED")
                self.assertTrue(result["cleanup_complete"])
                self.assertNotIn("fabricated-secret", json.dumps(result))
        self.backend._browser_prctl.assert_not_called()
        self.backend._start_stopped_browser_private_proc_guardian.assert_not_called()

    def test_environment_refusal_occurs_before_any_backend_operation(self):
        with patch.object(capability, "_require_clean_supervisor",
                          side_effect=capability.HostedDiagnosticError("refused")):
            with self.assertRaises(capability.HostedDiagnosticError):
                self.attempt()
        self.assertEqual(self.backend.mock_calls, [])

    def test_invalid_generation_refuses_before_backend_operation(self):
        for value in ("", "a" * 63, "A" * 64, "x" * 64):
            with self.subTest(value=value), self.assertRaises(capability.HostedDiagnosticError):
                capability._namespace_attempt(self.backend, value)
        self.assertEqual(self.backend.mock_calls, [])

    def test_body_failure_cleans_owned_guardian_without_echoing_error(self):
        self.backend._continue_browser_private_proc_guardian.side_effect = RuntimeError("fabricated-secret")
        result = self.attempt()
        self.assertEqual(result["reason"], "NAMESPACE_PREREQUISITE_REFUSED")
        self.assertTrue(result["cleanup_complete"])
        self.assertNotIn("fabricated-secret", json.dumps(result))
        self.assert_no_authority(result)

    def test_start_failure_does_not_claim_unobserved_cleanup(self):
        self.backend._start_stopped_browser_private_proc_guardian.side_effect = RuntimeError("start")
        result = self.attempt()
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(result["reason"], "CLEANUP_FAILED")
        self.backend._close_browser_prerequisite_guardian_lease.assert_not_called()

    def test_cleanup_failure_is_bounded_and_overrides_body_cancellation(self):
        self.backend._continue_browser_private_proc_guardian.side_effect = KeyboardInterrupt
        self.backend._close_browser_prerequisite_guardian_lease.side_effect = OSError("cleanup")
        result = self.attempt()
        self.assertEqual(self.backend._close_browser_prerequisite_guardian_lease.call_count, 2)
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(result["reason"], "CLEANUP_FAILED")
        self.assert_no_authority(result)

    def test_terminal_cleanup_error_is_not_retried_against_released_lease(self):
        def terminal_failure(guardian):
            self.close(guardian)
            raise OSError("cleanup proof failed")
        self.backend._close_browser_prerequisite_guardian_lease.side_effect = terminal_failure
        result = self.attempt()
        self.assertEqual(self.backend._close_browser_prerequisite_guardian_lease.call_count, 1)
        self.assertFalse(result["cleanup_complete"])
        self.assert_no_authority(result)

    def test_unclosed_lease_cannot_claim_cleanup(self):
        self.backend._close_browser_prerequisite_guardian_lease.side_effect = None
        result = self.attempt()
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(result["reason"], "CLEANUP_FAILED")

    def test_deferred_cancellation_arrives_after_cleanup(self):
        self.backend._restore_browser_projection_signals.side_effect = KeyboardInterrupt
        result = self.attempt()
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(result["reason"], "CANCELLED")
        self.assertIsNone(self.guardian._owner_token)
        self.assert_no_authority(result)

    def test_malformed_mount_result_refuses_and_cleans(self):
        for evidence in (("mount sensitive value", 0), ("mounted", True), ("mounted", -1)):
            self.guardian._owner_token = object()
            self.backend._continue_browser_private_proc_guardian.return_value = evidence
            with self.subTest(evidence=evidence):
                result = self.attempt()
                self.assertEqual(result["reason"], "NAMESPACE_PREREQUISITE_REFUSED")
                self.assertTrue(result["cleanup_complete"])

    def test_ordinary_invocation_remains_failure_without_source_or_process(self):
        with patch.object(capability, "bind_hosted_source") as bind, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(capability.main([]), 1)
        bind.assert_not_called()
        lines = output.getvalue().splitlines()
        report = json.loads(lines[0])
        self.assertEqual(lines[1], "evidence_sha256=" + hashlib.sha256(lines[0].encode()).hexdigest())
        self.assertIsNone(report["commit"])
        self.assertEqual(report["provider_calls"], 0)

    def test_source_refusal_precedes_retained_namespace_observation(self):
        with patch.object(capability, 'bind_hosted_source', side_effect=source.HostedSourceError('refused')), \
             patch('hosted_synthetic_namespace.observe_private_proc') as observe, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 1)
        observe.assert_not_called()
        self.assertEqual(json.loads(output.getvalue().splitlines()[0])['reason'],
                         'SOURCE_OR_SUPERVISOR_PREREQUISITE_REFUSED')

    def test_allowlisted_source_failure_reports_phase_without_raw_output(self):
        with patch.object(capability, 'bind_hosted_source', side_effect=source.HostedSourceError('source_git_refused')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 1)
        self.assertEqual(json.loads(output.getvalue().splitlines()[0])['refusal'],
                         {'phase': 'SOURCE_BINDING', 'code': 'source_git_refused', 'errno': None})

    def test_supervisor_category_is_bounded_and_unknown_text_is_withheld(self):
        for error, code in ((capability.HostedDiagnosticError('supervisor_descriptors_refused'), 'supervisor_descriptors_refused'),
                            (PermissionError(13, 'synthetic-private-path'), 'UNCLASSIFIED_REFUSAL')):
            with patch.object(capability, '_require_clean_supervisor', side_effect=error), \
                 patch.object(capability, 'bind_hosted_source') as bind, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 1)
            bind.assert_not_called()
            refusal = json.loads(output.getvalue().splitlines()[0])['refusal']
            self.assertEqual((refusal['phase'], refusal['code']), ('SUPERVISOR', code))
            self.assertEqual(refusal['errno'], 13 if isinstance(error, OSError) else None)
            self.assertNotIn('synthetic-private-path', output.getvalue())

    def test_bound_namespace_evidence_still_cannot_authorize_release(self):
        binding = SimpleNamespace(commit='a'*40, tree='b'*40, files=())
        observation = dict(reason='INCOMPLETE_PREFLIGHT', cleanup_complete=True,
                           private_proc={'members': [1, 2], 'projection': {'sentinel_exec': 'NOT_RUN'}},
                           runtime='HOST_RUNTIME_UNSEALED')
        with patch.object(capability, 'bind_hosted_source', return_value=binding) as bind, \
             patch('hosted_synthetic_namespace.observe_private_proc', return_value=observation) as observe, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 1)
        bind.assert_called_once()
        observe.assert_called_once()
        self.assertRegex(observe.call_args.args[0], r'^[0-9a-f]{64}$')
        result = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(result['namespace_attempt'], observation)
        self.assertEqual(result['projection'], 'MATERIALIZATION_CHECK_ONLY')
        self.assertEqual(result['sentinel_exec'], 'NOT_RUN')
        self.assertEqual(result['HOST_CAPABILITY'], 'FAIL')
        self.assertEqual(result['credential_release'], 'WITHHELD')
        self.assertEqual(result['provider_calls'], 0)
        self.assertFalse(result['execution_authority'])

    def test_missing_host_prerequisite_prevents_namespace_mutations(self):
        binding = SimpleNamespace(commit='a'*40, tree='b'*40, files=())
        with patch.object(capability, 'bind_hosted_source', return_value=binding), \
             patch('hosted_synthetic_host.observe_host_prerequisites', return_value={'prerequisites_available': False}), \
             patch('hosted_synthetic_namespace.observe_private_proc') as observe, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 1)
        observe.assert_not_called()
        result = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(result['reason'], 'HOST_PREREQUISITES_REFUSED')
        self.assertEqual(result['provider_calls'], 0)
        self.assertFalse(result['execution_authority'])

    def test_host_cleanup_failure_is_not_a_capability_refusal(self):
        from hosted_synthetic_cgroup import CgroupCleanupError
        binding = SimpleNamespace(commit='a'*40, tree='b'*40, files=())
        with patch.object(capability, 'bind_hosted_source', return_value=binding), \
             patch('hosted_synthetic_host.observe_host_prerequisites', side_effect=CgroupCleanupError('private-value')), \
             patch('hosted_synthetic_namespace.observe_private_proc') as observe, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 1)
        observe.assert_not_called()
        self.assertEqual(json.loads(output.getvalue().splitlines()[0])['reason'], 'CLEANUP_FAILED')
        self.assertNotIn('private-value', output.getvalue())

    def _preownership_main_refusal(self, selected):
        import hosted_synthetic_namespace as namespace
        binding = SimpleNamespace(commit='a'*40, tree='b'*40, files=())
        target = namespace.backend
        if selected == 'prctl':
            refused = patch.object(target, '_browser_libc')
        else:
            refused = patch.object(target, '_block_browser_projection_signals',
                                   side_effect=target.LocalStagingAcceptanceError('synthetic-private-value'))
        with refused as operation, patch.object(capability, 'bind_hosted_source', return_value=binding), \
             patch.object(namespace.os, 'fork') as fork, contextlib.redirect_stdout(io.StringIO()) as output:
            if selected == 'prctl':
                operation.return_value.prctl.return_value = -1
            self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 1)
        fork.assert_not_called()
        lines = output.getvalue().splitlines()
        result = json.loads(lines[0])
        self.assertEqual(result['reason'], 'NAMESPACE_PREREQUISITE_REFUSED')
        self.assertEqual(result['HOST_CAPABILITY'], 'FAIL')
        self.assertEqual(result['credential_release'], 'WITHHELD')
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(lines[1], 'evidence_sha256=' + hashlib.sha256(lines[0].encode()).hexdigest())
        self.assertNotIn('synthetic-private-value', output.getvalue())

    def test_main_prctl_preownership_refusal_is_canonical(self):
        self._preownership_main_refusal('prctl')

    def test_main_mask_preownership_refusal_is_canonical(self):
        self._preownership_main_refusal('mask')

    def test_main_preownership_cancellation_is_canonical_and_has_no_observer(self):
        with patch.object(capability, 'bind_hosted_source', side_effect=KeyboardInterrupt), \
             patch('hosted_synthetic_namespace.observe_private_proc') as observe, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(capability.main(['repo', 'push', 'ref', 'a'*40]), 130)
        observe.assert_not_called()
        self.assertEqual(json.loads(output.getvalue().splitlines()[0])['reason'], 'CANCELLED')

    def test_clean_initial_environment_and_ambient_secret_refusal_in_fresh_process(self):
        self.clean.stop()
        code = (
            "import sys; "
            f"sys.path[:0] = {[str(Path(capability.__file__).parent), str(Path(source.__file__).parent)]!r}; "
            "import hosted_synthetic_capability as c; "
            "c._require_clean_supervisor()"
        )
        for extra, expected in (({}, 0), ({"GH_TOKEN": "fabricated-secret"}, 1)):
            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", code],
                env=dict(capability._ENVIRONMENT, **extra), capture_output=True, timeout=5,
            )
            self.assertEqual(completed.returncode, expected)
            self.assertNotIn(b"fabricated-secret", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()

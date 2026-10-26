"""Fail-closed source contract for the deliberately incomplete hosted workflow."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class HostedSyntheticWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / '.github/workflows/hosted-synthetic-acceptance.yml').read_text()

    def test_only_exact_branch_push_has_authority(self):
        self.assertIn('on:\n  push:\n    branches: [codex/hosted-synthetic-acceptance]\n', self.text)
        for trigger in ('pull_request', 'workflow_dispatch', 'workflow_call', 'schedule:', 'repository_dispatch'):
            self.assertNotIn(trigger, self.text)
        self.assertIn("if: github.repository == 'Vedang1998/Buffalo-Procurement-System' && github.event_name == 'push' && github.ref == 'refs/heads/codex/hosted-synthetic-acceptance'", self.text)

    def test_read_only_permissions_and_bounded_vm(self):
        self.assertEqual(re.findall(r'(?m)^permissions:\n((?:  .+\n)+)', self.text), ['  contents: read\n'])
        self.assertEqual(re.findall(r'(?m)^    runs-on: (.+)$', self.text), ['ubuntu-24.04'])
        self.assertEqual(re.findall(r'(?m)^    timeout-minutes: (.+)$', self.text), ['10'])
        for forbidden in ('secrets.', 'id-token:', 'write-all', 'contents: write', 'services:', 'container:', 'sudo ', 'sysctl ', '--privileged', 'upload-artifact', 'download-artifact'):
            self.assertNotIn(forbidden, self.text)
        self.assertNotRegex(self.text, r'(?m)^\s*environment:')

    def test_exact_actions_and_detached_complete_checkout(self):
        self.assertEqual(re.findall(r'uses: ([^\s]+)', self.text), [
            'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1'])
        for fragment in ('ref: ${{ github.sha }}', 'fetch-depth: 0', 'persist-credentials: false', "python-version: '3.13.5'", 'check-latest: false', 'update-environment: false'):
            self.assertIn(fragment, self.text)

    def test_trusted_metadata_and_source_assertions_precede_repo_code(self):
        trusted = self.text.split('- name: Verify trusted source before repository code', 1)[1].split('- name: Prepare Python', 1)[0]
        for fragment in ('test "$EXPECTED_REPOSITORY" = Vedang1998/Buffalo-Procurement-System', 'test "$EXPECTED_EVENT" = push', 'test "$EXPECTED_REF" = refs/heads/codex/hosted-synthetic-acceptance', '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', 'if git symbolic-ref --quiet HEAD; then exit 1; fi', 'git merge-base --is-ancestor cb0969059b0b85241d611124910d691e49212881 HEAD', 'test -z "$(git status --porcelain=v1 --untracked-files=all)"', "git rev-parse 'HEAD^{tree}'"):
            self.assertIn(fragment, trusted)
        self.assertNotIn('procurement/', self.text.split('- name: Prepare Python', 1)[0])

    def test_repository_python_has_no_site_or_ambient_environment(self):
        launches = [line.strip() for line in self.text.splitlines() if 'env -i ' in line]
        self.assertEqual(len(launches), 2)
        for line in launches:
            self.assertTrue(line.startswith('env -i PATH=/usr/bin:/bin LC_ALL=C.UTF-8 "$DIAGNOSTIC_PYTHON" -I -S -B '))
        self.assertIn('hosted_synthetic_capability.py "$EXPECTED_REPOSITORY" "$EXPECTED_EVENT" "$EXPECTED_REF" "$EXPECTED_SHA"', launches[1])

    def test_focused_gate_rejects_skips_xfails_and_missing_population(self):
        focused = self.text.split('- name: Focused source and diagnostic validation', 1)[1].split('- name: Observe namespace', 1)[0]
        for fragment in ('"test_hosted_synthetic_source"', '"test_hosted_synthetic_capability"', '"test_hosted_synthetic_workflow"', '"test_hosted_synthetic_namespace"', '"test_hosted_synthetic_projection"', '"test_hosted_synthetic_cgroup"', '"test_hosted_synthetic_host"', 'assert [item.countTestCases() for item in modules] == [36,23,8,65,22,28,18]', 'result.wasSuccessful()', 'result.testsRun == 200', 'not result.skipped', 'not result.expectedFailures', 'not result.unexpectedSuccesses'):
            self.assertIn(fragment, focused)
        self.assertNotIn('continue-on-error', self.text)
        self.assertNotIn('always()', self.text)

    def test_workflow_has_no_acceptance_or_deployment_commands(self):
        self.assertNotRegex(self.text, r'(?i)\b(?:railway|shopify|pg_restore|docker|curl|wget)\b')
        self.assertNotIn('./scripts/procurement-tests', self.text)
        self.assertNotRegex(self.text, r'(?m)^\s*run:.*run_local_staging_acceptance')
        self.assertNotIn('HOST_CAPABILITY=PASS', self.text)
        # Public evidence is written by the shell; the scrubbed Python process
        # receives no GitHub environment/output-path authority. Failure remains
        # the diagnostic's exit code, never continue-on-error or a success echo.
        for fragment in ('report=$(mktemp)', 'trap \'rm -f "$report"\' EXIT',
                         'test "$(wc -c < "$report")" -le 65536',
                         'cat "$report" >> "$GITHUB_STEP_SUMMARY"', 'exit "$status"'):
            self.assertIn(fragment, self.text)

    def test_public_notice_escapes_command_data_without_changing_failure(self):
        block = self.text.split('          diagnostic=$(head -n 1 "$report")\n', 1)[1]
        commands = '\n'.join(line[10:] for line in block.splitlines())
        result = subprocess.run(['/bin/bash', '-c', 'diagnostic="$1"; status=1;\n' + commands,
                                 'annotation-test', '{"test":"100%\r\n::error::synthetic"}'],
                                env={'PATH': '/usr/bin:/bin'}, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'::notice title=Credential-free host prerequisites::{"test":"100%25%0D%0A::error::synthetic"}\n')
        self.assertEqual(result.stderr, b'')


if __name__ == '__main__':
    unittest.main()

"""Fail-closed source contract for the deliberately incomplete hosted workflow."""
from pathlib import Path
import json
import base64
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import shlex
import unittest
from unittest.mock import patch

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
        launches = [line.strip() for line in self.text.splitlines() if 'env -i ' in line and '"$DIAGNOSTIC_PYTHON" -I -S -B' in line]
        self.assertEqual(len(launches), 4)
        self.assertTrue(launches[0].startswith('env -i PATH=/usr/bin:/bin LC_ALL=C.UTF-8 BUFFALO_DIAGNOSTIC_NODE="$diagnostic_node" "$DIAGNOSTIC_PYTHON" -I -S -B '))
        self.assertTrue(launches[3].startswith('/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C.UTF-8 "$DIAGNOSTIC_PYTHON" -I -S -B '))
        self.assertIn('"procurement/tools/hosted_synthetic_capability.py",*sys.argv[1:]],close_fds=True', launches[3])
        self.assertIn('"$EXPECTED_REPOSITORY" "$EXPECTED_EVENT" "$EXPECTED_REF" "$EXPECTED_SHA"', launches[3])
        self.assertIn('hosted_synthetic_pid1_regression.py --run-pid1-regressions',launches[1])
        self.assertNotIn('BUFFALO_DIAGNOSTIC_NODE', ''.join(launches[1:]))
        self.assertIn('diagnostic_node=$(command -v node)', self.text)
        self.assertIn('sha256sum "$diagnostic_node"', self.text)

    def test_diagnostic_node_override_is_explicit_and_fail_closed(self):
        from hosted_test_node import node_executable
        for value in ('', 'node', './node', '/definitely-missing-buffalo-node', '/tmp'):
            with self.subTest(value=value),patch.dict(os.environ,{'BUFFALO_DIAGNOSTIC_NODE':value}):
                self.assertIsNone(node_executable())
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'diagnostic-node'
            executable.write_text('not executed by this locator check')
            executable.chmod(0o600)
            with patch.dict(os.environ,{'BUFFALO_DIAGNOSTIC_NODE':str(executable)}):
                self.assertIsNone(node_executable());executable.chmod(0o700)
                self.assertEqual(node_executable(),str(executable))

    def test_focused_gate_rejects_skips_xfails_and_missing_population(self):
        focused = self.text.split('- name: Focused source and diagnostic validation', 1)[1].split('- name: Review gate', 1)[0]
        for fragment in ('"test_hosted_synthetic_source"', '"test_hosted_synthetic_capability"', '"test_hosted_synthetic_workflow"', '"test_hosted_synthetic_namespace"', '"test_hosted_synthetic_projection"', '"test_hosted_synthetic_cgroup"', '"test_hosted_synthetic_host"', 'assert [item.countTestCases() for item in modules] == [36,33,13,66,22,35,20,11,17,30,15,4,20,14,23,10,23,14,22,15,20,23,21,28,18,26,23,21,22,16,24,26,17]', 'result.wasSuccessful()', 'result.testsRun == 728', 'not result.skipped', 'not result.expectedFailures', 'not result.unexpectedSuccesses'):
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

    def test_review_gate_exits_before_any_live_service_command(self):
        observer=self.text.split('- name: Review gate before hosted execution',1)[1]
        body=observer.split('          set -euo pipefail\n',1)[1].split('          report=$(mktemp)',1)[0]
        commands='\n'.join(line[10:] for line in body.splitlines())
        result=subprocess.run(['/bin/bash','-c','set -euo pipefail;\n'+commands+'\nprintf LIVE_SERVICE_REACHED'],
                              cwd=ROOT,env={'PATH':'/usr/bin:/bin','DIAGNOSTIC_PYTHON':sys.executable},
                              capture_output=True,timeout=5)
        self.assertEqual(result.returncode,78,result.stderr.decode())
        self.assertNotIn(b'LIVE_SERVICE_REACHED',result.stdout)
        self.assertEqual(json.loads(result.stdout)['privileged_processes_started'],0)
        self.assertNotIn('sudo ',self.text)

    def test_public_notice_chunks_reconstruct_exact_bytes_and_keep_failure(self):
        block = self.text.split('          diagnostic=$(head -n 1 "$report")\n', 1)[1]
        commands = '\n'.join(line[10:] for line in block.splitlines())
        for raw in ('{"test":"100%\r\n::error::synthetic"}', 'a'*12000, 'b'*16384):
            with self.subTest(size=len(raw)):
                result = subprocess.run(['/bin/bash', '-c', 'set -euo pipefail; diagnostic="$1"; status=1;\n' + commands,
                                         'annotation-test', raw], env={'PATH': '/usr/bin:/bin'},
                                        capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b'')
                lines = result.stdout.splitlines()
                self.assertTrue(1 <= len(lines) <= 8)
                encoded = []
                digest = hashlib.sha256(raw.encode()).hexdigest().encode()
                for index, line in enumerate(lines):
                    self.assertLess(len(line), 4096)
                    matched = re.fullmatch(rb'::notice title=Buffalo diagnostic part::v1 index=([0-7]) count=([1-8]) sha256=([0-9a-f]{64}) data=([A-Za-z0-9+/=]{1,3000})', line)
                    self.assertIsNotNone(matched)
                    self.assertEqual((int(matched[1]), int(matched[2]), matched[3]), (index, len(lines), digest))
                    encoded.append(matched[4])
                self.assertEqual(base64.b64decode(b''.join(encoded), validate=True), raw.encode())
        result = subprocess.run(['/bin/bash', '-c', 'set -euo pipefail; diagnostic="$1"; status=1;\n' + commands,
                                 'annotation-test', 'a'*16385], env={'PATH': '/usr/bin:/bin'},
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')

    def test_owned_launch_drops_extra_descriptor_preserving_parent_and_assertion(self):
        observer = self.text.split('- name: Review gate', 1)[1]
        code = re.search(r"-c '([^']+)'", observer).group(1)
        fd = os.open(os.devnull, os.O_RDONLY)
        try:
            os.set_inheritable(fd, True)
            identity = os.fstat(fd)
            arguments = ['synthetic-invalid-repository', 'push', 'refs/heads/codex/hosted-synthetic-acceptance', 'a'*40]
            environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'}
            direct = subprocess.run([sys.executable, '-I', '-S', '-B',
                                     'procurement/tools/hosted_synthetic_capability.py', *arguments],
                                    cwd=ROOT, env=environment, pass_fds=(fd,), capture_output=True, timeout=5)
            refused = json.loads(direct.stdout.splitlines()[0])
            self.assertEqual(refused['refusal']['code'], 'supervisor_descriptors_refused')
            completed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, *arguments],
                                       cwd=ROOT, env=environment, pass_fds=(fd,), capture_output=True, timeout=5)
            result = json.loads(completed.stdout.splitlines()[0])
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(result['refusal']['phase'], 'POSTDROP_IDENTITY')
            self.assertEqual(result['refusal']['code'], 'POSTDROP_PREREQUISITE_REFUSED')
            self.assertEqual(result['provider_calls'], 0)
            self.assertFalse(result['execution_authority'])
            self.assertIsNone(result['host_prerequisites'])
            self.assertEqual(os.fstat(fd), identity)
            self.assertTrue(os.get_inheritable(fd))
        finally:
            os.close(fd)

    def test_user_manager_service_is_bounded_and_keeps_same_launcher(self):
        for fragment in ('/usr/bin/systemd-run --user --quiet --wait --pipe --collect --service-type=exec --expand-environment=no',
                         '--unit="$unit"', '--slice=app.slice', '--working-directory="$(pwd -P)"', "--property='Delegate=memory pids'",
                         '--property=DelegateSubgroup=supervisor', '--property=RuntimeMaxSec=120s', '--property=TimeoutStartSec=10s',
                         '--property=TimeoutStopSec=5s', '--property=KillMode=control-group',
                         '--property=NoNewPrivileges=yes', '--property=MemoryMax=512M', '--property=TasksMax=64',
                         'XDG_RUNTIME_DIR="/run/user/$uid"', 'DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus"'):
            self.assertIn(fragment, self.text)
        self.assertNotIn('systemctl --user stop', self.text)
        self.assertNotIn('reset-failed', self.text)

    def test_manager_failure_status_and_unobserved_cleanup_never_pass(self):
        block = self.text.split('          # Begin owned user-service diagnostic.', 1)[1]
        block = block.split('          # End owned user-service diagnostic.', 1)[0]
        block = '\n'.join(line[10:] for line in block.splitlines()[1:])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manager = directory / 'manager'
            control = directory / 'control'
            output = directory / 'output'
            trace = directory / 'trace'
            for observed, manager_status, query_status, expected_cleanup in (
                ('not-found', 1, 0, 'NOT_FOUND_OBSERVED'), ('loaded', 1, 0, 'UNVERIFIED'),
                ('not-found', 124, 0, 'UNVERIFIED'), ('not-found', 1, 1, 'NOT_FOUND_OBSERVED'),
                ('not-found', 1, 124, 'UNVERIFIED'),
            ):
                manager.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\nPath({str(trace)!r}).write_text(repr(sys.argv[1:]))\nprint("synthetic diagnostic")\nsys.exit({manager_status})\n')
                manager.chmod(0o700)
                control.write_text(f'#!{sys.executable}\nimport sys\nassert sys.argv[1:3]==["--user","show"]\nprint({observed!r})\nsys.exit({query_status})\n')
                control.chmod(0o700)
                simulated = block.replace('/usr/bin/systemd-run', shlex.quote(str(manager))).replace('/usr/bin/systemctl', shlex.quote(str(control)))
                code = 'set -euo pipefail; report="$1"; DIAGNOSTIC_PYTHON="$2"; EXPECTED_REPOSITORY=test; EXPECTED_EVENT=push; EXPECTED_REF=test; EXPECTED_SHA=test; status=0;\n' + simulated + '\nexit "$status"'
                run = subprocess.run(['/bin/bash', '-c', code, 'lifecycle-test', str(output), sys.executable],
                                     env={'PATH': '/usr/bin:/bin'}, capture_output=True, timeout=5)
                self.assertEqual(run.returncode, 1, run.stderr)
                self.assertIn(('load_state='+expected_cleanup).encode(), run.stdout)
                self.assertEqual(output.read_text(), 'synthetic diagnostic\n')
                self.assertIn('--collect', trace.read_text())


if __name__ == '__main__':
    unittest.main()

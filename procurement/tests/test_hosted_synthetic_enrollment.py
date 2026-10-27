"""Protocol tests with simulated cgroup operations; never live containment proof."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_namespace as ns
import hosted_synthetic_capability as capability
import hosted_synthetic_cgroup as cg


class HostedSyntheticEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('hosted_synthetic_launcher.inspect_host_identity', return_value={}))
        self.lease = Mock(path='/unit/buffalo-' + 'a'*64)
        self.guardian = Mock(number=10)
        self.init = Mock(number=11)
        self.owners = []
        self.events = []
        self.enterContext(patch.object(ns, '_receive', return_value={'pid': 102, 'start': 300}))
        self.enterContext(patch.object(ns, '_wait', side_effect=lambda *a, **k: self.events.append('stopped')))
        self.enterContext(patch.object(ns, '_pidfd', return_value=self.init))
        self.enterContext(patch.object(ns, '_process', return_value={'state': 'T'}))
        self.send = self.enterContext(patch.object(ns, '_send', side_effect=lambda *a, **k: self.events.append('ack')))
        self.resume = self.enterContext(patch.object(ns.signal, 'pidfd_send_signal', side_effect=lambda *a: self.events.append('resume')))
        self.lease.enroll_stopped_init.side_effect = lambda **k: self.events.append('enroll')

    def enroll(self):
        return ns._supervisor_enrollment(self.lease, Mock(), Mock(), self.guardian, 101, 200,
                                        'a'*64, 1.0, self.owners)

    def test_enrollment_precedes_ack_and_only_guardian_resume(self):
        result = self.enroll()
        self.assertEqual(self.events, ['stopped', 'enroll', 'ack', 'resume'])
        self.assertEqual(result, {'pid': 102, 'start': 300, 'pidfd': self.init})
        self.assertEqual(self.owners, [self.init])
        self.resume.assert_called_once_with(10, signal.SIGCONT)
        self.assertEqual(self.send.call_args.kwargs, {'pid': 102, 'start': 300, 'path': self.lease.path})

    def test_no_ack_or_resume_after_enrollment_failure(self):
        self.lease.enroll_stopped_init.side_effect = cg.CgroupProofError('synthetic')
        with self.assertRaises(cg.CgroupProofError):
            self.enroll()
        self.send.assert_not_called()
        self.resume.assert_not_called()
        self.assertEqual(self.owners, [self.init])

    def test_ack_or_descriptor_failure_never_resumes(self):
        for target in (self.send, self.guardian.verify):
            with self.subTest(target=target):
                target.side_effect = OSError('synthetic')
                with self.assertRaises(OSError):
                    self.enroll()
                self.resume.assert_not_called()
                target.side_effect = None

    def test_bad_or_replayed_request_precedes_ownership_and_write(self):
        for request in ({'pid': True, 'start': 300}, {'pid': 101, 'start': 300},
                        {'pid': os.getpid(), 'start': 300}, {'pid': 102, 'start': 0},
                        {'pid': 2**32, 'start': 300}, {'pid': 102, 'start': True}):
            with self.subTest(request=request), patch.object(ns, '_receive', return_value=request), \
                 self.assertRaises(ns.NamespaceProofError):
                self.enroll()
        with patch.object(ns, '_receive', side_effect=ns.NamespaceProofError('generation')), \
             self.assertRaises(ns.NamespaceProofError):
            self.enroll()
        self.assertEqual(self.owners, [])
        self.lease.enroll_stopped_init.assert_not_called()
        self.resume.assert_not_called()

    def test_guardian_must_still_be_stopped(self):
        with patch.object(ns, '_process', return_value={'state': 'S'}), self.assertRaises(ns.NamespaceProofError):
            self.enroll()
        self.lease.enroll_stopped_init.assert_not_called()
        self.resume.assert_not_called()

    def test_guardian_rejects_mismatched_ack_before_init_resume(self):
        process = {'state': 'T', 'parent': os.getpid(), 'group': os.getpid(), 'start': 300}
        for key, value in (('pid', 999), ('start', 999), ('path', '/foreign')):
            ack = {'pid': 102, 'start': 300, 'path': self.lease.path}
            ack[key] = value
            with self.subTest(key=key), patch.object(ns, '_process', return_value=process), \
                 patch.object(ns.backend, '_browser_pidfd_is_terminal', return_value=False), \
                 patch.object(ns.os, 'kill') as stop, patch.object(ns, '_receive', return_value=ack), \
                 patch.object(ns, '_read', return_value=b'0::/unit/supervisor\n'), \
                 self.assertRaises(ns.NamespaceProofError):
                ns._guardian_enrollment(Mock(), Mock(), 102, self.init, 'a'*64, 1.0)
            stop.assert_called_once_with(os.getpid(), signal.SIGSTOP)
        self.assertTrue(all(call.args == (11, signal.SIGSTOP) for call in self.resume.call_args_list))

    def test_guardian_requires_enrollment_path_and_unchanged_init(self):
        process = {'state': 'T', 'parent': os.getpid(), 'group': os.getpid(), 'start': 300}
        ack = {'pid': 102, 'start': 300, 'path': self.lease.path}
        with patch.object(ns, '_process', return_value=process), \
             patch.object(ns.backend, '_browser_pidfd_is_terminal', return_value=False), \
             patch.object(ns.os, 'kill'), patch.object(ns, '_receive', return_value=ack), \
             patch.object(ns, '_read', side_effect=lambda p, n: ('0::' + ('/unit/supervisor' if p == '/proc/self/cgroup' else self.lease.path) + '\n').encode()):
            ns._guardian_enrollment(Mock(), Mock(), 102, self.init, 'a'*64, 1.0)
        self.assertEqual([call.args for call in self.resume.call_args_list],
                         [(11, signal.SIGSTOP), (11, signal.SIGCONT)])

    def test_wrong_delegation_precedes_lease_or_fork(self):
        with patch('hosted_synthetic_capability._require_clean_supervisor'), \
             patch.object(ns, '_read', return_value=b'0::/unit/supervisor\n'), \
             patch.object(cg.CgroupLease, 'create') as create, patch.object(ns.os, 'fork') as fork:
            for value in (Path('/sys/fs/cgroup/foreign'), '/sys/fs/cgroup/unit'):
                with self.subTest(value=value), self.assertRaises(ns.NamespaceProofError):
                    ns.observe_private_proc('a'*64, delegation=value)
        create.assert_not_called()
        fork.assert_not_called()

    def test_component_report_never_becomes_acceptance(self):
        observation = {'reason': 'INCOMPLETE_PREFLIGHT', 'cgroup': 'COMPONENT_OBSERVATION_ONLY',
                       'cgroup_evidence': {'members': [102, 103]}}
        result = capability._report(observation=observation)
        self.assertEqual(result['cgroup'], 'COMPONENT_OBSERVATION_ONLY')
        self.assertEqual(result['HOST_CAPABILITY'], 'FAIL')
        self.assertEqual(result['credential_release'], 'WITHHELD')
        self.assertEqual(result['sentinel_exec'], 'NOT_RUN')
        self.assertEqual(result['provider_calls'], 0)

    def test_resume_failure_retains_both_owned_identities_for_outer_cleanup(self):
        self.resume.side_effect = OSError('synthetic resume refusal')
        with self.assertRaises(OSError):
            self.enroll()
        self.assertEqual(self.owners, [self.init])
        self.lease.enroll_stopped_init.assert_called_once()
        self.send.assert_called_once()
        self.init.close.assert_not_called()

    def test_retained_pair_rechecks_processes_and_exact_membership(self):
        self._pair_case()
        self.lease.attest.assert_called_once_with((102, 103))

    def test_retained_pair_identity_or_membership_change_refuses(self):
        for failure in ('start', 'parent', 'worker_running', 'nspid', 'threads', 'path', 'outside', 'terminal', 'attest'):
            with self.subTest(failure=failure), self.assertRaises((ns.NamespaceProofError, cg.CgroupProofError)):
                self._pair_case(failure)

    def _pair_case(self, failure=None):
        processes = {101: dict(start=200, parent=os.getpid(), group=101, session=101, state='S'),
                     102: dict(start=300, parent=101, group=101, session=101, state='S'),
                     103: dict(start=400, parent=102, group=101, session=101, state='T')}
        if failure == 'start': processes[103]['start'] = 401
        if failure == 'parent': processes[103]['parent'] = 101
        if failure == 'worker_running': processes[103]['state'] = 'R'
        def read(path, maximum):
            pid = int(path.split('/')[2])
            if path.endswith('/status'):
                return json.dumps([pid, 9 if failure == 'nspid' else (1 if pid == 102 else 2)]).encode()
            value = self.lease.path if pid in (102, 103) else '/unit/supervisor'
            if failure == 'path' and pid == 103: value = '/foreign'
            if failure == 'outside' and pid == 101: value = self.lease.path
            return ('0::' + value + '\n').encode()
        with patch.object(ns, '_process', side_effect=lambda pid: processes[pid]), \
             patch.object(ns, '_pidfd', return_value=Mock(number=12)), \
             patch.object(ns.backend, '_browser_pidfd_is_terminal', return_value=failure == 'terminal'), \
             patch.object(ns, '_read', side_effect=read), \
             patch.object(ns, 'parse_status', side_effect=lambda raw: {'nspid': json.loads(raw)}), \
             patch.object(ns.os, 'listdir', side_effect=lambda path: [path.split('/')[2], '999'] if failure == 'threads' else [path.split('/')[2]]):
            if failure == 'attest':
                self.lease.attest.side_effect = cg.CgroupProofError('changed limits or members')
            try:
                ns._attest_cgroup_pair(self.lease, {'init_pid':102,'init_start':300,'worker_pid':103,'worker_start':400},
                                      {'pid':102,'start':300,'pidfd':self.init}, self.guardian,101,200,self.owners)
            finally:
                self.lease.attest.side_effect = None

    def test_outer_cleanup_reaps_before_lease_close_and_preserves_failure(self):
        for failure in ('enroll', 'retained', 'pair', 'lease_close'):
            events = []
            lease = Mock(final_resources={})
            def enroll(*args):
                args[-1].append(self.init)
                events.append('enroll')
                if failure == 'enroll': raise OSError('after enrollment')
                return {'pid':102,'start':300,'pidfd':self.init}
            def receive(fd, generation, stage, deadline, **kwargs):
                if stage == 'RETAINED':
                    if failure == 'retained': raise OSError('retained refusal')
                    return {'evidence': {}, 'outer': {}}
                return {}
            def pair(*args):
                if failure == 'pair': raise ns.NamespaceProofError('pair refusal')
                return {'members':[102,103]}
            def close():
                events.append('close')
                if failure == 'lease_close': raise cg.CgroupCleanupError('close refusal')
            lease.close.side_effect = close
            with self.subTest(failure=failure), \
                 patch('hosted_synthetic_capability._require_clean_supervisor'), \
                 patch.object(ns, '_read', return_value=b'0::/unit/supervisor\n'), \
                 patch.object(ns, '_namespaces', return_value={}), \
                 patch.object(ns.backend, '_browser_libc', return_value=Mock(prctl=Mock(return_value=0))), \
                 patch.object(ns.backend, '_browser_prctl'), \
                 patch('hosted_synthetic_runtime.capture_runtime', return_value=Mock()), \
                 patch.object(cg.CgroupLease, 'create', return_value=lease), \
                 patch.object(ns, '_pipe', side_effect=[(Mock(),Mock()),(Mock(),Mock())]), \
                 patch.object(ns.os, 'fork', return_value=101), \
                 patch.object(ns, '_process', return_value={'pid':101,'parent':os.getpid(),'group':101,'session':101,'start':200}), \
                 patch.object(ns, '_receive', side_effect=receive), \
                 patch.object(ns, '_wait', side_effect=lambda *a,**k: events.append('stopped' if k.get('stopped') else 'reap')), \
                 patch.object(ns.backend, '_write_browser_user_namespace_maps'), \
                 patch.object(ns, '_supervisor_enrollment', side_effect=enroll), \
                 patch.object(ns, '_attest_cgroup_pair', side_effect=pair), \
                 patch.object(ns.os, 'killpg', side_effect=lambda *a: events.append('kill')), \
                 patch.object(ns.backend, '_wait_browser_child_bounded', side_effect=lambda *a,**k:events.append('reap')), \
                 patch.object(ns.os, 'waitpid', side_effect=ChildProcessError), \
                 patch.object(ns.backend, '_browser_pidfd_is_terminal', return_value=True):
                result = ns.observe_private_proc('a'*64, delegation=Path('/sys/fs/cgroup/unit'))
            self.assertLess(events.index('reap'), events.index('close'))
            self.assertEqual(result['reason'], 'CLEANUP_FAILED' if failure == 'lease_close' else 'NAMESPACE_PROOF_REFUSED')
            self.assertEqual(result['provider_calls'], 0)
            self.assertEqual(result['credential_release'], 'WITHHELD')

    def test_lease_creation_refusal_precedes_fork_without_fallback(self):
        with patch('hosted_synthetic_capability._require_clean_supervisor'), \
             patch.object(ns, '_read', return_value=b'0::/unit/supervisor\n'), \
             patch.object(ns, '_namespaces', return_value={}), \
             patch.object(ns.backend, '_browser_libc', return_value=Mock(prctl=Mock(return_value=0))), \
             patch.object(ns.backend, '_browser_prctl'), \
             patch('hosted_synthetic_runtime.capture_runtime', return_value=Mock()), \
             patch.object(cg.CgroupLease, 'create', side_effect=cg.CgroupProofError('synthetic')) as create, \
             patch.object(ns.os, 'fork') as fork:
            result = ns.observe_private_proc('a'*64, delegation=Path('/sys/fs/cgroup/unit'))
        fork.assert_not_called()
        create.assert_called_once()
        self.assertEqual(result['reason'], 'NAMESPACE_PROOF_REFUSED')
        self.assertIsNone(result['cgroup_evidence'])

    def test_namespace_only_path_does_not_create_lease(self):
        with patch('hosted_synthetic_capability._require_clean_supervisor'), \
             patch.object(ns, '_namespaces', return_value={}), \
             patch.object(ns.backend, '_browser_libc', return_value=Mock(prctl=Mock(return_value=0))), \
             patch.object(ns.backend, '_browser_prctl'), \
             patch.object(cg.CgroupLease, 'create') as create, \
             patch.object(ns.os, 'fork', side_effect=OSError('synthetic fork refusal')):
            result = ns.observe_private_proc('a'*64)
        create.assert_not_called()
        self.assertEqual(result['cgroup'], 'NOT_RUN')
        self.assertFalse(result['execution_authority'])

    def test_missing_or_malformed_sealed_mode_refuses_before_descriptor_resume(self):
        import fcntl
        for mode in (None, 0, 1, 'true', [], {}):
            packet = dict.fromkeys(('generation','supervisor','deadline','outside_uid','outside_gid',
                                    'original_namespaces','namespaces','process','images','descriptors','mask'))
            if mode is not None: packet['cgroup_required'] = mode
            encoded = json.dumps(packet,sort_keys=True,separators=(',',':')).encode()
            fd = os.memfd_create('synthetic-mode',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
            inherited_stdio = [os.get_inheritable(number) for number in (0, 1, 2)]
            try:
                os.write(fd,encoded)
                seals=fcntl.F_SEAL_SEAL|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_WRITE
                fcntl.fcntl(fd,fcntl.F_ADD_SEALS,seals)
                with self.subTest(mode=mode), patch.object(ns.os,'readlink',return_value='/dev/null'), \
                     patch.object(ns,'_resume_descriptor') as resume, self.assertRaises(ns.NamespaceProofError):
                    ns._resume_guardian(['--resume-diagnostic',str(fd)])
                resume.assert_not_called()
            finally:
                for number, inherited in enumerate(inherited_stdio):
                    os.set_inheritable(number, inherited)
                os.close(fd)

    def test_real_stopped_guardian_and_init_use_simulated_enrollment_only(self):
        root = str(Path(ns.__file__).resolve().parent)
        program = r'''
import os,signal,sys,time,json
sys.path.insert(0,ROOT)
import hosted_synthetic_namespace as n
n.backend._browser_prctl(36,1)
generation='a'*64
failure=FAILURE
deadline=time.monotonic()+4
gr,gw=n._pipe(); sr,sw=n._pipe()
supervisor=os.getpid()
guardian=os.fork()
if guardian==0:
 child=-1
 try:
  n._hardening(supervisor); os.setsid()
  release_read,release_write=os.pipe()
  child=os.fork()
  if child==0:
   n._hardening(os.getppid()); os.read(release_read,1); os._exit(0)
  fd=n._pidfd(child)
  original=n._read
  def read(path, maximum=n._LIMIT):
   if path=='/proc/self/cgroup': return b'0::/unit/supervisor\n'
   if path==f'/proc/{child}/cgroup': return ('0::/unit/buffalo-'+generation+'\n').encode()
   return original(path,maximum)
  n._read=read
  n._guardian_enrollment(gr,sw,child,fd,generation,deadline)
  os.write(release_write,b"x")
  n._wait(child,deadline); child=-1;fd.close()
  os._exit(0)
 except BaseException:
  if child>0:
   os.kill(child,signal.SIGKILL);os.waitpid(child,0)
  os._exit(1)
owners=[];fd=n._pidfd(guardian);reaped=False
class SimulatedLease:
 path='/unit/buffalo-'+generation
 def enroll_stopped_init(self, **values):
  assert n._process(guardian)['state']=='T'
  assert n._process(values['pid'])['state']=='T'
  assert n._process(values['pid'])['parent']==guardian
  assert values['guardian_start']==n._process(guardian)['start']
  # No cgroup filesystem operation, namespace, executable sentinel or provider.
  if failure=='enroll': raise OSError('synthetic enrollment refusal')
original_send=n._send
def send(*args,**kwargs):
 if failure=='ack' and args[2]=='ENROLLED': raise OSError('synthetic acknowledgment refusal')
 return original_send(*args,**kwargs)
n._send=send
original_signal=signal.pidfd_send_signal
def dispatch(fd,selected):
 if failure=='resume' and selected==signal.SIGCONT: raise OSError('synthetic resume refusal')
 return original_signal(fd,selected)
signal.pidfd_send_signal=dispatch
try:
 bound=n._supervisor_enrollment(SimulatedLease(),gw,sr,fd,guardian,n._process(guardian)['start'],generation,deadline,owners)
 n._wait(guardian,deadline);reaped=True
 assert n.backend._browser_pidfd_is_terminal(bound['pidfd'].number)
 print('real stops; simulated enrollment; zero release')
except OSError:
 assert failure in ('enroll','ack','resume') and len(owners)==1
 assert n._process(guardian)['state']=='T'
 assert not n.backend._browser_pidfd_is_terminal(owners[0].number)
 print('real stops; simulated enrollment; zero release')
finally:
 if not reaped:
  try:os.killpg(guardian,signal.SIGKILL)
  except ProcessLookupError:pass
  try:os.kill(guardian,signal.SIGKILL)
  except ProcessLookupError:pass
  os.waitpid(guardian,0)
 while True:
  try:os.waitpid(-guardian,0)
  except ChildProcessError:break
 assert all(n.backend._browser_pidfd_is_terminal(item.number) for item in owners)
 for item in [*owners,fd,gr,gw,sr,sw]:item.close()
'''.replace('ROOT', repr(root))
        for failure in ('none', 'enroll', 'ack', 'resume'):
            with self.subTest(failure=failure):
                result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program.replace('FAILURE', repr(failure))],
                                        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'},
                                        capture_output=True, timeout=8)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(result.stdout, b'real stops; simulated enrollment; zero release\n')



if __name__ == '__main__':
    unittest.main()

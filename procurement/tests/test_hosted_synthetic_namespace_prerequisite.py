"""Policy/syscall classification only; actual namespace attempt is mocked."""
import errno
import json
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch
import hosted_synthetic_namespace_prerequisite as probe
import hosted_synthetic_namespace as ns

class NamespacePrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.binding=SimpleNamespace(commit='a'*40,tree='b'*40)
    def run_probe(self,result):
        with patch.object(probe,'inspect_host_identity',return_value={'entry':'checked'}),patch.object(probe,'observe_policy',return_value={}),patch.object(probe,'_run_probe',return_value=result) as run:
            answer=probe.observe_prerequisite(self.binding)
        self.assertEqual(run.call_count,1)
        self.assertNotIn('sudo',run.call_args.args[0]);return answer
    def result(self,number):
        raw={'operation':'unshare_USER_MNT_PID_NET','attempts':1,'errno':number,'errno_name':errno.errorcode.get(number),'result':'SYSCALL_REFUSED' if number else 'SYSCALL_SUCCEEDED_ONLY','attempt_context_policy':{k:{'state':'MISSING'} for k in probe._POLICY}}
        return SimpleNamespace(returncode=0,stdout=json.dumps(raw).encode(),stderr=b'')
    def test_precise_errno_is_preserved_without_policy_attribution(self):
        for number in (errno.EPERM,errno.EACCES,errno.ENOSPC):
            with self.subTest(number=number):
                value=self.run_probe(self.result(number));self.assertEqual(value['operation']['errno'],number)
                self.assertTrue(value['policy_cause'].startswith('UNCONFIRMED'))
    def test_success_is_only_initial_syscall_not_containment(self):
        value=self.run_probe(self.result(0));self.assertEqual(value['maps_mounts_payloads'],0)
        self.assertFalse(value['execution_authority']);self.assertEqual(value['source_commit'],'a'*40)
    def test_generic_exit_is_harness_failure_not_inferred_eperm(self):
        value=self.run_probe(SimpleNamespace(returncode=1,stdout=b'',stderr=b'untrusted error text'))
        self.assertEqual(value['operation']['result'],'HARNESS_FAILURE');self.assertIsNone(value['operation']['errno'])
        self.assertNotIn('untrusted',str(value))
    def test_timeout_is_distinct_from_syscall_refusal(self):
        with patch.object(probe,'inspect_host_identity',return_value={}),patch.object(probe,'observe_policy',return_value={}),patch.object(probe,'_run_probe',side_effect=subprocess.TimeoutExpired('fixed',3)):
            value=probe.observe_prerequisite(self.binding)
        self.assertEqual(value['operation']['result'],'HARNESS_TIMEOUT')
    def test_entry_refusal_precedes_child_and_policy(self):
        with patch.object(probe,'inspect_host_identity',side_effect=RuntimeError('identity')),patch.object(probe,'_run_probe') as run,patch.object(probe,'observe_policy') as policy,self.assertRaises(RuntimeError):probe.observe_prerequisite(self.binding)
        run.assert_not_called();policy.assert_not_called()
    def test_policy_missing_and_denied_are_distinct(self):
        for number,state in ((errno.ENOENT,'MISSING'),(errno.EACCES,'DENIED'),(errno.EIO,'READ_ERROR')):
            with self.subTest(number=number),patch.object(ns,'_read',side_effect=OSError(number,'private')):
                values=probe.observe_policy();self.assertTrue(all(x['state']==state for x in values.values()))
                self.assertNotIn('private',str(values))
    def test_oversized_policy_is_not_pass(self):
        with patch.object(ns,'_read',side_effect=ns.NamespaceProofError('bound')):
            self.assertTrue(all(x['state']=='MALFORMED_OR_OVERSIZED' for x in probe.observe_policy().values()))
    def test_unknown_profile_is_hashed_without_leaking_name(self):
        with patch.object(probe,'_POLICY',{'/proc/self/attr/current':'profile'}),patch.object(ns,'_read',return_value=b'private-profile (enforce)\n'):
            value=probe.observe_policy();self.assertNotIn('private-profile',str(value))
            self.assertEqual(value['/proc/self/attr/current']['profile'],'REDACTED_PROFILE')
    def test_malformed_numeric_policy_is_explicit(self):
        with patch.object(probe,'_POLICY',{'/fixed':'integer'}),patch.object(ns,'_read',return_value=b'-1\n'):
            self.assertEqual(probe.observe_policy()['/fixed']['state'],'MALFORMED_OR_OVERSIZED')
    def test_exact_launcher_namespace_flags_one_attempt(self):
        import ast
        tree=ast.parse(probe._CHILD)
        calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='unshare']
        self.assertEqual(len(calls),1)
        self.assertIn('os.CLONE_NEWUSER|os.CLONE_NEWNS|os.CLONE_NEWPID|os.CLONE_NEWNET',probe._CHILD)
        self.assertNotIn('map-root-user',probe._CHILD)
        self.assertIn('result["attempt_context_policy"]=policy',probe._CHILD)
    def test_errno_forgery_refuses(self):
        result=self.result(errno.EPERM);value=json.loads(result.stdout);value['errno_name']='EACCES';result.stdout=json.dumps(value).encode()
        with self.assertRaises(ns.NamespaceProofError):self.run_probe(result)
    def test_malformed_output_is_harness_failure(self):
        value=self.run_probe(SimpleNamespace(returncode=0,stdout=b'{',stderr=b''))
        self.assertEqual(value['operation']['result'],'HARNESS_MALFORMED_OUTPUT')

    def test_bounded_collector_reaps_real_benign_child_and_closes_fds(self):
        import sys
        from hosted_synthetic_runtime_regression import _descriptor_inventory
        before=_descriptor_inventory()
        result=probe._run_probe([sys.executable,'-I','-S','-c','print("synthetic")'])
        self.assertEqual(result.stdout,b'synthetic\n');self.assertEqual(result.returncode,0)
        self.assertEqual(_descriptor_inventory(),before)

    def test_bounded_collector_excess_output_refuses_and_cleans(self):
        import sys
        from hosted_synthetic_runtime_regression import _descriptor_inventory
        before=_descriptor_inventory()
        with self.assertRaises(ns.NamespaceProofError):probe._run_probe([sys.executable,'-I','-S','-c','print("x"*9000)'])
        self.assertEqual(_descriptor_inventory(),before)

if __name__=='__main__':unittest.main()

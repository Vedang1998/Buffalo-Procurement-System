"""Current tree binding, projection identity and offline component boundaries."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import hosted_synthetic_application_runtime as app
import hosted_synthetic_runtime as runtime
import hosted_synthetic_runtime_regression as regression
import hosted_synthetic_source as source
import hosted_synthetic_namespace as ns
from test_hosted_synthetic_runtime import fixture_bundle


class ApplicationRuntimeTests(unittest.TestCase):
    def fixture(self):
        binding=source.HostedSourceBinding('b'*40,'c'*40,())
        contents={p:b'fixture' for p in (*app.INPUT_FILES,*[d+'file' for d in app.INPUT_DIRECTORIES])}
        records=tuple((p,b'100644',hashlib.sha1(b'blob '+str(len(v)).encode()+b'\0'+v).hexdigest().encode()) for p,v in contents.items())
        def git(root,*args):return ((binding.tree if args[-1]=='HEAD^{tree}' else binding.commit)+'\n').encode()
        return binding,contents,records,git

    def test_application_enumerates_bound_immutable_tree_oid(self):
        binding,contents,records,git=self.fixture()
        with patch.object(source,'_git',side_effect=git),patch.object(source,'_tree_records',return_value=records) as read_tree,patch.object(source,'_file_bytes',side_effect=lambda r,p,m:contents[p]):
            self.assertEqual(app.application_files(Path('/fixture'),binding),contents)
        read_tree.assert_called_once_with(Path('/fixture'),binding.tree)

    def test_changed_current_application_bytes_refuse_git_blob_binding(self):
        binding,contents,records,git=self.fixture()
        with patch.object(source,'_git',side_effect=git),patch.object(source,'_tree_records',return_value=records),patch.object(source,'_file_bytes',return_value=b'other'),self.assertRaises(ns.NamespaceProofError):
            app.application_files(Path('/fixture'),binding)

    def test_missing_fixed_application_inputs_refuse(self):
        binding,contents,records,git=self.fixture()
        with patch.object(source,'_git',side_effect=git),patch.object(source,'_tree_records',return_value=records[1:]),patch.object(source,'_file_bytes',side_effect=lambda r,p,m:contents[p]),self.assertRaises(ns.NamespaceProofError):
            app.application_files(Path('/fixture'),binding)

    def test_executable_and_startup_hook_inputs_refuse(self):
        binding,contents,records,git=self.fixture()
        for altered in ((records[0][0],b'100755',records[0][2]),('procurement/src/custom.pth',b'100644',records[0][2])):
            with self.subTest(altered=altered),patch.object(source,'_git',side_effect=git),patch.object(source,'_tree_records',return_value=(altered,*records[1:])),patch.object(source,'_file_bytes',return_value=b'fixture'),self.assertRaises(ns.NamespaceProofError):
                app.application_files(Path('/fixture'),binding)

    def test_changed_head_refuses_before_file_read(self):
        binding,*_=self.fixture()
        with patch.object(source,'_git',return_value=('d'*40+'\n').encode()),patch.object(source,'_tree_records') as files,self.assertRaises(ns.NamespaceProofError):
            app.application_files(Path('/fixture'),binding)
        files.assert_not_called()

    def test_tree_selector_cannot_inject_git_option_or_mutable_ref(self):
        for tree in ('--help','main','HEAD~1','a'*39,False):
            with self.subTest(tree=tree),patch.object(source,'_git') as git,self.assertRaises(source.HostedSourceError):source._tree_records(Path('/fixture'),tree)
            git.assert_not_called()

    def test_application_policy_and_dependency_identity_survive_sealed_resume(self):
        value=fixture_bundle();received=None;extended=None
        try:
            application={'scope':'IMPORT_COMPONENT_ONLY','browser_closure':'UNSEALED','credential_release':'WITHHELD'}
            raw=runtime._identity_bytes(value.generation,value.executable,value.source_files,value.native_files,value.source_commit,value.source_tree,
                policy=runtime.APPLICATION_POLICY,application=application)
            entries=[replace(e,content=raw,provenance='audit-source:'+hashlib.sha256(raw).hexdigest()) if e.path==runtime._IDENTITY_PATH else e
                for e in runtime._entries(value) if e.path not in ('/proc','/tmp')]
            bundle=ns.backend._seal_browser_runtime_bundle(tuple(entries))
            extended=runtime.RuntimeBundle(bundle,value.generation,value.executable,value.source_files,value.native_files,value.source_commit,value.source_tree,
                runtime.APPLICATION_POLICY,application)
            extended._owner=extended;extended._token=runtime._TOKEN
            fd=os.dup(bundle.descriptor);packet=extended.packet();packet['bundle']['descriptor']=fd
            try:received=runtime.receive_runtime(packet)
            except BaseException:os.close(fd);raise
            self.assertEqual(received.application,application);self.assertEqual(received.policy,runtime.APPLICATION_POLICY)
            self.assertFalse(received.bundle.execution_authority)
        finally:
            if received:received.close()
            if extended:extended.close()
            value.close()

    def test_diagnostic_policy_never_silently_carries_application_identity(self):
        value=fixture_bundle()
        try:
            value.application={}
            with self.assertRaises(ns.NamespaceProofError):value.verify()
        finally:value.application=None;value.close()

    def test_application_archive_authentication_precedes_any_runtime_capture(self):
        binding=source.HostedSourceBinding('b'*40,'c'*40,(('uv.lock','a'*64),))
        with patch.object(source,'_file_bytes',return_value=b'changed lock'),patch.object(runtime,'capture_runtime') as capture, self.assertRaises(ns.NamespaceProofError):
            app.capture_application('a'*64,Path(__file__).resolve().parents[2],binding,Path('/fixture'))
        capture.assert_not_called()

    def test_component_timeout_preserves_parent_resources_and_closes_input(self):
        import subprocess
        value=fixture_bundle()
        before=regression._descriptor_inventory()
        try:
            with patch.object(regression.subprocess,'run',side_effect=subprocess.TimeoutExpired('component',30)) as run,self.assertRaises(subprocess.TimeoutExpired):
                regression._execute(value,consumer=regression._APPLICATION_CONSUMER,private_net=True)
            self.assertIn('--net',run.call_args.args[0])
            self.assertNotIn('--fork',run.call_args.args[0])
            self.assertEqual(regression._descriptor_inventory(),before)
            value.verify()
        finally:value.close()

    def test_base_close_failure_closes_unreturned_result(self):
        from unittest.mock import Mock
        error=OSError('synthetic base close');base=Mock();result=Mock()
        base.close.side_effect=error
        with self.assertRaises(OSError) as caught:app._finish_capture(base,result,None)
        self.assertIs(caught.exception,error);base.close.assert_called_once();result.close.assert_called_once()

    def test_body_failure_survives_both_cleanup_failures(self):
        from unittest.mock import Mock
        error=ValueError('synthetic body');base=Mock();result=Mock()
        base.close.side_effect=OSError('synthetic base');result.close.side_effect=OSError('synthetic result')
        with self.assertRaises(ValueError) as caught:app._finish_capture(base,result,error)
        self.assertIs(caught.exception,error);self.assertEqual(len(error.__notes__),2)
        base.close.assert_called_once();result.close.assert_called_once()

    def test_successful_capture_transfers_only_result_ownership(self):
        from unittest.mock import Mock
        base=Mock();result=Mock()
        self.assertIs(app._finish_capture(base,result,None),result)
        base.close.assert_called_once();result.close.assert_not_called()

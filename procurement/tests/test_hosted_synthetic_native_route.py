"""Connected route with disposable bytes and controlled host boundaries, never sudo."""
import fcntl
import gzip
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_native_route as route
import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_installation as install
import hosted_synthetic_native_measurement as measurement
import hosted_synthetic_identity as identity
import hosted_synthetic_bootstrap as boot
import hosted_synthetic_postgres_runtime as pg
from hosted_synthetic_source import HostedSourceBinding,REPOSITORY,REF
from test_hosted_synthetic_native_inputs import archive,row
from test_hosted_synthetic_native_installation import SyntheticOps

BINDING=HostedSourceBinding('a'*40,'b'*40,())
ARGS=[REPOSITORY,'push',REF,BINDING.commit]


class NativeRouteTests(unittest.TestCase):
    def fixture(self):
        self.events=[];self.leases=[]
        parent=Path(self.enterContext(tempfile.TemporaryDirectory()))
        payload=gzip.compress(archive().getvalue(),mtime=0);digest=hashlib.sha256(payload).hexdigest()
        (parent/digest).write_bytes(payload)
        selected=dict(row(),artifact_sha256=digest)
        artifact={'publisher':'actions/python-versions','artifact_bytes':len(payload)}
        rows={selected['path']:selected};inventory={'rows':[selected],'artifacts':{digest:artifact}}
        self.enterContext(patch.object(native,'require_complete_inventory',return_value=inventory))
        self.enterContext(patch.object(native,'require_native_capacity'))
        self.enterContext(patch.object(native,'execution_inventory',return_value={'rows':rows,'renamed_logical_files':{}}))
        self.enterContext(patch.object(native,'inventory',return_value={'rows':[selected]}))
        self.enterContext(patch.object(native,'_verify_request'))
        self.enterContext(patch('hosted_synthetic_capability._require_clean_supervisor'))
        self.enterContext(patch('hosted_synthetic_source.bind_hosted_source',return_value=BINDING))
        self.enterContext(patch.object(install,'_source_binding'))
        self.enterContext(patch.object(install,'inspect_import_tools'))
        self.enterContext(patch.object(install,'_NativeOps',SyntheticOps))
        self.enterContext(patch.object(route,'verify_publisher_metadata',side_effect=lambda *a:self.events.append('publisher') or {'fixture':True}))
        self.enterContext(patch.object(identity,'inspect_account_platform'))
        self.enterContext(patch.object(identity,'inspect_account'))
        self.enterContext(patch.object(identity,'identity_processes',return_value=[]))
        self.enterContext(patch('hosted_synthetic_quarantine.reserved_ids',return_value=set()))
        self.enterContext(patch.object(os.path,'lexists',return_value=False))
        create=install.InstallationLease._create
        def record(*a,**k):
            result=create(*a,**k);self.leases.append(result);self.events.append('install');return result
        self.enterContext(patch.object(install.InstallationLease,'_create',side_effect=record))
        def view(binding,root,*,deadline,installation):
            self.events.append('view');self.assertTrue(installation.installed)
            self.assertEqual(installation.total,len(b'synthetic data'))
            request=native.ViewRequest('c'*64,binding.commit,binding.tree,native.INSTALLATION_MANIFEST_SHA256,
                root,installation.owned[''][:2],'d'*64,deadline,native._TOKEN,(1,2),
                {'/opt':[1,3],'/usr/share':[1,4],'/opt/example':[1,5]})
            installation.view_requests[request.nonce]=request;return request
        self.enterContext(patch.object(native,'view_request',side_effect=view))
        self.receipt=object.__new__(measurement.NativePhaseReadiness)
        def verify(*a):self.events.append('memory_verified')
        self.enterContext(patch.object(self.receipt,'verify',side_effect=verify,create=True))
        def measured(consumer,source):
            self.events.append('measure');self.receipt.consumer=consumer;return self.receipt
        self.measure=self.enterContext(patch.object(measurement,'measure',side_effect=measured))
        def drain(consumer,installation):
            measurement._association(consumer,installation);self.events.append('drain_'+consumer.role)
        self.enterContext(patch.object(measurement,'require_consumer_drain',side_effect=drain))
        self.account=SimpleNamespace(closed=False,drain_proof=None,platform={})
        def close():self.events.append('account_cleanup');self.account.closed=True
        self.account.close_after_service=close
        def account(*a,**k):self.events.append('account');return self.account
        self.create=self.enterContext(patch.object(identity.AccountLease,'create',side_effect=account))
        self.plan=self.enterContext(patch.object(boot,'plan_native_bootstrap',return_value=object()))
        def service(*a,**k):
            self.events.append('service');self.assertEqual(k['native_consumer'].role,'service')
            return {'report':{'tree':BINDING.tree}}
        self.service=self.enterContext(patch.object(boot,'run_native_plan',side_effect=service))
        token=route._SCOPE.set(route._TOKEN);self.addCleanup(route._SCOPE.reset,token)
        return parent,digest,selected

    def test_public_entry_refuses_before_source_download_install_or_account(self):
        with patch.object(route,'_run') as run,patch.object(route.subprocess,'Popen') as process:
            with self.assertRaisesRegex(native.ns.NamespaceProofError,'not_authorized'):
                route.installation_measurement_entry(ARGS,'/untrusted')
        run.assert_not_called();process.assert_not_called()

    def test_private_route_cannot_be_called_without_reviewed_entry_scope(self):
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'not_authorized'):route._run(ARGS,'/untrusted')

    def test_connected_materialization_measurement_m1_service_and_drain(self):
        parent,_,_=self.fixture();result=route._run(ARGS,parent)
        self.assertTrue(result['native_installation_removed']);self.assertTrue(result['dedicated_identity_removed'])
        self.assertLess(self.events.index('memory_verified'),self.events.index('account'))
        self.assertLess(self.events.index('account'),self.events.index('service'))
        self.assertLess(self.events.index('account_cleanup'),self.events.index('drain_service'))
        self.assertFalse(self.leases[0].ops.nodes)
        stages=result['preaccount_preflight']['stages']
        self.assertEqual([s['stage'] for s in stages],['PUBLISHER_SNAPSHOT','NATIVE_MATERIALIZATION','NATIVE_CAPACITY_AND_MEMORY'])
        self.assertTrue(all(0<=s['remaining_shared_seconds']<=60 for s in stages))

    def test_memory_refusal_cleans_installation_without_creating_account(self):
        parent,_,_=self.fixture();self.measure.side_effect=pg.PhaseMemoryRefusal({'capacity':'FIT','memory':'REFUSED'})
        with self.assertRaises(pg.PhaseMemoryRefusal):route._run(ARGS,parent)
        self.create.assert_not_called();self.service.assert_not_called();self.assertTrue(self.leases[0].closed)

    def test_boolean_memory_receipt_cannot_create_account(self):
        parent,_,_=self.fixture();self.measure.side_effect=None;self.measure.return_value=True
        with self.assertRaisesRegex(boot.BootstrapRefusal,'memory_readiness_required'):route._run(ARGS,parent)
        self.create.assert_not_called();self.assertTrue(self.leases[0].closed)

    def test_collision_reuses_verified_measurement_without_new_deadline(self):
        parent,_,_=self.fixture();calls=[]
        from hosted_synthetic_account_preflight import OwnershipCollision
        def account(*a,**k):
            calls.append((a[0].uid,k['ownership_deadline'],k['preflight']))
            if len(calls)==1:raise OwnershipCollision('synthetic_owned_file')
            return self.account
        self.create.side_effect=account
        route._run(ARGS,parent)
        self.assertEqual([c[0] for c in calls],[900,901]);self.assertEqual(calls[0][1:],calls[1][1:])
        self.assertEqual(self.measure.call_count,1);self.assertEqual(self.receipt.verify.call_count,3)

    def test_expiry_during_account_creation_blocks_service_and_cleans(self):
        parent,_,_=self.fixture()
        def account(*a,**k):
            self.receipt.verify.side_effect=install.WorkExpired('native_install_work_expired')
            return self.account
        self.create.side_effect=account
        with self.assertRaises(install.WorkExpired):route._run(ARGS,parent)
        self.service.assert_not_called();self.assertTrue(self.account.closed);self.assertTrue(self.leases[0].closed)

    def test_cancellation_after_installation_cleans_known_owned_objects(self):
        parent,_,_=self.fixture();self.measure.side_effect=KeyboardInterrupt('cancelled')
        with self.assertRaises(KeyboardInterrupt):route._run(ARGS,parent)
        self.create.assert_not_called();self.assertTrue(self.leases[0].closed)

    def test_artifact_drift_prevents_view_and_account_and_cleans_partial_installation(self):
        parent,digest,_=self.fixture();(parent/digest).write_bytes(b'x')
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'artifact_shape'):route._run(ARGS,parent)
        self.assertNotIn('view',self.events);self.create.assert_not_called();self.assertTrue(self.leases[0].closed)

    def test_failure_with_unknown_consumer_keeps_original_and_retains_installation(self):
        parent,_,_=self.fixture();error=KeyboardInterrupt('cancelled')
        def failure(*a):self.leases[0].uncertain=True;raise error
        self.measure.side_effect=failure
        with self.assertRaises(KeyboardInterrupt) as raised:route._run(ARGS,parent)
        self.assertIs(raised.exception,error);self.assertFalse(self.leases[0].closed)
        self.assertIn('native_installation_cleanup_unverified',error.__notes__)

    def test_public_trust_metadata_memfd_is_readonly_fully_sealed_and_closed(self):
        before=set(os.listdir('/proc/self/fd'))
        with route._sealed_metadata(b'public key bytes') as fd:
            self.assertEqual(os.pread(fd,100,0),b'public key bytes')
            with self.assertRaises(OSError):os.write(fd,b'x')
            with self.assertRaises(OSError):os.ftruncate(fd,0)
            self.assertEqual(fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
        self.assertEqual(set(os.listdir('/proc/self/fd')),before)

    def test_expired_artifact_read_refuses_before_open(self):
        with patch.object(os,'open') as opened,self.assertRaises(install.WorkExpired):
            route._artifact_fd('/foreign','a'*64,1,time.monotonic()-1)
        opened.assert_not_called()

    def test_temporary_cleanup_preserves_foreign_resource_and_original_error(self):
        path=None;error=KeyboardInterrupt('cancelled')
        try:
            with self.assertRaises(KeyboardInterrupt) as raised:
                with route._empty_directory('buffalo-fixture-') as name:
                    path=Path(name);(path/'foreign').write_bytes(b'owned by fixture');raise error
            self.assertIs(raised.exception,error);self.assertTrue((path/'foreign').exists())
            self.assertIn('native_temporary_directory_retained',error.__notes__)
        finally:
            if path is not None:(path/'foreign').unlink();path.rmdir()

    def test_decoder_expiry_closes_stage_before_producing_materialization(self):
        parent,digest,selected=self.fixture();stage=native.Stage(parent/'stage');fd=os.open(parent/digest,os.O_RDONLY)
        try:
            with self.assertRaises(install.WorkExpired):route.materialize_artifact(stage,fd,digest,[selected],time.monotonic()-1)
        finally:
            os.close(fd)
            if not stage.closed:stage.close()
        self.assertFalse(stage.path.exists())

    def test_fixed_publisher_verification_keeps_digest_and_signature_assurances_separate(self):
        parent=Path(self.enterContext(tempfile.TemporaryDirectory()));key=b'synthetic public key';(parent/'ubuntu-archive-keyring.gpg').write_bytes(key)
        manifest=native.installation_manifest();manifest['trust_root']=dict(manifest['trust_root'],bytes=len(key),sha256=hashlib.sha256(key).hexdigest())
        status=b'[GNUPG:] VALIDSIG '+manifest['trust_root']['signer'].encode()+b' fixture\n'
        with patch.object(native,'installation_manifest',return_value=manifest),patch('hosted_synthetic_runtime.read_native',return_value=(b'fixture tool',0o555)),patch.object(route.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=status,stderr=b'')) as command:
            result=route.verify_publisher_metadata(parent,time.monotonic()+60)
        self.assertEqual(command.call_count,2);self.assertFalse(result['installation_authority'])
        self.assertIn('NOT_SIGNED_BUILD_ATTESTATION',result['python'])
        self.assertIn('OWNER_DECISION_PENDING',result['snapshot_policy'])
        for call in command.call_args_list:
            self.assertEqual(call.args[0][0],'/usr/bin/gpgv');self.assertEqual(len(call.kwargs['pass_fds']),2)
            for fd in call.kwargs['pass_fds']:
                with self.assertRaises(OSError):os.fstat(fd)

    def test_wrong_signature_or_signer_prevents_installation_metadata_result(self):
        parent=Path(self.enterContext(tempfile.TemporaryDirectory()));key=b'synthetic public key';(parent/'ubuntu-archive-keyring.gpg').write_bytes(key)
        manifest=native.installation_manifest();manifest['trust_root']=dict(manifest['trust_root'],bytes=len(key),sha256=hashlib.sha256(key).hexdigest())
        for code,raw in ((1,b''),(0,b'[GNUPG:] VALIDSIG '+b'0'*40+b' fixture\n'),(0,b'')):
            with self.subTest(code=code),patch.object(native,'installation_manifest',return_value=manifest),patch('hosted_synthetic_runtime.read_native',return_value=(b'fixture tool',0o555)),patch.object(route.subprocess,'run',return_value=SimpleNamespace(returncode=code,stdout=raw,stderr=b'')),self.assertRaises(native.ns.NamespaceProofError):
                route.verify_publisher_metadata(parent,time.monotonic()+60)

    def test_wrong_keyring_bytes_refuse_before_signature_tool_or_installation(self):
        parent=Path(self.enterContext(tempfile.TemporaryDirectory()));(parent/'ubuntu-archive-keyring.gpg').write_bytes(b'wrong')
        with patch.object(route.subprocess,'run') as command,self.assertRaises(native.ns.NamespaceProofError):
            route.verify_publisher_metadata(parent,time.monotonic()+60)
        command.assert_not_called()

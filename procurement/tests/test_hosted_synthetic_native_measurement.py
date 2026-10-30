"""Native-view handoff regressions; no sudo, installation, systemd or PG exec."""
from contextlib import ExitStack,redirect_stdout
from dataclasses import replace
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_measurement as measurement
import hosted_synthetic_native_installation as install
import hosted_synthetic_runtime as runtime
import hosted_synthetic_postgres_runtime as pg
import hosted_synthetic_namespace as ns
import hosted_synthetic_identity as identity
import hosted_synthetic_bootstrap as boot
import hosted_synthetic_launcher as launcher
from hosted_synthetic_source import HostedSourceBinding
from test_hosted_synthetic_bootstrap import plan,UNIT
from test_hosted_synthetic_identity import ID as _ID
ID=replace(_ID,operator_uid=os.geteuid(),operator_gid=os.getegid())

BINDING=HostedSourceBinding('a'*40,'b'*40,())
KEYS=('descriptor','target','device','inode','size','sha256','manifest_sha256','entries','regular_bytes','execution_authority')


class NativeMeasurementTests(unittest.TestCase):
    def consumer(self,role='measurement'):
        self.enterContext(patch.object(native,'_verify_request'))
        selected={'path':native.PYTHON,'sha256':'c'*64,'bytes':1,'mode':'0555'}
        self.enterContext(patch.object(native,'execution_inventory',return_value={'rows':{native.PYTHON:selected},'renamed_logical_files':{}}))
        lease=object.__new__(install.InstallationLease);lease._owner=lease;lease._token=install._TOKEN
        lease.root='/run/buffalo-native-'+'a'*32;lease.installed=True;lease.consumers=[];lease.view_requests={}
        lease.binding=BINDING;lease.owned={'':(1,2,stat.S_IFDIR|0o700,0,0,2,0)}
        self.enterContext(patch.object(lease,'verify'))
        request=native.ViewRequest('d'*64,BINDING.commit,BINDING.tree,native.DIGESTS[0],lease.root,(1,2),'c'*64,
            time.monotonic()+60,native._TOKEN,(1,3),{'/opt':[1,4],'/usr/share':[1,5],native.PYTHON:[1,6]})
        lease.view_requests[request.nonce]=request
        return measurement.reserve_consumer(lease,request,BINDING,UNIT,role=role,uid=os.geteuid(),gid=os.getegid())

    def capsule(self,raw=b'{}',path='/native-view.json'):
        entry=ns.backend._BrowserRuntimeBundleEntry(path,'F',0o444,0,0,'audit-source:'+hashlib.sha256(raw).hexdigest(),raw)
        bundle=ns.backend._seal_browser_runtime_bundle((entry,))
        self.addCleanup(lambda: ns.backend._close_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.target,(bundle.device,bundle.inode)))
        return bundle,{key:getattr(bundle,key) for key in KEYS}

    def test_actual_inherited_sealed_capsule_consumed_closed_after_exec(self):
        bundle,packet=self.capsule()
        code='import sys,json,os;sys.path.insert(0,sys.argv[1]);import hosted_synthetic_native_inputs as n;p=json.loads(sys.argv[2]);assert n.consume_view_capsule(p)==b"{}";\ntry:os.fstat(p["descriptor"])\nexcept OSError:print("OWNED_CLOSED")\nelse:raise AssertionError("leaked")'
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(Path(native.__file__).parent),json.dumps(packet)],
            env=boot._ENVIRONMENT,pass_fds=(bundle.descriptor,),capture_output=True,timeout=10)
        self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'OWNED_CLOSED\n',b''))
        os.fstat(bundle.descriptor)  # Parent retains its independently owned handle.

    def test_oversized_owned_control_capsule_refuses_and_closes_before_decode(self):
        _,packet=self.capsule(b'x'*65537)
        with patch.object(ns.backend,'_validate_pinned_browser_runtime_bundle') as decode,self.assertRaisesRegex(ns.NamespaceProofError,'capsule_bound'):
            native.consume_view_capsule(packet)
        decode.assert_not_called()
        with self.assertRaises(OSError):os.fstat(packet['descriptor'])

    def test_foreign_or_stale_descriptor_is_not_closed_or_modified(self):
        _,packet=self.capsule();fd=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC);self.addCleanup(os.close,fd)
        packet['descriptor']=fd
        with self.assertRaises(ns.NamespaceProofError):native.consume_view_capsule(packet)
        self.assertFalse(os.get_inheritable(fd));os.fstat(fd)

    def test_owned_wrong_content_and_integrity_close_the_transferred_descriptor(self):
        for path,change in (('/wrong',False),('/native-view.json',True)):
            _,packet=self.capsule(path=path)
            if change:packet['sha256']='f'*64
            with self.assertRaises((ns.NamespaceProofError,ns.backend.LocalStagingAcceptanceError)):native.consume_view_capsule(packet)
            with self.assertRaises(OSError):os.fstat(packet['descriptor'])

    def test_wire_roundtrip_preserves_nonce_source_backing_and_deadline(self):
        c=self.consumer();actual=native.view_from_wire(c.wire,BINDING)
        self.assertEqual(actual,c.request);self.assertLess(len(c.wire),65536)

    def test_wire_malformed_types_noncanonical_and_backing_population_refuse(self):
        c=self.consumer();value=json.loads(c.wire)
        cases=[b'[]',c.wire+b' ',b'null']
        for field,replacement in [('backing',[]),('root_identity',True),('proposed_manifest','f'*64)]:
            changed=dict(value);changed[field]=replacement;cases.append(native._canonical(changed))
        for raw in cases:
            with self.subTest(raw=raw[:30]),self.assertRaises(ns.NamespaceProofError):native.view_from_wire(raw,BINDING)

    def test_changed_request_nonce_deadline_or_unit_cannot_reuse_reservation(self):
        for field,value in [('nonce','e'*64),('deadline',time.monotonic()+999),('root_identity',(9,9))]:
            c=self.consumer();c.request=replace(c.request,**{field:value})
            with self.subTest(field=field),self.assertRaises(ns.NamespaceProofError):measurement.verify_consumer(c)
        c=self.consumer();c.unit=UNIT.replace('aaaa','bbbb')
        with self.assertRaises(ns.NamespaceProofError):measurement.require_consumer_drain(c,c.installation)

    def test_unattempted_reservation_is_not_a_forged_drain_proof(self):
        c=self.consumer();measurement.require_consumer_drain(c,c.installation)
        for fake in (True,Mock(),SimpleNamespace(require_native_drain=lambda x:None)):
            with self.assertRaises(ns.NamespaceProofError):measurement.require_consumer_drain(fake,c.installation)
        c.attempted=True
        with self.assertRaises(ns.NamespaceProofError):measurement.require_consumer_drain(c,c.installation)

    def test_matching_terminal_observer_requires_all_handles_and_exact_consumer(self):
        c=self.consumer();o=object.__new__(measurement.MeasurementObserver);o._owner=o;o.consumer=c;o.unit=c.unit
        o.closed=True;o.failed=False;o.drained=True;o.descriptors_closed=True;c.attempted=True;c.observer=o
        measurement.require_consumer_drain(c,c.installation)
        for field,value in [('closed',False),('failed',True),('drained',False),('descriptors_closed',False),('consumer',None)]:
            old=getattr(o,field);setattr(o,field,value)
            with self.subTest(field=field),self.assertRaises(ns.NamespaceProofError):measurement.require_consumer_drain(c,c.installation)
            setattr(o,field,old)

    def test_capture_report_cannot_mint_parent_readiness(self):
        with self.assertRaises(ns.NamespaceProofError):measurement.NativePhaseReadiness()
        with self.assertRaises(ns.NamespaceProofError):measurement.NativeConsumer()
        with self.assertRaises(ns.NamespaceProofError):measurement.MeasurementObserver()

    def test_native_route_memory_refusal_precedes_account_creation(self):
        c=self.consumer();failure=pg.PhaseMemoryRefusal({'capacity':'FIT','memory':{'result':'REFUSED_COMPONENT_ENVELOPE'}})
        with patch('hosted_synthetic_capability._require_clean_supervisor'),patch('hosted_synthetic_source.bind_hosted_source',return_value=BINDING),patch.object(measurement,'measure',side_effect=failure),patch.object(identity.AccountLease,'create') as create,self.assertRaises(pg.PhaseMemoryRefusal):
            identity.run_provisioned_candidate(ID,source='/fixture',python=native.PYTHON,repository='repo',event='push',ref='ref',commit=BINDING.commit,
                diagnostic='postgres-version',ownership_deadline=c.request.deadline,native_measurement=c)
        create.assert_not_called()

    def test_native_route_missing_or_boolean_readiness_never_creates_account(self):
        c=self.consumer()
        for result in (None,True,{'capacity':'FIT'}):
            with self.subTest(result=result),patch('hosted_synthetic_capability._require_clean_supervisor'),patch('hosted_synthetic_source.bind_hosted_source',return_value=BINDING),patch.object(measurement,'measure',return_value=result),patch.object(identity.AccountLease,'create') as create,self.assertRaisesRegex(boot.BootstrapRefusal,'memory_readiness_required'):
                identity.run_provisioned_candidate(ID,source='/fixture',python=native.PYTHON,repository='repo',event='push',ref='ref',commit=BINDING.commit,
                    diagnostic='postgres-version',ownership_deadline=c.request.deadline,native_measurement=c)
            create.assert_not_called()

    def test_valid_owned_readiness_reaches_next_gate_and_is_verified_first(self):
        c=self.consumer();receipt=object.__new__(measurement.NativePhaseReadiness);seen=[]
        with patch('hosted_synthetic_capability._require_clean_supervisor'),patch('hosted_synthetic_source.bind_hosted_source',return_value=BINDING),patch.object(measurement,'measure',return_value=receipt),patch.object(receipt,'verify',side_effect=lambda *a:seen.append('verify')),patch.object(identity.AccountLease,'create',side_effect=lambda *a,**k:(_ for _ in ()).throw(boot.BootstrapRefusal('next_account_gate'))) as create,self.assertRaisesRegex(boot.BootstrapRefusal,'next_account_gate'):
            identity.run_provisioned_candidate(ID,source='/fixture',python=native.PYTHON,repository='repo',event='push',ref='ref',commit=BINDING.commit,
                diagnostic='postgres-version',ownership_deadline=c.request.deadline,native_measurement=c)
        self.assertEqual(seen,['verify']);create.assert_called_once()

    def test_measurement_ack_and_source_mismatch_never_start_capture(self):
        c=self.consumer()
        o=object.__new__(measurement.MeasurementObserver);o.consumer=c;o.ready=False
        frame={'nonce':'e'*64,'commit':BINDING.commit,'tree':BINDING.tree,'unit':UNIT,'view_sha256':c.digest,'pid':123,'start':456}
        with patch.object(measurement.outer,'_manager') as manager,self.assertRaisesRegex(ns.NamespaceProofError,'ready_refused'):o.bind_ready(frame)
        manager.assert_not_called()

    def test_service_plan_keeps_native_drop_bounds_and_exact_view(self):
        c=self.consumer()
        with patch.object(native,'require_complete_inventory'),patch.object(native,'require_native_capacity'):
            result=plan(python=native.PYTHON,diagnostic='postgres-version',native_view_wire=c.wire)
        self.assertEqual(result.native_view_wire,c.wire)
        rendered=' '.join(result.argv)
        self.assertIn('--native-view-v1',rendered);self.assertIn('MemoryMax=536870912',rendered)
        self.assertIn('--bounding-set=-all',rendered);self.assertIn('TasksMax=64',rendered)
        self.assertNotIn('MemoryMax=1024',rendered)
        with self.assertRaises(boot.BootstrapRefusal):plan(native_view_wire=c.wire)

    def test_capability_consumes_capsule_before_unchanged_clean_gate(self):
        import hosted_synthetic_capability as capability
        _,packet=self.capsule();seen=[]
        args=['repo','push','ref','a'*40,'--native-bootstrap-postgres-version',UNIT,'1001','1001','5','123',
              '--native-view-capsule-v1',json.dumps(packet)]
        def inner(*args,**kwargs):
            with self.assertRaises(OSError):os.fstat(packet['descriptor'])
            seen.append(kwargs['native_wire']);return 1
        with patch.object(launcher,'inspect_host_identity'),patch.object(capability,'_main',side_effect=inner):
            self.assertEqual(capability.main(args),1)
        self.assertEqual(seen,[b'{}'])

    def test_capability_rejects_privileges_before_touching_capsule(self):
        import hosted_synthetic_capability as capability
        _,packet=self.capsule();args=['repo','push','ref','a'*40,'--native-bootstrap-postgres-version',UNIT,'1001','1001','5','123','--native-view-capsule-v1',json.dumps(packet)]
        with patch.object(launcher,'inspect_host_identity',side_effect=launcher.LauncherRefusal('groups')),patch.object(native,'consume_view_capsule') as consume,redirect_stdout(io.StringIO()):
            self.assertEqual(capability.main(args),1)
        consume.assert_not_called();os.fstat(packet['descriptor'])

    def test_native_activation_remains_unconditional_refusal(self):
        with patch.object(boot.subprocess,'Popen') as process:
            with self.assertRaises(ns.NamespaceProofError):install.activate_installation(approved=True)
        process.assert_not_called()

    def test_loaded_maps_reject_unbound_host_fallback_and_anonymous_execution(self):
        c=self.consumer();device,inode=c.request.backing[native.PYTHON]
        line=f"1000-2000 r-xp 00000000 {os.major(device):02x}:{os.minor(device):02x} {inode} {native.PYTHON}\n".encode()
        self.assertEqual(native.validate_loaded_mappings(line,c.request),[(native.PYTHON,device,inode)])
        for bad in (line.replace(native.PYTHON.encode(),b'/usr/lib/host-fallback.so'),line.replace(b' 6 ',b' 7 '),
                    line+b'3000-4000 rwxp 00000000 00:00 0\n',line[:-1]):
            with self.subTest(bad=bad[-40:]),self.assertRaises(ns.NamespaceProofError):native.validate_loaded_mappings(bad,c.request)

    def test_missing_ack_can_drain_but_cannot_validate_memory_result(self):
        c=self.consumer();o=object.__new__(measurement.MeasurementObserver);o._owner=o;o.consumer=c;o.ready=True
        o.work_acknowledged=False;o.failed=False;o.service_invocation='f'*32;o.service=Mock();o.actor=Mock();o.root=Mock()
        o.unit=c.unit;o.slice_unit='slice';o.path='/owned';o.mount=1;o.invocation='e'*32;o.drained=False
        o.read=lambda name:b'populated 0\nfrozen 0\n' if name=='cgroup.events' else b''
        with patch.object(o,'sample'),patch.object(measurement.outer,'_await_absence'),patch.object(measurement.outer,'_removed'),patch.object(measurement.outer,'_children',return_value=[]),patch.object(boot,'_collect_native_client',return_value=(0,b'')):
            o.finish()
        self.assertTrue(o.drained);self.assertFalse(o.work_acknowledged)

    def test_pre_ready_refusal_waits_for_two_absences_before_owned_slice_stop(self):
        c=self.consumer();o=object.__new__(measurement.MeasurementObserver);o._owner=o;o.consumer=c;o.unit=c.unit
        o.initial_service_absent=True;o.ready=False;o.work_acknowledged=False;o.actor=None;o.service=None;o.failed=False
        o.root=Mock();o.slice_unit='slice';o.path='/owned';o.mount=1;o.invocation='e'*32;o.drained=False
        o.read=lambda name:b'populated 0\nfrozen 0\n' if name=='cgroup.events' else b''
        refused={'native_measurement':'REFUSED','nonce':c.request.nonce,'commit':BINDING.commit,'tree':BINDING.tree,'view_sha256':c.digest}
        with patch.object(measurement.outer,'_manager',return_value={'LoadState':'not-found'}) as manager,patch.object(os.path,'lexists',return_value=False),patch.object(measurement.outer,'_children',return_value=[]),patch.object(o,'sample'),patch.object(boot,'_collect_native_client',return_value=(0,b'')) as command,patch.object(measurement.outer,'_await_absence'),patch.object(measurement.outer,'_removed'),patch.object(measurement.time,'sleep'):
            o.finish_refusal(refused,1)
        self.assertEqual(manager.call_count,2);command.assert_called_once();self.assertTrue(o.drained)

    def test_unbound_pre_ready_refusal_cannot_remove_slice(self):
        c=self.consumer();o=object.__new__(measurement.MeasurementObserver);o.consumer=c
        with patch.object(boot,'_collect_native_client') as command,self.assertRaises(ns.NamespaceProofError):o.finish_refusal({'native_measurement':'REFUSED'},1)
        command.assert_not_called()

"""Dormant native-input contract tests; no sudo, installation or payload exec."""
from dataclasses import replace
from pathlib import Path
import copy
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from unittest.mock import patch,Mock

import hosted_synthetic_native_inputs as native
import hosted_synthetic_runtime as runtime
import hosted_synthetic_namespace as ns
from test_hosted_synthetic_runtime import elf


def archive(name='bin/example',content=b'synthetic data', *, link=False):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w') as tar:
        item=tarfile.TarInfo(name);item.mode=0o644;item.size=len(content)
        if link:item.type=tarfile.SYMTYPE;item.linkname='/foreign';item.size=0
        tar.addfile(item,None if link else io.BytesIO(content))
    output.seek(0);return output


def row():
    return {'artifact_member':'bin/example','path':'/opt/example','bytes':len(b'synthetic data'),
            'sha256':hashlib.sha256(b'synthetic data').hexdigest(),'mode':'0444'}


class NativeInputTests(unittest.TestCase):
    def stage(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        stage=native.Stage(root/'stage')
        self.addCleanup(lambda: None if stage.closed else stage.close())
        return stage

    def test_package_claim_is_exact_but_not_publisher_authentication(self):
        import hosted_synthetic_native_observation as observation
        self.assertEqual(observation.package_rows(b'libc6:amd64\t2.39-0ubuntu8.1\tamd64\tii \n',{'libc6:amd64'}),
            {'libc6:amd64':{'version':'2.39-0ubuntu8.1','architecture':'amd64'}})
        for raw in (b'',b'libc6:amd64\tlatest\tamd64\trc \n',b'libc6:amd64\t1\tarm64\tii \n',
                    b'libc6:amd64\t1\tamd64\tii \n'*2,b'libc6:amd64\t1\tamd64\tii \nextra',b'x'*32769):
            with self.subTest(raw=raw[:32]),self.assertRaises(ns.NamespaceProofError):observation.package_rows(raw,{'libc6:amd64'})

    def test_ambiguous_diverted_or_wrong_path_owner_is_not_selected(self):
        import hosted_synthetic_native_observation as observation
        self.assertEqual(observation.package_owner(b'libc6:amd64: /usr/lib/libc.so.6\n','/usr/lib/libc.so.6'),'libc6:amd64')
        for raw in (b'libc6: /wrong\n',b'libc6, foreign: /usr/lib/libc.so.6\n',b'local diversion from: /usr/lib/libc.so.6\n',
                    b'libc6: /usr/lib/libc.so.6\nlibc6: /usr/lib/libc.so.6\n',b'\xff\n'):
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):observation.package_owner(raw,'/usr/lib/libc.so.6')

    def test_observation_deadline_refuses_before_file_open(self):
        import hosted_synthetic_native_observation as observation
        with patch.object(observation.time,'monotonic',return_value=2),patch.object(os,'open') as opening,self.assertRaisesRegex(ns.NamespaceProofError,'deadline'):
            observation.observe_file('/usr/lib/libc.so.6',1)
        opening.assert_not_called()

    def test_readonly_observer_never_authorizes_installation(self):
        import hosted_synthetic_native_observation as observation
        from hosted_synthetic_source import HostedSourceBinding
        empty=dict(native.inventory(),required_unresolved_external_sonames=[],required_unresolved_timezone_files=[])
        with patch.object(observation.native,'inventory',return_value=empty),patch.object(observation,'observe_file',side_effect=FileNotFoundError),patch.object(observation.boot,'_collect_native_client') as command:
            report=observation.observe(HostedSourceBinding('a'*40,'b'*40,()))
        command.assert_not_called();self.assertFalse(report['execution_authority'])
        self.assertEqual(report['privileged_activation'],'DISABLED')
        self.assertTrue(report['unresolved']);self.assertEqual(report['publisher_authentication'],'NOT_ESTABLISHED_BY_DPKG_OR_LOCAL_HASH')

    def test_public_inventory_compression_is_complete_digest_bound_and_notice_bounded(self):
        import hosted_synthetic_native_observation as observation
        import base64,gzip
        value={'status':'TARGET_OBSERVATION_ONLY','rows':[{'path':'/usr/lib/example'+str(i),'sha256':hashlib.sha256(str(i).encode()).hexdigest()} for i in range(300)]}
        encoded=observation.public_envelope(value);envelope=json.loads(encoded)
        raw=gzip.decompress(base64.b64decode(envelope['payload'],validate=True))
        self.assertEqual(json.loads(raw),value);self.assertEqual(len(raw),envelope['bytes'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),envelope['sha256'])
        self.assertLessEqual((len(base64.b64encode(encoded.encode()))+2999)//3000,10)
        with self.assertRaises(ns.NamespaceProofError):observation.public_envelope({'oversized':'x'*65537})

    def test_authenticated_native_inventory_refuses_unchanged_capacity(self):
        result=native.proposed_capacity()
        self.assertEqual((result['logical_files'],result['logical_regular_bytes'],result['native_entries_with_parents']),(831,149540145,915))
        self.assertEqual(result['physical_lower_bound_bytes'],149303529)
        self.assertEqual((result['maximum_regular_bytes'],result['maximum_entries']),(134217728,3000))
        self.assertEqual((native._STAGE_MAX_BYTES,native._STAGE_MAX_FILES),(134217728,3000))
        self.assertFalse(result['execution_authority']);self.assertEqual(result['memory_readiness'],'NOT_MEASURED')
        with self.assertRaises(native.NativeCapacityRefusal) as caught:native.require_native_capacity()
        self.assertEqual(caught.exception.evidence,result)

    def test_libpq_layout_preserves_authenticated_bytes_and_uses_requested_name(self):
        name='/usr/lib/x86_64-linux-gnu/libpq.so.5'
        for value in (native.inventory(),native._proposed_inventory()):
            selected=[r for r in value['rows'] if r['path']==name]
            self.assertEqual(len(selected),1);row=selected[0]
            self.assertEqual(row['mode'],'0444')
            self.assertEqual(row['artifact_member'],'./usr/lib/x86_64-linux-gnu/libpq.so.5.16')
            self.assertEqual(row['artifact_sha256'],'3f96dd36bcd7841172c1ead4965da20a193bb9ccf9a93a3f5a3ac7f56369dbb1')
            self.assertEqual((row['bytes'],row['sha256']),(354488,'b4ba0da9e51354cf40a599e787f0942c98fc268cf29b51806c62770d8f2a2a71'))
            self.assertFalse(any(r['path']==name+'.16' for r in value['rows']))
        for deps in native._proposed_inventory()['resolved_dependencies'].values():
            if 'libpq.so.5' in deps:self.assertEqual(deps['libpq.so.5'],name)

    def test_loader_logical_name_can_materialize_a_different_authenticated_member(self):
        stage=self.stage();selected=dict(row(),artifact_member='lib/libpq.so.5.16',path='/usr/lib/libpq.so.5')
        stage.materialize_tar(archive(name=selected['artifact_member']),[selected])
        with patch.object(native,'inventory',return_value={'rows':[selected]}):
            with stage.verified_stream(selected) as chunks:self.assertEqual(b''.join(chunks),b'synthetic data')
        self.assertEqual((stage.path/'usr/lib/libpq.so.5').stat().st_mode&0o777,0o444)
        self.assertFalse((stage.path/'usr/lib/libpq.so.5.16').exists())

    def test_proposed_inventory_totals_policy_and_alias_conflicts_are_not_authority(self):
        original=native._proposed_inventory()
        cases=[]
        for key,value in [('known_regular_bytes',1),('known_file_count',True),('physical_content_count',True),('physical_content_bytes',1)]:
            changed=copy.deepcopy(original);changed[key]=value;cases.append(changed)
        for key,value in [('policy','BUFFALO_DIAGNOSTIC'),('cap_bytes',150000000)]:
            changed=copy.deepcopy(original);changed['capacity'][key]=value;cases.append(changed)
        changed=copy.deepcopy(original);changed['rows'][0]['bytes']=True;cases.append(changed)
        changed=copy.deepcopy(original);changed['rows'][0]['physical_path']=changed['rows'][1]['physical_path'];cases.append(changed)
        for changed in cases:
            with self.subTest(change=cases.index(changed)),patch.object(native,'_proposed_inventory',return_value=changed),self.assertRaises(ns.NamespaceProofError):native.proposed_capacity()

    def test_proposed_inventory_hash_cannot_be_replaced_by_new_local_hash(self):
        with patch.object(Path,'read_bytes',return_value=b'{}'),self.assertRaisesRegex(ns.NamespaceProofError,'inventory_identity'):
            native.proposed_capacity()

    def test_capacity_refusal_precedes_every_native_plan_and_view_effect(self):
        def protected():
            with native.protected_view(object(),object()):self.fail('view entered')
        actions=(lambda:native.native_install_plan('a'*32,object()),
            lambda:native.view_request(object(),'/foreign',deadline=10**12),
            lambda:native.view_properties(object(),object()),protected,
            lambda:native.bind_measurement_result(object(),object(),True))
        with patch.object(native,'require_complete_inventory') as complete,patch.object(native,'_verify_request') as verify,patch.object(os.path,'lexists') as exists:
            for action in actions:
                with self.assertRaises(native.NativeCapacityRefusal):action()
        complete.assert_not_called();verify.assert_not_called();exists.assert_not_called()

    def test_alias_outputs_count_separately_and_duplicate_destinations_refuse(self):
        stage=self.stage();selected=[row(),dict(row(),path='/opt/alias')]
        result=stage.materialize_tar(archive(),selected)
        self.assertEqual(result['lifetime_regular_bytes'],28);self.assertEqual(result['lifetime_regular_files'],2)
        self.assertEqual((stage.path/'opt/alias').read_bytes(),b'synthetic data')
        other=self.stage()
        with self.assertRaisesRegex(ns.NamespaceProofError,'duplicate_destination'):other.materialize_tar(archive(),[row(),row()])
        self.assertTrue(other.closed);self.assertFalse(other.path.exists())

    def test_materializer_enforces_lifetime_bytes_across_artifacts_and_cleans(self):
        stage=self.stage()
        # Scale only this fixture; production constants are separately asserted.
        with patch.object(native,'_STAGE_MAX_BYTES',41):
            stage.materialize_tar(archive(),[row(),dict(row(),path='/opt/alias')])
            with self.assertRaisesRegex(ns.NamespaceProofError,'capacity'):
                stage.materialize_tar(archive(),[dict(row(),path='/opt/next'),dict(row(),path='/opt/next-alias')])
        self.assertTrue(stage.closed);self.assertFalse(stage.path.exists())
        with self.assertRaises(OSError):os.fstat(stage.fd)

    def test_materializer_enforces_lifetime_entries_across_artifacts(self):
        stage=self.stage()
        with patch.object(native,'_STAGE_MAX_FILES',2):
            stage.materialize_tar(archive(),[row(),dict(row(),path='/opt/alias')])
            with self.assertRaisesRegex(ns.NamespaceProofError,'capacity'):
                stage.materialize_tar(archive(),[dict(row(),path='/opt/next')])
        self.assertTrue(stage.closed);self.assertFalse(stage.path.exists())

    def test_streamed_import_rechecks_bytes_and_rejects_partial_consumption(self):
        stage=self.stage();stage.materialize_tar(archive(),[row()])
        with patch.object(native,'inventory',return_value={'rows':[row()]}):
            with stage.verified_stream(row()) as chunks:self.assertEqual(b''.join(chunks),b'synthetic data')
            with self.assertRaisesRegex(ns.NamespaceProofError,'not_fully_consumed'):
                with stage.verified_stream(row()):pass
            (stage.path/'opt/example').chmod(0o644);(stage.path/'opt/example').write_bytes(b'synthetic DATA');(stage.path/'opt/example').chmod(0o444)
            with self.assertRaisesRegex(ns.NamespaceProofError,'content_changed'):
                with stage.verified_stream(row()):self.fail('changed bytes streamed')

    def test_artifact_verification_failure_cleans_empty_materialization(self):
        stage=self.stage()
        with patch.object(native,'verify_artifact_fd',side_effect=ns.NamespaceProofError('artifact_digest_refused')),self.assertRaisesRegex(ns.NamespaceProofError,'artifact_digest'):
            stage.materialize_python_artifact(999)
        self.assertTrue(stage.closed);self.assertFalse(stage.path.exists())

    def test_view_mounts_bind_backing_identity_and_actual_readonly_private_mounts(self):
        from types import SimpleNamespace
        request=Mock(root='/run/buffalo-native-'+'a'*32,initial_mount_namespace=(1,2))
        descriptors=[Mock(number=i,identity=(3,4)) for i in range(10,14)]
        info=SimpleNamespace(st_uid=0,st_mode=0o40555,st_dev=5,st_ino=6)
        with patch.object(os,'stat',return_value=info),patch.object(ns,'_acquire',side_effect=descriptors),patch.object(os,'fstat',return_value=info),patch.object(ns,'_mount_id',side_effect=lambda fd:fd.number),patch.object(os,'fstatvfs',return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            result=native._view_mount_snapshot(request)
        self.assertEqual(result['mount_namespace'],[5,6]);self.assertEqual(set(result['roots']),{'/opt','/usr/share'})
        for fd in descriptors:fd.close.assert_called_once()

    def test_readonly_mount_of_wrong_backing_refuses_and_closes_descriptors(self):
        from types import SimpleNamespace
        request=Mock(root='/run/buffalo-native-'+'a'*32,initial_mount_namespace=(1,2))
        descriptors=[Mock(number=10,identity=(3,4)),Mock(number=11,identity=(3,5))]
        info=SimpleNamespace(st_uid=0,st_mode=0o40555,st_dev=5,st_ino=6)
        with patch.object(os,'stat',return_value=info),patch.object(ns,'_acquire',side_effect=descriptors),patch.object(os,'fstat',return_value=info),self.assertRaisesRegex(ns.NamespaceProofError,'backing_mismatch'):
            native._view_mount_snapshot(request)
        for fd in descriptors:fd.close.assert_called_once()

    def test_original_mount_namespace_cannot_claim_protected_view(self):
        from types import SimpleNamespace
        request=Mock(initial_mount_namespace=(1,2))
        with patch.object(os,'stat',return_value=SimpleNamespace(st_dev=1,st_ino=2)),patch.object(ns,'_acquire') as acquire,self.assertRaisesRegex(ns.NamespaceProofError,'not_private'):
            native._view_mount_snapshot(request)
        acquire.assert_not_called()

    def test_fixed_inventory_preserves_missing_dependency_gate(self):
        value=native.inventory()
        self.assertEqual(value['known_file_count'],709)
        self.assertEqual(value['known_regular_bytes'],75947095)
        self.assertEqual(len(value['required_unresolved_external_sonames']),29)
        with self.assertRaisesRegex(ns.NamespaceProofError,'inventory_incomplete'):native.require_complete_inventory()

    def test_relabeling_partial_inventory_is_not_completion(self):
        value=native.inventory();value['status']='COMPLETE_VERIFIED';value['complete_runtime_manifest']=True
        with patch.object(native,'inventory',return_value=value),self.assertRaisesRegex(ns.NamespaceProofError,'inventory_incomplete'):
            native.require_complete_inventory()

    def test_modified_pin_bytes_refuse_before_any_effect(self):
        with patch.object(Path,'read_bytes',return_value=b'{}'),self.assertRaisesRegex(ns.NamespaceProofError,'manifest_identity'):
            native.inventory()

    def test_selected_runpath_policy_is_path_hash_and_byte_bound(self):
        data=elf(needed=native.RUNPATH.encode(),tag=29)
        selected={'path':native.PYTHON,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                  'runpath':native.RUNPATH,'rpath':None}
        with patch.object(native,'_pinned',return_value={'objects':[selected]}):
            result=native.parse_selected_elf(native.PYTHON,data)
            self.assertEqual(result['interpreter'],'/lib64/ld-linux-x86-64.so.2')
            for path,raw in [(native.PYTHON+'other',data),(native.PYTHON,data+b'x')]:
                with self.subTest(path=path),self.assertRaises(ns.NamespaceProofError):native.parse_selected_elf(path,raw)
        with self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(data)

    def test_boolean_or_forged_loader_permission_cannot_relax_default(self):
        data=elf(needed=native.RUNPATH.encode(),tag=29)
        for permission in (True,{},native.ElfPermission(native.PYTHON,'a'*64,native.RUNPATH,object())):
            with self.subTest(permission=type(permission)),self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(data,native_permission=permission)

    def test_relative_empty_and_extra_runpath_are_never_selected(self):
        for path in ('','relative/lib',native.RUNPATH+':/tmp',native.RUNPATH+':'):
            raw=elf(needed=path.encode(),tag=29)
            with self.subTest(path=path),self.assertRaises(ns.NamespaceProofError):native.parse_selected_elf(native.PYTHON,raw)

    def test_unprivileged_materialization_and_exact_cleanup(self):
        stage=self.stage();result=stage.materialize_tar(archive(),[row()])
        self.assertFalse(result['execution_authority']);self.assertEqual((stage.path/'opt/example').read_bytes(),b'synthetic data')
        self.assertEqual((stage.path/'opt/example').stat().st_mode&0o777,0o444)
        if not stage.closed:stage.close()
        self.assertFalse(stage.path.exists())

    def test_selected_empty_regular_file_keeps_its_authenticated_digest(self):
        stage=self.stage();selected=dict(row(),bytes=0,sha256=hashlib.sha256(b'').hexdigest())
        result=stage.materialize_tar(archive(content=b''),[selected])
        self.assertEqual(result['regular_bytes'],0);self.assertEqual((stage.path/'opt/example').read_bytes(),b'')

    def test_path_escape_symlink_and_missing_member_refuse(self):
        for payload in (archive('../escape'),archive('/absolute'),archive(link=True),archive('missing')):
            stage=self.stage()
            with self.assertRaises(ns.NamespaceProofError):stage.materialize_tar(payload,[row()])
            if not stage.closed:stage.close()

    def test_wrong_content_or_size_refuses_before_file_write(self):
        for payload in (archive(content=b'synthetic DATA'),archive(content=b'short')):
            stage=self.stage()
            with self.assertRaises(ns.NamespaceProofError):stage.materialize_tar(payload,[row()])
            self.assertEqual(stage.owned,[]);self.assertTrue(stage.closed)

    def test_archive_duplicate_refuses_and_owned_partial_output_is_cleaned(self):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w') as tar:
            for _ in range(2):
                item=tarfile.TarInfo('bin/example');item.size=14;tar.addfile(item,io.BytesIO(b'synthetic data'))
        output.seek(0);stage=self.stage()
        with self.assertRaisesRegex(ns.NamespaceProofError,'duplicate'):stage.materialize_tar(output,[row()])
        if not stage.closed:stage.close()
        self.assertFalse(stage.path.exists())

    def test_unknown_or_replaced_stage_objects_never_removed(self):
        stage=self.stage();(stage.path/'foreign').write_bytes(b'keep')
        with self.assertRaises(ns.NamespaceCleanupError):stage.close()
        self.assertEqual((stage.path/'foreign').read_bytes(),b'keep')

    def test_stage_cleanup_closes_owned_descriptor_when_named_root_changes(self):
        stage=self.stage();fd=stage.fd;other=stage.path.with_name('retained-owned')
        stage.path.rename(other);stage.path.mkdir()
        with self.assertRaises(ns.NamespaceCleanupError):stage.close()
        with self.assertRaises(OSError):os.fstat(fd)
        self.assertTrue(other.exists() and stage.path.exists())

    def test_materializer_refuses_root_and_existing_destination(self):
        with patch.object(os,'geteuid',return_value=0),self.assertRaises(ns.NamespaceProofError):native.Stage('/not-created')
        stage=self.stage()
        with self.assertRaises(FileExistsError):native.Stage(stage.path)

    def test_untrusted_rows_cannot_change_modes_or_escape(self):
        for changes in ({'mode':'0777'},{'bytes':True},{'path':'/opt/../foreign'}):
            stage=self.stage()
            with self.subTest(changes=changes),self.assertRaises(ns.NamespaceProofError):stage.materialize_tar(archive(),[dict(row(),**changes)])
            if not stage.closed:stage.close()

    def test_incomplete_inventory_blocks_plan_before_destination_or_privilege(self):
        stage=self.stage()
        with patch.object(native,'require_native_capacity'),patch.object(os.path,'lexists') as exists,self.assertRaisesRegex(ns.NamespaceProofError,'inventory_incomplete'):
            native.native_install_plan('a'*32,stage)
        exists.assert_not_called()

    def test_native_plan_never_reads_operator_paths_as_root_or_contains_python(self):
        stage=self.stage();stage.materialize_tar(archive(),[row()])
        with patch.object(native,'require_native_capacity'),patch.object(native,'require_complete_inventory',return_value={'rows':[row()]}),patch.object(os.path,'lexists',return_value=False):
            plan=native.native_install_plan('a'*32,stage)
        self.assertFalse(plan['installation_authority'])
        for command in plan['commands']:
            self.assertNotIn(str(stage.path),str(command['argv']))
            self.assertNotIn('python',str(command['argv']));self.assertNotIn('/bin/sh',command['argv'])
        writes=[c for c in plan['commands'] if c['argv'][0]=='/usr/bin/dd']
        self.assertEqual(writes[0]['stdin'],'VERIFIED_UNPRIVILEGED_STAGE_STREAM')
        self.assertIn('oflag=excl,nofollow',writes[0]['argv'])

    def test_installation_gate_has_no_pass_flag(self):
        for args in ({},{'approved':True},{'review':'PASS'}):
            with self.assertRaisesRegex(ns.NamespaceProofError,'not_authorized'):native.activate_installation(**args)

    def test_view_cannot_be_minted_from_partial_manifest(self):
        with patch.object(native,'require_native_capacity'),self.assertRaisesRegex(ns.NamespaceProofError,'inventory_incomplete'):
            native.view_request(Mock(commit='a'*40,tree='b'*40),'/run/buffalo-native-'+'a'*32,deadline=10**12)

    def test_changed_view_source_manifest_or_nonce_refuses(self):
        binding=Mock(commit='a'*40,tree='b'*40)
        request=native.ViewRequest('a'*64,'a'*40,'b'*40,native.DIGESTS[0],'/run/buffalo-native-'+'a'*32,(1,2),'c'*64,10**12,native._TOKEN)
        for changed in (replace(request,tree='f'*40),replace(request,manifest='f'*64),replace(request,nonce='bad'),replace(request,root='/foreign'),replace(request,deadline=0)):
            with self.subTest(changed=changed),patch.object(os,'lstat') as observed,self.assertRaises(ns.NamespaceProofError):native._verify_request(changed,binding)
            observed.assert_not_called()

    def test_caller_json_or_boolean_cannot_bind_measurement(self):
        with patch.object(native,'require_native_capacity'),patch.object(native,'_verify_request'),patch.object(native,'active_view',return_value=object()):
            for report in (True,{}, {'capacity':'FIT','memory':'PASS'}):
                with self.assertRaisesRegex(ns.NamespaceProofError,'measurement_view'):native.bind_measurement_result(object(),object(),report)

    def test_active_view_rejects_host_fallback_or_modified_bytes(self):
        request=Mock(deadline=10**12);files={row()['path']:row()}
        token=native._ACTIVE_VIEW.set(request);ft=native._ACTIVE_FILES.set(files)
        try:
            native.require_selected_file('/opt/example',b'synthetic data',0o444)
            for path,data,mode in [('/usr/lib/foreign',b'synthetic data',0o444),('/opt/example',b'changed',0o444),('/opt/example',b'synthetic data',0o555)]:
                with self.assertRaises(ns.NamespaceProofError):native.require_selected_file(path,data,mode)
        finally:native._ACTIVE_FILES.reset(ft);native._ACTIVE_VIEW.reset(token)

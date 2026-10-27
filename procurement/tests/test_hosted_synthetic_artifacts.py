"""Actual disposable file/ZIP framing checks, never workflow packet evidence."""
import ast
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import stat
import signal
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import hosted_synthetic_artifacts as a

ROOT=Path(__file__).resolve().parents[2]
RUN='00000000-0000-4000-8000-000000000001'
GUID='00000000-0000-4000-8000-000000000002'
VENDORS=('00000000-0000-4000-8000-000000000003','00000000-0000-4000-8000-000000000004')


def source_builders():
    module=ast.parse((ROOT/'procurement/src/procurement_os/emergency_packet.py').read_text())
    functions=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in ('_zip_bytes','_json_entry','_json_default')]
    namespace={'Any':object,'date':date,'datetime':datetime,'Decimal':Decimal,'io':io,'zipfile':zipfile,'json':json}
    exec(compile(ast.Module(body=functions,type_ignores=[]),'actual-packet-byte-builders','exec'),namespace)
    return namespace


def registry(data,kind='EMERGENCY_REVIEW_PACKET'):
    return a.RegisteredArtifact(RUN,'42',kind,None if kind=='EMERGENCY_REVIEW_PACKET' else VENDORS[0],len(data),hashlib.sha256(data).hexdigest())


def captured(data):return a.CapturedArtifact(registry(data),GUID,data)


class HostedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='buffalo-artifact-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'downloads';self.root.mkdir(mode=0o700)
        self.path=self.root/GUID;self.data=b'synthetic-only-packet-ingress';self.path.write_bytes(self.data);self.path.chmod(0o600)
        self.expect=registry(self.data);self.uid=os.geteuid();self.gid=os.getegid()
        self.builders=source_builders()

    def read(self,**kw):return a.read_owned_download(str(self.root),GUID,kw.pop('registry',self.expect),uid=kw.pop('uid',self.uid),gid=kw.pop('gid',self.gid),**kw)

    def entries(self):
        # Deliberately NOT a business packet. Real source builders exercise the
        # framing format; economics/forecast/workflow semantics remain separate.
        entries={name:b'{"component_fixture":true}\n' for name in a._MEMBERS-{'manifest.json'}}
        entries.update({f'vendor-{v}.internal.csv':b'component_fixture\n' for v in VENDORS})
        entries['manifest.json']=self.builders['_json_entry']({'safety_label':a.SAFETY_LABEL,'entries':{n:hashlib.sha256(v).hexdigest() for n,v in sorted(entries.items())}})
        return entries

    def packet(self,entries=None):return self.builders['_zip_bytes'](self.entries() if entries is None else entries)

    def assert_refused_packet(self,raw):
        with self.assertRaises(a.HostedArtifactError):a.packet_members(captured(raw),VENDORS)

    def test_actual_held_read_matches_independent_registry_and_closes_fds(self):
        before=len(os.listdir('/proc/self/fd'));result=self.read()
        self.assertEqual(result.data,self.data);self.assertEqual(len(os.listdir('/proc/self/fd')),before)
        self.assertNotIn(self.data.decode(),repr(result))

    def test_captured_bytes_do_not_change_when_original_changes_later(self):
        result=self.read();self.path.write_bytes(b'new mutable bytes');self.assertEqual(result.data,self.data);result.validate()

    def test_unsafe_basename_relative_or_traversing_root_refuse_before_open(self):
        for root,guid in [('relative',GUID),(str(self.root)+'/../downloads',GUID),(str(self.root)+'\0',GUID),('/'+str(self.root),GUID),(str(self.root),'../foreign'),(str(self.root),GUID+'/x')]:
            with self.subTest(root=root,guid=guid),mock.patch.object(a.os,'open',side_effect=AssertionError('must not open')):
                with self.assertRaises(a.HostedArtifactError):a.read_owned_download(root,guid,self.expect,uid=self.uid,gid=self.gid)

    def test_leaf_and_ancestor_symlinks_refuse_without_read(self):
        foreign=Path(self.temp.name)/'foreign';foreign.write_bytes(self.data);self.path.unlink();self.path.symlink_to(foreign)
        with self.assertRaises(a.HostedArtifactError):self.read()
        self.path.unlink();self.path.write_bytes(self.data);link=Path(self.temp.name)/'alias';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(a.HostedArtifactError):a.read_owned_download(str(link),GUID,self.expect,uid=self.uid,gid=self.gid)
        self.assertEqual(foreign.read_bytes(),self.data)

    def test_hardlink_fifo_and_directory_refuse(self):
        os.link(self.path,Path(self.temp.name)/'other-link')
        with self.assertRaises(a.HostedArtifactError):self.read()
        self.path.unlink();os.mkfifo(self.path)
        with self.assertRaises(a.HostedArtifactError):self.read()
        self.path.unlink();self.path.mkdir()
        with self.assertRaises(a.HostedArtifactError):self.read()

    def test_exact_directory_owner_group_and_private_mode(self):
        for changes in [{'uid':self.uid+1},{'gid':self.gid+1}]:
            with self.assertRaises(a.HostedArtifactError):self.read(**changes)
        self.root.chmod(0o750)
        with self.assertRaises(a.HostedArtifactError):self.read()

    def test_group_writable_or_executable_file_refuses(self):
        for mode in (0o660,0o700,0o664,0o4600):
            self.path.chmod(mode)
            with self.subTest(mode=mode),self.assertRaises(a.HostedArtifactError):self.read()
        self.path.chmod(0o644);self.assertEqual(self.read().data,self.data)

    def test_registry_hash_size_type_and_parser_bound_are_required(self):
        for expected in [replace(self.expect,sha256='0'*64),replace(self.expect,size_bytes=len(self.data)-1),replace(self.expect,size_bytes=True),replace(self.expect,size_bytes=a.MAX_ARTIFACT_BYTES+1),replace(self.expect,vendor_id=VENDORS[0])]:
            with self.subTest(expected=expected),self.assertRaises(a.HostedArtifactError):self.read(registry=expected)

    def test_rename_swap_after_read_refuses_and_leaves_foreign_file(self):
        original=os.pread;fired=False
        def read(fd,size,offset):
            nonlocal fired
            data=original(fd,size,offset)
            if not fired:
                fired=True;self.path.rename(self.root/'original');self.path.write_bytes(b'foreign')
            return data
        before=len(os.listdir('/proc/self/fd'))
        with mock.patch.object(a.os,'pread',side_effect=read),self.assertRaises(a.HostedArtifactError):self.read()
        self.assertEqual(self.path.read_bytes(),b'foreign');self.assertEqual((self.root/'original').read_bytes(),self.data)
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_file_mutation_and_interruption_refuse_and_close_fds(self):
        original=os.pread;fired=False
        def read(fd,size,offset):
            nonlocal fired
            value=original(fd,size,offset)
            if not fired:fired=True;self.path.write_bytes(self.data+b'changed')
            return value
        before=len(os.listdir('/proc/self/fd'))
        with mock.patch.object(a.os,'pread',side_effect=read),self.assertRaises(a.HostedArtifactError):self.read()
        self.path.write_bytes(self.data)
        with mock.patch.object(a.os,'pread',side_effect=InterruptedError),self.assertRaises(a.HostedArtifactError):self.read()
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_directory_replacement_during_read_refuses(self):
        original=os.pread;fired=False
        def read(fd,size,offset):
            nonlocal fired
            data=original(fd,size,offset)
            if not fired:
                fired=True;self.root.rename(Path(self.temp.name)/'old');self.root.mkdir(mode=0o700);self.path.write_bytes(self.data)
            return data
        with mock.patch.object(a.os,'pread',side_effect=read),self.assertRaises(a.HostedArtifactError):self.read()

    def signal_boundary(self,function,line,occurrence=0):
        lines,start=inspect.getsourcelines(function)
        target=start+[i for i,value in enumerate(lines) if value.strip()==line][occurrence]
        fired=False;previous=sys.gettrace();before=len(os.listdir('/proc/self/fd'))
        def trace(frame,event,arg):
            nonlocal fired
            if not fired and event=='line' and frame.f_code==function.__code__ and frame.f_lineno==target:
                fired=True;os.kill(os.getpid(),signal.SIGINT)
            return trace
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):self.read()
        finally:sys.settrace(previous)
        self.assertTrue(fired);self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_real_sigint_at_ancestor_acquisition_is_deferred_until_fd_closure(self):
        self.signal_boundary(a._held_source_file.__wrapped__,'descriptors.append(parent)')

    def test_real_sigint_at_leaf_acquisition_is_deferred_until_fd_closure(self):
        self.signal_boundary(a.read_owned_download.__wrapped__,'try:',1)

    def test_invalid_signal_boundary_refuses_before_open(self):
        old=signal.signal(signal.SIGCHLD,lambda *_:None)
        try:
            with mock.patch.object(a.os,'open',side_effect=AssertionError('must not open')):
                with self.assertRaises(a.HostedSourceError):self.read()
        finally:signal.signal(signal.SIGCHLD,old)

    def test_real_source_zip_builder_preserves_exact_fourteen_original_members(self):
        entries=self.entries();members=a.packet_members(captured(self.packet(entries)),VENDORS)
        self.assertEqual(dict(members),entries);self.assertEqual(len(members),14)

    def test_registry_different_vendor_kind_or_payload_hash_refuses(self):
        packet=captured(self.packet())
        for bad in [replace(packet,data=packet.data+b'!'),replace(packet,registry=replace(packet.registry,sha256='0'*64)),replace(packet,registry=replace(packet.registry,kind='VENDOR_INTERNAL_CSV',vendor_id=VENDORS[0]))]:
            with self.assertRaises(a.HostedArtifactError):a.packet_members(bad,VENDORS)
        with self.assertRaises(a.HostedArtifactError):a.packet_members(packet,(VENDORS[0],RUN))

    def test_missing_extra_renamed_and_wrong_vendor_members_refuse(self):
        for change in ('missing','extra','traversal','other_vendor'):
            entries=self.entries()
            if change=='missing':entries.pop('packet-summary.json')
            elif change=='extra':entries['unreviewed.json']=b'{}'
            elif change=='traversal':entries['../packet-summary.json']=entries.pop('packet-summary.json')
            else:entries[f'vendor-{RUN}.internal.csv']=entries.pop(f'vendor-{VENDORS[0]}.internal.csv')
            self.assert_refused_packet(self.packet(entries))

    def test_duplicate_zip_member_and_zip_comment_refuse(self):
        output=io.BytesIO(self.packet())
        with zipfile.ZipFile(output,'a') as archive:archive.comment=b'not permitted'
        self.assert_refused_packet(output.getvalue())
        output=io.BytesIO(self.packet())
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(output,'a') as archive:archive.writestr('packet-summary.json',b'{}')
        self.assert_refused_packet(output.getvalue())

    def test_prefix_suffix_truncation_and_damaged_compressed_payload_refuse(self):
        raw=self.packet()
        for data in [b'prefix'+raw,raw+b'suffix',raw[:-1],raw[:100]]:self.assert_refused_packet(data)
        unsupported=bytearray(raw);central=struct.unpack('<4s4H2LH',raw[-22:])[6];struct.pack_into('<H',unsupported,central+6,1000);self.assert_refused_packet(bytes(unsupported))
        damaged=bytearray(raw);name_length=struct.unpack_from('<H',raw,26)[0];damaged[30+name_length]^=0xff;self.assert_refused_packet(bytes(damaged))

    def test_symlink_executable_stored_or_extra_field_members_refuse(self):
        for mode in ('symlink','executable','stored','extra'):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as archive:
                for index,(name,data) in enumerate(sorted(self.entries().items())):
                    info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
                    if index==0:
                        if mode=='symlink':info.external_attr=(stat.S_IFLNK|0o777)<<16
                        elif mode=='executable':info.external_attr=0o755<<16
                        elif mode=='stored':info.compress_type=zipfile.ZIP_STORED
                        else:info.extra=b'\x01\x00\x00\x00'
                    archive.writestr(info,data)
            self.assert_refused_packet(out.getvalue())

    def test_expansion_limits_apply_before_member_materialization(self):
        entries=self.entries();entries['packet-summary.json']=b'A'*(a.MAX_ARTIFACT_BYTES+1)
        self.assert_refused_packet(self.packet(entries))
        entries=self.entries()
        for name in sorted(entries)[:5]:entries[name]=b'A'*a.MAX_ARTIFACT_BYTES
        self.assert_refused_packet(self.packet(entries))

    def test_manifest_exact_inventory_digest_and_safety_label(self):
        for change in ('hash','missing','extra','safety'):
            entries=self.entries();manifest=json.loads(entries['manifest.json'])
            if change=='hash':manifest['entries']['packet-summary.json']='0'*64
            elif change=='missing':manifest['entries'].pop('packet-summary.json')
            elif change=='extra':manifest['entries']['extra.json']='0'*64
            else:manifest['safety_label']='ordering approved'
            entries['manifest.json']=json.dumps(manifest).encode();self.assert_refused_packet(self.packet(entries))

    def test_json_duplicate_keys_invalid_utf8_nonfinite_float_and_depth_refuse(self):
        for raw in [b'{"x":1,"x":2}',b'\xff',b'{"x":NaN}',b'{"x":1.5}',b'['*34+b'0'+b']'*34]:
            with self.subTest(raw=raw[:30]),self.assertRaises(a.HostedArtifactError):a.bounded_json(raw)
        self.assertEqual(a.bounded_json(b'{"x":"289.00","safe":true}'),{'x':'289.00','safe':True})

    def test_jsonb_numeric_mapping_fields_have_narrow_source_path_policy(self):
        for record,tail in [('mapping_candidate',['shopify_units_value']),('mapping_candidate',['canonical_payload','physical_units_value']),
                            ('mapping_decision',['qualifying_units_value']),('mapping_decision',['reviewed_facts','physical_units_value']),
                            ('mapping_decision',['canonical_payload','reviewed_facts','retail_pack_units_value'])]:
            data=4.0
            for key in reversed(tail):data={key:data}
            selected={'selected_offer_input_evidence':{record:data}}
            payloads={'frozen-input-manifest.json':{'input_manifest':{'contexts':[selected]}},
                      'recommendations-and-reasons.json':{'items':[{'metrics':selected}]},'supplier-mapping-evidence.json':{'items':[selected]}}
            for member,payload in payloads.items():
                raw=json.dumps(payload).encode();self.assertEqual(a.packet_json(member,raw),payload)
                with self.assertRaises(a.HostedArtifactError):a.bounded_json(raw)

    def test_mapping_float_admission_does_not_extend_to_money_ids_or_other_members(self):
        for key in ('price_id','offer_id','case_price','unit_price','head_version'):
            payload={'items':[{'selected_offer_input_evidence':{'mapping_candidate':{key:4.0}}}]}
            with self.assertRaises(a.HostedArtifactError):a.packet_json('supplier-mapping-evidence.json',json.dumps(payload).encode())
        raw=b'{"items":[{"selected_offer_input_evidence":{"mapping_candidate":{"shopify_units_value":4.0}}}]}'
        with self.assertRaises(a.HostedArtifactError):a.packet_json('frozen-price-economics.json',raw)
        with self.assertRaises(a.HostedArtifactError):a.packet_json('supplier-mapping-evidence.json',b'{"shopify_units_value":4.0}')

    def test_mapping_numeric_metadata_is_finite_positive_numeric12_4_and_bounded(self):
        for value in (0.0,-1.0,100000000.0,4.00001,float('inf'),float('nan')):
            raw=json.dumps({'items':[{'selected_offer_input_evidence':{'mapping_candidate':{'shopify_units_value':value}}}]}).encode()
            with self.subTest(value=value),self.assertRaises(a.HostedArtifactError):a.packet_json('supplier-mapping-evidence.json',raw)

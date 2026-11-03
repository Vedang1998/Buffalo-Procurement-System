"""Pinned metadata chain; disposable synthetic bytes, no network/sudo/install."""
import copy
import hashlib
import json
import lzma
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hosted_synthetic_native_route as route
import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_installation as install


class NativeProvenanceTests(unittest.TestCase):
    def fixture(self,records=None):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()));data=b'public fixture artifact';sha=hashlib.sha256(data).hexdigest()
        raw=(f'Package: fixture\nVersion: 1.0-1\nArchitecture: amd64\nFilename: pool/main/f/fixture/fixture_1.0-1_amd64.deb\nSize: {len(data)}\nSHA256: {sha}\nDescription: fixture\n continued\n').encode()
        artifact={'package':'fixture','version':'1.0-1','architecture':'amd64','url':'https://archive.ubuntu.com/ubuntu/pool/main/f/fixture/fixture_1.0-1_amd64.deb','artifact_sha256':sha,'artifact_bytes':len(data),'package_record_sha256':hashlib.sha256(raw).hexdigest()}
        plain=(raw+b'\n') if records is None else records(raw)
        compressed=lzma.compress(plain);digest=hashlib.sha256(compressed).hexdigest()
        index={'sha256':digest,'bytes':len(compressed),'uncompressed_bytes':len(plain),'uncompressed_sha256':hashlib.sha256(plain).hexdigest(),'component':'main'}
        artifact['index_sha256']=digest;path=root/(digest+'.Packages.xz');path.write_bytes(compressed);(root/sha).write_bytes(data)
        return root,index,artifact,raw,path

    def test_exact_index_record_and_complete_artifact_bytes_match(self):
        root,index,a,_,_=self.fixture();before=set(os.listdir('/proc/self/fd'))
        result=route.verify_index_records(root,index,[a],time.monotonic()+60)
        self.assertEqual(result['matched_artifacts'],[a['artifact_sha256']]);self.assertEqual(result['parsed_records'],1)
        fd=route._artifact_fd(root/a['artifact_sha256'],a['artifact_sha256'],a['artifact_bytes'],time.monotonic()+60);os.close(fd)
        self.assertEqual(set(os.listdir('/proc/self/fd')),before)

    def test_missing_index_is_named_refusal(self):
        root,index,a,_,path=self.fixture();path.unlink()
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'pinned_index_missing'):
            route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_truncated_or_corrupt_compressed_index_refuses_before_parse(self):
        for truncate in (True,False):
            root,index,a,_,path=self.fixture();raw=path.read_bytes();path.write_bytes(raw[:-1] if truncate else bytes([raw[0]^1])+raw[1:])
            with self.subTest(truncate=truncate),patch.object(route,'_index_records') as parse,self.assertRaises(native.ns.NamespaceProofError):
                route.verify_index_records(root,index,[a],time.monotonic()+60)
            parse.assert_not_called()

    def test_wrong_package_version_or_architecture_cannot_match_record(self):
        for key,value in (('package','other'),('version','2.0-1'),('architecture','all')):
            root,index,a,_,_=self.fixture();a[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(native.ns.NamespaceProofError,'exact_package_record_missing'):
                route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_wrong_filename_hash_size_or_record_digest_refuses(self):
        for key,value in (('url','https://archive.ubuntu.com/ubuntu/pool/foreign.deb'),('artifact_sha256','f'*64),('artifact_bytes',100),('package_record_sha256','0'*64)):
            root,index,a,_,_=self.fixture();a[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(native.ns.NamespaceProofError,'record_binding_refused'):
                route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_duplicate_and_conflicting_selected_records_refuse(self):
        for conflict in (False,True):
            root,index,a,_,_=self.fixture(lambda raw:raw+b'\n'+(raw.replace(b'Size: ',b'Size: 9') if conflict else raw)+b'\n')
            with self.subTest(conflict=conflict),self.assertRaisesRegex(native.ns.NamespaceProofError,'duplicate_package_record'):
                route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_duplicate_fields_and_identity_continuation_refuse(self):
        for mutate in (lambda raw:raw+b'SHA256: '+'f'.encode()*64+b'\n',lambda raw:raw.replace(b'Version: 1.0-1\n',b'Version: 1.0-1\n hidden\n')):
            root,index,a,_,_=self.fixture(lambda raw:mutate(raw)+b'\n')
            with self.assertRaises(native.ns.NamespaceProofError):route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_decompression_byte_and_record_bounds_remain_fail_closed(self):
        root,index,a,_,_=self.fixture();index['uncompressed_bytes']-=1
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'expansion_refused'):route.verify_index_records(root,index,[a],time.monotonic()+60)
        root,index,a,_,_=self.fixture(lambda raw:raw+b' '+b'x'*(256*1024)+b'\n\n')
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'record_bound'):route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_publisher_empty_second_xz_stream_is_bounded_not_ignored_junk(self):
        root,index,a,_,path=self.fixture();raw=path.read_bytes()+lzma.compress(b'')
        path.unlink();index['bytes']=len(raw);index['sha256']=hashlib.sha256(raw).hexdigest();a['index_sha256']=index['sha256'];path=root/(index['sha256']+'.Packages.xz');path.write_bytes(raw)
        self.assertEqual(route.verify_index_records(root,index,[a],time.monotonic()+60)['parsed_records'],1)
        for tail in (b'junk',lzma.compress(b'')*4):
            bad=raw+tail;old=path;index['bytes']=len(bad);index['sha256']=hashlib.sha256(bad).hexdigest();a['index_sha256']=index['sha256'];path=root/(index['sha256']+'.Packages.xz');path.write_bytes(bad)
            with self.assertRaises(native.ns.NamespaceProofError):route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_signed_release_index_binding_requires_exact_unique_size_hash_path(self):
        _,index,_,_,_=self.fixture();line=f" {index['sha256']} {index['bytes']} main/binary-amd64/Packages.xz\n".encode()
        raw=b'Origin: Ubuntu\nSHA256:\n'+line+b'SHA512:\n';route._release_index_binding(raw,index)
        for bad in (raw.replace(str(index['bytes']).encode(),b'1'),raw.replace(index['sha256'].encode(),b'f'*64),raw.replace(line,line*2),raw.replace(b'main/',b'universe/')):
            with self.assertRaises(native.ns.NamespaceProofError):route._release_index_binding(bad,index)

    def test_artifact_drift_after_valid_record_still_refuses(self):
        root,index,a,_,_=self.fixture();route.verify_index_records(root,index,[a],time.monotonic()+60)
        path=root/a['artifact_sha256'];data=path.read_bytes();path.write_bytes(b'x'+data[1:])
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'artifact_digest'):
            route._artifact_fd(path,a['artifact_sha256'],a['artifact_bytes'],time.monotonic()+60)

    def test_index_change_after_parse_cannot_return_chain_result(self):
        root,index,a,_,path=self.fixture();original=route._index_records
        def drift(*args):
            yield from original(*args)
            data=path.read_bytes();path.write_bytes(b'x'+data[1:])
        with patch.object(route,'_index_records',side_effect=drift),self.assertRaises(native.ns.NamespaceProofError):
            route.verify_index_records(root,index,[a],time.monotonic()+60)

    def test_provenance_failure_in_connected_route_precedes_installation(self):
        from test_hosted_synthetic_native_route import NativeRouteTests,ARGS
        fixture=NativeRouteTests();self.addCleanup(fixture.doCleanups);parent,_,_=fixture.fixture()
        with patch.object(route,'verify_publisher_metadata',side_effect=native.ns.NamespaceProofError('native_pinned_index_missing')),patch.object(install.InstallationLease,'_create') as create,self.assertRaisesRegex(native.ns.NamespaceProofError,'pinned_index_missing'):
            route._run(ARGS,parent)
        create.assert_not_called();fixture.create.assert_not_called()

    def verified_result(self,payload,*,code=0,status=None):
        signer='F6ECB3762474EDA9D21B7022871920D1991BC93C'
        if status is None:status=b'gpgv: Good signature\n[GNUPG:] VALIDSIG '+signer.encode()+b' fixture\n'
        return SimpleNamespace(returncode=code,stdout=payload,stderr=status),signer,hashlib.sha256(payload).hexdigest()

    def test_verifier_emitted_signed_payload_has_exact_existing_identity(self):
        root,index,_,_,_=self.fixture()
        payload=(f"Origin: Ubuntu\nSHA256:\n {index['sha256']} {index['bytes']} main/binary-amd64/Packages.xz\n").encode()
        result,signer,digest=self.verified_result(payload)
        actual=route._verified_release_payload(result,signer,digest)
        self.assertIs(actual,payload);route._release_index_binding(actual,index)

    def test_wrong_empty_oversized_or_changed_emitted_payload_refuses(self):
        result,signer,digest=self.verified_result(b'Origin: Ubuntu\n')
        for payload in (b'',b'x'*(2*1024**2+1),b'Origin: Changed\n',None,'text'):
            with self.subTest(size=None if payload is None else len(payload)),self.assertRaises(native.ns.NamespaceProofError):
                route._verified_release_payload(SimpleNamespace(returncode=0,stdout=payload,stderr=result.stderr),signer,digest)

    def test_wrong_duplicate_missing_or_contradictory_signature_refuses(self):
        result,signer,digest=self.verified_result(b'payload')
        for status in (b'',result.stderr.replace(signer.encode(),b'0'*40),result.stderr*2,
                       result.stderr+b'[GNUPG:] BADSIG other\n',result.stderr+b'[GNUPG:] FAILURE verify 1\n',b'x'*4097):
            with self.subTest(status=status[:50]),self.assertRaises(native.ns.NamespaceProofError):
                route._verified_release_payload(SimpleNamespace(returncode=0,stdout=result.stdout,stderr=status),signer,digest)
        result.returncode=1
        with self.assertRaises(native.ns.NamespaceProofError):route._verified_release_payload(result,signer,digest)

    def test_unsigned_outer_index_text_is_never_parsed_as_signed_content(self):
        _,index,_,_,_=self.fixture();payload=b'Origin: Ubuntu\nSHA256:\n'
        outer=payload+f" {index['sha256']} {index['bytes']} main/binary-amd64/Packages.xz\n".encode()
        route._release_index_binding(outer,index)  # Apparent binding outside emitted text.
        result,signer,digest=self.verified_result(payload)
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'index_binding'):
            route._release_index_binding(route._verified_release_payload(result,signer,digest),index)

    def metadata_fixture(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()));key=b'public fixture key';(root/'ubuntu-archive-keyring.gpg').write_bytes(key)
        manifest=native.installation_manifest();manifest['trust_root']=dict(manifest['trust_root'],bytes=len(key),sha256=hashlib.sha256(key).hexdigest())
        self.enterContext(patch.object(native,'installation_manifest',return_value=manifest))
        self.enterContext(patch('hosted_synthetic_runtime.read_native',return_value=(b'fixture tool',0o555)))
        inventory=native.require_complete_inventory();payloads={}
        for suite in ('noble','noble-updates'):
            records=[a for a in inventory['artifacts'].values() if a.get('suite')==suite]
            index=records[0]['packages_index']
            payload=(f"Origin: fixture\nSHA256:\n {index['sha256']} {index['bytes']} main/binary-amd64/Packages.xz\n").encode()
            payloads[records[0]['signed_release']['signed_sha256']]=payload
            for a in records:a['signed_release']['verified_release_sha256']=hashlib.sha256(payload).hexdigest()
        self.enterContext(patch.object(native,'require_complete_inventory',return_value=inventory))
        def result(argv,**kwargs):
            payload=payloads[hashlib.sha256(Path(argv[-1]).read_bytes()).hexdigest()]
            return self.verified_result(payload)[0]
        command=self.enterContext(patch.object(route.subprocess,'run',side_effect=result))
        return root,command

    def test_raw_signature_digest_still_refuses_before_verifier_or_indexes(self):
        root,command=self.metadata_fixture();inventory=native.require_complete_inventory()
        for a in inventory['artifacts'].values():
            if a.get('suite')=='noble':a['signed_release']['signed_sha256']='0'*64
        with patch.object(native,'require_complete_inventory',return_value=inventory),patch.object(route,'verify_index_records') as index,self.assertRaisesRegex(native.ns.NamespaceProofError,'snapshot_refused'):
            route.verify_publisher_metadata(root,time.monotonic()+60)
        command.assert_not_called();index.assert_not_called()

    def test_payload_selection_mismatch_or_signature_failure_prevents_index_and_installation(self):
        for mismatch in (True,False):
            root,command=self.metadata_fixture();inventory=native.require_complete_inventory()
            if mismatch:
                next(a for a in inventory['artifacts'].values() if a.get('suite')=='noble')['signed_release']['verified_release_sha256']='0'*64
            else:command.side_effect=None;command.return_value=SimpleNamespace(returncode=1,stdout=b'Origin: Ubuntu\n',stderr=b'bad signature')
            before=set(os.listdir('/proc/self/fd'))
            with patch.object(native,'require_complete_inventory',return_value=inventory),patch.object(route,'verify_index_records') as index,patch.object(install.InstallationLease,'_create') as create,self.assertRaises(native.ns.NamespaceProofError):
                route.verify_publisher_metadata(root,time.monotonic()+60)
            index.assert_not_called();create.assert_not_called()
            self.assertEqual(set(os.listdir('/proc/self/fd')),before)
            for call in command.call_args_list:
                for fd in call.kwargs['pass_fds']:
                    with self.assertRaises(OSError):os.fstat(fd)

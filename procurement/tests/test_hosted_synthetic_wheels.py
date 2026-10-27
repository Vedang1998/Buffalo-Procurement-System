"""Causal original-artifact, archive ownership and loader-resolution checks."""
from dataclasses import replace
import base64
import csv
import hashlib
import io
from pathlib import Path
import stat
import struct
import unittest
from unittest.mock import patch
import zipfile

import hosted_synthetic_wheels as wheels
import hosted_synthetic_runtime as runtime
import hosted_synthetic_namespace as ns
from test_hosted_synthetic_runtime import elf

DIST='example-1.0.dist-info'


def archive(*,add=None,remove=(),record_transform=None,metadata=None,wheel=None,mode=None,duplicates=()):
    files={'example/__init__.py':b'value=1\n',DIST+'/METADATA':metadata or b'Metadata-Version: 2.4\nName: example\nVersion: 1.0\n\n',
        DIST+'/WHEEL':wheel or b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n'}
    files.update(add or {})
    for path in remove:files.pop(path,None)
    rows=[]
    for path,data in files.items():
        rows.append([path,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='),str(len(data))])
    rows.append([DIST+'/RECORD','',''])
    if record_transform:rows=record_transform(rows)
    output=io.StringIO(newline='');csv.writer(output).writerows(rows);files[DIST+'/RECORD']=output.getvalue().encode()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path,data in (*files.items(),*duplicates):
            info=zipfile.ZipInfo(path);info.create_system=3
            info.external_attr=(mode or {}).get(path,stat.S_IFREG|0o644)<<16
            info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,data)
    raw=output.getvalue()
    spec=wheels.LockedWheel('example','1.0',DIST,'example-1.0-py3-none-any.whl',hashlib.sha256(raw).hexdigest(),len(raw),('py3-none-any',))
    return spec,raw


class LockedWheelTests(unittest.TestCase):
    def refused(self,**kwargs):
        with self.assertRaises((ns.NamespaceProofError,ns.backend.LocalStagingAcceptanceError)):
            wheels.verify_wheel(*archive(**kwargs))

    def test_original_digest_is_checked_before_zip_parser(self):
        spec,raw=archive()
        with patch.object(wheels.zipfile,'ZipFile') as parse,self.assertRaises(ns.NamespaceProofError):
            wheels.verify_wheel(spec,raw[:-1]+bytes([raw[-1]^1]))
        parse.assert_not_called()

    def test_original_size_must_match(self):
        spec,raw=archive()
        with self.assertRaises(ns.NamespaceProofError):wheels.verify_wheel(replace(spec,size=spec.size+1),raw)

    def test_original_bytes_and_record_are_preserved(self):
        spec,raw=archive();files=wheels.verify_wheel(spec,raw)
        self.assertEqual(files['example/__init__.py'],b'value=1\n')
        self.assertEqual(len(files),4);self.assertTrue(files[DIST+'/RECORD'].endswith(b',,\r\n'))

    def test_archive_modes_never_grant_projected_write_authority(self):
        spec,raw=archive(mode={DIST+'/METADATA':stat.S_IFREG|0o664})
        self.assertIn(DIST+'/METADATA',wheels.verify_wheel(spec,raw))
        for mode in (stat.S_IFLNK|0o777,stat.S_IFCHR|0o600,stat.S_IFREG|0o4755,stat.S_IFREG|0o666):
            with self.subTest(mode=mode):self.refused(mode={'example/__init__.py':mode})

    def test_traversal_absolute_backslash_and_nul_refuse(self):
        for path in ('../escape','/escape','a/../../escape','a\\escape','a//escape','a/./escape','a\x00escape'):
            with self.subTest(path=path):self.refused(add={path:b'x'})

    def test_duplicate_and_case_collision_refuse(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            self.refused(duplicates=(('example/__init__.py',b'duplicate'),))
        self.refused(add={'Example/__init__.py':b'collision'})

    def test_file_cannot_be_ancestor(self):
        self.refused(add={'example':b'file'})

    def test_member_and_expansion_limits_refuse_before_unbounded_read(self):
        spec,raw=archive()
        with patch.object(wheels,'MAX_FILES',2),self.assertRaises(ns.NamespaceProofError):wheels.verify_wheel(spec,raw)
        with patch.object(wheels,'MAX_EXPANDED',4),self.assertRaises(ns.NamespaceProofError):wheels.verify_wheel(spec,raw)

    def test_distribution_identity_and_single_dist_info_are_required(self):
        self.refused(metadata=b'Metadata-Version: 2.4\nName: foreign\nVersion: 1.0\n\n')
        self.refused(add={'other-1.0.dist-info/METADATA':b'foreign'})

    def test_record_requires_exact_member_coverage(self):
        self.refused(record_transform=lambda rows:rows[1:])
        self.refused(record_transform=lambda rows:rows+[rows[0]])
        self.refused(record_transform=lambda rows:rows+[['missing','sha256=x','1']])

    def test_record_never_accepts_installer_external_paths(self):
        self.refused(record_transform=lambda rows:rows+[['../../../bin/uvicorn','sha256=x','1']])

    def test_record_hash_and_size_are_independently_required(self):
        for value in ('sha256='+('A'*43),'md5=x','sha256='):
            with self.subTest(value=value):
                self.refused(record_transform=lambda rows:[rows[0][:1]+[value,rows[0][2]],*rows[1:]])
        for size in ('0','01','-1','1e1'):
            with self.subTest(size=size):
                self.refused(record_transform=lambda rows:[rows[0][:2]+[size],*rows[1:]])

    def test_record_self_entry_is_only_blank_hash(self):
        self.refused(record_transform=lambda rows:[['example/__init__.py','',''],*rows[1:]])
        self.refused(record_transform=lambda rows:[*rows[:-1],[DIST+'/RECORD','sha256=x','1']])

    def test_wheel_tags_purelib_and_build_are_exact(self):
        for body in (b'Wheel-Version: 2.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n',
                     b'Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-any\n\n',
                     b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py2-none-any\n\n',
                     b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\nBuild: 1\n\n'):
            with self.subTest(body=body):self.refused(wheel=body)

    def test_startup_hooks_relocation_and_installer_metadata_refuse(self):
        for path in ('custom.pth','sitecustomize.py','x/usercustomize.py','x.egg-link','x.pyc',
                     'example.data/scripts/tool',DIST+'/INSTALLER',DIST+'/direct_url.json'):
            with self.subTest(path=path):self.refused(add={path:b'x'})

    def test_existing_lock_selects_exact_23_original_archives(self):
        raw=(Path(__file__).resolve().parents[2]/'uv.lock').read_bytes()
        specs=wheels.locked_wheels(raw)
        self.assertEqual(len(specs),23);self.assertEqual(sum(s.size for s in specs),9276162)
        self.assertEqual(tuple((s.name,s.version,s.dist_info) for s in specs),ns.backend._BROWSER_DEPENDENCY_DISTRIBUTIONS)
        self.assertEqual(sum(s.tags==('py3-none-any',) for s in specs),19)

    def test_lock_upgrade_missing_duplicate_and_foreign_registry_refuse(self):
        raw=(Path(__file__).resolve().parents[2]/'uv.lock').read_bytes()
        for changed in (raw.replace(b'version = "0.0.5"',b'version = "0.0.6"'),
                        raw.replace(b'https://pypi.org/simple',b'https://foreign.invalid/simple'),
                        raw.replace(b'annotated_doc-0.0.5-py3-none-any.whl',b'annotated_doc-0.0.5-py2-none-any.whl')):
            with self.subTest(),self.assertRaises(ns.NamespaceProofError):wheels.locked_wheels(changed)

    def test_narrow_rpath_requires_exact_bytes_and_runpath_stays_refused(self):
        data=elf(tag=15,needed=b'$ORIGIN')
        self.assertIsNone(runtime.parse_elf(data,expected_rpath='$ORIGIN')['soname'])
        for expected in (None,'$ORIGIN/../psycopg_binary.libs','/host'):
            with self.subTest(expected=expected),self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(data,expected_rpath=expected)
        with self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(elf(tag=29,needed=b'$ORIGIN'),expected_rpath='$ORIGIN')

    def graph(self,*,missing=False,foreign=False,no_ancestor=False,unreached=False):
        ext='psycopg_binary/pq.cpython-313-x86_64-linux-gnu.so'
        if no_ancestor:ext='psycopg_binary/unexpected.so'
        first=wheels._PRIVATE+'libpq-2be5f14a.so.5.18';child=wheels._PRIVATE+'libchild.so.1'
        paths=[ext,first]+([] if missing else [child])+([wheels._PRIVATE+'unused.so.1'] if unreached else [])
        spec=wheels.LockedWheel('psycopg-binary','3.3.4','unused','unused','a'*64,1,())
        payload={p:b'\x7fELF'+p.encode() for p in paths};owners={p:spec for p in paths}
        if foreign:owners[ext]=replace(spec,name='foreign')
        def parse(data,**kwargs):
            path=data[4:].decode()
            needs=(Path(first).name,) if path==ext else ((Path(child).name,) if path==first else ('libc.so.6',))
            return dict(interpreter=None,needed=needs,soname=Path(path).name if path.startswith(wheels._PRIVATE) else None)
        with patch.object(runtime,'parse_elf',side_effect=parse):return wheels.elf_closure(payload,owners)

    def test_transitive_private_library_uses_inherited_rpath(self):
        external,elfs=self.graph()
        self.assertEqual(external,('libc.so.6',));self.assertEqual(len(elfs),3)

    def test_missing_private_library_never_substitutes_host(self):
        with patch.object(runtime,'_resolve_library') as host,self.assertRaises(ns.NamespaceProofError):self.graph(missing=True)
        host.assert_not_called()

    def test_private_rpath_cannot_cross_wheel_ownership(self):
        with self.assertRaises(ns.NamespaceProofError):self.graph(foreign=True)

    def test_private_library_requires_loader_ancestry(self):
        with self.assertRaises(ns.NamespaceProofError):self.graph(no_ancestor=True)

    def test_unreachable_private_elf_refuses(self):
        with self.assertRaises(ns.NamespaceProofError):self.graph(unreached=True)

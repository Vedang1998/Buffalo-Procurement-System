"""Full pinned manifest control-flow regressions; no privileged calls or file copies.

Transport metadata/bytes are controlled. Real decode/stage measurements are separate.
"""
from collections import Counter
from contextlib import contextmanager
import os
import stat
import time
import unittest
from unittest.mock import patch

import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_installation as install
import hosted_synthetic_native_route as route
import hosted_synthetic_native_measurement as measurement
import hosted_synthetic_postgres_runtime as pg
from test_hosted_synthetic_native_installation import SyntheticOps,BINDING
import test_hosted_synthetic_native_route as route_tests


class CountingOps(SyntheticOps):
    def __init__(self,deadline):
        super().__init__(deadline);self.counts=Counter();self.phase='create';self.rows=native.execution_inventory()['rows']
    def hit(self,program):self.counts[self.phase,program]+=1
    def run(self,program,*args):
        self.hit(program)
        # Exercise real argument bounds too; rendering does not execute sudo.
        install._command(program,*args)
        if program=='sha256sum':
            return b''.join((self.rows[p.split('/view',1)[1]]['sha256']+'  '+p+'\n').encode() for p in args[1:])
        return super().run(program,*args)
    def stream(self,path,row,chunks):
        self.hit('dd');self.serial+=1
        self.nodes[path]=[1,self.serial,stat.S_IFREG|0o600,0,0,1,row['bytes']]
    def observe(self,path):self.hit('stat');return tuple(self.nodes[path])
    def observe_many(self,paths,*,follow=False):
        for _ in range(0,len(paths),64):self.hit('stat')
        return [tuple(self.nodes[p]) for p in paths]
    def inventory(self,root):self.hit('find');return super().inventory(root)

@contextmanager
def empty_stream(stage,row):yield iter(())
def stage_close(stage):stage.closed=True


class NativeBatchTests(unittest.TestCase):
    def setUp(self):
        self.rows=native.execution_inventory()['rows'];self.inventory=native.require_complete_inventory()
        self.enterContext(patch.object(install,'_source_binding'))
        self.enterContext(patch.object(install,'_NativeOps',CountingOps))
        self.enterContext(patch.object(native.Stage,'verified_stream',empty_stream))
        self.enterContext(patch.object(native.Stage,'close',stage_close))
        self.enterContext(patch.object(os.path,'lexists',return_value=False))
    def lease(self):
        lease=install.InstallationLease._create('f'*32,deadline=time.monotonic()+60,binding=BINDING)
        return lease
    def imports(self,lease):
        lease.ops.phase='import'
        for digest in sorted(self.inventory['artifacts']):
            rows=[r for r in self.rows.values() if r['artifact_sha256']==digest]
            stage=object.__new__(native.Stage);stage.closed=False
            lease.import_stage(stage,rows);self.assertTrue(stage.closed)
    def test_full_manifest_exact_command_population_and_cleanup(self):
        self.assertEqual((len(self.rows),len(self.inventory['artifacts'])),(832,66))
        lease=self.lease();self.imports(lease);lease.ops.phase='finish';lease.finish()
        self.assertEqual(lease.total,149567173);self.assertEqual(len(lease.owned),919)
        lease.ops.phase='cleanup';lease.close()
        totals=Counter()
        for (phase,program),count in lease.ops.counts.items():totals[phase]+=count
        self.assertEqual(dict(totals),{'create':45,'import':1365,'finish':23,'cleanup':114})
        self.assertEqual(sum(totals.values()),1547)
        self.assertEqual(lease.ops.counts['import','dd'],832)
        self.assertTrue(lease.closed);self.assertFalse(lease.ops.nodes)
    def test_partial_directory_batch_quarantines_unaccounted_objects(self):
        original=CountingOps.run
        def fail(ops,program,*args):
            if program=='mkdir' and len(args)>3:
                original(ops,program,*args[:3]);raise native.ns.NamespaceProofError('partial_batch')
            return original(ops,program,*args)
        with patch.object(CountingOps,'run',fail),self.assertRaises(native.ns.NamespaceProofError) as raised:self.lease()
        lease=raised.exception.native_installation_lease
        self.assertTrue(lease.uncertain)
        before=lease.ops.counts.copy()
        with self.assertRaises(native.ns.NamespaceProofError):lease.close()
        self.assertEqual(lease.ops.counts,before)
    def test_wrong_hash_order_missing_output_and_partial_mode_refuse(self):
        for fault in ('order','missing','mode'):
            lease=self.lease();original=lease.ops.run
            def fail(program,*args):
                raw=original(program,*args)
                if program=='sha256sum' and len(args)>2:
                    lines=raw.splitlines(keepends=True)
                    if fault=='order':return b''.join(reversed(lines))
                    if fault=='missing':return b''.join(lines[:-1])
                if fault=='mode' and program=='chmod':lease.ops.nodes[args[-1]][2]^=0o020
                return raw
            lease.ops.run=fail
            with self.subTest(fault=fault),self.assertRaises(native.ns.NamespaceProofError):self.imports(lease)
            self.assertTrue(lease.uncertain)
            with self.assertRaises(native.ns.NamespaceProofError):lease.close()
    def test_hardlink_symlink_and_replaced_output_identity_refuse(self):
        for fault in ('hardlink','symlink','replacement'):
            lease=self.lease();original=lease.ops.run
            def fail(program,*args):
                raw=original(program,*args)
                if program=='sha256sum':
                    node=lease.ops.nodes[args[-1]]
                    if fault=='hardlink':node[5]=2
                    if fault=='symlink':node[2]=stat.S_IFLNK|0o777
                    if fault=='replacement':node[1]+=10000
                return raw
            lease.ops.run=fail
            with self.subTest(fault=fault),self.assertRaises(native.ns.NamespaceProofError):self.imports(lease)
            self.assertTrue(lease.uncertain)
    def test_batch_argument_count_bytes_and_nul_bounds(self):
        for args in (['x']*71,['x'*32768],['a\0b']):
            with self.assertRaisesRegex(native.ns.NamespaceProofError,'arguments_refused'):install._command('stat',*args)
        self.assertIn('/usr/bin/stat',install._command('stat',*(['x']*64)))
    def connected(self):
        parent,_,_=route_tests.NativeRouteTests.fixture(self)
        # Restore the actual full manifest after the small route fixture setup.
        self.enterContext(patch.object(native,'require_complete_inventory',return_value=self.inventory))
        self.enterContext(patch.object(native,'execution_inventory',return_value={'rows':self.rows,'renamed_logical_files':{}}))
        self.enterContext(patch.object(install,'_NativeOps',CountingOps))
        def artifact(*args):return os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
        self.enterContext(patch.object(route,'_artifact_fd',side_effect=artifact))
        self.enterContext(patch.object(route,'materialize_artifact'))
        # Real empty disposable Stage directories, removed by their original close.
        def close(stage):
            os.close(stage.fd);stage.path.rmdir();stage.closed=True
        self.enterContext(patch.object(native.Stage,'close',close))
        def view(binding,root,*,deadline,installation):
            self.events.append('view');self.assertTrue(installation.installed)
            self.assertEqual(installation.total,149567173)
            request=native.ViewRequest('c'*64,binding.commit,binding.tree,native.INSTALLATION_MANIFEST_SHA256,
                root,installation.owned[''][:2],'d'*64,deadline,native._TOKEN,(1,2),
                {p:list(installation.owned['view'+p][:2]) for p in ('/opt','/usr/share',*self.rows)})
            installation.view_requests[request.nonce]=request;return request
        self.enterContext(patch.object(native,'view_request',side_effect=view))
        return parent
    def test_connected_full_manifest_m1_service_and_cleanup(self):
        parent=self.connected();result=route._run(route_tests.ARGS,parent)
        self.assertTrue(result['native_installation_removed']);self.assertTrue(result['dedicated_identity_removed'])
        self.assertLess(self.events.index('memory_verified'),self.events.index('account'))
        self.assertIn('service',self.events);self.assertFalse(self.leases[0].ops.nodes)
    def test_connected_full_manifest_memory_failure_and_cancellation_create_no_account(self):
        for failure in (pg.PhaseMemoryRefusal({'capacity':'FIT','memory':'REFUSED'}),KeyboardInterrupt('cancel')):
            parent=self.connected();self.measure.side_effect=failure
            with self.subTest(error=type(failure).__name__),self.assertRaises(type(failure)):route._run(route_tests.ARGS,parent)
            self.create.assert_not_called();self.service.assert_not_called()
            self.assertTrue(self.leases[0].closed);self.assertFalse(self.leases[0].ops.nodes)
    def test_expired_partial_stream_preserves_error_and_blocks_cleanup_pass(self):
        lease=self.lease();error=install.WorkExpired('partial_stream_expiry');original=lease.ops.stream
        def stream(*args):original(*args);raise error
        lease.ops.stream=stream
        with self.assertRaises(install.WorkExpired) as raised:self.imports(lease)
        self.assertIs(raised.exception,error);self.assertTrue(lease.uncertain)
        with self.assertRaises(native.ns.NamespaceProofError):lease.close()

    def test_partial_stream_batch_and_mode_failure_remain_quarantined(self):
        for fault in ('stream','chmod'):
            lease=self.lease();streams=0;original=lease.ops.stream;run=lease.ops.run
            def stream(*args):
                nonlocal streams
                original(*args);streams+=1
                if fault=='stream' and streams==2:raise KeyboardInterrupt('partial_stream')
            def command(program,*args):
                result=run(program,*args)
                if fault=='chmod' and program=='chmod':raise KeyboardInterrupt('partial_mode')
                return result
            lease.ops.stream=stream;lease.ops.run=command
            with self.subTest(fault=fault),self.assertRaises(KeyboardInterrupt):self.imports(lease)
            self.assertTrue(lease.uncertain);before=lease.ops.counts.copy()
            with self.assertRaises(native.ns.NamespaceProofError):lease.close()
            self.assertEqual(before,lease.ops.counts)

    def test_cancel_after_batch_metadata_before_parent_ack_keeps_uncertainty(self):
        lease=self.lease();error=KeyboardInterrupt('parent_refresh_cancelled')
        with patch.object(lease,'_refresh_parent_batch',side_effect=error),self.assertRaises(KeyboardInterrupt) as raised:
            self.imports(lease)
        self.assertIs(raised.exception,error);self.assertTrue(lease.pending);self.assertTrue(lease.uncertain)
        with self.assertRaises(native.ns.NamespaceProofError):lease.close()

    def test_full_manifest_output_and_argument_bounds_unchanged(self):
        lease=self.lease();self.imports(lease)
        self.assertEqual(sum(len(n.encode())+1 for n in lease.owned),64686)
        self.assertLessEqual(sum(len(n.encode())+1 for n in lease.owned),65536)
        for digest in self.inventory['artifacts']:
            rows=[r for r in self.rows.values() if r['artifact_sha256']==digest]
            for offset in range(0,len(rows),64):
                batch=rows[offset:offset+64];paths=[install._path(lease.root,'view'+r['path']) for r in batch]
                output=''.join(r['sha256']+'  '+p+'\n' for r,p in zip(batch,paths)).encode()
                self.assertLessEqual(len(output),65536)
                self.assertLessEqual(sum(len(p)+1 for p in paths)+3,32768)
        lease.finish();lease.close()

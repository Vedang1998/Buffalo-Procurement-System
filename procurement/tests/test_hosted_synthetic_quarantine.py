"""Disposable journal tests; no accounts, services or credentials are created."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hosted_synthetic_quarantine as journal
from hosted_synthetic_identity import DedicatedIdentity
from hosted_synthetic_bootstrap import BootstrapRefusal


class HostedQuarantineTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='buffalo-quarantine-test-')
        self.addCleanup(self.temporary.cleanup)
        selected=patch.object(journal,'ROOT',Path(self.temporary.name));selected.start();self.addCleanup(selected.stop)
        self.identity=DedicatedIdentity('01234567-89ab-cdef-0123-456789abcdef',900,900,os.geteuid(),os.getegid())

    def create(self):return journal.Quarantine.create(self.identity,'a'*40)

    def test_reservation_is_durable_private_and_has_no_retained_descriptor(self):
        before=set(os.listdir('/proc/self/fd'));value=self.create()
        self.assertEqual(before,set(os.listdir('/proc/self/fd')))
        self.assertEqual(value.path.stat().st_mode&0o777,0o600)
        self.assertEqual(journal.reserved_ids(),{(900,900)})
        value.record('provision_attempt_0');value.record('provision_ack_0')
        self.assertEqual(before,set(os.listdir('/proc/self/fd')))
        self.assertNotIn(b'@',value.path.read_bytes())

    def test_unresolved_identity_cannot_be_reused_with_another_nonce(self):
        self.create()
        changed=DedicatedIdentity('11234567-89ab-cdef-0123-456789abcdef',900,901,os.geteuid(),os.getegid())
        with self.assertRaisesRegex(BootstrapRefusal,'quarantined_identity'):journal.Quarantine.create(changed,'b'*40)

    def test_resolution_is_explicit_and_unknown_stage_cannot_resolve(self):
        value=self.create()
        with self.assertRaises(BootstrapRefusal):value.record('RESOLVED')
        self.assertEqual(journal.reserved_ids(),{(900,900)})
        value.resolve();self.assertEqual(journal.reserved_ids(),set())
        with self.assertRaises(BootstrapRefusal):value.record('service_start_attempt')

    def test_closing_after_partial_setup_preserves_reservation(self):
        value=self.create();value.record('provision_attempt_1');value.close()
        self.assertEqual(journal.reserved_ids(),{(900,900)})

    def test_fsync_failure_leaves_reservation_and_closes_owned_fd(self):
        before=set(os.listdir('/proc/self/fd'))
        with patch.object(os,'fsync',side_effect=OSError('synthetic')),self.assertRaises(OSError):self.create()
        self.assertEqual(before,set(os.listdir('/proc/self/fd')))
        self.assertEqual(journal.reserved_ids(),{(900,900)})

    def test_empty_or_truncated_record_blocks_allocation(self):
        path=journal.ROOT/(journal.PREFIX+self.identity.nonce+'.jsonl');path.write_bytes(b'');path.chmod(0o600)
        with self.assertRaises(BootstrapRefusal):self.create()

    def test_replaced_path_is_never_appended_or_unlinked(self):
        value=self.create();value.path.unlink();value.path.write_bytes(b'foreign');value.path.chmod(0o600)
        with self.assertRaises(BootstrapRefusal):value.record('service_start_attempt')
        self.assertEqual(value.path.read_bytes(),b'foreign')

    def test_copied_journal_cannot_mutate_owned_record(self):
        value=self.create();before=value.path.read_bytes()
        with self.assertRaises(BootstrapRefusal):copy.copy(value).record('service_start_attempt')
        self.assertEqual(value.path.read_bytes(),before)

    def test_unknown_duplicate_or_reordered_record_refuses(self):
        value=self.create();header=value.raw.splitlines(keepends=True)[0]
        for raw in (header+b'{"sequence":1,"stage":"RESERVED"}\n',
                    header+b'{"sequence":0,"stage":"unknown"}\n',
                    value.raw+b'{"sequence":1,"stage":"RESOLVED"}\n'+b'{"sequence":2,"stage":"service_start_attempt"}\n'):
            with self.subTest(raw=raw[-30:]),self.assertRaises(BootstrapRefusal):journal._decode(raw)

    def test_insecure_permissions_or_hardlinks_do_not_establish_reservation(self):
        value=self.create();value.path.chmod(0o644)
        with self.assertRaises(BootstrapRefusal):journal.reserved_ids()
        value.path.chmod(0o600);os.link(value.path,journal.ROOT/'alias')
        with self.assertRaises(BootstrapRefusal):journal.reserved_ids()


if __name__=='__main__':unittest.main()

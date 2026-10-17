"""Projection contract/refusal regressions; never execute the sentinel."""
from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_projection as projection


TARGET = '/tmp/buffalo-hosted-projection-' + 'a'*64


def mountinfo(options=b'ro,nosuid,nodev', optional=b'', target=TARGET.encode(), fs=b'tmpfs', number=25):
    return str(number).encode() + b' 20 0:35 / ' + target + b' ' + options + optional + b' - ' + fs + b' tmpfs ro\n'


class HostedSyntheticProjectionTests(unittest.TestCase):
    def test_static_elf_has_no_loader_dynamic_segment_or_writable_code(self):
        raw = projection.sentinel_bytes()
        self.assertEqual(len(raw), 341)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '4e4042014c2ed77edea2140f0b1a85a153510fcc53b05d480d79aa5419780a91')
        header = struct.unpack('<16sHHIQQQIHHHHHH', raw[:64])
        self.assertEqual(header[1:4], (2, 62, 1))
        self.assertEqual(header[4:7], (0x4000b0, 64, 0))
        self.assertEqual(header[8:], (64, 56, 2, 0, 0, 0))
        load, stack = (struct.unpack('<IIQQQQQQ', raw[start:start+56]) for start in (64, 120))
        self.assertEqual(load, (1, 5, 0, 0x400000, 0x400000, 341, 341, 4096))
        self.assertEqual(stack, (0x6474e551, 6, 0, 0, 0, 0, 0, 16))

    def test_mutated_instruction_bytes_fail_before_any_resource(self):
        with patch.object(projection, '_CODE', projection._CODE[:-1] + b'\0'), \
             patch.object(projection.os, 'mkdir') as create:
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection.sentinel_bytes()
        create.assert_not_called()

    def test_platform_architecture_refuses_before_resources(self):
        for machine in ('aarch64', 'i686', ''):
            with patch.object(projection.platform, 'machine', return_value=machine), \
                 patch.object(projection.os, 'mkdir') as create:
                with self.assertRaises(projection.ownership.NamespaceProofError):
                    projection.observe_projection('a'*64)
                create.assert_not_called()

    def test_ordinary_non_namespace_invocation_refuses_before_resources(self):
        with patch.object(projection.os, 'getpid', return_value=123), patch.object(projection.os, 'mkdir') as create:
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection.observe_projection('a'*64)
        create.assert_not_called()

    def test_invalid_generation_refuses_before_resources(self):
        with patch.object(projection.os, 'mkdir') as create:
            for value in (None, True, '', 'a'*63, 'A'*64, '../foreign'):
                with self.assertRaises(projection.ownership.NamespaceProofError):
                    projection.observe_projection(value)
        create.assert_not_called()

    def test_owned_mount_identity_and_exact_flags_are_required(self):
        result = projection.parse_mount(mountinfo(), 25, TARGET)
        self.assertEqual(result, {'mount_id':25, 'flags':['ro','nosuid','nodev'], 'executable':True})

    def test_each_missing_required_flag_refuses(self):
        for flags in (b'nosuid,nodev', b'ro,nodev', b'ro,nosuid'):
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection.parse_mount(mountinfo(options=flags), 25, TARGET)

    def test_noexec_or_writable_projection_refuses(self):
        for flags in (b'ro,nosuid,nodev,noexec', b'rw,nosuid,nodev', b'ro,rw,nosuid,nodev'):
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection.parse_mount(mountinfo(options=flags), 25, TARGET)

    def test_foreign_mount_identity_path_and_filesystem_refuse(self):
        for raw in (mountinfo(number=26), mountinfo(target=b'/tmp/foreign'), mountinfo(fs=b'ext4')):
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection.parse_mount(raw, 25, TARGET)

    def test_duplicate_mount_identity_and_shared_propagation_refuse(self):
        for raw in (mountinfo()*2, mountinfo(optional=b' shared:9'), mountinfo(options=b'ro,nosuid,nodev,ro')):
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection.parse_mount(raw, 25, TARGET)

    def test_malformed_oversize_and_untrusted_target_refuse(self):
        for raw in (b'', b'malformed\n', mountinfo()[:-1], b'x'*(projection.ownership._LIMIT+1)):
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection.parse_mount(raw, 25, TARGET)
        with self.assertRaises(projection.ownership.NamespaceProofError):
            projection.parse_mount(mountinfo(), 25, '/tmp/foreign')

    def test_write_refusal_requires_exact_erofs_not_permissions(self):
        root = Mock()
        with patch.object(projection.os, 'open', side_effect=OSError(errno.EROFS, 'readonly')):
            projection._write_refusal(root)
        for error in (errno.EACCES, errno.EPERM, errno.EEXIST, errno.ENOSPC):
            with patch.object(projection.os, 'open', side_effect=OSError(error, 'refused')):
                with self.assertRaises(projection.ownership.NamespaceProofError):
                    projection._write_refusal(root)

    def test_unexpected_write_success_closes_descriptor_and_refuses(self):
        descriptor = Mock()
        with patch.object(projection.os, 'open', return_value=99), \
             patch.object(projection.ownership, '_acquire', return_value=descriptor):
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection._write_refusal(Mock())
        descriptor.close.assert_called_once()

    def _partial(self, transition):
        generation = hashlib.sha256(self.id().encode()).hexdigest()
        real_acquire = projection.ownership._acquire
        with tempfile.TemporaryDirectory() as directory:
            parent = real_acquire(lambda: os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
            target = Path(directory)/('buffalo-hosted-projection-' + generation)
            count = 0
            def acquire(factory):
                nonlocal count
                count += 1
                if transition == 'capture' and count == 1:
                    raise OSError('synthetic capture failure')
                return real_acquire(factory)
            try:
                with patch.object(projection, '_open_tmpfs', side_effect=OSError('synthetic fsopen refusal')), \
                     patch.object(projection.ownership, '_acquire', side_effect=acquire):
                    with self.assertRaises(OSError):
                        projection._check_projection(parent, generation)
                self.assertFalse(target.exists())
            finally:
                parent.close()

    def test_tmpfs_open_failure_removes_only_new_owned_target(self):
        self._partial('fsopen')

    def test_target_capture_failure_still_removes_identity_bound_directory(self):
        self._partial('capture')

    def test_foreign_name_replacement_is_preserved_and_cleanup_fails(self):
        generation = hashlib.sha256(self.id().encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            parent = projection.ownership._acquire(lambda: os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
            target = Path(directory)/('buffalo-hosted-projection-' + generation)
            moved = Path(str(target)+'-moved')
            def replace(owners):
                target.rename(moved)
                target.mkdir(mode=0o700)
                raise OSError('synthetic replacement')
            try:
                with patch.object(projection, '_open_tmpfs', side_effect=replace):
                    with self.assertRaises(projection.ownership.NamespaceCleanupError):
                        projection._check_projection(parent, generation)
                self.assertTrue(target.is_dir())
                self.assertTrue(moved.is_dir())
            finally:
                parent.close()

    def test_moved_owned_mount_is_detached_by_identity_even_if_name_restored(self):
        root = Mock(number=11, identity=(35, 1))
        with patch.object(projection.ownership, '_mount_id', return_value=25), \
             patch.object(projection, '_mount_rows', side_effect=[[(25, 20)], []]), \
             patch.object(projection.backend, '_browser_unmount') as unmount:
            projection._detach_owned(root, ((35, 1), 25))
        unmount.assert_called_once_with(b'/proc/self/fd/11', 2)

    def test_missing_or_truncated_mount_evidence_refuses_before_detach(self):
        root = Mock(number=11, identity=(35, 1))
        for raw in (b'', mountinfo()[:-1], b'x'*(projection.ownership._LIMIT+1), None):
            with self.subTest(raw_type=type(raw).__name__), \
                 patch.object(projection.ownership, '_mount_id', return_value=25), \
                 patch.object(projection.ownership, '_read', return_value=raw), \
                 patch.object(projection.backend, '_browser_unmount') as unmount:
                with self.assertRaises(projection.ownership.NamespaceProofError):
                    projection._detach_owned(root, ((35, 1), 25))
                unmount.assert_not_called()

    def test_missing_mount_evidence_after_detach_cannot_prove_cleanup(self):
        root = Mock(number=11, identity=(35, 1))
        for raw in (b'', mountinfo()[:-1]):
            with patch.object(projection.ownership, '_mount_id', return_value=25), \
                 patch.object(projection.ownership, '_read', side_effect=[mountinfo(), raw]), \
                 patch.object(projection.backend, '_browser_unmount') as unmount:
                with self.assertRaises(projection.ownership.NamespaceProofError):
                    projection._detach_owned(root, ((35, 1), 25))
                unmount.assert_called_once_with(b'/proc/self/fd/11', 2)

    def test_foreign_submount_is_preserved_and_prevents_detach(self):
        root = Mock(number=11, identity=(35, 1))
        with patch.object(projection.ownership, '_mount_id', return_value=25), \
             patch.object(projection, '_mount_rows', return_value=[(25, 20), (26, 25)]), \
             patch.object(projection.backend, '_browser_unmount') as unmount:
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection._detach_owned(root, ((35, 1), 25))
        unmount.assert_not_called()

    def test_mount_still_present_after_detach_refuses(self):
        root = Mock(number=11, identity=(35, 1))
        with patch.object(projection.ownership, '_mount_id', return_value=25), \
             patch.object(projection, '_mount_rows', return_value=[(25, 20)]), \
             patch.object(projection.backend, '_browser_unmount'):
            with self.assertRaises(projection.ownership.NamespaceProofError):
                projection._detach_owned(root, ((35, 1), 25))

    def test_same_content_foreign_inode_or_mount_refuses_inventory(self):
        root = Mock(number=10, identity=(35, 1))
        descriptor = Mock(number=11, identity=(35, 2))
        directory = SimpleNamespace(st_mode=0o40700, st_uid=0, st_gid=0, st_dev=35)
        regular = SimpleNamespace(st_mode=0o100555, st_uid=0, st_gid=0, st_nlink=1, st_dev=35, st_size=341)
        for identity, mounts in (((35, 3), [25,25]), ((35, 2), [26,25])):
            with patch.object(projection.os, 'fstat', side_effect=[directory,regular]), \
                 patch.object(projection.os, 'listdir', return_value=['sentinel']), \
                 patch.object(projection.os, 'pread', return_value=projection.sentinel_bytes()), \
                 patch.object(projection.ownership, '_acquire', return_value=descriptor), \
                 patch.object(projection.ownership, '_mount_id', side_effect=mounts):
                with self.assertRaises(projection.ownership.NamespaceProofError):
                    projection._inventory(root, identity)


if __name__ == '__main__':
    unittest.main()

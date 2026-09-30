"""Synthetic-only tests for the Railway research deployment inventory."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from procurement_os import staging_research_transfer as transfer
from procurement_os.staging_research_transfer import (
    DeploymentInventory,
    DirectoryContract,
    InventoryMember,
    ResearchTransferError,
    UnavailableDependency,
    audit_host_dependency_closure,
    validate_host_dependency_closure,
)
from procurement_os.supplier_review_real_v5 import _AUXILIARY_FILES


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _SyntheticClosure:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(mode=0o700)
        (root / "data").mkdir(mode=0o700)
        (root / "a1-package-v1").mkdir(mode=0o700)
        self.historical_host: list[InventoryMember] = []
        for index in range(71):
            raw = f"accepted-record-{index:02d}".encode("ascii")
            relative = f"data/record-{index:02d}.bin"
            self._write(relative, raw)
            self.historical_host.append(
                InventoryMember(
                    relative_path=relative,
                    deployment_path=relative,
                    source_class=transfer.SOURCE_CLASS_HOST,
                    bytes=len(raw),
                    sha256=_digest(raw),
                    mode="0600",
                    ownership_role=transfer.OWNER_RESEARCH,
                    deployment_mode="0600",
                    deployment_ownership_role=transfer.OWNER_RESEARCH,
                    dependency_role="Accepted synthetic fixture",
                    authority_source="synthetic accepted manifest",
                )
            )
        self.auxiliary: list[InventoryMember] = []
        for name, raw in (
            ("Buffalo_Daytime_Final_Deliverable_Hashes.json", b"synthetic-hashes"),
            ("Buffalo_Daytime_V5_Portable_Handoff.md", b"synthetic-handoff"),
        ):
            relative = f"a1-package-v1/{name}"
            self._write(relative, raw)
            self.auxiliary.append(
                InventoryMember(
                    relative_path=relative,
                    deployment_path=relative,
                    source_class=transfer.SOURCE_CLASS_HOST,
                    bytes=len(raw),
                    sha256=_digest(raw),
                    mode="0600",
                    ownership_role=transfer.OWNER_RESEARCH,
                    deployment_mode="0600",
                    deployment_ownership_role=transfer.OWNER_RESEARCH,
                    dependency_role="A1 semantic-reader auxiliary",
                    authority_source="synthetic accepted reader pin",
                )
            )
        self.tracked = [
            InventoryMember(
                relative_path=f"procurement/fixture-{index}.json",
                deployment_path=f"procurement/fixture-{index}.json",
                source_class=transfer.SOURCE_CLASS_GIT,
                bytes=index + 1,
                sha256=_digest(bytes([index + 1]) * (index + 1)),
                mode="100644",
                ownership_role=transfer.OWNER_SOURCE,
                deployment_mode="0644",
                deployment_ownership_role=transfer.OWNER_IMAGE,
                dependency_role=f"Tracked fixture {index}",
                authority_source="synthetic accepted commit",
            )
            for index in range(4)
        ]
        self.directories = (
            DirectoryContract(".", "0700"),
            DirectoryContract("a1-package-v1", "0700"),
            DirectoryContract("data", "0700"),
        )
        self.unavailable = tuple(
            UnavailableDependency(
                identity=f"unavailable-{index}.bin",
                reason=(
                    "UNAVAILABLE_ORIGINAL_BYTES"
                    if index < 3
                    else "UNRESOLVED_STANDALONE_READER_FILE"
                ),
                expected_bytes=index + 1,
                expected_sha256=_digest(bytes([index])),
            )
            for index in range(6)
        )

    def _write(self, relative: str, raw: bytes) -> None:
        path = self.root / relative
        path.write_bytes(raw)
        path.chmod(0o600)

    @contextmanager
    def accepted_inventory(self):
        historical = tuple(sorted((*self.historical_host, *self.tracked)))
        auxiliary = tuple(sorted(self.auxiliary))
        members = tuple(sorted((*historical, *auxiliary)))
        values = {
            "HISTORICAL_RECORD_COUNT": len(historical),
            "HISTORICAL_AGGREGATE_BYTES": sum(item.bytes for item in historical),
            "HISTORICAL_HOST_RECORD_COUNT": len(self.historical_host),
            "HISTORICAL_HOST_AGGREGATE_BYTES": sum(
                item.bytes for item in self.historical_host
            ),
            "GIT_RECORD_COUNT": len(self.tracked),
            "GIT_AGGREGATE_BYTES": sum(item.bytes for item in self.tracked),
            "DEPLOYMENT_RECORD_COUNT": len(members),
            "DEPLOYMENT_AGGREGATE_BYTES": sum(item.bytes for item in members),
            "DEPLOYMENT_HOST_RECORD_COUNT": len(self.historical_host)
            + len(auxiliary),
            "DEPLOYMENT_HOST_AGGREGATE_BYTES": sum(
                item.bytes for item in (*self.historical_host, *auxiliary)
            ),
            "AUXILIARY_RECORD_COUNT": len(auxiliary),
            "AUXILIARY_AGGREGATE_BYTES": sum(item.bytes for item in auxiliary),
            "HISTORICAL_MEMBERSHIP_SHA256": transfer._membership_sha256(historical),
            "AUXILIARY_MEMBERSHIP_SHA256": transfer._membership_sha256(auxiliary),
        }
        with mock.patch.multiple(transfer, **values):
            inventory = DeploymentInventory(
                members=members,
                historical_membership_sha256=values[
                    "HISTORICAL_MEMBERSHIP_SHA256"
                ],
                auxiliary_membership_sha256=values[
                    "AUXILIARY_MEMBERSHIP_SHA256"
                ],
                directories=self.directories,
                unavailable=self.unavailable,
            )
            with mock.patch.object(
                transfer, "DEPLOYMENT_INVENTORY_SHA256", inventory.identity_sha256
            ):
                yield inventory


class StagingResearchTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        temporary.chmod(0o700)
        self.source = _SyntheticClosure(temporary / "source")
        self.staging = temporary / "staging"
        self.destination = temporary / "destination"
        self.staging.mkdir(mode=0o700)
        self.destination.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_code_owned_auxiliary_pins_are_exact(self) -> None:
        self.assertEqual(
            _AUXILIARY_FILES,
            {
                "Buffalo_Daytime_Final_Deliverable_Hashes.json": (
                    1_320,
                    "cd5b6a5d7f0db4d9e0e0d21595c93701aeb70f0d2cd18a1c54f2f05bfc2e388e",
                ),
                "Buffalo_Daytime_V5_Portable_Handoff.md": (
                    3_185,
                    "aba5558cda8d5f2f8b919bc07720d99c319845e126d76e3d6837b59f785119bd",
                ),
            },
        )
        self.assertEqual(
            (
                transfer.HISTORICAL_RECORD_COUNT,
                transfer.HISTORICAL_AGGREGATE_BYTES,
                transfer.DEPLOYMENT_RECORD_COUNT,
                transfer.DEPLOYMENT_AGGREGATE_BYTES,
            ),
            (75, 1_030_999_197, 77, 1_031_003_702),
        )
        self.assertEqual(
            (
                transfer.HISTORICAL_MEMBERSHIP_SHA256,
                transfer.AUXILIARY_MEMBERSHIP_SHA256,
                transfer.DEPLOYMENT_INVENTORY_SHA256,
                transfer.DEPLOYMENT_INVENTORY_BYTES,
                transfer.DEPLOYMENT_INVENTORY_FILE_SHA256,
            ),
            (
                "d8514a0b795bd5e588df4a8995b4a8003f5182f9aba4ab4aff10da11e5a99395",
                "0e15e027e618591b6e4d23b95bfc4df41b8f925445b16c15455998f6cb384881",
                "285381c6bab3c11427e83c37765b9e728bc987af16e6f6b3b5e914d9fe68f1b1",
                49_648,
                "97fd10669302a2158882d08102b8d306b51b6b3c61bae6941ac957ef787238b2",
            ),
        )

    def test_complete_revised_dependency_set_is_accepted(self) -> None:
        with self.source.accepted_inventory() as inventory:
            proof = validate_host_dependency_closure(
                inventory,
                research_root=self.source.root,
                expected_source_uid=os.getuid(),
                expected_source_gid=os.getgid(),
            )
            self.assertEqual(proof.record_count, 73)
            self.assertTrue(proof.source_unchanged)
            body = inventory.to_dict()
            self.assertEqual(body["record_count"], 77)
            self.assertEqual(body["supersedes"]["record_count"], 75)
            self.assertEqual(
                body["supersedes"]["contract"],
                "BUFFALO_ACCEPTED_SOURCE_DEPENDENCY_INVENTORY_V1",
            )
            self.assertEqual(
                body["supersedes"]["accepted_inventory_sha256"],
                transfer.ACCEPTED_DEPENDENCY_INVENTORY_SHA256,
            )
            self.assertEqual(
                body["supersedes"]["derived_available_membership_sha256"],
                inventory.historical_membership_sha256,
            )
            self.assertEqual(body["auxiliary_delta"]["record_count"], 2)
            self.assertEqual(body["identity_sha256"], inventory.identity_sha256)
            raw = inventory.canonical_bytes()
            with mock.patch.multiple(
                transfer,
                DEPLOYMENT_INVENTORY_BYTES=len(raw),
                DEPLOYMENT_INVENTORY_FILE_SHA256=hashlib.sha256(raw).hexdigest(),
            ):
                self.assertEqual(
                    transfer.parse_accepted_deployment_inventory(raw), inventory
                )

    def test_each_missing_auxiliary_fails_closed(self) -> None:
        for member in self.source.auxiliary:
            with self.subTest(member=member.relative_path):
                path = self.source.root / member.relative_path
                raw = path.read_bytes()
                path.unlink()
                with self.source.accepted_inventory() as inventory:
                    with self.assertRaisesRegex(
                        ResearchTransferError, member.relative_path
                    ):
                        validate_host_dependency_closure(
                            inventory,
                            research_root=self.source.root,
                            expected_source_uid=os.getuid(),
                            expected_source_gid=os.getgid(),
                        )
                path.write_bytes(raw)
                path.chmod(0o600)

    def test_both_missing_auxiliaries_are_reported_together(self) -> None:
        for member in self.source.auxiliary:
            (self.source.root / member.relative_path).unlink()
        with self.source.accepted_inventory() as inventory:
            audit = audit_host_dependency_closure(
                inventory,
                research_root=self.source.root,
                expected_source_uid=os.getuid(),
                expected_source_gid=os.getgid(),
            )
        finding_paths = {item.relative_path for item in audit.findings}
        self.assertTrue(
            {item.relative_path for item in self.source.auxiliary}.issubset(
                finding_paths
            )
        )

    def test_read_error_and_missing_member_are_reported_together(self) -> None:
        missing, unreadable = self.source.auxiliary
        (self.source.root / missing.relative_path).unlink()
        unreadable_inode = (self.source.root / unreadable.relative_path).stat().st_ino
        real_read = transfer.os.read

        def fail_selected_read(descriptor: int, size: int) -> bytes:
            if os.fstat(descriptor).st_ino == unreadable_inode:
                raise OSError("synthetic read failure")
            return real_read(descriptor, size)

        with self.source.accepted_inventory() as inventory:
            with mock.patch.object(transfer.os, "read", fail_selected_read):
                audit = audit_host_dependency_closure(
                    inventory,
                    research_root=self.source.root,
                    expected_source_uid=os.getuid(),
                    expected_source_gid=os.getgid(),
                )
        finding_paths = {item.relative_path for item in audit.findings}
        self.assertTrue(
            {missing.relative_path, unreadable.relative_path}.issubset(finding_paths)
        )

    def test_each_tampered_auxiliary_fails_closed(self) -> None:
        for member in self.source.auxiliary:
            with self.subTest(member=member.relative_path):
                path = self.source.root / member.relative_path
                original = path.read_bytes()
                path.write_bytes(original + b"tampered")
                path.chmod(0o600)
                with self.source.accepted_inventory() as inventory:
                    with self.assertRaises(ResearchTransferError):
                        validate_host_dependency_closure(
                            inventory,
                            research_root=self.source.root,
                            expected_source_uid=os.getuid(),
                            expected_source_gid=os.getgid(),
                        )
                path.write_bytes(original)
                path.chmod(0o600)

    def test_unrelated_source_file_is_not_discovered_or_staged(self) -> None:
        unrelated = self.source.root / "a1-package-v1" / "unrelated-private.txt"
        unrelated.write_bytes(b"not authorized")
        unrelated.chmod(0o600)
        with self.source.accepted_inventory() as inventory:
            final = transfer._stage_and_promote_host_closure(
                inventory,
                research_root=self.source.root,
                staging_parent=self.staging,
                destination_parent=self.destination,
                destination_name="release",
                source_uid=os.getuid(),
                source_gid=os.getgid(),
                destination_uid=os.getuid(),
                destination_gid=os.getgid(),
            )
        self.assertFalse(
            (final / "payload" / "a1-package-v1" / unrelated.name).exists()
        )
        staged_files = {
            item.relative_to(final / "payload").as_posix()
            for item in (final / "payload").rglob("*")
            if item.is_file()
        }
        self.assertEqual(
            staged_files,
            {
                item.relative_path
                for item in inventory.members
                if item.source_class == transfer.SOURCE_CLASS_HOST
            },
        )

    def test_extra_inventory_member_is_rejected(self) -> None:
        with self.source.accepted_inventory() as inventory:
            extra = InventoryMember(
                relative_path="data/unrelated.bin",
                deployment_path="data/unrelated.bin",
                source_class=transfer.SOURCE_CLASS_HOST,
                bytes=1,
                sha256=_digest(b"x"),
                mode="0600",
                ownership_role=transfer.OWNER_RESEARCH,
                deployment_mode="0600",
                deployment_ownership_role=transfer.OWNER_RESEARCH,
                dependency_role="Unrelated",
                authority_source="not accepted",
            )
            changed = DeploymentInventory(
                members=tuple(sorted((*inventory.members, extra))),
                historical_membership_sha256=inventory.historical_membership_sha256,
                auxiliary_membership_sha256=inventory.auxiliary_membership_sha256,
                directories=inventory.directories,
                unavailable=inventory.unavailable,
            )
            with self.assertRaises(ResearchTransferError):
                validate_host_dependency_closure(
                    changed,
                    research_root=self.source.root,
                    expected_source_uid=os.getuid(),
                    expected_source_gid=os.getgid(),
                )

    def test_exact_duplicate_and_owner_mismatch_fail_closed(self) -> None:
        first = self.source.historical_host[0]
        with self.assertRaisesRegex(ResearchTransferError, "duplicate paths"):
            DeploymentInventory(
                members=(first, first),
                historical_membership_sha256="0" * 64,
                auxiliary_membership_sha256="1" * 64,
                directories=self.source.directories,
                unavailable=self.source.unavailable,
            )
        with self.source.accepted_inventory() as inventory:
            with self.assertRaisesRegex(
                ResearchTransferError, "root contract differs"
            ):
                validate_host_dependency_closure(
                    inventory,
                    research_root=self.source.root,
                    expected_source_uid=os.getuid() + 1,
                    expected_source_gid=os.getgid(),
                )

    def test_case_colliding_or_traversal_paths_are_rejected(self) -> None:
        first = self.source.historical_host[0]
        collision = InventoryMember(
            relative_path=first.relative_path.upper(),
            deployment_path=first.deployment_path.upper(),
            source_class=transfer.SOURCE_CLASS_HOST,
            bytes=first.bytes,
            sha256=first.sha256,
            mode="0600",
            ownership_role=transfer.OWNER_RESEARCH,
            deployment_mode="0600",
            deployment_ownership_role=transfer.OWNER_RESEARCH,
            dependency_role=first.dependency_role,
            authority_source=first.authority_source,
        )
        with self.assertRaises(ResearchTransferError):
            DeploymentInventory(
                members=tuple(sorted((first, collision))),
                historical_membership_sha256="0" * 64,
                auxiliary_membership_sha256="1" * 64,
                directories=(DirectoryContract(".", "0700"),),
                unavailable=(),
            )
        with self.assertRaises(ResearchTransferError):
            InventoryMember(
                relative_path="../escape",
                deployment_path="../escape",
                source_class=transfer.SOURCE_CLASS_HOST,
                bytes=1,
                sha256="0" * 64,
                mode="0600",
                ownership_role=transfer.OWNER_RESEARCH,
                deployment_mode="0600",
                deployment_ownership_role=transfer.OWNER_RESEARCH,
                dependency_role="bad",
                authority_source="bad",
            )
        with self.assertRaises(ResearchTransferError):
            DeploymentInventory(
                members=(first,),
                historical_membership_sha256="0" * 64,
                auxiliary_membership_sha256="1" * 64,
                directories=tuple(
                    sorted(
                        (
                            DirectoryContract(".", "0700"),
                            DirectoryContract("data", "0700"),
                            DirectoryContract("Data", "0700"),
                        )
                    )
                ),
                unavailable=(),
            )
        with self.assertRaises(ResearchTransferError):
            DeploymentInventory(
                members=(first,),
                historical_membership_sha256="0" * 64,
                auxiliary_membership_sha256="1" * 64,
                directories=tuple(
                    sorted(
                        (
                            DirectoryContract(".", "0700"),
                            DirectoryContract("data", "0700"),
                            DirectoryContract(first.relative_path, "0700"),
                        )
                    )
                ),
                unavailable=(),
            )

    def test_symlink_fifo_and_changed_mode_fail_without_blocking(self) -> None:
        member = self.source.historical_host[0]
        path = self.source.root / member.relative_path
        original = path.read_bytes()
        for kind in ("symlink", "fifo", "mode"):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(self.source.root / "data" / "record-01.bin")
                elif kind == "fifo":
                    os.mkfifo(path, 0o600)
                else:
                    path.write_bytes(original)
                    path.chmod(0o640)
                with self.source.accepted_inventory() as inventory:
                    with self.assertRaises(ResearchTransferError):
                        validate_host_dependency_closure(
                            inventory,
                            research_root=self.source.root,
                            expected_source_uid=os.getuid(),
                            expected_source_gid=os.getgid(),
                        )
                path.unlink()
                path.write_bytes(original)
                path.chmod(0o600)

    def test_existing_destination_is_never_replaced(self) -> None:
        existing = self.destination / "release"
        existing.mkdir(mode=0o700)
        marker = existing / "marker"
        marker.write_bytes(b"preserve")
        with self.source.accepted_inventory() as inventory:
            with self.assertRaises(ResearchTransferError):
                transfer._stage_and_promote_host_closure(
                    inventory,
                    research_root=self.source.root,
                    staging_parent=self.staging,
                    destination_parent=self.destination,
                    destination_name="release",
                    source_uid=os.getuid(),
                    source_gid=os.getgid(),
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                )
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_source_files_are_unchanged_after_staging(self) -> None:
        before = {
            item.relative_path: (
                (self.source.root / item.relative_path).stat().st_ino,
                (self.source.root / item.relative_path).stat().st_mtime_ns,
                _digest((self.source.root / item.relative_path).read_bytes()),
            )
            for item in (*self.source.historical_host, *self.source.auxiliary)
        }
        with self.source.accepted_inventory() as inventory:
            transfer._stage_and_promote_host_closure(
                inventory,
                research_root=self.source.root,
                staging_parent=self.staging,
                destination_parent=self.destination,
                destination_name="release",
                source_uid=os.getuid(),
                source_gid=os.getgid(),
                destination_uid=os.getuid(),
                destination_gid=os.getgid(),
            )
        after = {
            item.relative_path: (
                (self.source.root / item.relative_path).stat().st_ino,
                (self.source.root / item.relative_path).stat().st_mtime_ns,
                _digest((self.source.root / item.relative_path).read_bytes()),
            )
            for item in (*self.source.historical_host, *self.source.auxiliary)
        }
        self.assertEqual(before, after)

    def test_metadata_change_during_copy_fails_without_publication(self) -> None:
        target_member = sorted(self.source.auxiliary)[0]
        target = self.source.root / target_member.relative_path
        real_read = transfer.os.read
        changed = False

        def mutate_after_read(descriptor: int, size: int) -> bytes:
            nonlocal changed
            raw = real_read(descriptor, size)
            if raw and not changed:
                target.chmod(0o400)
                changed = True
            return raw

        try:
            with self.source.accepted_inventory() as inventory:
                with mock.patch.object(transfer.os, "read", mutate_after_read):
                    with self.assertRaises(ResearchTransferError):
                        transfer._stage_and_promote_host_closure(
                            inventory,
                            research_root=self.source.root,
                            staging_parent=self.staging,
                            destination_parent=self.destination,
                            destination_name="release",
                            source_uid=os.getuid(),
                            source_gid=os.getgid(),
                            destination_uid=os.getuid(),
                            destination_gid=os.getgid(),
                        )
        finally:
            target.chmod(0o600)
        self.assertTrue(changed)
        self.assertFalse((self.destination / "release").exists())


if __name__ == "__main__":
    unittest.main()

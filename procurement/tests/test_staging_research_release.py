"""Atomic complete-release tests using only tiny synthetic fixtures."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from procurement_os import staging_research_release as release
from procurement_os import staging_research_transfer as transfer
from procurement_os.staging_research_release import ResearchReleaseError
from procurement_os.staging_research_transfer import DirectoryContract, InventoryMember
from test_staging_research_transfer import _SyntheticClosure
from test_staging_source_bundle import _BundleFixture, _run


class StagingResearchReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.closure = _SyntheticClosure(self.root / "research-source")
        bundle_root = self.root / "bundle-fixture"
        bundle_root.mkdir(mode=0o700)
        self.bundle = _BundleFixture(bundle_root)
        self.staging = self.root / "staging"
        self.destination = self.root / "destination"
        self.staging.mkdir(mode=0o700)
        self.destination.mkdir(mode=0o710)
        self._bind_git_members()
        self.application = self.root / "application"
        self.application.mkdir(mode=0o755)
        self._materialize_application_members()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _bind_git_members(self) -> None:
        seed_root = self.closure.root / "private-research" / "v3-sources" / "sha256"
        seed_root.mkdir(parents=True, mode=0o700)
        for path in (
            self.closure.root / "private-research",
            self.closure.root / "private-research" / "v3-sources",
            seed_root,
        ):
            path.chmod(0o700)
        members: list[InventoryMember] = []
        for index, source_path in enumerate(
            ("seed.json", "seed.csv", "policy.json", "joint.json")
        ):
            raw = _run(
                self.bundle.repository,
                "show",
                f"{self.bundle.tip}:{source_path}",
            )
            digest = hashlib.sha256(raw).hexdigest()
            if index < 2:
                suffix = ".json" if index == 0 else ".csv"
                deployment_path = (
                    f"private-research/v3-sources/sha256/{digest}{suffix}"
                )
            else:
                deployment_path = source_path
            members.append(
                InventoryMember(
                    relative_path=source_path,
                    deployment_path=deployment_path,
                    source_class=transfer.SOURCE_CLASS_GIT,
                    bytes=len(raw),
                    sha256=digest,
                    mode="100644",
                    ownership_role=transfer.OWNER_SOURCE,
                    deployment_mode=("0600" if index < 2 else "0644"),
                    deployment_ownership_role=(
                        transfer.OWNER_RESEARCH
                        if index < 2
                        else transfer.OWNER_IMAGE
                    ),
                    dependency_role=f"Tracked fixture {index}",
                    authority_source="synthetic accepted commit",
                )
            )
        self.closure.tracked = members
        self.closure.directories = tuple(
            sorted(
                (
                    *self.closure.directories,
                    DirectoryContract("private-research", "0700"),
                    DirectoryContract("private-research/v3-sources", "0700"),
                    DirectoryContract(
                        "private-research/v3-sources/sha256", "0700"
                    ),
                )
            )
        )

    def _materialize_application_members(self) -> None:
        for member in self.closure.tracked[2:]:
            raw = _run(
                self.bundle.repository,
                "show",
                f"{self.bundle.tip}:{member.relative_path}",
            )
            path = self.application / member.deployment_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o644)

    def _stage(self, inventory):
        manifest = inventory.canonical_bytes()
        return release._stage_release_with_contract(
            inventory,
            research_root=self.closure.root,
            bundle_path=self.bundle.bundle,
            staging_parent=self.staging,
            destination_parent=self.destination,
            destination_name="accepted-release",
            source_uid=os.getuid(),
            source_gid=os.getgid(),
            destination_uid=os.getuid(),
            destination_gid=os.getgid(),
            bundle_contract=self.bundle.contract,
            manifest_bytes=len(manifest),
            manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )

    def test_complete_release_is_validated_then_published_once(self) -> None:
        observed_before_publish: list[set[str]] = []
        real_rename = release._rename_noreplace

        def checked_rename(source: Path, destination: Path) -> None:
            self.assertFalse(destination.exists())
            observed_before_publish.append({item.name for item in source.iterdir()})
            real_rename(source, destination)

        with self.closure.accepted_inventory() as inventory:
            with mock.patch.object(release, "_rename_noreplace", checked_rename):
                final, proof = self._stage(inventory)
            expected_manifest = inventory.canonical_bytes()
            validated = release._validate_release_with_contract(
                final,
                inventory=inventory,
                manifest_bytes=len(expected_manifest),
                manifest_sha256=hashlib.sha256(expected_manifest).hexdigest(),
                destination_uid=os.getuid(),
                destination_gid=os.getgid(),
                supervisor_uid=os.geteuid(),
                bundle_contract=self.bundle.contract,
            )

        self.assertEqual(
            observed_before_publish,
            [{"deployment-inventory.json", "payload", "source.git"}],
        )
        self.assertEqual(proof.tip, self.bundle.tip)
        self.assertEqual(validated, proof)
        self.assertEqual(final, self.destination / "accepted-release")
        self.assertEqual(
            (final / "deployment-inventory.json").read_bytes(), expected_manifest
        )
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o710)
        self.assertEqual(
            stat.S_IMODE((final / "deployment-inventory.json").stat().st_mode),
            0o440,
        )
        manifest = json.loads(expected_manifest)
        self.assertEqual(manifest["record_count"], 77)
        self.assertEqual(manifest["supersedes"]["record_count"], 75)

        payload = final / "payload"
        payload_files = tuple(path for path in payload.rglob("*") if path.is_file())
        self.assertEqual(len(payload_files), 75)
        for member in self.closure.tracked[:2]:
            path = payload / member.deployment_path
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_size, member.bytes)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), member.sha256)
        for member in self.closure.tracked[2:]:
            self.assertFalse((payload / member.deployment_path).exists())

        store = final / "source.git"
        for parent, directories, files in os.walk(store):
            self.assertEqual(stat.S_IMODE(Path(parent).stat().st_mode), 0o550)
            for name in directories:
                self.assertEqual(
                    stat.S_IMODE((Path(parent) / name).stat().st_mode), 0o550
                )
            for name in files:
                self.assertEqual(
                    stat.S_IMODE((Path(parent) / name).stat().st_mode), 0o440
                )
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_bundle_failure_leaves_no_partial_release(self) -> None:
        original = self.bundle.bundle.read_bytes()
        self.bundle.bundle.write_bytes(original[:-1])
        self.bundle.bundle.chmod(0o600)
        with self.closure.accepted_inventory() as inventory:
            with self.assertRaises(ResearchReleaseError):
                self._stage(inventory)
        self.assertFalse((self.destination / "accepted-release").exists())
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_every_git_commitment_is_revalidated_before_publication(self) -> None:
        original = list(self.closure.tracked)
        for index, member in enumerate(original):
            with self.subTest(member=member.relative_path):
                wrong = "0" * 64 if member.sha256 != "0" * 64 else "1" * 64
                self.closure.tracked = [
                    replace(item, sha256=wrong) if offset == index else item
                    for offset, item in enumerate(original)
                ]
                with self.closure.accepted_inventory() as inventory:
                    with self.assertRaisesRegex(
                        ResearchReleaseError, "Git-backed deployment member differs"
                    ):
                        self._stage(inventory)
                self.assertFalse((self.destination / "accepted-release").exists())
                self.assertEqual(list(self.staging.iterdir()), [])
        self.closure.tracked = original

    def test_cleanup_failure_occurs_before_irreversible_promotion(self) -> None:
        real_remove = release.source_bundle._remove_private_tree
        failed = False

        def fail_once(path: Path) -> None:
            nonlocal failed
            if path.name.startswith(".research-control.") and not failed:
                failed = True
                raise OSError("synthetic cleanup failure")
            real_remove(path)

        with self.closure.accepted_inventory() as inventory:
            with mock.patch.object(
                release.source_bundle, "_remove_private_tree", fail_once
            ):
                with self.assertRaises(OSError):
                    self._stage(inventory)
        self.assertTrue(failed)
        self.assertFalse((self.destination / "accepted-release").exists())
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_post_commit_durability_error_reports_published_state(self) -> None:
        real_fsync = release._fsync_directory

        def fail_destination(path: Path) -> None:
            if path == self.destination and (path / "accepted-release").exists():
                raise OSError("synthetic parent fsync failure")
            real_fsync(path)

        with self.closure.accepted_inventory() as inventory:
            with mock.patch.object(release, "_fsync_directory", fail_destination):
                with self.assertRaisesRegex(
                    ResearchReleaseError, "was published.*durability proof failed"
                ):
                    self._stage(inventory)
        self.assertTrue((self.destination / "accepted-release").is_dir())
        self.assertEqual(
            {item.name for item in (self.destination / "accepted-release").iterdir()},
            {"deployment-inventory.json", "payload", "source.git"},
        )

    def test_existing_release_is_never_replaced(self) -> None:
        existing = self.destination / "accepted-release"
        existing.mkdir(mode=0o700)
        marker = existing / "owner-marker"
        marker.write_text("preserve", encoding="ascii")
        with self.closure.accepted_inventory() as inventory:
            with self.assertRaisesRegex(ResearchReleaseError, "already exists"):
                self._stage(inventory)
        self.assertEqual(marker.read_text(encoding="ascii"), "preserve")
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_restart_revalidation_covers_payload_store_and_application(self) -> None:
        with self.closure.accepted_inventory() as inventory:
            final, expected_proof = self._stage(inventory)
            manifest = inventory.canonical_bytes()
            patches = (
                mock.patch.object(
                    release, "DEPLOYMENT_INVENTORY_BYTES", len(manifest)
                ),
                mock.patch.object(
                    release,
                    "DEPLOYMENT_INVENTORY_FILE_SHA256",
                    hashlib.sha256(manifest).hexdigest(),
                ),
                mock.patch.object(
                    release,
                    "parse_accepted_deployment_inventory",
                    return_value=inventory,
                ),
                mock.patch.object(
                    release.source_bundle, "_ACCEPTED_CONTRACT", self.bundle.contract
                ),
                mock.patch.object(
                    release, "_accepted_application_root", return_value=self.application
                ),
                mock.patch.object(release, "_APPLICATION_IMAGE_UID", os.getuid()),
                mock.patch.object(release, "_APPLICATION_IMAGE_GID", os.getgid()),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                parsed, proof = release.validate_accepted_research_release(
                    final,
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                    supervisor_uid=os.geteuid(),
                )
            self.assertEqual(parsed, inventory)
            self.assertEqual(proof, expected_proof)

    def test_application_member_or_directory_drift_fails_closed(self) -> None:
        with self.closure.accepted_inventory() as inventory:
            release._validate_application_members(
                inventory,
                application_root=self.application,
                expected_uid=os.getuid(),
                expected_gid=os.getgid(),
            )
            self.application.chmod(0o700)
            with self.assertRaisesRegex(
                ResearchReleaseError, "image root contract differs"
            ):
                release._validate_application_members(
                    inventory,
                    application_root=self.application,
                    expected_uid=os.getuid(),
                    expected_gid=os.getgid(),
                )
            self.application.chmod(0o755)
            target = self.application / self.closure.tracked[2].deployment_path
            original = target.read_bytes()
            target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            target.chmod(0o644)
            with self.assertRaisesRegex(ResearchReleaseError, "member bytes differ"):
                release._validate_application_members(
                    inventory,
                    application_root=self.application,
                    expected_uid=os.getuid(),
                    expected_gid=os.getgid(),
                )
            target.write_bytes(original)
            target.chmod(0o644)

        permissive = self.root / "permissive-image"
        permissive.mkdir(mode=0o755)
        nested = permissive / "procurement"
        nested.mkdir(mode=0o700)
        nested.chmod(0o777)
        (nested / "policy.json").write_bytes(b"policy")
        (nested / "policy.json").chmod(0o644)
        with self.assertRaisesRegex(
            ResearchReleaseError, "directory contract differs"
        ):
            release._open_application_member(
                permissive,
                "procurement/policy.json",
                expected_uid=os.getuid(),
                expected_gid=os.getgid(),
            )

    def test_public_stage_rechecks_image_at_publication_boundary(self) -> None:
        with self.closure.accepted_inventory() as inventory:
            target = self.application / self.closure.tracked[2].deployment_path
            manifest = inventory.canonical_bytes()
            real_copy = release._copy_member
            changed = False

            def copy_then_change_image(*args, **kwargs) -> None:
                nonlocal changed
                real_copy(*args, **kwargs)
                if not changed:
                    target.chmod(0o600)
                    changed = True

            with (
                mock.patch.object(
                    release, "DEPLOYMENT_INVENTORY_BYTES", len(manifest)
                ),
                mock.patch.object(
                    release,
                    "DEPLOYMENT_INVENTORY_FILE_SHA256",
                    hashlib.sha256(manifest).hexdigest(),
                ),
                mock.patch.object(
                    release.source_bundle,
                    "_ACCEPTED_CONTRACT",
                    self.bundle.contract,
                ),
                mock.patch.object(
                    release,
                    "_accepted_application_root",
                    return_value=self.application,
                ),
                mock.patch.object(release, "_APPLICATION_IMAGE_UID", os.getuid()),
                mock.patch.object(release, "_APPLICATION_IMAGE_GID", os.getgid()),
                mock.patch.object(release, "_copy_member", copy_then_change_image),
            ):
                with self.assertRaisesRegex(
                    ResearchReleaseError, "member metadata differs"
                ):
                    release.stage_accepted_research_release(
                        inventory,
                        research_root=self.closure.root,
                        bundle_path=self.bundle.bundle,
                        staging_parent=self.staging,
                        destination_parent=self.destination,
                        destination_name="accepted-release",
                        source_uid=os.getuid(),
                        source_gid=os.getgid(),
                        destination_uid=os.getuid(),
                        destination_gid=os.getgid(),
                    )
        self.assertTrue(changed)
        self.assertFalse((self.destination / "accepted-release").exists())
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_host_source_is_rechecked_after_assembly(self) -> None:
        real_copy = release._copy_member
        changed = False
        target: Path | None = None
        original: bytes | None = None

        def copy_then_change_source(*args, **kwargs) -> None:
            nonlocal changed, target, original
            real_copy(*args, **kwargs)
            if not changed:
                source_root = args[0]
                member = args[1]
                target = source_root / member.relative_path
                original = target.read_bytes()
                target.write_bytes(original + b"drift")
                target.chmod(0o600)
                changed = True

        try:
            with self.closure.accepted_inventory() as inventory:
                with mock.patch.object(
                    release, "_copy_member", copy_then_change_source
                ):
                    with self.assertRaisesRegex(
                        ResearchReleaseError, "dependency closure failed"
                    ):
                        self._stage(inventory)
        finally:
            if target is not None and original is not None:
                target.write_bytes(original)
                target.chmod(0o600)
        self.assertTrue(changed)
        self.assertFalse((self.destination / "accepted-release").exists())
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_bundle_source_is_rechecked_after_assembly(self) -> None:
        original = self.bundle.bundle.read_bytes()
        real_build = release.source_bundle._build_unpublished_with_contract
        changed = False

        def build_then_change_bundle(*args, **kwargs):
            nonlocal changed
            proof = real_build(*args, **kwargs)
            self.bundle.bundle.write_bytes(original[:-1])
            self.bundle.bundle.chmod(0o600)
            changed = True
            return proof

        try:
            with self.closure.accepted_inventory() as inventory:
                with mock.patch.object(
                    release.source_bundle,
                    "_build_unpublished_with_contract",
                    build_then_change_bundle,
                ):
                    with self.assertRaisesRegex(
                        ResearchReleaseError, "source bundle"
                    ):
                        self._stage(inventory)
        finally:
            self.bundle.bundle.write_bytes(original)
            self.bundle.bundle.chmod(0o600)
        self.assertTrue(changed)
        self.assertFalse((self.destination / "accepted-release").exists())
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_payload_hardlink_and_source_store_extra_fail_revalidation(self) -> None:
        with self.closure.accepted_inventory() as inventory:
            final, _proof = self._stage(inventory)
            manifest = inventory.canonical_bytes()
            first_host = next(
                item
                for item in inventory.members
                if item.source_class == transfer.SOURCE_CLASS_HOST
            )
            outside = self.root / "outside-hardlink"
            os.link(final / "payload" / first_host.deployment_path, outside)
            with self.assertRaisesRegex(ResearchReleaseError, "metadata differs"):
                release._validate_release_with_contract(
                    final,
                    inventory=inventory,
                    manifest_bytes=len(manifest),
                    manifest_sha256=hashlib.sha256(manifest).hexdigest(),
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                    supervisor_uid=os.geteuid(),
                    bundle_contract=self.bundle.contract,
                )
            outside.unlink()

            store = final / "source.git"
            store.chmod(0o750)
            extra = store / "unrelated.private"
            extra.write_bytes(b"unrelated")
            extra.chmod(0o440)
            store.chmod(0o550)
            with self.assertRaisesRegex(
                ResearchReleaseError, "filesystem layout differs"
            ):
                release._validate_release_with_contract(
                    final,
                    inventory=inventory,
                    manifest_bytes=len(manifest),
                    manifest_sha256=hashlib.sha256(manifest).hexdigest(),
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                    supervisor_uid=os.geteuid(),
                    bundle_contract=self.bundle.contract,
                )


if __name__ == "__main__":
    unittest.main()

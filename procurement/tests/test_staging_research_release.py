"""Atomic complete-release tests using only tiny synthetic fixtures."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
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

    def _recovery_parent(self, name: str) -> Path:
        parent = self.root / name
        parent.mkdir(mode=0o700)
        return parent

    @staticmethod
    def _crash_directory(parent: Path, prefix: str) -> Path:
        value = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        value.chmod(0o700)
        return value

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
                supervisor_gid=os.getegid(),
                bundle_contract=self.bundle.contract,
                validation_parent=self.staging,
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

    def test_recovery_removes_only_proven_partial_release_control_and_validation(self) -> None:
        staging_recovery = self._recovery_parent("recovery-stage-pass")
        partial_release = self._crash_directory(
            staging_recovery, ".research-release."
        )
        (partial_release / "payload").mkdir(mode=0o700)
        (partial_release / "source.git").mkdir(mode=0o700)
        (partial_release / "source.git" / "HEAD").write_bytes(b"partial")
        (partial_release / "source.git" / "HEAD").chmod(0o600)
        control = self._crash_directory(staging_recovery, ".research-control.")
        git_runtime = self._crash_directory(control, ".git-runtime.")
        (git_runtime / "accepted.bundle").write_bytes(b"partial")
        (git_runtime / "accepted.bundle").chmod(0o600)
        validation_recovery = self._recovery_parent("recovery-validation-pass")
        validation = self._crash_directory(
            validation_recovery, ".research-release-validation."
        )
        (validation / "store").mkdir(mode=0o700)

        with self.closure.accepted_inventory() as inventory:
            self.assertEqual(
                release.reconcile_unpublished_research_staging(
                    inventory,
                    staging_parent=staging_recovery,
                    supervisor_uid=os.geteuid(),
                    supervisor_gid=os.getegid(),
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                ),
                2,
            )
            self.assertEqual(
                release.reconcile_unpublished_research_staging(
                    inventory,
                    staging_parent=validation_recovery,
                    supervisor_uid=os.geteuid(),
                    supervisor_gid=os.getegid(),
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                ),
                1,
            )
        self.assertEqual(list(staging_recovery.iterdir()), [])
        self.assertEqual(list(validation_recovery.iterdir()), [])

    def test_recovery_refuses_wrong_phase_layouts_and_duplicate_candidates(self) -> None:
        cases = (
            "wrong-type",
            "nested-extra",
            "source-extra",
            "loose-object",
            "pack-stems",
            "pack-keep",
            "multiple-install-pairs",
            "detached-install-pair",
            "control-extra",
            "control-phase",
            "duplicates",
        )
        with self.closure.accepted_inventory() as inventory:
            for case in cases:
                with self.subTest(case=case):
                    recovery = self._recovery_parent(f"recovery-phase-{case}")
                    if case == "wrong-type":
                        candidate = self._crash_directory(
                            recovery, ".research-release-validation."
                        )
                        (candidate / "members").write_bytes(b"not-a-directory")
                        (candidate / "members").chmod(0o600)
                    elif case == "nested-extra":
                        candidate = self._crash_directory(
                            recovery, ".research-release-validation."
                        )
                        store = candidate / "store"
                        store.mkdir(mode=0o700)
                        (store / "unrelated.private").write_bytes(b"preserve")
                        (store / "unrelated.private").chmod(0o600)
                    elif case == "source-extra":
                        candidate = self._crash_directory(
                            recovery, ".research-release."
                        )
                        source = candidate / "source.git"
                        pack = source / "objects" / "pack"
                        pack.mkdir(parents=True, mode=0o700)
                        for path in (source, source / "objects", pack):
                            path.chmod(0o700)
                        (pack / "unrelated.private").write_bytes(b"preserve")
                        (pack / "unrelated.private").chmod(0o600)
                    elif case in {
                        "loose-object",
                        "pack-stems",
                        "pack-keep",
                        "multiple-install-pairs",
                        "detached-install-pair",
                    }:
                        candidate = self._crash_directory(
                            recovery, ".research-release."
                        )
                        source = candidate / "source.git"
                        objects = source / "objects"
                        objects.mkdir(parents=True, mode=0o700)
                        source.chmod(0o700)
                        objects.chmod(0o700)
                        if case == "loose-object":
                            loose = objects / "ab"
                            loose.mkdir(mode=0o700)
                            path = loose / ("c" * 38)
                            path.write_bytes(b"preserve")
                            path.chmod(0o400)
                        elif case in {"pack-stems", "pack-keep"}:
                            pack = objects / "pack"
                            pack.mkdir(mode=0o700)
                            if case == "pack-stems":
                                names = (
                                    f"pack-{'a' * 40}.pack",
                                    f"pack-{'b' * 40}.pack",
                                )
                            else:
                                names = (f"pack-{'a' * 40}.keep",)
                            for name in names:
                                path = pack / name
                                path.write_bytes(b"preserve")
                                path.chmod(0o400)
                        else:
                            pack = objects / "pack"
                            pack.mkdir(mode=0o700)
                            kinds = (
                                ("pack", "idx")
                                if case == "multiple-install-pairs"
                                else ("pack",)
                            )
                            for kind in kinds:
                                temporary = pack / f"tmp_{kind}_ab12cd"
                                final = pack / f"pack-{'a' * 40}.{kind}"
                                temporary.write_bytes(kind.encode("ascii"))
                                temporary.chmod(0o400)
                                if case == "multiple-install-pairs":
                                    os.link(temporary, final)
                                else:
                                    final.write_bytes(b"different")
                                    final.chmod(0o400)
                    elif case == "control-extra":
                        candidate = self._crash_directory(
                            recovery, ".research-control."
                        )
                        extract = candidate / "extract"
                        home = extract / "home"
                        home.mkdir(parents=True, mode=0o700)
                        extract.chmod(0o700)
                        home.chmod(0o700)
                        (home / "unrelated.private").write_bytes(b"preserve")
                        (home / "unrelated.private").chmod(0o600)
                    elif case == "control-phase":
                        candidate = self._crash_directory(
                            recovery, ".research-control."
                        )
                        (candidate / "extract").mkdir(mode=0o700)
                        (candidate / "accepted-source-recheck.bundle").write_bytes(
                            b"partial"
                        )
                        (candidate / "accepted-source-recheck.bundle").chmod(0o600)
                    else:
                        candidate = self._crash_directory(
                            recovery, ".research-control."
                        )
                        self._crash_directory(recovery, ".research-control.")
                    with self.assertRaises(ResearchReleaseError):
                        release.reconcile_unpublished_research_staging(
                            inventory,
                            staging_parent=recovery,
                            supervisor_uid=os.geteuid(),
                            supervisor_gid=os.getegid(),
                            destination_uid=os.getuid(),
                            destination_gid=os.getgid(),
                        )
                    self.assertTrue(candidate.is_dir())
                    shutil.rmtree(recovery)

    def test_recovery_accepts_exact_git_install_links_and_temporary_names(self) -> None:
        with self.closure.accepted_inventory() as inventory:
            for kind in ("pack", "idx", "rev"):
                with self.subTest(kind=kind):
                    recovery = self._recovery_parent(f"recovery-git-install-{kind}")
                    candidate = self._crash_directory(
                        recovery, ".research-release."
                    )
                    manifest = candidate / "deployment-inventory.json"
                    manifest.write_bytes(b"partial")
                    manifest.chmod(0o400)
                    source = candidate / "source.git"
                    pack = source / "objects" / "pack"
                    pack.mkdir(parents=True, mode=0o700)
                    for path in (source, source / "objects", pack):
                        path.chmod(0o700)
                    temporary = pack / f"tmp_{kind}_ab12cd"
                    temporary.write_bytes(kind.encode("ascii"))
                    temporary.chmod(0o400)
                    os.link(
                        temporary,
                        pack / f"pack-{'a' * 40}.{kind}",
                    )
                    self.assertEqual(
                        release.reconcile_unpublished_research_staging(
                            inventory,
                            staging_parent=recovery,
                            supervisor_uid=os.geteuid(),
                            supervisor_gid=os.getegid(),
                            destination_uid=os.getuid(),
                            destination_gid=os.getgid(),
                        ),
                        1,
                    )
                    self.assertEqual(list(recovery.iterdir()), [])

    def test_recovery_handles_only_exact_git_init_transients(self) -> None:
        with self.closure.accepted_inventory() as inventory:
            for case in ("config-mode-700", "config-mode-744", "filemode-probe"):
                with self.subTest(case=case):
                    recovery = self._recovery_parent(f"recovery-git-{case}")
                    candidate = self._crash_directory(
                        recovery, ".research-release."
                    )
                    source = candidate / "source.git"
                    source.mkdir(mode=0o700)
                    if case.startswith("config-mode"):
                        transient = source / "config"
                        transient.write_bytes(b"partial")
                        transient.chmod(
                            0o700 if case == "config-mode-700" else 0o744
                        )
                    else:
                        transient = source / "tAb12Cd"
                        transient.touch(mode=0o600)
                    self.assertEqual(
                        release.reconcile_unpublished_research_staging(
                            inventory,
                            staging_parent=recovery,
                            supervisor_uid=os.geteuid(),
                            supervisor_gid=os.getegid(),
                            destination_uid=os.getuid(),
                            destination_gid=os.getgid(),
                        ),
                        1,
                    )

            for case in (
                "head-mode-700",
                "head-mode-744",
                "probe-content",
                "probe-multiple",
                "probe-name",
            ):
                with self.subTest(case=case):
                    recovery = self._recovery_parent(f"recovery-git-{case}")
                    candidate = self._crash_directory(
                        recovery, ".research-release."
                    )
                    source = candidate / "source.git"
                    source.mkdir(mode=0o700)
                    if case.startswith("head-mode"):
                        invalid = source / "HEAD"
                        invalid.write_bytes(b"partial")
                        invalid.chmod(0o700 if case == "head-mode-700" else 0o744)
                    elif case == "probe-content":
                        invalid = source / "tAb12Cd"
                        invalid.write_bytes(b"x")
                        invalid.chmod(0o600)
                    elif case == "probe-multiple":
                        invalid = source / "tAb12Cd"
                        invalid.touch(mode=0o600)
                        (source / "tEf34Gh").touch(mode=0o600)
                    else:
                        invalid = source / "tAb12C_"
                        invalid.touch(mode=0o600)
                    with self.assertRaises(ResearchReleaseError):
                        release.reconcile_unpublished_research_staging(
                            inventory,
                            staging_parent=recovery,
                            supervisor_uid=os.geteuid(),
                            supervisor_gid=os.getegid(),
                            destination_uid=os.getuid(),
                            destination_gid=os.getgid(),
                        )
                    self.assertTrue(candidate.is_dir())
                    shutil.rmtree(recovery)

    def test_recovery_rejects_cross_product_owner_mode_states(self) -> None:
        supervisor_uid = os.geteuid()
        supervisor_gid = os.getegid()
        destination_uid = supervisor_uid + 101
        destination_gid = supervisor_gid + 102
        cases = ("payload", "git-pack", "git-temp", "git-rev", "git-lock")
        with self.closure.accepted_inventory() as inventory:
            for case in cases:
                with self.subTest(case=case):
                    recovery = self._recovery_parent(f"recovery-state-{case}")
                    candidate = self._crash_directory(
                        recovery, ".research-release."
                    )
                    if case == "payload":
                        payload = candidate / "payload"
                        payload.mkdir(mode=0o700)
                        target = payload / "data"
                        target.mkdir(mode=0o700)
                        fake_mode = stat.S_IFDIR | 0o755
                        fake_uid = destination_uid
                        fake_gid = destination_gid
                    elif case != "git-lock":
                        source = candidate / "source.git"
                        pack = source / "objects" / "pack"
                        pack.mkdir(parents=True, mode=0o700)
                        for path in (source, source / "objects", pack):
                            path.chmod(0o700)
                        if case == "git-pack":
                            target = pack / f"pack-{'a' * 40}.pack"
                            fake_permissions = 0o444
                        elif case == "git-temp":
                            target = pack / "tmp_pack_ab12cd"
                            fake_permissions = 0o440
                        else:
                            target = pack / f"pack-{'a' * 40}.rev"
                            fake_permissions = 0o440
                        target.write_bytes(b"partial")
                        target.chmod(0o444)
                        fake_mode = stat.S_IFREG | fake_permissions
                        fake_uid = supervisor_uid
                        fake_gid = destination_gid
                    else:
                        source = candidate / "source.git"
                        source.mkdir(mode=0o700)
                        target = source / "HEAD.lock"
                        target.write_bytes(b"partial")
                        target.chmod(0o600)
                        fake_mode = stat.S_IFREG | 0o440
                        fake_uid = supervisor_uid
                        fake_gid = destination_gid
                    real_stat = Path.stat

                    def cross_product_stat(path: Path, *args, **kwargs):
                        info = real_stat(path, *args, **kwargs)
                        if path == target:
                            values = list(info)
                            values[0] = fake_mode
                            values[4] = fake_uid
                            values[5] = fake_gid
                            return os.stat_result(values)
                        return info

                    with mock.patch.object(Path, "stat", cross_product_stat):
                        with self.assertRaisesRegex(
                            ResearchReleaseError, "metadata differs"
                        ):
                            release.reconcile_unpublished_research_staging(
                                inventory,
                                staging_parent=recovery,
                                supervisor_uid=supervisor_uid,
                                supervisor_gid=supervisor_gid,
                                destination_uid=destination_uid,
                                destination_gid=destination_gid,
                            )
                    self.assertTrue(candidate.is_dir())
                    shutil.rmtree(recovery)

    def test_recovery_accepts_release_root_between_chmod_and_chown(self) -> None:
        recovery = self._recovery_parent("recovery-release-chmod")
        candidate = self._crash_directory(recovery, ".research-release.")
        candidate.chmod(0o710)
        with self.closure.accepted_inventory() as inventory:
            self.assertEqual(
                release.reconcile_unpublished_research_staging(
                    inventory,
                    staging_parent=recovery,
                    supervisor_uid=os.geteuid(),
                    supervisor_gid=os.getegid(),
                    destination_uid=os.geteuid() + 101,
                    destination_gid=os.getegid() + 102,
                ),
                1,
            )

    def test_recovery_retries_after_cleanup_normalized_a_0755_directory(self) -> None:
        original_directories = self.closure.directories
        self.closure.directories = tuple(
            sorted((*original_directories, DirectoryContract("transitional", "0755")))
        )
        try:
            recovery = self._recovery_parent("recovery-cleanup-retry")
            candidate = self._crash_directory(recovery, ".research-release.")
            payload = candidate / "payload"
            payload.mkdir(mode=0o700)
            transitional = payload / "transitional"
            transitional.mkdir(mode=0o755)
            transitional.chmod(0o755)
            with self.closure.accepted_inventory() as inventory:
                with mock.patch.object(
                    release.source_bundle.shutil,
                    "rmtree",
                    side_effect=OSError("injected cleanup failure"),
                ):
                    with self.assertRaisesRegex(
                        ResearchReleaseError, "staging cleanup failed"
                    ):
                        release.reconcile_unpublished_research_staging(
                            inventory,
                            staging_parent=recovery,
                            supervisor_uid=os.geteuid(),
                            supervisor_gid=os.getegid(),
                            destination_uid=os.getuid(),
                            destination_gid=os.getgid(),
                        )
                self.assertEqual(stat.S_IMODE(transitional.stat().st_mode), 0o700)
                self.assertEqual(
                    release.reconcile_unpublished_research_staging(
                        inventory,
                        staging_parent=recovery,
                        supervisor_uid=os.geteuid(),
                        supervisor_gid=os.getegid(),
                        destination_uid=os.getuid(),
                        destination_gid=os.getgid(),
                    ),
                    1,
                )
        finally:
            self.closure.directories = original_directories

    def test_recovery_refuses_unknown_or_unsafe_entries_without_deleting_valid_state(self) -> None:
        cases = ("unknown", "symlink", "fifo", "hardlink", "mode", "owner")
        with self.closure.accepted_inventory() as inventory:
            for case in cases:
                with self.subTest(case=case):
                    recovery = self._recovery_parent(f"recovery-{case}")
                    retained = self._crash_directory(
                        recovery, ".research-control."
                    )
                    candidate = self._crash_directory(
                        recovery, ".research-release."
                    )
                    patcher = None
                    outside: Path | None = None
                    if case == "unknown":
                        (recovery / "owner-note").write_text(
                            "preserve", encoding="ascii"
                        )
                    elif case == "symlink":
                        (candidate / "payload").symlink_to(self.root)
                    elif case == "fifo":
                        os.mkfifo(candidate / "deployment-inventory.json", 0o600)
                    elif case == "hardlink":
                        outside = self.root / f"outside-{case}"
                        outside.write_bytes(b"preserve")
                        outside.chmod(0o600)
                        os.link(outside, candidate / "deployment-inventory.json")
                    elif case == "mode":
                        candidate.chmod(0o777)
                    else:
                        real_stat = Path.stat

                        def changed_owner(path: Path, *args, **kwargs):
                            info = real_stat(path, *args, **kwargs)
                            if path == candidate:
                                values = list(info)
                                values[4] = info.st_uid + 1
                                return os.stat_result(values)
                            return info

                        patcher = mock.patch.object(Path, "stat", changed_owner)
                        patcher.start()
                    try:
                        with self.assertRaises(ResearchReleaseError):
                            release.reconcile_unpublished_research_staging(
                                inventory,
                                staging_parent=recovery,
                                supervisor_uid=os.geteuid(),
                                supervisor_gid=os.getegid(),
                                destination_uid=os.getuid(),
                                destination_gid=os.getgid(),
                            )
                    finally:
                        if patcher is not None:
                            patcher.stop()
                    self.assertTrue(retained.is_dir())
                    self.assertTrue(candidate.exists() or candidate.is_symlink())
                    if outside is not None:
                        self.assertEqual(outside.read_bytes(), b"preserve")
                    shutil.rmtree(recovery)
                    if outside is not None:
                        outside.unlink()

    def test_recovery_never_touches_published_release_parent(self) -> None:
        published = self.destination / "accepted-release"
        published.mkdir(mode=0o700)
        marker = published / "owner-marker"
        marker.write_text("preserve", encoding="ascii")
        with self.closure.accepted_inventory() as inventory:
            with self.assertRaises(ResearchReleaseError):
                release.reconcile_unpublished_research_staging(
                    inventory,
                    staging_parent=self.destination,
                    supervisor_uid=os.geteuid(),
                    supervisor_gid=os.getegid(),
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                )
        self.assertEqual(marker.read_text(encoding="ascii"), "preserve")

        recovery = self._recovery_parent("recovery-published")
        self._crash_directory(recovery, ".research-control.")
        with self.closure.accepted_inventory() as inventory:
            self.assertEqual(
                release.reconcile_unpublished_research_staging(
                    inventory,
                    staging_parent=recovery,
                    supervisor_uid=os.geteuid(),
                    supervisor_gid=os.getegid(),
                    destination_uid=os.getuid(),
                    destination_gid=os.getgid(),
                ),
                1,
            )
        self.assertEqual(marker.read_text(encoding="ascii"), "preserve")

    def test_restart_revalidation_covers_payload_store_and_application(self) -> None:
        with self.closure.accepted_inventory() as inventory:
            final, expected_proof = self._stage(inventory)
            stale = self._crash_directory(
                self.staging, ".research-release-validation."
            )
            (stale / "store").mkdir(mode=0o700)
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
                    supervisor_gid=os.getegid(),
                    validation_parent=self.staging,
                )
            self.assertEqual(parsed, inventory)
            self.assertEqual(proof, expected_proof)
            self.assertEqual(list(self.staging.iterdir()), [])

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
        self._crash_directory(self.staging, ".research-control.")
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
                    supervisor_gid=os.getegid(),
                    bundle_contract=self.bundle.contract,
                    validation_parent=self.staging,
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
                    supervisor_gid=os.getegid(),
                    bundle_contract=self.bundle.contract,
                    validation_parent=self.staging,
                )


if __name__ == "__main__":
    unittest.main()

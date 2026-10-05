"""Focused state-machine tests for accepted research release publication."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import fcntl
import hashlib
from io import StringIO
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

from procurement_os import staging_research_materializer as materializer


class _Inventory:
    def canonical_bytes(self) -> bytes:
        return b"exact accepted inventory"


class StagingResearchMaterializerTests(unittest.TestCase):
    def _fixture(self, root: Path) -> materializer._MaterializerContract:
        volume = root / "data"
        validation = root / "validation"
        ingress = root / "ingress"
        volume.mkdir(mode=0o755)
        validation.mkdir(mode=0o700)
        ingress.mkdir(mode=0o700)
        return materializer._MaterializerContract(
            volume_root=volume,
            transfer_root=volume / "transfer",
            release_root=volume / "research-release",
            validation_parent=validation,
            research_root=ingress / "research",
            inventory_path=ingress / "deployment-inventory.json",
            bundle_path=ingress / "accepted.bundle",
            source_uid=os.geteuid(),
            source_gid=os.getegid(),
            supervisor_uid=os.geteuid(),
            supervisor_gid=os.getegid(),
            destination_uid=os.geteuid(),
            destination_gid=os.getegid(),
            validate_host_identity=False,
            validate_mounts=False,
        )

    def _successful_stage(self, contract, **_kwargs):
        contract.release_root.mkdir(mode=0o710)
        contract.release_root.chmod(0o710)
        return contract.release_root, object()

    def _common_patches(self, contract, inventory):
        return (
            patch.object(materializer, "_load_ingress", return_value=inventory),
            patch.object(materializer, "_reconcile"),
            patch.object(
                materializer,
                "_validate_release",
                return_value=inventory,
            ),
            patch.object(
                materializer,
                "stage_accepted_research_release",
                side_effect=lambda _inventory, **kwargs: self._successful_stage(
                    contract, **kwargs
                ),
            ),
        )

    def test_empty_volume_publishes_once_and_exact_replay_is_read_only(self):
        inventory = _Inventory()
        with TemporaryDirectory() as temporary:
            contract = self._fixture(Path(temporary))
            load, reconcile, validate, stage = self._common_patches(
                contract, inventory
            )
            with (
                load as load_mock,
                reconcile as reconcile_mock,
                validate,
                stage as stage_mock,
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            self.assertEqual(load_mock.call_count, 1)
            self.assertEqual(stage_mock.call_count, 1)
            self.assertEqual(
                stage_mock.call_args,
                call(
                    inventory,
                    research_root=contract.research_root,
                    bundle_path=contract.bundle_path,
                    staging_parent=contract.transfer_root,
                    destination_parent=contract.volume_root,
                    destination_name=contract.release_root.name,
                    source_uid=contract.source_uid,
                    source_gid=contract.source_gid,
                    destination_uid=contract.destination_uid,
                    destination_gid=contract.destination_gid,
                ),
            )
            self.assertEqual(
                reconcile_mock.call_args_list,
                [
                    call(inventory, contract, parent=contract.validation_parent),
                    call(inventory, contract, parent=contract.transfer_root),
                    call(inventory, contract, parent=contract.transfer_root),
                ],
            )
            self.assertEqual(
                {item.name for item in contract.volume_root.iterdir()},
                {"research-release"},
            )
            self.assertFalse(contract.transfer_root.exists())
            volume_info = contract.volume_root.stat(follow_symlinks=False)
            self.assertEqual(
                (
                    stat.S_IMODE(volume_info.st_mode),
                    volume_info.st_uid,
                    volume_info.st_gid,
                ),
                (0o755, os.geteuid(), os.getegid()),
            )
            before = (
                volume_info.st_dev,
                volume_info.st_ino,
                volume_info.st_mode,
                volume_info.st_uid,
                volume_info.st_gid,
                volume_info.st_mtime_ns,
            )
            with (
                patch.object(
                    materializer,
                    "_load_ingress",
                    side_effect=AssertionError("replay read ingress"),
                ),
                patch.object(
                    materializer,
                    "stage_accepted_research_release",
                    side_effect=AssertionError("replay staged"),
                ),
                patch.object(
                    materializer,
                    "_validate_release",
                    return_value=inventory,
                ) as replay_validate,
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            after_info = contract.volume_root.stat(follow_symlinks=False)
            self.assertEqual(
                before,
                (
                    after_info.st_dev,
                    after_info.st_ino,
                    after_info.st_mode,
                    after_info.st_uid,
                    after_info.st_gid,
                    after_info.st_mtime_ns,
                ),
            )
            self.assertEqual(replay_validate.call_count, 2)

    def test_exact_post_publish_crash_state_recovers_without_ingress(self):
        inventory = _Inventory()
        with TemporaryDirectory() as temporary:
            contract = self._fixture(Path(temporary))
            contract.transfer_root.mkdir(mode=0o700)
            contract.release_root.mkdir(mode=0o710)
            contract.volume_root.chmod(0o710)
            with (
                patch.object(
                    materializer,
                    "_load_ingress",
                    side_effect=AssertionError("recovery read ingress"),
                ),
                patch.object(materializer, "_reconcile") as reconcile,
                patch.object(
                    materializer,
                    "_validate_release",
                    return_value=inventory,
                ),
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            reconcile.assert_called_once_with(
                inventory,
                contract,
                parent=contract.transfer_root,
            )
            self.assertFalse(contract.transfer_root.exists())
            self.assertEqual(
                stat.S_IMODE(contract.volume_root.stat().st_mode),
                0o755,
            )

        with TemporaryDirectory() as temporary:
            contract = self._fixture(Path(temporary))
            contract.transfer_root.mkdir(mode=0o700)
            contract.release_root.mkdir(mode=0o710)
            contract.volume_root.chmod(0o710)
            real_fsync = os.fsync
            fsync_calls = 0

            def fail_after_marker_removal(descriptor):
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 2:
                    raise OSError("injected post-rmdir durability failure")
                return real_fsync(descriptor)

            with (
                patch.object(materializer, "_load_ingress") as load,
                patch.object(materializer, "_reconcile"),
                patch.object(
                    materializer,
                    "_validate_release",
                    return_value=inventory,
                ),
                patch.object(materializer.os, "fsync", fail_after_marker_removal),
                self.assertRaises(materializer.ResearchMaterializerError),
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            load.assert_not_called()
            self.assertFalse(contract.transfer_root.exists())
            self.assertTrue(contract.release_root.is_dir())

            with (
                patch.object(
                    materializer,
                    "_validate_release",
                    return_value=inventory,
                ),
                patch.object(materializer.os, "fsync", wraps=real_fsync) as retry_fsync,
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            self.assertEqual(retry_fsync.call_count, 1)

    def test_ambiguous_post_publish_failure_is_observed_not_resubmitted(self):
        inventory = _Inventory()
        with TemporaryDirectory() as temporary:
            contract = self._fixture(Path(temporary))

            def publish_then_fail(_inventory, **_kwargs):
                contract.release_root.mkdir(mode=0o710)
                raise OSError("private sentinel after atomic rename")

            with (
                patch.object(materializer, "_load_ingress", return_value=inventory),
                patch.object(materializer, "_reconcile"),
                patch.object(
                    materializer,
                    "_validate_release",
                    return_value=inventory,
                ) as validate,
                patch.object(
                    materializer,
                    "stage_accepted_research_release",
                    side_effect=publish_then_fail,
                ) as stage,
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            self.assertEqual(stage.call_count, 1)
            self.assertGreaterEqual(validate.call_count, 3)
            self.assertTrue(contract.release_root.is_dir())
            self.assertFalse(contract.transfer_root.exists())

    def test_pre_publish_failure_restores_parent_and_retains_marker(self):
        inventory = _Inventory()
        for case in ("current-attempt", "crash-retry"):
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                contract = self._fixture(Path(temporary))
                load_result = inventory
                if case == "crash-retry":
                    contract.transfer_root.mkdir(mode=0o700)
                    contract.volume_root.chmod(0o710)
                    load_result = materializer.ResearchMaterializerError(
                        "private source changed"
                    )
                with (
                    patch.object(
                        materializer,
                        "_load_ingress",
                        return_value=load_result,
                        side_effect=(
                            load_result
                            if isinstance(
                                load_result, materializer.ResearchMaterializerError
                            )
                            else None
                        ),
                    ),
                    patch.object(materializer, "_reconcile"),
                    patch.object(
                        materializer,
                        "stage_accepted_research_release",
                        side_effect=OSError("private source changed"),
                    ),
                    self.assertRaises(materializer.ResearchMaterializerError),
                ):
                    materializer.materialize_accepted_research_release(
                        contract=contract
                    )
                self.assertTrue(contract.transfer_root.is_dir())
                self.assertFalse(contract.release_root.exists())
                volume_info = contract.volume_root.stat(follow_symlinks=False)
                self.assertEqual(
                    (
                        stat.S_IMODE(volume_info.st_mode),
                        volume_info.st_uid,
                        volume_info.st_gid,
                    ),
                    (0o755, os.geteuid(), os.getegid()),
                )

    def test_unknown_recovery_or_invalid_release_is_preserved(self):
        inventory = _Inventory()
        with TemporaryDirectory() as temporary:
            contract = self._fixture(Path(temporary))
            contract.transfer_root.mkdir(mode=0o700)
            unknown = contract.transfer_root / "unknown-private-sentinel"
            unknown.write_bytes(b"do not delete")
            with (
                patch.object(materializer, "_load_ingress", return_value=inventory),
                patch.object(
                    materializer,
                    "_reconcile",
                    side_effect=materializer.ResearchMaterializerError(
                        "recovery differs"
                    ),
                ),
                patch.object(
                    materializer,
                    "stage_accepted_research_release",
                ) as stage,
                self.assertRaises(materializer.ResearchMaterializerError),
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            self.assertEqual(unknown.read_bytes(), b"do not delete")
            stage.assert_not_called()

        with TemporaryDirectory() as temporary:
            contract = self._fixture(Path(temporary))
            contract.release_root.mkdir(mode=0o710)
            contract.transfer_root.mkdir(mode=0o700)
            with (
                patch.object(
                    materializer,
                    "_validate_release",
                    side_effect=materializer.ResearchMaterializerError(
                        "invalid release"
                    ),
                ),
                patch.object(materializer, "_reconcile") as reconcile,
                self.assertRaises(materializer.ResearchMaterializerError),
            ):
                materializer.materialize_accepted_research_release(contract=contract)
            self.assertTrue(contract.release_root.is_dir())
            self.assertTrue(contract.transfer_root.is_dir())
            reconcile.assert_not_called()

    def test_volume_membership_metadata_and_lock_refuse_before_effects(self):
        cases = ("extra", "symlink", "fifo", "wrong-mode")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                contract = self._fixture(Path(temporary))
                if case == "extra":
                    (contract.volume_root / "foreign").write_bytes(b"foreign")
                elif case == "symlink":
                    (contract.volume_root / "research-release").symlink_to(
                        contract.validation_parent,
                        target_is_directory=True,
                    )
                elif case == "fifo":
                    os.mkfifo(contract.volume_root / "transfer", 0o600)
                else:
                    contract.volume_root.chmod(0o700)
                with (
                    patch.object(
                        materializer,
                        "_load_ingress",
                        side_effect=AssertionError("unsafe state read ingress"),
                    ),
                    self.assertRaises(materializer.ResearchMaterializerError),
                ):
                    materializer.materialize_accepted_research_release(
                        contract=contract
                    )

        with TemporaryDirectory() as temporary:
            contract = self._fixture(Path(temporary))
            descriptor = os.open(
                contract.volume_root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(
                    materializer.ResearchMaterializerError, "another.*active"
                ):
                    materializer.materialize_accepted_research_release(
                        contract=contract
                    )
            finally:
                os.close(descriptor)
            self.assertEqual(tuple(contract.volume_root.iterdir()), ())

    def test_exact_ingress_reader_rejects_mutable_or_aliased_files(self):
        raw = b"registered private test bytes"
        digest = hashlib.sha256(raw).hexdigest()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "inventory.json"
            target.write_bytes(raw)
            target.chmod(0o600)
            self.assertEqual(
                materializer._read_exact_file(
                    target,
                    expected_bytes=len(raw),
                    expected_sha256=digest,
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    retain=True,
                ),
                raw,
            )
            for case in ("mode", "hardlink", "symlink", "size"):
                with self.subTest(case=case):
                    alias = root / f"{case}-alias"
                    if case == "mode":
                        target.chmod(0o640)
                    elif case == "hardlink":
                        os.link(target, alias)
                    elif case == "symlink":
                        alias.symlink_to(target)
                    else:
                        target.write_bytes(raw + b"x")
                    selected = alias if case == "symlink" else target
                    with self.assertRaises(materializer.ResearchMaterializerError):
                        materializer._read_exact_file(
                            selected,
                            expected_bytes=len(raw),
                            expected_sha256=digest,
                            uid=os.geteuid(),
                            gid=os.getegid(),
                            retain=False,
                        )
                    if alias.exists() or alias.is_symlink():
                        alias.unlink()
                    target.write_bytes(raw)
                    target.chmod(0o600)

        with TemporaryDirectory() as temporary:
            contract = replace(
                self._fixture(Path(temporary)),
                validate_mounts=True,
            )
            inventory = _Inventory()
            inventory_raw = b"exact inventory bytes"
            with (
                patch.object(materializer, "_require_read_only_ingress") as readonly,
                patch.object(materializer, "_require_directory") as directory,
                patch.object(
                    materializer,
                    "_read_exact_file",
                    side_effect=(inventory_raw, None),
                ) as read,
                patch.object(
                    materializer,
                    "parse_accepted_deployment_inventory",
                    return_value=inventory,
                ) as parse,
            ):
                self.assertIs(materializer._load_ingress(contract), inventory)
            readonly.assert_called_once_with(
                (
                    contract.research_root,
                    contract.inventory_path,
                    contract.bundle_path,
                )
            )
            directory.assert_called_once_with(
                contract.research_root,
                uid=contract.source_uid,
                gid=contract.source_gid,
                mode=0o700,
            )
            self.assertEqual(
                read.call_args_list,
                [
                    call(
                        contract.inventory_path,
                        expected_bytes=materializer.DEPLOYMENT_INVENTORY_BYTES,
                        expected_sha256=(
                            materializer.DEPLOYMENT_INVENTORY_FILE_SHA256
                        ),
                        uid=contract.source_uid,
                        gid=contract.source_gid,
                        retain=True,
                    ),
                    call(
                        contract.bundle_path,
                        expected_bytes=(
                            materializer.staging_source_bundle.ACCEPTED_BUNDLE_BYTES
                        ),
                        expected_sha256=(
                            materializer.staging_source_bundle.ACCEPTED_BUNDLE_SHA256
                        ),
                        uid=contract.source_uid,
                        gid=contract.source_gid,
                        retain=False,
                    ),
                ],
            )
            parse.assert_called_once_with(inventory_raw)

            with (
                patch.object(
                    materializer,
                    "_require_read_only_ingress",
                    side_effect=materializer.ResearchMaterializerError(
                        "ingress is writable"
                    ),
                ),
                patch.object(materializer, "_read_exact_file") as unsafe_read,
                self.assertRaises(materializer.ResearchMaterializerError),
            ):
                materializer._load_ingress(contract)
            unsafe_read.assert_not_called()

    def test_main_is_zero_argument_silent_and_sanitizes_failures(self):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(materializer.main(["unexpected"]), 2)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch.object(
                materializer,
                "materialize_accepted_research_release",
                side_effect=RuntimeError("PRIVATE_SENTINEL"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(materializer.main([]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "Buffalo research materialization failed\n",
        )
        self.assertNotIn("PRIVATE_SENTINEL", stderr.getvalue())

    def test_production_entrypoint_contract_is_fixed_and_non_startup(self):
        repository = Path(__file__).resolve().parents[2]
        source = (repository / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            'ENTRYPOINT ["/usr/bin/tini", "-g", "--", '
            '"/opt/buffalo-venv/bin/python", "-I", "-B", '
            '"-m", "procurement_os.staging_bootstrap"]',
            source,
        )
        self.assertNotIn("staging_research_materializer", source)
        bootstrap_source = (
            repository
            / "procurement"
            / "src"
            / "procurement_os"
            / "staging_bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("staging_research_materializer", bootstrap_source)
        self.assertEqual(
            materializer._PRODUCTION_CONTRACT,
            materializer._MaterializerContract(
                volume_root=Path("/data"),
                transfer_root=Path("/data/transfer"),
                release_root=Path("/data/research-release"),
                validation_parent=Path("/run/buffalo-research-materializer"),
                research_root=Path("/mnt/buffalo-accepted-research"),
                inventory_path=Path("/mnt/deployment-inventory.json"),
                bundle_path=Path(
                    "/mnt/buffalo-procurement-os-accepted-source-608929ad.bundle"
                ),
                source_uid=1000,
                source_gid=1000,
                supervisor_uid=0,
                supervisor_gid=0,
                destination_uid=1103,
                destination_gid=1203,
                validate_host_identity=True,
                validate_mounts=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import backup_synthetic_staging as backup_cli

from procurement_os import local_backup_v2 as local_backup
from procurement_os import staging_bootstrap
from procurement_os.local_backup_v2 import (
    BACKUP_V2_CONTRACT,
    LocalBackupV2Error,
    canonical_sha256,
    database_state_evidence,
    verify_price_apply_backup,
)
from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID,
    EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID,
    EXPECTED_PROJECT_ID,
)
from procurement_os.synthetic_price_replacement_contract import (
    CATALOG_SHA256 as PRICE_CATALOG_SHA256,
    MIGRATION_NAME as PRICE_MIGRATION_NAME,
    MIGRATION_SHA256 as PRICE_MIGRATION_SHA256,
)
from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE,
    RUNTIME_LOGIN,
    STAGING_BACKUP_RELEASE_SHA256,
    SyntheticStagingTarget,
    staging_backup_release,
)
from procurement_os import synthetic_staging_backup_v2 as staging_backup
from procurement_os.synthetic_staging_backup_v2 import (
    SyntheticStagingBackupError,
    create_staging_backup_v2,
)


class SyntheticStagingBackupV2Tests(unittest.TestCase):
    COMMIT = "1" * 40
    TREE = "2" * 40
    BATCH = "11111111-1111-4111-8111-111111111111"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="buffalo-staging-backup-v2-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.backups = self.root / "backups"
        self.backups.mkdir(mode=0o700)
        self.target = SyntheticStagingTarget(
            database_url=(
                f"postgresql://{RUNTIME_LOGIN}@127.0.0.1:55432/"
                f"{EXPECTED_DATABASE}"
            ),
            expected_private_host="127.0.0.1",
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
            transfer_manifest_sha256="a" * 64,
            owned_local_port=55432,
        )
        self.raw = b"synthetic,price\n"
        self.raw_sha = hashlib.sha256(self.raw).hexdigest()

    @staticmethod
    def _member(path: Path, payload: bytes) -> dict[str, object]:
        path.write_bytes(payload)
        path.chmod(0o600)
        return {
            "path": path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }

    def _manifest(self, *, staging: bool) -> tuple[Path, dict[str, object]]:
        destination = self.backups / ("staging-v2-test" if staging else "legacy-v2-test")
        destination.mkdir(mode=0o700)
        dump = self._member(destination / "database.dump", b"dump")
        archive_path = destination / "storage.tar"
        raw_key = f"price-books/raw/{self.raw_sha}.csv"
        with tarfile.open(archive_path, "w") as archive:
            record = tarfile.TarInfo(raw_key)
            record.size = len(self.raw)
            archive.addfile(record, io.BytesIO(self.raw))
        archive_path.chmod(0o600)
        archive = {
            "path": archive_path.name,
            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "bytes": archive_path.stat().st_size,
            "files": [
                {
                    "path": raw_key,
                    "sha256": self.raw_sha,
                    "bytes": len(self.raw),
                }
            ],
        }
        facts = {
            "review_batches": 0,
            "review_candidates": 0,
            "mapping_decisions": 0,
            "selection_events": 0,
            "selection_heads": 0,
            "runs": 1,
            "purchase_orders": 0,
            "artifacts": 0,
            "decision_payloads": "",
            "selection_payloads": "",
            "run_fingerprints": "f" * 64,
            "artifact_hashes": "",
            "relation_inventory": [
                {
                    "relation": "meta",
                    "row_count": 1,
                    "sha256": "3" * 64,
                }
            ],
            "sequence_inventory": [
                {
                    "sequence": "prices_price_id_seq",
                    "last_value": 10,
                    "is_called": True,
                }
            ],
        }
        database = {
            "database": EXPECTED_DATABASE if staging else "fixture_demo",
            "postgres_major": 16,
            "server_address": "127.0.0.1",
            "session_user": RUNTIME_LOGIN if staging else "qa_release_login",
            "current_user": RUNTIME_LOGIN if staging else "qa_mapping_owner",
            "database_owner": (
                "buffalo_synthetic_owner" if staging else "qa_mapping_owner"
            ),
        }
        if staging:
            database["server_host"] = self.target.expected_private_host
        manifest: dict[str, object] = {
            "contract": BACKUP_V2_CONTRACT,
            "created_utc": "20261004T010203Z",
            "database": database,
            "database_dump": dump,
            "storage_archive": archive,
            "state": {"facts": facts, "sha256": canonical_sha256(facts)},
            "limitations": [
                "same-host local recovery only",
                "synthetic price APPLY recovery proof only",
                "sessions and secret files are intentionally excluded",
            ],
            "source_git": {"commit": self.COMMIT, "tree": self.TREE},
            "schema_release": {
                "migration": PRICE_MIGRATION_NAME,
                "migration_sha256": PRICE_MIGRATION_SHA256,
                "catalog_sha256": PRICE_CATALOG_SHA256,
            },
            "price_replacement": {
                "price_book_batch_id": self.BATCH,
                "vendor_id": "22222222-2222-4222-8222-222222222222",
                "price_scope_key": "COMPLETE_VENDOR",
                "prior_event_id": "33333333-3333-4333-8333-333333333333",
                "prior_head_version": 1,
                "raw_content_sha256": self.raw_sha,
                "raw_storage_key": raw_key,
            },
            "prechange_current_scope": {
                "rows": [{"offer_id": 1, "case_price": "9.50"}],
                "rows_sha256": canonical_sha256(
                    [{"offer_id": 1, "case_price": "9.50"}]
                ),
                "database_scope_sha256": "4" * 64,
            },
        }
        if staging:
            manifest["staging_release"] = staging_backup_release(self.target)
        path = destination / "manifest.json"
        path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path, manifest

    def _verify(self, path: Path, *, staging: bool):
        return verify_price_apply_backup(
            runtime_root=self.root,
            manifest_path=path,
            expected_manifest_sha=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_commit=self.COMMIT,
            expected_tree=self.TREE,
            expected_profile="staging" if staging else "legacy",
            expected_staging_release=(
                staging_backup_release(self.target) if staging else None
            ),
            expected_staging_target=self.target if staging else None,
        )

    def _rewrite(self, path: Path, manifest: dict[str, object]) -> None:
        path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def _producer_fixture(self):
        workspace = self.root / "producer"
        workspace.mkdir(mode=0o700)
        recovery = workspace / "recovery"
        storage = workspace / "storage"
        source = workspace / "source"
        for path in (recovery, storage, source):
            path.mkdir(mode=0o700)
        (recovery / "backups").mkdir(mode=0o700)
        raw_key = f"price-books/raw/{self.raw_sha}.csv"
        raw_path = storage / raw_key
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(self.raw)
        raw_path.chmod(0o600)
        facts = {
            "review_batches": 0,
            "review_candidates": 0,
            "mapping_decisions": 0,
            "selection_events": 0,
            "selection_heads": 0,
            "runs": 1,
            "purchase_orders": 0,
            "artifacts": 0,
            "decision_payloads": "",
            "selection_payloads": "",
            "run_fingerprints": "f" * 64,
            "artifact_hashes": "",
            "relation_inventory": [
                {"relation": "meta", "row_count": 1, "sha256": "3" * 64}
            ],
            "sequence_inventory": [
                {
                    "sequence": "prices_price_id_seq",
                    "last_value": 10,
                    "is_called": True,
                }
            ],
        }
        snapshot = {
            "attestation": staging_backup_release(self.target)[
                "runtime_attestation_identity"
            ],
            "database": {
                "database": EXPECTED_DATABASE,
                "postgres_major": 16,
                "server_address": "127.0.0.1",
                "session_user": RUNTIME_LOGIN,
                "current_user": RUNTIME_LOGIN,
                "database_owner": "buffalo_synthetic_owner",
                "server_host": self.target.expected_private_host,
            },
            "state": {"facts": facts, "sha256": canonical_sha256(facts)},
            "storage_bindings": [
                {"path": raw_key, "sha256": self.raw_sha, "bytes": None}
            ],
            "price_replacement": {
                "price_book_batch_id": self.BATCH,
                "vendor_id": "22222222-2222-4222-8222-222222222222",
                "price_scope_key": "COMPLETE_VENDOR",
                "prior_event_id": "33333333-3333-4333-8333-333333333333",
                "prior_head_version": 1,
                "raw_content_sha256": self.raw_sha,
                "raw_storage_key": raw_key,
            },
            "prechange_current_scope": {
                "rows": [{"offer_id": 1, "case_price": "9.50"}],
                "rows_sha256": canonical_sha256(
                    [{"offer_id": 1, "case_price": "9.50"}]
                ),
                "database_scope_sha256": "4" * 64,
            },
        }
        provisioner_url = (
            "postgresql://buffalo_synthetic_provisioner@127.0.0.1:55432/"
            f"{EXPECTED_DATABASE}"
        )
        return recovery, storage, source, snapshot, provisioner_url

    @staticmethod
    def _fake_dump(argv, **kwargs):
        if "--list" in argv:
            return mock.Mock(
                returncode=0,
                stdout=(
                    "; Archive created at 2026-10-04 00:00:00 UTC\n"
                    "1; 2615 1 SCHEMA - qa_mapping_test owner\n"
                    "2; 3079 2 EXTENSION - pgcrypto owner\n"
                ),
            )
        if "--file" in argv:
            return mock.Mock(returncode=0)
        output = kwargs["stdout"]
        mode = stat.S_IMODE(os.fstat(output.fileno()).st_mode)
        if mode != 0o600:
            raise AssertionError(f"dump output mode differs: {mode:o}")
        output.write(b"custom-format-dump")
        return mock.Mock(returncode=0)

    def test_explicit_staging_profile_accepts_exact_manifest_only(self):
        path, _manifest = self._manifest(staging=True)
        verified = self._verify(path, staging=True)
        self.assertEqual(verified.target_kind, "staging")
        self.assertEqual(
            verified.staging_release_sha256,
            STAGING_BACKUP_RELEASE_SHA256,
        )
        self.assertEqual(
            verified.runtime_attestation_identity,
            staging_backup_release(self.target)["runtime_attestation_identity"],
        )
        with self.assertRaisesRegex(LocalBackupV2Error, "contract differs"):
            self._verify(path, staging=False)

    def test_bound_staging_profile_uses_only_the_staging_environment_bundle(self):
        path, _manifest = self._manifest(staging=True)
        manifest_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        target_environment = {
            "DATABASE_URL": self.target.database_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": "a" * 64,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": "55432",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_PRICE_BACKUP_ROOT": str(self.root),
            "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST": str(path),
            "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST_SHA256": manifest_sha,
            "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_COMMIT": self.COMMIT,
            "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_TREE": self.TREE,
        }
        with mock.patch.dict(os.environ, target_environment, clear=True):
            verified = local_backup.verify_bound_price_apply_backup()
        self.assertEqual(verified.target_kind, "staging")
        child = subprocess.run(
            staging_bootstrap._price_backup_verifier_argv(sys.executable),
            check=False,
            capture_output=True,
            env={
                **target_environment,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
            },
            timeout=10,
        )
        self.assertEqual(child.returncode, 0, child.stderr.decode(errors="replace"))
        unbound = {
            name: value
            for name, value in target_environment.items()
            if name not in local_backup.STAGING_BINDING_ENVIRONMENT_NAMES
        }
        with mock.patch.dict(os.environ, unbound, clear=True), self.assertRaisesRegex(
            LocalBackupV2Error, "binding is absent"
        ):
            local_backup.verify_bound_price_apply_backup()
        for missing in local_backup.STAGING_BINDING_ENVIRONMENT_NAMES:
            partial = dict(target_environment)
            partial.pop(missing)
            with self.subTest(missing=missing), mock.patch.dict(
                os.environ, partial, clear=True
            ), self.assertRaisesRegex(LocalBackupV2Error, "binding is absent"):
                local_backup.verify_bound_price_apply_backup()
        mixed = {
            **target_environment,
            local_backup.RUNTIME_ROOT_ENV: str(self.root),
        }
        with mock.patch.dict(os.environ, mixed, clear=True), self.assertRaisesRegex(
            LocalBackupV2Error, "profile differs"
        ):
            local_backup.verify_bound_price_apply_backup()
        legacy_only = {
            **unbound,
            local_backup.RUNTIME_ROOT_ENV: str(self.root),
            local_backup.MANIFEST_ENV: str(path),
            local_backup.MANIFEST_SHA_ENV: manifest_sha,
            local_backup.SOURCE_COMMIT_ENV: self.COMMIT,
            local_backup.SOURCE_TREE_ENV: self.TREE,
        }
        with mock.patch.dict(
            os.environ, legacy_only, clear=True
        ), self.assertRaisesRegex(LocalBackupV2Error, "profile differs"):
            local_backup.verify_bound_price_apply_backup()

    def test_legacy_hardlink_behavior_is_unchanged_but_staging_is_strict(self):
        legacy_path, _manifest = self._manifest(staging=False)
        os.link(legacy_path, legacy_path.parent / "legacy-manifest-link.json")
        self.assertEqual(
            self._verify(legacy_path, staging=False).target_kind,
            "legacy-local",
        )
        staging_path, _manifest = self._manifest(staging=True)
        os.link(staging_path, staging_path.parent / "staging-manifest-link.json")
        with self.assertRaisesRegex(LocalBackupV2Error, "ownership differs"):
            self._verify(staging_path, staging=True)

    def test_default_legacy_profile_remains_exact_and_rejects_staging(self):
        legacy, _manifest = self._manifest(staging=False)
        verified = self._verify(legacy, staging=False)
        self.assertEqual(verified.target_kind, "legacy-local")
        self.assertIsNone(verified.staging_release_sha256)
        with self.assertRaisesRegex(LocalBackupV2Error, "contract differs"):
            self._verify(legacy, staging=True)

    def test_every_staging_release_leaf_and_shape_is_source_pinned(self):
        path, original = self._manifest(staging=True)
        release = original["staging_release"]
        assert isinstance(release, dict)
        mutations: list[dict[str, object]] = []
        for key in sorted(release):
            if key == "target":
                target = release[key]
                assert isinstance(target, dict)
                for target_key in sorted(target):
                    changed = copy.deepcopy(original)
                    changed_release = changed["staging_release"]
                    assert isinstance(changed_release, dict)
                    changed_target = changed_release["target"]
                    assert isinstance(changed_target, dict)
                    changed_target[target_key] = "wrong"
                    mutations.append(changed)
            else:
                changed = copy.deepcopy(original)
                changed_release = changed["staging_release"]
                assert isinstance(changed_release, dict)
                changed_release[key] = 15 if key == "postgres_major" else "wrong"
                mutations.append(changed)
        missing = copy.deepcopy(original)
        missing_release = missing["staging_release"]
        assert isinstance(missing_release, dict)
        missing_release.pop("schema")
        mutations.append(missing)
        extra = copy.deepcopy(original)
        extra_release = extra["staging_release"]
        assert isinstance(extra_release, dict)
        extra_release["unexpected"] = "wrong"
        mutations.append(extra)
        for index, changed in enumerate(mutations):
            with self.subTest(index=index):
                self._rewrite(path, changed)
                with self.assertRaisesRegex(
                    LocalBackupV2Error, "staging release differs"
                ):
                    self._verify(path, staging=True)

    def test_staging_database_and_schema_release_are_exact(self):
        path, original = self._manifest(staging=True)
        changes = (
            ("database", "session_user", "qa_release_login"),
            ("database", "current_user", "buffalo_synthetic_owner"),
            ("database", "database_owner", "qa_mapping_owner"),
            ("database", "server_host", "other.railway.internal"),
            ("database", "server_address", "203.0.113.4"),
            ("schema_release", "migration_sha256", "0" * 64),
            ("schema_release", "catalog_sha256", "0" * 64),
        )
        for parent, key, value in changes:
            with self.subTest(parent=parent, key=key):
                changed = copy.deepcopy(original)
                section = changed[parent]
                assert isinstance(section, dict)
                section[key] = value
                self._rewrite(path, changed)
                with self.assertRaises(LocalBackupV2Error):
                    self._verify(path, staging=True)

    def test_legacy_database_state_path_does_not_probe_staging_identity(self):
        class SentinelConnection:
            statement = ""

            def execute(self, statement, _parameters=None):
                self.statement = str(statement)
                raise RuntimeError("stop after first query")

        connection = SentinelConnection()
        with self.assertRaisesRegex(RuntimeError, "first query"):
            database_state_evidence(connection, schema="qa_mapping_test")
        self.assertIn("supplier_mapping_review_batches", connection.statement)
        self.assertNotIn("session_user", connection.statement)

    def test_archive_member_hashing_is_bounded(self):
        class BoundedReader(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > 1024 * 1024:
                    raise AssertionError("archive verifier attempted an unbounded read")
                return super().read(size)

        payload = b"bounded-archive-member" * 100
        digest, size = local_backup._stream_sha256(BoundedReader(payload))
        self.assertEqual(size, len(payload))
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_operator_producer_writes_secure_self_verified_manifest(self):
        recovery, storage, source, snapshot, provisioner_url = (
            self._producer_fixture()
        )
        source_identity = {"commit": self.COMMIT, "tree": self.TREE}
        with mock.patch.object(
            staging_backup, "_source_identity", return_value=source_identity
        ), mock.patch.object(
            staging_backup,
            "_source_git_boundaries",
            return_value=(source, source),
        ), mock.patch.object(
            staging_backup,
            "_attest_provisioner",
            return_value=STAGING_BACKUP_RELEASE_SHA256,
        ), mock.patch.object(
            staging_backup, "_runtime_snapshot", return_value=snapshot
        ), mock.patch.object(
            staging_backup, "_postgres_dump", return_value="/usr/bin/pg_dump"
        ), mock.patch.object(
            staging_backup,
            "_postgres_restore",
            return_value="/usr/bin/pg_restore",
        ), mock.patch.object(
            staging_backup.subprocess, "run", side_effect=self._fake_dump
        ) as run:
            manifest_path = create_staging_backup_v2(
                runtime_url=self.target.database_url,
                provisioner_url=provisioner_url,
                target=self.target,
                recovery_root=recovery,
                storage_root=storage,
                source_root=source,
                batch_id=self.BATCH,
            )
        self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(manifest_path.parent.stat().st_mode), 0o700)
        manifest = json.loads(manifest_path.read_bytes())
        self.assertEqual(manifest["staging_release"], staging_backup_release(self.target))
        self.assertEqual(manifest["database"], snapshot["database"])
        commands = [call.args[0] for call in run.call_args_list]
        dump_argv = next(argv for argv in commands if "--format=custom" in argv)
        restore_argv = next(argv for argv in commands if "--list" in argv)
        materialize_argv = next(
            argv for argv in commands if "--file" in argv
        )
        self.assertNotIn("--file", dump_argv)
        self.assertNotIn("password", " ".join(dump_argv).lower())
        self.assertEqual(restore_argv[-1], str(manifest_path.parent / "database.dump"))
        self.assertEqual(materialize_argv[-1], str(manifest_path.parent / "database.dump"))
        self.assertEqual(materialize_argv[-3:-1], ["--file", os.devnull])
        self.assertNotIn(self.raw.decode().strip(), json.dumps(manifest))

    def test_operator_producer_refuses_drift_and_cleans_exact_destination(self):
        recovery, storage, source, snapshot, provisioner_url = (
            self._producer_fixture()
        )
        changed = copy.deepcopy(snapshot)
        changed["state"]["facts"]["runs"] = 2
        changed["state"]["sha256"] = canonical_sha256(
            changed["state"]["facts"]
        )
        with mock.patch.object(
            staging_backup,
            "_source_identity",
            return_value={"commit": self.COMMIT, "tree": self.TREE},
        ), mock.patch.object(
            staging_backup,
            "_source_git_boundaries",
            return_value=(source, source),
        ), mock.patch.object(
            staging_backup,
            "_attest_provisioner",
            return_value=STAGING_BACKUP_RELEASE_SHA256,
        ), mock.patch.object(
            staging_backup,
            "_runtime_snapshot",
            side_effect=(snapshot, changed),
        ), mock.patch.object(
            staging_backup, "_postgres_dump", return_value="/usr/bin/pg_dump"
        ), mock.patch.object(
            staging_backup,
            "_postgres_restore",
            return_value="/usr/bin/pg_restore",
        ), mock.patch.object(
            staging_backup.subprocess, "run", side_effect=self._fake_dump
        ):
            with self.assertRaisesRegex(
                SyntheticStagingBackupError, "inputs changed"
            ):
                create_staging_backup_v2(
                    runtime_url=self.target.database_url,
                    provisioner_url=provisioner_url,
                    target=self.target,
                    recovery_root=recovery,
                    storage_root=storage,
                    source_root=source,
                    batch_id=self.BATCH,
                )
        self.assertEqual(list((recovery / "backups").iterdir()), [])

    def test_operator_producer_rejects_credentials_symlinks_and_missing_raw(self):
        recovery, storage, source, snapshot, provisioner_url = (
            self._producer_fixture()
        )
        credentialed = provisioner_url.replace("@", ":secret@")
        with self.assertRaisesRegex(
            SyntheticStagingBackupError, "database URL differs"
        ):
            create_staging_backup_v2(
                runtime_url=self.target.database_url,
                provisioner_url=credentialed,
                target=self.target,
                recovery_root=recovery,
                storage_root=storage,
                source_root=source,
                batch_id=self.BATCH,
            )
        alias = self.root / "recovery-alias"
        alias.symlink_to(recovery, target_is_directory=True)
        with self.assertRaisesRegex(
            SyntheticStagingBackupError, "ancestor is a symlink"
        ):
            create_staging_backup_v2(
                runtime_url=self.target.database_url,
                provisioner_url=provisioner_url,
                target=self.target,
                recovery_root=alias,
                storage_root=storage,
                source_root=source,
                batch_id=self.BATCH,
            )
        nested_recovery = source / "recovery"
        nested_recovery.mkdir(mode=0o700)
        (nested_recovery / "backups").mkdir(mode=0o700)
        with mock.patch.object(
            staging_backup,
            "_source_git_boundaries",
            return_value=(source, source),
        ):
            with self.assertRaisesRegex(
                SyntheticStagingBackupError, "overlap source identity"
            ):
                create_staging_backup_v2(
                    runtime_url=self.target.database_url,
                    provisioner_url=provisioner_url,
                    target=self.target,
                    recovery_root=nested_recovery,
                    storage_root=storage,
                    source_root=source,
                    batch_id=self.BATCH,
                )
        secret = storage / "operator-session-secret.key"
        secret.write_bytes(b"must-not-enter-backup")
        secret.chmod(0o600)
        with mock.patch.object(
            staging_backup,
            "_source_identity",
            return_value={"commit": self.COMMIT, "tree": self.TREE},
        ), mock.patch.object(
            staging_backup,
            "_source_git_boundaries",
            return_value=(source, source),
        ), mock.patch.object(
            staging_backup,
            "_attest_provisioner",
            return_value=STAGING_BACKUP_RELEASE_SHA256,
        ), mock.patch.object(
            staging_backup, "_runtime_snapshot", return_value=snapshot
        ):
            with self.assertRaisesRegex(
                SyntheticStagingBackupError, "storage inventory differs"
            ):
                create_staging_backup_v2(
                    runtime_url=self.target.database_url,
                    provisioner_url=provisioner_url,
                    target=self.target,
                    recovery_root=recovery,
                    storage_root=storage,
                    source_root=source,
                    batch_id=self.BATCH,
                )
        secret.unlink()
        (storage / snapshot["price_replacement"]["raw_storage_key"]).unlink()
        with mock.patch.object(
            staging_backup,
            "_source_identity",
            return_value={"commit": self.COMMIT, "tree": self.TREE},
        ), mock.patch.object(
            staging_backup,
            "_source_git_boundaries",
            return_value=(source, source),
        ), mock.patch.object(
            staging_backup,
            "_attest_provisioner",
            return_value=STAGING_BACKUP_RELEASE_SHA256,
        ), mock.patch.object(
            staging_backup, "_runtime_snapshot", return_value=snapshot
        ):
            with self.assertRaisesRegex(
                SyntheticStagingBackupError, "absent from storage"
            ):
                create_staging_backup_v2(
                    runtime_url=self.target.database_url,
                    provisioner_url=provisioner_url,
                    target=self.target,
                    recovery_root=recovery,
                    storage_root=storage,
                    source_root=source,
                    batch_id=self.BATCH,
                )
        self.assertEqual(list((recovery / "backups").iterdir()), [])

    def test_operator_producer_rejects_unreadable_dump_and_cleans(self):
        recovery, storage, source, snapshot, provisioner_url = (
            self._producer_fixture()
        )

        def invalid_dump(argv, **kwargs):
            if "--list" in argv:
                return mock.Mock(
                    returncode=0,
                    stdout=(
                        "1; 2615 1 SCHEMA - qa_mapping_test owner\n"
                        "2; 3079 2 EXTENSION - pgcrypto owner\n"
                    ),
                )
            if "--file" in argv:
                raise subprocess.CalledProcessError(1, argv)
            kwargs["stdout"].write(b"not-a-postgresql-archive")
            return mock.Mock(returncode=0)

        with mock.patch.object(
            staging_backup,
            "_source_identity",
            return_value={"commit": self.COMMIT, "tree": self.TREE},
        ), mock.patch.object(
            staging_backup,
            "_source_git_boundaries",
            return_value=(source, source),
        ), mock.patch.object(
            staging_backup,
            "_attest_provisioner",
            return_value=STAGING_BACKUP_RELEASE_SHA256,
        ), mock.patch.object(
            staging_backup, "_runtime_snapshot", return_value=snapshot
        ), mock.patch.object(
            staging_backup, "_postgres_dump", return_value="/usr/bin/pg_dump"
        ), mock.patch.object(
            staging_backup,
            "_postgres_restore",
            return_value="/usr/bin/pg_restore",
        ), mock.patch.object(
            staging_backup.subprocess, "run", side_effect=invalid_dump
        ):
            with self.assertRaisesRegex(
                SyntheticStagingBackupError, "dump archive is unreadable"
            ):
                create_staging_backup_v2(
                    runtime_url=self.target.database_url,
                    provisioner_url=provisioner_url,
                    target=self.target,
                    recovery_root=recovery,
                    storage_root=storage,
                    source_root=source,
                    batch_id=self.BATCH,
                )
        self.assertEqual(list((recovery / "backups").iterdir()), [])

    def test_operator_producer_refuses_untrusted_database_program(self):
        executable = self.root / "pg_dump"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        with mock.patch.object(
            staging_backup.shutil, "which", return_value=str(executable)
        ):
            with self.assertRaisesRegex(
                SyntheticStagingBackupError, "trusted pg_dump executable differs"
            ):
                staging_backup._postgres_dump()

    def test_operator_cli_passes_only_explicit_inputs_and_prints_digest(self):
        recovery, storage, _source, _snapshot, provisioner_url = (
            self._producer_fixture()
        )
        destination = recovery / "backups" / "staging-v2-cli-test"
        destination.mkdir(mode=0o700)
        manifest = destination / "manifest.json"
        manifest.write_bytes(b'{"contract":"test"}\n')
        manifest.chmod(0o600)
        environment = {
            "BUFFALO_SYNTHETIC_BACKUP_RUNTIME_URL": self.target.database_url,
            "BUFFALO_SYNTHETIC_PROVISIONING_URL": provisioner_url,
            "BUFFALO_STAGING_PRICE_BACKUP_ROOT": str(recovery),
            "PROCUREMENT_STORAGE_ROOT": str(storage),
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": "a" * 64,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": "55432",
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            backup_cli, "create_staging_backup_v2", return_value=manifest
        ) as create, mock.patch("sys.stdout", output):
            self.assertEqual(
                backup_cli.main(
                    [
                        "--batch-id",
                        self.BATCH,
                        "--confirm-database",
                        EXPECTED_DATABASE,
                        "--confirm-service-state",
                        "STOPPED",
                    ]
                ),
                0,
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(
            payload,
            {
                "backup_label": destination.name,
                "manifest_sha256": hashlib.sha256(
                    manifest.read_bytes()
                ).hexdigest(),
            },
        )
        called = create.call_args.kwargs
        self.assertEqual(called["runtime_url"], self.target.database_url)
        self.assertEqual(called["provisioner_url"], provisioner_url)
        self.assertEqual(called["recovery_root"], recovery)
        self.assertEqual(called["storage_root"], storage)
        self.assertNotIn(self.target.database_url, output.getvalue())
        self.assertNotIn(provisioner_url, output.getvalue())

    def test_operator_cli_refuses_ambient_database_and_unstopped_service(self):
        environment = {
            "DATABASE_URL": self.target.database_url,
            "BUFFALO_SYNTHETIC_BACKUP_RUNTIME_URL": self.target.database_url,
            "BUFFALO_SYNTHETIC_PROVISIONING_URL": (
                "postgresql://buffalo_synthetic_provisioner@127.0.0.1:55432/"
                f"{EXPECTED_DATABASE}"
            ),
        }
        base = [
            "--batch-id",
            self.BATCH,
            "--confirm-database",
            EXPECTED_DATABASE,
            "--confirm-service-state",
            "STOPPED",
        ]
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                SystemExit, "explicit stopped staging backup boundary"
            ):
                backup_cli.main(base)
        environment.pop("DATABASE_URL")
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                SystemExit, "explicit stopped staging backup boundary"
            ):
                backup_cli.main([*base[:-1], "RUNNING"])


if __name__ == "__main__":
    unittest.main()

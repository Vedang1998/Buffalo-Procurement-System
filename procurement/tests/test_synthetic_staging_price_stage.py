from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import psycopg

import test_synthetic_staging_database as staging_fixture
from procurement_os.database_lifecycle import (
    DatabaseLifecycleError,
    acquire_database_lifecycle_lock,
    database_lifecycle_lock_name,
    release_database_lifecycle_lock,
)
from procurement_os.persistent_mapping import Principal
from procurement_os.staging_bootstrap import (
    DATABASE_PASSWORD_ENV,
    StagingBootstrapInputs,
)
from procurement_os.storage import LocalFilesystemStorage
from procurement_os.synthetic_price_replacement import (
    SyntheticPriceReplacementError,
    _stage_and_validate_declared_price_book,
    confirm_declared_price_book,
    preview_declared_price_confirmation,
    registered_target_declaration,
    stage_and_validate_declared_price_book,
)
from procurement_os.synthetic_price_replacement_contract import (
    REGISTERED_OPERATOR_BOOK_BYTES,
    REGISTERED_OPERATOR_BOOK_PATH,
    REGISTERED_OPERATOR_BOOK_REF,
    REGISTERED_OPERATOR_BOOK_SHA256,
    STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
)
from procurement_os import synthetic_staging_price_stage as price_stage
from procurement_os.synthetic_staging_price_stage import (
    SyntheticStagingPriceStageError,
    load_registered_operator_fixture,
    stage_registered_operator_fixture,
)


class SyntheticStagingPriceStageUnitTests(unittest.TestCase):
    SECRET = "runtime:fixture\\secret"

    def inputs(self) -> StagingBootstrapInputs:
        return StagingBootstrapInputs(
            expected_commit="a" * 40,
            external_host="staging.example.test",
            owner_verifier="unused-owner-verifier",
            port="8080",
            replica_id="replica-01",
            volume_root=Path("/data"),
            database_url=(
                "postgresql://buffalo_synthetic_runtime@127.0.0.1:5432/"
                "buffalo_synthetic_staging_demo"
            ),
            postgres_private_host="127.0.0.1",
            transfer_manifest_sha256="b" * 64,
            local_port="5432",
        )

    def proof(self) -> bytes:
        return json.dumps(
            {
                "contract": STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
                "source_ref": REGISTERED_OPERATOR_BOOK_REF,
                "source_bytes": REGISTERED_OPERATOR_BOOK_BYTES,
                "raw_sha256": REGISTERED_OPERATOR_BOOK_SHA256,
                "target_attestation_sha256": (
                    price_stage.EXPECTED_RUNTIME_ATTESTATION_IDENTITY
                ),
                "batch_id": "11111111-1111-4111-8111-111111111111",
                "status": "VALIDATED",
                "declaration_sha256": "1" * 64,
                "validation_fingerprint": "2" * 64,
                "proposed_scope_membership_sha256": "3" * 64,
                "staging_rows_sha256": "4" * 64,
                "validation_issues_sha256": "5" * 64,
                "unchanged_database_sha256": "6" * 64,
                "unchanged_storage_sha256": "7" * 64,
                "idempotent_replay": False,
                "ambiguous_commit_recovered": False,
            },
            sort_keys=True,
        ).encode("ascii")

    def test_registered_source_is_one_exact_public_fixture(self):
        fixture = load_registered_operator_fixture()
        expected = (
            Path(price_stage.__file__).resolve().parents[2]
            / REGISTERED_OPERATOR_BOOK_PATH
        ).read_bytes()
        self.assertEqual(fixture.csv_bytes, expected)
        self.assertEqual(fixture.byte_count, REGISTERED_OPERATOR_BOOK_BYTES)
        self.assertEqual(fixture.content_sha256, REGISTERED_OPERATOR_BOOK_SHA256)
        self.assertEqual(len(expected), 1_590)
        self.assertEqual(
            hashlib.sha256(expected).hexdigest(),
            "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c",
        )

    def test_altered_source_refuses_before_target_or_storage_observation(self):
        source = load_registered_operator_fixture().csv_bytes
        mutations = ("bytes", "mode", "hardlink", "symlink")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                target = Path(root) / "registered.csv"
                target.write_bytes(source)
                target.chmod(0o600)
                selected = target
                if mutation == "bytes":
                    target.write_bytes(b"X" + source[1:])
                elif mutation == "mode":
                    target.chmod(0o620)
                elif mutation == "hardlink":
                    os.link(target, Path(root) / "alias.csv")
                else:
                    actual = Path(root) / "actual.csv"
                    target.rename(actual)
                    target.symlink_to(actual)
                    selected = target
                with mock.patch.object(
                    price_stage, "_source_path", return_value=selected
                ):
                    with self.assertRaisesRegex(
                        SyntheticStagingPriceStageError,
                        "source (differs|is unavailable)",
                    ):
                        load_registered_operator_fixture()

        target = mock.Mock()
        with mock.patch.object(
            price_stage,
            "load_registered_operator_fixture",
            side_effect=SyntheticStagingPriceStageError("source differs"),
        ), mock.patch.object(price_stage, "_attest_and_lock") as attest:
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "source differs",
            ):
                stage_registered_operator_fixture(
                    target=target,
                    password=self.SECRET,
                    storage=mock.Mock(),
                )
        attest.assert_not_called()

    def test_secret_channel_is_fifo_bounded_utf8_and_closed(self):
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        os.write(write_fd, self.SECRET.encode("utf-8"))
        os.close(write_fd)
        observed = price_stage._read_secret_fd(
            {price_stage._SECRET_FD_ENV: str(read_fd)}
        )
        self.assertEqual(observed, self.SECRET)
        with self.assertRaises(OSError):
            os.fstat(read_fd)

        with tempfile.NamedTemporaryFile() as handle:
            duplicate = os.dup(handle.fileno())
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "credential channel differs",
            ):
                price_stage._read_secret_fd(
                    {price_stage._SECRET_FD_ENV: str(duplicate)}
                )

        for label, payload, accepted in (
            ("exact-limit", b"x" * price_stage._MAX_SECRET_BYTES, True),
            ("empty", b"", False),
            ("over-limit", b"x" * (price_stage._MAX_SECRET_BYTES + 1), False),
            ("newline", b"secret\n", False),
            ("carriage-return", b"secret\r", False),
            ("nul", b"secret\0", False),
            ("invalid-utf8", b"\xff", False),
        ):
            with self.subTest(label=label):
                read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
                os.write(write_fd, payload)
                os.close(write_fd)
                if accepted:
                    self.assertEqual(
                        price_stage._read_secret_fd(
                            {price_stage._SECRET_FD_ENV: str(read_fd)}
                        ),
                        payload.decode("utf-8"),
                    )
                else:
                    with self.assertRaisesRegex(
                        SyntheticStagingPriceStageError,
                        "credential differs",
                    ):
                        price_stage._read_secret_fd(
                            {price_stage._SECRET_FD_ENV: str(read_fd)}
                        )
                with self.assertRaises(OSError):
                    os.fstat(read_fd)

        for environment in ({}, {price_stage._SECRET_FD_ENV: "not-a-fd"}):
            with self.subTest(environment=environment), self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "credential channel differs",
            ):
                price_stage._read_secret_fd(environment)

    def test_root_rejects_arguments_ambient_authority_and_backup_names(self):
        environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage, "run_stopped_service_price_stage"
        ) as run:
            self.assertEqual(
                price_stage.main(["unexpected"], environment=environment),
                2,
            )
        run.assert_not_called()
        self.assertNotIn(DATABASE_PASSWORD_ENV, environment)

        for name in (
            "DATABASE_URL",
            "PROCUREMENT_STORAGE_ROOT",
            "BUFFALO_STAGING_PRICE_BACKUP_LABEL",
            "BUFFALO_STAGING_PRICE_BACKUP_ROOT",
        ):
            with self.subTest(name=name), mock.patch.object(
                price_stage, "load_bootstrap_inputs"
            ) as load:
                candidate = {DATABASE_PASSWORD_ENV: self.SECRET, name: ""}
                with self.assertRaisesRegex(
                    SyntheticStagingPriceStageError,
                    "ambient authority",
                ):
                    price_stage.run_stopped_service_price_stage(candidate)
                load.assert_not_called()
                self.assertNotIn(DATABASE_PASSWORD_ENV, candidate)

    def test_root_child_contract_contains_no_secret_or_ambient_authority(self):
        process = mock.MagicMock()
        process.returncode = 0
        environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage,
            "load_bootstrap_inputs",
            return_value=(self.inputs(), self.SECRET),
        ), mock.patch.object(
            price_stage, "_validate_root_identity_and_accounts"
        ), mock.patch.object(
            price_stage, "_validate_layout_root_separation"
        ), mock.patch.object(
            price_stage, "_validate_volume_mount"
        ), mock.patch.object(
            price_stage, "_validate_storage_root"
        ), mock.patch.object(
            price_stage.subprocess, "Popen", return_value=process
        ) as popen, mock.patch.object(
            price_stage,
            "_read_bounded_child_output",
            return_value=self.proof(),
        ):
            proof = price_stage.run_stopped_service_price_stage(environment)
        self.assertEqual(proof["status"], "VALIDATED")
        self.assertNotIn(DATABASE_PASSWORD_ENV, environment)
        argv = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        rendered = json.dumps([argv, kwargs["env"]], sort_keys=True)
        self.assertNotIn(self.SECRET, rendered)
        self.assertEqual(
            argv[-3:],
            ["-B", "-m", "procurement_os.synthetic_staging_price_stage"],
        )
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(kwargs["user"], 1102)
        self.assertEqual(kwargs["group"], 1202)
        self.assertEqual(kwargs["extra_groups"], ())
        self.assertEqual(kwargs["umask"], 0o077)
        self.assertTrue(kwargs["start_new_session"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(tuple(kwargs["pass_fds"]), (int(
            kwargs["env"][price_stage._SECRET_FD_ENV]
        ),))
        self.assertNotIn("PGPASSWORD", kwargs["env"])
        self.assertNotIn("PGPASSFILE", kwargs["env"])
        self.assertNotIn("PROCUREMENT_STORAGE_ROOT", kwargs["env"])
        self.assertNotIn("BUFFALO_STAGING_OWNER_VERIFIER", kwargs["env"])

    def test_root_timeout_kills_and_reaps_the_child_group(self):
        process = mock.MagicMock()
        process.pid = 4242
        process.poll.return_value = None
        environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage,
            "load_bootstrap_inputs",
            return_value=(self.inputs(), self.SECRET),
        ), mock.patch.object(
            price_stage, "_validate_root_identity_and_accounts"
        ), mock.patch.object(
            price_stage, "_validate_layout_root_separation"
        ), mock.patch.object(
            price_stage, "_validate_volume_mount"
        ), mock.patch.object(
            price_stage, "_validate_storage_root"
        ), mock.patch.object(
            price_stage.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            price_stage,
            "_read_bounded_child_output",
            side_effect=subprocess.TimeoutExpired(["python"], 300),
        ), mock.patch.object(price_stage.os, "killpg") as killpg:
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "child did not complete",
            ):
                price_stage.run_stopped_service_price_stage(environment)
        killpg.assert_called_once_with(4242, price_stage.signal.SIGKILL)
        process.wait.assert_called_once_with()
        self.assertNotIn(DATABASE_PASSWORD_ENV, environment)

    def test_root_rejects_incomplete_or_tampered_child_proof(self):
        process = mock.MagicMock()
        process.returncode = 0
        incomplete = json.dumps(
            {
                "contract": STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
                "status": "VALIDATED",
            }
        ).encode("ascii")
        environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage,
            "load_bootstrap_inputs",
            return_value=(self.inputs(), self.SECRET),
        ), mock.patch.object(
            price_stage, "_validate_root_identity_and_accounts"
        ), mock.patch.object(
            price_stage, "_validate_layout_root_separation"
        ), mock.patch.object(
            price_stage, "_validate_volume_mount"
        ), mock.patch.object(
            price_stage, "_validate_storage_root"
        ), mock.patch.object(
            price_stage.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            price_stage,
            "_read_bounded_child_output",
            return_value=incomplete,
        ):
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "proof differs",
            ):
                price_stage.run_stopped_service_price_stage(environment)

        valid = json.loads(self.proof())
        mutations = {
            "extra-key": {**valid, "unexpected": "authority"},
            "contract": {**valid, "contract": "UNREGISTERED"},
            "fixed-source": {**valid, "source_ref": "caller-selected"},
            "fixed-size": {**valid, "source_bytes": 1_591},
            "fixed-raw": {**valid, "raw_sha256": "0" * 64},
            "fixed-target": {
                **valid,
                "target_attestation_sha256": "0" * 64,
            },
            "hash-shape": {**valid, "declaration_sha256": "A" * 64},
            "uuid-shape": {**valid, "batch_id": "not-a-uuid"},
            "status": {**valid, "status": "VERIFIED_FUTURE"},
            "boolean-type": {**valid, "idempotent_replay": 1},
            "second-boolean-type": {
                **valid,
                "ambiguous_commit_recovered": "false",
            },
        }
        for name in (
            "declaration_sha256",
            "validation_fingerprint",
            "proposed_scope_membership_sha256",
            "staging_rows_sha256",
            "validation_issues_sha256",
            "unchanged_database_sha256",
            "unchanged_storage_sha256",
        ):
            mutations[f"invalid-{name}"] = {**valid, name: "z" * 64}
        for label, candidate in mutations.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "proof differs",
            ):
                price_stage._validate_operator_proof(candidate)

    def test_child_output_reader_enforces_limit_while_streaming(self):
        process = subprocess.Popen(
            [
                price_stage.sys.executable,
                "-c",
                (
                    "import sys;"
                    f"sys.stdout.buffer.write(b'x'*{price_stage._MAX_OUTPUT_BYTES + 1})"
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "output exceeds limit",
            ):
                price_stage._read_bounded_child_output(
                    process,
                    timeout_seconds=5,
                )
        finally:
            if process.poll() is None:
                os.killpg(process.pid, price_stage.signal.SIGKILL)
                process.wait()

        process = mock.MagicMock()
        process.pid = 4343
        process.poll.return_value = None
        environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage,
            "load_bootstrap_inputs",
            return_value=(self.inputs(), self.SECRET),
        ), mock.patch.object(
            price_stage, "_validate_root_identity_and_accounts"
        ), mock.patch.object(
            price_stage, "_validate_layout_root_separation"
        ), mock.patch.object(
            price_stage, "_validate_volume_mount"
        ), mock.patch.object(
            price_stage, "_validate_storage_root"
        ), mock.patch.object(
            price_stage.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            price_stage,
            "_read_bounded_child_output",
            side_effect=SyntheticStagingPriceStageError(
                "synthetic price-stage child output exceeds limit"
            ),
        ), mock.patch.object(price_stage.os, "killpg") as killpg:
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "output exceeds limit",
            ):
                price_stage.run_stopped_service_price_stage(environment)
        killpg.assert_called_once_with(4343, price_stage.signal.SIGKILL)
        process.wait.assert_called_once_with()

    def test_ambiguous_result_is_observed_and_never_blindly_resubmitted(self):
        target = mock.Mock()
        first = mock.Mock()
        second = mock.Mock()
        with mock.patch.object(
            price_stage, "load_registered_operator_fixture", return_value=mock.Mock()
        ), mock.patch.object(
            price_stage,
            "_attest_and_lock",
            side_effect=((first, "lock"), (second, "lock")),
        ), mock.patch.object(
            price_stage,
            "_stage_once",
            side_effect=psycopg.OperationalError("outcome unavailable"),
        ) as stage, mock.patch.object(
            price_stage, "_batch_exists", return_value=False
        ) as exists, mock.patch.object(
            price_stage, "_close_locked_connection"
        ):
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "outcome is unknown",
            ):
                stage_registered_operator_fixture(
                    target=target,
                    password=self.SECRET,
                    storage=mock.Mock(),
                )
        self.assertEqual(stage.call_count, 1)
        exists.assert_called_once_with(second, mock.ANY)

    def test_retry_policy_is_bounded_and_exact_existing_outcome_recovers(self):
        target = mock.Mock()
        connections = [mock.Mock(), mock.Mock()]
        proof = {"status": "VALIDATED"}
        with mock.patch.object(
            price_stage, "load_registered_operator_fixture", return_value=mock.Mock()
        ), mock.patch.object(
            price_stage,
            "_attest_and_lock",
            side_effect=((connections[0], "lock"), (connections[1], "lock")),
        ), mock.patch.object(
            price_stage,
            "_stage_once",
            side_effect=(psycopg.OperationalError("commit unknown"), proof),
        ) as stage, mock.patch.object(
            price_stage, "_batch_exists", return_value=True
        ) as exists, mock.patch.object(
            price_stage, "_close_locked_connection"
        ):
            observed = stage_registered_operator_fixture(
                target=target,
                password=self.SECRET,
                storage=mock.Mock(),
            )
        self.assertIs(observed, proof)
        self.assertEqual(stage.call_count, 2)
        exists.assert_called_once()
        self.assertTrue(
            stage.call_args_list[1].kwargs["ambiguous_commit_recovered"]
        )

        retry_connections = [mock.Mock(), mock.Mock()]
        with mock.patch.object(
            price_stage, "load_registered_operator_fixture", return_value=mock.Mock()
        ), mock.patch.object(
            price_stage,
            "_attest_and_lock",
            side_effect=((retry_connections[0], "lock"), (retry_connections[1], "lock")),
        ), mock.patch.object(
            price_stage,
            "_stage_once",
            side_effect=(psycopg.errors.SerializationFailure("retry"), proof),
        ) as stage, mock.patch.object(
            price_stage, "_close_locked_connection"
        ):
            self.assertIs(
                stage_registered_operator_fixture(
                    target=target,
                    password=self.SECRET,
                    storage=mock.Mock(),
                ),
                proof,
            )
        self.assertEqual(stage.call_count, 2)

    def test_public_stage_rejects_operator_role_and_main_sanitizes_failure(self):
        principal = Principal(
            principal_ref=price_stage.STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL,
            role_ref=price_stage.STOPPED_SERVICE_PRICE_STAGE_ROLE,
            authn_context_sha256=(
                price_stage.STOPPED_SERVICE_PRICE_STAGE_AUTHN_SHA256
            ),
        )
        with self.assertRaisesRegex(
            SyntheticPriceReplacementError,
            "PRINCIPAL_DIFFERS",
        ):
            stage_and_validate_declared_price_book(
                mock.Mock(),
                mock.Mock(),
                csv_bytes=b"not-observed",
                principal=principal,
                expected_declaration_sha256="a" * 64,
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage,
            "run_stopped_service_price_stage",
            side_effect=RuntimeError(
                f"{self.SECRET}:{REGISTERED_OPERATOR_BOOK_SHA256}"
            ),
        ), mock.patch.object(price_stage.sys, "stdout", stdout), mock.patch.object(
            price_stage.sys, "stderr", stderr
        ):
            self.assertEqual(price_stage.main([], environment=environment), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "Buffalo stopped-service price stage failed\n",
        )
        self.assertNotIn(self.SECRET, stderr.getvalue())
        self.assertNotIn(REGISTERED_OPERATOR_BOOK_SHA256, stderr.getvalue())

    def test_signal_during_spawn_is_deferred_until_child_can_be_reaped(self):
        process = mock.MagicMock()
        process.pid = 8181
        process.poll.side_effect = (None, 1)
        handlers: list[object] = []

        def install(_signum, handler):
            if callable(handler):
                handlers.append(handler)
            return price_stage.signal.SIG_DFL

        def spawn(*_args, **_kwargs):
            handlers[0](price_stage.signal.SIGTERM, None)
            return process

        environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage,
            "load_bootstrap_inputs",
            return_value=(self.inputs(), self.SECRET),
        ), mock.patch.object(
            price_stage, "_validate_root_identity_and_accounts"
        ), mock.patch.object(
            price_stage, "_validate_layout_root_separation"
        ), mock.patch.object(
            price_stage, "_validate_volume_mount"
        ), mock.patch.object(
            price_stage, "_validate_storage_root"
        ), mock.patch.object(
            price_stage.subprocess, "Popen", side_effect=spawn
        ), mock.patch.object(
            price_stage.signal, "signal", side_effect=install
        ), mock.patch.object(price_stage.os, "killpg") as killpg:
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "child did not complete",
            ):
                price_stage.run_stopped_service_price_stage(environment)
        killpg.assert_called_once_with(8181, price_stage.signal.SIGKILL)
        process.wait.assert_called_once_with()

        cleanup_process = mock.MagicMock()
        cleanup_process.pid = 8282
        cleanup_handlers: list[object] = []

        def install_cleanup_handler(_signum, handler):
            if callable(handler):
                cleanup_handlers.append(handler)
            return price_stage.signal.SIG_DFL

        def signal_during_cleanup():
            cleanup_handlers[0](price_stage.signal.SIGTERM, None)
            return None

        cleanup_process.poll.side_effect = signal_during_cleanup
        cleanup_environment = {DATABASE_PASSWORD_ENV: self.SECRET}
        with mock.patch.object(
            price_stage,
            "load_bootstrap_inputs",
            return_value=(self.inputs(), self.SECRET),
        ), mock.patch.object(
            price_stage, "_validate_root_identity_and_accounts"
        ), mock.patch.object(
            price_stage, "_validate_layout_root_separation"
        ), mock.patch.object(
            price_stage, "_validate_volume_mount"
        ), mock.patch.object(
            price_stage, "_validate_storage_root"
        ), mock.patch.object(
            price_stage.subprocess, "Popen", return_value=cleanup_process
        ), mock.patch.object(
            price_stage,
            "_read_bounded_child_output",
            side_effect=SyntheticStagingPriceStageError(
                "synthetic price-stage child output exceeds limit"
            ),
        ), mock.patch.object(
            price_stage.signal,
            "signal",
            side_effect=install_cleanup_handler,
        ) as signal_install, mock.patch.object(
            price_stage.os, "killpg"
        ) as cleanup_killpg:
            with self.assertRaisesRegex(
                SyntheticStagingPriceStageError,
                "output exceeds limit",
            ):
                price_stage.run_stopped_service_price_stage(
                    cleanup_environment
                )
        cleanup_killpg.assert_called_once_with(
            8282, price_stage.signal.SIGKILL
        )
        cleanup_process.wait.assert_called_once_with()
        self.assertEqual(signal_install.call_count, 6)
        self.assertNotIn(DATABASE_PASSWORD_ENV, cleanup_environment)

    def test_unprivileged_child_revalidates_identity_storage_and_target(self):
        target = mock.Mock()
        layout = mock.Mock()
        storage = mock.Mock()
        proof = {"contract": STOPPED_SERVICE_PRICE_STAGE_CONTRACT}
        environment = {
            "BUFFALO_STAGING_VOLUME_ROOT": "/data",
            price_stage._SECRET_FD_ENV: "7",
        }
        with mock.patch.object(price_stage.os, "getuid", return_value=1102), \
             mock.patch.object(price_stage.os, "geteuid", return_value=1102), \
             mock.patch.object(price_stage.os, "getgid", return_value=1202), \
             mock.patch.object(price_stage.os, "getegid", return_value=1202), \
             mock.patch.object(price_stage.os, "getgroups", return_value=[]), \
             mock.patch.object(price_stage.os, "getsid", return_value=55), \
             mock.patch.object(price_stage.os, "getpid", return_value=55), \
             mock.patch.object(price_stage.os, "getpgid", return_value=55), \
             mock.patch.object(price_stage.os, "umask", return_value=0o077), \
             mock.patch.object(
                 price_stage,
                 "load_registered_operator_fixture",
                 return_value=price_stage.RegisteredOperatorFixture(
                     csv_bytes=b"fixture",
                     content_sha256=REGISTERED_OPERATOR_BOOK_SHA256,
                     byte_count=REGISTERED_OPERATOR_BOOK_BYTES,
                 ),
             ), \
             mock.patch.object(price_stage, "_read_secret_fd", return_value=self.SECRET), \
             mock.patch.object(price_stage, "target_from_environment", return_value=target), \
             mock.patch.object(price_stage.StagingBootstrapLayout, "build", return_value=layout), \
             mock.patch.object(price_stage, "_validate_storage_root") as validate, \
             mock.patch.object(price_stage, "LocalFilesystemStorage", return_value=storage), \
             mock.patch.object(price_stage, "stage_registered_operator_fixture", return_value=proof) as stage:
            observed = price_stage._run_child(environment)
        self.assertEqual(observed, proof)
        validate.assert_called_once_with(layout)
        stage.assert_called_once_with(
            target=target,
            password=self.SECRET,
            storage=storage,
        )

    def test_operator_core_is_not_reachable_from_gateway_or_api(self):
        source_root = Path(price_stage.__file__).resolve().parent
        for name in ("api.py", "staging_gateway.py", "staging_routes.py"):
            source = (source_root / name).read_text(encoding="utf-8")
            self.assertNotIn("synthetic_staging_price_stage", source)
            self.assertNotIn("procurement.price.stage", source)
            self.assertNotIn(REGISTERED_OPERATOR_BOOK_REF, source)


class SyntheticStagingPriceStagePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        fixture = staging_fixture.SyntheticStagingDatabasePostgresTests
        fixture.setUpClass()
        cls.fixture = fixture
        cls.target = fixture._target
        cls.runtime_url = fixture._runtime_url
        cls.runtime_secret = fixture.RUNTIME_SECRET
        cls.storage_temporary = tempfile.TemporaryDirectory(
            prefix="buffalo-stopped-price-stage-"
        )
        cls.storage = LocalFilesystemStorage(cls.storage_temporary.name)
        cls.environment = {
            "DATABASE_URL": cls.runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": fixture.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(fixture._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": staging_fixture.EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": staging_fixture.EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": staging_fixture.EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": (
                staging_fixture.EXPECTED_POSTGRES_SERVICE_ID
            ),
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
            "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
        }

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.storage_temporary.cleanup()
        finally:
            cls.fixture.doClassCleanups()
        super().tearDownClass()

    def test_01_refusals_and_real_ambiguous_commit_recovery(self):
        fixture = load_registered_operator_fixture()
        raw_key = f"price-books/raw/{fixture.content_sha256}.csv"
        principal = Principal(
            principal_ref=price_stage.STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL,
            role_ref=price_stage.STOPPED_SERVICE_PRICE_STAGE_ROLE,
            authn_context_sha256=(
                price_stage.STOPPED_SERVICE_PRICE_STAGE_AUTHN_SHA256
            ),
        )
        with mock.patch.dict(os.environ, self.environment, clear=False), \
             psycopg.connect(self.runtime_url, autocommit=True) as blocker:
            lock_name = acquire_database_lifecycle_lock(
                blocker,
                database="buffalo_synthetic_staging_demo",
            )
            try:
                with self.assertRaisesRegex(
                    DatabaseLifecycleError,
                    "operation is active",
                ):
                    stage_registered_operator_fixture(
                        target=self.target,
                        password=self.runtime_secret,
                        storage=self.storage,
                    )

                wrong_lock_name = database_lifecycle_lock_name(
                    "some_other_database"
                )
                with psycopg.connect(
                    self.runtime_url,
                    autocommit=True,
                ) as effect_conn:
                    self.assertEqual(
                        effect_conn.execute(
                            "SELECT pg_try_advisory_lock("
                            "pg_catalog.hashtextextended(%s,0))",
                            (wrong_lock_name,),
                        ).fetchone(),
                        (True,),
                    )
                    declaration = registered_target_declaration(effect_conn)
                    try:
                        with self.assertRaisesRegex(
                            DatabaseLifecycleError,
                            "lifecycle identity differs",
                        ):
                            _stage_and_validate_declared_price_book(
                                effect_conn,
                                self.storage,
                                csv_bytes=fixture.csv_bytes,
                                principal=principal,
                                expected_declaration_sha256=declaration[
                                    "declaration_sha256"
                                ],
                                operator_stage=True,
                                operator_lock_name=wrong_lock_name,
                            )
                    finally:
                        self.assertEqual(
                            effect_conn.execute(
                                "SELECT pg_advisory_unlock("
                                "pg_catalog.hashtextextended(%s,0))",
                                (wrong_lock_name,),
                            ).fetchone(),
                            (True,),
                        )
            finally:
                release_database_lifecycle_lock(blocker, lock_name=lock_name)
        with psycopg.connect(self.runtime_url) as conn:
            count = conn.execute(
                "SELECT count(*) FROM price_book_batches"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertFalse(self.storage.exists(raw_key))

        wrong_target = replace(
            self.target,
            transfer_manifest_sha256="d" * 64,
        )
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(Exception, "provenance differs"):
                stage_registered_operator_fixture(
                    target=wrong_target,
                    password=self.runtime_secret,
                    storage=self.storage,
                )
        with psycopg.connect(self.runtime_url) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM price_book_batches").fetchone(),
                (0,),
            )
        self.assertFalse(self.storage.exists(raw_key))

        with psycopg.connect(
            self.fixture._postgres_admin_url,
            autocommit=True,
        ) as admin:
            admin.execute(
                f"ALTER ROLE {staging_fixture.RUNTIME_LOGIN} "
                "CONNECTION LIMIT 1"
            )
        try:
            with mock.patch.dict(os.environ, self.environment, clear=False):
                with self.assertRaisesRegex(Exception, "role flags differ"):
                    stage_registered_operator_fixture(
                        target=self.target,
                        password=self.runtime_secret,
                        storage=self.storage,
                    )
        finally:
            with psycopg.connect(
                self.fixture._postgres_admin_url,
                autocommit=True,
            ) as admin:
                admin.execute(
                    f"ALTER ROLE {staging_fixture.RUNTIME_LOGIN} "
                    "CONNECTION LIMIT -1"
                )
        with psycopg.connect(self.runtime_url) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM price_book_batches").fetchone(),
                (0,),
            )
        self.assertFalse(self.storage.exists(raw_key))

        with psycopg.connect(
            self.fixture._target_admin_url,
            autocommit=True,
        ) as admin:
            admin.execute("CREATE SCHEMA option_b_operator_catalog_drift")
        try:
            with mock.patch.dict(os.environ, self.environment, clear=False):
                with self.assertRaisesRegex(
                    Exception,
                    "semantic catalog envelope differs|catalog.*differs",
                ):
                    stage_registered_operator_fixture(
                        target=self.target,
                        password=self.runtime_secret,
                        storage=self.storage,
                    )
        finally:
            with psycopg.connect(
                self.fixture._target_admin_url,
                autocommit=True,
            ) as admin:
                admin.execute(
                    "DROP SCHEMA option_b_operator_catalog_drift"
                )
        with psycopg.connect(self.runtime_url) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM price_book_batches").fetchone(),
                (0,),
            )
        self.assertFalse(self.storage.exists(raw_key))

        with mock.patch.dict(os.environ, self.environment, clear=False), \
             psycopg.connect(self.runtime_url) as conn:
            declaration = registered_target_declaration(conn)
            conn.rollback()

        dirty_staging = (
            Path(self.storage_temporary.name) / ".immutable-staging"
        )
        dirty_staging.mkdir(mode=0o700)
        (dirty_staging / "unexpected").write_bytes(b"incomplete")
        try:
            with mock.patch.dict(os.environ, self.environment, clear=False):
                with self.assertRaisesRegex(
                    SyntheticStagingPriceStageError,
                    "storage staging differs",
                ):
                    stage_registered_operator_fixture(
                        target=self.target,
                        password=self.runtime_secret,
                        storage=self.storage,
                    )
        finally:
            (dirty_staging / "unexpected").unlink()
            dirty_staging.rmdir()
        with psycopg.connect(self.runtime_url) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM price_book_batches").fetchone(),
                (0,),
            )
        self.assertFalse(self.storage.exists(raw_key))

        with mock.patch.dict(os.environ, self.environment, clear=False):
            conn, lock_name = price_stage._attest_and_lock(
                target=self.target,
                password=self.runtime_secret,
            )
            try:
                with self.assertRaisesRegex(
                    SyntheticPriceReplacementError,
                    "DECLARATION_HASH_DIFFERS",
                ):
                    _stage_and_validate_declared_price_book(
                        conn,
                        self.storage,
                        csv_bytes=fixture.csv_bytes,
                        principal=principal,
                        expected_declaration_sha256="0" * 64,
                        operator_stage=True,
                        operator_lock_name=lock_name,
                    )
            finally:
                price_stage._close_locked_connection(conn, lock_name)
        with psycopg.connect(self.runtime_url) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM price_book_batches").fetchone(),
                (0,),
            )
        self.assertFalse(self.storage.exists(raw_key))

        committed: list[dict[str, object]] = []
        observed_connections: list[object] = []
        real_stage_once = price_stage._stage_once

        def committed_then_connection_lost(*args, **kwargs):
            observed_connections.append(args[0])
            observed = real_stage_once(*args, **kwargs)
            committed.append(observed)
            if len(committed) == 1:
                raise psycopg.OperationalError("result unavailable after commit")
            return observed

        with mock.patch.dict(os.environ, self.environment, clear=False), \
             mock.patch.object(
                 price_stage,
                 "_stage_once",
                 side_effect=committed_then_connection_lost,
             ):
            recovered = stage_registered_operator_fixture(
                target=self.target,
                password=self.runtime_secret,
                storage=self.storage,
            )
        self.assertEqual(len(committed), 2)
        self.assertEqual(len(observed_connections), 2)
        self.assertIsNot(observed_connections[0], observed_connections[1])
        self.assertTrue(recovered["ambiguous_commit_recovered"])
        self.assertTrue(recovered["idempotent_replay"])
        self.assertEqual(recovered["batch_id"], committed[0]["batch_id"])
        self.assertEqual(
            recovered["unchanged_database_sha256"],
            committed[0]["unchanged_database_sha256"],
        )
        self.assertEqual(
            recovered["unchanged_storage_sha256"],
            committed[0]["unchanged_storage_sha256"],
        )
        with mock.patch.dict(os.environ, self.environment, clear=False), \
             psycopg.connect(self.runtime_url) as conn:
            self.assertEqual(
                conn.execute(
                    """SELECT
                         (SELECT count(*) FROM price_book_batches),
                         (SELECT count(*) FROM price_book_scope_memberships),
                         (SELECT count(*) FROM price_book_promotion_events),
                         (SELECT count(*) FROM prices
                           WHERE source_price_book_batch_id IS NOT NULL)"""
                ).fetchone(),
                (1, 0, 0, 0),
            )
            conn.rollback()
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError,
                "LOCK_ABSENT",
            ):
                _stage_and_validate_declared_price_book(
                    conn,
                    self.storage,
                    csv_bytes=fixture.csv_bytes,
                    principal=principal,
                    expected_declaration_sha256=declaration[
                        "declaration_sha256"
                    ],
                    operator_stage=True,
                    operator_lock_name=None,
                )
            conn.rollback()

    def test_02_exact_stage_replay_tamper_and_advanced_refusal(self):
        with mock.patch.dict(os.environ, self.environment, clear=False):
            first = stage_registered_operator_fixture(
                target=self.target,
                password=self.runtime_secret,
                storage=self.storage,
            )
            replay = stage_registered_operator_fixture(
                target=self.target,
                password=self.runtime_secret,
                storage=self.storage,
            )
        self.assertEqual(first["status"], "VALIDATED")
        self.assertTrue(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertFalse(first["ambiguous_commit_recovered"])
        self.assertFalse(replay["ambiguous_commit_recovered"])
        self.assertEqual(replay["batch_id"], first["batch_id"])
        self.assertEqual(
            replay["unchanged_database_sha256"],
            first["unchanged_database_sha256"],
        )
        self.assertEqual(
            replay["unchanged_storage_sha256"],
            first["unchanged_storage_sha256"],
        )
        self.assertEqual(
            replay["proposed_scope_membership_sha256"],
            first["proposed_scope_membership_sha256"],
        )
        self.assertRegex(first["staging_rows_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(first["validation_issues_sha256"], r"^[0-9a-f]{64}$")
        with psycopg.connect(self.runtime_url) as conn:
            staged_effects = conn.execute(
                """SELECT
                     (SELECT count(*) FROM price_book_scope_memberships),
                     (SELECT count(*) FROM price_book_promotion_events),
                     (SELECT count(*) FROM prices
                       WHERE source_price_book_batch_id=%s)""",
                (first["batch_id"],),
            ).fetchone()
        self.assertEqual(staged_effects, (0, 0, 0))

        with psycopg.connect(
            self.fixture._target_admin_url,
            autocommit=True,
        ) as admin:
            admin.execute("SET session_replication_role = replica")
            admin.execute(
                """UPDATE qa_mapping_test.price_book_batches
                      SET validation_evidence =
                          validation_evidence || '{"unexpected":true}'::jsonb
                    WHERE price_book_batch_id=%s""",
                (first["batch_id"],),
            )
            admin.execute("RESET session_replication_role")
        try:
            with mock.patch.dict(os.environ, self.environment, clear=False):
                with self.assertRaisesRegex(
                    SyntheticPriceReplacementError,
                    "REPLAY_DIFFERS",
                ):
                    stage_registered_operator_fixture(
                        target=self.target,
                        password=self.runtime_secret,
                        storage=self.storage,
                    )
        finally:
            with psycopg.connect(
                self.fixture._target_admin_url,
                autocommit=True,
            ) as admin:
                admin.execute("SET session_replication_role = replica")
                admin.execute(
                    """UPDATE qa_mapping_test.price_book_batches
                          SET validation_evidence =
                              validation_evidence - 'unexpected'
                        WHERE price_book_batch_id=%s""",
                    (first["batch_id"],),
                )
                admin.execute("RESET session_replication_role")

        fixture = load_registered_operator_fixture()
        raw_key = f"price-books/raw/{fixture.content_sha256}.csv"
        raw_path = Path(self.storage_temporary.name) / raw_key
        principal = Principal(
            principal_ref="owner:local-task9",
            role_ref="procurement.price.approve",
            authn_context_sha256="c" * 64,
        )

        forged_bytes = b"x" * REGISTERED_OPERATOR_BOOK_BYTES
        forged_sha256 = hashlib.sha256(forged_bytes).hexdigest()
        forged_key = f"price-books/raw/{forged_sha256}.csv"
        forged_path = Path(self.storage_temporary.name) / forged_key
        self.storage.put_bytes_once(forged_key, forged_bytes)
        with psycopg.connect(
            self.fixture._target_admin_url,
            autocommit=True,
        ) as admin:
            admin.execute("SET session_replication_role = replica")
            admin.execute(
                """UPDATE qa_mapping_test.price_book_batches
                      SET content_sha256=%s,raw_storage_key=%s
                    WHERE price_book_batch_id=%s""",
                (forged_sha256, forged_key, first["batch_id"]),
            )
            admin.execute("RESET session_replication_role")
        try:
            with mock.patch.dict(os.environ, self.environment, clear=False), \
                 psycopg.connect(self.runtime_url) as conn:
                with self.assertRaisesRegex(
                    SyntheticPriceReplacementError,
                    "price-stage evidence differs",
                ):
                    preview_declared_price_confirmation(
                        conn,
                        self.storage,
                        batch_id=first["batch_id"],
                        confirmation_idempotency_key=(
                            "task9-option-b-forged-raw-v1"
                        ),
                        warning_review_reason=(
                            "Reviewed four fabricated synthetic price changes."
                        ),
                        principal=principal,
                    )
                conn.rollback()
        finally:
            with psycopg.connect(
                self.fixture._target_admin_url,
                autocommit=True,
            ) as admin:
                admin.execute("SET session_replication_role = replica")
                admin.execute(
                    """UPDATE qa_mapping_test.price_book_batches
                          SET content_sha256=%s,raw_storage_key=%s
                        WHERE price_book_batch_id=%s""",
                    (
                        REGISTERED_OPERATOR_BOOK_SHA256,
                        raw_key,
                        first["batch_id"],
                    ),
                )
                admin.execute("RESET session_replication_role")
            forged_path.unlink()

        alias = raw_path.with_name("raw-hardlink-alias.csv")
        os.link(raw_path, alias)
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError,
                "IMMUTABLE_STORAGE_DIFFERS",
            ):
                stage_registered_operator_fixture(
                    target=self.target,
                    password=self.runtime_secret,
                    storage=self.storage,
                )
        alias.unlink()
        raw_path.write_bytes(b"tampered")
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError,
                "REPLAY_DIFFERS|IMMUTABLE_STORAGE_DIFFERS",
            ):
                stage_registered_operator_fixture(
                    target=self.target,
                    password=self.runtime_secret,
                    storage=self.storage,
                )
        self.assertEqual(raw_path.read_bytes(), b"tampered")
        raw_path.write_bytes(fixture.csv_bytes)
        raw_path.unlink()
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError,
                "IMMUTABLE_STORAGE_DIFFERS",
            ):
                stage_registered_operator_fixture(
                    target=self.target,
                    password=self.runtime_secret,
                    storage=self.storage,
                )
        self.assertFalse(raw_path.exists())
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(fixture.csv_bytes)
        raw_path.chmod(0o600)

        os.link(raw_path, alias)
        with mock.patch.dict(os.environ, self.environment, clear=False), \
             psycopg.connect(self.runtime_url) as conn:
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError,
                "IMMUTABLE_STORAGE_DIFFERS",
            ):
                preview_declared_price_confirmation(
                    conn,
                    self.storage,
                    batch_id=first["batch_id"],
                    confirmation_idempotency_key=(
                        "task9-option-b-confirmation-v1"
                    ),
                    warning_review_reason=(
                        "Reviewed four fabricated synthetic price changes."
                    ),
                    principal=principal,
                )
            conn.rollback()
        alias.unlink()
        with mock.patch.dict(os.environ, self.environment, clear=False), \
             psycopg.connect(self.runtime_url) as conn:
            preview = preview_declared_price_confirmation(
                conn,
                self.storage,
                batch_id=first["batch_id"],
                confirmation_idempotency_key="task9-option-b-confirmation-v1",
                warning_review_reason=(
                    "Reviewed four fabricated synthetic price changes."
                ),
                principal=principal,
            )
            confirmed = confirm_declared_price_book(
                conn,
                self.storage,
                batch_id=first["batch_id"],
                confirmation_idempotency_key="task9-option-b-confirmation-v1",
                expected_preview_sha256=preview["preview_sha256"],
                confirm="CONFIRM",
                warning_review_reason=(
                    "Reviewed four fabricated synthetic price changes."
                ),
                principal=principal,
            )
        self.assertEqual(confirmed["status"], "VERIFIED_FUTURE")
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError,
                "REPLAY_DIFFERS",
            ):
                stage_registered_operator_fixture(
                    target=self.target,
                    password=self.runtime_secret,
                    storage=self.storage,
                )
        with psycopg.connect(self.runtime_url) as conn:
            facts = conn.execute(
                """SELECT status,
                          (SELECT count(*) FROM price_book_batches),
                          (SELECT count(*) FROM price_book_promotion_events),
                          (SELECT count(*) FROM prices
                            WHERE source_price_book_batch_id=%s)
                     FROM price_book_batches WHERE price_book_batch_id=%s""",
                (first["batch_id"], first["batch_id"]),
            ).fetchone()
        self.assertEqual(facts, ("VERIFIED_FUTURE", 1, 1, 4))


if __name__ == "__main__":
    unittest.main()

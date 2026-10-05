"""Stopped-service, exact-fixture staging for LOCAL Railway acceptance only."""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, MutableMapping
from uuid import UUID

import psycopg

from .database_lifecycle import (
    acquire_database_lifecycle_lock,
    assert_database_lifecycle_lock,
    release_database_lifecycle_lock,
)
from .local_backup_v2 import canonical_sha256, database_state_evidence
from .persistent_mapping import Principal
from .price_book import parse_price_book_csv
from .staging_bootstrap import (
    BACKUP_LABEL_ENV,
    DATABASE_PASSWORD_ENV,
    EXPECTED_TREE_ENV,
    RUNTIME_ROOT,
    STAGING_MANIFEST_SHA_ENV,
    SYNTHETIC_ACCOUNT,
    StagingBootstrapLayout,
    _database_target_environment,
    _validate_layout_root_separation,
    _validate_root_identity_and_accounts,
    _validate_volume_mount,
    load_bootstrap_inputs,
)
from .storage import LocalFilesystemStorage
from .synthetic_price_replacement import (
    _stage_and_validate_declared_price_book,
    registered_target_declaration,
)
from .synthetic_price_replacement_contract import (
    REGISTERED_OPERATOR_BOOK_BYTES,
    REGISTERED_OPERATOR_BOOK_PATH,
    REGISTERED_OPERATOR_BOOK_REF,
    REGISTERED_OPERATOR_BOOK_SHA256,
    STOPPED_SERVICE_PRICE_STAGE_AUTHN_SHA256,
    STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
    STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL,
    STOPPED_SERVICE_PRICE_STAGE_ROLE,
)
from .synthetic_staging_database import (
    EXPECTED_DATABASE,
    EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
    SyntheticStagingTarget,
    attest_runtime_connection,
    target_from_environment,
)


_CHILD_ENV = "BUFFALO_STOPPED_SERVICE_PRICE_STAGE_CHILD"
_SECRET_FD_ENV = "BUFFALO_STOPPED_SERVICE_PRICE_STAGE_SECRET_FD"
_MAX_SECRET_BYTES = 2_048
_MAX_OUTPUT_BYTES = 16_384
_CHILD_TIMEOUT_SECONDS = 300
_PROOF_KEYS = frozenset(
    {
        "contract",
        "source_ref",
        "source_bytes",
        "raw_sha256",
        "target_attestation_sha256",
        "batch_id",
        "status",
        "declaration_sha256",
        "validation_fingerprint",
        "proposed_scope_membership_sha256",
        "staging_rows_sha256",
        "validation_issues_sha256",
        "unchanged_database_sha256",
        "unchanged_storage_sha256",
        "idempotent_replay",
        "ambiguous_commit_recovered",
    }
)
_FORBIDDEN_OPERATOR_INPUTS = frozenset(
    {
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "PGPASSWORD",
        "PGPASSFILE",
        "PROCUREMENT_STORAGE_ROOT",
        BACKUP_LABEL_ENV,
        STAGING_MANIFEST_SHA_ENV,
        EXPECTED_TREE_ENV,
    }
)
_ALLOWED_RELATIONS = frozenset(
    {
        "price_book_batches",
        "price_book_staging_rows",
        "price_book_validation_issues",
    }
)
_ALLOWED_SEQUENCES = frozenset(
    {
        "price_book_batches_batch_generation_seq",
        "price_book_staging_rows_price_book_staging_row_id_seq",
        "price_book_validation_issues_price_book_validation_issue_id_seq",
    }
)


class SyntheticStagingPriceStageError(RuntimeError):
    """The fixed stopped-service staging boundary cannot be proved."""


class _OperatorInterrupted(BaseException):
    """A termination signal interrupted the bounded root composition."""


@dataclass(frozen=True)
class RegisteredOperatorFixture:
    csv_bytes: bytes
    content_sha256: str
    byte_count: int


def _source_path() -> Path:
    return Path(__file__).resolve().parents[2] / REGISTERED_OPERATOR_BOOK_PATH


def load_registered_operator_fixture() -> RegisteredOperatorFixture:
    """Read the sole registered source through one stable no-follow inode."""

    path = _source_path()
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != REGISTERED_OPERATOR_BOOK_BYTES
            or before.st_mode & 0o022
        ):
            raise SyntheticStagingPriceStageError(
                "registered price-stage source differs"
            )
        chunks: list[bytes] = []
        observed = 0
        while observed <= REGISTERED_OPERATOR_BOOK_BYTES:
            block = os.read(
                descriptor,
                min(64 * 1024, REGISTERED_OPERATOR_BOOK_BYTES + 1 - observed),
            )
            if not block:
                break
            chunks.append(block)
            observed += len(block)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        named = path.stat(follow_symlinks=False)
        if (
            len(raw) != REGISTERED_OPERATOR_BOOK_BYTES
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (before.st_dev, before.st_ino, before.st_size)
            != (named.st_dev, named.st_ino, named.st_size)
            or hashlib.sha256(raw).hexdigest()
            != REGISTERED_OPERATOR_BOOK_SHA256
        ):
            raise SyntheticStagingPriceStageError(
                "registered price-stage source differs"
            )
        return RegisteredOperatorFixture(
            csv_bytes=raw,
            content_sha256=REGISTERED_OPERATOR_BOOK_SHA256,
            byte_count=REGISTERED_OPERATOR_BOOK_BYTES,
        )
    except SyntheticStagingPriceStageError:
        raise
    except (OSError, ValueError) as exc:
        raise SyntheticStagingPriceStageError(
            "registered price-stage source is unavailable"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_storage_root(layout: StagingBootstrapLayout) -> None:
    root = layout.synthetic_storage
    try:
        if root.resolve(strict=True) != root:
            raise SyntheticStagingPriceStageError(
                "synthetic price-stage storage differs"
            )
        current = root
        while True:
            info = current.stat(follow_symlinks=False)
            if current.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise SyntheticStagingPriceStageError(
                    "synthetic price-stage storage differs"
                )
            if current == layout.volume_root:
                break
            if current == current.parent or not current.is_relative_to(
                layout.volume_root
            ):
                raise SyntheticStagingPriceStageError(
                    "synthetic price-stage storage differs"
                )
            current = current.parent
        info = root.stat(follow_symlinks=False)
    except SyntheticStagingPriceStageError:
        raise
    except OSError as exc:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage storage is unavailable"
        ) from exc
    if (
        stat.S_IMODE(info.st_mode) != 0o700
        or (info.st_uid, info.st_gid)
        != (SYNTHETIC_ACCOUNT.uid, SYNTHETIC_ACCOUNT.gid)
    ):
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage storage differs"
        )


def _read_secret_fd(environment: Mapping[str, str]) -> str:
    raw_descriptor = environment.get(_SECRET_FD_ENV, "")
    if not raw_descriptor.isascii() or not raw_descriptor.isdigit():
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage credential channel differs"
        )
    descriptor = int(raw_descriptor)
    if descriptor < 3:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage credential channel differs"
        )
    value = bytearray()
    try:
        info = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        endpoint = os.readlink(f"/proc/self/fd/{descriptor}")
        if (
            not stat.S_ISFIFO(info.st_mode)
            or flags & os.O_ACCMODE != os.O_RDONLY
            or endpoint != f"pipe:[{info.st_ino}]"
        ):
            raise SyntheticStagingPriceStageError(
                "synthetic price-stage credential channel differs"
            )
        os.set_inheritable(descriptor, False)
        while len(value) <= _MAX_SECRET_BYTES:
            block = os.read(
                descriptor,
                min(512, _MAX_SECRET_BYTES + 1 - len(value)),
            )
            if not block:
                break
            value.extend(block)
    except SyntheticStagingPriceStageError:
        raise
    except OSError as exc:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage credential channel is unavailable"
        ) from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        if (
            not value
            or len(value) > _MAX_SECRET_BYTES
            or any(character in value for character in (0, 10, 13))
        ):
            raise ValueError
        return bytes(value).decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage credential differs"
        ) from exc
    finally:
        for index in range(len(value)):
            value[index] = 0


def _attest_and_lock(
    *, target: SyntheticStagingTarget, password: str
) -> tuple[Any, str]:
    conn = psycopg.connect(
        target.database_url,
        password=password,
        connect_timeout=5,
        autocommit=False,
    )
    try:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '10000ms'")
        conn.execute("SET LOCAL lock_timeout = '2000ms'")
        conn.execute("SET LOCAL idle_in_transaction_session_timeout = '15000ms'")
        identity = attest_runtime_connection(conn, target)
        if identity != EXPECTED_RUNTIME_ATTESTATION_IDENTITY:
            raise SyntheticStagingPriceStageError(
                "synthetic price-stage target differs"
            )
        conn.rollback()
        conn.autocommit = True
        lock_name = acquire_database_lifecycle_lock(
            conn, database=EXPECTED_DATABASE
        )
        assert_database_lifecycle_lock(conn, lock_name=lock_name)
        return conn, lock_name
    except BaseException:
        conn.close()
        raise


def _close_locked_connection(conn: Any, lock_name: str) -> None:
    try:
        if not bool(getattr(conn, "closed", False)):
            release_database_lifecycle_lock(conn, lock_name=lock_name)
    finally:
        conn.close()


def _protected_database_sha256(
    conn: Any,
    target: SyntheticStagingTarget,
    *,
    vendor_name: str,
    batch_ref: str,
) -> str:
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if (
            attest_runtime_connection(conn, target)
            != EXPECTED_RUNTIME_ATTESTATION_IDENTITY
        ):
            raise SyntheticStagingPriceStageError(
                "synthetic price-stage target differs"
            )
        state = database_state_evidence(
            conn,
            schema="qa_mapping_test",
            staging_runtime=True,
        )
        unrelated_price_state: dict[str, list[Any]] = {
            "price_book_batches": [
                row[0]
                for row in conn.execute(
                    """SELECT to_jsonb(b)
                         FROM price_book_batches b
                        WHERE NOT (b.vendor_name=%s AND b.batch_ref=%s)
                        ORDER BY to_jsonb(b)::text""",
                    (vendor_name, batch_ref),
                ).fetchall()
            ],
            "price_book_staging_rows": [
                row[0]
                for row in conn.execute(
                    """SELECT to_jsonb(s)
                         FROM price_book_staging_rows s
                        WHERE NOT EXISTS (
                              SELECT 1 FROM price_book_batches b
                               WHERE b.price_book_batch_id=s.price_book_batch_id
                                 AND b.vendor_name=%s AND b.batch_ref=%s)
                        ORDER BY to_jsonb(s)::text""",
                    (vendor_name, batch_ref),
                ).fetchall()
            ],
            "price_book_validation_issues": [
                row[0]
                for row in conn.execute(
                    """SELECT to_jsonb(i)
                         FROM price_book_validation_issues i
                        WHERE NOT EXISTS (
                              SELECT 1 FROM price_book_batches b
                               WHERE b.price_book_batch_id=i.price_book_batch_id
                                 AND b.vendor_name=%s AND b.batch_ref=%s)
                        ORDER BY to_jsonb(i)::text""",
                    (vendor_name, batch_ref),
                ).fetchall()
            ],
        }
    facts = dict(state["facts"])
    facts["relation_inventory"] = [
        item
        for item in facts["relation_inventory"]
        if item["relation"] not in _ALLOWED_RELATIONS
    ]
    facts["sequence_inventory"] = [
        item
        for item in facts["sequence_inventory"]
        if item["sequence"] not in _ALLOWED_SEQUENCES
    ]
    facts["unrelated_price_stage_sha256"] = canonical_sha256(
        unrelated_price_state
    )
    return canonical_sha256(facts)


def _protected_storage_sha256(
    storage: LocalFilesystemStorage, *, raw_key: str
) -> str:
    try:
        storage.require_clean_immutable_staging()
    except (OSError, PermissionError) as exc:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage storage staging differs"
        ) from exc
    inventory = []
    for key in storage.list_keys():
        if key == raw_key:
            continue
        value = storage.get_bytes(key)
        inventory.append(
            {
                "key": key,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
        )
    return canonical_sha256(inventory)


def _stage_once(
    conn: Any,
    *,
    target: SyntheticStagingTarget,
    fixture: RegisteredOperatorFixture,
    storage: LocalFilesystemStorage,
    ambiguous_commit_recovered: bool,
    lock_name: str,
) -> dict[str, Any]:
    parsed = parse_price_book_csv(fixture.csv_bytes)
    raw_key = f"price-books/raw/{fixture.content_sha256}.csv"
    before_database = _protected_database_sha256(
        conn,
        target,
        vendor_name=str(parsed["vendor_name"]),
        batch_ref=str(parsed["batch_ref"]),
    )
    before_storage = _protected_storage_sha256(storage, raw_key=raw_key)
    with conn.transaction():
        declaration = registered_target_declaration(conn)
    principal = Principal(
        principal_ref=STOPPED_SERVICE_PRICE_STAGE_PRINCIPAL,
        role_ref=STOPPED_SERVICE_PRICE_STAGE_ROLE,
        authn_context_sha256=STOPPED_SERVICE_PRICE_STAGE_AUTHN_SHA256,
    )
    result = _stage_and_validate_declared_price_book(
        conn,
        storage,
        csv_bytes=fixture.csv_bytes,
        principal=principal,
        expected_declaration_sha256=declaration["declaration_sha256"],
        operator_stage=True,
        operator_lock_name=lock_name,
    )
    evidence = result.get("validation_evidence")
    operator = (
        evidence.get("stopped_service_operator_stage")
        if isinstance(evidence, dict)
        else None
    )
    memberships = conn.execute(
        "SELECT count(*) FROM price_book_scope_memberships "
        "WHERE price_book_batch_id=%s",
        (result["price_book_batch_id"],),
    ).fetchone()
    after_database = _protected_database_sha256(
        conn,
        target,
        vendor_name=str(parsed["vendor_name"]),
        batch_ref=str(parsed["batch_ref"]),
    )
    after_storage = _protected_storage_sha256(storage, raw_key=raw_key)
    try:
        observed_raw = storage.read_bytes_once(
            raw_key,
            fixture.content_sha256,
            fixture.byte_count,
        )
    except (OSError, ValueError) as exc:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage raw evidence differs"
        ) from exc
    if (
        parsed["content_sha256"] != fixture.content_sha256
        or result.get("status") != "VALIDATED"
        or result.get("scope_membership_sha256") is not None
        or memberships != (0,)
        or not isinstance(operator, dict)
        or operator.get("contract") != STOPPED_SERVICE_PRICE_STAGE_CONTRACT
        or before_database != after_database
        or before_storage != after_storage
        or observed_raw != fixture.csv_bytes
    ):
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage postcondition differs"
        )
    return {
        "contract": STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
        "source_ref": REGISTERED_OPERATOR_BOOK_REF,
        "source_bytes": fixture.byte_count,
        "raw_sha256": fixture.content_sha256,
        "target_attestation_sha256": EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        "batch_id": str(result["price_book_batch_id"]),
        "status": "VALIDATED",
        "declaration_sha256": str(result["declaration_sha256"]),
        "validation_fingerprint": str(result["validation_fingerprint"]),
        "proposed_scope_membership_sha256": str(
            evidence["proposed_scope_membership_sha256"]
        ),
        "staging_rows_sha256": str(operator["staging_rows_sha256"]),
        "validation_issues_sha256": str(
            operator["validation_issues_sha256"]
        ),
        "unchanged_database_sha256": before_database,
        "unchanged_storage_sha256": before_storage,
        "idempotent_replay": bool(result["idempotent_replay"]),
        "ambiguous_commit_recovered": ambiguous_commit_recovered,
    }


def _batch_exists(conn: Any, fixture: RegisteredOperatorFixture) -> bool:
    parsed = parse_price_book_csv(fixture.csv_bytes)
    row = conn.execute(
        "SELECT count(*) FROM price_book_batches WHERE vendor_name=%s AND batch_ref=%s",
        (parsed["vendor_name"], parsed["batch_ref"]),
    ).fetchone()
    if row is None or int(row[0]) not in {0, 1}:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage recovery inventory differs"
        )
    return int(row[0]) == 1


def stage_registered_operator_fixture(
    *,
    target: SyntheticStagingTarget,
    password: str,
    storage: LocalFilesystemStorage,
) -> dict[str, Any]:
    """Stage or observe the sole fixed fixture under the shared lifecycle lock."""

    target.validate_static()
    fixture = load_registered_operator_fixture()
    ambiguous = False
    last_error: BaseException | None = None
    for attempt in range(3):
        conn: Any | None = None
        lock_name: str | None = None
        attempt_error: BaseException | None = None
        try:
            conn, lock_name = _attest_and_lock(target=target, password=password)
            if ambiguous:
                if not _batch_exists(conn, fixture):
                    raise SyntheticStagingPriceStageError(
                        "synthetic price-stage transaction outcome is unknown"
                    )
                return _stage_once(
                    conn,
                    target=target,
                    fixture=fixture,
                    storage=storage,
                    ambiguous_commit_recovered=True,
                    lock_name=lock_name,
                )
            return _stage_once(
                conn,
                target=target,
                fixture=fixture,
                storage=storage,
                ambiguous_commit_recovered=ambiguous,
                lock_name=lock_name,
            )
        except (
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
        ) as exc:
            attempt_error = exc
            last_error = exc
            if attempt == 2:
                break
        except psycopg.OperationalError as exc:
            attempt_error = exc
            last_error = exc
            ambiguous = True
            if attempt == 2:
                break
        finally:
            if conn is not None and lock_name is not None:
                try:
                    _close_locked_connection(conn, lock_name)
                except BaseException:
                    if attempt_error is None:
                        raise
            elif conn is not None:
                conn.close()
    raise SyntheticStagingPriceStageError(
        "synthetic price-stage transaction did not complete"
    ) from last_error


def _child_environment(inputs: Any, secret_fd: int) -> dict[str, str]:
    return {
        **_database_target_environment(inputs),
        "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
        "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
        "BUFFALO_STAGING_VOLUME_ROOT": str(inputs.volume_root),
        _CHILD_ENV: "1",
        _SECRET_FD_ENV: str(secret_fd),
    }


def _validate_operator_proof(proof: Any) -> dict[str, Any]:
    if not isinstance(proof, dict) or set(proof) != _PROOF_KEYS:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage proof differs"
        )
    hash_names = _PROOF_KEYS - {
        "contract",
        "source_ref",
        "source_bytes",
        "batch_id",
        "status",
        "idempotent_replay",
        "ambiguous_commit_recovered",
    }
    try:
        batch_id = str(UUID(str(proof["batch_id"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage proof differs"
        ) from exc
    if (
        proof["contract"] != STOPPED_SERVICE_PRICE_STAGE_CONTRACT
        or proof["source_ref"] != REGISTERED_OPERATOR_BOOK_REF
        or proof["source_bytes"] != REGISTERED_OPERATOR_BOOK_BYTES
        or proof["raw_sha256"] != REGISTERED_OPERATOR_BOOK_SHA256
        or proof["target_attestation_sha256"]
        != EXPECTED_RUNTIME_ATTESTATION_IDENTITY
        or proof["status"] != "VALIDATED"
        or proof["batch_id"] != batch_id
        or any(
            not isinstance(proof[name], str)
            or len(proof[name]) != 64
            or any(character not in "0123456789abcdef" for character in proof[name])
            for name in hash_names
        )
        or type(proof["idempotent_replay"]) is not bool
        or type(proof["ambiguous_commit_recovered"]) is not bool
    ):
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage proof differs"
        )
    return proof


def _read_bounded_child_output(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
) -> bytes:
    if process.stdout is None:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage child output is unavailable"
        )
    deadline = time.monotonic() + timeout_seconds
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(descriptor, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            block = os.read(
                descriptor,
                _MAX_OUTPUT_BYTES + 1 - len(output),
            )
            if not block:
                break
            output.extend(block)
            if len(output) > _MAX_OUTPUT_BYTES:
                raise SyntheticStagingPriceStageError(
                    "synthetic price-stage child output exceeds limit"
                )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        process.wait(timeout=remaining)
        return bytes(output)
    finally:
        selector.close()
        process.stdout.close()


def _run_child(environment: Mapping[str, str]) -> dict[str, Any]:
    if (
        (os.getuid(), os.geteuid(), os.getgid(), os.getegid())
        != (
            SYNTHETIC_ACCOUNT.uid,
            SYNTHETIC_ACCOUNT.uid,
            SYNTHETIC_ACCOUNT.gid,
            SYNTHETIC_ACCOUNT.gid,
        )
        or os.getgroups()
        or os.getsid(0) != os.getpid()
        or os.getpgid(0) != os.getpid()
    ):
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage process identity differs"
        )
    previous_umask = os.umask(0o077)
    os.umask(previous_umask)
    if previous_umask != 0o077:
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage process mode differs"
        )
    fixture = load_registered_operator_fixture()
    if fixture.content_sha256 != REGISTERED_OPERATOR_BOOK_SHA256:
        raise SyntheticStagingPriceStageError(
            "registered price-stage source differs"
        )
    password = _read_secret_fd(environment)
    try:
        target = target_from_environment(environment)
        layout = StagingBootstrapLayout.build(
            volume_root=Path(environment["BUFFALO_STAGING_VOLUME_ROOT"]),
            runtime_root=RUNTIME_ROOT,
        )
        _validate_storage_root(layout)
        return stage_registered_operator_fixture(
            target=target,
            password=password,
            storage=LocalFilesystemStorage(layout.synthetic_storage),
        )
    finally:
        password = ""


def run_stopped_service_price_stage(
    environment: MutableMapping[str, str],
    *,
    python_executable: str = sys.executable,
) -> dict[str, Any]:
    """Validate root inputs and run one fixed unprivileged effecting child."""

    if any(name in environment for name in _FORBIDDEN_OPERATOR_INPUTS) or any(
        name.startswith("BUFFALO_STAGING_PRICE_BACKUP_") for name in environment
    ):
        environment.pop(DATABASE_PASSWORD_ENV, None)
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage ambient authority is present"
        )
    password: str | None = None
    try:
        inputs, password = load_bootstrap_inputs(environment)
    finally:
        environment.pop(DATABASE_PASSWORD_ENV, None)
    if any(
        value is not None
        for value in (
            inputs.backup_label,
            inputs.expected_manifest_sha256,
            inputs.expected_tree,
        )
    ):
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage backup authority is present"
        )
    _validate_root_identity_and_accounts(runtime_root=RUNTIME_ROOT)
    layout = StagingBootstrapLayout.build(
        volume_root=inputs.volume_root, runtime_root=RUNTIME_ROOT
    )
    _validate_layout_root_separation(layout)
    _validate_volume_mount(
        layout.volume_root, local_acceptance=inputs.local_port is not None
    )
    _validate_storage_root(layout)
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    process: subprocess.Popen[bytes] | None = None
    secret = bytearray(password.encode("utf-8"))
    prior_handlers: dict[signal.Signals, Any] = {}
    interrupted_signal: int | None = None
    cleanup_in_progress = False

    def interrupt(signum: int, _frame: Any) -> None:
        nonlocal interrupted_signal
        if cleanup_in_progress:
            return
        interrupted_signal = signum
        if process is not None:
            raise _OperatorInterrupted(signum)

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                prior_handlers[signum] = signal.signal(signum, interrupt)
        offset = 0
        while offset < len(secret):
            written = os.write(write_fd, secret[offset:])
            if written < 1:
                raise SyntheticStagingPriceStageError(
                    "synthetic price-stage credential transfer failed"
                )
            offset += written
        os.close(write_fd)
        write_fd = -1
        argv = (
            python_executable,
            "-I",
            "-B",
            "-m",
            "procurement_os.synthetic_staging_price_stage",
        )
        child_environment = _child_environment(inputs, read_fd)
        if interrupted_signal is not None:
            raise _OperatorInterrupted(interrupted_signal)
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_environment,
            close_fds=True,
            pass_fds=(read_fd,),
            user=SYNTHETIC_ACCOUNT.uid,
            group=SYNTHETIC_ACCOUNT.gid,
            extra_groups=(),
            umask=0o077,
            start_new_session=True,
            shell=False,
        )
        if interrupted_signal is not None:
            raise _OperatorInterrupted(interrupted_signal)
        os.close(read_fd)
        read_fd = -1
        try:
            output = _read_bounded_child_output(
                process,
                timeout_seconds=_CHILD_TIMEOUT_SECONDS,
            )
        except (
            subprocess.TimeoutExpired,
            KeyboardInterrupt,
            _OperatorInterrupted,
        ):
            raise SyntheticStagingPriceStageError(
                "synthetic price-stage child did not complete"
            ) from None
        if (
            process.returncode != 0
            or not output
            or output.find(secret) != -1
        ):
            raise SyntheticStagingPriceStageError(
                "synthetic price-stage child failed"
            )
        try:
            proof = json.loads(output)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SyntheticStagingPriceStageError(
                "synthetic price-stage proof differs"
            ) from exc
        return _validate_operator_proof(proof)
    except (KeyboardInterrupt, _OperatorInterrupted):
        raise SyntheticStagingPriceStageError(
            "synthetic price-stage child did not complete"
        ) from None
    finally:
        cleanup_in_progress = True
        try:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        finally:
            password = None
            for index in range(len(secret)):
                secret[index] = 0
            for signum, handler in prior_handlers.items():
                signal.signal(signum, handler)
            for descriptor in (read_fd, write_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


def main(
    arguments: list[str] | None = None,
    *,
    environment: MutableMapping[str, str] | None = None,
) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    target = os.environ if environment is None else environment
    if values:
        target.pop(DATABASE_PASSWORD_ENV, None)
        return 2
    try:
        if target.get(_CHILD_ENV) == "1":
            proof = _run_child(target)
        else:
            proof = run_stopped_service_price_stage(target)
        sys.stdout.write(
            json.dumps(
                proof,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
    except BaseException:
        target.pop(DATABASE_PASSWORD_ENV, None)
        sys.stderr.write("Buffalo stopped-service price stage failed\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

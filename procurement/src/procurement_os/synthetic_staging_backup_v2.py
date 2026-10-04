"""Operator-only Backup V2 producer for the attested staging successor.

The application never calls this module at startup.  A stopped-service
operator supplies separate credential-free runtime and provisioner URLs.  The
runtime connection supplies the attested evidence embedded in the manifest;
the provisioner is used only for the complete custom-format dump.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import psycopg
from psycopg import sql

from .local_backup_v2 import (
    BACKUP_V2_CONTRACT,
    LocalBackupV2Error,
    canonical_sha256,
    database_state_evidence,
    verify_price_apply_backup,
)
from .synthetic_price_replacement_contract import (
    CATALOG_SHA256 as PRICE_CATALOG_SHA,
    MIGRATION_NAME as PRICE_MIGRATION,
    MIGRATION_SHA256 as PRICE_MIGRATION_SHA,
)
from .synthetic_staging_database import (
    EXPECTED_DATABASE,
    OBJECT_OWNER,
    PROVISIONER,
    RUNTIME_LOGIN,
    SCHEMA,
    SyntheticStagingTarget,
    attest_provisioner_connection,
    attest_runtime_connection,
    staging_backup_release,
)


_GIT_ID = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_PRICE_STORAGE_KEY = re.compile(r"^price-books/raw/([0-9a-f]{64})\.csv$")
_ARTIFACT_STORAGE_KEY = re.compile(
    rf"^monday-runs/{_UUID}/(?:vendor-{_UUID}/([0-9a-f]{{64}})\.internal\.csv|"
    rf"packet/([0-9a-f]{{64}})\.review\.zip)$"
)


class SyntheticStagingBackupError(RuntimeError):
    """The offline staging recovery proof could not be created exactly."""


def _trusted_program(name: str) -> str:
    selected = shutil.which(name)
    if selected is None:
        raise SyntheticStagingBackupError(
            f"trusted {name} executable is unavailable"
        )
    try:
        path = Path(selected).resolve(strict=True)
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise SyntheticStagingBackupError(
            f"trusted {name} executable is unavailable"
        ) from exc
    if (
        not path.is_absolute()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(path, os.X_OK)
        or (
            info.st_uid != 0
            and re.match(
                r"^/nix/store/[0-9a-z]{32}-[^/]+/(?:.+/)?[^/]+$",
                str(path),
            )
            is None
        )
    ):
        raise SyntheticStagingBackupError(
            f"trusted {name} executable differs"
        )
    _require_canonical_path(path)
    for parent in path.parents:
        if parent == Path(parent.anchor):
            continue
        parent_info = parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_IMODE(
            parent_info.st_mode
        ) & 0o022:
            raise SyntheticStagingBackupError(
                f"trusted {name} executable differs"
            )
    return str(path)


def _require_canonical_path(path: Path) -> None:
    if not path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise SyntheticStagingBackupError("staging backup path is not canonical")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise SyntheticStagingBackupError(
                "staging backup path ancestor is unavailable"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise SyntheticStagingBackupError(
                "staging backup path ancestor is a symlink"
            )


def _require_directory(path: Path, *, label: str) -> os.stat_result:
    if path == Path("/"):
        raise SyntheticStagingBackupError(f"{label} path differs")
    _require_canonical_path(path)
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise SyntheticStagingBackupError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise SyntheticStagingBackupError(f"{label} ownership differs")
    return info


def _ancestor_identities(path: Path) -> set[tuple[int, int]]:
    identities: set[tuple[int, int]] = set()
    for parent in path.parents:
        info = parent.stat(follow_symlinks=False)
        identities.add((int(info.st_dev), int(info.st_ino)))
    return identities


def _paths_overlap(
    left: Path,
    left_info: os.stat_result,
    right: Path,
    right_info: os.stat_result,
) -> bool:
    return (
        left == right
        or left in right.parents
        or right in left.parents
        or (int(left_info.st_dev), int(left_info.st_ino))
        == (int(right_info.st_dev), int(right_info.st_ino))
        or (int(left_info.st_dev), int(left_info.st_ino))
        in _ancestor_identities(right)
        or (int(right_info.st_dev), int(right_info.st_ino))
        in _ancestor_identities(left)
    )


def _require_url(
    value: str, *, username: str, target: SyntheticStagingTarget
) -> None:
    try:
        parsed = urlparse(value)
        target_parsed = urlparse(target.database_url)
        port = parsed.port
        target_port = target_parsed.port
    except ValueError as exc:
        raise SyntheticStagingBackupError("staging backup database URL differs") from exc
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.username != username
        or parsed.password is not None
        or parsed.hostname != target.expected_private_host
        or port != target_port
        or parsed.path != f"/{EXPECTED_DATABASE}"
        or parsed.query
        or parsed.fragment
        or parsed.params
    ):
        raise SyntheticStagingBackupError("staging backup database URL differs")


def _hash_file(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SyntheticStagingBackupError("staging backup file is unavailable") from exc
    digest = hashlib.sha256()
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise SyntheticStagingBackupError(
                "staging backup file ownership differs"
            )
        remaining = info.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise SyntheticStagingBackupError(
                    "staging backup file changed while hashing"
                )
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise SyntheticStagingBackupError(
                "staging backup file changed while hashing"
            )
        return digest.hexdigest(), int(info.st_size)
    finally:
        os.close(descriptor)


def _storage_inventory(root: Path) -> list[dict[str, Any]]:
    _require_directory(root, label="staging synthetic storage root")
    inventory: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        try:
            info = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SyntheticStagingBackupError(
                "staging synthetic storage changed during inventory"
            ) from exc
        if path.is_symlink():
            raise SyntheticStagingBackupError(
                "staging synthetic storage contains a symlink"
            )
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".immutable-staging":
            if stat.S_ISREG(info.st_mode):
                raise SyntheticStagingBackupError(
                    "staging synthetic storage has an incomplete write"
                )
            continue
        if stat.S_ISDIR(info.st_mode):
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise SyntheticStagingBackupError(
                    "staging synthetic storage directory ownership differs"
                )
            continue
        if not stat.S_ISREG(info.st_mode):
            raise SyntheticStagingBackupError(
                "staging synthetic storage object type differs"
            )
        digest, size = _hash_file(path)
        inventory.append(
            {"path": relative.as_posix(), "sha256": digest, "bytes": size}
        )
    if [item["path"] for item in inventory] != sorted(
        item["path"] for item in inventory
    ):
        raise AssertionError("storage inventory ordering differs")
    return inventory


def _write_storage_archive(
    archive_path: Path, *, storage_root: Path, inventory: list[dict[str, Any]]
) -> None:
    try:
        descriptor = os.open(
            archive_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise SyntheticStagingBackupError(
            "staging backup archive destination is unavailable"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            with tarfile.open(fileobj=output, mode="w") as archive:
                for item in inventory:
                    source = storage_root / item["path"]
                    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                    source_descriptor = os.open(source, flags)
                    try:
                        info = os.fstat(source_descriptor)
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or int(info.st_size) != item["bytes"]
                        ):
                            raise SyntheticStagingBackupError(
                                "staging synthetic storage changed during archive"
                            )
                        record = tarfile.TarInfo(item["path"])
                        record.size = int(info.st_size)
                        record.mode = 0o600
                        record.mtime = 0
                        record.uid = record.gid = 0
                        record.uname = record.gname = ""
                        with os.fdopen(
                            os.dup(source_descriptor), "rb", closefd=True
                        ) as source_file:
                            archive.addfile(record, source_file)
                    finally:
                        os.close(source_descriptor)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise SyntheticStagingBackupError(
            "staging backup manifest destination is unavailable"
        ) from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise SyntheticStagingBackupError(
                    "staging backup manifest write failed"
                )
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run_git(source_root: Path, *arguments: str) -> str:
    _require_canonical_path(source_root)
    git = _trusted_program("git")
    try:
        result = subprocess.run(
            [git, "-C", str(source_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyntheticStagingBackupError(
            "staging backup source identity is unavailable"
        ) from exc
    return result.stdout.strip()


def _source_git_boundaries(source_root: Path) -> tuple[Path, Path]:
    top = Path(_run_git(source_root, "rev-parse", "--show-toplevel"))
    common_raw = Path(_run_git(source_root, "rev-parse", "--git-common-dir"))
    if not top.is_absolute():
        raise SyntheticStagingBackupError(
            "staging backup source identity differs"
        )
    if not common_raw.is_absolute():
        common_raw = source_root / common_raw
    _require_canonical_path(top)
    _require_canonical_path(common_raw)
    try:
        exact_source = source_root.resolve(strict=True)
        exact_top = top.resolve(strict=True)
        exact_common = common_raw.resolve(strict=True)
    except OSError as exc:
        raise SyntheticStagingBackupError(
            "staging backup source identity is unavailable"
        ) from exc
    if exact_source != exact_top:
        raise SyntheticStagingBackupError(
            "staging backup source identity differs"
        )
    return exact_top, exact_common


def _source_identity(source_root: Path) -> dict[str, str]:
    status = _run_git(
        source_root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    commit = _run_git(source_root, "rev-parse", "--verify", "HEAD^{commit}")
    tree = _run_git(source_root, "rev-parse", "--verify", "HEAD^{tree}")
    if status or _GIT_ID.fullmatch(commit) is None or _GIT_ID.fullmatch(tree) is None:
        raise SyntheticStagingBackupError("staging backup source identity differs")
    return {"commit": commit, "tree": tree}


def _database_facts(conn: Any, target: SyntheticStagingTarget) -> dict[str, Any]:
    row = conn.execute(
        "SELECT current_database(),current_setting('server_version_num')::int/10000,"
        "pg_catalog.host(inet_server_addr()),session_user::text,current_user::text,"
        "pg_catalog.pg_get_userbyid(d.datdba) "
        "FROM pg_catalog.pg_database d WHERE d.datname=current_database()"
    ).fetchone()
    if row is None:
        raise SyntheticStagingBackupError("staging backup database identity is absent")
    return {
        "database": str(row[0]),
        "postgres_major": int(row[1]),
        "server_address": str(row[2]),
        "session_user": str(row[3]),
        "current_user": str(row[4]),
        "database_owner": str(row[5]),
        "server_host": target.expected_private_host,
    }


def _price_evidence(conn: Any, batch_id: str) -> dict[str, Any]:
    try:
        canonical_batch_id = str(UUID(str(batch_id)))
    except (TypeError, ValueError) as exc:
        raise SyntheticStagingBackupError(
            "staging replacement batch identity is malformed"
        ) from exc
    target = sql.Identifier(SCHEMA)
    rows = conn.execute(
        sql.SQL(
            "SELECT b.price_book_batch_id::text,b.vendor_id::text,"
            "b.price_scope_key,b.content_sha256,b.raw_storage_key,"
            "h.supplier_price_authority_event_id::text,h.head_version,"
            "{}.supplier_price_scope_current_payload(b.vendor_id)::text,"
            "{}.supplier_price_scope_current_sha256(b.vendor_id),b.row_count,"
            "(SELECT count(*) FROM {}.price_book_scope_memberships m "
            " WHERE m.price_book_batch_id=b.price_book_batch_id),"
            "(SELECT count(*) FROM {}.prices x "
            " WHERE x.source_price_book_batch_id=b.price_book_batch_id "
            " AND x.price_state='future' AND x.verified),"
            "e.raw_content_sha256,e.declaration_sha256,e.policy_sha256,"
            "e.scope_membership_sha256,b.declaration_sha256,p.policy_sha256,"
            "b.scope_membership_sha256 "
            "FROM {}.price_book_batches b "
            "JOIN {}.supplier_price_schedule_policies p "
            " ON p.policy_ref=b.schedule_policy_ref "
            "JOIN {}.supplier_price_authority_heads h "
            " ON h.vendor_id=b.vendor_id AND h.price_scope_key=b.price_scope_key "
            "JOIN {}.price_book_promotion_events e "
            " ON e.price_book_batch_id=b.price_book_batch_id "
            "AND e.replacement_contract=b.replacement_contract "
            "WHERE b.price_book_batch_id=%s "
            "AND b.status='VERIFIED_FUTURE' "
            "AND b.replacement_contract='SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1' "
            "AND b.price_scope_key='COMPLETE_VENDOR' "
            "AND p.fixture_database_name=current_database()"
        ).format(*(target for _ in range(8))),
        (canonical_batch_id,),
    ).fetchall()
    if len(rows) != 1:
        raise SyntheticStagingBackupError(
            "confirmed staging replacement batch is absent"
        )
    row = rows[0]
    try:
        scope_rows = json.loads(str(row[7]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise SyntheticStagingBackupError(
            "staging replacement current scope is malformed"
        ) from exc
    if (
        str(row[0]) != canonical_batch_id
        or row[2] != "COMPLETE_VENDOR"
        or not isinstance(scope_rows, list)
        or not scope_rows
        or _SHA256.fullmatch(str(row[3])) is None
        or str(row[4]) != f"price-books/raw/{row[3]}.csv"
        or int(row[6]) < 1
        or int(row[9]) < 1
        or (int(row[10]), int(row[11])) != (int(row[9]), int(row[9]))
        or (str(row[12]), str(row[13]), str(row[14]), str(row[15]))
        != (str(row[3]), str(row[16]), str(row[17]), str(row[18]))
        or any(
            _SHA256.fullmatch(str(value)) is None
            for value in (row[8], row[12], row[13], row[14], row[15])
        )
    ):
        raise SyntheticStagingBackupError(
            "confirmed staging replacement evidence differs"
        )
    return {
        "price_replacement": {
            "price_book_batch_id": str(row[0]),
            "vendor_id": str(row[1]),
            "price_scope_key": str(row[2]),
            "prior_event_id": str(row[5]),
            "prior_head_version": int(row[6]),
            "raw_content_sha256": str(row[3]),
            "raw_storage_key": str(row[4]),
        },
        "prechange_current_scope": {
            "rows": scope_rows,
            "rows_sha256": canonical_sha256(scope_rows),
            "database_scope_sha256": str(row[8]),
        },
    }


def _storage_bindings(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT raw_storage_key,content_sha256,NULL::bigint "
        "FROM qa_mapping_test.price_book_batches "
        "WHERE raw_storage_key IS NOT NULL "
        "UNION SELECT storage_key,sha256,size_bytes "
        "FROM qa_mapping_test.monday_run_artifacts "
        "ORDER BY 1,2,3"
    ).fetchall()
    bindings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path, raw_digest, raw_size in rows:
        path = str(raw_path)
        digest = str(raw_digest)
        price_match = _PRICE_STORAGE_KEY.fullmatch(path)
        artifact_match = _ARTIFACT_STORAGE_KEY.fullmatch(path)
        path_digest = (
            price_match.group(1)
            if price_match is not None
            else next(
                (
                    value
                    for value in artifact_match.groups()
                    if value is not None
                ),
                None,
            )
            if artifact_match is not None
            else None
        )
        size = None if raw_size is None else int(raw_size)
        if (
            path in seen
            or _SHA256.fullmatch(digest) is None
            or path_digest != digest
            or (price_match is not None and size is not None)
            or (artifact_match is not None and (size is None or size < 0))
            or (price_match is None and artifact_match is None)
        ):
            raise SyntheticStagingBackupError(
                "staging synthetic storage binding differs"
            )
        seen.add(path)
        bindings.append({"path": path, "sha256": digest, "bytes": size})
    return bindings


def _verify_storage_inventory(
    inventory: list[dict[str, Any]], bindings: list[dict[str, Any]]
) -> None:
    expected = {str(item["path"]): item for item in bindings}
    observed = {str(item["path"]): item for item in inventory}
    if set(expected) != set(observed):
        raise SyntheticStagingBackupError(
            "staging synthetic storage inventory differs"
        )
    for path, binding in expected.items():
        item = observed[path]
        if (
            item["sha256"] != binding["sha256"]
            or (
                binding["bytes"] is not None
                and int(item["bytes"]) != binding["bytes"]
            )
        ):
            raise SyntheticStagingBackupError(
                "staging synthetic storage inventory differs"
            )


def _runtime_snapshot(
    runtime_url: str, target: SyntheticStagingTarget, batch_id: str
) -> dict[str, Any]:
    try:
        with psycopg.connect(runtime_url, connect_timeout=5) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            attestation = attest_runtime_connection(conn, target)
            result = {
                "attestation": attestation,
                "database": _database_facts(conn, target),
                "state": database_state_evidence(
                    conn, schema=SCHEMA, staging_runtime=True
                ),
                "storage_bindings": _storage_bindings(conn),
                **_price_evidence(conn, batch_id),
            }
            conn.rollback()
            return result
    except SyntheticStagingBackupError:
        raise
    except Exception as exc:
        raise SyntheticStagingBackupError(
            "staging runtime backup evidence differs"
        ) from exc


def _attest_provisioner(
    provisioner_url: str, target: SyntheticStagingTarget
) -> str:
    try:
        with psycopg.connect(provisioner_url, connect_timeout=5) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            result = attest_provisioner_connection(conn, target)
            conn.rollback()
            return result
    except Exception as exc:
        raise SyntheticStagingBackupError(
            "staging provisioner backup evidence differs"
        ) from exc


def _postgres_program(name: str) -> str:
    path = _trusted_program(name)
    try:
        result = subprocess.run(
            [path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": os.path.dirname(path), "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyntheticStagingBackupError(
            f"PostgreSQL 16 {name} is unavailable"
        ) from exc
    if re.search(r"\(PostgreSQL\) 16(?:\.|\s|$)", result.stdout) is None:
        raise SyntheticStagingBackupError(
            f"PostgreSQL 16 {name} is unavailable"
        )
    return path


def _postgres_dump() -> str:
    return _postgres_program("pg_dump")


def _postgres_restore() -> str:
    return _postgres_program("pg_restore")


def _validate_dump_archive(pg_restore: str, dump_path: Path) -> None:
    try:
        result = subprocess.run(
            [pg_restore, "--list", str(dump_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": os.path.dirname(pg_restore), "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyntheticStagingBackupError(
            "staging database dump archive differs"
        ) from exc
    entries = [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    if (
        not entries
        or not any(" SCHEMA - qa_mapping_test " in line for line in entries)
        or not any(" EXTENSION - pgcrypto " in line for line in entries)
        or any(
            " SCHEMA - " in line
            and " SCHEMA - qa_mapping_test " not in line
            for line in entries
        )
        or any(
            " EXTENSION - " in line
            and " EXTENSION - pgcrypto " not in line
            for line in entries
        )
    ):
        raise SyntheticStagingBackupError(
            "staging database dump archive differs"
        )
    try:
        subprocess.run(
            [pg_restore, "--file", os.devnull, str(dump_path)],
            check=True,
            env={"PATH": os.path.dirname(pg_restore), "LANG": "C.UTF-8"},
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyntheticStagingBackupError(
            "staging database dump archive is unreadable"
        ) from exc


def create_staging_backup_v2(
    *,
    runtime_url: str,
    provisioner_url: str,
    target: SyntheticStagingTarget,
    recovery_root: Path,
    storage_root: Path,
    source_root: Path,
    batch_id: str,
) -> Path:
    """Create and self-verify one exact stopped-service staging Backup V2."""

    target.validate_static()
    _require_url(runtime_url, username=RUNTIME_LOGIN, target=target)
    _require_url(provisioner_url, username=PROVISIONER, target=target)
    if runtime_url != target.database_url:
        raise SyntheticStagingBackupError("staging runtime URL differs")
    recovery_info = _require_directory(
        recovery_root, label="staging recovery root"
    )
    backups = recovery_root / "backups"
    _require_directory(backups, label="staging recovery backup root")
    storage_info = _require_directory(
        storage_root, label="staging synthetic storage root"
    )
    source_boundaries = _source_git_boundaries(source_root)
    if _paths_overlap(
        recovery_root, recovery_info, storage_root, storage_info
    ):
        raise SyntheticStagingBackupError(
            "staging recovery and storage roots overlap"
        )
    for boundary in source_boundaries:
        boundary_info = boundary.stat(follow_symlinks=False)
        if _paths_overlap(
            recovery_root, recovery_info, boundary, boundary_info
        ) or _paths_overlap(
            storage_root, storage_info, boundary, boundary_info
        ):
            raise SyntheticStagingBackupError(
                "staging backup roots overlap source identity"
            )
    source_before = _source_identity(source_root)
    provisioner_before = _attest_provisioner(provisioner_url, target)
    runtime_before = _runtime_snapshot(runtime_url, target, batch_id)
    storage_before = _storage_inventory(storage_root)
    raw = runtime_before["price_replacement"]
    raw_member = next(
        (item for item in storage_before if item["path"] == raw["raw_storage_key"]),
        None,
    )
    if raw_member is None or raw_member["sha256"] != raw["raw_content_sha256"]:
        raise SyntheticStagingBackupError(
            "confirmed staging replacement source is absent from storage"
        )
    _verify_storage_inventory(
        storage_before, runtime_before["storage_bindings"]
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = str(UUID(str(batch_id))).replace("-", "")[:12]
    destination = backups / f"staging-v2-{stamp}-{suffix}"
    try:
        destination.mkdir(mode=0o700)
        destination_info = destination.stat(follow_symlinks=False)
    except OSError as exc:
        raise SyntheticStagingBackupError(
            "staging backup destination is unavailable"
        ) from exc
    dump_path = destination / "database.dump"
    archive_path = destination / "storage.tar"
    manifest_path = destination / "manifest.json"
    try:
        pg_dump = _postgres_dump()
        pg_restore = _postgres_restore()
        if Path(pg_dump).parent != Path(pg_restore).parent:
            raise SyntheticStagingBackupError(
                "PostgreSQL 16 backup tool identity differs"
            )
        dump_environment = {
            "PATH": os.path.dirname(pg_dump),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        for name in ("PGPASSFILE", "PGSSLMODE", "PGSSLROOTCERT"):
            value = os.environ.get(name)
            if value:
                dump_environment[name] = value
        dump_descriptor = os.open(
            dump_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(dump_descriptor, "wb", closefd=False) as dump_file:
                subprocess.run(
                    [
                        pg_dump,
                        "--dbname",
                        provisioner_url,
                        "--format=custom",
                        "--schema",
                        SCHEMA,
                        "--extension",
                        "pgcrypto",
                    ],
                    check=True,
                    timeout=120,
                    env=dump_environment,
                    stdout=dump_file,
                )
                dump_file.flush()
                os.fsync(dump_file.fileno())
        finally:
            os.close(dump_descriptor)
        _validate_dump_archive(pg_restore, dump_path)
        _write_storage_archive(
            archive_path, storage_root=storage_root, inventory=storage_before
        )
        runtime_after = _runtime_snapshot(runtime_url, target, batch_id)
        provisioner_after = _attest_provisioner(provisioner_url, target)
        storage_after = _storage_inventory(storage_root)
        source_after = _source_identity(source_root)
        if (
            runtime_after != runtime_before
            or provisioner_after != provisioner_before
            or storage_after != storage_before
            or source_after != source_before
        ):
            raise SyntheticStagingBackupError(
                "staging backup inputs changed during capture"
            )
        dump_sha, dump_size = _hash_file(dump_path)
        archive_sha, archive_size = _hash_file(archive_path)
        manifest = {
            "contract": BACKUP_V2_CONTRACT,
            "created_utc": stamp,
            "database": runtime_after["database"],
            "database_dump": {
                "path": dump_path.name,
                "sha256": dump_sha,
                "bytes": dump_size,
            },
            "storage_archive": {
                "path": archive_path.name,
                "sha256": archive_sha,
                "bytes": archive_size,
                "files": storage_after,
            },
            "state": runtime_after["state"],
            "limitations": [
                "same-host local recovery only",
                "synthetic price APPLY recovery proof only",
                "sessions and secret files are intentionally excluded",
            ],
            "source_git": source_after,
            "schema_release": {
                "migration": PRICE_MIGRATION,
                "migration_sha256": PRICE_MIGRATION_SHA,
                "catalog_sha256": PRICE_CATALOG_SHA,
            },
            "staging_release": staging_backup_release(target),
            "price_replacement": runtime_after["price_replacement"],
            "prechange_current_scope": runtime_after["prechange_current_scope"],
        }
        manifest_bytes = (
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        _write_exclusive(manifest_path, manifest_bytes)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        verified = verify_price_apply_backup(
            runtime_root=recovery_root,
            manifest_path=manifest_path,
            expected_manifest_sha=manifest_sha,
            expected_commit=source_after["commit"],
            expected_tree=source_after["tree"],
            expected_profile="staging",
            expected_staging_release=staging_backup_release(target),
            expected_staging_target=target,
        )
        if (
            verified.batch_id != str(UUID(str(batch_id)))
            or verified.runtime_attestation_identity
            != runtime_after["attestation"]
        ):
            raise SyntheticStagingBackupError(
                "new staging Backup V2 identity differs"
            )
        return manifest_path
    except BaseException as exc:
        cleanup_error: BaseException | None = None
        try:
            current = destination.stat(follow_symlinks=False)
            if (
                destination.is_symlink()
                or current.st_dev != destination_info.st_dev
                or current.st_ino != destination_info.st_ino
            ):
                raise SyntheticStagingBackupError(
                    "staging backup destination identity changed during cleanup"
                )
            shutil.rmtree(destination)
        except BaseException as cleanup_exc:
            cleanup_error = cleanup_exc
        if cleanup_error is not None:
            raise SyntheticStagingBackupError(
                "staging backup failed and cleanup was incomplete"
            ) from cleanup_error
        if isinstance(exc, SyntheticStagingBackupError):
            raise
        if isinstance(exc, (LocalBackupV2Error, OSError, subprocess.SubprocessError)):
            raise SyntheticStagingBackupError("staging backup creation failed") from exc
        raise

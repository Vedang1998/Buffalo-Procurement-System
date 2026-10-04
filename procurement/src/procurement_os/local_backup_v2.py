"""Strict local backup V2 transport used as synthetic APPLY recovery proof.

The browser never supplies a path or label.  The local launcher resolves one
owned backup beneath its runtime root and passes the exact manifest path and
digest to the child process.  This module re-verifies those bytes without
granting any real-price authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from typing import Any, Mapping
from urllib.parse import urlparse

from psycopg import sql


BACKUP_V2_CONTRACT = "BUFFALO_LOCAL_CANDIDATE_BACKUP_V2"
MANIFEST_ENV = "BUFFALO_PRICE_APPLY_BACKUP_MANIFEST"
MANIFEST_SHA_ENV = "BUFFALO_PRICE_APPLY_BACKUP_MANIFEST_SHA256"
RUNTIME_ROOT_ENV = "BUFFALO_LOCAL_RUNTIME_ROOT"
SOURCE_COMMIT_ENV = "BUFFALO_SOURCE_COMMIT"
SOURCE_TREE_ENV = "BUFFALO_SOURCE_TREE"
STAGING_RUNTIME_ROOT_ENV = "BUFFALO_STAGING_PRICE_BACKUP_ROOT"
STAGING_MANIFEST_ENV = "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST"
STAGING_MANIFEST_SHA_ENV = "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST_SHA256"
STAGING_SOURCE_COMMIT_ENV = "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_COMMIT"
STAGING_SOURCE_TREE_ENV = "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_TREE"
STAGING_BINDING_ENVIRONMENT_NAMES = frozenset(
    {
        STAGING_RUNTIME_ROOT_ENV,
        STAGING_MANIFEST_ENV,
        STAGING_MANIFEST_SHA_ENV,
        STAGING_SOURCE_COMMIT_ENV,
        STAGING_SOURCE_TREE_ENV,
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_ID = re.compile(r"^[0-9a-f]{40}$")
_MAX_STAGING_MANIFEST_BYTES = 4 * 1024 * 1024


class LocalBackupV2Error(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedPriceApplyBackup:
    manifest_ref: str
    manifest_sha256: str
    dump_sha256: str
    storage_sha256: str
    prechange_scope_sha256: str
    database: str
    batch_id: str
    vendor_id: str
    price_scope_key: str
    prior_event_id: str
    prior_head_version: int
    raw_content_sha256: str
    raw_storage_key: str
    migration_sha256: str
    catalog_sha256: str
    state: dict[str, Any]
    target_kind: str = "legacy-local"
    staging_release_sha256: str | None = None
    runtime_attestation_identity: str | None = None


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stream_sha256(handle: Any) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        block = handle.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _read_manifest_bytes(
    path: Path,
    *,
    expected_uid: int | None,
    expected_gid: int | None,
    strict_file_identity: bool,
) -> bytes:
    if not strict_file_identity:
        return path.read_bytes()
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        owner_uid = os.getuid() if expected_uid is None else expected_uid
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != owner_uid
            or (expected_gid is not None and before.st_gid != expected_gid)
            or before.st_nlink != 1
            or not 0 < before.st_size <= _MAX_STAGING_MANIFEST_BYTES
        ):
            raise LocalBackupV2Error(
                "price APPLY recovery path ownership differs"
            )
        blocks: list[bytes] = []
        observed = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            observed += len(block)
            if observed > _MAX_STAGING_MANIFEST_BYTES:
                raise LocalBackupV2Error(
                    "price APPLY recovery manifest bytes differ"
                )
            blocks.append(block)
        after = os.fstat(descriptor)
        named = path.stat(follow_symlinks=False)
        if (
            observed != before.st_size
            or (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or (before.st_dev, before.st_ino)
            != (named.st_dev, named.st_ino)
        ):
            raise LocalBackupV2Error(
                "price APPLY recovery manifest bytes differ"
            )
        return b"".join(blocks)
    except LocalBackupV2Error:
        raise
    except OSError as exc:
        raise LocalBackupV2Error(
            "price APPLY recovery path is unavailable"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                raise LocalBackupV2Error(
                    "price APPLY recovery path is unavailable"
                ) from exc


def _require_owned(
    path: Path,
    *,
    mode: int,
    directory: bool,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    strict_file_identity: bool = False,
) -> None:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalBackupV2Error("price APPLY recovery path is unavailable") from exc
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    owner_uid = os.getuid() if expected_uid is None else expected_uid
    if (
        path.is_symlink()
        or not expected
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_uid != owner_uid
        or (expected_gid is not None and info.st_gid != expected_gid)
        or (strict_file_identity and not directory and info.st_nlink != 1)
    ):
        raise LocalBackupV2Error("price APPLY recovery path ownership differs")


def _member_record(value: Any) -> tuple[str, str, int]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "bytes"}:
        raise LocalBackupV2Error("price APPLY recovery member differs")
    path = value.get("path")
    digest = value.get("sha256")
    size = value.get("bytes")
    if (
        not isinstance(path, str)
        or not path
        or "\\" in path
        or Path(path).is_absolute()
        or Path(path).as_posix() != path
        or any(part in {"", ".", ".."} for part in Path(path).parts)
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        raise LocalBackupV2Error("price APPLY recovery member differs")
    return path, digest, size


def _verify_member(
    parent: Path,
    value: Any,
    expected_name: str,
    *,
    expected_uid: int | None,
    expected_gid: int | None,
    strict_file_identity: bool,
) -> Path:
    name, digest, size = _member_record(value)
    if name != expected_name or len(Path(name).parts) != 1:
        raise LocalBackupV2Error("price APPLY recovery payload path differs")
    path = parent / name
    _require_owned(
        path,
        mode=0o600,
        directory=False,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        strict_file_identity=strict_file_identity,
    )
    if path.stat().st_size != size or _sha256_file(path) != digest:
        raise LocalBackupV2Error("price APPLY recovery payload bytes differ")
    return path


def _validated_state_evidence(value: Any) -> dict[str, Any]:
    expected_fact_keys = {
        "review_batches",
        "review_candidates",
        "mapping_decisions",
        "selection_events",
        "selection_heads",
        "runs",
        "purchase_orders",
        "artifacts",
        "decision_payloads",
        "selection_payloads",
        "run_fingerprints",
        "artifact_hashes",
        "relation_inventory",
        "sequence_inventory",
    }
    if (
        not isinstance(value, dict)
        or set(value) != {"facts", "sha256"}
        or not isinstance(value.get("facts"), dict)
        or set(value["facts"]) != expected_fact_keys
        or _SHA256.fullmatch(str(value.get("sha256", ""))) is None
    ):
        raise LocalBackupV2Error("price APPLY database state evidence differs")
    count_keys = {
        "review_batches",
        "review_candidates",
        "mapping_decisions",
        "selection_events",
        "selection_heads",
        "runs",
        "purchase_orders",
        "artifacts",
    }
    hash_keys = expected_fact_keys - count_keys - {
        "relation_inventory",
        "sequence_inventory",
    }
    facts = value["facts"]
    relation_inventory = facts["relation_inventory"]
    sequence_inventory = facts["sequence_inventory"]
    if (
        any(type(facts[key]) is not int or facts[key] < 0 for key in count_keys)
        or any(not isinstance(facts[key], str) for key in hash_keys)
        or not isinstance(relation_inventory, list)
        or not isinstance(sequence_inventory, list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"relation", "row_count", "sha256"}
            or re.fullmatch(r"[a-z][a-z0-9_]*", str(item.get("relation", "")))
            is None
            or type(item.get("row_count")) is not int
            or item["row_count"] < 0
            or _SHA256.fullmatch(str(item.get("sha256", ""))) is None
            for item in relation_inventory
        )
        or [item["relation"] for item in relation_inventory]
        != sorted(item["relation"] for item in relation_inventory)
        or any(
            not isinstance(item, dict)
            or set(item) != {"sequence", "last_value", "is_called"}
            or re.fullmatch(r"[a-z][a-z0-9_]*", str(item.get("sequence", "")))
            is None
            or type(item.get("last_value")) is not int
            or type(item.get("is_called")) is not bool
            for item in sequence_inventory
        )
        or [item["sequence"] for item in sequence_inventory]
        != sorted(item["sequence"] for item in sequence_inventory)
        or canonical_sha256(facts) != value["sha256"]
    ):
        raise LocalBackupV2Error("price APPLY database state evidence differs")
    return value


def database_state_evidence(
    conn: Any, *, schema: str, staging_runtime: bool = False
) -> dict[str, Any]:
    """Recompute V2 relation/sequence evidence through the deciding connection."""

    if staging_runtime:
        try:
            from .synthetic_staging_database import (
                SCHEMA as STAGING_SCHEMA,
                is_staging_runtime_connection,
            )

            if not is_staging_runtime_connection(conn):
                raise LocalBackupV2Error(
                    "price APPLY staging runtime identity is absent"
                )
        except LocalBackupV2Error:
            raise
        except Exception as exc:
            raise LocalBackupV2Error(
                "price APPLY database state target differs"
            ) from exc
        if schema != STAGING_SCHEMA:
            raise LocalBackupV2Error(
                "price APPLY database state schema differs"
            )
        try:
            row = conn.execute(
                sql.SQL(
                    "SELECT {}.synthetic_staging_backup_v2_state_facts()"
                ).format(sql.Identifier(schema))
            ).fetchone()
        except Exception as exc:
            raise LocalBackupV2Error(
                "price APPLY staging state evidence is unavailable"
            ) from exc
        if row is None or len(row) != 1 or not isinstance(row[0], dict):
            raise LocalBackupV2Error(
                "price APPLY staging state evidence differs"
            )
        return _validated_state_evidence(
            {"facts": row[0], "sha256": canonical_sha256(row[0])}
        )

    target = sql.Identifier(schema)
    row = conn.execute(
        sql.SQL(
            "SELECT "
            "(SELECT count(*) FROM {}.supplier_mapping_review_batches),"
            "(SELECT count(*) FROM {}.supplier_mapping_review_candidates),"
            "(SELECT count(*) FROM {}.supplier_mapping_decisions),"
            "(SELECT count(*) FROM {}.supplier_offer_selection_events),"
            "(SELECT count(*) FROM {}.supplier_offer_selection_heads),"
            "(SELECT count(*) FROM {}.runs),"
            "(SELECT count(*) FROM {}.purchase_orders),"
            "(SELECT count(*) FROM {}.monday_run_artifacts),"
            "COALESCE((SELECT string_agg(payload_sha256,'|' ORDER BY payload_sha256) FROM {}.supplier_mapping_decisions),''),"
            "COALESCE((SELECT string_agg(payload_sha256,'|' ORDER BY payload_sha256) FROM {}.supplier_offer_selection_events),''),"
            "COALESCE((SELECT string_agg(input_fingerprint,'|' ORDER BY run_id) FROM {}.runs),''),"
            "COALESCE((SELECT string_agg(sha256,'|' ORDER BY monday_run_artifact_id) FROM {}.monday_run_artifacts),'')"
        ).format(*(target for _ in range(12)))
    ).fetchone()
    if row is None:
        raise LocalBackupV2Error("price APPLY database state evidence differs")
    relation_names = [
        str(item[0])
        for item in conn.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind IN ('r','p') ORDER BY c.relname",
            (schema,),
        ).fetchall()
    ]
    relation_inventory = []
    for relation_name in relation_names:
        payloads = [
            str(item[0])
            for item in conn.execute(
                sql.SQL(
                    "SELECT pg_catalog.to_jsonb(t)::text FROM {}.{} t "
                    "ORDER BY pg_catalog.to_jsonb(t)::text"
                ).format(target, sql.Identifier(relation_name))
            ).fetchall()
        ]
        relation_inventory.append(
            {
                "relation": relation_name,
                "row_count": len(payloads),
                "sha256": hashlib.sha256("\n".join(payloads).encode()).hexdigest(),
            }
        )
    sequence_names = [
        str(item[0])
        for item in conn.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind='S' ORDER BY c.relname",
            (schema,),
        ).fetchall()
    ]
    sequence_inventory = []
    for sequence_name in sequence_names:
        sequence_value = conn.execute(
            sql.SQL("SELECT last_value,is_called FROM {}.{}").format(
                target, sql.Identifier(sequence_name)
            )
        ).fetchone()
        sequence_inventory.append(
            {
                "sequence": sequence_name,
                "last_value": int(sequence_value[0]),
                "is_called": bool(sequence_value[1]),
            }
        )
    names = (
        "review_batches",
        "review_candidates",
        "mapping_decisions",
        "selection_events",
        "selection_heads",
        "runs",
        "purchase_orders",
        "artifacts",
        "decision_payloads",
        "selection_payloads",
        "run_fingerprints",
        "artifact_hashes",
    )
    facts = dict(zip(names, row, strict=True))
    facts["relation_inventory"] = relation_inventory
    facts["sequence_inventory"] = sequence_inventory
    return {"facts": facts, "sha256": canonical_sha256(facts)}


def verify_bound_price_apply_backup() -> VerifiedPriceApplyBackup:
    """Verify the launcher's exact V2 path, manifest, dump and storage bytes."""

    expected_profile = "legacy"
    expected_staging_release: Mapping[str, Any] | None = None
    expected_staging_target: Any | None = None
    database_url = os.getenv("DATABASE_URL", "")
    try:
        database_username = urlparse(database_url).username
    except ValueError:
        database_username = None
    staging_signal = (
        database_username == "buffalo_synthetic_runtime"
        or bool(os.getenv("BUFFALO_STAGING_POSTGRES_SERVICE_ID", "").strip())
    )
    legacy_binding_names = {
        RUNTIME_ROOT_ENV,
        MANIFEST_ENV,
        MANIFEST_SHA_ENV,
        SOURCE_COMMIT_ENV,
        SOURCE_TREE_ENV,
    }
    if staging_signal:
        if any(os.getenv(name, "") for name in legacy_binding_names):
            raise LocalBackupV2Error("price APPLY recovery profile differs")
        try:
            from .synthetic_staging_database import (
                staging_backup_release,
                target_from_environment,
            )

            target = target_from_environment(os.environ)
            expected_staging_release = staging_backup_release(target)
            expected_staging_target = target
            expected_profile = "staging"
        except Exception as exc:
            raise LocalBackupV2Error(
                "price APPLY staging target differs"
            ) from exc
        binding = {
            name: os.getenv(name, "")
            for name in STAGING_BINDING_ENVIRONMENT_NAMES
        }
        if any(not value for value in binding.values()):
            raise LocalBackupV2Error("price APPLY recovery binding is absent")
        runtime_root = Path(binding[STAGING_RUNTIME_ROOT_ENV])
        manifest_path = Path(binding[STAGING_MANIFEST_ENV])
        manifest_sha = binding[STAGING_MANIFEST_SHA_ENV]
        source_commit = binding[STAGING_SOURCE_COMMIT_ENV]
        source_tree = binding[STAGING_SOURCE_TREE_ENV]
    else:
        if any(os.getenv(name, "") for name in STAGING_BINDING_ENVIRONMENT_NAMES):
            raise LocalBackupV2Error("price APPLY recovery profile differs")
        runtime_root = Path(os.getenv(RUNTIME_ROOT_ENV, ""))
        manifest_path = Path(os.getenv(MANIFEST_ENV, ""))
        manifest_sha = os.getenv(MANIFEST_SHA_ENV, "")
        source_commit = os.getenv(SOURCE_COMMIT_ENV, "")
        source_tree = os.getenv(SOURCE_TREE_ENV, "")
    return verify_price_apply_backup(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        expected_manifest_sha=manifest_sha,
        expected_commit=source_commit,
        expected_tree=source_tree,
        expected_profile=expected_profile,
        expected_staging_release=expected_staging_release,
        expected_staging_target=expected_staging_target,
    )


def verify_price_apply_backup(
    *,
    runtime_root: Path,
    manifest_path: Path,
    expected_manifest_sha: str,
    expected_commit: str,
    expected_tree: str,
    expected_profile: str = "legacy",
    expected_staging_release: Mapping[str, Any] | None = None,
    expected_staging_target: Any | None = None,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> VerifiedPriceApplyBackup:
    """Verify one explicitly launcher-resolved V2 manifest."""

    if (
        not str(runtime_root)
        or not str(manifest_path)
        or _SHA256.fullmatch(expected_manifest_sha) is None
        or _GIT_ID.fullmatch(expected_commit) is None
        or _GIT_ID.fullmatch(expected_tree) is None
    ):
        raise LocalBackupV2Error("price APPLY recovery binding is absent")
    if expected_profile not in {"legacy", "staging"}:
        raise LocalBackupV2Error("price APPLY recovery profile differs")
    staging_manifest = expected_profile == "staging"
    root = runtime_root
    if not root.is_absolute() or not manifest_path.is_absolute():
        raise LocalBackupV2Error("price APPLY recovery path is not absolute")
    _require_owned(
        root,
        mode=0o700,
        directory=True,
        expected_uid=expected_owner_uid,
        expected_gid=expected_owner_gid,
        strict_file_identity=staging_manifest,
    )
    backups = root / "backups"
    _require_owned(
        backups,
        mode=0o700,
        directory=True,
        expected_uid=expected_owner_uid,
        expected_gid=expected_owner_gid,
        strict_file_identity=staging_manifest,
    )
    try:
        relative = manifest_path.relative_to(backups)
    except ValueError as exc:
        raise LocalBackupV2Error("price APPLY recovery path is outside backup root") from exc
    if len(relative.parts) != 2 or relative.name != "manifest.json":
        raise LocalBackupV2Error("price APPLY recovery label shape differs")
    _require_owned(
        manifest_path.parent,
        mode=0o700,
        directory=True,
        expected_uid=expected_owner_uid,
        expected_gid=expected_owner_gid,
        strict_file_identity=staging_manifest,
    )
    _require_owned(
        manifest_path,
        mode=0o600,
        directory=False,
        expected_uid=expected_owner_uid,
        expected_gid=expected_owner_gid,
        strict_file_identity=staging_manifest,
    )
    raw = _read_manifest_bytes(
        manifest_path,
        expected_uid=expected_owner_uid,
        expected_gid=expected_owner_gid,
        strict_file_identity=staging_manifest,
    )
    if hashlib.sha256(raw).hexdigest() != expected_manifest_sha:
        raise LocalBackupV2Error("price APPLY recovery manifest bytes differ")
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBackupV2Error("price APPLY recovery manifest is malformed") from exc
    expected_keys = {
        "contract",
        "created_utc",
        "database",
        "database_dump",
        "storage_archive",
        "state",
        "limitations",
        "source_git",
        "schema_release",
        "price_replacement",
        "prechange_current_scope",
    }
    if staging_manifest:
        expected_keys.add("staging_release")
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise LocalBackupV2Error("price APPLY recovery manifest contract differs")
    if manifest.get("contract") != BACKUP_V2_CONTRACT:
        raise LocalBackupV2Error("price APPLY recovery manifest contract differs")
    if manifest.get("limitations") != [
        "same-host local recovery only",
        "synthetic price APPLY recovery proof only",
        "sessions and secret files are intentionally excluded",
    ]:
        raise LocalBackupV2Error("price APPLY recovery limitations differ")
    created_utc = manifest.get("created_utc")
    if (
        not isinstance(created_utc, str)
        or re.fullmatch(r"\d{8}T\d{6}Z", created_utc) is None
    ):
        raise LocalBackupV2Error("price APPLY recovery creation time differs")
    state = _validated_state_evidence(manifest.get("state"))
    source_git = manifest.get("source_git")
    if source_git != {"commit": expected_commit, "tree": expected_tree}:
        raise LocalBackupV2Error("price APPLY source identity differs")
    database = manifest.get("database")
    staging_release_sha256: str | None = None
    runtime_attestation_identity: str | None = None
    if staging_manifest:
        from .synthetic_staging_database import (
            EXPECTED_DATABASE,
            OBJECT_OWNER,
            RUNTIME_LOGIN,
            STAGING_BACKUP_RELEASE_SHA256,
            EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
            staging_backup_release,
        )

        staging_release = manifest.get("staging_release")
        if (
            expected_staging_release is None
            or expected_staging_target is None
            or dict(expected_staging_release) != staging_backup_release()
            or staging_release != dict(expected_staging_release)
            or canonical_sha256(staging_release)
            != STAGING_BACKUP_RELEASE_SHA256
        ):
            raise LocalBackupV2Error(
                "price APPLY staging release differs"
            )
        staging_release_sha256 = STAGING_BACKUP_RELEASE_SHA256
        expected_staging_target.validate_static()
        expected_database_keys = {
            "database",
            "postgres_major",
            "server_address",
            "session_user",
            "current_user",
            "database_owner",
            "server_host",
        }
        if not isinstance(database, dict) or set(database) != expected_database_keys:
            raise LocalBackupV2Error("price APPLY database identity differs")
        try:
            server_address = ipaddress.ip_address(
                str(database.get("server_address", ""))
            )
        except ValueError as exc:
            raise LocalBackupV2Error(
                "price APPLY database identity differs"
            ) from exc
        if (
            database.get("database") != EXPECTED_DATABASE
            or database.get("postgres_major") != 16
            or database.get("session_user") != RUNTIME_LOGIN
            or database.get("current_user") != RUNTIME_LOGIN
            or database.get("database_owner") != OBJECT_OWNER
            or database.get("server_host")
            != expected_staging_target.expected_private_host
            or (
                expected_staging_target.owned_local_port is not None
                and not server_address.is_loopback
            )
            or (
                expected_staging_target.owned_local_port is None
                and (
                    server_address.is_loopback
                    or not server_address.is_private
                    or server_address.is_unspecified
                    or server_address.is_multicast
                )
            )
        ):
            raise LocalBackupV2Error("price APPLY database identity differs")
        runtime_attestation_identity = EXPECTED_RUNTIME_ATTESTATION_IDENTITY
    else:
        if (
            expected_staging_release is not None
            or expected_staging_target is not None
        ):
            raise LocalBackupV2Error("price APPLY recovery profile differs")
        expected_database_suffix = (
            "_test"
            if os.getenv("BUFFALO_RUNTIME_MODE", "").strip().upper()
            == "AUTOMATED_TEST"
            else "_demo"
        )
        if (
            not isinstance(database, dict)
            or database.get("postgres_major") != 16
            or database.get("session_user") != "qa_release_login"
            or database.get("current_user") != "qa_mapping_owner"
            or database.get("database_owner") != "qa_mapping_owner"
            or not isinstance(database.get("database"), str)
            or not database["database"].endswith(expected_database_suffix)
            or database.get("server_address") not in {"127.0.0.1", "::1"}
        ):
            raise LocalBackupV2Error("price APPLY database identity differs")
    schema_release = manifest.get("schema_release")
    if (
        not isinstance(schema_release, dict)
        or schema_release.get("migration") != "016_synthetic_price_replacement.sql"
        or _SHA256.fullmatch(str(schema_release.get("migration_sha256", ""))) is None
        or _SHA256.fullmatch(str(schema_release.get("catalog_sha256", ""))) is None
    ):
        raise LocalBackupV2Error("price APPLY schema release differs")
    if staging_manifest:
        from .synthetic_price_replacement_contract import (
            CATALOG_SHA256 as PRICE_CATALOG_SHA256,
            MIGRATION_SHA256 as PRICE_MIGRATION_SHA256,
        )

        # This established field remains the immutable 016 source release.
        # The installed staging successor is separately and exactly bound by
        # staging_release.price_catalog_sha256.
        if schema_release != {
            "migration": "016_synthetic_price_replacement.sql",
            "migration_sha256": PRICE_MIGRATION_SHA256,
            "catalog_sha256": PRICE_CATALOG_SHA256,
        }:
            raise LocalBackupV2Error("price APPLY schema release differs")
    price = manifest.get("price_replacement")
    required_price_keys = {
        "price_book_batch_id",
        "vendor_id",
        "price_scope_key",
        "prior_event_id",
        "prior_head_version",
        "raw_content_sha256",
        "raw_storage_key",
    }
    if (
        not isinstance(price, dict)
        or set(price) != required_price_keys
        or price.get("price_scope_key") != "COMPLETE_VENDOR"
        or not isinstance(price.get("prior_head_version"), int)
        or isinstance(price.get("prior_head_version"), bool)
        or price["prior_head_version"] < 1
        or _SHA256.fullmatch(str(price.get("raw_content_sha256", ""))) is None
        or price.get("raw_storage_key")
        != f"price-books/raw/{price.get('raw_content_sha256')}.csv"
        or any(not isinstance(price.get(key), str) or not price[key]
               for key in ("price_book_batch_id", "vendor_id", "prior_event_id"))
    ):
        raise LocalBackupV2Error("price APPLY batch binding differs")
    scope = manifest.get("prechange_current_scope")
    if (
        not isinstance(scope, dict)
        or set(scope) != {"rows", "rows_sha256", "database_scope_sha256"}
        or not isinstance(scope.get("rows"), list)
        or _SHA256.fullmatch(str(scope.get("rows_sha256", ""))) is None
        or _SHA256.fullmatch(str(scope.get("database_scope_sha256", ""))) is None
        or canonical_sha256(scope["rows"]) != scope["rows_sha256"]
    ):
        raise LocalBackupV2Error("price APPLY prechange scope differs")
    dump_path = _verify_member(
        manifest_path.parent,
        manifest.get("database_dump"),
        "database.dump",
        expected_uid=expected_owner_uid,
        expected_gid=expected_owner_gid,
        strict_file_identity=staging_manifest,
    )
    archive_value = manifest.get("storage_archive")
    if (
        not isinstance(archive_value, dict)
        or set(archive_value) != {"path", "sha256", "bytes", "files"}
    ):
        raise LocalBackupV2Error("price APPLY storage archive differs")
    archive_path = _verify_member(
        manifest_path.parent,
        {key: archive_value[key] for key in ("path", "sha256", "bytes")},
        "storage.tar",
        expected_uid=expected_owner_uid,
        expected_gid=expected_owner_gid,
        strict_file_identity=staging_manifest,
    )
    files_value = archive_value.get("files")
    if not isinstance(files_value, list):
        raise LocalBackupV2Error("price APPLY storage inventory differs")
    files = [_member_record(item) for item in files_value]
    if files != sorted(files) or len({item[0] for item in files}) != len(files):
        raise LocalBackupV2Error("price APPLY storage inventory differs")
    expected = {path: (digest, size) for path, digest, size in files}
    if (
        price["raw_storage_key"] not in expected
        or expected[price["raw_storage_key"]][0]
        != price["raw_content_sha256"]
    ):
        raise LocalBackupV2Error("price APPLY raw source is absent from backup")
    try:
        with tarfile.open(archive_path, "r") as archive:
            members = archive.getmembers()
            if [member.name for member in members] != [item[0] for item in files]:
                raise LocalBackupV2Error("price APPLY storage archive inventory differs")
            for member in members:
                if not member.isfile() or member.size != expected[member.name][1]:
                    raise LocalBackupV2Error("price APPLY storage archive member differs")
                source = archive.extractfile(member)
                if source is None:
                    raise LocalBackupV2Error("price APPLY storage archive member differs")
                digest, size = _stream_sha256(source)
                if (
                    size != expected[member.name][1]
                    or digest != expected[member.name][0]
                ):
                    raise LocalBackupV2Error(
                        "price APPLY storage archive member differs"
                    )
    except tarfile.TarError as exc:
        raise LocalBackupV2Error("price APPLY storage archive is unreadable") from exc
    return VerifiedPriceApplyBackup(
        manifest_ref=str(relative.parent),
        manifest_sha256=expected_manifest_sha,
        dump_sha256=_sha256_file(dump_path),
        storage_sha256=_sha256_file(archive_path),
        prechange_scope_sha256=scope["database_scope_sha256"],
        database=database["database"],
        batch_id=price["price_book_batch_id"],
        vendor_id=price["vendor_id"],
        price_scope_key=price["price_scope_key"],
        prior_event_id=price["prior_event_id"],
        prior_head_version=price["prior_head_version"],
        raw_content_sha256=price["raw_content_sha256"],
        raw_storage_key=price["raw_storage_key"],
        migration_sha256=schema_release["migration_sha256"],
        catalog_sha256=schema_release["catalog_sha256"],
        state=state,
        target_kind=("staging" if staging_manifest else "legacy-local"),
        staging_release_sha256=staging_release_sha256,
        runtime_attestation_identity=runtime_attestation_identity,
    )

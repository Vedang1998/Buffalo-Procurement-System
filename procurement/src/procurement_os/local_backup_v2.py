"""Strict local backup V2 transport used as synthetic APPLY recovery proof.

The browser never supplies a path or label.  The local launcher resolves one
owned backup beneath its runtime root and passes the exact manifest path and
digest to the child process.  This module re-verifies those bytes without
granting any real-price authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from typing import Any

from psycopg import sql


BACKUP_V2_CONTRACT = "BUFFALO_LOCAL_CANDIDATE_BACKUP_V2"
MANIFEST_ENV = "BUFFALO_PRICE_APPLY_BACKUP_MANIFEST"
MANIFEST_SHA_ENV = "BUFFALO_PRICE_APPLY_BACKUP_MANIFEST_SHA256"
RUNTIME_ROOT_ENV = "BUFFALO_LOCAL_RUNTIME_ROOT"
SOURCE_COMMIT_ENV = "BUFFALO_SOURCE_COMMIT"
SOURCE_TREE_ENV = "BUFFALO_SOURCE_TREE"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_ID = re.compile(r"^[0-9a-f]{40}$")


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


def _require_owned(path: Path, *, mode: int, directory: bool) -> None:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalBackupV2Error("price APPLY recovery path is unavailable") from exc
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        path.is_symlink()
        or not expected
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_uid != os.getuid()
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


def _verify_member(parent: Path, value: Any, expected_name: str) -> Path:
    name, digest, size = _member_record(value)
    if name != expected_name or len(Path(name).parts) != 1:
        raise LocalBackupV2Error("price APPLY recovery payload path differs")
    path = parent / name
    _require_owned(path, mode=0o600, directory=False)
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


def database_state_evidence(conn: Any, *, schema: str) -> dict[str, Any]:
    """Recompute V2 relation/sequence evidence through the deciding connection."""

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

    return verify_price_apply_backup(
        runtime_root=Path(os.getenv(RUNTIME_ROOT_ENV, "")),
        manifest_path=Path(os.getenv(MANIFEST_ENV, "")),
        expected_manifest_sha=os.getenv(MANIFEST_SHA_ENV, ""),
        expected_commit=os.getenv(SOURCE_COMMIT_ENV, ""),
        expected_tree=os.getenv(SOURCE_TREE_ENV, ""),
    )


def verify_price_apply_backup(
    *,
    runtime_root: Path,
    manifest_path: Path,
    expected_manifest_sha: str,
    expected_commit: str,
    expected_tree: str,
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
    root = runtime_root
    if not root.is_absolute() or not manifest_path.is_absolute():
        raise LocalBackupV2Error("price APPLY recovery path is not absolute")
    _require_owned(root, mode=0o700, directory=True)
    backups = root / "backups"
    _require_owned(backups, mode=0o700, directory=True)
    try:
        relative = manifest_path.relative_to(backups)
    except ValueError as exc:
        raise LocalBackupV2Error("price APPLY recovery path is outside backup root") from exc
    if len(relative.parts) != 2 or relative.name != "manifest.json":
        raise LocalBackupV2Error("price APPLY recovery label shape differs")
    _require_owned(manifest_path.parent, mode=0o700, directory=True)
    _require_owned(manifest_path, mode=0o600, directory=False)
    raw = manifest_path.read_bytes()
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
        manifest_path.parent, manifest.get("database_dump"), "database.dump"
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
                if source is None or hashlib.sha256(source.read()).hexdigest() != expected[member.name][0]:
                    raise LocalBackupV2Error("price APPLY storage archive member differs")
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
    )

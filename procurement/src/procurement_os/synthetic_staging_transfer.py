"""Private one-time transfer for the immutable pre-017 synthetic fixture.

This module is an operator tool, never an application-startup path.  It
exports the already guarded development-forecast-v2 fixture from one owned
PostgreSQL 16 cluster and restores that exact dump once into an empty,
dedicated target.  The existing staging bootstrap/provision/attestation
functions then perform and prove the reviewed legacy-to-staging transition.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from typing import Any, Mapping
from urllib.parse import urlparse

import psycopg
from psycopg import sql

from .local_backup_v2 import (
    _validated_state_evidence,
    canonical_sha256,
    database_state_evidence,
)
from .synthetic_staging_backup_v2 import (
    SyntheticStagingBackupError,
    _hash_file,
    _paths_overlap,
    _postgres_program,
    _require_directory,
    _source_git_boundaries,
    _source_identity,
    _validate_dump_archive,
    _write_exclusive,
)
from .synthetic_staging_database import (
    DEVELOPMENT_FIXTURE_CONTRACT,
    DEVELOPMENT_FIXTURE_PROFILE,
    EXPECTED_DATABASE,
    EXPECTED_DATABASE_CREATION,
    EXPECTED_POSTGRES_MAJOR,
    EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
    FIXTURE_BUSINESS_DATE,
    FIXTURE_CONTRACT,
    IMMUTABLE_FIXTURE_MANIFEST_SHA256,
    LEGACY_LOGIN,
    LEGACY_MARKERS,
    LEGACY_OWNER,
    LEGACY_RETIREMENT_CATALOG_MARKERS,
    MULTIVENDOR_FIXTURE_CONTRACT,
    OBJECT_OWNER,
    PROVISIONER,
    RUNTIME_LOGIN,
    SCHEMA,
    STAGING_BACKUP_RELEASE_SHA256,
    STAGING_TRANSFER_CONTRACT,
    SUCCESSOR_CATALOG_SHA256,
    TRANSFER_SOURCE_CATALOG_SHA256,
    SyntheticStagingDatabaseError,
    SyntheticStagingTarget,
    _metadata,
    _observed_staging_markers,
    _verify_core_global_privilege_envelope,
    _require_source_hashes,
    _verify_fixture_provenance,
    _verify_semantic_catalog_envelope,
    _verified_pgcrypto_rows,
    _verify_role_topology,
    _validated_role_secret,
    attest_provisioner_connection,
    attest_runtime_connection,
    bootstrap_roles,
    compute_immutable_fixture_sha256,
    database_creation_envelope,
    install_transfer_provenance,
    provision_contract,
    verify_transfer_source_catalog,
    verify_database_creation_envelope,
)


TRANSFER_CONTRACT = STAGING_TRANSFER_CONTRACT
SOURCE_CATALOG_CONTRACT = "BUFFALO_SYNTHETIC_TRANSFER_SOURCE_CATALOG_V1"
MANIFEST_NAME = "manifest.json"
DUMP_NAME = "database.dump"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_ID = re.compile(r"^[0-9a-f]{40}$")
_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
TRANSFER_ARCHIVE_TOC_ENTRIES = 846
TRANSFER_TOC_ENTRIES = 842
TRANSFER_TOC_SEMANTIC_SHA256 = (
    "bce860227495cf55374016878274436ae1412b5ca15a14508315df7c4651472e"
)
_ALLOWED_TOC_KINDS = tuple(
    sorted(
        {
            "ACL",
            "COMMENT",
            "CONSTRAINT",
            "DEFAULT",
            "EXTENSION",
            "FK CONSTRAINT",
            "FUNCTION",
            "INDEX",
            "SCHEMA",
            "SEQUENCE",
            "SEQUENCE OWNED BY",
            "SEQUENCE SET",
            "TABLE",
            "TABLE DATA",
            "TRIGGER",
            "VIEW",
        },
        key=len,
        reverse=True,
    )
)
_EXPECTED_SOURCE_CONTROLS = {
    "sales_dates": 138,
    "sales_rows": 966,
    "sales_variants": 7,
    "schedule_policies": 2,
    "variants": 8,
    "vendors": 3,
    "supplier_offers": 8,
    "runs": 1,
    "purchase_orders": 0,
    "monday_artifacts": 0,
}
_RESTORE_OWNER_ACL_RELATIONS = (
    "monday_stale_forecast_retirements",
    "supplier_mapping_decisions",
    "supplier_mapping_review_batches",
    "supplier_mapping_review_candidates",
    "supplier_offer_selection_events",
    "supplier_offer_selection_heads",
    "v_effective_supplier_mapping_decisions",
    "v_selected_standard_supplier_offers",
    "v_supplier_offer_selection_diagnostics",
    "v_supplier_offer_selection_shadow",
)
_FORBIDDEN_TOC_KINDS = (
    " BLOB ",
    " BLOB DATA ",
    " BLOB COMMENTS ",
    " BLOBS ",
    " DATABASE ",
    " DATABASE PROPERTIES ",
    " TABLESPACE ",
    " ROLE ",
    " FOREIGN DATA WRAPPER ",
    " SERVER ",
    " USER MAPPING ",
    " EVENT TRIGGER ",
    " POLICY ",
    " RULE ",
    " TYPE ",
    " DOMAIN ",
    " COLLATION ",
    " CONVERSION ",
    " OPERATOR ",
    " OPERATOR CLASS ",
    " OPERATOR FAMILY ",
    " TEXT SEARCH ",
    " PUBLICATION ",
    " SUBSCRIPTION ",
    " LARGE OBJECT ",
    " SECURITY LABEL ",
)


class SyntheticStagingTransferError(RuntimeError):
    """The private initial transfer differs from the reviewed contract."""


@dataclass(frozen=True)
class VerifiedTransferArtifact:
    root: Path
    manifest_path: Path
    dump_path: Path
    manifest_sha256: str
    manifest: dict[str, Any]


@dataclass
class PreparedTransferDump:
    pg_restore: str
    staged: Any
    use_list: Any

    def close(self) -> None:
        for resource in (self.use_list, self.staged):
            try:
                resource.close()
            except OSError:
                # Both anonymous inputs are read-only/fsync'd before target
                # observation.  Cleanup ambiguity must not mask the durable
                # destination classification, but both closes are attempted.
                pass


def _close_connection(connection: Any) -> None:
    """Best-effort close without letting rollback failure skip close."""

    try:
        connection.rollback()
    except Exception:
        pass
    try:
        connection.close()
    except Exception:
        pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _require_credential_free_url(value: str, *, username: str) -> None:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer database URL differs"
        ) from exc
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.username != username
        or parsed.password is not None
        or not parsed.hostname
        or port is None
        or parsed.path != f"/{EXPECTED_DATABASE}"
        or parsed.fragment
        or parsed.params
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer database URL differs"
        )


def _require_initializer_url(value: str) -> None:
    _require_credential_free_url(value, username=LEGACY_LOGIN)
    parsed = urlparse(value)
    if parsed.query not in {
        "options=-c%20role%3Dqa_mapping_owner",
        "options=-c+role%3Dqa_mapping_owner",
    }:
        raise SyntheticStagingTransferError(
            "synthetic transfer initializer URL differs"
        )


def _require_plain_url(value: str, *, username: str) -> None:
    _require_credential_free_url(value, username=username)
    if urlparse(value).query:
        raise SyntheticStagingTransferError(
            "synthetic transfer database URL differs"
        )


def _role_flags(conn: Any, role: str) -> tuple[bool, ...] | None:
    row = conn.execute(
        "SELECT rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
        "rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=%s",
        (role,),
    ).fetchone()
    return None if row is None else tuple(bool(value) for value in row)


def _source_controls(conn: Any) -> dict[str, Any]:
    row = conn.execute(
        sql.SQL(
            "SELECT "
            "(SELECT count(DISTINCT sale_date) FROM {}.sales_daily),"
            "(SELECT count(*) FROM {}.sales_daily),"
            "(SELECT count(DISTINCT variant_id) FROM {}.sales_daily),"
            "(SELECT count(*) FROM {}.supplier_price_schedule_policies),"
            "(SELECT count(*) FROM {}.variants),"
            "(SELECT count(*) FROM {}.vendors),"
            "(SELECT count(*) FROM {}.supplier_offers),"
            "(SELECT count(*) FROM {}.runs),"
            "(SELECT count(*) FROM {}.purchase_orders),"
            "(SELECT count(*) FROM {}.monday_run_artifacts)"
        ).format(*(sql.Identifier(SCHEMA) for _ in range(10)))
    ).fetchone()
    if row is None:
        raise SyntheticStagingTransferError(
            "synthetic transfer fixture controls are absent"
        )
    result = {
        "sales_dates": int(row[0]),
        "sales_rows": int(row[1]),
        "sales_variants": int(row[2]),
        "schedule_policies": int(row[3]),
        "variants": int(row[4]),
        "vendors": int(row[5]),
        "supplier_offers": int(row[6]),
        "runs": int(row[7]),
        "purchase_orders": int(row[8]),
        "monday_artifacts": int(row[9]),
    }
    if result != _EXPECTED_SOURCE_CONTROLS:
        raise SyntheticStagingTransferError(
            "synthetic transfer fixture controls differ"
        )
    return result


def _object_inventory(conn: Any) -> dict[str, Any]:
    conn.execute("SET LOCAL search_path = pg_catalog")
    relations = [
        {
            "kind": str(kind),
            "name": str(name),
            "owner": str(owner),
            "acl": None if acl is None else str(acl),
        }
        for kind, name, owner, acl in conn.execute(
            "SELECT c.relkind,c.relname,pg_catalog.pg_get_userbyid(c.relowner),"
            "c.relacl::text "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname=%s "
            "AND c.relkind IN ('r','p','v','m','S','f') ORDER BY c.relkind,c.relname",
            (SCHEMA,),
        ).fetchall()
    ]
    routines = [
        {
            "name": str(name),
            "identity_arguments": str(arguments),
            "kind": str(kind),
            "owner": str(owner),
            "config": None if config is None else [str(item) for item in config],
        }
        for name, arguments, kind, owner, config in conn.execute(
            "SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
            "p.prokind,pg_catalog.pg_get_userbyid(p.proowner),p.proconfig "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
            "ON n.oid=p.pronamespace LEFT JOIN pg_catalog.pg_depend d "
            "ON d.classid='pg_proc'::regclass AND d.objid=p.oid "
            "AND d.deptype='e' WHERE n.nspname=%s AND d.objid IS NULL "
            "ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)",
            (SCHEMA,),
        ).fetchall()
    ]
    extensions = [
        {"name": str(name), "version": str(version), "owner": str(owner)}
        for name, version, owner in conn.execute(
            "SELECT e.extname,e.extversion,pg_catalog.pg_get_userbyid(e.extowner) "
            "FROM pg_catalog.pg_extension e WHERE e.extname='pgcrypto' "
            "ORDER BY e.extname"
        ).fetchall()
    ]
    schema_owner = conn.execute(
        "SELECT pg_catalog.pg_get_userbyid(nspowner) FROM pg_catalog.pg_namespace "
        "WHERE nspname=%s",
        (SCHEMA,),
    ).fetchone()
    unexpected_semantics = conn.execute(
        "SELECT "
        "EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r "
        "JOIN pg_catalog.pg_class c ON c.oid=r.ev_class "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND r.rulename<>'_RETURN'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_policy p "
        "JOIN pg_catalog.pg_class c ON c.oid=p.polrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_description d "
        "LEFT JOIN pg_catalog.pg_class c "
        "ON d.classoid='pg_catalog.pg_class'::pg_catalog.regclass "
        "AND d.objoid=c.oid "
        "LEFT JOIN pg_catalog.pg_proc p "
        "ON d.classoid='pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND d.objoid=p.oid "
        "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE cn.nspname=%s OR pn.nspname=%s "
        "OR (d.classoid='pg_catalog.pg_namespace'::pg_catalog.regclass "
        "AND d.objoid=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s)))",
        (SCHEMA, SCHEMA, SCHEMA, SCHEMA, SCHEMA),
    ).fetchone()
    if (
        schema_owner != (LEGACY_OWNER,)
        or len(extensions) != 1
        or extensions[0]["name"] != "pgcrypto"
        or extensions[0]["owner"] != LEGACY_OWNER
        or not extensions[0]["version"]
        or unexpected_semantics is None
        or any(bool(value) for value in unexpected_semantics)
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer object inventory differs"
        )
    if not relations or not routines:
        raise SyntheticStagingTransferError(
            "synthetic transfer object inventory differs"
        )
    expected_owner_acl = f"{{{LEGACY_OWNER}=arwdDxt/{LEGACY_OWNER}}}"
    owner_acl_relations = tuple(
        item["name"]
        for item in relations
        if item["acl"] == expected_owner_acl
    )
    if owner_acl_relations != _RESTORE_OWNER_ACL_RELATIONS:
        raise SyntheticStagingTransferError(
            "synthetic transfer owner ACL inventory differs"
        )
    return {
        "schema_owner": str(schema_owner[0]),
        "relations": relations,
        "routines": routines,
        "extensions": extensions,
    }


def _legacy_source_snapshot(
    conn: Any, *, database_acl: str = "source"
) -> dict[str, Any]:
    _require_source_hashes()
    conn.execute("SET LOCAL search_path = pg_catalog")
    _verify_core_global_privilege_envelope(conn)
    row = conn.execute(
        "SELECT pg_catalog.current_database(),"
        "pg_catalog.current_setting('server_version_num')::int/10000,"
        "session_user::text,current_user::text,"
        "pg_catalog.pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d "
        "WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    if row != (
        EXPECTED_DATABASE,
        EXPECTED_POSTGRES_MAJOR,
        LEGACY_LOGIN,
        LEGACY_OWNER,
        LEGACY_OWNER,
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer source identity differs"
        )
    if any(
        _role_flags(conn, role) is not None
        for role in (OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN)
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer source role state differs"
        )
    try:
        _verify_role_topology(conn, bootstrapped=False)
        source_catalog_sha256 = verify_transfer_source_catalog(
            conn, database_acl=database_acl
        )
        _verify_fixture_provenance(conn)
        _verified_pgcrypto_rows(conn)
    except SyntheticStagingDatabaseError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer source contract differs"
        ) from exc
    if _observed_staging_markers(conn):
        raise SyntheticStagingTransferError(
            "synthetic transfer source is already transitioned"
        )
    metadata = _metadata(conn, list(LEGACY_MARKERS))
    retirement = metadata.get("monday_forecast_v2_retirement_catalog_sha256")
    if (
        set(metadata) != set(LEGACY_MARKERS)
        or retirement not in LEGACY_RETIREMENT_CATALOG_MARKERS
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer source markers differ"
        )
    fixture_sha = compute_immutable_fixture_sha256(conn)
    if fixture_sha != IMMUTABLE_FIXTURE_MANIFEST_SHA256:
        raise SyntheticStagingTransferError(
            "synthetic transfer fixture manifest differs"
        )
    state = database_state_evidence(conn, schema=SCHEMA)
    system_identifier_row = conn.execute(
        "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
    ).fetchone()
    if (
        system_identifier_row is None
        or re.fullmatch(r"[1-9][0-9]{9,19}", str(system_identifier_row[0]))
        is None
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer source cluster identity differs"
        )
    return {
        "database": {
            "name": EXPECTED_DATABASE,
            "postgres_major": EXPECTED_POSTGRES_MAJOR,
            "schema": SCHEMA,
            "session_user": LEGACY_LOGIN,
            "current_user": LEGACY_OWNER,
            "database_owner": LEGACY_OWNER,
            "creation": database_creation_envelope(conn),
            "system_identifier": str(system_identifier_row[0]),
        },
        "fixture": {
            "contract": FIXTURE_CONTRACT,
            "multivendor_contract": MULTIVENDOR_FIXTURE_CONTRACT,
            "development_contract": DEVELOPMENT_FIXTURE_CONTRACT,
            "profile": DEVELOPMENT_FIXTURE_PROFILE,
            "business_date": FIXTURE_BUSINESS_DATE,
            "manifest_sha256": fixture_sha,
        },
        "legacy_markers": metadata,
        "source_catalog": {
            "contract": SOURCE_CATALOG_CONTRACT,
            "sha256": source_catalog_sha256,
        },
        "controls": _source_controls(conn),
        "objects": _object_inventory(conn),
        "state": state,
    }


def _snapshot_source_connection(
    conn: Any, *, database_acl: str = "source"
) -> dict[str, Any]:
    try:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        conn.execute("SET LOCAL search_path = pg_catalog")
        return _legacy_source_snapshot(conn, database_acl=database_acl)
    except SyntheticStagingTransferError:
        raise
    except Exception as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer source is unavailable"
        ) from exc
    finally:
        conn.rollback()


def _snapshot_source(source_url: str) -> dict[str, Any]:
    _require_initializer_url(source_url)
    try:
        with psycopg.connect(source_url, connect_timeout=5) as conn:
            return _snapshot_source_connection(conn)
    except SyntheticStagingTransferError:
        raise
    except Exception as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer source is unavailable"
        ) from exc


def _open_verified_source_snapshot(
    source_url: str,
    source_admin_url: str,
) -> tuple[Any, dict[str, Any], str]:
    """Keep one verified snapshot open for the exact pg_dump invocation."""

    _require_initializer_url(source_url)
    admin_username = urlparse(source_admin_url).username
    if not admin_username:
        raise SyntheticStagingTransferError(
            "synthetic transfer source administrative URL differs"
        )
    _require_plain_url(source_admin_url, username=admin_username)
    if _endpoint(source_admin_url) != _endpoint(source_url):
        raise SyntheticStagingTransferError(
            "synthetic transfer source endpoint differs"
        )
    conn: Any | None = None
    try:
        conn = psycopg.connect(source_admin_url, connect_timeout=5)
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        conn.execute("SET LOCAL search_path = pg_catalog")
        _verify_core_global_privilege_envelope(conn)
        row = conn.execute(
            "SELECT current_user=session_user AND rolsuper "
            "FROM pg_catalog.pg_authid WHERE rolname=current_user"
        ).fetchone()
        if row != (True,):
            raise SyntheticStagingTransferError(
                "synthetic transfer source administrative identity differs"
            )
        _audit_global_envelope(conn, stage="source")
        conn.execute(
            sql.SQL("SET SESSION AUTHORIZATION {}").format(
                sql.Identifier(LEGACY_LOGIN)
            )
        )
        conn.execute(
            sql.SQL("SET ROLE {}").format(sql.Identifier(LEGACY_OWNER))
        )
        source = _legacy_source_snapshot(conn)
        snapshot_row = conn.execute(
            "SELECT pg_catalog.pg_export_snapshot()"
        ).fetchone()
        if (
            snapshot_row is None
            or not isinstance(snapshot_row[0], str)
            or re.fullmatch(r"[0-9A-Fa-f-]{10,80}", snapshot_row[0]) is None
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer source snapshot differs"
            )
        return conn, source, snapshot_row[0]
    except SyntheticStagingTransferError:
        if conn is not None:
            _close_connection(conn)
        raise
    except Exception as exc:
        if conn is not None:
            _close_connection(conn)
        raise SyntheticStagingTransferError(
            "synthetic transfer source is unavailable"
        ) from exc


def _dump_environment(program: str) -> dict[str, str]:
    environment = {
        "PATH": str(Path(program).parent),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    for name in ("PGPASSFILE", "PGSSLMODE", "PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _normalized_toc(
    pg_restore: str,
    dump_path: Path,
    *,
    pass_fds: tuple[int, ...] = (),
) -> tuple[str, int, str, bytes]:
    try:
        result = subprocess.run(
            [pg_restore, "--list", str(dump_path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="ascii",
            errors="strict",
            timeout=30,
            env={"PATH": str(Path(pg_restore).parent), "LANG": "C.UTF-8"},
            pass_fds=pass_fds,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer dump table of contents differs"
        ) from exc
    entries: list[str] = []
    use_list: list[str] = []
    descriptions: list[str] = []
    dump_ids: set[int] = set()
    header_fields: set[str] = set()
    seen_record = False
    for raw_line in result.stdout.splitlines():
        try:
            raw_line.encode("ascii")
        except UnicodeEncodeError as exc:
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            ) from exc
        if any(ord(character) < 0x20 or ord(character) > 0x7E for character in raw_line):
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            )
        if not raw_line:
            if seen_record:
                raise SyntheticStagingTransferError(
                    "synthetic transfer dump table of contents differs"
                )
            continue
        if raw_line.startswith(";"):
            if seen_record or re.match(r"^;\s*[0-9]+;", raw_line):
                raise SyntheticStagingTransferError(
                    "synthetic transfer dump table of contents differs"
                )
            if raw_line == ";":
                continue
            patterns = {
                "created": r"^; Archive created at .+$",
                "database": rf"^;     dbname: {re.escape(EXPECTED_DATABASE)}$",
                "entries": rf"^;     TOC Entries: {TRANSFER_ARCHIVE_TOC_ENTRIES}$",
                "compression": r"^;     Compression: [ -~]+$",
                "dump_version": r"^;     Dump Version: [0-9.-]+$",
                "format": r"^;     Format: CUSTOM$",
                "integer": r"^;     Integer: [48] bytes$",
                "offset": r"^;     Offset: [48] bytes$",
                "source_version": rf"^;     Dumped from database version: {EXPECTED_POSTGRES_MAJOR}\.[ -~]+$",
                "tool_version": rf"^;     Dumped by pg_dump version: {EXPECTED_POSTGRES_MAJOR}\.[ -~]+$",
                "selected": r"^; Selected TOC Entries:$",
            }
            matched = [
                name
                for name, pattern in patterns.items()
                if re.fullmatch(pattern, raw_line)
            ]
            if len(matched) != 1 or matched[0] in header_fields:
                raise SyntheticStagingTransferError(
                    "synthetic transfer dump table of contents differs"
                )
            header_fields.add(matched[0])
            continue
        if raw_line.startswith(" "):
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            )
        seen_record = True
        entry = " ".join(raw_line.split())
        match = re.fullmatch(r"([1-9][0-9]*); ([0-9]+) ([0-9]+) (.+)", entry)
        if match is None:
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            )
        dump_id = int(match.group(1))
        description = match.group(4)
        if dump_id in dump_ids:
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            )
        dump_ids.add(dump_id)
        kind = next(
            (
                candidate
                for candidate in _ALLOWED_TOC_KINDS
                if description == candidate
                or description.startswith(f"{candidate} ")
            ),
            None,
        )
        if kind is None:
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            )
        if kind == "SCHEMA":
            allowed = description == f"SCHEMA - {SCHEMA} {LEGACY_OWNER}"
        elif kind == "EXTENSION":
            allowed = description == "EXTENSION - pgcrypto"
        elif kind == "COMMENT":
            allowed = description == "COMMENT - EXTENSION pgcrypto"
        else:
            allowed = description.startswith(f"{kind} {SCHEMA} ") and (
                description.endswith(f" {LEGACY_OWNER}")
            )
        if not allowed:
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            )
        expected_raw = (
            f"{entry} " if kind in {"EXTENSION", "COMMENT"} else entry
        )
        if raw_line != expected_raw:
            raise SyntheticStagingTransferError(
                "synthetic transfer dump table of contents differs"
            )
        entries.append(entry)
        use_list.append(raw_line)
        descriptions.append(description)
    semantic_payload = ("\n".join(sorted(descriptions)) + "\n").encode("ascii")
    semantic_sha256 = hashlib.sha256(semantic_payload).hexdigest()
    if (
        header_fields
        != {
            "created",
            "database",
            "entries",
            "compression",
            "dump_version",
            "format",
            "integer",
            "offset",
            "source_version",
            "tool_version",
            "selected",
        }
        or not seen_record
        or len(entries) != TRANSFER_TOC_ENTRIES
        or semantic_sha256 != TRANSFER_TOC_SEMANTIC_SHA256
        or sum(item.startswith("SCHEMA ") for item in descriptions) != 1
        or sum(item.startswith("EXTENSION ") for item in descriptions) != 1
        or sum(item.startswith("COMMENT ") for item in descriptions) != 1
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer dump table of contents differs"
        )
    payload = "\n".join(entries).encode("utf-8")
    return (
        hashlib.sha256(payload).hexdigest(),
        len(entries),
        semantic_sha256,
        ("\n".join(use_list) + "\n").encode("ascii"),
    )


def create_transfer_artifact(
    *,
    source_url: str,
    source_admin_url: str,
    transfer_root: Path,
    source_root: Path,
    label: str,
) -> Path:
    """Export one exact, private pre-017 fixture from an owned PG16 source."""

    if _LABEL.fullmatch(label) is None:
        raise SyntheticStagingTransferError("synthetic transfer label differs")
    try:
        transfer_info = _require_directory(
            transfer_root, label="synthetic transfer root"
        )
        source_top, source_common = _source_git_boundaries(source_root)
        for boundary in (source_top, source_common):
            boundary_info = boundary.stat(follow_symlinks=False)
            if _paths_overlap(
                transfer_root, transfer_info, boundary, boundary_info
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer root overlaps source identity"
                )
        source_identity = _source_identity(source_root)
    except SyntheticStagingTransferError:
        raise
    except SyntheticStagingBackupError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer source path differs"
        ) from exc
    destination = transfer_root / label
    dump_path = destination / DUMP_NAME
    manifest_path = destination / MANIFEST_NAME
    source_conn: Any | None = None
    destination_created = False
    completed = False
    try:
        destination.mkdir(mode=0o700)
        destination_created = True
        destination_info = destination.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(destination_info.st_mode)
            or stat.S_IMODE(destination_info.st_mode) != 0o700
            or destination_info.st_uid != os.getuid()
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer destination ownership differs"
            )
        source_conn, before, source_snapshot = _open_verified_source_snapshot(
            source_url, source_admin_url
        )
        pg_dump = _postgres_program("pg_dump")
        pg_restore = _postgres_program("pg_restore")
        if Path(pg_dump).parent != Path(pg_restore).parent:
            raise SyntheticStagingTransferError(
                "synthetic transfer PostgreSQL tool identity differs"
            )
        descriptor = os.open(
            dump_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                subprocess.run(
                    [
                        pg_dump,
                        "--dbname",
                        source_url,
                        "--snapshot",
                        source_snapshot,
                        "--format=custom",
                        "--no-large-objects",
                        "--no-publications",
                        "--no-security-labels",
                        "--no-subscriptions",
                        "--no-tablespaces",
                        "--schema",
                        SCHEMA,
                        "--extension",
                        "pgcrypto",
                    ],
                    check=True,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=180,
                    env=_dump_environment(pg_dump),
                )
                output.flush()
                os.fsync(output.fileno())
        finally:
            os.close(descriptor)
        os.chmod(dump_path, 0o600, follow_symlinks=False)
        _validate_dump_archive(pg_restore, dump_path)
        toc_sha, toc_entries, toc_semantic_sha, _use_list = _normalized_toc(
            pg_restore, dump_path
        )
        dump_sha, dump_size = _hash_file(dump_path)
        _close_connection(source_conn)
        after = _snapshot_source(source_url)
        source_identity_after = _source_identity(source_root)
        if after != before or source_identity_after != source_identity:
            raise SyntheticStagingTransferError(
                "synthetic transfer source changed during export"
            )
        manifest = {
            "contract": TRANSFER_CONTRACT,
            "source": {
                **before,
                "git": source_identity,
            },
            "dump": {
                "name": DUMP_NAME,
                "bytes": dump_size,
                "sha256": dump_sha,
                "toc_entries": toc_entries,
                "toc_semantic_sha256": toc_semantic_sha,
                "toc_sha256": toc_sha,
            },
        }
        _write_exclusive(manifest_path, _canonical_bytes(manifest))
        verify_transfer_artifact(
            artifact_root=destination,
            expected_manifest_sha256=hashlib.sha256(
                _canonical_bytes(manifest)
            ).hexdigest(),
            expected_source_commit=source_identity["commit"],
            expected_source_tree=source_identity["tree"],
        )
        completed = True
        return manifest_path
    except SyntheticStagingTransferError:
        raise
    except (SyntheticStagingBackupError, OSError, subprocess.SubprocessError) as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer export failed"
        ) from exc
    finally:
        if source_conn is not None and not source_conn.closed:
            _close_connection(source_conn)
        if destination_created and not completed:
            for path in (manifest_path, dump_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            try:
                destination.rmdir()
            except OSError:
                pass


def _read_private_file(path: Path, *, maximum: int | None = None) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or (maximum is not None and not 0 < before.st_size <= maximum)
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer artifact ownership differs"
            )
        digest = bytearray()
        remaining = int(before.st_size)
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise SyntheticStagingTransferError(
                    "synthetic transfer artifact changed while reading"
                )
            digest.extend(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        named = path.stat(follow_symlinks=False)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or (before.st_dev, before.st_ino)
            != (named.st_dev, named.st_ino)
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer artifact changed while reading"
            )
        return bytes(digest)
    except SyntheticStagingTransferError:
        raise
    except OSError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer artifact is unavailable"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"contract", "source", "dump"}:
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest shape differs"
        )
    source = value.get("source")
    dump = value.get("dump")
    if (
        value.get("contract") != TRANSFER_CONTRACT
        or not isinstance(source, dict)
        or set(source)
        != {
            "database",
            "fixture",
            "legacy_markers",
            "source_catalog",
            "controls",
            "objects",
            "state",
            "git",
        }
        or not isinstance(dump, dict)
        or set(dump)
        != {
            "name",
            "bytes",
            "sha256",
            "toc_entries",
            "toc_semantic_sha256",
            "toc_sha256",
        }
        or dump.get("name") != DUMP_NAME
        or type(dump.get("bytes")) is not int
        or dump["bytes"] <= 0
        or type(dump.get("toc_entries")) is not int
        or dump["toc_entries"] != TRANSFER_TOC_ENTRIES
        or _SHA256.fullmatch(str(dump.get("sha256", ""))) is None
        or dump.get("toc_semantic_sha256") != TRANSFER_TOC_SEMANTIC_SHA256
        or _SHA256.fullmatch(str(dump.get("toc_sha256", ""))) is None
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest shape differs"
        )
    database = source.get("database")
    fixture = source.get("fixture")
    git = source.get("git")
    expected_database = {
            "name": EXPECTED_DATABASE,
            "postgres_major": EXPECTED_POSTGRES_MAJOR,
            "schema": SCHEMA,
            "session_user": LEGACY_LOGIN,
            "current_user": LEGACY_OWNER,
            "database_owner": LEGACY_OWNER,
            "creation": dict(EXPECTED_DATABASE_CREATION),
    }
    if (
        not isinstance(database, dict)
        or set(database) != {*expected_database, "system_identifier"}
        or {key: database[key] for key in expected_database} != expected_database
        or type(database.get("system_identifier")) is not str
        or re.fullmatch(
            r"[1-9][0-9]{9,19}", str(database.get("system_identifier", ""))
        )
        is None
        or fixture
        != {
            "contract": FIXTURE_CONTRACT,
            "multivendor_contract": MULTIVENDOR_FIXTURE_CONTRACT,
            "development_contract": DEVELOPMENT_FIXTURE_CONTRACT,
            "profile": DEVELOPMENT_FIXTURE_PROFILE,
            "business_date": FIXTURE_BUSINESS_DATE,
            "manifest_sha256": IMMUTABLE_FIXTURE_MANIFEST_SHA256,
        }
        or not isinstance(git, dict)
        or set(git) != {"commit", "tree"}
        or _GIT_ID.fullmatch(str(git.get("commit", ""))) is None
        or _GIT_ID.fullmatch(str(git.get("tree", ""))) is None
        or not isinstance(source.get("legacy_markers"), dict)
        or source.get("source_catalog")
        != {
            "contract": SOURCE_CATALOG_CONTRACT,
            "sha256": TRANSFER_SOURCE_CATALOG_SHA256,
        }
        or not isinstance(source.get("controls"), dict)
        or not isinstance(source.get("objects"), dict)
        or not isinstance(source.get("state"), dict)
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest provenance differs"
        )
    markers = source["legacy_markers"]
    expected = dict(LEGACY_MARKERS)
    retirement = markers.get("monday_forecast_v2_retirement_catalog_sha256")
    if (
        set(markers) != set(expected)
        or any(
            markers[key] != expected[key]
            for key in expected
            if key != "monday_forecast_v2_retirement_catalog_sha256"
        )
        or retirement not in LEGACY_RETIREMENT_CATALOG_MARKERS
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest markers differ"
        )
    controls = source["controls"]
    objects = source["objects"]
    if (
        not isinstance(controls, dict)
        or set(controls)
        != {
            "sales_dates",
            "sales_rows",
            "sales_variants",
            "schedule_policies",
            "variants",
            "vendors",
            "supplier_offers",
            "runs",
            "purchase_orders",
            "monday_artifacts",
        }
        or controls != _EXPECTED_SOURCE_CONTROLS
        or not isinstance(objects, dict)
        or set(objects) != {"schema_owner", "relations", "routines", "extensions"}
        or objects["schema_owner"] != LEGACY_OWNER
        or not isinstance(objects["relations"], list)
        or not isinstance(objects["routines"], list)
        or not isinstance(objects["extensions"], list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"kind", "name", "owner", "acl"}
            or not item["name"]
            or item["owner"] != LEGACY_OWNER
            or item["acl"] is not None
            and not isinstance(item["acl"], str)
            for item in objects["relations"]
        )
        or any(
            not isinstance(item, dict)
            or set(item)
            != {"name", "identity_arguments", "kind", "owner", "config"}
            or not item["name"]
            or not isinstance(item["identity_arguments"], str)
            or item["kind"] not in {"f", "p"}
            or item["owner"] != LEGACY_OWNER
            or (
                item["config"] is not None
                and (
                    not isinstance(item["config"], list)
                    or len(item["config"]) != 1
                    or not isinstance(item["config"][0], str)
                    or not _reviewed_search_path(item["config"][0])
                )
            )
            for item in objects["routines"]
        )
        or len(objects["extensions"]) != 1
        or set(objects["extensions"][0]) != {"name", "version", "owner"}
        or objects["extensions"][0]["name"] != "pgcrypto"
        or objects["extensions"][0]["version"] != "1.3"
        or objects["extensions"][0]["owner"] != LEGACY_OWNER
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest inventory differs"
        )
    try:
        _validated_state_evidence(source["state"])
    except Exception as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest state differs"
        ) from exc
    return value


def verify_transfer_artifact(
    *,
    artifact_root: Path,
    expected_manifest_sha256: str,
    expected_source_commit: str,
    expected_source_tree: str,
) -> VerifiedTransferArtifact:
    """Verify exact private bytes before any destination observation."""

    if (
        _SHA256.fullmatch(expected_manifest_sha256) is None
        or _GIT_ID.fullmatch(expected_source_commit) is None
        or _GIT_ID.fullmatch(expected_source_tree) is None
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest digest differs"
        )
    try:
        _require_directory(artifact_root, label="synthetic transfer artifact")
    except SyntheticStagingBackupError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer artifact ownership differs"
        ) from exc
    manifest_path = artifact_root / MANIFEST_NAME
    dump_path = artifact_root / DUMP_NAME
    try:
        entries = sorted(path.name for path in artifact_root.iterdir())
    except OSError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer artifact is unavailable"
        ) from exc
    if entries != [DUMP_NAME, MANIFEST_NAME]:
        raise SyntheticStagingTransferError(
            "synthetic transfer artifact inventory differs"
        )
    manifest_bytes = _read_private_file(
        manifest_path, maximum=_MAX_MANIFEST_BYTES
    )
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest digest differs"
        )
    try:
        parsed = json.loads(manifest_bytes.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest is malformed"
        ) from exc
    if _canonical_bytes(parsed) != manifest_bytes:
        raise SyntheticStagingTransferError(
            "synthetic transfer manifest is not canonical"
        )
    manifest = _validate_manifest(parsed)
    if manifest["source"]["git"] != {
        "commit": expected_source_commit,
        "tree": expected_source_tree,
    }:
        raise SyntheticStagingTransferError(
            "synthetic transfer source identity differs"
        )
    dump_sha, dump_size = _hash_file(dump_path)
    if (dump_sha, dump_size) != (
        manifest["dump"]["sha256"],
        manifest["dump"]["bytes"],
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer dump digest differs"
        )
    try:
        pg_restore = _postgres_program("pg_restore")
        _validate_dump_archive(pg_restore, dump_path)
    except SyntheticStagingBackupError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer dump archive differs"
        ) from exc
    toc_sha, toc_entries, toc_semantic_sha, _use_list = _normalized_toc(
        pg_restore, dump_path
    )
    if (toc_sha, toc_entries, toc_semantic_sha) != (
        manifest["dump"]["toc_sha256"],
        manifest["dump"]["toc_entries"],
        manifest["dump"]["toc_semantic_sha256"],
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer dump table of contents differs"
        )
    return VerifiedTransferArtifact(
        root=artifact_root,
        manifest_path=manifest_path,
        dump_path=dump_path,
        manifest_sha256=expected_manifest_sha256,
        manifest=manifest,
    )


def _preflight_empty_destination(
    conn: Any, *, source_system_identifier: str
) -> None:
    conn.execute("SET LOCAL search_path = pg_catalog")
    try:
        _verify_core_global_privilege_envelope(conn)
    except SyntheticStagingDatabaseError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer destination core privilege envelope differs"
        ) from exc
    system_identifier = conn.execute(
        "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
    ).fetchone()
    if (
        re.fullmatch(r"[1-9][0-9]{9,19}", source_system_identifier) is None
        or system_identifier is None
        or re.fullmatch(r"[1-9][0-9]{9,19}", str(system_identifier[0])) is None
        or str(system_identifier[0]) == source_system_identifier
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer destination cluster identity differs"
        )
    row = conn.execute(
        "SELECT current_database(),current_setting('server_version_num')::int/10000,"
        "session_user::text,current_user::text,rolsuper,"
        "pg_catalog.pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d "
        "JOIN pg_catalog.pg_authid r ON r.rolname=current_user "
        "WHERE d.datname=current_database()"
    ).fetchone()
    if (
        row is None
        or row[0] != EXPECTED_DATABASE
        or int(row[1]) != EXPECTED_POSTGRES_MAJOR
        or row[2] != row[3]
        or not bool(row[4])
        or row[5] != LEGACY_OWNER
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer destination identity differs"
        )
    try:
        verify_database_creation_envelope(conn)
    except SyntheticStagingDatabaseError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer destination creation envelope differs"
        ) from exc
    if any(
        _role_flags(conn, role) is not None
        for role in (OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN)
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer destination role state differs"
        )
    try:
        _verify_role_topology(conn, bootstrapped=False)
    except SyntheticStagingDatabaseError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer destination role state differs"
        ) from exc
    legacy_credentials = dict(
        conn.execute(
            "SELECT rolname,rolpassword IS NULL FROM pg_catalog.pg_authid "
            "WHERE rolname=ANY(%s) ORDER BY rolname",
            ([LEGACY_OWNER, LEGACY_LOGIN],),
        ).fetchall()
    )
    if legacy_credentials != {LEGACY_OWNER: True, LEGACY_LOGIN: True}:
        raise SyntheticStagingTransferError(
            "synthetic transfer destination role credential state differs"
        )
    namespaces = {
        str(row[0])
        for row in conn.execute(
            "SELECT nspname FROM pg_catalog.pg_namespace "
            "WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'"
        ).fetchall()
    }
    extensions = {
        str(row[0])
        for row in conn.execute(
            "SELECT extname FROM pg_catalog.pg_extension ORDER BY extname"
        ).fetchall()
    }
    public_objects = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public') OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public')"
    ).fetchone()[0]
    unexpected_catalog = conn.execute(
        "SELECT "
        "EXISTS(SELECT 1 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n "
        "ON n.oid=t.typnamespace WHERE n.nspname='public' AND t.typtype<>'p'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_server),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_user_mapping),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_publication),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_subscription),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_largeobject_metadata),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_default_acl d JOIN pg_catalog.pg_roles r "
        "ON r.oid=d.defaclrole WHERE r.rolname=ANY(%s)),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_db_role_setting s "
        "LEFT JOIN pg_catalog.pg_roles r ON r.oid=s.setrole "
        "WHERE r.rolname=ANY(%s) OR s.setdatabase=(SELECT oid FROM pg_catalog.pg_database "
        "WHERE datname=current_database()))",
        ([LEGACY_OWNER, LEGACY_LOGIN], [LEGACY_OWNER, LEGACY_LOGIN]),
    ).fetchone()
    envelope = conn.execute(
        "SELECT d.datacl::text,pg_catalog.pg_get_userbyid(n.nspowner),n.nspacl::text "
        "FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_namespace n "
        "WHERE d.datname=current_database() AND n.nspname='public'"
    ).fetchone()
    public_connect_elsewhere = conn.execute(
        "SELECT d.datname FROM pg_catalog.pg_database d "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(d.datacl,"
        "pg_catalog.acldefault('d',d.datdba))) acl "
        "WHERE d.datallowconn AND d.datname<>current_database() "
        "AND acl.grantee=0 AND acl.privilege_type='CONNECT' "
        "ORDER BY d.datname LIMIT 1"
    ).fetchone()
    if (
        namespaces != {"public"}
        or extensions != {"plpgsql"}
        or public_objects
        or unexpected_catalog is None
        or any(bool(value) for value in unexpected_catalog)
        or envelope
        != (
            None,
            "pg_database_owner",
            "{pg_database_owner=UC/pg_database_owner,=U/pg_database_owner}",
        )
        or public_connect_elsewhere is not None
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer destination is not empty"
        )


def _destination_pid(conn: Any) -> int:
    row = conn.execute("SELECT pg_catalog.pg_backend_pid()").fetchone()
    if row is None or type(row[0]) is not int or int(row[0]) <= 0:
        raise SyntheticStagingTransferError(
            "synthetic transfer destination session identity differs"
        )
    return int(row[0])


def _require_destination_quiescent(
    conn: Any,
    *,
    allowed_pids: frozenset[int],
    retiring_owned_pids: frozenset[int] = frozenset(),
) -> None:
    if allowed_pids & retiring_owned_pids:
        raise SyntheticStagingTransferError(
            "synthetic transfer destination session authority differs"
        )
    deadline = time.monotonic() + 2.0
    while True:
        # Statistics snapshots are transaction-cached.  Clear before every
        # bounded retry so a just-closed owned backend can actually disappear.
        conn.execute("SELECT pg_catalog.pg_stat_clear_snapshot()")
        observed = frozenset(
            int(row[0])
            for row in conn.execute(
                "SELECT a.pid FROM pg_catalog.pg_stat_activity a "
                "WHERE a.datid=(SELECT oid FROM pg_catalog.pg_database "
                "WHERE datname=pg_catalog.current_database()) "
                "AND a.backend_type='client backend' ORDER BY a.pid"
            ).fetchall()
        )
        prepared = conn.execute(
            "SELECT gid FROM pg_catalog.pg_prepared_xacts "
            "WHERE database=pg_catalog.current_database() ORDER BY gid LIMIT 1"
        ).fetchone()
        if prepared is not None or observed - allowed_pids - retiring_owned_pids:
            raise SyntheticStagingTransferError(
                "synthetic transfer destination is not quiescent"
            )
        if observed == allowed_pids:
            return
        if time.monotonic() >= deadline:
            raise SyntheticStagingTransferError(
                "synthetic transfer destination is not quiescent"
            )
        time.sleep(0.01)


def _fence_destination(conn: Any) -> None:
    conn.execute("SET LOCAL search_path = pg_catalog")
    conn.execute("SET LOCAL lock_timeout = '5s'")
    conn.execute("SET LOCAL statement_timeout = '10s'")
    conn.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
            sql.Identifier(EXPECTED_DATABASE)
        )
    )
    conn.commit()


def _audit_global_envelope(conn: Any, *, stage: str) -> None:
    """Reject source/target globals that scoped catalog hashes cannot see."""

    if stage not in {"source", "final"}:
        raise SyntheticStagingTransferError(
            "synthetic transfer global envelope stage differs"
        )

    try:
        _verify_core_global_privilege_envelope(conn)
        _verify_semantic_catalog_envelope(conn)
    except SyntheticStagingDatabaseError as exc:
        raise SyntheticStagingTransferError(
            f"synthetic transfer {stage} global envelope differs"
        ) from exc
    namespaces = {
        str(row[0])
        for row in conn.execute(
            "SELECT nspname FROM pg_catalog.pg_namespace "
            "WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'"
        ).fetchall()
    }
    extensions = {
        str(row[0])
        for row in conn.execute(
            "SELECT extname FROM pg_catalog.pg_extension ORDER BY extname"
        ).fetchall()
    }
    unexpected = conn.execute(
        "SELECT "
        "EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_type t "
        "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace "
        "WHERE n.nspname='public' AND t.typtype<>'p'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_server),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_user_mapping),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_publication),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_subscription),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_largeobject_metadata),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r "
        "JOIN pg_catalog.pg_class c ON c.oid=r.ev_class "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND r.rulename<>'_RETURN'),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_policy p "
        "JOIN pg_catalog.pg_class c ON c.oid=p.polrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_description d "
        "LEFT JOIN pg_catalog.pg_class c "
        "ON d.classoid='pg_catalog.pg_class'::pg_catalog.regclass "
        "AND d.objoid=c.oid "
        "LEFT JOIN pg_catalog.pg_proc p "
        "ON d.classoid='pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND d.objoid=p.oid "
        "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE cn.nspname=%s OR pn.nspname=%s "
        "OR (d.classoid='pg_catalog.pg_namespace'::pg_catalog.regclass "
        "AND d.objoid=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s))),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_default_acl d "
        "LEFT JOIN pg_catalog.pg_namespace n ON n.oid=d.defaclnamespace "
        "JOIN pg_catalog.pg_roles r ON r.oid=d.defaclrole "
        "WHERE (n.nspname=%s OR n.nspname IS NULL) "
        "AND r.rolname<>ALL(%s)),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_db_role_setting s "
        "LEFT JOIN pg_catalog.pg_roles r ON r.oid=s.setrole "
        "WHERE (s.setdatabase=0 AND r.rolname=ANY(%s)) OR "
        "(s.setdatabase=(SELECT oid FROM pg_catalog.pg_database "
        "WHERE datname=current_database()) AND r.rolname<>ALL(%s)))",
        (
            SCHEMA,
            SCHEMA,
            SCHEMA,
            SCHEMA,
            SCHEMA,
            SCHEMA,
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
            [LEGACY_OWNER, LEGACY_LOGIN, OBJECT_OWNER, PROVISIONER, RUNTIME_LOGIN],
        ),
    ).fetchone()
    if (
        namespaces != {"public", SCHEMA}
        or extensions != {"plpgsql", "pgcrypto"}
        or unexpected is None
        or any(bool(value) for value in unexpected)
    ):
        raise SyntheticStagingTransferError(
            f"synthetic transfer {stage} global envelope differs"
        )


def _stage_verified_dump(artifact: VerifiedTransferArtifact):
    """Copy verified dump bytes to an anonymous inode for race-free restore."""

    source: int | None = None
    staged = None
    try:
        source = os.open(
            artifact.dump_path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(source)
        named = artifact.dump_path.stat(follow_symlinks=False)
        expected = artifact.manifest["dump"]
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
            or before.st_size != expected["bytes"]
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer dump changed before restore"
            )
        staged = tempfile.TemporaryFile(
            mode="w+b", prefix=".verified-transfer-", dir=artifact.root
        )
        digest = hashlib.sha256()
        copied = 0
        while True:
            block = os.read(source, 1024 * 1024)
            if not block:
                break
            staged.write(block)
            digest.update(block)
            copied += len(block)
        after = os.fstat(source)
        named_after = artifact.dump_path.stat(follow_symlinks=False)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or (before.st_dev, before.st_ino)
            != (named_after.st_dev, named_after.st_ino)
            or copied != expected["bytes"]
            or digest.hexdigest() != expected["sha256"]
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer dump changed before restore"
            )
        staged.flush()
        os.fsync(staged.fileno())
        staged.seek(0)
        return staged
    except SyntheticStagingTransferError:
        if staged is not None:
            staged.close()
        raise
    except OSError as exc:
        if staged is not None:
            staged.close()
        raise SyntheticStagingTransferError(
            "synthetic transfer dump is unavailable for restore"
        ) from exc
    finally:
        if source is not None:
            os.close(source)


def _prepare_restore_dump(
    artifact: VerifiedTransferArtifact,
) -> PreparedTransferDump:
    """Stage and fully validate immutable restore inputs before target mutation."""

    staged = None
    use_list = None
    try:
        pg_restore = _postgres_program("pg_restore")
        staged = _stage_verified_dump(artifact)
        descriptor = staged.fileno()
        staged.seek(0)
        toc_sha, toc_entries, toc_semantic_sha, use_list_bytes = (
            _normalized_toc(
                pg_restore,
                Path(f"/proc/self/fd/{descriptor}"),
                pass_fds=(descriptor,),
            )
        )
        if (toc_sha, toc_entries, toc_semantic_sha) != (
            artifact.manifest["dump"]["toc_sha256"],
            artifact.manifest["dump"]["toc_entries"],
            artifact.manifest["dump"]["toc_semantic_sha256"],
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer staged dump table of contents differs"
            )
        use_list = tempfile.TemporaryFile(
            mode="w+b", prefix=".verified-transfer-list-", dir=artifact.root
        )
        use_list.write(use_list_bytes)
        use_list.flush()
        os.fsync(use_list.fileno())
        use_list.seek(0)
        staged.seek(0)
        return PreparedTransferDump(
            pg_restore=pg_restore,
            staged=staged,
            use_list=use_list,
        )
    except SyntheticStagingTransferError:
        if use_list is not None:
            use_list.close()
        if staged is not None:
            staged.close()
        raise
    except (SyntheticStagingBackupError, OSError, subprocess.SubprocessError) as exc:
        if use_list is not None:
            use_list.close()
        if staged is not None:
            staged.close()
        raise SyntheticStagingTransferError(
            "synthetic transfer dump is unavailable for restore"
        ) from exc


def _restore_dump(
    *,
    artifact: VerifiedTransferArtifact,
    admin_url: str,
    prepared: PreparedTransferDump | None = None,
) -> None:
    parsed = urlparse(admin_url)
    if not parsed.username:
        raise SyntheticStagingTransferError(
            "synthetic transfer administrative URL differs"
        )
    _require_plain_url(admin_url, username=parsed.username)
    owned = prepared is None
    selected = prepared or _prepare_restore_dump(artifact)
    try:
        descriptor = selected.staged.fileno()
        list_descriptor = selected.use_list.fileno()
        selected.staged.seek(0)
        selected.use_list.seek(0)
        subprocess.run(
            [
                selected.pg_restore,
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-publications",
                "--no-security-labels",
                "--no-subscriptions",
                "--no-tablespaces",
                "--use-list",
                f"/proc/self/fd/{list_descriptor}",
                "--role",
                LEGACY_OWNER,
                "--dbname",
                admin_url,
                f"/proc/self/fd/{descriptor}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=300,
            env=_dump_environment(selected.pg_restore),
            pass_fds=(descriptor, list_descriptor),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer restore failed"
        ) from exc
    finally:
        if owned:
            selected.close()


def _normalize_restored_owner_acls(admin_url: str) -> int:
    """Restore explicit owner ACL arrays that pg_dump treats as redundant."""

    expected = f"{{{LEGACY_OWNER}=arwdDxt/{LEGACY_OWNER}}}"
    backend_pid: int | None = None
    try:
        with psycopg.connect(admin_url, connect_timeout=5) as conn:
            backend_pid = _destination_pid(conn)
            conn.execute("SET LOCAL search_path = pg_catalog")
            rows = conn.execute(
                "SELECT c.relname,pg_catalog.pg_get_userbyid(c.relowner),"
                "c.relacl::text FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname=ANY(%s) ORDER BY c.relname",
                (SCHEMA, list(_RESTORE_OWNER_ACL_RELATIONS)),
            ).fetchall()
            if tuple(str(row[0]) for row in rows) != _RESTORE_OWNER_ACL_RELATIONS or any(
                (str(owner), acl) != (LEGACY_OWNER, None)
                for _name, owner, acl in rows
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer restored owner ACL state differs"
                )
            conn.execute(
                sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(LEGACY_OWNER))
            )
            for name in _RESTORE_OWNER_ACL_RELATIONS:
                conn.execute(
                    sql.SQL("GRANT ALL PRIVILEGES ON TABLE {}.{} TO {}").format(
                        sql.Identifier(SCHEMA),
                        sql.Identifier(name),
                        sql.Identifier(LEGACY_OWNER),
                    )
                )
            conn.execute("RESET ROLE")
            observed = conn.execute(
                "SELECT c.relname,c.relacl::text FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname=ANY(%s) ORDER BY c.relname",
                (SCHEMA, list(_RESTORE_OWNER_ACL_RELATIONS)),
            ).fetchall()
            if tuple((str(name), str(acl)) for name, acl in observed) != tuple(
                (name, expected) for name in _RESTORE_OWNER_ACL_RELATIONS
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer restored owner ACL state differs"
                )
            conn.commit()
    except SyntheticStagingTransferError:
        raise
    except psycopg.Error as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer restored owner ACL state differs"
        ) from exc
    if backend_pid is None:
        raise SyntheticStagingTransferError(
            "synthetic transfer restored owner ACL session differs"
        )
    return backend_pid


def _reviewed_search_path(value: str) -> bool:
    if not value.startswith("search_path="):
        return False
    tokens = tuple(
        token.strip().strip('"')
        for token in value.removeprefix("search_path=").split(",")
    )
    return tokens in {
        (SCHEMA, "pg_catalog"),
        ("pg_catalog",),
        ("pg_catalog", SCHEMA),
        ("pg_catalog", SCHEMA, "pg_temp"),
        ("pg_catalog", SCHEMA, SCHEMA, "pg_temp"),
    }


def _normalize_restored_function_config(
    admin_url: str, *, source_objects: Mapping[str, Any]
) -> int:
    """Recreate SET FROM CURRENT spellings lost by pg_dump serialization."""

    routines = source_objects.get("routines")
    if not isinstance(routines, list):
        raise SyntheticStagingTransferError(
            "synthetic transfer routine configuration inventory differs"
        )
    wanted: list[tuple[str, str, str]] = []
    for item in routines:
        if not isinstance(item, dict):
            raise SyntheticStagingTransferError(
                "synthetic transfer routine configuration inventory differs"
            )
        config = item.get("config")
        if config is None:
            continue
        if (
            not isinstance(config, list)
            or len(config) != 1
            or not isinstance(config[0], str)
            or not _reviewed_search_path(config[0])
        ):
            raise SyntheticStagingTransferError(
                "synthetic transfer routine configuration inventory differs"
            )
        wanted.append(
            (
                str(item.get("name", "")),
                str(item.get("identity_arguments", "")),
                config[0],
            )
        )
    backend_pid: int | None = None
    try:
        with psycopg.connect(admin_url, connect_timeout=5) as conn:
            backend_pid = _destination_pid(conn)
            conn.execute(
                "SELECT pg_catalog.set_config('search_path',%s,true)",
                ("pg_catalog",),
            )
            current = {
                (str(name), str(arguments)): (
                    None if config is None else [str(value) for value in config],
                    str(target),
                )
                for name, arguments, config, target in conn.execute(
                    "SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
                    "p.proconfig,pg_catalog.format('%%I.%%I(%%s)',n.nspname,p.proname,"
                    "pg_catalog.pg_get_function_identity_arguments(p.oid)) "
                    "FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "LEFT JOIN pg_catalog.pg_depend d "
                    "ON d.classid='pg_proc'::regclass AND d.objid=p.oid "
                    "AND d.deptype='e' WHERE n.nspname=%s AND d.objid IS NULL "
                    "ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)",
                    (SCHEMA,),
                ).fetchall()
            }
            source_keys = {
                (str(item.get("name", "")), str(item.get("identity_arguments", "")))
                for item in routines
            }
            if len(source_keys) != len(routines) or set(current) != source_keys:
                raise SyntheticStagingTransferError(
                    "synthetic transfer routine configuration inventory differs"
                )
            for name, arguments, config in wanted:
                if (name, arguments) not in current:
                    raise SyntheticStagingTransferError(
                        "synthetic transfer routine configuration inventory differs"
                    )
                conn.execute(
                    "SELECT pg_catalog.set_config('search_path',%s,true)",
                    (config.removeprefix("search_path="),),
                )
                conn.execute(
                    sql.SQL("ALTER FUNCTION {} SET search_path FROM CURRENT").format(
                        sql.SQL(current[(name, arguments)][1]),
                    )
                )
                conn.execute(
                    "SELECT pg_catalog.set_config('search_path','pg_catalog',true)"
                )
            conn.execute(
                "SELECT pg_catalog.set_config('search_path',%s,true)",
                ("pg_catalog",),
            )
            observed = {
                (str(name), str(arguments)): (
                    None if config is None else [str(value) for value in config]
                )
                for name, arguments, config in conn.execute(
                    "SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),"
                    "p.proconfig FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "LEFT JOIN pg_catalog.pg_depend d "
                    "ON d.classid='pg_proc'::regclass AND d.objid=p.oid "
                    "AND d.deptype='e' WHERE n.nspname=%s AND d.objid IS NULL",
                    (SCHEMA,),
                ).fetchall()
            }
            expected = {
                (str(item["name"]), str(item["identity_arguments"])): item["config"]
                for item in routines
            }
            if observed != expected:
                raise SyntheticStagingTransferError(
                    "synthetic transfer routine configuration inventory differs"
                )
            conn.commit()
    except SyntheticStagingTransferError:
        raise
    except psycopg.Error as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer routine configuration inventory differs"
        ) from exc
    if backend_pid is None:
        raise SyntheticStagingTransferError(
            "synthetic transfer routine configuration session differs"
        )
    return backend_pid


def _endpoint(value: str) -> tuple[str, int, str]:
    parsed = urlparse(value)
    if not parsed.hostname:
        raise SyntheticStagingTransferError(
            "synthetic transfer database endpoint differs"
        )
    return parsed.hostname, parsed.port or 5432, parsed.path


def _require_one_endpoint(
    *, target: SyntheticStagingTarget, urls: tuple[str, ...]
) -> None:
    expected = _endpoint(target.database_url)
    if any(_endpoint(value) != expected for value in urls):
        raise SyntheticStagingTransferError(
            "synthetic transfer database endpoint differs"
        )


def _maintenance_admin_url(
    value: str, *, target: SyntheticStagingTarget
) -> tuple[str, str]:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer maintenance endpoint differs"
        ) from exc
    target_host, target_port, _target_path = _endpoint(target.database_url)
    maintenance_database = parsed.path.removeprefix("/")
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.username
        or parsed.password is not None
        or parsed.hostname != target_host
        or (port or 5432) != target_port
        or maintenance_database in {"", EXPECTED_DATABASE, "template0", "template1"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SyntheticStagingTransferError(
            "synthetic transfer maintenance endpoint differs"
        )
    target_admin_url = parsed._replace(
        path=f"/{EXPECTED_DATABASE}", params="", query="", fragment=""
    ).geturl()
    return maintenance_database, target_admin_url


def prepare_transfer_destination(
    *,
    artifact_root: Path,
    expected_manifest_sha256: str,
    maintenance_admin_url: str,
    target: SyntheticStagingTarget,
    source_root: Path,
    expected_source_commit: str,
    expected_source_tree: str,
) -> dict[str, Any]:
    """Create the one accepted empty target scaffold without reset fallback."""

    try:
        executing_source = _source_identity(source_root)
    except SyntheticStagingBackupError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer executing source identity differs"
        ) from exc
    if executing_source != {
        "commit": expected_source_commit,
        "tree": expected_source_tree,
    }:
        raise SyntheticStagingTransferError(
            "synthetic transfer executing source identity differs"
        )
    artifact = verify_transfer_artifact(
        artifact_root=artifact_root,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_source_commit=expected_source_commit,
        expected_source_tree=expected_source_tree,
    )
    target.validate_static()
    if target.transfer_manifest_sha256 != expected_manifest_sha256:
        raise SyntheticStagingTransferError(
            "synthetic transfer target manifest identity differs"
        )
    maintenance_database, target_admin_url = _maintenance_admin_url(
        maintenance_admin_url, target=target
    )
    source_system_identifier = artifact.manifest["source"]["database"][
        "system_identifier"
    ]
    mutated = False
    try:
        with psycopg.connect(
            maintenance_admin_url, connect_timeout=5
        ) as maintenance:
            maintenance.execute("SET LOCAL search_path = pg_catalog")
            _verify_core_global_privilege_envelope(maintenance)
            identity = maintenance.execute(
                "SELECT pg_catalog.current_database(),"
                "pg_catalog.current_setting('server_version_num')::int/10000,"
                "session_user::pg_catalog.text,current_user::pg_catalog.text,"
                "r.rolsuper,pg_catalog.current_setting('TimeZone'),"
                "c.system_identifier::text "
                "FROM pg_catalog.pg_authid r "
                "CROSS JOIN pg_catalog.pg_control_system() c "
                "WHERE r.rolname=current_user"
            ).fetchone()
            if (
                identity is None
                or identity[0] != maintenance_database
                or int(identity[1]) != EXPECTED_POSTGRES_MAJOR
                or identity[2] != identity[3]
                or not bool(identity[4])
                or identity[5] != "UTC"
                or str(identity[6]) == source_system_identifier
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer maintenance identity differs"
                )
            databases = {
                str(row[0]): (bool(row[1]), bool(row[2]))
                for row in maintenance.execute(
                    "SELECT datname,datallowconn,datistemplate "
                    "FROM pg_catalog.pg_database ORDER BY datname"
                ).fetchall()
            }
            expected_databases = {
                "postgres",
                "template0",
                "template1",
                maintenance_database,
            }
            if (
                set(databases) != expected_databases
                or databases.get("template0") != (False, True)
                or databases.get("template1") != (True, True)
                or databases.get(maintenance_database) != (True, False)
                or EXPECTED_DATABASE in databases
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer maintenance database inventory differs"
                )
            roles = {
                str(row[0])
                for row in maintenance.execute(
                    "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)",
                    (
                        [
                            LEGACY_OWNER,
                            LEGACY_LOGIN,
                            OBJECT_OWNER,
                            PROVISIONER,
                            RUNTIME_LOGIN,
                        ],
                    ),
                ).fetchall()
            }
            if roles:
                raise SyntheticStagingTransferError(
                    "synthetic transfer maintenance role inventory differs"
                )
            own_pid = _destination_pid(maintenance)
            sessions = {
                int(row[0])
                for row in maintenance.execute(
                    "SELECT pid FROM pg_catalog.pg_stat_activity "
                    "WHERE backend_type='client backend' ORDER BY pid"
                ).fetchall()
            }
            prepared = maintenance.execute(
                "SELECT gid FROM pg_catalog.pg_prepared_xacts ORDER BY gid LIMIT 1"
            ).fetchone()
            if sessions != {own_pid} or prepared is not None:
                raise SyntheticStagingTransferError(
                    "synthetic transfer maintenance service is not quiescent"
                )
            mutated = True
            maintenance.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(LEGACY_OWNER))
            )
            maintenance.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(LEGACY_LOGIN))
            )
            maintenance.execute(
                sql.SQL(
                    "GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
                ).format(
                    sql.Identifier(LEGACY_OWNER), sql.Identifier(LEGACY_LOGIN)
                )
            )
            for database in sorted(databases):
                maintenance.execute(
                    sql.SQL(
                        "REVOKE CONNECT,TEMPORARY ON DATABASE {} FROM PUBLIC"
                    ).format(sql.Identifier(database))
                )
            maintenance.commit()
        with psycopg.connect(
            maintenance_admin_url, connect_timeout=5, autocommit=True
        ) as maintenance:
            maintenance.execute(
                sql.SQL(
                    "CREATE DATABASE {} WITH OWNER={} TEMPLATE=template0 "
                    "ENCODING='UTF8' LOCALE_PROVIDER=libc LC_COLLATE='C' "
                    "LC_CTYPE='C' TABLESPACE=pg_default CONNECTION LIMIT=-1 "
                    "IS_TEMPLATE=false ALLOW_CONNECTIONS=true"
                ).format(
                    sql.Identifier(EXPECTED_DATABASE),
                    sql.Identifier(LEGACY_OWNER),
                )
            )
        with psycopg.connect(target_admin_url, connect_timeout=5) as target_admin:
            _preflight_empty_destination(
                target_admin,
                source_system_identifier=source_system_identifier,
            )
            target_identity = target_admin.execute(
                "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
            ).fetchone()[0]
            target_admin.rollback()
    except SyntheticStagingTransferError as exc:
        if mutated:
            raise SyntheticStagingTransferError(
                "synthetic transfer destination preparation is partial and "
                "the service must be discarded"
            ) from exc
        raise
    except (OSError, psycopg.Error) as exc:
        if mutated:
            raise SyntheticStagingTransferError(
                "synthetic transfer destination preparation is partial and "
                "the service must be discarded"
            ) from exc
        raise SyntheticStagingTransferError(
            "synthetic transfer destination preparation failed"
        ) from exc
    return {
        "contract": TRANSFER_CONTRACT,
        "database": EXPECTED_DATABASE,
        "manifest_sha256": expected_manifest_sha256,
        "system_identifier": str(target_identity),
    }


def restore_and_transition(
    *,
    artifact_root: Path,
    expected_manifest_sha256: str,
    admin_url: str,
    provisioner_url: str,
    runtime_url: str,
    provisioner_secret: str,
    runtime_secret: str,
    target: SyntheticStagingTarget,
    source_root: Path,
    expected_source_commit: str,
    expected_source_tree: str,
) -> dict[str, Any]:
    """Restore exactly once, apply 017, and return sanitized proof identities."""

    try:
        executing_source = _source_identity(source_root)
    except SyntheticStagingBackupError as exc:
        raise SyntheticStagingTransferError(
            "synthetic transfer executing source identity differs"
        ) from exc
    if executing_source != {
        "commit": expected_source_commit,
        "tree": expected_source_tree,
    }:
        raise SyntheticStagingTransferError(
            "synthetic transfer executing source identity differs"
        )
    artifact = verify_transfer_artifact(
        artifact_root=artifact_root,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_source_commit=expected_source_commit,
        expected_source_tree=expected_source_tree,
    )
    _require_plain_url(provisioner_url, username=PROVISIONER)
    _require_plain_url(runtime_url, username=RUNTIME_LOGIN)
    _validated_role_secret(provisioner_secret, label="provisioner")
    _validated_role_secret(runtime_secret, label="runtime")
    if hmac.compare_digest(provisioner_secret, runtime_secret):
        raise SyntheticStagingTransferError(
            "synthetic transfer role credentials must be distinct"
        )
    admin_username = urlparse(admin_url).username
    if not admin_username:
        raise SyntheticStagingTransferError(
            "synthetic transfer administrative URL differs"
        )
    _require_plain_url(admin_url, username=admin_username)
    if runtime_url != target.database_url:
        raise SyntheticStagingTransferError(
            "synthetic transfer runtime target differs"
        )
    if target.transfer_manifest_sha256 != expected_manifest_sha256:
        raise SyntheticStagingTransferError(
            "synthetic transfer target manifest identity differs"
        )
    target.validate_static()
    _require_one_endpoint(
        target=target,
        urls=(admin_url, provisioner_url, runtime_url),
    )
    # Every byte and selected TOC record that pg_restore will consume is now
    # held on anonymous, fsync'd descriptors before the destination is even
    # observed.  A local artifact defect therefore cannot strand a fenced DB.
    prepared = _prepare_restore_dump(artifact)
    destination_mutated = False
    try:
        with psycopg.connect(admin_url, connect_timeout=5) as admin_conn:
            _preflight_empty_destination(
                admin_conn,
                source_system_identifier=artifact.manifest["source"]["database"][
                    "system_identifier"
                ],
            )
            admin_pid = _destination_pid(admin_conn)
            admin_conn.rollback()
            with psycopg.connect(admin_url, connect_timeout=5) as initializer_conn:
                initializer_conn.execute(
                    sql.SQL("SET SESSION AUTHORIZATION {}").format(
                        sql.Identifier(LEGACY_LOGIN)
                    )
                )
                initializer_conn.execute(
                    sql.SQL("SET ROLE {}").format(sql.Identifier(LEGACY_OWNER))
                )
                initializer_conn.commit()
                if initializer_conn.execute(
                    "SELECT session_user::text,current_user::text"
                ).fetchone() != (LEGACY_LOGIN, LEGACY_OWNER):
                    raise SyntheticStagingTransferError(
                        "synthetic transfer initializer identity differs"
                    )
                initializer_pid = _destination_pid(initializer_conn)
                initializer_conn.rollback()
                allowed = frozenset({admin_pid, initializer_pid})
                _require_destination_quiescent(
                    admin_conn, allowed_pids=allowed
                )
                admin_conn.rollback()
                # From the first durable fence attempt onward, a lost server
                # response is an ambiguous mutation and the target is never
                # presented as safely retryable.
                destination_mutated = True
                _fence_destination(admin_conn)
                _require_destination_quiescent(
                    admin_conn, allowed_pids=allowed
                )
                admin_conn.rollback()
                _restore_dump(
                    artifact=artifact,
                    admin_url=admin_url,
                    prepared=prepared,
                )
                _require_destination_quiescent(
                    admin_conn, allowed_pids=allowed
                )
                admin_conn.rollback()
                owner_acl_pid = _normalize_restored_owner_acls(admin_url)
                _require_destination_quiescent(
                    admin_conn,
                    allowed_pids=allowed,
                    retiring_owned_pids=frozenset({owner_acl_pid}),
                )
                admin_conn.rollback()
                function_config_pid = _normalize_restored_function_config(
                    admin_url,
                    source_objects=artifact.manifest["source"]["objects"],
                )
                _require_destination_quiescent(
                    admin_conn,
                    allowed_pids=allowed,
                    retiring_owned_pids=frozenset({function_config_pid}),
                )
                admin_conn.rollback()
                restored = _snapshot_source_connection(
                    initializer_conn, database_acl="fenced"
                )
                expected_restored = {
                    key: value
                    for key, value in artifact.manifest["source"].items()
                    if key != "git"
                }
                restored_database = dict(restored["database"])
                source_database = dict(expected_restored["database"])
                restored_system_identifier = restored_database.pop(
                    "system_identifier"
                )
                source_system_identifier = source_database.pop(
                    "system_identifier"
                )
                restored["database"] = restored_database
                expected_restored["database"] = source_database
                if (
                    restored_system_identifier == source_system_identifier
                    or restored != expected_restored
                ):
                    raise SyntheticStagingTransferError(
                        "synthetic transfer restored state differs"
                    )
            _require_destination_quiescent(
                admin_conn,
                allowed_pids=frozenset({admin_pid}),
                retiring_owned_pids=frozenset({initializer_pid}),
            )
            admin_conn.rollback()
            if not install_transfer_provenance(
                admin_conn, expected_manifest_sha256
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer provenance did not transition"
                )
            admin_conn.commit()
            if not bootstrap_roles(
                admin_conn,
                transfer_manifest_sha256=expected_manifest_sha256,
                provisioner_secret=provisioner_secret,
                runtime_secret=runtime_secret,
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer role bootstrap did not transition"
                )
            admin_conn.commit()
        with psycopg.connect(
            provisioner_url, password=provisioner_secret, connect_timeout=5
        ) as conn:
            if not provision_contract(
                conn,
                transfer_manifest_sha256=expected_manifest_sha256,
            ):
                raise SyntheticStagingTransferError(
                    "synthetic transfer database contract did not transition"
                )
            conn.commit()
        with psycopg.connect(
            provisioner_url, password=provisioner_secret, connect_timeout=5
        ) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            provisioner_identity = attest_provisioner_connection(conn, target)
            conn.rollback()
        with psycopg.connect(
            runtime_url, password=runtime_secret, connect_timeout=5
        ) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            runtime_identity = attest_runtime_connection(conn, target)
            conn.rollback()
        if runtime_identity != EXPECTED_RUNTIME_ATTESTATION_IDENTITY:
            raise SyntheticStagingTransferError(
                "synthetic transfer runtime attestation differs"
            )
        with psycopg.connect(admin_url, connect_timeout=5) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            _audit_global_envelope(conn, stage="final")
            conn.rollback()
    except SyntheticStagingTransferError as exc:
        if destination_mutated:
            raise SyntheticStagingTransferError(
                "synthetic transfer destination is partial and must be discarded"
            ) from exc
        raise
    except (SyntheticStagingDatabaseError, psycopg.Error) as exc:
        message = (
            "synthetic transfer destination is partial and must be discarded"
            if destination_mutated
            else "synthetic transfer destination verification failed"
        )
        raise SyntheticStagingTransferError(message) from exc
    finally:
        prepared.close()
    return {
        "contract": TRANSFER_CONTRACT,
        "manifest_sha256": artifact.manifest_sha256,
        "fixture_manifest_sha256": IMMUTABLE_FIXTURE_MANIFEST_SHA256,
        "successor_catalog_sha256": SUCCESSOR_CATALOG_SHA256,
        "staging_release_sha256": STAGING_BACKUP_RELEASE_SHA256,
        "runtime_attestation_identity": runtime_identity,
        "provisioner_attestation_identity": provisioner_identity,
        "administrative_credential_removal": "EXTERNAL_REQUIRED",
    }

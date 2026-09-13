#!/usr/bin/env python3
"""Start, back up, and restore the owned local purchasing candidate.

This tool is intentionally restricted to loopback PostgreSQL 16 databases
whose names end in ``_demo``.  It never uses Replit, Shopify, a public bind, or
an ambient database URL.  Secret values are read from owned mode-0600 files
and are passed only to the supervised local process.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

import psycopg
from psycopg import sql


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "qa_mapping_test"
DEMO_CONTRACT = "BUFFALO_SYNTHETIC_OWNER_DEMO_V1"
MAPPING_CONTRACT = "v1-shadow-only"
MAPPING_MIGRATION_MARKER = (
    "sha256:80c5d6c0a0299edf8d04f9c5f684f9cea277494b894fa0feb0a387476bfa8c86"
)
MAPPING_CATALOG_SHA256 = (
    "5a9fff00c1d62c2de89ca1dc27d4e264def12eb726ab249a9ab86a9fc529c72e"
)
PID_FILE = "candidate.pid"
SECRET_FILES = {
    "BUFFALO_LOCAL_AUTH_SECRET_FILE": "local-auth.secret",
    "RECONCILIATION_REVIEW_TOKEN": "review-token.secret",
    "PRICE_BOOK_REVIEW_TOKEN": "price-token.secret",
}
SCRUBBED_NAMES = {
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "SHOPIFY_SHOP",
    "SHOPIFY_STORE",
    "SHOPIFY_STORE_DOMAIN",
    "SHOPIFY_SHOP_DOMAIN",
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "SHOPIFY_API_KEY",
    "SHOPIFY_API_SECRET",
    "SHOPIFY_ACCESS_TOKEN",
    "SHOPIFY_ADMIN_ACCESS_TOKEN",
    "PHASE4_REVIEW_TOKEN_INPUT",
}
_DISABLED_MAPPING_POLICY = {
    "contract_version": MAPPING_CONTRACT,
    "routine_selection_scope": "ROUTINE_PROCUREMENT_STANDARD",
    "review_intake_writes_enabled": False,
    "human_mapping_writes_enabled": False,
    "policy_mapping_writes_enabled": False,
    "routine_selection_writes_enabled": False,
    "selected_offer_shadow_reads_enabled": False,
    "recommendation_cutover_enabled": False,
    "offer_activation_enabled": False,
}
_MAPPING_RELATIONS = (
    "supplier_mapping_review_batches",
    "supplier_mapping_review_candidates",
    "supplier_mapping_decisions",
    "supplier_offer_selection_events",
    "supplier_offer_selection_heads",
    "v_effective_supplier_mapping_decisions",
    "v_supplier_offer_selection_diagnostics",
    "v_selected_standard_supplier_offers",
    "v_supplier_offer_selection_shadow",
)
_DATABASE_LIFECYCLE_LOCK_PREFIX = "buffalo:local-purchasing-candidate:lifecycle:v1"


class CandidateBoundaryError(RuntimeError):
    pass


def acquire_database_lifecycle_lock(conn: Any, database: str) -> str:
    """Acquire the cooperative lifecycle lock for one exact owned database."""

    lock_name = f"{_DATABASE_LIFECYCLE_LOCK_PREFIX}:{database}"
    locked = conn.execute(
        "SELECT pg_catalog.pg_try_advisory_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (lock_name,),
    ).fetchone()[0]
    if not locked:
        raise CandidateBoundaryError(
            "another local purchasing lifecycle operation is active for this database"
        )
    return lock_name


@contextmanager
def _database_lifecycle_guard(database_url: str, *, restore: bool = False):
    """Hold a dedicated PostgreSQL session lock for one whole lifecycle action."""

    database = _database_name(database_url, restore=restore)
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT pg_catalog.current_database(),"
            "pg_catalog.current_setting('server_version_num')::integer,"
            "pg_catalog.inet_server_addr()::text,session_user::text,current_user::text,"
            "pg_catalog.pg_get_userbyid(d.datdba) "
            "FROM pg_catalog.pg_database d "
            "WHERE d.datname=pg_catalog.current_database()"
        ).fetchone()
        if row is None:
            raise CandidateBoundaryError("database lifecycle identity is absent")
        try:
            server_address = ipaddress.ip_interface(str(row[2])).ip
        except ValueError as exc:
            raise CandidateBoundaryError(
                "database lifecycle server address is malformed"
            ) from exc
        if (
            str(row[0]) != database
            or int(row[1]) // 10000 != 16
            or not server_address.is_loopback
            or (str(row[3]), str(row[4]), str(row[5]))
            != ("qa_release_login", "qa_mapping_owner", "qa_mapping_owner")
        ):
            raise CandidateBoundaryError(
                "database lifecycle identity is outside the owned demo contract"
            )
        acquire_database_lifecycle_lock(conn, database)
        yield


def _require_mode(path: Path, mode: int, *, directory: bool) -> None:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CandidateBoundaryError(f"required path is unavailable: {path}") from exc
    expected_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if path.is_symlink() or not expected_kind or stat.S_IMODE(info.st_mode) != mode:
        kind = "directory" if directory else "file"
        raise CandidateBoundaryError(f"{kind} must be owned mode {mode:04o}: {path}")
    if info.st_uid != os.getuid():
        raise CandidateBoundaryError(f"path ownership differs: {path}")


def _database_name(database_url: str, *, restore: bool = False) -> str:
    try:
        parsed = urlparse(database_url)
        parsed_port = parsed.port
        query = parse_qs(
            parsed.query, strict_parsing=True, keep_blank_values=True
        )
    except ValueError as exc:
        raise CandidateBoundaryError("database URL is malformed") from exc
    if parsed.scheme not in {"postgresql", "postgres"} or parsed.hostname not in {
        "127.0.0.1",
        "::1",
    }:
        raise CandidateBoundaryError("database URL must use an exact loopback PostgreSQL host")
    if parsed_port is None or parsed.password or parsed.fragment or parsed.params:
        raise CandidateBoundaryError("database URL must not embed credentials or fragments")
    if parsed.username != "qa_release_login":
        raise CandidateBoundaryError("database session role must be qa_release_login")
    database = parsed.path.removeprefix("/")
    suffix = "_restore_demo" if restore else "_demo"
    if (
        not database
        or not database.endswith(suffix)
        or re.fullmatch(r"[a-z][a-z0-9_]*", database) is None
    ):
        raise CandidateBoundaryError(f"database name must end in {suffix}")
    if query != {"options": ["-c role=qa_mapping_owner"]}:
        raise CandidateBoundaryError("database URL must select the reviewed maintenance role")
    return database


def _application_database_url(database_url: str) -> str:
    """Bind the synthetic app process to its exact owned schema.

    The input URL has already passed ``_database_name``.  Only the child app
    receives this extra search-path setting; lifecycle checks, dump/restore,
    and external callers continue to use the original exact role-pair URL.
    """

    _database_name(database_url)
    parsed = urlparse(database_url)
    options = "-c role=qa_mapping_owner -c search_path=qa_mapping_test,pg_catalog"
    return parsed._replace(
        query=urlencode({"options": options}, quote_via=quote)
    ).geturl()


def _database_facts(
    database_url: str, *, require_initialized: bool, restore: bool = False
) -> dict[str, Any]:
    database = _database_name(database_url, restore=restore)
    rules_path = REPO_ROOT / "procurement" / "config" / "rules.toml"
    migration_path = REPO_ROOT / "procurement" / "db" / (
        "014_persistent_mapping_foundation.sql"
    )
    rules = tomllib.loads(rules_path.read_text(encoding="utf-8"))
    if rules.get("persistent_mapping") != _DISABLED_MAPPING_POLICY:
        raise CandidateBoundaryError("persistent mapping policy is not disabled")
    if _sha256_file(migration_path) != MAPPING_MIGRATION_MARKER.removeprefix(
        "sha256:"
    ):
        raise CandidateBoundaryError("persistent mapping migration source differs")
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT pg_catalog.current_database(),"
            "pg_catalog.current_setting('server_version_num')::integer,"
            "pg_catalog.inet_server_addr()::text,session_user::text,current_user::text,"
            "pg_catalog.pg_get_userbyid(d.datdba) "
            "FROM pg_catalog.pg_database d "
            "WHERE d.datname=pg_catalog.current_database()"
        ).fetchone()
        assert row is not None
        try:
            server_address = ipaddress.ip_interface(str(row[2])).ip
        except ValueError as exc:
            raise CandidateBoundaryError("database server address is malformed") from exc
        if (
            str(row[0]) != database
            or int(row[1]) // 10000 != 16
            or not server_address.is_loopback
            or (str(row[3]), str(row[4]))
            != ("qa_release_login", "qa_mapping_owner")
            or str(row[5]) != "qa_mapping_owner"
        ):
            raise CandidateBoundaryError("database identity is outside the owned demo contract")
        schema_oid = conn.execute(
            "SELECT pg_catalog.to_regnamespace(%s)", (SCHEMA,)
        ).fetchone()[0]
        user_schemas = tuple(
            row[0]
            for row in conn.execute(
                "SELECT nspname FROM pg_catalog.pg_namespace "
                "WHERE nspname <> 'public' AND nspname <> 'information_schema' "
                "AND nspname !~ '^pg_' ORDER BY nspname"
            ).fetchall()
        )
        extensions = tuple(
            row[0]
            for row in conn.execute(
                "SELECT extname FROM pg_catalog.pg_extension ORDER BY extname"
            ).fetchall()
        )
        public_objects = conn.execute(
            "SELECT "
            "(SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname='public'),"
            "(SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public'),"
            "(SELECT count(*) FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n "
            "ON n.oid=t.typnamespace WHERE n.nspname='public')"
        ).fetchone()
        if tuple(int(value) for value in public_objects) != (0, 0, 0):
            raise CandidateBoundaryError("public schema contains unowned demo objects")
        if require_initialized:
            if schema_oid is None or user_schemas != (SCHEMA,) or extensions != (
                "pgcrypto",
                "plpgsql",
            ):
                raise CandidateBoundaryError("demo schema is absent")
            metadata = dict(
                conn.execute(
                    sql.SQL(
                        "SELECT key,value FROM {}.meta WHERE key=ANY(%s)"
                    ).format(sql.Identifier(SCHEMA)),
                    (
                        [
                            "synthetic_owner_demo_contract",
                            "persistent_mapping_foundation_contract",
                            "persistent_mapping_foundation_catalog_sha256",
                            "migration:014_persistent_mapping_foundation.sql",
                        ],
                    ),
                ).fetchall()
            )
            if (
                metadata.get("synthetic_owner_demo_contract") != DEMO_CONTRACT
                or metadata.get("persistent_mapping_foundation_contract")
                != MAPPING_CONTRACT
                or metadata.get("persistent_mapping_foundation_catalog_sha256")
                != MAPPING_CATALOG_SHA256
                or metadata.get("migration:014_persistent_mapping_foundation.sql")
                != MAPPING_MIGRATION_MARKER
            ):
                raise CandidateBoundaryError("demo database contract metadata differs")
            target = sql.Identifier(SCHEMA)
            computed = conn.execute(
                sql.SQL("SELECT {}.compute_persistent_mapping_catalog_sha256()").format(
                    target
                )
            ).fetchone()[0]
            if computed != MAPPING_CATALOG_SHA256:
                raise CandidateBoundaryError("demo database catalog signature differs")
            conn.execute(
                sql.SQL("SELECT {}.assert_persistent_mapping_foundation_contract()").format(
                    target
                )
            )
        elif (
            schema_oid is not None
            or user_schemas
            or extensions != ("plpgsql",)
        ):
            raise CandidateBoundaryError("restore target is not empty")
        return {
            "database": database,
            "postgres_major": int(row[1]) // 10000,
            "server_address": str(server_address),
            "session_user": str(row[3]),
            "current_user": str(row[4]),
            "database_owner": str(row[5]),
        }


def _runtime_paths(runtime_root: Path) -> tuple[Path, Path, Path]:
    if not runtime_root.is_absolute():
        raise CandidateBoundaryError("runtime root must be absolute")
    _require_mode(runtime_root, 0o700, directory=True)
    storage = runtime_root / "storage"
    backups = runtime_root / "backups"
    for directory in (storage, backups):
        _require_mode(directory, 0o700, directory=True)
    return storage, backups, runtime_root / PID_FILE


def _secret_values(runtime_root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    secret_digests: set[str] = set()
    for environment_name, filename in SECRET_FILES.items():
        path = runtime_root / filename
        _require_mode(path, 0o600, directory=False)
        raw = path.read_bytes()
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        if len(raw) < 24 or len(raw) > 512 or b"\x00" in raw:
            raise CandidateBoundaryError(f"secret file shape differs: {filename}")
        digest = hashlib.sha256(raw).hexdigest()
        if digest in secret_digests:
            raise CandidateBoundaryError("local candidate secrets must be distinct")
        secret_digests.add(digest)
        if environment_name.endswith("_FILE"):
            values[environment_name] = str(path)
        else:
            values[environment_name] = raw.decode("utf-8")
    return values


def _reserve_pid_file(pid_file: Path) -> int:
    """Exclusively reserve lifecycle ownership before a child can exist."""

    for _ in range(2):
        try:
            return os.open(pid_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            before = pid_file.stat(follow_symlinks=False)
            try:
                raw = pid_file.read_text(encoding="ascii").strip()
                pid = int(raw)
            except (OSError, UnicodeError, ValueError) as exc:
                raise CandidateBoundaryError(
                    "candidate lifecycle is already reserved or malformed"
                ) from exc
            if not raw or pid <= 0:
                raise CandidateBoundaryError("candidate lifecycle is already reserved")
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                after = pid_file.stat(follow_symlinks=False)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise CandidateBoundaryError("candidate lifecycle changed during inspection")
                pid_file.unlink()
                continue
            except PermissionError as exc:
                raise CandidateBoundaryError("candidate PID cannot be inspected") from exc
            raise CandidateBoundaryError("a local candidate process is already running")
    raise CandidateBoundaryError("candidate lifecycle could not be reserved")


def _reservation_identity(descriptor: int) -> tuple[int, int]:
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


def _release_reserved_pid_path(pid_file: Path, identity: tuple[int, int]) -> None:
    try:
        current = pid_file.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise CandidateBoundaryError("candidate lifecycle reservation disappeared") from exc
    if pid_file.is_symlink() or (current.st_dev, current.st_ino) != identity:
        raise CandidateBoundaryError("candidate lifecycle reservation was replaced")
    pid_file.unlink()


def _write_reserved_pid(descriptor: int, pid: int) -> None:
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(f"{pid}\n")
        handle.flush()
        os.fsync(handle.fileno())


def _child_environment(
    *, database_url: str, runtime_root: Path, storage: Path, port: int
) -> dict[str, str]:
    secrets = _secret_values(runtime_root)
    environment = {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(REPO_ROOT / "procurement" / "src"),
        "DATABASE_URL": _application_database_url(database_url),
        "PROCUREMENT_STORAGE_BACKEND": "local",
        "PROCUREMENT_STORAGE_ROOT": str(storage),
        "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
        "BUFFALO_LOCAL_PORT": str(port),
        "BUFFALO_LOCAL_PRINCIPAL_REF": "synthetic:owner-browser:01",
        "BUFFALO_LOCAL_ROLE_REF": "LOCAL_SYNTHETIC_OWNER",
        "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        **secrets,
    }
    if SCRUBBED_NAMES.intersection(environment) != {"DATABASE_URL"}:
        raise CandidateBoundaryError("child environment credential allowlist differs")
    return environment


def _wait_for_health(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + 30
    request = Request(
        f"http://127.0.0.1:{port}/health",
        headers={"Host": f"127.0.0.1:{port}"},
    )
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CandidateBoundaryError(
                f"local candidate exited before readiness ({process.returncode})"
            )
        try:
            with urlopen(request, timeout=1) as response:
                payload = json.loads(response.read(4096))
                if (
                    response.status == 200
                    and payload.get("service") == "buffalo-procurement-os"
                    and int(payload.get("process_id", -1)) == process.pid
                ):
                    return
        except (URLError, TimeoutError, OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise CandidateBoundaryError("local candidate did not become healthy")


def _terminate_owned_process_group(
    process: subprocess.Popen[bytes], *, timeout: float = 10
) -> None:
    """Terminate a child launched in its own session, including descendants."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            if process.poll() is None:
                process.kill()
        process.wait(timeout=timeout)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    os.killpg(process.pid, signal.SIGKILL)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise CandidateBoundaryError("owned child process group survived termination")


def serve(database_url: str, runtime_root: Path, port: int) -> int:
    if port < 1024 or port > 65535:
        raise CandidateBoundaryError("local port must be between 1024 and 65535")
    with _database_lifecycle_guard(database_url):
        return _serve_locked(database_url, runtime_root, port)


def _serve_locked(database_url: str, runtime_root: Path, port: int) -> int:
    _database_facts(database_url, require_initialized=True)
    storage, _, pid_file = _runtime_paths(runtime_root)
    reservation = _reserve_pid_file(pid_file)
    reservation_identity = _reservation_identity(reservation)
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(
            (
                sys.executable,
                "-m",
                "uvicorn",
                "procurement_os.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--no-proxy-headers",
            ),
            cwd=REPO_ROOT,
            env=_child_environment(
                database_url=database_url,
                runtime_root=runtime_root,
                storage=storage,
                port=port,
            ),
            start_new_session=True,
        )
        _write_reserved_pid(reservation, child.pid)
    except BaseException:
        try:
            os.close(reservation)
        except OSError:
            pass
        if child is not None and child.poll() is None:
            _terminate_owned_process_group(child)
        _release_reserved_pid_path(pid_file, reservation_identity)
        raise
    assert child is not None

    stop_requested = False

    def relay(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        if child.poll() is None:
            child.send_signal(signum)

    old_handlers = {
        signum: signal.signal(signum, relay)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        _wait_for_health(child, port)
        print(f"LOCAL_SYNTHETIC candidate ready at http://127.0.0.1:{port}/", flush=True)
        return_code = child.wait()
        if stop_requested and return_code in {0, -signal.SIGINT, -signal.SIGTERM}:
            return 0
        return return_code
    finally:
        if child.poll() is None:
            _terminate_owned_process_group(child)
        _release_reserved_pid_path(pid_file, reservation_identity)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _storage_inventory(storage: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in sorted(storage.rglob("*")):
        if path.is_symlink():
            raise CandidateBoundaryError("storage contains a symlink")
        if path.is_file():
            relative = path.relative_to(storage)
            stat_result = path.stat()
            inventory.append(
                {
                    "path": relative.as_posix(),
                    "sha256": _sha256_file(path),
                    "bytes": stat_result.st_size,
                }
            )
    return inventory


def _postgres_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise CandidateBoundaryError(f"{name} is unavailable")
    completed = subprocess.run(
        [path, "--version"],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.path.dirname(path), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        timeout=10,
    )
    if not re.search(r"\(PostgreSQL\) 16(?:\.|\s|$)", completed.stdout):
        raise CandidateBoundaryError(f"{name} is not PostgreSQL 16")
    return path


def _state_evidence(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        target = sql.Identifier(SCHEMA)
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
        relation_names = [
            str(item[0])
            for item in conn.execute(
                "SELECT c.relname FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relkind IN ('r','p') "
                "ORDER BY c.relname",
                (SCHEMA,),
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
                    ).format(
                        sql.Identifier(SCHEMA), sql.Identifier(relation_name)
                    )
                ).fetchall()
            ]
            relation_inventory.append(
                {
                    "relation": relation_name,
                    "row_count": len(payloads),
                    "sha256": hashlib.sha256(
                        "\n".join(payloads).encode("utf-8")
                    ).hexdigest(),
                }
            )
        sequence_names = [
            str(item[0])
            for item in conn.execute(
                "SELECT c.relname FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relkind='S' ORDER BY c.relname",
                (SCHEMA,),
            ).fetchall()
        ]
        sequence_inventory = []
        for sequence_name in sequence_names:
            sequence_value = conn.execute(
                sql.SQL("SELECT last_value,is_called FROM {}.{}").format(
                    sql.Identifier(SCHEMA), sql.Identifier(sequence_name)
                )
            ).fetchone()
            sequence_inventory.append(
                {
                    "sequence": sequence_name,
                    "last_value": int(sequence_value[0]),
                    "is_called": bool(sequence_value[1]),
                }
            )
    assert row is not None
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
    evidence = dict(zip(names, row, strict=True))
    evidence["relation_inventory"] = relation_inventory
    evidence["sequence_inventory"] = sequence_inventory
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    return {"facts": evidence, "sha256": hashlib.sha256(encoded).hexdigest()}


def _validate_storage_archive(
    archive_path: Path, expected_files: list[dict[str, Any]]
) -> None:
    try:
        with tarfile.open(archive_path, "r") as archive:
            members = archive.getmembers()
            if [member.name for member in members] != [
                item["path"] for item in expected_files
            ]:
                raise CandidateBoundaryError("storage archive inventory differs")
            for member, expected in zip(members, expected_files, strict=True):
                _safe_relative_member(member.name)
                if not member.isfile() or member.size != expected["bytes"]:
                    raise CandidateBoundaryError("storage archive member differs")
                source_file = archive.extractfile(member)
                if source_file is None:
                    raise CandidateBoundaryError("storage archive member is unreadable")
                if hashlib.sha256(source_file.read()).hexdigest() != expected["sha256"]:
                    raise CandidateBoundaryError("storage archive member checksum differs")
    except tarfile.TarError as exc:
        raise CandidateBoundaryError("storage archive is unreadable") from exc


def backup(database_url: str, runtime_root: Path) -> Path:
    with _database_lifecycle_guard(database_url):
        return _backup_locked(database_url, runtime_root)


def _backup_locked(database_url: str, runtime_root: Path) -> Path:
    facts = _database_facts(database_url, require_initialized=True)
    storage, backups, pid_file = _runtime_paths(runtime_root)
    reservation = _reserve_pid_file(pid_file)
    reservation_identity = _reservation_identity(reservation)
    destination: Path | None = None
    destination_created = False
    try:
        pg_dump = _postgres_tool("pg_dump")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = backups / f"candidate-{stamp}"
        destination.mkdir(mode=0o700)
        destination_created = True
        dump_path = destination / "database.dump"
        archive_path = destination / "storage.tar"
        state_before = _state_evidence(database_url)
        subprocess.run(
            [
                pg_dump,
                "--dbname",
                database_url,
                "--format=custom",
                "--schema",
                SCHEMA,
                "--extension",
                "pgcrypto",
                "--file",
                str(dump_path),
            ],
            check=True,
            env={"PATH": os.path.dirname(pg_dump), "LANG": "C.UTF-8"},
            timeout=120,
        )
        dump_path.chmod(0o600)
        storage_files = _storage_inventory(storage)
        with tarfile.open(archive_path, "w") as archive:
            for item in storage_files:
                path = storage / item["path"]
                archive.add(path, arcname=item["path"], recursive=False)
        archive_path.chmod(0o600)
        _validate_storage_archive(archive_path, storage_files)
        if _storage_inventory(storage) != storage_files:
            raise CandidateBoundaryError("storage changed during backup")
        state_after = _state_evidence(database_url)
        if state_after != state_before:
            raise CandidateBoundaryError("database changed during backup")
        manifest = {
            "contract": "BUFFALO_LOCAL_CANDIDATE_BACKUP_V1",
            "created_utc": stamp,
            "database": facts,
            "database_dump": {
                "path": dump_path.name,
                "sha256": _sha256_file(dump_path),
                "bytes": dump_path.stat().st_size,
            },
            "storage_archive": {
                "path": archive_path.name,
                "sha256": _sha256_file(archive_path),
                "bytes": archive_path.stat().st_size,
                "files": storage_files,
            },
            "state": state_after,
            "limitations": [
                "same-host local recovery only",
                "sessions and secret files are intentionally excluded",
            ],
        }
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        manifest_path.chmod(0o600)
        return manifest_path
    except BaseException:
        if destination_created and destination is not None:
            shutil.rmtree(destination)
        raise
    finally:
        try:
            os.close(reservation)
        except OSError:
            pass
        _release_reserved_pid_path(pid_file, reservation_identity)


def _safe_relative_member(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CandidateBoundaryError("backup member path is malformed")
    member = Path(value)
    if (
        member.is_absolute()
        or value != member.as_posix()
        or any(part in {"", ".", ".."} for part in member.parts)
        or any(part in set(SECRET_FILES.values()) | {PID_FILE} for part in member.parts)
    ):
        raise CandidateBoundaryError("backup member path is unsafe")
    return value


def _restore_preflight(
    manifest_path: Path,
) -> tuple[dict[str, Any], Path, Path, list[dict[str, Any]]]:
    """Validate every manifest/archive byte and path before database mutation."""

    _require_mode(manifest_path, 0o600, directory=False)
    _require_mode(manifest_path.parent, 0o700, directory=True)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateBoundaryError("backup manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "contract",
            "created_utc",
            "database",
            "database_dump",
            "storage_archive",
            "state",
            "limitations",
        }
        or manifest.get("contract")
        != (
        "BUFFALO_LOCAL_CANDIDATE_BACKUP_V1"
        )
    ):
        raise CandidateBoundaryError("backup manifest contract differs")
    database_identity = manifest.get("database")
    if (
        not isinstance(manifest.get("created_utc"), str)
        or re.fullmatch(r"\d{8}T\d{6}Z", manifest["created_utc"]) is None
        or manifest.get("limitations")
        != [
            "same-host local recovery only",
            "sessions and secret files are intentionally excluded",
        ]
        or not isinstance(database_identity, dict)
        or set(database_identity)
        != {
            "database",
            "postgres_major",
            "server_address",
            "session_user",
            "current_user",
            "database_owner",
        }
        or not isinstance(database_identity.get("database"), str)
        or re.fullmatch(r"[a-z][a-z0-9_]*_demo", database_identity["database"]) is None
        or database_identity.get("postgres_major") != 16
        or database_identity.get("session_user") != "qa_release_login"
        or database_identity.get("current_user") != "qa_mapping_owner"
        or database_identity.get("database_owner") != "qa_mapping_owner"
    ):
        raise CandidateBoundaryError("backup provenance differs")
    try:
        if not ipaddress.ip_address(database_identity["server_address"]).is_loopback:
            raise CandidateBoundaryError("backup source server is not loopback")
    except ValueError as exc:
        raise CandidateBoundaryError("backup source server is malformed") from exc
    database_dump = manifest.get("database_dump")
    storage_archive = manifest.get("storage_archive")
    if (
        not isinstance(database_dump, dict)
        or set(database_dump) != {"path", "sha256", "bytes"}
        or not isinstance(storage_archive, dict)
        or set(storage_archive) != {"path", "sha256", "bytes", "files"}
    ):
        raise CandidateBoundaryError("backup member inventory is malformed")
    dump_name = _safe_relative_member(database_dump.get("path"))
    archive_name = _safe_relative_member(storage_archive.get("path"))
    if dump_name != "database.dump" or archive_name != "storage.tar":
        raise CandidateBoundaryError("backup payloads must be direct manifest siblings")
    source = manifest_path.parent
    dump_path = source / dump_name
    archive_path = source / archive_name
    for path, description in ((dump_path, database_dump), (archive_path, storage_archive)):
        _require_mode(path, 0o600, directory=False)
        expected_hash = description.get("sha256")
        expected_bytes = description.get("bytes")
        if (
            not isinstance(expected_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
            or path.stat().st_size != expected_bytes
            or _sha256_file(path) != expected_hash
        ):
            raise CandidateBoundaryError("backup member checksum or size differs")
    raw_expected = storage_archive.get("files")
    if not isinstance(raw_expected, list):
        raise CandidateBoundaryError("storage file inventory is malformed")
    expected_files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_expected:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            raise CandidateBoundaryError("storage file record is malformed")
        item_path = _safe_relative_member(item.get("path"))
        item_hash = item.get("sha256")
        item_bytes = item.get("bytes")
        if (
            item_path in seen
            or not isinstance(item_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", item_hash) is None
            or not isinstance(item_bytes, int)
            or item_bytes < 0
        ):
            raise CandidateBoundaryError("storage file record differs")
        seen.add(item_path)
        expected_files.append(
            {"path": item_path, "sha256": item_hash, "bytes": item_bytes}
        )
    if expected_files != sorted(expected_files, key=lambda item: item["path"]):
        raise CandidateBoundaryError("storage file inventory is not canonical")
    _validate_storage_archive(archive_path, expected_files)
    state = manifest.get("state")
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
        not isinstance(state, dict)
        or set(state) != {"facts", "sha256"}
        or not isinstance(state.get("facts"), dict)
        or set(state["facts"]) != expected_fact_keys
        or not isinstance(state.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", state["sha256"]) is None
    ):
        raise CandidateBoundaryError("backup state evidence is malformed")
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
    if any(
        not isinstance(state["facts"][key], int) or state["facts"][key] < 0
        for key in count_keys
    ) or any(not isinstance(state["facts"][key], str) for key in hash_keys):
        raise CandidateBoundaryError("backup state fact types differ")
    relation_inventory = state["facts"]["relation_inventory"]
    sequence_inventory = state["facts"]["sequence_inventory"]
    if (
        not isinstance(relation_inventory, list)
        or not isinstance(sequence_inventory, list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"relation", "row_count", "sha256"}
            or not isinstance(item["relation"], str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", item["relation"]) is None
            or not isinstance(item["row_count"], int)
            or item["row_count"] < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])) is None
            for item in relation_inventory
        )
        or [item["relation"] for item in relation_inventory]
        != sorted(item["relation"] for item in relation_inventory)
        or any(
            not isinstance(item, dict)
            or set(item) != {"sequence", "last_value", "is_called"}
            or not isinstance(item["sequence"], str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", item["sequence"]) is None
            or not isinstance(item["last_value"], int)
            or type(item["is_called"]) is not bool
            for item in sequence_inventory
        )
        or [item["sequence"] for item in sequence_inventory]
        != sorted(item["sequence"] for item in sequence_inventory)
    ):
        raise CandidateBoundaryError("backup state inventory differs")
    state_bytes = json.dumps(
        state["facts"], sort_keys=True, separators=(",", ":")
    ).encode()
    if hashlib.sha256(state_bytes).hexdigest() != state["sha256"]:
        raise CandidateBoundaryError("backup state digest differs")
    return manifest, dump_path, archive_path, expected_files


def _clean_failed_restore(database_url: str) -> None:
    database = _database_name(database_url, restore=True)
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT current_database()::text,current_setting('server_version_num')::integer,"
            "inet_server_addr()::text,session_user::text,current_user::text,"
            "pg_catalog.pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d "
            "WHERE d.datname=pg_catalog.current_database()"
        ).fetchone()
        assert row is not None
        if (
            str(row[0]) != database
            or int(row[1]) // 10000 != 16
            or not ipaddress.ip_interface(str(row[2])).ip.is_loopback
            or tuple(str(value) for value in row[3:5])
            != ("qa_release_login", "qa_mapping_owner")
            or str(row[5]) != "qa_mapping_owner"
        ):
            raise CandidateBoundaryError("restore cleanup target differs")
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA)))
        conn.execute("DROP EXTENSION IF EXISTS pgcrypto CASCADE")
    _database_facts(database_url, require_initialized=False, restore=True)


def _validate_restore_toc(value: str) -> None:
    """Require the custom archive to contain only the owned schema and pgcrypto."""

    entries = [line for line in value.splitlines() if line and not line.startswith(";")]
    if (
        not entries
        or not any(" SCHEMA - qa_mapping_test " in f" {line} " for line in entries)
        or not any(" EXTENSION - pgcrypto " in f" {line} " for line in entries)
        or any(
            "qa_mapping_test" not in line
            and " EXTENSION - pgcrypto " not in f" {line} "
            and " COMMENT - EXTENSION pgcrypto " not in f" {line} "
            for line in entries
        )
    ):
        raise CandidateBoundaryError("backup database archive exceeds the owned restore scope")


def _normalize_restored_acl_representation(database_url: str) -> None:
    """Reproduce the migration's explicit owner-only relation ACL representation."""

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        relations = sql.SQL(",").join(
            sql.Identifier(SCHEMA, relation) for relation in _MAPPING_RELATIONS
        )
        conn.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM PUBLIC").format(
                relations
            )
        )


def _remove_owned_restore_root(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        path.is_symlink()
        or not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != identity
    ):
        raise CandidateBoundaryError("restore storage ownership changed during cleanup")
    shutil.rmtree(path)


def restore(database_url: str, restore_root: Path, manifest_path: Path) -> dict[str, Any]:
    with _database_lifecycle_guard(database_url, restore=True):
        return _restore_locked(database_url, restore_root, manifest_path)


def _restore_locked(
    database_url: str, restore_root: Path, manifest_path: Path
) -> dict[str, Any]:
    target_facts = _database_facts(
        database_url, require_initialized=False, restore=True
    )
    if not restore_root.is_absolute():
        raise CandidateBoundaryError("restore storage root must be a new absolute path")
    _require_mode(restore_root.parent, 0o700, directory=True)
    manifest, dump_path, archive_path, expected_files = _restore_preflight(manifest_path)
    pg_restore = _postgres_tool("pg_restore")
    listed = subprocess.run(
        [pg_restore, "--list", str(dump_path)],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.path.dirname(pg_restore), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        timeout=30,
    )
    _validate_restore_toc(listed.stdout)
    mutated_database = False
    restore_created = False
    restore_identity: tuple[int, int] | None = None
    try:
        try:
            restore_root.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise CandidateBoundaryError(
                "restore storage root was claimed concurrently"
            ) from exc
        restore_created = True
        restored_root_stat = restore_root.stat(follow_symlinks=False)
        restore_identity = (restored_root_stat.st_dev, restored_root_stat.st_ino)
        with tarfile.open(archive_path, "r") as archive:
            for member in archive.getmembers():
                target = restore_root / member.name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source_file = archive.extractfile(member)
                assert source_file is not None
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "wb") as output:
                    shutil.copyfileobj(source_file, output)
        actual_files = [
            {
                "path": path.relative_to(restore_root).as_posix(),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(restore_root.rglob("*"))
            if path.is_file()
        ]
        if actual_files != expected_files:
            raise CandidateBoundaryError("restored storage evidence differs")
        mutated_database = True
        subprocess.run(
            [
                pg_restore,
                "--single-transaction",
                "--exit-on-error",
                "--dbname",
                database_url,
                "--no-owner",
                str(dump_path),
            ],
            check=True,
            env={"PATH": os.path.dirname(pg_restore), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            timeout=120,
        )
        _normalize_restored_acl_representation(database_url)
        restored_facts = _database_facts(
            database_url, require_initialized=True, restore=True
        )
        state = _state_evidence(database_url)
        if state != manifest["state"]:
            raise CandidateBoundaryError("restored database state evidence differs")
        return {
            "restored": True,
            "database": restored_facts["database"],
            "source_database": manifest.get("database", {}).get("database"),
            "state": state,
        }
    except BaseException as exc:
        storage_cleanup_error: BaseException | None = None
        if restore_created and restore_identity is not None:
            try:
                _remove_owned_restore_root(restore_root, restore_identity)
            except BaseException as cleanup_exc:
                storage_cleanup_error = cleanup_exc
        if mutated_database:
            try:
                _clean_failed_restore(database_url)
            except BaseException as cleanup_exc:
                raise CandidateBoundaryError(
                    "restore failed and owned-target cleanup was incomplete"
                ) from cleanup_exc
        if storage_cleanup_error is not None:
            raise CandidateBoundaryError(
                "restore failed and owned-storage cleanup was incomplete"
            ) from storage_cleanup_error
        raise exc


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "backup"):
        child = subparsers.add_parser(name)
        child.add_argument("--database-url", required=True)
        child.add_argument("--runtime-root", required=True, type=Path)
        if name == "serve":
            child.add_argument("--port", type=int, default=8765)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--database-url", required=True)
    restore_parser.add_argument("--restore-storage-root", required=True, type=Path)
    restore_parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            if args.port < 1024 or args.port > 65535:
                raise CandidateBoundaryError("local port must be between 1024 and 65535")
            return serve(args.database_url, args.runtime_root, args.port)
        if args.command == "backup":
            print(backup(args.database_url, args.runtime_root))
            return 0
        print(json.dumps(restore(args.database_url, args.restore_storage_root, args.manifest), sort_keys=True))
        return 0
    except (CandidateBoundaryError, OSError, psycopg.Error, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

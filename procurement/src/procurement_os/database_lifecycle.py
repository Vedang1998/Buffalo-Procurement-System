"""Shared PostgreSQL session lock for mutually exclusive database lifecycles."""
from __future__ import annotations

import re
from typing import Any


DATABASE_LIFECYCLE_LOCK_PREFIX = (
    "buffalo:local-purchasing-candidate:lifecycle:v1"
)
_DATABASE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class DatabaseLifecycleError(RuntimeError):
    """The exact database lifecycle lock cannot be proved."""


def database_lifecycle_lock_name(database: str) -> str:
    """Return the preserved advisory-lock name for one exact database."""

    if _DATABASE_NAME.fullmatch(database) is None:
        raise DatabaseLifecycleError("database lifecycle identity is malformed")
    return f"{DATABASE_LIFECYCLE_LOCK_PREFIX}:{database}"


def _validate_lock_name(lock_name: str) -> None:
    prefix = f"{DATABASE_LIFECYCLE_LOCK_PREFIX}:"
    if not lock_name.startswith(prefix):
        raise DatabaseLifecycleError("database lifecycle lock identity differs")
    database = lock_name.removeprefix(prefix)
    if database_lifecycle_lock_name(database) != lock_name:
        raise DatabaseLifecycleError("database lifecycle lock identity differs")


def _session_holds_lock(conn: Any, lock_name: str) -> bool:
    row = conn.execute(
        "SELECT pg_catalog.count(*)=1 "
        "FROM pg_catalog.pg_locks l "
        "WHERE l.locktype='advisory' "
        "AND l.pid=pg_catalog.pg_backend_pid() "
        "AND l.classid=((pg_catalog.hashtextextended(%s,0) >> 32) "
        "& 4294967295)::oid "
        "AND l.objid=(pg_catalog.hashtextextended(%s,0) "
        "& 4294967295)::oid "
        "AND l.objsubid=1 AND l.granted",
        (lock_name, lock_name),
    ).fetchone()
    return row is not None and bool(row[0])


def acquire_database_lifecycle_lock(conn: Any, *, database: str) -> str:
    """Acquire one session lock after binding the connection to ``database``."""

    lock_name = database_lifecycle_lock_name(database)
    row = conn.execute(
        "SELECT pg_catalog.current_database()::text"
    ).fetchone()
    if row is None or str(row[0]) != database:
        raise DatabaseLifecycleError("database lifecycle identity differs")
    if _session_holds_lock(conn, lock_name):
        raise DatabaseLifecycleError("database lifecycle lock is already held")
    locked = conn.execute(
        "SELECT pg_catalog.pg_try_advisory_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (lock_name,),
    ).fetchone()
    if locked is None or not bool(locked[0]):
        raise DatabaseLifecycleError(
            "another local purchasing lifecycle operation is active for this database"
        )
    return lock_name


def assert_database_lifecycle_lock(conn: Any, *, lock_name: str) -> None:
    """Prove that this backend still owns the exact session advisory lock."""

    _validate_lock_name(lock_name)
    if not _session_holds_lock(conn, lock_name):
        raise DatabaseLifecycleError("database lifecycle lock is no longer held")


def release_database_lifecycle_lock(conn: Any, *, lock_name: str) -> None:
    """Release exactly one held lock; closing the session remains the backstop."""

    _validate_lock_name(lock_name)
    row = conn.execute(
        "SELECT pg_catalog.pg_advisory_unlock("
        "pg_catalog.hashtextextended(%s,0))",
        (lock_name,),
    ).fetchone()
    if row is None or not bool(row[0]):
        raise DatabaseLifecycleError("database lifecycle lock release differs")

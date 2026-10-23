"""Portable catalog fixtures; never imported by application or acceptance code.

Call only on freshly initdb-created, owned fixture clusters, before scaffolding.
The historical production pin remains unchanged outside this scoped fixture.
"""
from contextlib import ExitStack, contextmanager
import hashlib
from unittest import mock

from procurement_os import synthetic_staging_database as contract


# Keep the complete security projection: internal SQL-body OIDs vary by build.
# The real verifier runs below, so query drift cannot silently establish a PASS.
_ROUTINES = (
    "SELECT n.nspname,p.proname,"
    "pg_catalog.pg_get_function_identity_arguments(p.oid),"
    "pg_catalog.pg_get_function_result(p.oid),l.lanname,p.prokind,"
    "p.provolatile,p.proparallel,p.prosecdef,p.proleakproof,p.proisstrict,"
    "p.proretset,p.pronargs,p.pronargdefaults,p.proargmodes::text,"
    "p.proargnames,p.proallargtypes::text,p.proargdefaults::text,"
    "p.prosrc,COALESCE(p.probin,''),p.prosqlbody::text,p.proconfig,"
    "p.procost::text,p.prorows::text,p.prosupport::regproc::text,"
    "CASE WHEN p.proowner=b.owner_oid THEN '<BOOTSTRAP_ADMIN>' "
    "ELSE pg_catalog.pg_get_userbyid(p.proowner) END "
    "FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
    "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
    "CROSS JOIN (SELECT nspowner AS owner_oid "
    "FROM pg_catalog.pg_namespace WHERE nspname='pg_catalog') b "
    "WHERE n.nspname IN ('pg_catalog','information_schema') "
    "ORDER BY n.nspname,p.proname,"
    "pg_catalog.pg_get_function_identity_arguments(p.oid)"
)
_active_baseline = False


def _routine_digest(connection):
    if connection.info.server_version // 10000 != 16 or connection.info.dbname != "postgres":
        raise RuntimeError("pristine PostgreSQL16 fixture database required")
    rows = [list(row) for row in connection.execute(_ROUTINES).fetchall()]
    if len(rows) != 3297:
        raise RuntimeError("pristine routine count differs")
    return hashlib.sha256(contract._canonical(rows)).hexdigest()


@contextmanager
def pristine_core_catalog_expectation(*connections):
    """Freeze once before mutations; restore even if verification/setup fails."""
    global _active_baseline
    if not connections or _active_baseline:
        raise RuntimeError("missing or overlapping pristine catalog fixture")
    _active_baseline = True
    try:
        with ExitStack() as transactions:
            digests = []
            for connection in connections:
                transactions.enter_context(connection.transaction())
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL search_path = pg_catalog")
                digests.append(_routine_digest(connection))
            if len(set(digests)) != 1:
                raise RuntimeError("source/destination pristine routine baselines differ")
            with mock.patch.object(contract, "CORE_SYSTEM_ROUTINE_SHA256", digests[0]):
                for connection in connections:
                    contract._verify_core_global_privilege_envelope(connection)
                # Class setup closes these connections; exit later only restores
                # the constant, never queries a closed or subsequently changed DB.
                transactions.close()
                yield digests[0]
    finally:
        _active_baseline = False

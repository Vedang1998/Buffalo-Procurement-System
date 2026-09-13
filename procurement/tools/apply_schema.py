#!/usr/bin/env python3
"""Apply the PostgreSQL schema and checksum-pinned migrations.

Legacy migrations retain their historical transactional replay behavior.  The
persistent-mapping family is a separately opted-in maintenance boundary: it is
never run by ordinary application startup, is bound to the reviewed synthetic
schema and maintenance-role tuple, and validates its source before executing or
trusting an installed assertion.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from psycopg import sql


MAPPING_MIGRATION_NAME = "014_persistent_mapping_foundation.sql"
MIGRATION_ORDER = [
    "schema_postgres.sql",
    "001_v1_3_catalog_sales.sql",
    "002_seed_import_records.sql",
    "003_phase3_reconciliation.sql",
    "004_identity_decision_invariants.sql",
    "005_identity_investigation.sql",
    "006_phase4_sales_backfill.sql",
    "007_phase4_terminal_disposition.sql",
    "008_monday_inventory_foundation.sql",
    "009_monday_vendor_rules.sql",
    "010_monday_po_ledger.sql",
    "011_monday_price_book_staging.sql",
    "012_monday_review_draft_packet.sql",
    "013_monday_p1_remediation.sql",
    MAPPING_MIGRATION_NAME,
]


@dataclass(frozen=True)
class MappingRelease:
    family: str
    version: str
    postgres_major: int
    target_schema: str
    migration_name: str
    migration_sha256: str
    predecessor_release: str | None
    maintenance_identity_config_ref: str
    maintenance_identity_config_sha256: str
    maintenance_identity_pairs_sha256: str


@dataclass(frozen=True)
class MappingReleaseTrust:
    family: str
    version: str
    function_identities: tuple[tuple[str, str], ...]
    function_catalog_sha256: str
    pgcrypto_digest_rows: tuple[tuple[Any, ...], ...]


MAPPING_RELEASE = MappingRelease(
    family="persistent-mapping-foundation",
    version="v1-shadow-only",
    postgres_major=16,
    target_schema="qa_mapping_test",
    migration_name=MAPPING_MIGRATION_NAME,
    migration_sha256="80c5d6c0a0299edf8d04f9c5f684f9cea277494b894fa0feb0a387476bfa8c86",
    predecessor_release=None,
    maintenance_identity_config_ref="config/persistent_mapping_maintenance.synthetic.json",
    maintenance_identity_config_sha256="ab773e859feee527bba01c4ae3193fece3258bbe471d92595302531cbc270450",
    maintenance_identity_pairs_sha256="2d08568ea5df7ef3ee383381ae2d952c197ac87f8d8fffd4c1af877ca05ef69c",
)
PERSISTENT_MAPPING_RELEASE_MANIFEST = (MAPPING_RELEASE,)

LEGACY_MIGRATION_SHA256 = {
    "schema_postgres.sql": "25b64090868bbf9a5868c43451b49908acc64711eea6aa1af6bcf39ed25d8515",
    "001_v1_3_catalog_sales.sql": "66bc873f91142e0941728af4260031a908195195a2e8d1a54d4ed5fee28ff50b",
    "002_seed_import_records.sql": "950e9520a12938ca0bee68ac3d68976da5a6119ec97753d4a4c243cb80ea3ad9",
    "003_phase3_reconciliation.sql": "079396b466baadea0501fd591aaa585a39299f6649bbc71fc52897d9d8a65e42",
    "004_identity_decision_invariants.sql": "bb490864a7657da672505d3fcba9df8c87ded72fefa0497db11298a058b64e7e",
    "005_identity_investigation.sql": "c9cc337fe34857368c471f493702fa0b42691ac48fe3fae4ca1a4fc3ea929b01",
    "006_phase4_sales_backfill.sql": "3ea0e2f280395c83d99d51617c7ffc6d6c1097d95f90e1bcad59698e67d775b1",
    "007_phase4_terminal_disposition.sql": "657b4db6f150b26aa93a83ba32e1e8d648ed183d9c939f4ebd1109f6afcdd363",
    "008_monday_inventory_foundation.sql": "34a80213be38332e1db5e8d63fb0a14560ef47eed4af78e2654bc47f31033a03",
    "009_monday_vendor_rules.sql": "c65817187a73123530a15711ed79ce429b3f69c443ba8e1d862df9b6bf05c6fa",
    "010_monday_po_ledger.sql": "11686ade7ceff33ec6d256ea2d8eb0079afc600ebfabc7abe866aa269dd5ebb6",
    "011_monday_price_book_staging.sql": "e0f912e8fd0433febf814d098d848a6178e24706956145904d0663c5b734aabe",
    "012_monday_review_draft_packet.sql": "c640072418e721a4b2a28faaeaf4f52f852d5c06afc41ab861f64af5b88d9aef",
    "013_monday_p1_remediation.sql": "9bd0b9d1755f35f96f6feb9dfc22814d4885f34dbe79cacab1ea48569ee5bef0",
}

# Exact PostgreSQL 16 function identities installed by the reviewed v1 release.
# The full properties of these identities are independently bound by the
# catalog digest below; this tuple prevents an added overload from hiding in a
# dynamically discovered set.
TRUSTED_FUNCTION_IDENTITIES = (
    ("assert_persistent_mapping_foundation_contract", ""),
    ("compute_persistent_mapping_catalog_sha256", ""),
    ("persistent_mapping_assert_safe_role_topology", ""),
    ("persistent_mapping_candidate_decision_scope", "c supplier_mapping_review_candidates"),
    ("persistent_mapping_candidate_evidence_set_sha256", "c supplier_mapping_review_candidates"),
    ("persistent_mapping_candidate_operational_offer_key", "c supplier_mapping_review_candidates"),
    ("persistent_mapping_candidate_reviewed_facts", "c supplier_mapping_review_candidates"),
    ("persistent_mapping_candidate_supplier_identity_key", "c supplier_mapping_review_candidates"),
    ("persistent_mapping_catalog_fingerprint", "wanted_variant_id text"),
    ("persistent_mapping_decision_confirmation_sha256", "d supplier_mapping_decisions"),
    ("persistent_mapping_decision_preview_sha256", "d supplier_mapping_decisions"),
    ("persistent_mapping_decision_request_sha256", "d supplier_mapping_decisions"),
    ("persistent_mapping_json_sha256", "value jsonb"),
    ("persistent_mapping_offer_fingerprint", "wanted_offer_id bigint"),
    ("persistent_mapping_offer_has_prior_references", "wanted_offer_id bigint"),
    ("persistent_mapping_rejection_contract_fingerprint", "wanted_rejection_id bigint"),
    ("persistent_mapping_rejection_fingerprint", "wanted_vendor_id uuid, wanted_source_key text, excluded_rejection_id bigint"),
    ("persistent_mapping_require_enabled_capability", "expected_capability text"),
    ("persistent_mapping_require_human_context", "expected_principal_ref text, expected_role_ref text, expected_authn_context_sha256 text, expected_action text"),
    ("persistent_mapping_selection_confirmation_sha256", "e supplier_offer_selection_events"),
    ("persistent_mapping_selection_preview_sha256", "e supplier_offer_selection_events"),
    ("persistent_mapping_text_sha256", "value text"),
    ("persistent_mapping_vendor_fingerprint", "wanted_vendor_id uuid"),
    ("protect_persistent_mapping_rejection_contract", ""),
    ("protect_persistently_mapped_offer_contract", ""),
    ("protect_unactivated_mapped_offer_price", ""),
    ("reject_persistent_mapping_mutation", ""),
    ("supplier_mapping_policy_is_published", "policy_ref text, policy_version text, publication_sha256 text, evidence_set_sha256 text"),
    ("validate_mapping_review_batch_insert", ""),
    ("validate_mapping_review_candidate_insert", ""),
    ("validate_mapping_review_candidate_set", ""),
    ("validate_supplier_mapping_decision_insert", ""),
    ("validate_supplier_offer_selection_event_committed", ""),
    ("validate_supplier_offer_selection_event_insert", ""),
    ("validate_supplier_offer_selection_head_change", ""),
)
TRUSTED_FUNCTION_CATALOG_SHA256 = (
    "2d990fc5e4308600b09995b978653b83352dd63d34e43e8241c0b84ea8f9700b"
)
TRUSTED_PGCRYPTO_DIGEST_ROWS = (
    (
        "pgcrypto", "1.3", "qa_mapping_test", "qa_mapping_owner", "digest",
        "bytea, text", "bytea", "c", "i", True, "s", "$libdir/pgcrypto",
        "pg_digest", "<NULL>", "e", False, False, "<NULL>", False, 0,
        "<NULL>",
    ),
    (
        "pgcrypto", "1.3", "qa_mapping_test", "qa_mapping_owner", "digest",
        "text, text", "bytea", "c", "i", True, "s", "$libdir/pgcrypto",
        "pg_digest", "<NULL>", "e", False, False, "<NULL>", False, 0,
        "<NULL>",
    ),
)

MAPPING_RELEASE_TRUST_MANIFEST = {
    (MAPPING_RELEASE.family, MAPPING_RELEASE.version): MappingReleaseTrust(
        family=MAPPING_RELEASE.family,
        version=MAPPING_RELEASE.version,
        function_identities=TRUSTED_FUNCTION_IDENTITIES,
        function_catalog_sha256=TRUSTED_FUNCTION_CATALOG_SHA256,
        pgcrypto_digest_rows=TRUSTED_PGCRYPTO_DIGEST_ROWS,
    )
}

_MAPPING_HEADER = (
    b"-- buffalo-migration-replay: checksum-skip-v2\n"
    b"-- buffalo-contract-family: persistent-mapping-foundation\n"
)
_MAPPING_FAMILY_LOCK_PREFIX = "buffalo:migration:persistent-mapping-foundation"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _release_source(db_dir: Path, release: MappingRelease = MAPPING_RELEASE) -> bytes:
    raw = (db_dir / release.migration_name).read_bytes()
    if not raw.startswith(_MAPPING_HEADER):
        raise RuntimeError("persistent mapping migration header differs")
    if _sha256(raw) != release.migration_sha256:
        raise RuntimeError("persistent mapping migration checksum differs")
    if b"__BUFFALO_" in raw or b"PROPOSED_UNNUMBERED" in raw:
        raise RuntimeError("persistent mapping migration retains a design token")
    return raw


def _release_trust(release: MappingRelease) -> MappingReleaseTrust:
    try:
        trust = MAPPING_RELEASE_TRUST_MANIFEST[(release.family, release.version)]
    except KeyError as exc:
        raise RuntimeError("persistent mapping release trust manifest is absent") from exc
    if (
        trust.family != release.family
        or trust.version != release.version
        or not trust.function_identities
        or not re.fullmatch(r"[0-9a-f]{64}", trust.function_catalog_sha256)
        or not trust.pgcrypto_digest_rows
    ):
        raise RuntimeError("persistent mapping release trust manifest differs")
    return trust


def _verify_source_inventory(db_dir: Path) -> None:
    legacy_names = tuple(LEGACY_MIGRATION_SHA256)
    releases = PERSISTENT_MAPPING_RELEASE_MANIFEST
    if tuple(MIGRATION_ORDER[: len(legacy_names)]) != legacy_names:
        raise RuntimeError("legacy migration order differs from the reviewed predecessor")
    if not releases or tuple(MIGRATION_ORDER[len(legacy_names) :]) != tuple(
        release.migration_name for release in releases
    ):
        raise RuntimeError("persistent mapping release manifest is inconsistent")
    common = releases[0]
    seen_versions: set[str] = set()
    seen_files: set[str] = set()
    prior: MappingRelease | None = None
    for release in releases:
        if (
            release.family != common.family
            or release.target_schema != common.target_schema
            or release.postgres_major != common.postgres_major
            or release.version in seen_versions
            or release.migration_name in seen_files
            or release.predecessor_release
            != (None if prior is None else prior.version)
        ):
            raise RuntimeError("persistent mapping release manifest is inconsistent")
        if not release.migration_name.endswith(".sql"):
            raise RuntimeError("persistent mapping migration filename differs")
        _release_trust(release)
        seen_versions.add(release.version)
        seen_files.add(release.migration_name)
        prior = release
    expected_trust = {(release.family, release.version) for release in releases}
    if set(MAPPING_RELEASE_TRUST_MANIFEST) != expected_trust:
        raise RuntimeError("persistent mapping release trust inventory differs")
    for name, expected in LEGACY_MIGRATION_SHA256.items():
        if _sha256((db_dir / name).read_bytes()) != expected:
            raise RuntimeError(f"reviewed predecessor source differs: {name}")
    for release in releases:
        _release_source(db_dir, release)
    for path in db_dir.glob("*.sql"):
        raw = path.read_bytes()
        if (
            raw.startswith(_MAPPING_HEADER)
            or "persistent_mapping" in path.name
        ) and path.name not in seen_files:
            raise RuntimeError("unmanifested persistent mapping migration exists")


def _verified_legacy_source(db_dir: Path, name: str) -> bytes:
    expected = LEGACY_MIGRATION_SHA256.get(name)
    if expected is None:
        raise RuntimeError(f"unreviewed predecessor migration: {name}")
    raw = (db_dir / name).read_bytes()
    if _sha256(raw) != expected:
        raise RuntimeError(f"reviewed predecessor source differs: {name}")
    return raw


def _capture_caller_search_path_oids(conn: Any) -> tuple[int, ...]:
    rows = conn.execute(
        "SELECT n.oid FROM pg_catalog.unnest(pg_catalog.current_schemas(true)) "
        "WITH ORDINALITY AS path(name,position) "
        "JOIN pg_catalog.pg_namespace n ON n.nspname=path.name "
        "ORDER BY path.position"
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def _pgcrypto_installed(conn: Any) -> bool:
    return bool(
        conn.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_extension WHERE extname='pgcrypto')"
        ).fetchone()[0]
    )


def _reject_pgcrypto_decoys_before_install(conn: Any, *, schema_oid: int) -> None:
    if _pgcrypto_installed(conn):
        raise RuntimeError("pgcrypto unexpectedly exists before reviewed bootstrap")
    digest_rows = conn.execute(
        "SELECT n.nspname,pg_catalog.pg_get_function_identity_arguments(p.oid) "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE p.proname='digest' AND pg_catalog.pg_get_function_identity_arguments(p.oid) "
        "IN ('bytea, text','text, text')"
    ).fetchall()
    if digest_rows:
        raise RuntimeError("a same-signature pgcrypto helper decoy exists")
    if conn.execute(
        "SELECT pg_catalog.has_schema_privilege(0::oid,%s,'CREATE')", (schema_oid,)
    ).fetchone()[0]:
        raise RuntimeError("PUBLIC can create in the pgcrypto destination schema")


def _set_controlled_legacy_path(conn: Any) -> None:
    conn.execute(
        sql.SQL("SET LOCAL search_path = {}, pg_temp").format(
            sql.Identifier(MAPPING_RELEASE.target_schema)
        )
    )
    if conn.execute("SELECT pg_catalog.current_schema()").fetchone()[0] != MAPPING_RELEASE.target_schema:
        raise RuntimeError("controlled legacy path does not create in the target schema")


def _verify_controlled_digest_resolution(conn: Any) -> None:
    expected = conn.execute(
        "SELECT p.oid FROM pg_catalog.pg_extension e "
        "JOIN pg_catalog.pg_depend d ON d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass "
        "AND d.refobjid=e.oid AND d.classid='pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND d.deptype='e' JOIN pg_catalog.pg_proc p ON p.oid=d.objid "
        "WHERE e.extname='pgcrypto' AND p.proname='digest' "
        "AND pg_catalog.pg_get_function_identity_arguments(p.oid)='bytea, text'"
    ).fetchall()
    resolved = conn.execute(
        "SELECT pg_catalog.to_regprocedure('digest(bytea,text)')::oid"
    ).fetchone()[0]
    if len(expected) != 1 or resolved != expected[0][0]:
        raise RuntimeError("controlled legacy digest resolution differs")


def _require_empty_unmarked_target(conn: Any, *, schema_oid: int) -> None:
    object_count = conn.execute(
        "SELECT (SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=%s) + "
        "(SELECT count(*) FROM pg_catalog.pg_proc WHERE pronamespace=%s) + "
        "(SELECT count(*) FROM pg_catalog.pg_type WHERE typnamespace=%s AND typname NOT LIKE '\\_%%') + "
        "(SELECT count(*) FROM pg_catalog.pg_extension WHERE extnamespace=%s)",
        (schema_oid, schema_oid, schema_oid, schema_oid),
    ).fetchone()[0]
    if int(object_count) != 0:
        raise RuntimeError("unmarked persistent mapping target is not empty")


def apply_verified_legacy_file(
    conn: Any,
    db_dir: Path,
    name: str,
    *,
    schema_oid: int,
) -> None:
    """Execute one checksum-pinned predecessor file on a controlled path."""

    raw = _verified_legacy_source(db_dir, name)
    installed = _pgcrypto_installed(conn)
    if name == "schema_postgres.sql" and not installed:
        _reject_pgcrypto_decoys_before_install(conn, schema_oid=schema_oid)
    elif not installed:
        raise RuntimeError("pgcrypto is absent after the reviewed bootstrap boundary")
    else:
        _verify_pgcrypto(conn, schema_oid=schema_oid)
    _set_controlled_legacy_path(conn)
    if installed:
        _verify_controlled_digest_resolution(conn)
    conn.execute(raw.decode("utf-8"))
    _verify_pgcrypto(conn, schema_oid=schema_oid)
    _verify_controlled_digest_resolution(conn)
    conn.execute(
        sql.SQL(
            "INSERT INTO {}.meta(key,value) VALUES (%s,'applied') "
            "ON CONFLICT(key) DO UPDATE SET value='applied',updated_at=pg_catalog.now()"
        ).format(sql.Identifier(MAPPING_RELEASE.target_schema)),
        (f"migration:{name}",),
    )


def _maintenance_binding(
    db_dir: Path, release: MappingRelease = MAPPING_RELEASE
) -> tuple[tuple[str, str], ...]:
    config_path = db_dir.parent / release.maintenance_identity_config_ref
    raw = config_path.read_bytes()
    if _sha256(raw) != release.maintenance_identity_config_sha256:
        raise RuntimeError("maintenance identity configuration checksum differs")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("maintenance identity configuration is malformed") from exc
    expected_keys = {"allowed_pairs", "contract", "family", "release", "target_schema"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError("maintenance identity configuration shape differs")
    if (
        value["contract"] != "BUFFALO_PERSISTENT_MAPPING_MAINTENANCE_IDENTITY_V1"
        or value["family"] != release.family
        or value["release"] != release.version
        or value["target_schema"] != release.target_schema
        or raw != _canonical_json(value) + b"\n"
    ):
        raise RuntimeError("maintenance identity configuration contract differs")
    pairs = value["allowed_pairs"]
    if not isinstance(pairs, list) or not pairs:
        raise RuntimeError("maintenance identity pair set is empty")
    parsed: list[tuple[str, str]] = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {"session_user", "current_user"}:
            raise RuntimeError("maintenance identity pair shape differs")
        session_user = pair["session_user"]
        current_user = pair["current_user"]
        if not isinstance(session_user, str) or not session_user.strip():
            raise RuntimeError("maintenance session role is blank")
        if not isinstance(current_user, str) or not current_user.strip():
            raise RuntimeError("maintenance effective role is blank")
        parsed.append((session_user, current_user))
    if parsed != sorted(set(parsed)):
        raise RuntimeError("maintenance identity pairs are duplicate or unordered")
    if _sha256(_canonical_json(pairs)) != release.maintenance_identity_pairs_sha256:
        raise RuntimeError("maintenance identity pair checksum differs")
    return tuple(parsed)


def _verify_server_and_identity(
    conn: Any,
    *,
    allowed_pairs: tuple[tuple[str, str], ...],
    release: MappingRelease = MAPPING_RELEASE,
) -> int:
    row = conn.execute(
        "SELECT pg_catalog.current_setting('server_version_num')::integer,"
        "session_user::text,current_user::text,pg_catalog.current_schema()::text"
    ).fetchone()
    if row is None or int(row[0]) // 10000 != release.postgres_major:
        raise RuntimeError("PostgreSQL major is not the reviewed mapping release major")
    if (str(row[1]), str(row[2])) not in allowed_pairs:
        raise RuntimeError("maintenance session/effective-role pair is not approved")
    schema = conn.execute(
        """SELECT n.nspname,n.oid,n.oid=pg_catalog.pg_my_temp_schema(),
                  pg_catalog.pg_is_other_temp_schema(n.oid),
                  pg_catalog.pg_get_userbyid(n.nspowner),
                  pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE'),
                  pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE'),
                  pg_catalog.has_schema_privilege(0::oid,n.oid,'CREATE')
             FROM pg_catalog.pg_namespace n WHERE n.nspname=%s""",
        (release.target_schema,),
    ).fetchone()
    if (
        schema is None
        or schema[2]
        or schema[3]
        or str(schema[4]) != str(row[2])
        or not schema[5]
        or not schema[6]
        or schema[7]
    ):
        raise RuntimeError("persistent mapping target schema ownership differs")
    for session_role, owner_role in allowed_pairs:
        topology = conn.execute(
            """SELECT member.rolcanlogin,owner.rolcanlogin,
                      membership.inherit_option,membership.set_option,
                      membership.admin_option,
                      pg_catalog.has_schema_privilege(member.oid,%s,'CREATE')
                 FROM pg_catalog.pg_roles member
                 JOIN pg_catalog.pg_roles owner ON owner.rolname=%s
                 LEFT JOIN pg_catalog.pg_auth_members membership
                   ON membership.member=member.oid AND membership.roleid=owner.oid
                WHERE member.rolname=%s""",
            (int(schema[1]), owner_role, session_role),
        ).fetchone()
        if (
            topology is None
            or not topology[0]
            or topology[1]
            or topology[2] is not False
            or topology[3] is not True
            or topology[4] is not False
            or topology[5]
        ):
            raise RuntimeError("maintenance role topology differs")
        inherited = conn.execute(
            """WITH RECURSIVE inherited(roleid) AS (
                   SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s
                   UNION
                   SELECT membership.roleid
                     FROM inherited
                     JOIN pg_catalog.pg_auth_members membership
                       ON membership.member=inherited.roleid
                    WHERE membership.inherit_option
               ) SELECT EXISTS(
                   SELECT 1 FROM inherited JOIN pg_catalog.pg_roles role
                     ON role.oid=inherited.roleid WHERE role.rolname=%s
               )""",
            (session_role, owner_role),
        ).fetchone()[0]
        if inherited:
            raise RuntimeError("maintenance owner is reachable through INHERIT")
    conn.execute(
        "SELECT pg_catalog.set_config('search_path',%s,true)",
        (f'"{release.target_schema}",pg_catalog',),
    )
    return int(schema[1])


def _verify_installed_function_catalog(
    conn: Any,
    *,
    schema_oid: int,
    release: MappingRelease = MAPPING_RELEASE,
) -> None:
    trust = _release_trust(release)
    names = sorted({item[0] for item in trust.function_identities})
    rows = conn.execute(
        sql.SQL("""SELECT p.proname,
                  pg_catalog.pg_get_function_identity_arguments(p.oid),
                  pg_catalog.pg_get_function_result(p.oid),p.prokind,p.provolatile,
                  p.proisstrict,p.prosecdef,p.proleakproof,p.proparallel,l.lanname,
                  pg_catalog.pg_get_userbyid(p.proowner),
                  COALESCE(p.proconfig,ARRAY[]::text[]),p.probin,
                  COALESCE(p.proacl::text,'<NULL>'),
                  pg_catalog.encode({}.digest(
                      pg_catalog.convert_to(p.prosrc,'UTF8'),'sha256'
                  ),'hex'),
                  p.proretset,p.pronargdefaults,
                  COALESCE(pg_catalog.pg_get_expr(p.proargdefaults,0),'<NULL>')
             FROM pg_catalog.pg_proc p
             JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE p.pronamespace=%s AND p.proname=ANY(%s)
            ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)""").format(
            sql.Identifier(release.target_schema)
        ),
        (schema_oid, names),
    ).fetchall()
    identities = tuple((str(row[0]), str(row[1])) for row in rows)
    if identities != trust.function_identities:
        raise RuntimeError("installed persistent mapping function identities differ")
    if _sha256(_canonical_json(rows)) != trust.function_catalog_sha256:
        raise RuntimeError("installed persistent mapping function properties differ")


def _verify_pgcrypto(
    conn: Any,
    *,
    schema_oid: int,
    release: MappingRelease = MAPPING_RELEASE,
) -> None:
    trust = _release_trust(release)
    rows = conn.execute(
        """SELECT e.extname,e.extversion,n.nspname,
                  pg_catalog.pg_get_userbyid(e.extowner),p.proname,
                  pg_catalog.pg_get_function_identity_arguments(p.oid),
                  pg_catalog.pg_get_function_result(p.oid),l.lanname,p.provolatile,
                  p.proisstrict,p.proparallel,p.probin,p.prosrc,
                  COALESCE(p.proacl::text,'<NULL>'),d.deptype,
                  p.prosecdef,p.proleakproof,
                  COALESCE(pg_catalog.array_to_string(p.proconfig,E'\\n'),'<NULL>'),
                  p.proretset,p.pronargdefaults,
                  COALESCE(pg_catalog.pg_get_expr(p.proargdefaults,0),'<NULL>')
             FROM pg_catalog.pg_extension e
             JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
             JOIN pg_catalog.pg_depend d
               ON d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass
              AND d.refobjid=e.oid
              AND d.classid='pg_catalog.pg_proc'::pg_catalog.regclass
              AND d.deptype='e'
             JOIN pg_catalog.pg_proc p ON p.oid=d.objid
             JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE e.extname='pgcrypto' AND e.extnamespace=%s AND p.proname='digest'
            ORDER BY pg_catalog.pg_get_function_identity_arguments(p.oid)""",
        (schema_oid,),
    ).fetchall()
    if tuple(tuple(value for value in row) for row in rows) != trust.pgcrypto_digest_rows:
        raise RuntimeError("pgcrypto digest extension contract differs")
    substitutes = conn.execute(
        "SELECT n.nspname,pg_catalog.pg_get_function_identity_arguments(p.oid) "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE p.proname='digest' AND pg_catalog.pg_get_function_identity_arguments(p.oid) "
        "IN ('bytea, text','text, text') AND p.oid NOT IN ("
        "SELECT d.objid FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_depend d "
        "ON d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass "
        "AND d.refobjid=e.oid AND d.classid='pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND d.deptype='e' WHERE e.extname='pgcrypto')"
    ).fetchall()
    if substitutes:
        raise RuntimeError("a same-signature pgcrypto helper substitute exists")


def _migration_markers(conn: Any) -> dict[str, str] | None:
    target_schema = PERSISTENT_MAPPING_RELEASE_MANIFEST[0].target_schema
    meta_exists = conn.execute(
        "SELECT pg_catalog.to_regclass(%s)",
        (f'"{target_schema}".meta',),
    ).fetchone()[0]
    if meta_exists is None:
        return None
    return dict(
        conn.execute(
            sql.SQL("SELECT key,value FROM {}.meta WHERE key LIKE 'migration:%'").format(
                sql.Identifier(target_schema)
            )
        ).fetchall()
    )


def _validate_marker_prefix(
    markers: dict[str, str] | None, *, allow_empty: bool
) -> tuple[MappingRelease, ...]:
    if markers is None or not markers:
        if allow_empty:
            return ()
        raise RuntimeError("reviewed predecessor migration markers are absent")
    legacy = {f"migration:{name}": "applied" for name in LEGACY_MIGRATION_SHA256}
    releases = PERSISTENT_MAPPING_RELEASE_MANIFEST
    mapping_keys = {
        f"migration:{release.migration_name}": release for release in releases
    }
    allowed = set(legacy) | set(mapping_keys)
    if set(markers) - allowed:
        raise RuntimeError("unknown mapping-family or predecessor marker exists")
    for key, expected in legacy.items():
        if markers.get(key) != expected:
            raise RuntimeError("reviewed predecessor marker prefix differs")
    installed: list[MappingRelease] = []
    gap_seen = False
    for release in releases:
        value = markers.get(f"migration:{release.migration_name}")
        if value is None:
            gap_seen = True
            continue
        if gap_seen:
            raise RuntimeError("installed persistent mapping release prefix has a gap")
        if value != f"sha256:{release.migration_sha256}":
            raise RuntimeError("installed persistent mapping migration checksum differs")
        installed.append(release)
    return tuple(installed)


def _mapping_metadata(conn: Any, *, target_schema: str) -> dict[str, str]:
    return dict(
        conn.execute(
            sql.SQL("SELECT key,value FROM {}.meta WHERE key=ANY(%s)").format(
                sql.Identifier(target_schema)
            ),
            (
                [
                    "persistent_mapping_foundation_contract",
                    "persistent_mapping_foundation_catalog_sha256",
                ],
            ),
        ).fetchall()
    )


def _transition_allowed_pairs(
    db_dir: Path,
    release: MappingRelease,
    *,
    applying: bool,
) -> tuple[tuple[str, str], ...]:
    current = set(_maintenance_binding(db_dir, release))
    if applying and release.predecessor_release is not None:
        releases = PERSISTENT_MAPPING_RELEASE_MANIFEST
        index = releases.index(release)
        prior = set(_maintenance_binding(db_dir, releases[index - 1]))
        current &= prior
        if not current:
            raise RuntimeError("maintenance identity rotation has no reviewed intersection")
    return tuple(sorted(current))


def _verify_installed_release(
    conn: Any,
    *,
    release: MappingRelease,
    schema_oid: int,
    allowed_pairs: tuple[tuple[str, str], ...],
) -> None:
    _verify_pgcrypto(conn, schema_oid=schema_oid, release=release)
    _verify_installed_function_catalog(
        conn, schema_oid=schema_oid, release=release
    )
    metadata = _mapping_metadata(conn, target_schema=release.target_schema)
    if set(metadata) != {
        "persistent_mapping_foundation_contract",
        "persistent_mapping_foundation_catalog_sha256",
    } or metadata["persistent_mapping_foundation_contract"] != release.version:
        raise RuntimeError("installed persistent mapping contract metadata differs")
    target = sql.Identifier(release.target_schema)
    computed = conn.execute(
        sql.SQL("SELECT {}.compute_persistent_mapping_catalog_sha256()").format(target)
    ).fetchone()[0]
    if metadata["persistent_mapping_foundation_catalog_sha256"] != computed:
        raise RuntimeError("installed persistent mapping catalog signature differs")
    conn.execute(
        sql.SQL("SELECT {}.assert_persistent_mapping_foundation_contract()").format(
            target
        )
    )
    _verify_installed_function_catalog(
        conn, schema_oid=schema_oid, release=release
    )
    _verify_pgcrypto(conn, schema_oid=schema_oid, release=release)
    _verify_server_and_identity(
        conn, allowed_pairs=allowed_pairs, release=release
    )


def _verify_or_apply_mapping_release(
    conn: Any,
    db_dir: Path,
    release: MappingRelease = MAPPING_RELEASE,
) -> bool:
    _verify_source_inventory(db_dir)
    conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended(%s,0))",
        (f"{_MAPPING_FAMILY_LOCK_PREFIX}:{release.target_schema}",),
    )
    installed = _validate_marker_prefix(
        _migration_markers(conn), allow_empty=False
    )
    releases = PERSISTENT_MAPPING_RELEASE_MANIFEST
    release_index = releases.index(release)
    if release_index < len(installed) - 1:
        installed_release = installed[-1]
        allowed_pairs = _maintenance_binding(db_dir, installed_release)
        schema_oid = _verify_server_and_identity(
            conn, allowed_pairs=allowed_pairs, release=installed_release
        )
        _verify_installed_release(
            conn,
            release=installed_release,
            schema_oid=schema_oid,
            allowed_pairs=allowed_pairs,
        )
        return False
    applying = release_index == len(installed)
    if release_index > len(installed):
        raise RuntimeError("persistent mapping release predecessor is absent")
    allowed_pairs = _transition_allowed_pairs(
        db_dir, release, applying=applying
    )
    schema_oid = _verify_server_and_identity(
        conn, allowed_pairs=allowed_pairs, release=release
    )
    if installed:
        _verify_installed_release(
            conn,
            release=installed[-1],
            schema_oid=schema_oid,
            allowed_pairs=allowed_pairs,
        )
    elif _mapping_metadata(conn, target_schema=release.target_schema):
        raise RuntimeError("persistent mapping metadata exists without a release marker")
    if not applying:
        return False

    raw = _release_source(db_dir, release)
    expected_marker = f"sha256:{release.migration_sha256}"
    target = sql.Identifier(release.target_schema)
    _verify_pgcrypto(
        conn,
        schema_oid=schema_oid,
        release=installed[-1] if installed else release,
    )
    conn.execute(raw.decode("utf-8"))
    after_oid = _verify_server_and_identity(
        conn, allowed_pairs=allowed_pairs, release=release
    )
    if after_oid != schema_oid:
        raise RuntimeError("persistent mapping target schema identity changed")
    _verify_pgcrypto(conn, schema_oid=schema_oid, release=release)
    _verify_installed_function_catalog(
        conn, schema_oid=schema_oid, release=release
    )
    conn.execute(
        sql.SQL("SELECT {}.persistent_mapping_assert_safe_role_topology()").format(
            target
        )
    )
    catalog_sha = conn.execute(
        sql.SQL("SELECT {}.compute_persistent_mapping_catalog_sha256()").format(target)
    ).fetchone()[0]
    if not isinstance(catalog_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", catalog_sha):
        raise RuntimeError("persistent mapping catalog signature is malformed")
    conn.execute(
        sql.SQL(
            "INSERT INTO {}.meta(key,value) VALUES (%s,%s),(%s,%s) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=pg_catalog.now()"
        ).format(target),
        (
            "persistent_mapping_foundation_contract",
            release.version,
            "persistent_mapping_foundation_catalog_sha256",
            catalog_sha,
        ),
    )
    conn.execute(
        sql.SQL("SELECT {}.assert_persistent_mapping_foundation_contract()").format(target)
    )
    _verify_installed_function_catalog(
        conn, schema_oid=schema_oid, release=release
    )
    _verify_pgcrypto(conn, schema_oid=schema_oid, release=release)
    _verify_server_and_identity(
        conn, allowed_pairs=allowed_pairs, release=release
    )
    conn.execute(
        sql.SQL("INSERT INTO {}.meta(key,value) VALUES (%s,%s)").format(target),
        (f"migration:{release.migration_name}", expected_marker),
    )
    return True


def apply_schema_connection(
    conn: Any,
    db_dir: Path,
    *,
    include_persistent_mapping: bool = False,
) -> list[str]:
    """Apply files transactionally; mapping requires an explicit opt-in."""
    applied: list[str] = []
    installed_marker_keys: set[str] = set()
    mapping_releases = {
        release.migration_name: release
        for release in PERSISTENT_MAPPING_RELEASE_MANIFEST
    }
    if include_persistent_mapping and mapping_releases:
        _verify_source_inventory(db_dir)
        first_release = PERSISTENT_MAPPING_RELEASE_MANIFEST[0]
        with conn.transaction():
            _capture_caller_search_path_oids(conn)
            markers = _migration_markers(conn)
            installed = _validate_marker_prefix(markers, allow_empty=True)
            identity_release = installed[-1] if installed else first_release
            allowed_pairs = _maintenance_binding(db_dir, identity_release)
            schema_oid = _verify_server_and_identity(
                conn, allowed_pairs=allowed_pairs, release=identity_release
            )
            installed_marker_keys = set(markers or {})
            if not installed_marker_keys:
                _require_empty_unmarked_target(conn, schema_oid=schema_oid)
    for name in MIGRATION_ORDER:
        if name in mapping_releases:
            if not include_persistent_mapping:
                continue
            with conn.transaction():
                if _verify_or_apply_mapping_release(
                    conn, db_dir, mapping_releases[name]
                ):
                    applied.append(name)
            continue
        if include_persistent_mapping and f"migration:{name}" in installed_marker_keys:
            continue
        with conn.transaction():
            if include_persistent_mapping:
                apply_verified_legacy_file(
                    conn,
                    db_dir,
                    name,
                    schema_oid=schema_oid,
                )
            else:
                raw_sql = (db_dir / name).read_text(encoding="utf-8")
                conn.execute(raw_sql)
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES (%s,'applied')"
                    " ON CONFLICT(key) DO UPDATE SET value='applied', updated_at=now()",
                    (f"migration:{name}",),
                )
        applied.append(name)
    return applied


def apply_schema(
    db_dir: Path,
    database_url: str,
    *,
    include_persistent_mapping: bool = False,
) -> list[str]:
    import psycopg

    if include_persistent_mapping:
        parsed = urlparse(database_url)
        database = parsed.path.removeprefix("/")
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("persistent mapping database port is malformed") from exc
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or port is None
            or not database.endswith(("_test", "_demo"))
            or parsed.password is not None
            or parsed.fragment
        ):
            raise RuntimeError(
                "persistent mapping execution requires an owned loopback *_test/*_demo target"
            )
    with psycopg.connect(database_url) as conn:
        if include_persistent_mapping:
            connected = conn.execute(
                "SELECT pg_catalog.current_database(),pg_catalog.inet_server_addr()::text"
            ).fetchone()
            if (
                connected is None
                or str(connected[0]) != database
                or not ipaddress.ip_interface(str(connected[1])).ip.is_loopback
            ):
                raise RuntimeError("connected persistent mapping database identity differs")
        return apply_schema_connection(
            conn,
            db_dir,
            include_persistent_mapping=include_persistent_mapping,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-dir", type=Path, default=Path(__file__).resolve().parents[1] / "db"
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--include-persistent-mapping",
        action="store_true",
        help="explicitly enter the reviewed synthetic mapping maintenance boundary",
    )
    args = parser.parse_args()
    for name in apply_schema(
        args.db_dir,
        args.database_url,
        include_persistent_mapping=args.include_persistent_mapping,
    ):
        print(f"applied: {name}")


if __name__ == "__main__":
    main()

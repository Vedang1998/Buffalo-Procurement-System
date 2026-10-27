"""Bound populated-recovery TOCs to the attested final PostgreSQL 16 catalog.

Dormant operator component. No CLI, URL, subprocess, restore or credential release.
The caller must keep the stopped, owned source and its read-only exported snapshot
alive while BOTH dumps are made with the fixed options below. A returned use-list
does not establish archive provenance, target ownership or restore permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

SCHEMA = 'qa_mapping_test'
OWNER = 'buffalo_synthetic_owner'
LEGACY_OWNER = 'qa_mapping_owner'
DATABASE = 'buffalo_synthetic_staging_demo'
MAX_ENTRIES = 8192
MAX_TOC_BYTES = 2 * 1024**2
DUMP_OPTIONS = ('--format=custom', '--schema', SCHEMA, '--extension', 'pgcrypto',
                '--no-acl', '--no-comments', '--no-security-labels',
                '--no-publications', '--no-subscriptions')
_NAME = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
_SNAPSHOT = re.compile(r'[0-9A-Fa-f-]{10,80}\Z')
_KINDS = frozenset(('SCHEMA', 'EXTENSION', 'TABLE', 'VIEW', 'SEQUENCE', 'FUNCTION',
                    'INDEX', 'DEFAULT', 'CONSTRAINT', 'CHECK CONSTRAINT', 'FK CONSTRAINT', 'TRIGGER',
                    'SEQUENCE OWNED BY', 'TABLE DATA', 'SEQUENCE SET'))
_DATA = frozenset(('TABLE DATA', 'SEQUENCE SET'))
PGCRYPTO_MEMBERS_SQL = r"""
SELECT d.classid='pg_catalog.pg_proc'::pg_catalog.regclass,d.objid,d.objsubid,d.refobjsubid
FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
WHERE d.refclassid=e.tableoid AND d.deptype='e' AND e.extname='pgcrypto'
ORDER BY d.classid,d.objid,d.objsubid
"""

# These structural relationships are not all present in the older catalog hash.
# Refuse unsupported shapes instead of silently filtering them out of the TOC.
CATALOG_SHAPE_SQL = r"""
WITH ns AS (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s),
cl AS (SELECT c.tableoid,c.* FROM pg_catalog.pg_class c JOIN ns ON ns.oid=c.relnamespace),
seq_edges AS (
 SELECT s.oid,d.deptype,d.refobjid,d.refobjsubid FROM cl s
 JOIN pg_catalog.pg_depend d ON d.classid=s.tableoid AND d.objid=s.oid
 AND d.objsubid=0 AND d.refclassid='pg_catalog.pg_class'::pg_catalog.regclass
 AND d.deptype IN ('a','i') WHERE s.relkind='S'
)
SELECT NOT (
 EXISTS (SELECT 1 FROM cl c WHERE c.relkind NOT IN ('r','v','S','i') OR c.relispartition
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_depend d WHERE d.classid=c.tableoid AND d.objid=c.oid
 AND d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass AND d.deptype='e'))
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN ns ON ns.oid=p.pronamespace WHERE p.prokind<>'f'
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
 WHERE d.classid=p.tableoid AND d.objid=p.oid AND d.refclassid=e.tableoid AND d.deptype='e'
 AND (e.extname<>'pgcrypto' OR d.objsubid<>0)))
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_extension e JOIN ns ON ns.oid=e.extnamespace
 WHERE e.extname<>'pgcrypto' OR e.extconfig IS NOT NULL OR e.extcondition IS NOT NULL)
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a JOIN cl c ON c.oid=a.attrelid
 WHERE a.attnum>0 AND NOT a.attisdropped AND a.attgenerated<>'')
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k JOIN cl c ON c.oid=k.conrelid
 WHERE k.contype NOT IN ('p','u','x','f','c') OR NOT k.conislocal OR k.conparentid<>0)
 OR EXISTS (SELECT 1 FROM seq_edges GROUP BY oid HAVING count(*)>1)
 OR EXISTS (SELECT 1 FROM seq_edges d LEFT JOIN cl c ON c.oid=d.refobjid
 LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=d.refobjsubid
 WHERE c.oid IS NULL OR c.relkind<>'r' OR a.attname IS NULL OR a.attisdropped
 OR d.refobjsubid<=0 OR (d.deptype='a' AND a.attidentity<>'')
 OR (d.deptype='i' AND a.attidentity NOT IN ('a','d')))
)
"""

# Catalog OIDs come from this database, never fixed OID guesses. pg_dump emits
# nil catalog IDs for OWNED BY and SEQUENCE SET, and (0, relid) for TABLE DATA.
# Optional defaults/CHECKs may be embedded in TABLE rather than separate entries.
# The same-snapshot schema-only dump supplies their exact serialized placement.
CATALOG_SQL = r"""
WITH ns AS (SELECT * FROM pg_catalog.pg_namespace WHERE nspname=%s),
cl AS (SELECT c.tableoid,c.* FROM pg_catalog.pg_class c JOIN ns ON ns.oid=c.relnamespace),
extension_members AS (
 SELECT d.classid,d.objid FROM pg_catalog.pg_depend d
 JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
 WHERE d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass AND d.deptype='e'
 AND e.extname='pgcrypto'
), records AS (
 SELECT 'pg_catalog.pg_namespace'::pg_catalog.regclass::oid AS catalog_oid,
 n.oid AS object_oid,'SCHEMA' AS kind,'-' AS namespace,n.nspname::text AS tag,
 pg_catalog.pg_get_userbyid(n.nspowner)::text AS owner,true AS required FROM ns n
 UNION ALL
 SELECT 'pg_catalog.pg_extension'::pg_catalog.regclass::oid,e.oid,'EXTENSION','-',e.extname,
 pg_catalog.pg_get_userbyid(e.extowner),true FROM pg_catalog.pg_extension e
 JOIN ns ON ns.oid=e.extnamespace WHERE e.extname='pgcrypto'
 UNION ALL
 SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::oid,c.oid,
 CASE c.relkind WHEN 'r' THEN 'TABLE' WHEN 'v' THEN 'VIEW' WHEN 'S' THEN 'SEQUENCE' ELSE 'INDEX' END,
 %s,c.relname,pg_catalog.pg_get_userbyid(c.relowner),true FROM cl c
 WHERE c.relkind IN ('r','v','S','i') AND NOT EXISTS
 (SELECT 1 FROM pg_catalog.pg_constraint k WHERE k.conindid=c.oid AND k.contype IN ('p','u','x'))
 UNION ALL
 SELECT 'pg_catalog.pg_proc'::pg_catalog.regclass::oid,p.oid,'FUNCTION',%s,
 p.proname||'('||COALESCE((SELECT pg_catalog.string_agg(pg_catalog.format_type(a.typ,NULL),', ' ORDER BY a.n)
 FROM pg_catalog.unnest(p.proargtypes::oid[]) WITH ORDINALITY AS a(typ,n)),'')||')',
 pg_catalog.pg_get_userbyid(p.proowner),true FROM pg_catalog.pg_proc p JOIN ns ON ns.oid=p.pronamespace
 WHERE p.prokind='f' AND NOT EXISTS (SELECT 1 FROM extension_members m
 WHERE m.classid='pg_catalog.pg_proc'::pg_catalog.regclass AND m.objid=p.oid)
 UNION ALL
 SELECT 'pg_catalog.pg_attrdef'::pg_catalog.regclass::oid,d.oid,'DEFAULT',%s,
 c.relname||' '||a.attname,pg_catalog.pg_get_userbyid(c.relowner),false
 FROM pg_catalog.pg_attrdef d JOIN cl c ON c.oid=d.adrelid
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=d.adnum
 WHERE c.relkind='r' AND a.attgenerated='' AND NOT a.attisdropped
 UNION ALL
 SELECT 'pg_catalog.pg_constraint'::pg_catalog.regclass::oid,k.oid,
 CASE k.contype WHEN 'f' THEN 'FK CONSTRAINT' WHEN 'c' THEN 'CHECK CONSTRAINT' ELSE 'CONSTRAINT' END,%s,
 c.relname||' '||k.conname,pg_catalog.pg_get_userbyid(c.relowner),k.contype<>'c'
 FROM pg_catalog.pg_constraint k JOIN cl c ON c.oid=k.conrelid
 WHERE k.contype IN ('p','u','x','f','c') AND k.conislocal
 UNION ALL
 SELECT 'pg_catalog.pg_trigger'::pg_catalog.regclass::oid,t.oid,'TRIGGER',%s,
 c.relname||' '||t.tgname,pg_catalog.pg_get_userbyid(c.relowner),true
 FROM pg_catalog.pg_trigger t JOIN cl c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal
 UNION ALL
 SELECT 0,0,'SEQUENCE OWNED BY',%s,c.relname,pg_catalog.pg_get_userbyid(c.relowner),true
 FROM cl c JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_class'::pg_catalog.regclass
 AND d.objid=c.oid AND d.objsubid=0 AND d.refclassid='pg_catalog.pg_class'::pg_catalog.regclass
 JOIN cl parent ON parent.oid=d.refobjid AND parent.relkind='r'
 JOIN pg_catalog.pg_attribute a ON a.attrelid=parent.oid AND a.attnum=d.refobjsubid
 WHERE c.relkind='S' AND d.deptype='a' AND a.attidentity='' AND NOT a.attisdropped
 UNION ALL
 SELECT 0,c.oid,'TABLE DATA',%s,c.relname,pg_catalog.pg_get_userbyid(c.relowner),true FROM cl c WHERE c.relkind='r'
 UNION ALL
 SELECT 0,0,'SEQUENCE SET',%s,c.relname,pg_catalog.pg_get_userbyid(c.relowner),true FROM cl c WHERE c.relkind='S'
)
SELECT catalog_oid,object_oid,kind,namespace,tag,owner,required FROM records
ORDER BY kind,namespace,tag LIMIT 8193
"""


class RecoveryTocRefusal(RuntimeError):
    """A fixed diagnostic; never include database rows or native output."""


def _require(condition):
    if not condition:
        raise RecoveryTocRefusal('populated_recovery_toc_refused')


@dataclass(frozen=True)
class CatalogRecord:
    catalog_oid: int
    object_oid: int
    kind: str
    namespace: str
    tag: str
    owner: str
    required: bool

    def key(self):
        owner = '' if self.kind == 'EXTENSION' else self.owner
        return (self.catalog_oid, self.object_oid, f'{self.kind} {self.namespace} {self.tag} {owner}')


@dataclass(frozen=True)
class CatalogCapture:
    records: tuple[CatalogRecord, ...]
    snapshot: str
    release_sha256: str


def capture_catalog(conn, target):
    """Read in the caller's retained RR/read-only transaction, then export it.

    Requires the existing final-catalog/provenance attestation. Does not open a
    connection or own its lifetime. The caller must separately prove no writers,
    native-tool identity, complete archive capture and the source process lease.
    """
    from procurement_os.synthetic_staging_database import attest_provisioner_connection
    _require(conn.execute('SHOW transaction_isolation').fetchone() == ('repeatable read',))
    _require(conn.execute('SHOW transaction_read_only').fetchone() == ('on',))
    release = attest_provisioner_connection(conn, target)
    _pgcrypto_members(conn)
    shape = conn.execute(CATALOG_SHAPE_SQL, (SCHEMA,)).fetchone()
    _require(type(shape) is tuple and len(shape) == 1 and shape[0] is True)
    rows = conn.execute(CATALOG_SQL, (SCHEMA,) * 9).fetchall()
    records = tuple(CatalogRecord(*row) for row in rows)
    snapshot = conn.execute('SELECT pg_catalog.pg_export_snapshot()').fetchone()[0]
    captured = CatalogCapture(records, snapshot, release)
    _inventory(captured)
    return captured


def _pgcrypto_members(conn):
    # The old catalog hash includes function definitions but not extension
    # membership. An application function illicitly added to pgcrypto would
    # otherwise disappear from both dumps without changing that hash.
    from procurement_os.synthetic_staging_database import _verified_pgcrypto_rows
    rows = _verified_pgcrypto_rows(conn)
    _require(rows and all(type(r[0]) is int and r[0] > 0 and r[13] == OWNER for r in rows))
    expected = {r[0] for r in rows}
    _require(len(expected) == len(rows))
    members = conn.execute(PGCRYPTO_MEMBERS_SQL).fetchall()
    _require(len(members) == len(expected) and all(type(m) is tuple and len(m) == 4
             and m[0] is True and type(m[1]) is int and m[1] in expected
             and type(m[2]) is int and m[2] == 0 and type(m[3]) is int and m[3] == 0 for m in members))
    _require({m[1] for m in members} == expected)


def _inventory(captured):
    _require(type(captured) is CatalogCapture and type(captured.records) is tuple
             and 0 < len(captured.records) <= MAX_ENTRIES
             and type(captured.snapshot) is str and _SNAPSHOT.fullmatch(captured.snapshot)
             and type(captured.release_sha256) is str and re.fullmatch('[0-9a-f]{64}', captured.release_sha256))
    keys = {}
    for row in captured.records:
        _require(type(row) is CatalogRecord and type(row.kind) is str and row.kind in _KINDS
                 and type(row.required) is bool and type(row.tag) is str and len(row.tag) <= 2048
                 and all(type(v) is int and 0 <= v < 2**32 for v in (row.catalog_oid, row.object_oid)))
        _require(row.namespace == ('-' if row.kind in ('SCHEMA', 'EXTENSION') else SCHEMA))
        _require(row.owner == (LEGACY_OWNER if row.kind == 'EXTENSION' else OWNER))
        if row.kind == 'SCHEMA':
            _require(row.tag == SCHEMA)
        elif row.kind == 'EXTENSION':
            _require(row.tag == 'pgcrypto')
        elif row.kind == 'FUNCTION':
            # Actual tags are compared byte-for-byte below. This alphabet permits
            # built-in multiword/array types, but no quoted/user-defined types or SQL.
            _require(re.fullmatch(r'[a-z][a-z0-9_]{0,62}\([a-z0-9_, \[\]]*\)', row.tag))
        else:
            parts = row.tag.split(' ')
            _require(len(parts) == (2 if row.kind in ('DEFAULT', 'CONSTRAINT', 'CHECK CONSTRAINT', 'FK CONSTRAINT', 'TRIGGER') else 1)
                     and all(_NAME.fullmatch(p) for p in parts))
        if row.kind in ('SEQUENCE SET', 'SEQUENCE OWNED BY'):
            _require((row.catalog_oid, row.object_oid) == (0, 0))
        elif row.kind == 'TABLE DATA':
            _require(row.catalog_oid == 0 and row.object_oid > 0)
        else:
            _require(row.catalog_oid > 0 and row.object_oid > 0)
        _require(row.required or row.kind in ('DEFAULT', 'CHECK CONSTRAINT'))
        key = row.key()
        _require(key not in keys)
        keys[key] = row
    _require(sum(r.kind == 'SCHEMA' for r in keys.values()) == 1
             and sum(r.kind == 'EXTENSION' for r in keys.values()) == 1)
    tables = {r.tag: r for r in keys.values() if r.kind == 'TABLE'}
    sequences = {r.tag for r in keys.values() if r.kind == 'SEQUENCE'}
    _require(tables and sequences)
    _require({r.tag for r in keys.values() if r.kind == 'TABLE DATA'} == set(tables)
             and {r.tag for r in keys.values() if r.kind == 'SEQUENCE SET'} == sequences)
    for row in keys.values():
        if row.kind == 'TABLE DATA':
            _require(row.object_oid == tables[row.tag].object_oid)
        elif row.kind == 'SEQUENCE OWNED BY':
            _require(row.tag in sequences)
    return keys


def _toc(raw, allowed, *, schema_only):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_TOC_BYTES and raw.endswith(b'\n'))
    try:
        text = raw.decode('ascii')
    except UnicodeError:
        raise RecoveryTocRefusal('populated_recovery_toc_refused') from None
    _require(all(c == '\n' or 32 <= ord(c) <= 126 for c in text))
    patterns = {
        'created': r'; Archive created at [ -~]{1,100}',
        'database': ';     dbname: ' + DATABASE,
        'entries': r';     TOC Entries: ([1-9][0-9]{0,4})',
        'compression': r';     Compression: [A-Za-z0-9.-]{1,30}',
        'dump_version': r';     Dump Version: 1\.15-0',
        'format': r';     Format: CUSTOM',
        'integer': r';     Integer: 4 bytes',
        'offset': r';     Offset: 8 bytes',
        'source_version': r';     Dumped from database version: 16\.[ -~]{1,100}',
        'tool_version': r';     Dumped by pg_dump version: 16\.[ -~]{1,100}',
        'selected': r'; Selected TOC Entries:',
    }
    headers = {}; ids = set(); seen = {}; lines = []
    for line in text.splitlines():
        _require(len(line) <= 4096)
        if not line or line == ';':
            _require(not seen)
            continue
        if line.startswith(';'):
            _require(not seen)
            matches = [(k, re.fullmatch(p, line)) for k, p in patterns.items()]
            matches = [(k, m) for k, m in matches if m]
            _require(len(matches) == 1 and matches[0][0] not in headers)
            name, match = matches[0]; headers[name] = match.group(1) if name == 'entries' else line
            continue
        _require(set(headers) == set(patterns))
        match = re.fullmatch(r'([1-9][0-9]{0,9}); (0|[1-9][0-9]{0,9}) (0|[1-9][0-9]{0,9}) (.+)', line)
        _require(match is not None)
        dump_id, cat, oid = map(int, match.group(1, 2, 3)); key = (cat, oid, match.group(4))
        _require(dump_id < 2**31 and dump_id not in ids and key not in seen and key in allowed)
        row = allowed[key]
        _require(not schema_only or row.kind not in _DATA)
        ids.add(dump_id); seen[key] = row; lines.append(line)
        _require(len(lines) <= MAX_ENTRIES)
    _require(seen and set(headers) == set(patterns) and len(lines) <= int(headers['entries']) <= MAX_ENTRIES)
    mandatory = {k for k, r in allowed.items() if r.required and (not schema_only or r.kind not in _DATA)}
    _require(mandatory <= set(seen))
    return seen, ('\n'.join(lines) + '\n').encode('ascii')


def validate_tocs(captured, full_toc, schema_toc):
    """Validate exact OID/descriptor coverage, return only the full-dump use-list.

    Different dump IDs/order/header times are legitimate. Every schema descriptor
    must match, including optional separately serialized defaults/CHECKs. This is
    not SQL-body validation or proof that these bytes came from a trusted capture.
    """
    allowed = _inventory(captured)
    full, use_list = _toc(full_toc, allowed, schema_only=False)
    schema, _ = _toc(schema_toc, allowed, schema_only=True)
    _require({k for k, r in full.items() if r.kind not in _DATA} == set(schema))
    semantic = '\n'.join(sorted(f'{a} {b} {c}' for a, b, c in full)).encode('ascii')
    observation = {'contract': 'BUFFALO_POPULATED_RECOVERY_TOC_V1',
                   'full_entries': len(full), 'schema_entries': len(schema),
                   'source_snapshot': captured.snapshot, 'source_release_sha256': captured.release_sha256,
                   'catalog_records_sha256': hashlib.sha256(json.dumps(
                       sorted((r.catalog_oid,r.object_oid,r.kind,r.namespace,r.tag,r.owner,r.required)
                              for r in captured.records),separators=(',',':')).encode('ascii')).hexdigest(),
                   'full_toc_sha256': hashlib.sha256(full_toc).hexdigest(),
                   'schema_toc_sha256': hashlib.sha256(schema_toc).hexdigest(),
                   'semantic_sha256': hashlib.sha256(semantic).hexdigest(),
                   'use_list_sha256': hashlib.sha256(use_list).hexdigest(),
                   'scope': 'CATALOG_BOUND_TOC_COMPONENT_ONLY',
                   'archive_provenance': 'CALLER_RETAINED_SNAPSHOT_REQUIRED',
                   'restore_execution': 'NOT_RUN', 'execution_authority': False}
    return use_list, observation

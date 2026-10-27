"""Dormant populated runtime snapshot adapter; opens no connection or process.

The caller owns an attested local source, keeps its exported snapshot alive and
proves stopped writers/sequence quiescence. A snapshot import does not prove those
facts or physical cluster ownership. Actual credential-bearing calls remain behind
the independent-review and same-generation containment/release gates.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from hosted_synthetic_recovery_toc import _inventory,SCHEMA,DATABASE
from hosted_synthetic_recovery_inputs import _storage_records,_ARTIFACT,_PRICE
from hosted_synthetic_restart_evidence import _state,_canonical,COUNTS

_TABLE_COUNTS = {
    'review_batches':'supplier_mapping_review_batches',
    'review_candidates':'supplier_mapping_review_candidates',
    'mapping_decisions':'supplier_mapping_decisions',
    'selection_events':'supplier_offer_selection_events',
    'selection_heads':'supplier_offer_selection_heads',
    'runs':'runs','purchase_orders':'purchase_orders','artifacts':'monday_run_artifacts',
}
_BEGIN='BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'
_ENDPOINT='SELECT pg_catalog.host(pg_catalog.inet_server_addr()),pg_catalog.inet_server_port(),pg_catalog.pg_backend_pid()'


class RecoverySnapshotRefusal(RuntimeError):
    """Never include connection strings, credentials or database output."""


class RecoverySnapshotCleanupError(RecoverySnapshotRefusal):
    pass


def _require(condition):
    if not condition:raise RecoverySnapshotRefusal('populated_recovery_snapshot_refused')


def _idle(conn):
    status=conn.info.transaction_status
    return (isinstance(status,int) and not isinstance(status,bool) and int(status)==0
            and conn.closed is False and conn.broken is False)


@dataclass(frozen=True)
class PopulatedSnapshotEvidence:
    state_json: bytes
    storage_bindings_json: bytes
    observation_json: bytes


def _validated_evidence(captured,state,bindings,*,commit,tree,runtime_identity):
    catalog=_inventory(captured)
    state_json=_canonical(state);state=_state(state_json);facts=state['facts']
    tables={r.tag for r in catalog.values() if r.kind=='TABLE'}
    sequences={r.tag for r in catalog.values() if r.kind=='SEQUENCE'}
    _require({r['relation'] for r in facts['relation_inventory']}==tables
             and {r['sequence'] for r in facts['sequence_inventory']}==sequences)
    counts={r['relation']:r['row_count'] for r in facts['relation_inventory']}
    _require(all(counts.get(table)==facts[key]==COUNTS[key] for key,table in _TABLE_COUNTS.items()))
    validated=_storage_records(bindings,binding=True)
    artifacts=[r for name,r in validated.items() if _ARTIFACT.fullmatch(name)]
    _require(len(artifacts)==facts['artifacts'] and any(_PRICE.fullmatch(name) for name in validated))
    _require(sorted(r['sha256'] for r in artifacts)==sorted(facts['artifact_hashes'].split('|')))
    bindings_json=_canonical(bindings)
    records=sorted((r.catalog_oid,r.object_oid,r.kind,r.namespace,r.tag,r.owner,r.required) for r in captured.records)
    catalog_sha=hashlib.sha256(json.dumps(records,separators=(',',':')).encode('ascii')).hexdigest()
    observation={'contract':'BUFFALO_POPULATED_RUNTIME_SNAPSHOT_V1','commit':commit,'tree':tree,
        'snapshot':captured.snapshot,'catalog_records_sha256':catalog_sha,
        'backup_release_sha256':captured.release_sha256,'runtime_attestation_identity':runtime_identity,
        'state_sha256':state['sha256'],'state_bytes_sha256':hashlib.sha256(state_json).hexdigest(),
        'storage_bindings_sha256':hashlib.sha256(bindings_json).hexdigest(),
        'tables':len(tables),'sequences':len(sequences),'storage_files':len(bindings),
        'scope':'POPULATED_RUNTIME_SNAPSHOT_COMPONENT_ONLY','source_stopped':'CALLER_LIFECYCLE_PROOF_REQUIRED',
        'sequence_quiescence':'CALLER_PROOF_REQUIRED_NOT_MVCC','physical_cluster_identity':'CALLER_OWNED_LEASE_REQUIRED',
        'native_dump_provenance':'NOT_PROVEN','restore_execution':'NOT_RUN','execution_authority':False}
    return PopulatedSnapshotEvidence(state_json,bindings_json,_canonical(observation))


def capture_populated_snapshot(conn,target,captured,*,commit,tree):
    """Import the exported snapshot into a fresh caller-owned local connection.

    Requires an idle autocommit connection so BEGIN options cannot be masked by an
    implicit earlier transaction. This adapter owns only the transaction it starts;
    it always rolls back and proves idle state before returning immutable evidence.
    Failed/ambiguous rollback requires caller quarantine, never automatic reconnect.
    """
    from psycopg import sql
    from procurement_os.synthetic_staging_database import (
        SyntheticStagingTarget,attest_runtime_connection,EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        STAGING_BACKUP_RELEASE_SHA256,RUNTIME_LOGIN,
    )
    from procurement_os.local_backup_v2 import database_state_evidence
    from procurement_os.synthetic_staging_backup_v2 import _storage_bindings

    _inventory(captured)
    _require(type(commit) is str and re.fullmatch('[0-9a-f]{40}',commit)
             and type(tree) is str and re.fullmatch('[0-9a-f]{40}',tree)
             and captured.release_sha256==STAGING_BACKUP_RELEASE_SHA256)
    _require(type(target) is SyntheticStagingTarget and type(target.owned_local_port) is int
             and 1<=target.owned_local_port<=65535 and target.expected_private_host in ('127.0.0.1','::1'))
    target.validate_static()
    _require(conn.autocommit is True and _idle(conn)
             and conn.info.host==target.expected_private_host and conn.info.hostaddr==target.expected_private_host
             and conn.info.port==target.owned_local_port
             and conn.info.dbname==DATABASE and conn.info.user==RUNTIME_LOGIN
             and type(conn.info.backend_pid) is int and conn.info.backend_pid>0)
    result=None;failure=None;cleanup=None
    try:
        conn.execute(_BEGIN)
        # SET commands do not acquire a data snapshot. Import precedes every
        # SELECT, including endpoint inspection and the existing attestation.
        conn.execute("SET LOCAL statement_timeout = '10s'")
        conn.execute("SET LOCAL lock_timeout = '2s'")
        conn.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
        conn.execute(sql.SQL('SET TRANSACTION SNAPSHOT {}').format(sql.Literal(captured.snapshot)))
        endpoint=conn.execute(_ENDPOINT).fetchone()
        _require(type(endpoint) is tuple and len(endpoint)==3 and endpoint==(
            target.expected_private_host,target.owned_local_port,conn.info.backend_pid)
            and type(endpoint[1]) is int and type(endpoint[2]) is int)
        identity=attest_runtime_connection(conn,target)
        _require(identity==EXPECTED_RUNTIME_ATTESTATION_IDENTITY)
        # Attestation leaves pg_catalog search_path. Do not call it a second time;
        # its original-path precondition intentionally expects the runtime path.
        state=database_state_evidence(conn,schema=SCHEMA,staging_runtime=True)
        bindings=_storage_bindings(conn)
        result=_validated_evidence(captured,state,bindings,commit=commit,tree=tree,runtime_identity=identity)
    except BaseException as error:
        failure=error
    finally:
        try:
            conn.execute('ROLLBACK')
            _require(_idle(conn))
        except BaseException as error:cleanup=error
    if cleanup is not None:
        raise RecoverySnapshotCleanupError('populated_runtime_transaction_cleanup_refused') from (failure if failure is not None else cleanup)
    if failure is not None:raise failure
    return result

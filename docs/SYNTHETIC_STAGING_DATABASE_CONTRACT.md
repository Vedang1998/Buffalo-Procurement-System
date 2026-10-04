# Synthetic staging database contract V2

Status: locally implemented and independently reproduced; not deployed or
accepted as a release candidate.

The versioned contract is `BUFFALO_SYNTHETIC_STAGING_DATABASE_V2`. Its
source-defined PostgreSQL transition is additive migration
`procurement/db/017_synthetic_staging_contract.sql`; migrations 001–016 and
their legacy behavior remain unchanged.

## Exact destination and authorities

The only accepted database is `buffalo_synthetic_staging_demo` on PostgreSQL
16. The runtime target additionally binds the reviewed Railway project,
environment, application service, PostgreSQL service, and private hostname.
Loopback is accepted only by the explicit owned-local acceptance configuration
used by the repository's disposable tests.

- `buffalo_synthetic_owner` is `NOLOGIN NOINHERIT` and owns the transitioned
  database objects.
- `buffalo_synthetic_provisioner` is a setup-only `LOGIN INHERIT` role with no
  administrator capabilities. It inherits only the legacy and successor owner
  roles needed to perform the single enumerated ownership transition. It is
  never present in an application environment and cannot create databases,
  roles, or schemas.
- `buffalo_synthetic_runtime` is `LOGIN NOINHERIT`, owns no object, has no role
  membership path, and receives only the explicit application matrix.

The provisioner's `INHERIT` setting is an intentional V2 reconciliation of the
obsolete V1 note that described `NOINHERIT`: PostgreSQL requires the actor
transferring each existing object to possess both ownership paths. The V2
contract constrains that authority through exact role flags and membership
edges, an exact target, a pinned predecessor, one transaction, and an
operator-only command. It does not broaden runtime authority.

## Source-defined state

The runtime matrix contains 60 readable application relations, 28 writable
relations with 36 exact operations, 14 sequences with `USAGE` only, and 45
exact routine signatures. The canonical 155-record matrix SHA-256 is:

`d58ed105c7eb6446c4b6d4694b2a3d8a12fc304454e8622f1de2d4a06dfeabb6`

No future-object grant exists. `PUBLIC` access, default privileges, column
privileges, grant options, ownership, database `CREATE`/`TEMP`, schema
`CREATE`, relation `TRUNCATE`/`TRIGGER`/`REFERENCES`, unlisted relations,
unlisted sequences, overloads, other schemas, and other databases are audited
fail closed.

The accepted immutable fixture manifest SHA-256 is:

`08fd401f70ad55f7957b09bd47e99797a648d1f7ba2c0c86e24f5d0034683ec1`

It binds the registered development-forecast-v2 metadata and immutable source
facts while deliberately excluding mutable review, run, DRAFT, and packet
state. The fixture-only sales-backfill UUID is source-defined so independent
initializations have the same provenance; ordinary callers continue to use a
PostgreSQL-generated UUID.

The post-bootstrap predecessor and final successor catalog SHA-256 values are:

- predecessor: `59ebe68a203511cb54d2d02d7c73ef44cb1ee3887f2c85effbaa08f7276cceda`
- successor: `af02588dee120940bd34a44c6d1fbc4b66062082eaeda36e268344ae992eaea3`

The staging successors for the integrity projections derived from migrations
014, 015, and 016 are separately pinned in
`procurement_os.synthetic_staging_database`. Runtime dispatch selects those
successors only for the exact staging runtime identity; legacy connections
continue to enforce their original contracts.

## Transition state machine

Administrative bootstrap and constrained provisioning are separate phases.
Both require explicit non-autocommit transactions and acquire transaction-level
advisory locks before state classification.

1. Administrative bootstrap accepts only the exact legacy role/database and
   pgcrypto-owner state, creates the three staging roles, hardens the legacy
   login, normalizes the enumerated extension-member owners, installs the exact
   membership/settings envelope, and transfers database ownership. Exact final
   bootstrap state is a verified no-op; mixed state is refused.
2. Provisioning accepts only the exact fixture, integrity consumers, role
   topology, and predecessor catalog. It enumerates every application object
   ownership change, installs the staging-only assertions and lock helpers,
   applies the exact ACL/default-ACL matrix, and writes all four staging markers
   last. Exact successor state is a verified no-op; missing, partial, forged,
   or conflicting state is refused without repair.

The operator tool exposes these as distinct `bootstrap` and `provision`
commands and reports `changed` versus `verified-no-op`. Ordinary gateway or
worker startup cannot invoke either phase.

## Runtime proof boundary

Startup/readiness calls `attest_runtime_connection()` using a real runtime-role
connection. Attestation is read-only and verifies destination identity, role
topology, fixture manifest, all markers, global and 014/015/016 successor
catalogs, database/schema/column/object/routine privileges, ownership, and
cross-database/cross-schema isolation before returning the fixed readiness
identity.

Local PG16 evidence proves rollback after an injected late transition failure,
exact predecessor-to-successor, no-op reapplication, NULL-safe marker refusal,
unrelated sentinel preservation, runtime denials, and the restricted-role
selected-offer → frozen-price → V2 demand → review → DRAFT → packet workflow.
Generation-bound worker lifecycle integration, private dump/restore, portable
startup, full browser acceptance, and independent external review remain later
candidate gates.

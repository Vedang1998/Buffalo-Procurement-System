# Synthetic staging database contract V1

Status: local implementation checkpoint; not deployed or accepted.

The versioned contract is `BUFFALO_SYNTHETIC_STAGING_DATABASE_V1`. Its
machine-readable object/operation allowlist and canonical SHA-256 are defined
in `procurement_os.synthetic_staging_database`. Every listed relation is read
by the synthetic workflow. Write grants are restricted to the relations and
operations used by price intake, recommendation, review, DRAFT, and packet
construction. The four executable routines are the existing catalog/integrity
assertions plus the staging assertion. No future-object grant is created.

Authorities are separated:

- `buffalo_synthetic_owner`: `NOLOGIN NOINHERIT`; owns restored objects.
- `buffalo_synthetic_provisioner`: setup-only login, `NOINHERIT`; may `SET
  ROLE` to the owner but is never placed in an application environment.
- `buffalo_synthetic_runtime`: application login, `NOINHERIT`, non-owner, and
  has no membership path to either privileged role.

Database `CONNECT`, schema `USAGE`, enumerated relation operations, and four
routine signatures are the entire runtime authority. Database `TEMP`, schema
`CREATE`, relation `TRUNCATE`, `TRIGGER`, `REFERENCES`, ownership, role
administration, and grant options are denied. `PUBLIC`, inherited, recursive
role topology, and effective privileges are inspected by readiness.

Provisioning is an explicit operator action against the exact dedicated
database. It transfers the already-initialized canonical fixture objects,
revokes public/default privileges, writes the immutable contract and matrix
markers, installs the SQL-side assertion, and stops on any mismatch. Ordinary
gateway/worker startup cannot invoke this command. Gateway and research worker
environment allowlists contain no database URL or password file.

Positive coverage verifies the exact destination tuple and explicit matrix.
Negative coverage verifies wrong Railway scope, host, database, login,
credentialed URL, and administrative operations. Disposable PostgreSQL
integration coverage and complete runtime workflow proof remain required
before this checkpoint can become the final candidate.

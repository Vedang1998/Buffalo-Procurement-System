# Phase 4 Published-Production Final Adversarial Remediation Design

**Date:** 2026-09-05  
**Status:** Owner-approved direction; written design awaiting owner review  
**Risk:** Level 4 — published-production historical identity/data correction  
**Reviewed starting head:** `16b1a234379ba777263772ce76118c52ac78bfd0`  
**Reviewed starting tree:** `97061e85b86a52bb209c40a95c2c666a1da2d7b3`  
**Branch:** `codex/phase4-published-production-reconciliation`

## 1. Scope and safety boundary

This remediation closes four independently identified execution-safety gaps:

1. make the complete bootstrap boundary independent of inherited `PATH`;
2. isolate every corrective canonical Git derivation from deployment secrets
   and inherited Git configuration;
3. re-prove each stage's exact predecessor state after the shared advisory lock
   is acquired and inside the mutation-owning transaction; and
4. attest the complete semantics of migration-007 identity protections rather
   than accepting object names alone.

The previously remediated inherited-Git boundary, exact migration-marker
dictionaries, and real late-finalizer rollback proof remain mandatory and must
stay green. This work may use only disposable loopback PostgreSQL 16 databases
whose names end in `_test`. It may not connect to `neondb`, access Shopify,
perform a PO action, create a deployment, open a PR, change an identity outcome,
or begin Phase 6.

## 2. Approaches considered

### Recommended: structural attestation plus narrow locked callbacks

Keep the existing state machine and canonical persistence services. Add generic,
optional post-lock callbacks to the two persistence services, with `None`
preserving every existing caller. The corrective runner supplies exact
stage-specific callbacks and uses a transaction-safe raw migration/schema
inspector. Build a deterministic PostgreSQL-catalog payload for migration-007
objects and freeze its SHA-256 after generating it from the committed migration
on disposable PostgreSQL 16.

This is the smallest approach that closes the concrete same-name schema bypass
and the classification-to-mutation race without copying business SQL.

### Rejected: one long connection/transaction for the entire executor

Holding classification and all stages in one transaction would eliminate some
race windows, but it would discard the accepted restart-safe stage boundaries,
make the finalizer transaction structure harder to reason about, and broaden
the production executor substantially.

### Rejected: textual DDL hash or behavioral probes alone

A `pg_dump`/pretty-DDL hash is sensitive to formatting and environment details.
Behavioral probes cover only anticipated attacks and cannot prove full trigger,
constraint, function, and view semantics. Structural catalog fields plus exact
function bodies and canonical PostgreSQL 16 definitions are deterministic and
fail closed on both known and unanticipated drift.

Likewise, merely assigning a safe `PATH` is rejected for the bootstrap: command
names would still be resolved dynamically. Every executable at the bootstrap
boundary must be an approved absolute literal.

## 3. Complete bootstrap trust boundary

The bootstrap becomes POSIX-shell compatible with the absolute shebang
`#!/bin/sh`. The future Scheduled Deployment command must invoke it as:

```text
/bin/sh ./scripts/phase4-published-production-bootstrap.sh <REVIEWED_40_CHAR_SHA> <REVIEWED_40_CHAR_TREE>
```

All external bootstrap utilities are absolute reviewed root-owned paths,
including `/usr/bin/mktemp`, `/usr/bin/mkdir`, `/usr/bin/rm`, `/usr/bin/env`,
`/usr/bin/git`, and the fixed utility used to attest ownership. The bootstrap
requires each invoked utility to be a regular executable, owned by UID 0, and
not writable by the executing user before use. Argument validation and other
simple operations use shell builtins. Clone, checkout, origin, HEAD, tree, and
clean-status checks retain the existing `env -i` Git allowlist and isolated
`HOME`/`XDG_CONFIG_HOME`. There is no packaged-source fallback.

The only accepted Python executable is the owner-approved literal:

```text
/nix/store/yp3s28b4xjvcq53wapb1v7hv5hlmmmma-python-wrapped-0.1.0/bin/.python-wrapped
```

The bootstrap hardcodes both that file and its exact package-root and `bin`
parent paths. Before Python execution it requires the parents to exist as
directories, be owned by UID 0, and be non-writable by the executing user, and
requires the interpreter to be a regular, executable, UID-0-owned,
non-writable file. There is no
`python`, `python3`, `PATH` lookup, glob, dynamic discovery, CLI choice,
environment override, or alternate fallback. Any failed check exits before
Python and therefore before a database connection. The exact interpreter is
invoked by absolute path only after every repository proof; that verified
Python invocation then receives the normal parent production environment for
its internal authorization checks.

The real bootstrap regression places hostile executable replacements for
`git`, `mktemp`, `mkdir`, `rm`, `python3`, and `bash` at the front of inherited
`PATH`. Every replacement records a sentinel. The test invokes the bootstrap
through `/bin/sh`, proves no sentinel executes, exercises the real clone and
provenance sequence, and proves verified repository Python receives the
authorized synthetic parent environment only after all proofs.

## 4. Corrective Git isolation for every derivation

The canonical `derive_runtime_execution_git_identity()` implementation and its
meaning remain unchanged. The corrective runner wraps all three downstream
calls that can invoke it in the existing corrective-only isolated Git
environment:

- terminal dry-run inside `classify_state()`;
- first terminal persistence inside `apply_terminal_stage()`; and
- mandatory zero-DML persistence replay inside `prove_terminal_noop()`.

During each whole canonical call, `os.environ` contains only the fixed Git
allowlist. Consequently the real Git subprocess cannot receive the database URL
or either review token, cannot resolve a hostile leading-`PATH` Git, and cannot
load inherited system/global/XDG Git configuration, fsmonitor, hooks,
templates, askpass, SSH, transport, or proxy configuration. The single-threaded
runner restores the exact parent Python environment in `finally` after each
call.

A real-Git integration test uses a temporary committed repository and patches
only the terminal module's repository anchor, not its derivation function. A
delegating subprocess spy proves each actual Git child sees the fixed safe
environment and none of the three sensitive variables. Under hostile parent
configuration it exercises real terminal classification, real terminal
persistence, and real mandatory replay, proving unchanged 858-mutation then
zero-mutation behavior and no hostile sentinel execution.

## 5. Transaction-local exact-state proof

The existing SELECT-only body of `migration_007_state()` becomes a raw helper
that assumes the caller's transaction. The public classifier continues to call
it through the established database-enforced read-only/no-XID wrapper. Mutation
stages call the raw helper directly after acquiring the shared advisory
transaction lock, avoiding an invalid nested read-only transaction.

Two canonical service signatures gain generic optional keyword-only callbacks:

- `persist_manifest_decisions(..., locked_precondition=None)`; and
- `persist_terminal_disposition(..., locked_precondition=None)`.

Each callback runs immediately after `acquire_backfill_transaction_lock()` and
before the service's database preflight/state inspection or first mutation.
Defaults preserve existing behavior and callers. Callback failure rolls back
the service-owned serializable transaction.

The corrective stages use the callbacks as follows:

- **Original manifest:** after the lock, require exact migration/schema
  `ABSENT`, frozen protected controls, exact State-A inventory, and exact
  manifest `MISSING=343` preflight before the canonical service can mutate.
- **Migration 007:** after the lock, require exact pre-007 `ABSENT` semantic
  state and marker dictionary, then exact State-B manifest, inventory, alias,
  exclusion, and protected controls before executing the migration SQL.
- **Terminal apply:** after the lock, require exact post-007 schema attestation
  and canonical `PRE_TERMINAL_EXACT` before terminal mutation.
- **Terminal replay:** after the lock, require exact post-007 schema attestation,
  canonical `CURRENT_TERMINAL_EXACT`, and `PRE_REBUILD` before the zero-DML
  replay. This prevents a stale replay from applying terminal mutations.
- **Rebuild:** immediately after the lock, require exact post-007 schema
  attestation, then retain the existing exact State-D terminal, inventory,
  protected-fingerprint, and authoritative-run checks before the real local
  finalizer.

No corrective callback duplicates manifest or terminal mutation SQL, changes
canonical Git provenance, or accepts caller-supplied observed identity.

Disposable PostgreSQL tests cover two separately classified State-B
executions, with the first committing migration 007 and the second refusing the
now-stale stage after its lock with zero DDL/DML. Additional tests insert marker
or lifecycle drift between outer classification and direct stage invocation for
the original manifest, terminal apply, terminal replay, and rebuild. Fresh
readback must prove the drift itself is the only change and no stage mutation
survives.

## 6. Migration-007 semantic schema attestation

The raw inspector computes a deterministic payload for the target schema. It
temporarily establishes a deterministic `pg_catalog,<target-schema>` search
path for PostgreSQL deparsing, restores the caller's setting afterward, and
normalizes only the target schema identity to a fixed token. Rows and nested
collections are sorted; canonical JSON uses sorted keys and compact separators;
SHA-256 covers the resulting UTF-8 bytes. OIDs, owners, timestamps, statistics,
and database-specific identifiers are excluded.

The payload contains:

- **Functions:** every required name and overload, with schema/name, identity
  arguments, result type, language, kind, volatility, parallel safety,
  strict/security-definer/leakproof/set-returning flags, argument metadata,
  defaults/configuration, binary identity, and exact `prosrc` body.
- **Triggers:** name, exact owning table, `tgtype`, enabled/internal/parent and
  deferrability fields, invoked function identity, update-column names, trigger
  arguments, transition-table names, exact condition, and non-pretty canonical
  trigger definition.
- **Constraints:** name, exact owning table, type, validation, deferrability,
  inheritance/locality fields, local/referenced columns and table, foreign-key
  actions/match type, exact expression, and non-pretty canonical definition.
  This includes every explicitly required migration constraint plus every
  constraint on the migration-created exclusion-authority table.
- **Views:** name, relation kind/persistence/options, exact non-pretty view
  definition, and ordered output-column name/type/typmod/nullability/collation.
- **Authority table:** complete column name/order/type/typmod/nullability,
  default, identity, and generated-column structure, complementing its full
  constraint coverage.

The expected payload/hash is not hand-authored. During implementation, the
exact prestate migrations are applied to disposable PostgreSQL 16, the exact
committed migration 007 is applied, and the helper's resulting hash is frozen
in the corrective runner. A deterministic regression recreates that fixture
from committed SQL and must reproduce the frozen hash exactly. Runtime
post-007 classification requires exact hash equality; a mismatch is
`PARTIAL_OR_DRIFTED`.

Adversarial tests independently preserve an object's name while changing each
material class:

1. replace `is_operational_current_variant(TEXT)` with an always-true body;
2. recreate a required trigger on the wrong table or with a no-op function;
3. replace a required named constraint with validated `CHECK (TRUE)`; and
4. alter an operational view while retaining its name and a superficial call to
   `is_operational_current_variant`.

Each drift must change the signature and make C/D/E classification stop before
any permitted action. The untouched canonical migration must remain COMPLETE.
PostgreSQL major version 16 is already mandatory; any unforeseen minor-version
deparse difference fails closed rather than weakening attestation.

## 7. Validation and closeout

Raise only applicable module/global floors. Required validation includes the
new hostile-PATH and downstream-Git tests, exact marker tests, stale-state and
overlap tests, four schema-drift classes, canonical schema reproduction, prior
late-finalizer rollback, the complete corrective module, all existing Phase 4
tests, Phase 5 tests, startup hardening, the authoritative full suite,
compilation, `/bin/sh` syntax, pinned `uv 0.12.3` lock validation,
`git diff --check`, and secret/generated-artifact scans.

`docs/CODEX_HANDOFF.md` will record the REQUEST CHANGES verdict, four findings,
implementation and machine evidence, and the exact absolute-shell deployment
template with placeholders only. Phase 4 published-production correction stays
OPEN and Phase 6 stays OWNER AUTHORIZED / PAUSED. The branch is pushed without a
PR; no deployment or production execution occurs.

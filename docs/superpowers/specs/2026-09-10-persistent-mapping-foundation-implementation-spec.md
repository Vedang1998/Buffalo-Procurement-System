# First Persistent Mapping Foundation — Construction-Ready Implementation Specification

Status: **REMEDIATED PROPOSAL / DESIGN ONLY / NOT AUTHORIZED FOR IMPLEMENTATION OR MIGRATION EXECUTION**

Prepared: 2026-09-10; seven-finding documentation remediation: 2026-09-11

Preserved parent: `codex/supplier-mapping-review-policy-followup` at
`a056e111e2f21b96a9452be9a559e10f03805a6f`, tree
`22552b1460a360823b3f35558a995855af887596`

Design branch: `codex/persistent-mapping-foundation-design`

This specification turns Step 1 of the reviewed persistent-multi-offer design
into one construction-ready package. It does not replace
`2026-09-08-persistent-multi-offer-mapping-authority-design.md`. That reviewed
document continues to control the later pricing, carry-forward, deal-overlay,
policy-execution, recommendation-cutover, and Shopify work.

No SQL below is in `procurement/db`, no migration number is assigned, and no
runtime/configuration/authority byte is changed by this design branch. The
sealed V5 package remains immutable `REVIEW ONLY / NOT_APPROVED /
NOT_IMPORT_READY` evidence.

The 2026-09-11 remediation accepts five P1 findings and two P2 corrections as
design defects. It specifies proposed controls and future tests; it does not
claim that the SQL, runner amendment, role topology, or concurrency behavior
has been executed or accepted. Section 9 maps every accepted finding to its
correction and planned proof.

## 1. Exact integration dependency and target

### Verified refs and current gap

| Ref | Verified commit/tree | Meaning |
|---|---|---|
| Preserved source branch, local/upstream/live remote | `a056e111e2f21b96a9452be9a559e10f03805a6f` / `22552b1460a360823b3f35558a995855af887596` | Source of this design package; it remains untouched |
| Live `origin/main` | `f308ac666a2377f540e528bc873463daecc20cf8` / `0a8a2ea80721a97858c2120545d1e6b6f3805247` | Current proposed integration base; the local branch named `main` is divergent and must not be used |
| Monday foundation review head | `88bf800` | Reviewed unmerged foundation through migrations 008–013 |
| PR #23 integration candidate | `ec71fe9c5a6f13832a8cad65b065be9747010486` / `543be91aee06386b7889a1fc4198eaafe89e74df` | Merge of `88bf800` with `f308ac6`; draft and not yet acceptable as an implementation base |

The merge base of `origin/main` and the preserved design lineage is
`1920a16a6dc13a1b4357315f5049b938cbe7c0e2`. Main has three unique commits and
the design lineage has 39. Therefore neither this document nor the newest
reader/follow-up commits are standalone cherry-picks onto main.

The local branch named `main` is
`4bde08152cf958dc97686e01a4f27d83fdb4961f`, tree
`3bd9063502f782ebd93c3a4ed65130b73220ad62`, with two local-only and 52
`origin/main`-only commits. It is diagnostic evidence only and must not be used
as an integration target.

PR #23 is open and draft. Its current public metadata reports 14 commits, 65
files, `+20,781/-146`, `mergeable=true`, `rebaseable=false`, and
`mergeable_state=unstable`; its body still describes the older `88bf800`
source and pre-integration counts. Procurement CI run `34185466802` completed
with failure: startup passed, the full-suite job exited 1 after 959 seconds,
and the public metadata does not expose the failure cause. Detailed logs were
previously admin-only (HTTP 403), and authenticated `gh` metadata is not
available in this environment. Those access failures were not repeated.

A three-way tree analysis predicts a conflict in `docs/CODEX_HANDOFF.md` when
joining the preserved lineage to either current main or the PR #23 candidate.
Files changed on both sides including `procurement/tools/run_tests.py` appear
auto-mergeable in that model, but this is not evidence that 39 commits are
independently cherry-pickable or behaviorally compatible.

### Exact prerequisite provenance

| Prerequisite | Reviewed/provenance commit(s) |
|---|---|
| Core catalog, vendor, offer, alias, rejection, and price contracts now on main | Original PostgreSQL contract `fa66a8e8b868ca50a6a375a8f9ba434402caed74`; Phase-4 offer/alias safeguards through `326ad7659f41e63c9353e9372e7f67b94af47357`; current main `f308ac666a2377f540e528bc873463daecc20cf8` |
| Monday design and PostgreSQL isolation | `7c24419db8075cc3069073590a1e83115e9a3b89`; `d0834a8c8cb729aec7a8fc0d77c79e9d9b508140` |
| Migration 008 inventory foundation | `955d4685ae757cd80f1bd0442e71d910ff2063b2` |
| Migration 009 vendor rules | `3c81704e500bcff085d366dfcbd0104422aa3e59` |
| Migration 010 PO ledger and referenced-offer protection | `7068f54fe2fb8b54397888aadba6990d3644b19a`, with contract/hardening `21e967e39aba14860f8ededaf47cd03cd9a02397` and `41a4df21d523ee45059b256090f0fdb47664827a` |
| Migration 011 price staging and priced-offer/vendor protections | `c042ad09ae1289168e826805118201c000b90131` |
| Migration 012 workflow/recommendation boundary | `4b342cf67ec1d488a2f84433042a468609624d84` |
| Migration 013 remediation and foundation review evidence | `dda6b0986710f032f05f50273527af160cacde5c`; reviewed/test evidence `e59ea665408cb881f25cff995cc2a6957fa59f94`; closeout `88bf800708881e5801a51d0cb165e84e8c8cf198` |
| Offline review bridge/read contract | Start `fa594b641aa47a107bc51c3ed504ff7974e93c25`; fail-closed hardening `bc160a71a1a56b8951b6c2f8cc52991c5a48176b`; reviewed implementation `ecc1835dc025c21c9c0e9c5879328a01b81c04dd`; closeout `2a7192ff16c86630f01300f493560ccee8d2685f` |
| V5 portable reader and sealed-package contract | Design `48f5b355dac9c3a5b3965f64c45e6fd9930ee706`; implementation/remediation `5dffb75591cfe1bc649dca2e5c3140c42e3c5807`, `72f2d12f67879153f728a628fe0392e00968e13c`, `21aa6fc803f9a4c0d6a7a47f617bcdf583ce3831`, `f7edd300264d45977e15fa143771c05cd48dbb11`; review checkpoint `bb0aaf3312742d59a1937d8538e729c7b3a5da99`; closeout `6528bc69b1a49c786d7a61fbf293989b6ec093f4` |
| Real A1 adapter | Design `7e57301bd2e4ee2c76245ece5ca1012396ce7601`; implementation `482b1e63d84bd4e76d8a75444d124d3e724b083d`; closeout `9ef51a2b7166df9ed58bc72c3f82f57ea8caeb55` |
| Reviewed persistent-policy revision, Packet A corrections, and closure protection | Design revision `db394295deafea53ac1d0eb944430b2d67b163ee`; corrections `ea9c50841bcf48bfbb1b57f237231a229681fcfe`; design correction `46c6eb41c6c93d8764f5cfcd5df637e86d7e7ca2`; handoff `6838ab3485c42c2b8b764a5d7aefe45a982f3106`; closure tests `438e416bc5ebec4ffc95cc1cef81669c41d64bef`; closeout `a056e111e2f21b96a9452be9a559e10f03805a6f` |

The current persistent design blob is
`362d37e9300a5ba7007bf5ca7308e09ad03d411d`. The implementation target must
retain that exact reviewed content until a separately approved amendment says
otherwise.

### One recommended future integration sequence

1. Make the Monday foundation an approved integration baseline. Refresh PR #23
   or create a separately authorized integration branch from the then-current
   approved main; deliberately preserve both handoff histories; diagnose the
   unexplained CI failure; and require green CI on the exact integrated tree.
   `ec71fe9` is the currently constructed candidate, not an acceptable
   implementation target while it remains draft, unstable, and non-green.
2. After that foundation lands, integrate and independently validate the
   dependent reader lineage in reviewed order:
   - offline bridge `fa594b6..2a7192f` (including fail-closed hardening
     `bc160a7` and reviewed implementation `ecc1835`);
   - V5 reader `48f5b35..6528bc6` (review checkpoint `bb0aaf3`);
   - A1 adapter `7e57301..9ef51a2`;
   - policy/follow-up and test closure `db39429..a056e11`.
   Resolve the handoff conflict intentionally, re-register exact test floors,
   and run the full suite on the integrated bytes.
3. Layer this design-package commit on that approved integrated target. Only
   then create a fresh implementation branch for the schema/domain-service
   slice below. Recheck the migration chain, assign the next number at that
   time, and obtain owner authorization before writing runtime SQL.

This sequence preserves the reviewed contract boundaries and avoids disguising
the unmerged foundation as a small mapping PR. No merge, rebase, cherry-pick,
PR mutation, or CI retry is part of this task.

## 2. First-slice object and lifecycle contract

### Four different facts

1. A **printed source occurrence** is one immutable candidate row with exact
   artifact hash, source table/file, page bounds, row/occurrence key,
   qualifiers, component relationships, owner clarifications, and historical
   capture scope. Repeated occurrences remain repeated evidence.
2. An **operational supplier offer** is the existing `supplier_offers` identity
   used by procurement. Its identity contract is Variant, vendor, exact
   supplier-code bytes, offer/package class, size/pack, conversion quantities,
   and assortment contract. Price tiers and printed occurrences are not new
   offer identities.
3. A **mapping decision** is an immutable approval, rejection, or deferral for
   one candidate and one exact decision scope. It may link several equivalent
   occurrences to the same offer. It cannot select, price, sync, or order.
4. A **selected routine offer** is the latest append-only selection event named
   by the one mutable `ROUTINE_PROCUREMENT_STANDARD` head for a Variant. It is a
   separate confirmation even when the same authenticated owner made the
   mapping decision.

`operational_offer_key_sha256` is calculated only when the candidate has the
minimum complete operational identity: canonical vendor and Variant, a
nonblank distributor product ID, known non-`UNKNOWN` offer class, a nonblank
package type, and every state/value pair needed by the operational contract.
It covers exact supplier-code state/bytes, package/size/pack/conversion,
assortment, qualifier, and component identity. A candidate that lacks those
facts has SQL null for this derived key; intake never invents an operational
identity merely to make a hash. The raw distributor-product identity remains
separate mapping evidence. The key excludes page, occurrence ordinal, price,
tier, and deal terms, so several tier rows can share one operational offer.
The service takes a session advisory lock on a nonnull key before offer lookup
or creation; the insert trigger retains a transaction-lock backstop. Any
historical `APPROVE_MAPPING` permanently binds that exact operational key to
its offer for identity reuse, even when a later DEFER or REJECT means the
approval is no longer effective authority. Reuse still requires a current
`LINKED_EXISTING` disposition and explicit confirmation of that exact offer;
effectiveness governs authority views, not historical identity. The database
refuses a second offer ID for the same key. Different regular, gift, special,
alternate, component, and combo identities remain separate.

A supplier code reused for a different product or contract never rewrites the
old offer. Human review may create a new **inactive** historical offer while
preserving the old identity and references. The existing partial unique index
`uq_active_vendor_supplier_sku` remains authoritative: two rows with the same
nonblank vendor/code may coexist only while at most one is active. The existing
PO-reference trigger from migration 010 and priced-offer/vendor protections
from migration 011 remain in force. The new mapping protection is additive.

### Exact first-slice lifecycle

| Step | Permitted result | Explicitly not implied |
|---|---|---|
| Intake | Immutable batch and candidates, including explicit nulls and absent fields | Approval, offer creation, selection, price, Shopify or order authority |
| `APPROVE_MAPPING` | Link an exact existing offer, or create one inactive exact-contract `supplier_offers` row in the same transaction | Offer activation, price creation/verification, routine selection, recommendation use |
| `REJECT_MAPPING` | For a candidate with an exact Variant/vendor and stable supplier identity, link a new active `mapping_rejections` row with exact negative evidence | A broad rejection when the target is unresolved; deleting a candidate or alternative |
| `DEFER` | Append the immutable batch/candidate reference, preserved source evidence and null states, reason, and authenticated human provenance; all unresolved operational/result fields remain SQL null | Inventing Variant/vendor/code/package/fingerprints/keys; offer, rejection, approval, selection, price, Shopify, or order effects |
| `SELECT` | Advance the Variant-wide head to an effective approved regular offer after a second preview and confirmation | Changing `supplier_offers.active`, creating prices, or changing recommendations |
| `CLEAR` | Advance the head to an explicit reviewed null | Deleting selection history or deactivating an offer |

An active exact legacy offer may be selected without changing its active state.
A newly approved inactive offer may also be selected, but the read model labels
it `SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION`. First-slice code must not
activate it. A later, separately authorized activation/cutover transaction must
revalidate active vendor/code uniqueness, protected references, mapping and
selection heads, price eligibility, and recommendation shadow parity before it
can change operational behavior. This is the defined transition; mapping and
selection do not activate pricing.

Accordingly, the current recommendation consumer in `recommendations._load_context`
continues to require exactly one active `STANDARD` offer. It does not query the
new views in this slice. Existing recommendations remain byte-for-byte and
behaviorally unchanged until a separately approved cutover.

## 3. Proposed migration identity and preconditions

The artifact below is named
`PROPOSED_UNNUMBERED_persistent_mapping_foundation.sql` here only. Under the
currently intended integrated chain its exact predecessor is
`013_monday_p1_remediation.sql`. Do not copy it into `procurement/db`, add it to
`MIGRATION_ORDER`, or assign a number until all of the following are true:

- the exact integrated target is approved and clean;
- `schema_postgres.sql` through `013_monday_p1_remediation.sql` are the complete
  predecessor chain with no intervening migration;
- `monday_price_book_contract='v2-future-only'` and
  `monday_p1_remediation_contract='v1'` survive integration;
- the resolved `digest(bytea,text)` function is the `pgcrypto` extension member
  from the integrated predecessor, not a search-path substitute;
- the migration 008 eligibility predicate, active vendor/SKU partial unique
  index, and migration 010/011 protection triggers exist with exact behavior;
- there are no partial objects from this contract;
- the finally assigned file name is exactly
  `NNN_persistent_mapping_foundation.sql`, where `NNN` is the next integrated
  number; the reapply guard requires it as the first post-013 marker and permits
  only strictly later, normally numbered migration markers;
- an installed copy's stored logical catalog signature exactly matches all new
  columns, constraints, indexes, functions, triggers, views, owners, and ACLs;
- a real historical upgrade fixture has been built from the exact predecessor,
  not from a consolidated schema containing the new objects.

If another migration lands after 013 first, stop, reassess dependencies, rename
this migration to the next available number, and update the expected marker set
and tests. Do not renumber an already published migration.

`apply_schema.py` already wraps each file and its migration-marker insertion in
one database transaction. Therefore the migration file must contain no
`BEGIN`, `COMMIT`, or autonomous transaction. The application operations below
each require their own explicit `SERIALIZABLE` transaction.

### Required checksum-pinned replay boundary

Publishing this migration also requires the following narrow runner amendment.
It is part of the first implementation change, not an edit made by this design
package. The existing runner re-executes every migration forever. That is safe
only while an old file continues to own the current form of every object it
creates; it would make this file reject or overwrite a later, intentional
`v2` contract transition before the later migration could run. Opt-in,
content-addressed replay fixes that without changing legacy migration behavior.

The future runner must contain a literal, reviewed
`PERSISTENT_MAPPING_RELEASE_MANIFEST`; it is not loaded from `meta`, generated
from the installed functions, or supplied by the caller. Each ordered release
record binds all of the following:

- contract family and exact version;
- required PostgreSQL major version (`16` for this contract);
- exact target-schema name and the expected schema-identity policy;
- transition migration name, its position in `MIGRATION_ORDER`, its SHA-256,
  and the immediately preceding reviewed release (or exact integrated-013
  predecessor for the first release);
- exact identities and independent catalog-property hashes for
  `assert_persistent_mapping_foundation_contract()` and
  `compute_persistent_mapping_catalog_sha256()`;
- the same hashes for every non-`pg_catalog` helper reachable by either anchor,
  including the mapping hash wrappers; and
- the allowed `pgcrypto` extension name/version, exact member-function
  signatures, and independently reviewed helper-schema binding.

The catalog-property hash is calculated in Python from a canonical tuple of
stable schema name, function name and identity arguments, result, `prokind`,
language, volatility, strictness, security-definer/leakproof/parallel flags,
symbolic owner classification, `proconfig`, `prosrc`, and `probin`. A database
schema OID is deliberately excluded because it cannot be derived from reviewed
source and varies across installations; the runner resolves that OID once and
uses it only to bind all catalog reads during one invocation. The hash is
populated from the reviewed migration source at publication and committed
beside the migration and runner change. It is never copied from
`pg_get_functiondef`, a mutable stored catalog signature, or either installed
anchor during acceptance.
Trusted queries use explicitly `pg_catalog`-qualified catalog objects and
functions. The extension helper must be the exact `pg_proc` member of the
reviewed `pgcrypto` `pg_extension` through `pg_depend`; a same-named helper is
not accepted.

The following AST-valid pseudocode fixes the future runner boundary. Helper
implementations omitted from the excerpt have the contracts stated immediately
below; none may consult an installed validator to obtain an expected value.

```python
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from psycopg import sql


@dataclass(frozen=True)
class TrustedFunction:
    identity: str
    catalog_property_sha256: str


@dataclass(frozen=True)
class MappingRelease:
    family: str
    version: str
    postgres_major: int
    target_schema: str
    migration_name: str
    migration_sha256: str
    predecessor_release: str | None
    assert_anchor: TrustedFunction
    compute_anchor: TrustedFunction
    helper_anchors: tuple[TrustedFunction, ...]
    pgcrypto_contract: tuple[str, str, tuple[str, ...]]


# The implementation commit publishes literal records here only after assigning
# the migration number on the approved integrated chain. Empty, placeholder,
# duplicate, unordered, or runtime-supplied records are a startup error.
PERSISTENT_MAPPING_RELEASE_MANIFEST: tuple[MappingRelease, ...]

_CHECKSUM_SKIP_HEADER = (
    b"-- buffalo-migration-replay: checksum-skip-v2\n"
    b"-- buffalo-contract-family: persistent-mapping-foundation\n"
)
_MAPPING_FAMILY_LOCK_PREFIX = "buffalo:migration:persistent-mapping-foundation"


def _validate_release_manifest(migration_order: list[str]) -> None:
    """Require literal hashes, one schema/family/major, and exact file order."""
    ...


def _mapping_release_for_file(name: str, raw_sql: bytes) -> MappingRelease | None:
    releases = {item.migration_name: item
                for item in PERSISTENT_MAPPING_RELEASE_MANIFEST}
    release = releases.get(name)
    has_header = raw_sql.startswith(_CHECKSUM_SKIP_HEADER)
    if release is None:
        if has_header or "persistent_mapping_" in name:
            raise RuntimeError(f"unmanifested mapping migration: {name}")
        return None
    if not has_header:
        raise RuntimeError(f"mapping migration lacks exact replay header: {name}")
    actual = hashlib.sha256(raw_sql).hexdigest()
    if actual != release.migration_sha256:
        raise RuntimeError(f"reviewed mapping migration bytes differ: {name}")
    return release


def _bind_target_schema(
    conn: Any,
    requested: str,
    release: MappingRelease,
    *,
    expected_oid: int | None = None,
) -> int:
    """Resolve one non-temporary schema by literal manifest name; return its OID."""
    if requested != release.target_schema:
        raise RuntimeError("requested target schema is not the reviewed release schema")
    if requested.startswith("pg_") or requested == "information_schema":
        raise RuntimeError("a system or temporary namespace cannot be the target")
    row = conn.execute(
        "SELECT n.oid, n.nspname, "
        "n.oid=pg_catalog.pg_my_temp_schema(), "
        "pg_catalog.pg_is_other_temp_schema(n.oid), "
        "n.nspname ~ '^pg_(toast_)?temp_[0-9]+$', "
        "pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE'), "
        "pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE') "
        "FROM pg_catalog.pg_namespace AS n WHERE n.nspname=%s",
        (requested,),
    ).fetchone()
    if row is None or row[1] != requested or any(row[2:5]):
        raise RuntimeError("reviewed target schema is absent or temporary")
    if not row[5] or not row[6]:
        raise RuntimeError("maintenance principal lacks target USAGE or CREATE")
    actual_oid = int(row[0])
    if expected_oid is not None and actual_oid != expected_oid:
        raise RuntimeError("target schema OID changed during runner invocation")
    return actual_oid


def _set_local_anchor_search_path(conn: Any, release: MappingRelease) -> None:
    """Use only independently bound schemas for mapping-family validation."""
    pgcrypto_schema = release.pgcrypto_contract[1]
    conn.execute(
        sql.SQL("SET LOCAL search_path = pg_catalog, {}, {}, pg_temp").format(
            sql.Identifier(release.target_schema),
            sql.Identifier(pgcrypto_schema),
        )
    )


def _set_local_legacy_search_path(
    conn: Any, release: MappingRelease, *, include_pgcrypto: bool
) -> None:
    """Keep legacy unqualified CREATE targets in the reviewed target schema."""
    # pg_catalog is intentionally omitted: PostgreSQL searches it implicitly
    # before explicit entries, while unqualified CREATE still targets the first
    # explicit valid schema. pg_temp is explicit and therefore cannot jump first.
    parts = [sql.Identifier(release.target_schema)]
    if (include_pgcrypto
            and release.pgcrypto_contract[1] != release.target_schema):
        parts.append(sql.Identifier(release.pgcrypto_contract[1]))
    parts.append(sql.SQL("pg_temp"))
    conn.execute(
        sql.SQL("SET LOCAL search_path = {}").format(sql.SQL(", ").join(parts))
    )


def _reject_pgcrypto_decoys_before_install(
    conn: Any,
    release: MappingRelease,
    *,
    caller_path_oids: tuple[int, ...],
) -> None:
    """Permit only a genuinely absent extension before schema_postgres installs it."""
    # Using explicit pg_catalog reads, require no pgcrypto extension, no
    # digest(bytea,text) lookalike in any schema, a non-temporary reviewed
    # destination schema equal to the target schema (the legacy CREATE
    # EXTENSION has no WITH SCHEMA clause), and no CREATE privilege there for
    # an untrusted role. Also reject an earlier caller-search-path schema
    # containing a same-name function. Nothing is resolved through the ambient
    # search_path.
    ...


def _pgcrypto_is_installed(conn: Any) -> bool:
    """Use pg_extension directly; do not resolve an extension member by name."""
    ...


def _verify_pgcrypto_contract(
    conn: Any,
    release: MappingRelease,
    *,
    caller_path_oids: tuple[int, ...],
) -> None:
    """Resolve exact extension/member signatures without ambient name lookup."""
    # Require the reviewed extension/version in the reviewed non-temp schema;
    # exact pg_depend extension membership and catalog properties for every
    # listed member; no other same-signature substitute in any earlier caller
    # search-path schema; no protected-signature object in the target unless
    # target is the verified extension namespace and that exact object is an
    # extension member; and no untrusted effective CREATE privilege on either
    # target or helper schema. Bind by namespace OID for this invocation, not
    # by a manifest OID. Unknown, duplicate, writable, or decoy state refuses.
    ...


def _verify_controlled_legacy_resolution(
    conn: Any, release: MappingRelease
) -> None:
    """Prove the path just set will create in target and call only real helpers."""
    # Require pg_catalog.current_schema() == the manifest target and compare
    # each unqualified protected signature under this controlled path to the
    # exact pg_depend-bound extension-member OID already verified above.
    # A silently omitted target or a target/helper decoy therefore refuses
    # before raw legacy SQL.
    ...


def _mapping_family_lock_key(target_schema: str) -> str:
    """Use the same version-independent, schema-scoped key as every SQL release."""
    return f"{_MAPPING_FAMILY_LOCK_PREFIX}:{target_schema}"


def _capture_caller_search_path_oids(conn: Any) -> tuple[int, ...]:
    """Capture the original effective path through qualified catalog queries."""
    # Resolve pg_catalog.current_schemas(true) to exact namespace OIDs before
    # any SET LOCAL. This is diagnostic input for decoy refusal only; it never
    # selects an authority object or target schema.
    ...


def _verify_server_major(conn: Any, release: MappingRelease) -> None:
    """Reject before catalog interpretation unless server_version_num is exact."""
    version_num = int(conn.execute(
        "SELECT pg_catalog.current_setting('server_version_num')"
    ).fetchone()[0])
    if version_num // 10000 != release.postgres_major:
        raise RuntimeError("PostgreSQL major is not the reviewed manifest major")


def _verify_release_anchors(
    conn: Any, release: MappingRelease, target_schema_oid: int
) -> None:
    """Fail closed using only manifest values and trusted pg_catalog reads."""
    # Query pg_proc/pg_namespace/pg_language/pg_depend/pg_extension directly;
    # require one exact overload; hash the complete property tuple in Python;
    # verify assert, compute, every enumerated helper, and pgcrypto membership.
    # No installed mapping function is invoked by this helper.
    ...


def _verify_effective_role_topology(
    conn: Any, *, target_schema_oid: int
) -> None:
    """Traverse SET-then-INHERIT paths and effective ACLs; never revoke."""
    # current_user/session_user are the explicitly invoked maintenance boundary;
    # superusers are trusted administrators. Every other LOGIN is untrusted.
    # From each login, recursively find roles reachable through only set_option
    # edges. From every such assumed role, recursively follow inherit_option
    # edges; reject if any effective role owns a protected object. This covers
    # pure SET, pure INHERIT, and SET-then-INHERIT combinations. Also query
    # effective schema CREATE, relation, column, and function privileges. Print
    # exact pg_auth_members paths. Custom GUC values are irrelevant.
    ...


def _observed_release_prefix(
    conn: Any, target_schema: str
) -> tuple[MappingRelease, ...]:
    """Reject unknown, missing-middle, duplicate, legacy, or mismatched markers."""
    # Compose the qualified meta identifier with sql.Identifier. Compare every
    # observed mapping marker/checksum with the ordered in-code manifest. The
    # result must be one exact prefix; database contract/version values do not
    # choose which manifest record is acceptable.
    ...


def _call_verified_assertion(conn: Any, release: MappingRelease) -> None:
    conn.execute(
        sql.SQL("SELECT {}.assert_persistent_mapping_foundation_contract()")
        .format(sql.Identifier(release.target_schema))
    )


def _publish_release_metadata(
    conn: Any,
    release: MappingRelease,
    actual_catalog: str,
    *,
    target_schema: str,
) -> None:
    """Advance exactly the reviewed predecessor; called after anchor checks."""
    if len(actual_catalog) != 64:
        raise RuntimeError("verified compute anchor returned a malformed signature")
    meta = sql.Identifier(target_schema)
    rows = dict(conn.execute(
        sql.SQL("SELECT key,value FROM {}.meta WHERE key=ANY(%s) FOR UPDATE")
        .format(meta),
        (["persistent_mapping_foundation_contract",
          "persistent_mapping_foundation_catalog_sha256"],),
    ).fetchall())
    prior = rows.get("persistent_mapping_foundation_contract")
    if prior != release.predecessor_release:
        raise RuntimeError("contract metadata is not the reviewed predecessor")
    if (prior is None) != (
        "persistent_mapping_foundation_catalog_sha256" not in rows
    ):
        raise RuntimeError("contract and catalog metadata are not co-present")
    for key, value in (
        ("persistent_mapping_foundation_contract", release.version),
        ("persistent_mapping_foundation_catalog_sha256", actual_catalog),
    ):
        conn.execute(
            sql.SQL("INSERT INTO {}.meta(key,value) VALUES (%s,%s) "
                    "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, "
                    "updated_at=pg_catalog.now()")
            .format(meta),
            (key, value),
        )


def apply_schema_connection(
    conn: Any, db_dir: Path, *, target_schema: str
) -> list[str]:
    """Apply files; serialize only the persistent-mapping migration family."""
    applied: list[str] = []
    if not PERSISTENT_MAPPING_RELEASE_MANIFEST:
        raise RuntimeError("reviewed mapping release manifest is absent")
    _validate_release_manifest(MIGRATION_ORDER)
    expected_release = PERSISTENT_MAPPING_RELEASE_MANIFEST[-1]
    with conn.transaction():
        _verify_server_major(conn, expected_release)
        caller_path_oids = _capture_caller_search_path_oids(conn)
        bound_schema_oid = _bind_target_schema(
            conn, target_schema, expected_release
        )
    for name in MIGRATION_ORDER:
        raw_sql = (db_dir / name).read_bytes()
        release = _mapping_release_for_file(name, raw_sql)
        with conn.transaction():
            schema_oid = _bind_target_schema(
                conn, target_schema, expected_release,
                expected_oid=bound_schema_oid,
            )
            if release is None:
                # schema_postgres.sql is the sole reviewed bootstrap allowed to
                # install an absent pgcrypto. Its DDL and the post-install
                # verification share this file transaction, so verification
                # failure rolls every bootstrap statement back before a marker.
                if name == "schema_postgres.sql":
                    pgcrypto_preexisted = _pgcrypto_is_installed(conn)
                    if pgcrypto_preexisted:
                        _verify_pgcrypto_contract(
                            conn, expected_release,
                            caller_path_oids=caller_path_oids,
                        )
                    else:
                        _reject_pgcrypto_decoys_before_install(
                            conn, expected_release,
                            caller_path_oids=caller_path_oids,
                        )
                    _set_local_legacy_search_path(
                        conn, expected_release,
                        include_pgcrypto=pgcrypto_preexisted,
                    )
                    if pgcrypto_preexisted:
                        _verify_controlled_legacy_resolution(
                            conn, expected_release
                        )
                    conn.execute(raw_sql.decode("utf-8"))
                    _verify_pgcrypto_contract(
                        conn, expected_release,
                        caller_path_oids=caller_path_oids,
                    )
                    _verify_controlled_legacy_resolution(
                        conn, expected_release
                    )
                else:
                    _verify_pgcrypto_contract(
                        conn, expected_release,
                        caller_path_oids=caller_path_oids,
                    )
                    _set_local_legacy_search_path(
                        conn, expected_release, include_pgcrypto=True
                    )
                    _verify_controlled_legacy_resolution(
                        conn, expected_release
                    )
                    conn.execute(raw_sql.decode("utf-8"))
                conn.execute(
                    sql.SQL("INSERT INTO {}.meta(key,value) VALUES (%s,'applied') "
                            "ON CONFLICT(key) DO UPDATE SET value='applied', "
                            "updated_at=pg_catalog.now()")
                    .format(sql.Identifier(target_schema)),
                    (f"migration:{name}",),
                )
                applied.append(name)
                continue

            # This transaction lock begins only here: schema through 013 may
            # already have executed and committed in this invocation.
            conn.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(%s,0))",
                (_mapping_family_lock_key(target_schema),),
            )
            schema_oid = _bind_target_schema(
                conn, target_schema, release, expected_oid=bound_schema_oid
            )
            _verify_pgcrypto_contract(
                conn, release, caller_path_oids=caller_path_oids
            )
            _set_local_anchor_search_path(conn, release)
            installed = _observed_release_prefix(conn, target_schema)
            marker_value = f"sha256:{release.migration_sha256}"
            if release in installed:
                current = installed[-1]
                _verify_pgcrypto_contract(
                    conn, current, caller_path_oids=caller_path_oids
                )
                _set_local_anchor_search_path(conn, current)
                _verify_release_anchors(conn, current, schema_oid)
                _verify_effective_role_topology(
                    conn, target_schema_oid=schema_oid
                )
                _call_verified_assertion(conn, current)
                continue
            if installed and release.predecessor_release != installed[-1].version:
                raise RuntimeError("mapping release predecessor is not installed")
            if not installed and release.predecessor_release is not None:
                raise RuntimeError("first observed mapping release is not v1")
            if installed:
                prior_release = installed[-1]
                _verify_pgcrypto_contract(
                    conn, prior_release, caller_path_oids=caller_path_oids
                )
                _set_local_anchor_search_path(conn, prior_release)
                _verify_release_anchors(conn, prior_release, schema_oid)
                _verify_effective_role_topology(
                    conn, target_schema_oid=schema_oid
                )
                _call_verified_assertion(conn, prior_release)
                _set_local_anchor_search_path(conn, release)

            conn.execute(raw_sql.decode("utf-8"))
            # The SQL has created/replaced anchors but has not called them or
            # published its signature/marker. Verify independent identities first.
            _verify_release_anchors(conn, release, schema_oid)
            _verify_effective_role_topology(conn, target_schema_oid=schema_oid)
            actual_catalog = conn.execute(
                sql.SQL("SELECT {}.compute_persistent_mapping_catalog_sha256()")
                .format(sql.Identifier(target_schema))
            ).fetchone()[0]
            _publish_release_metadata(
                conn, release, actual_catalog, target_schema=target_schema
            )
            _call_verified_assertion(conn, release)
            conn.execute(
                sql.SQL("INSERT INTO {}.meta(key,value) VALUES (%s,%s) "
                        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, "
                        "updated_at=pg_catalog.now()")
                .format(sql.Identifier(target_schema)),
                (f"migration:{name}", marker_value),
            )
            applied.append(name)
    return applied
```

`_observed_release_prefix` derives installed progress only from exact migration
names/checksums that are present in the reviewed in-code manifest. The highest
observed prefix entry is the only installed validator eligible to run. Mutable
`persistent_mapping_foundation_contract` metadata is validated by that release;
it cannot request an older acceptable anchor. The manifest's final entry in the
current checkout is the expected current release: a missing suffix is applied
in order, but an unknown version, unknown marker, missing middle marker,
checksum mismatch, inconsistent contract row, or absent anchor refuses.

On replay after a reviewed `v2`, the prefix ends at `v2`; when the loop visits
the historical v1 file it verifies the v2 anchors and skips v1 SQL. The v2
transition file later skips in the same way. Publishing v2 therefore requires a
new literal manifest entry and new reviewed function/property hashes in the
same commit as the new migration; v1 is not pinned forever and cannot overwrite
v2. A no-op assertion, compute function returning the stored signature,
coordinated replacement of both, or same-named helper substitute differs from
the manifest before either anchor is invoked.

The exact target schema is a manifest literal and runner argument, resolved to
one non-system, non-current-temp, non-other-temp OID with
`pg_catalog.pg_namespace`; the OID is invocation-local binding, never a
portable manifest hash input. The final numbered SQL contains literal
schema-qualified identifiers generated and reviewed before its checksum is
published; runtime textual substitution is forbidden. Calls and marker access
use `psycopg.sql.Identifier`, never caller interpolation. Every owned function
has a catalog-signed `SET search_path` containing only `pg_catalog`, the exact
target schema, the verified pgcrypto schema, and explicit `pg_temp` last, while
authority relations and helpers remain schema-qualified. Explicitly listing
`pg_temp` last prevents its normal implicit early placement, but qualification
is still required and is the primary defense. See PostgreSQL 16
[`search_path` behavior](https://www.postgresql.org/docs/16/runtime-config-client.html),
[`pg_extension`](https://www.postgresql.org/docs/16/catalog-pg-extension.html),
and [function information](https://www.postgresql.org/docs/16/functions-info.html).

Legacy schema-through-013 files still contain unqualified DDL, so their
separate compatibility path begins with the exact target schema, includes the
independently verified pgcrypto schema when installed, and lists `pg_temp`
last. It deliberately omits explicit `pg_catalog`: PostgreSQL searches the
catalog implicitly before explicit entries while an unqualified `CREATE`
continues to target the first explicit valid schema. Only
`schema_postgres.sql` may bootstrap a genuinely absent pgcrypto extension. The
runner first rejects same-signature decoys and untrusted effective `CREATE` on
the target and reviewed helper destination, requires maintenance-principal
`USAGE` and `CREATE`, proves `current_schema()` is the target, and compares
every protected unqualified helper resolution with its exact extension-member
OID. This includes a target schema that was absent from the caller's original
path: a protected-signature target decoy is still forbidden. The runner
executes that file transactionally and then
verifies extension identity/version/schema, `pg_depend` membership, function
properties, writable-schema exposure, and caller-path decoys before its marker
or commit. An already installed extension is verified before any legacy SQL;
every later file also verifies it first. Thus fresh installation is possible
without accepting ambient helper resolution, and a failed post-bootstrap check
rolls back the whole `schema_postgres.sql` file transaction. The runner also
compares `server_version_num` with the manifest's exact PostgreSQL major before
interpreting any release catalog contract.

The family transaction lock is deliberately not an invocation lock. Concurrent
runners may re-execute and commit legacy `schema_postgres.sql` through 013 before
they serialize at the first mapping release. The loser may therefore have made
the legacy runner's existing idempotent marker timestamp or DDL effects before
its mapping checksum refusal. The claim is only that no competing runner can
observe/publish mapping-family markers or execute mapping-family SQL out of
order. It is neither whole-chain serialization nor whole-invocation rollback.
The runner and every mapping SQL release use the same version-independent,
schema-scoped lock text
`buffalo:migration:persistent-mapping-foundation:<target-schema>`; the SQL lock
is therefore a defense-in-depth acquisition of the already selected family
boundary, not a different lock.

## 4. Exact proposed SQL

The following is the reviewable SQL proposed for the future migration. It is
intentionally outside the automatically discovered migration directory.

```sql
-- buffalo-migration-replay: checksum-skip-v2
-- buffalo-contract-family: persistent-mapping-foundation
-- PROPOSED_UNNUMBERED_persistent_mapping_foundation.sql
-- Exact predecessor under the reviewed chain: 013_monday_p1_remediation.sql.
-- apply_schema.py supplies the enclosing transaction and migration marker.
-- The design-only tokens "__BUFFALO_TARGET_SCHEMA__" / its
-- '__BUFFALO_TARGET_SCHEMA_TEXT__' literal and
-- "__BUFFALO_PGCRYPTO_SCHEMA__" / its
-- '__BUFFALO_PGCRYPTO_SCHEMA_TEXT__' literal MUST be replaced as whole tokens
-- with psycopg.sql.Identifier / psycopg.sql.Literal respectively before the
-- numbered file and checksum manifest are reviewed. Runtime rewriting and
-- residual tokens in a published migration are forbidden.

SET LOCAL search_path = pg_catalog,
    "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp;

SELECT pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
        'buffalo:migration:persistent-mapping-foundation:' ||
        '__BUFFALO_TARGET_SCHEMA_TEXT__', 0
    )
);

DO $bootstrap_preconditions$
DECLARE
    pgcrypto_schema CONSTANT TEXT := '__BUFFALO_PGCRYPTO_SCHEMA_TEXT__';
    resolved_digest REGPROCEDURE;
BEGIN
    resolved_digest := pg_catalog.to_regprocedure(pg_catalog.format(
        '%I.digest(bytea,text)',pgcrypto_schema
    ));
    IF resolved_digest IS NULL OR NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_depend d
          JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
         WHERE d.classid='pg_catalog.pg_proc'::regclass
           AND d.objid=resolved_digest::oid
           AND d.deptype='e' AND e.extname='pgcrypto'
    ) THEN
        RAISE EXCEPTION 'the resolved pgcrypto digest(bytea,text) member is absent';
    END IF;
END
$bootstrap_preconditions$;

DO $migration_preconditions$
DECLARE
    actual_markers TEXT[];
    expected_markers CONSTANT TEXT[] := ARRAY[
        'migration:001_v1_3_catalog_sales.sql',
        'migration:002_seed_import_records.sql',
        'migration:003_phase3_reconciliation.sql',
        'migration:004_identity_decision_invariants.sql',
        'migration:005_identity_investigation.sql',
        'migration:006_phase4_sales_backfill.sql',
        'migration:007_phase4_terminal_disposition.sql',
        'migration:008_monday_inventory_foundation.sql',
        'migration:009_monday_vendor_rules.sql',
        'migration:010_monday_po_ledger.sql',
        'migration:011_monday_price_book_staging.sql',
        'migration:012_monday_review_draft_packet.sql',
        'migration:013_monday_p1_remediation.sql',
        'migration:schema_postgres.sql'
    ];
    installed_contract TEXT;
    installed_catalog_sha256 TEXT;
    target_schema CONSTANT TEXT := '__BUFFALO_TARGET_SCHEMA_TEXT__';
    target_schema_oid OID;
BEGIN
    SELECT n.oid INTO target_schema_oid
      FROM pg_catalog.pg_namespace n WHERE n.nspname=target_schema;
    IF target_schema_oid IS NULL
       OR target_schema_oid=pg_catalog.pg_my_temp_schema()
       OR pg_catalog.pg_is_other_temp_schema(target_schema_oid)
       OR target_schema ~ '^pg_(toast_)?temp_[0-9]+$'
       OR pg_catalog.to_regclass(
              pg_catalog.format('%I.%I',target_schema,'meta')
          ) IS NULL THEN
        RAISE EXCEPTION 'persistent mapping migration requires an explicit target schema';
    END IF;
    SELECT value INTO installed_contract
      FROM "__BUFFALO_TARGET_SCHEMA__".meta WHERE key='persistent_mapping_foundation_contract';
    SELECT value INTO installed_catalog_sha256
      FROM "__BUFFALO_TARGET_SCHEMA__".meta
     WHERE key='persistent_mapping_foundation_catalog_sha256';
    SELECT array_agg(key ORDER BY key) INTO actual_markers
      FROM "__BUFFALO_TARGET_SCHEMA__".meta WHERE key LIKE 'migration:%';

    -- The manifest-aware runner must skip an installed release. This v1 body
    -- is an initial transition only and may not attest to or repair itself.
    IF installed_contract IS NOT NULL OR installed_catalog_sha256 IS NOT NULL THEN
        RAISE EXCEPTION
            'v1 mapping SQL cannot execute over installed contract metadata';
    END IF;
    PERFORM pg_catalog.set_config(
        'procurement.persistent_mapping_foundation_initial_install',
        'true',true
    );

    IF actual_markers IS DISTINCT FROM expected_markers THEN
        RAISE EXCEPTION
            'persistent mapping foundation requires exact predecessor 013; markers=%',
            actual_markers;
    END IF;
    IF (SELECT value FROM "__BUFFALO_TARGET_SCHEMA__".meta
         WHERE key='monday_price_book_contract') IS DISTINCT FROM 'v2-future-only'
       OR (SELECT value FROM "__BUFFALO_TARGET_SCHEMA__".meta
            WHERE key='monday_p1_remediation_contract') IS DISTINCT FROM 'v1' THEN
        RAISE EXCEPTION 'required Monday predecessor contracts differ';
    END IF;
    IF pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_mapping_review_batches')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_mapping_review_candidates')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_mapping_decisions')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_offer_selection_events')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_offer_selection_heads')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_effective_supplier_mapping_decisions')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_supplier_offer_selection_diagnostics')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_selected_standard_supplier_offers')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_supplier_offer_selection_shadow')) IS NOT NULL
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
             WHERE n.oid=target_schema_oid AND p.proname ~
               '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
          )
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
             WHERE c.relnamespace=target_schema_oid AND t.tgname IN (
                 'trg_protect_persistently_mapped_offer_contract',
                 'trg_protect_unactivated_mapped_offer_price',
                 'trg_protect_persistent_mapping_rejection_contract'
             )
          ) THEN
        RAISE EXCEPTION 'partial persistent mapping objects exist before v1 marker';
    END IF;

    IF to_regclass(format('%I.%I',target_schema,'variants')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'vendors')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_offers')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'mapping_rejections')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'prices')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'purchase_order_lines')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'procurement_recommendations')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'run_price_snapshots')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'combo_components')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'exceptions')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'price_book_staging_rows')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'price_book_validation_issues')) IS NULL
       OR to_regprocedure(format('%I.%I(text)',target_schema,'is_procurement_eligible_variant')) IS NULL
       OR to_regprocedure(format('%I.%I()',target_schema,'prevent_referenced_offer_identity_change')) IS NULL
       OR to_regprocedure(format('%I.%I()',target_schema,'protect_promoted_offer_contract')) IS NULL
       OR to_regprocedure(format('%I.%I()',target_schema,'protect_priced_vendor_contract')) IS NULL THEN
        RAISE EXCEPTION 'required catalog, offer, pricing, or reference contract is absent';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid=i.indexrelid
         WHERE c.relname='uq_active_vendor_supplier_sku'
           AND i.indrelid=to_regclass(format('%I.%I',target_schema,'supplier_offers'))
           AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive
           AND NOT i.indisprimary AND NOT i.indisexclusion AND i.indimmediate
           AND NOT i.indnullsnotdistinct
           AND i.indpred IS NOT NULL AND i.indexprs IS NULL
           AND i.indnkeyatts=2 AND i.indnatts=2
           AND pg_get_indexdef(i.indexrelid,1,true)='vendor_id'
           AND pg_get_indexdef(i.indexrelid,2,true)='supplier_sku'
           AND pg_get_expr(i.indpred,i.indrelid,false) IN (
               '((active = true) AND (supplier_sku IS NOT NULL) AND (supplier_sku <> ''''::text))',
               '(active AND (supplier_sku IS NOT NULL) AND (supplier_sku <> ''''::text))'
           )
    ) THEN
        RAISE EXCEPTION 'active vendor/supplier-code uniqueness contract is absent';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
         WHERE t.tgrelid=to_regclass(format('%I.%I',target_schema,'supplier_offers'))
           AND t.tgname='trg_prevent_referenced_offer_identity_change'
           AND t.tgfoid=to_regprocedure(format('%I.%I()',target_schema,'prevent_referenced_offer_identity_change'))
           AND t.tgtype=19 AND t.tgqual IS NULL
           AND NOT t.tgisinternal AND t.tgenabled='O'
           AND cardinality(t.tgattr)=9
           AND ARRAY(
               SELECT a.attname::text
                 FROM unnest(t.tgattr) AS changed_attribute(attnum)
                 JOIN pg_attribute a
                   ON a.attrelid=t.tgrelid
                  AND a.attnum=changed_attribute.attnum
                ORDER BY a.attname::text
           )=ARRAY[
               'confidence','package_type','qualifying_units_per_case',
               'raw_pack','shopify_units_per_case','size_text','supplier_sku',
               'variant_id','vendor_id'
           ]::text[]
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger t
         WHERE t.tgrelid=to_regclass(format('%I.%I',target_schema,'supplier_offers'))
           AND t.tgname='trg_protect_promoted_offer_contract'
           AND t.tgfoid=to_regprocedure(format('%I.%I()',target_schema,'protect_promoted_offer_contract'))
           AND t.tgtype=27 AND t.tgqual IS NULL
           AND cardinality(t.tgattr)=0
           AND NOT t.tgisinternal AND t.tgenabled='O'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger t
         WHERE t.tgrelid=to_regclass(format('%I.%I',target_schema,'vendors'))
           AND t.tgname='trg_protect_priced_vendor_contract'
           AND t.tgfoid=to_regprocedure(format('%I.%I()',target_schema,'protect_priced_vendor_contract'))
           AND t.tgtype=27 AND t.tgqual IS NULL
           AND cardinality(t.tgattr)=0
           AND NOT t.tgisinternal AND t.tgenabled='O'
    ) THEN
        RAISE EXCEPTION 'referenced/priced offer or vendor protection trigger is absent';
    END IF;
    IF (SELECT encode("__BUFFALO_PGCRYPTO_SCHEMA__".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
          FROM pg_proc p
         WHERE p.oid=to_regprocedure(format('%I.%I()',target_schema,'prevent_referenced_offer_identity_change'))
           AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
           AND p.prorettype='trigger'::regtype AND p.pronargs=0
           AND p.provolatile='v' AND NOT p.proisstrict AND NOT p.prosecdef
           AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM '0c8caf40ba425c3dbf847862195f3caf131ec1221bd4e0739e3cd5ffd81219aa'
       OR (SELECT encode("__BUFFALO_PGCRYPTO_SCHEMA__".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
             FROM pg_proc p
            WHERE p.oid=to_regprocedure(format('%I.%I()',target_schema,'protect_promoted_offer_contract'))
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
              AND p.prorettype='trigger'::regtype AND p.pronargs=0
              AND p.provolatile='v' AND NOT p.proisstrict AND NOT p.prosecdef
              AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM '1799ba81c390728e069c2a73b7814f9e2be9596b8e082dbadfe9288c556318d6'
       OR (SELECT encode("__BUFFALO_PGCRYPTO_SCHEMA__".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
             FROM pg_proc p
            WHERE p.oid=to_regprocedure(format('%I.%I()',target_schema,'protect_priced_vendor_contract'))
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
              AND p.prorettype='trigger'::regtype AND p.pronargs=0
              AND p.provolatile='v' AND NOT p.proisstrict AND NOT p.prosecdef
              AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM 'b2fd1ffccc54710d44d06050c884d2d31d6af5c6d3d409c70a43f23102f85589'
       OR (SELECT encode("__BUFFALO_PGCRYPTO_SCHEMA__".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
             FROM pg_proc p
            WHERE p.oid=to_regprocedure(format('%I.%I(text)',target_schema,'is_procurement_eligible_variant'))
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')
              AND p.prorettype='boolean'::regtype AND p.pronargs=1
              AND p.provolatile='s' AND NOT p.proisstrict AND NOT p.prosecdef
              AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM 'cde7b0dd0793ff9f8bc16cf42618d2f3542e2c442d2724a1b07d2f6fd170529a' THEN
        RAISE EXCEPTION 'critical predecessor function body or metadata differs';
    END IF;
END
$migration_preconditions$;

-- Installed catalog calculator. It is not trusted to attest to itself: the
-- runner verifies its manifest-bound source/properties/helpers before calling
-- it, and independently verifies the assertion before replay validation.
CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".compute_persistent_mapping_catalog_sha256()
RETURNS TEXT
LANGUAGE sql STABLE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $catalog_signature$
WITH target_namespace AS (
    SELECT n.oid,n.nspname,n.nspowner,n.nspacl
      FROM pg_catalog.pg_namespace n
     WHERE n.nspname='__BUFFALO_TARGET_SCHEMA_TEXT__'
), target_relations AS (
    SELECT c.oid,c.relname,c.relkind,c.relpersistence,c.relrowsecurity,
           c.relforcerowsecurity,c.relreplident,c.relowner,c.relacl
      FROM pg_class c
      JOIN target_namespace n ON n.oid=c.relnamespace
     WHERE TRUE
       AND c.relname IN (
           'supplier_mapping_review_batches',
           'supplier_mapping_review_candidates',
           'supplier_mapping_decisions',
           'supplier_offer_selection_events',
           'supplier_offer_selection_heads',
           'v_effective_supplier_mapping_decisions',
           'v_supplier_offer_selection_diagnostics',
           'v_selected_standard_supplier_offers',
           'v_supplier_offer_selection_shadow'
       )
), catalog_items AS (
    SELECT 'schema:'||n.nspname AS item_key,
           jsonb_build_object(
               'kind','schema','name',n.nspname,
               'owner',pg_get_userbyid(n.nspowner),
               'acl',COALESCE(n.nspacl::text,'')
           ) AS item
      FROM target_namespace n
    UNION ALL
    SELECT 'relation:'||r.relname AS item_key,
           jsonb_build_object(
               'kind','relation','name',r.relname,'relkind',r.relkind,
               'persistence',r.relpersistence,'row_security',r.relrowsecurity,
               'force_row_security',r.relforcerowsecurity,
               'replica_identity',r.relreplident,
               'owner',pg_get_userbyid(r.relowner),
               'acl',COALESCE(r.relacl::text,'')
           ) AS item
      FROM target_relations r
    UNION ALL
    SELECT 'column:'||r.relname||':'||lpad(a.attnum::text,4,'0'),
           jsonb_build_object(
               'kind','column','relation',r.relname,'position',a.attnum,
               'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
               'not_null',a.attnotnull,'identity',a.attidentity,
               'generated',a.attgenerated,'storage',a.attstorage,
               'compression',a.attcompression,
               'collation',CASE WHEN a.attcollation=0 THEN NULL
                                ELSE a.attcollation::regcollation::text END,
               'default',pg_get_expr(d.adbin,d.adrelid,false),
               'acl',COALESCE(a.attacl::text,'')
           )
      FROM target_relations r
      JOIN pg_attribute a ON a.attrelid=r.oid
      LEFT JOIN pg_attrdef d ON d.adrelid=r.oid AND d.adnum=a.attnum
     WHERE a.attnum>0 AND NOT a.attisdropped
    UNION ALL
    SELECT 'constraint:'||r.relname||':'||k.conname,
           jsonb_build_object(
               'kind','constraint','relation',r.relname,'name',k.conname,
               'type',k.contype,'deferrable',k.condeferrable,
               'initially_deferred',k.condeferred,'validated',k.convalidated,
               'definition',pg_get_constraintdef(k.oid,true)
           )
      FROM target_relations r
      JOIN pg_constraint k ON k.conrelid=r.oid
    UNION ALL
    SELECT 'index:'||r.relname||':'||c.relname,
           jsonb_build_object(
               'kind','index','relation',r.relname,'name',c.relname,
               'unique',i.indisunique,'primary',i.indisprimary,
               'exclusion',i.indisexclusion,'immediate',i.indimmediate,
               'nulls_not_distinct',i.indnullsnotdistinct,
               'valid',i.indisvalid,'ready',i.indisready,'live',i.indislive,
               'definition',pg_get_indexdef(i.indexrelid)
           )
      FROM target_relations r
      JOIN pg_index i ON i.indrelid=r.oid
      JOIN pg_class c ON c.oid=i.indexrelid
    UNION ALL
    SELECT 'trigger:'||c.relname||':'||t.tgname,
           jsonb_build_object(
               'kind','trigger','relation',c.relname,'name',t.tgname,
               'enabled',t.tgenabled,'internal',t.tgisinternal,
               'definition',pg_get_triggerdef(t.oid,true)
           )
      FROM pg_trigger t
      JOIN pg_class c ON c.oid=t.tgrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='__BUFFALO_TARGET_SCHEMA_TEXT__' AND NOT t.tgisinternal
       AND (
           t.tgrelid IN (SELECT oid FROM target_relations)
           OR t.tgname IN (
               'trg_protect_persistently_mapped_offer_contract',
               'trg_protect_unactivated_mapped_offer_price',
               'trg_protect_persistent_mapping_rejection_contract'
           )
       )
    UNION ALL
    SELECT 'function:'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',
           jsonb_build_object(
               'kind','function','name',p.proname,
               'arguments',pg_get_function_identity_arguments(p.oid),
               'result',pg_get_function_result(p.oid),'prokind',p.prokind,
               'language',l.lanname,'volatility',p.provolatile,
               'strict',p.proisstrict,'security_definer',p.prosecdef,
               'leakproof',p.proleakproof,'parallel',p.proparallel,
               'config',COALESCE(p.proconfig::text,''),
               'owner',pg_get_userbyid(p.proowner),
               'acl',COALESCE(p.proacl::text,''),'source',p.prosrc
           )
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
     WHERE n.nspname='__BUFFALO_TARGET_SCHEMA_TEXT__' AND p.proname ~
       '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    UNION ALL
    SELECT 'view:'||r.relname,
           jsonb_build_object(
               'kind','view','name',r.relname,
               'definition',pg_get_viewdef(r.oid,true)
           )
      FROM target_relations r WHERE r.relkind='v'
)
SELECT encode("__BUFFALO_PGCRYPTO_SCHEMA__".digest(convert_to(
           COALESCE(jsonb_agg(item ORDER BY item_key)::text,'[]'),'UTF8'
       ),'sha256'),'hex')
  FROM catalog_items
$catalog_signature$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_text_sha256(value TEXT)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$ SELECT encode("__BUFFALO_PGCRYPTO_SCHEMA__".digest(convert_to(value, 'UTF8'), 'sha256'), 'hex') $$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(value JSONB)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$ SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_text_sha256(value::text) $$;

CREATE TABLE IF NOT EXISTS "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches (
    review_batch_id UUID PRIMARY KEY,
    intake_idempotency_key UUID NOT NULL,
    source_package_kind TEXT NOT NULL CHECK (source_package_kind='SEALED_V5_REVIEW_PACKAGE'),
    source_package_id TEXT NOT NULL CHECK (btrim(source_package_id)<>''),
    source_revision TEXT NOT NULL CHECK (btrim(source_revision)<>''),
    source_artifact_ref TEXT NOT NULL CHECK (btrim(source_artifact_ref)<>''),
    source_artifact_sha256 TEXT NOT NULL CHECK (source_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    source_root_sha256 TEXT NOT NULL CHECK (source_root_sha256 ~ '^[0-9a-f]{64}$'),
    source_seal_sha256 TEXT NOT NULL CHECK (source_seal_sha256 ~ '^[0-9a-f]{64}$'),
    relationship_table_sha256 TEXT NOT NULL CHECK (relationship_table_sha256 ~ '^[0-9a-f]{64}$'),
    source_batch_sha256 TEXT NOT NULL CHECK (source_batch_sha256 ~ '^[0-9a-f]{64}$'),
    source_payload_sha256 TEXT NOT NULL CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_set_sha256 TEXT NOT NULL CHECK (candidate_set_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_count INTEGER NOT NULL CHECK (candidate_count>=0),
    supplier_period_scope JSONB NOT NULL CHECK (jsonb_typeof(supplier_period_scope)='object'),
    prerequisites JSONB NOT NULL CHECK (jsonb_typeof(prerequisites)='object'),
    structural_state TEXT NOT NULL CHECK (structural_state IN ('READY','BLOCKED')),
    source_evidence_state TEXT NOT NULL CHECK (source_evidence_state IN ('READY','BLOCKED')),
    semantic_state TEXT NOT NULL CHECK (semantic_state IN ('READY','BLOCKED')),
    source_authority_state TEXT NOT NULL DEFAULT 'NOT_APPROVED'
        CHECK (source_authority_state='NOT_APPROVED'),
    source_import_state TEXT NOT NULL DEFAULT 'NOT_IMPORT_READY'
        CHECK (source_import_state='NOT_IMPORT_READY'),
    source_is_simulation BOOLEAN NOT NULL DEFAULT FALSE,
    creator_principal_ref TEXT NOT NULL CHECK (btrim(creator_principal_ref)<>''),
    creator_role_ref TEXT NOT NULL CHECK (btrim(creator_role_ref)<>''),
    creator_authn_context_sha256 TEXT NOT NULL
        CHECK (creator_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_txid BIGINT NOT NULL DEFAULT txid_current(),
    CONSTRAINT uq_mapping_review_batches_idempotency
        UNIQUE (intake_idempotency_key)
);

CREATE TABLE IF NOT EXISTS "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates (
    review_batch_id UUID NOT NULL
        REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches(review_batch_id) ON DELETE RESTRICT,
    candidate_id UUID NOT NULL,
    occurrence_index INTEGER NOT NULL CHECK (occurrence_index>=1),
    occurrence_key TEXT NOT NULL CHECK (btrim(occurrence_key)<>''),
    printed_occurrence_sha256 TEXT NOT NULL
        CHECK (printed_occurrence_sha256 ~ '^[0-9a-f]{64}$'),
    supplier_identity_key_sha256 TEXT
        CHECK (supplier_identity_key_sha256 ~ '^[0-9a-f]{64}$'),
    operational_offer_key_sha256 TEXT
        CHECK (operational_offer_key_sha256 ~ '^[0-9a-f]{64}$'),
    decision_scope_sha256 TEXT NOT NULL
        CHECK (decision_scope_sha256 ~ '^[0-9a-f]{64}$'),
    source_table_name TEXT NOT NULL CHECK (btrim(source_table_name)<>''),
    source_row_key TEXT NOT NULL CHECK (btrim(source_row_key)<>''),
    source_file_name TEXT NOT NULL CHECK (btrim(source_file_name)<>''),
    source_file_sha256 TEXT NOT NULL CHECK (source_file_sha256 ~ '^[0-9a-f]{64}$'),
    source_page_start INTEGER CHECK (source_page_start IS NULL OR source_page_start>=1),
    source_page_end INTEGER CHECK (source_page_end IS NULL OR source_page_end>=1),
    source_locator JSONB NOT NULL CHECK (jsonb_typeof(source_locator)='object'),
    proposed_variant_id TEXT,
    proposed_vendor_id UUID,
    source_vendor_identity TEXT NOT NULL CHECK (btrim(source_vendor_identity)<>''),
    distributor_product_id_state TEXT NOT NULL
        CHECK (distributor_product_id_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    distributor_product_id_value TEXT,
    supplier_code_state TEXT NOT NULL
        CHECK (supplier_code_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    supplier_code_value TEXT,
    offer_class TEXT NOT NULL CHECK (offer_class IN
        ('REGULAR','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    occurrence_role TEXT NOT NULL CHECK (occurrence_role IN
        ('PRIMARY','TIER','REPEAT','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    package_type_state TEXT NOT NULL
        CHECK (package_type_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    package_type_value TEXT,
    size_state TEXT NOT NULL CHECK (size_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    size_value TEXT,
    raw_pack_state TEXT NOT NULL CHECK (raw_pack_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    raw_pack_value TEXT,
    physical_units_state TEXT NOT NULL
        CHECK (physical_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    physical_units_value NUMERIC(12,4),
    retail_pack_units_state TEXT NOT NULL
        CHECK (retail_pack_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    retail_pack_units_value NUMERIC(12,4),
    shopify_units_state TEXT NOT NULL
        CHECK (shopify_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    shopify_units_value NUMERIC(12,4),
    qualifying_units_state TEXT NOT NULL
        CHECK (qualifying_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    qualifying_units_value NUMERIC(12,4),
    assortment_scope_state TEXT NOT NULL
        CHECK (assortment_scope_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_scope_value TEXT,
    assortment_group_state TEXT NOT NULL
        CHECK (assortment_group_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_group_value TEXT,
    assortable_state TEXT NOT NULL
        CHECK (assortable_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortable_value BOOLEAN,
    identity_qualifiers JSONB NOT NULL CHECK (jsonb_typeof(identity_qualifiers)='object'),
    component_relationships JSONB NOT NULL CHECK (jsonb_typeof(component_relationships)='array'),
    independent_linkage_evidence JSONB NOT NULL
        CHECK (jsonb_typeof(independent_linkage_evidence)='array'),
    owner_clarifications JSONB NOT NULL CHECK (jsonb_typeof(owner_clarifications)='array'),
    historical_capture_scope JSONB NOT NULL CHECK (jsonb_typeof(historical_capture_scope)='object'),
    related_artifact_hashes JSONB NOT NULL CHECK (jsonb_typeof(related_artifact_hashes)='object'),
    blockers JSONB NOT NULL CHECK (jsonb_typeof(blockers)='array'),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    candidate_sha256 TEXT NOT NULL CHECK (candidate_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY (candidate_id),
    UNIQUE (review_batch_id,candidate_id),
    UNIQUE (review_batch_id,occurrence_index),
    UNIQUE (review_batch_id,occurrence_key),
    CHECK ((source_page_start IS NULL)=(source_page_end IS NULL)),
    CHECK (source_page_end IS NULL OR source_page_end>=source_page_start),
    CHECK ((distributor_product_id_state='VALUE')=(distributor_product_id_value IS NOT NULL)),
    CHECK ((supplier_code_state='VALUE')=(supplier_code_value IS NOT NULL)),
    CHECK ((package_type_state='VALUE')=(package_type_value IS NOT NULL)),
    CHECK ((size_state='VALUE')=(size_value IS NOT NULL)),
    CHECK ((raw_pack_state='VALUE')=(raw_pack_value IS NOT NULL)),
    CHECK ((physical_units_state='VALUE')=(physical_units_value IS NOT NULL)),
    CHECK ((retail_pack_units_state='VALUE')=(retail_pack_units_value IS NOT NULL)),
    CHECK ((shopify_units_state='VALUE')=(shopify_units_value IS NOT NULL)),
    CHECK ((qualifying_units_state='VALUE')=(qualifying_units_value IS NOT NULL)),
    CHECK ((assortment_scope_state='VALUE')=(assortment_scope_value IS NOT NULL)),
    CHECK (assortment_scope_value IS NULL OR
           assortment_scope_value IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')),
    CHECK ((assortment_group_state='VALUE')=(assortment_group_value IS NOT NULL)),
    CHECK ((assortable_state='VALUE')=(assortable_value IS NOT NULL)),
    CHECK (physical_units_value IS NULL OR physical_units_value>0),
    CHECK (retail_pack_units_value IS NULL OR retail_pack_units_value>0),
    CHECK (shopify_units_value IS NULL OR shopify_units_value>0),
    CHECK (qualifying_units_value IS NULL OR qualifying_units_value>0)
);

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_supplier_identity_key(
    c "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT CASE
      WHEN c.proposed_vendor_id IS NULL
        OR c.distributor_product_id_state<>'VALUE'
        OR btrim(c.distributor_product_id_value)='' THEN NULL
      ELSE "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','SUPPLIER_IDENTITY_V1',
        'proposed_vendor_id',c.proposed_vendor_id,
        'source_vendor_identity',c.source_vendor_identity,
        'distributor_product_id_state',c.distributor_product_id_state,
        'distributor_product_id_value',c.distributor_product_id_value,
        'supplier_code_state',c.supplier_code_state,
        'supplier_code_value',c.supplier_code_value
      ))
    END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_operational_offer_key(
    c "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT CASE
      WHEN "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_supplier_identity_key(c) IS NULL
        OR c.proposed_variant_id IS NULL
        OR c.offer_class='UNKNOWN'
        OR c.package_type_state<>'VALUE'
        OR btrim(c.package_type_value)=''
        OR (c.supplier_code_state='VALUE' AND (
             btrim(c.supplier_code_value)=''
             OR c.supplier_code_value<>btrim(c.supplier_code_value)
           ))
        OR c.assortment_scope_state<>'VALUE' THEN NULL
      ELSE "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','OPERATIONAL_OFFER_V1',
        'proposed_vendor_id',c.proposed_vendor_id,
        'supplier_code_state',c.supplier_code_state,
        'supplier_code_value',c.supplier_code_value,
        'proposed_variant_id',c.proposed_variant_id,
        'offer_class',c.offer_class,
        'package_type_state',c.package_type_state,
        'package_type_value',c.package_type_value,
        'size_state',c.size_state,'size_value',c.size_value,
        'raw_pack_state',c.raw_pack_state,'raw_pack_value',c.raw_pack_value,
        'physical_units_state',c.physical_units_state,
        'physical_units_value',c.physical_units_value,
        'retail_pack_units_state',c.retail_pack_units_state,
        'retail_pack_units_value',c.retail_pack_units_value,
        'shopify_units_state',c.shopify_units_state,
        'shopify_units_value',c.shopify_units_value,
        'qualifying_units_state',c.qualifying_units_state,
        'qualifying_units_value',c.qualifying_units_value,
        'assortment_scope_state',c.assortment_scope_state,
        'assortment_scope_value',c.assortment_scope_value,
        'assortment_group_state',c.assortment_group_state,
        'assortment_group_value',c.assortment_group_value,
        'assortable_state',c.assortable_state,
        'assortable_value',c.assortable_value,
        'identity_qualifiers',c.identity_qualifiers,
        'component_relationships',c.component_relationships
      ))
    END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_decision_scope(
    c "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','CANDIDATE_DECISION_SCOPE_V1',
        'review_batch_id',c.review_batch_id,
        'candidate_id',c.candidate_id
    ))
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_reviewed_facts(
    c "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
)
RETURNS JSONB
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT jsonb_build_object(
        'package_type_state',c.package_type_state,
        'package_type_value',c.package_type_value,
        'physical_units_state',c.physical_units_state,
        'physical_units_value',c.physical_units_value,
        'retail_pack_units_state',c.retail_pack_units_state,
        'retail_pack_units_value',c.retail_pack_units_value,
        'assortment_scope_state',c.assortment_scope_state,
        'assortment_scope_value',c.assortment_scope_value,
        'assortment_group_state',c.assortment_group_state,
        'assortment_group_value',c.assortment_group_value,
        'assortable_state',c.assortable_state,
        'assortable_value',c.assortable_value,
        'identity_qualifiers',c.identity_qualifiers,
        'independent_linkage_evidence',c.independent_linkage_evidence,
        'related_artifact_hashes',c.related_artifact_hashes,
        'source_locator',c.source_locator,
        'source_file_name',c.source_file_name,
        'source_file_sha256',c.source_file_sha256,
        'source_page_start',c.source_page_start,
        'source_page_end',c.source_page_end
    )
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_evidence_set_sha256(
    c "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','INDEPENDENT_LINKAGE_EVIDENCE_V1',
        'printed_source_sha256',c.source_file_sha256,
        'evidence',c.independent_linkage_evidence
    ))
$$;

CREATE INDEX IF NOT EXISTS idx_mapping_candidates_offer_key
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates(operational_offer_key_sha256);
CREATE INDEX IF NOT EXISTS idx_mapping_candidates_supplier_identity
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates(supplier_identity_key_sha256);
CREATE INDEX IF NOT EXISTS idx_mapping_candidates_proposed_identity
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates(proposed_vendor_id,proposed_variant_id);
CREATE INDEX IF NOT EXISTS idx_mapping_candidates_supplier_code
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates(proposed_vendor_id,supplier_code_value)
    WHERE supplier_code_state='VALUE';

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_catalog_fingerprint(wanted_variant_id TEXT)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'variant_id',v.variant_id,'shopify_gid',v.shopify_gid,
        'product_id',v.product_id,'product_gid',v.product_gid,
        'sku',v.sku,'barcode',v.barcode,'active',v.active,
        'identity_scope',v.identity_scope,'catalog_state',v.catalog_state,
        'source_snapshot',v.source_snapshot,'last_synced_at',v.last_synced_at
    )) FROM "__BUFFALO_TARGET_SCHEMA__".variants v WHERE v.variant_id=wanted_variant_id
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_vendor_fingerprint(wanted_vendor_id UUID)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'vendor_id',v.vendor_id,'vendor_name',v.vendor_name,'active',v.active,
        'order_day',v.order_day,'order_cycle_days',v.order_cycle_days,
        'lead_time_days',v.lead_time_days,'updated_at',v.updated_at
    )) FROM "__BUFFALO_TARGET_SCHEMA__".vendors v WHERE v.vendor_id=wanted_vendor_id
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_rejection_fingerprint(
    wanted_vendor_id UUID, wanted_source_key TEXT, excluded_rejection_id BIGINT
)
RETURNS TEXT
LANGUAGE sql STABLE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(COALESCE(jsonb_agg(
        jsonb_build_object(
            'rejection_id',r.rejection_id,'mapping_type',r.mapping_type,
            'source_key',r.source_key,'variant_id',r.rejected_variant_id,
            'vendor_id',r.vendor_id,'source_text',r.source_text,
            'evidence_json',r.evidence_json,'rejected_by',r.rejected_by,
            'rejected_at',r.rejected_at
        ) ORDER BY r.rejection_id
    ),'[]'::jsonb))
     FROM "__BUFFALO_TARGET_SCHEMA__".mapping_rejections r
     WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
       AND r.vendor_id IS NOT DISTINCT FROM wanted_vendor_id
       AND r.source_key=wanted_source_key
       AND (excluded_rejection_id IS NULL OR r.rejection_id<>excluded_rejection_id)
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_rejection_contract_fingerprint(
    wanted_rejection_id BIGINT
)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','PERSISTENT_MAPPING_REJECTION_V1',
        'mapping_type',r.mapping_type,'source_key',r.source_key,
        'rejected_variant_id',r.rejected_variant_id,'vendor_id',r.vendor_id,
        'source_text',r.source_text,'evidence_json',r.evidence_json,
        'rejected_by',r.rejected_by,'active',r.active
    )) FROM "__BUFFALO_TARGET_SCHEMA__".mapping_rejections r WHERE r.rejection_id=wanted_rejection_id
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_offer_fingerprint(wanted_offer_id BIGINT)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','SUPPLIER_OFFER_CONTRACT_V1',
        'variant_id',o.variant_id,'vendor_id',o.vendor_id,
        'supplier_sku',o.supplier_sku,'package_type',o.package_type,
        'size_text',o.size_text,'raw_pack',o.raw_pack,
        'shopify_units_per_case',o.shopify_units_per_case,
        'qualifying_units_per_case',o.qualifying_units_per_case,
        'assortment_scope',o.assortment_scope,'assortment_group',o.assortment_group,
        'assortable',o.assortable,'valid_from',o.valid_from,'valid_to',o.valid_to,
        'replaces_offer_id',o.replaces_offer_id,
        'source_file',o.source_file,'source_page',o.source_page,
        'confidence',o.confidence,'active',o.active
    )) FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offers o WHERE o.offer_id=wanted_offer_id
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_offer_has_prior_references(
    wanted_offer_id BIGINT
)
RETURNS BOOLEAN
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT
        EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".prices WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".purchase_order_lines WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".procurement_recommendations WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".run_price_snapshots WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".combo_components WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".exceptions WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".price_book_staging_rows WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".price_book_validation_issues WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offers
                    WHERE replaces_offer_id=wanted_offer_id)
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_human_context(
    expected_principal_ref TEXT,
    expected_role_ref TEXT,
    expected_authn_context_sha256 TEXT,
    expected_action TEXT
)
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
BEGIN
    IF expected_principal_ref IS NULL OR btrim(expected_principal_ref)=''
       OR expected_role_ref IS NULL OR btrim(expected_role_ref)=''
       OR expected_authn_context_sha256 IS NULL
       OR expected_authn_context_sha256 !~ '^[0-9a-f]{64}$'
       OR expected_action IS NULL OR btrim(expected_action)=''
       OR current_setting('procurement.principal_kind',true) IS DISTINCT FROM 'HUMAN'
       OR current_setting('procurement.principal_ref',true)
            IS DISTINCT FROM expected_principal_ref
       OR current_setting('procurement.authorized_role_ref',true)
            IS DISTINCT FROM expected_role_ref
       OR current_setting('procurement.authn_context_sha256',true)
            IS DISTINCT FROM expected_authn_context_sha256
       OR current_setting('procurement.authorized_action',true)
            IS DISTINCT FROM expected_action THEN
        RAISE EXCEPTION 'verified named-human authorization context is absent or differs';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_enabled_capability(
    expected_capability TEXT
)
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
BEGIN
    IF expected_capability NOT IN (
           'review_intake_writes_enabled',
           'human_mapping_writes_enabled',
           'policy_mapping_writes_enabled',
           'routine_selection_writes_enabled'
       )
       OR current_setting('procurement.enabled_capability',true)
            IS DISTINCT FROM expected_capability THEN
        RAISE EXCEPTION 'required server-enabled persistent-mapping capability is absent';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_policy_is_published(
    policy_ref TEXT,
    policy_version TEXT,
    publication_sha256 TEXT,
    evidence_set_sha256 TEXT
)
RETURNS BOOLEAN
LANGUAGE sql STABLE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$ SELECT FALSE $$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".reject_persistent_mapping_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is forbidden',TG_TABLE_NAME,TG_OP;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_mapping_review_batch_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE expected_payload JSONB;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'mapping review intake requires SERIALIZABLE isolation';
    END IF;
    IF NEW.created_txid<>txid_current() THEN
        RAISE EXCEPTION 'review batch transaction identity differs';
    END IF;
    PERFORM "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_enabled_capability(
        'review_intake_writes_enabled'
    );
    PERFORM "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_human_context(
        NEW.creator_principal_ref,NEW.creator_role_ref,
        NEW.creator_authn_context_sha256,'MAPPING_REVIEW_INTAKE'
    );
    expected_payload := to_jsonb(NEW)-ARRAY[
        'review_batch_id','intake_idempotency_key','canonical_payload',
        'payload_sha256','created_at','created_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'review batch canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_mapping_review_candidate_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE parent_txid BIGINT; expected_payload JSONB;
BEGIN
    SELECT created_txid INTO parent_txid
      FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches
     WHERE review_batch_id=NEW.review_batch_id FOR SHARE;
    IF parent_txid IS DISTINCT FROM txid_current()
       OR NEW.created_txid<>txid_current() THEN
        RAISE EXCEPTION 'batch and candidates must be created in one transaction';
    END IF;
    IF NEW.supplier_identity_key_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_supplier_identity_key(NEW)
       OR NEW.operational_offer_key_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_operational_offer_key(NEW)
       OR NEW.decision_scope_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_decision_scope(NEW) THEN
        RAISE EXCEPTION 'candidate identity, offer, or decision-scope key differs';
    END IF;
    expected_payload := to_jsonb(NEW)-ARRAY[
        'candidate_id','canonical_payload','candidate_sha256','created_at','created_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.candidate_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'candidate canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_mapping_review_candidate_set()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE actual_count BIGINT; actual_sha256 TEXT;
BEGIN
    SELECT count(*),"__BUFFALO_TARGET_SCHEMA__".persistent_mapping_text_sha256(COALESCE(
        string_agg(candidate_sha256||E'\n','' ORDER BY occurrence_index,candidate_id),''
    )) INTO actual_count,actual_sha256
      FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
     WHERE review_batch_id=NEW.review_batch_id;
    IF actual_count<>NEW.candidate_count
       OR actual_sha256 IS DISTINCT FROM NEW.candidate_set_sha256 THEN
        RAISE EXCEPTION 'review batch candidate count or set fingerprint differs';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_mapping_review_batch_insert
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches;
CREATE TRIGGER trg_validate_mapping_review_batch_insert
BEFORE INSERT ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_mapping_review_batch_insert();
DROP TRIGGER IF EXISTS trg_validate_mapping_review_candidate_insert
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates;
CREATE TRIGGER trg_validate_mapping_review_candidate_insert
BEFORE INSERT ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_mapping_review_candidate_insert();
DROP TRIGGER IF EXISTS trg_validate_mapping_review_candidate_set
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches;
CREATE CONSTRAINT TRIGGER trg_validate_mapping_review_candidate_set
AFTER INSERT ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_mapping_review_candidate_set();

DROP TRIGGER IF EXISTS trg_immutable_mapping_review_batches
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches;
CREATE TRIGGER trg_immutable_mapping_review_batches
BEFORE UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".reject_persistent_mapping_mutation();
DROP TRIGGER IF EXISTS trg_immutable_mapping_review_candidates
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates;
CREATE TRIGGER trg_immutable_mapping_review_candidates
BEFORE UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".reject_persistent_mapping_mutation();

CREATE TABLE IF NOT EXISTS "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions (
    mapping_decision_id UUID PRIMARY KEY,
    decision_idempotency_key UUID NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    review_batch_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    decision_scope_sha256 TEXT NOT NULL CHECK (decision_scope_sha256 ~ '^[0-9a-f]{64}$'),
    supplier_identity_key_sha256 TEXT
        CHECK (supplier_identity_key_sha256 ~ '^[0-9a-f]{64}$'),
    operational_offer_key_sha256 TEXT
        CHECK (operational_offer_key_sha256 ~ '^[0-9a-f]{64}$'),
    action TEXT NOT NULL CHECK (action IN ('APPROVE_MAPPING','REJECT_MAPPING','DEFER')),
    decision_origin TEXT NOT NULL CHECK (decision_origin IN ('HUMAN','POLICY')),
    authority_kind TEXT CHECK (authority_kind IN ('HUMAN_APPROVED','POLICY_APPROVED')),
    variant_id TEXT REFERENCES "__BUFFALO_TARGET_SCHEMA__".variants(variant_id) ON DELETE RESTRICT,
    vendor_id UUID REFERENCES "__BUFFALO_TARGET_SCHEMA__".vendors(vendor_id) ON DELETE RESTRICT,
    printed_occurrence_sha256 TEXT NOT NULL
        CHECK (printed_occurrence_sha256 ~ '^[0-9a-f]{64}$'),
    distributor_product_id_state TEXT NOT NULL
        CHECK (distributor_product_id_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    distributor_product_id_value TEXT,
    supplier_code_state TEXT NOT NULL
        CHECK (supplier_code_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    supplier_code_value TEXT,
    offer_class TEXT NOT NULL CHECK (offer_class IN
        ('REGULAR','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    result_offer_package_type TEXT CHECK (
        result_offer_package_type IS NULL OR btrim(result_offer_package_type)<>''
    ),
    size_state TEXT NOT NULL CHECK (size_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    size_value TEXT,
    raw_pack_state TEXT NOT NULL CHECK (raw_pack_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    raw_pack_value TEXT,
    shopify_units_state TEXT NOT NULL
        CHECK (shopify_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    shopify_units_value NUMERIC(12,4),
    qualifying_units_state TEXT NOT NULL
        CHECK (qualifying_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    qualifying_units_value NUMERIC(12,4),
    assortment_scope_state TEXT NOT NULL
        CHECK (assortment_scope_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_scope_value TEXT,
    assortment_group_state TEXT NOT NULL
        CHECK (assortment_group_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_group_value TEXT,
    assortable_state TEXT NOT NULL
        CHECK (assortable_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortable_value BOOLEAN,
    reviewed_facts JSONB NOT NULL CHECK (jsonb_typeof(reviewed_facts)='object'),
    component_relationships JSONB NOT NULL CHECK (jsonb_typeof(component_relationships)='array'),
    owner_clarifications JSONB NOT NULL CHECK (jsonb_typeof(owner_clarifications)='array'),
    historical_capture_scope JSONB NOT NULL CHECK (jsonb_typeof(historical_capture_scope)='object'),
    expected_batch_payload_sha256 TEXT NOT NULL
        CHECK (expected_batch_payload_sha256 ~ '^[0-9a-f]{64}$'),
    expected_candidate_sha256 TEXT NOT NULL
        CHECK (expected_candidate_sha256 ~ '^[0-9a-f]{64}$'),
    expected_catalog_sha256 TEXT
        CHECK (expected_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    expected_vendor_sha256 TEXT
        CHECK (expected_vendor_sha256 ~ '^[0-9a-f]{64}$'),
    expected_rejection_memory_sha256 TEXT
        CHECK (expected_rejection_memory_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_set_sha256 TEXT NOT NULL CHECK (evidence_set_sha256 ~ '^[0-9a-f]{64}$'),
    human_principal_ref TEXT,
    human_role_ref TEXT,
    human_authn_context_sha256 TEXT,
    preview_sha256 TEXT,
    confirmation_sha256 TEXT,
    service_principal_ref TEXT,
    policy_ref TEXT,
    policy_version TEXT,
    policy_publication_sha256 TEXT,
    policy_predicate_version TEXT,
    policy_predicate_result_sha256 TEXT,
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    result_offer_id BIGINT REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_offers(offer_id) ON DELETE RESTRICT,
    offer_link_kind TEXT CHECK (offer_link_kind IN ('CREATED_INACTIVE','LINKED_EXISTING')),
    result_offer_contract_sha256 TEXT,
    result_rejection_id BIGINT REFERENCES "__BUFFALO_TARGET_SCHEMA__".mapping_rejections(rejection_id) ON DELETE RESTRICT,
    result_rejection_contract_sha256 TEXT,
    supersedes_mapping_decision_id UUID
        REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(mapping_decision_id) ON DELETE RESTRICT,
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    decided_txid BIGINT NOT NULL DEFAULT txid_current(),
    FOREIGN KEY (review_batch_id,candidate_id)
        REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates(review_batch_id,candidate_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_supplier_mapping_decisions_idempotency
        UNIQUE (decision_idempotency_key),
    UNIQUE (mapping_decision_id,result_offer_id),
    CHECK ((distributor_product_id_state='VALUE')=
           (distributor_product_id_value IS NOT NULL)),
    CHECK ((supplier_code_state='VALUE')=(supplier_code_value IS NOT NULL)),
    CHECK ((size_state='VALUE')=(size_value IS NOT NULL)),
    CHECK ((raw_pack_state='VALUE')=(raw_pack_value IS NOT NULL)),
    CHECK ((shopify_units_state='VALUE')=(shopify_units_value IS NOT NULL)),
    CHECK ((qualifying_units_state='VALUE')=(qualifying_units_value IS NOT NULL)),
    CHECK ((assortment_scope_state='VALUE')=(assortment_scope_value IS NOT NULL)),
    CHECK (assortment_scope_value IS NULL OR
           assortment_scope_value IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')),
    CHECK ((assortment_group_state='VALUE')=(assortment_group_value IS NOT NULL)),
    CHECK ((assortable_state='VALUE')=(assortable_value IS NOT NULL)),
    CHECK (shopify_units_value IS NULL OR shopify_units_value>0),
    CHECK (qualifying_units_value IS NULL OR qualifying_units_value>0),
    CHECK (human_authn_context_sha256 IS NULL OR
           human_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (preview_sha256 IS NULL OR preview_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (confirmation_sha256 IS NULL OR confirmation_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (policy_publication_sha256 IS NULL OR
           policy_publication_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (policy_predicate_result_sha256 IS NULL OR
           policy_predicate_result_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (result_offer_contract_sha256 IS NULL OR
           result_offer_contract_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (result_rejection_contract_sha256 IS NULL OR
           result_rejection_contract_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (
        (action='APPROVE_MAPPING'
         AND authority_kind IS NOT NULL
         AND variant_id IS NOT NULL AND vendor_id IS NOT NULL
         AND supplier_identity_key_sha256 IS NOT NULL
         AND operational_offer_key_sha256 IS NOT NULL
         AND result_offer_package_type IS NOT NULL
         AND expected_catalog_sha256 IS NOT NULL
         AND expected_vendor_sha256 IS NOT NULL
         AND expected_rejection_memory_sha256 IS NOT NULL
         AND result_offer_id IS NOT NULL
         AND offer_link_kind IS NOT NULL
         AND result_offer_contract_sha256 IS NOT NULL
         AND result_rejection_id IS NULL
         AND result_rejection_contract_sha256 IS NULL)
        OR
        (action='REJECT_MAPPING' AND authority_kind IS NULL
         AND variant_id IS NOT NULL AND vendor_id IS NOT NULL
         AND supplier_identity_key_sha256 IS NOT NULL
         AND result_offer_package_type IS NULL
         AND expected_catalog_sha256 IS NOT NULL
         AND expected_vendor_sha256 IS NOT NULL
         AND expected_rejection_memory_sha256 IS NOT NULL
         AND result_offer_id IS NULL AND offer_link_kind IS NULL
         AND result_offer_contract_sha256 IS NULL AND result_rejection_id IS NOT NULL
         AND result_rejection_contract_sha256 IS NOT NULL)
        OR
        (action='DEFER' AND authority_kind IS NULL
         AND variant_id IS NULL AND vendor_id IS NULL
         AND supplier_identity_key_sha256 IS NULL
         AND operational_offer_key_sha256 IS NULL
         AND result_offer_package_type IS NULL
         AND expected_catalog_sha256 IS NULL
         AND expected_vendor_sha256 IS NULL
         AND expected_rejection_memory_sha256 IS NULL
         AND result_offer_id IS NULL AND offer_link_kind IS NULL
         AND result_offer_contract_sha256 IS NULL AND result_rejection_id IS NULL
         AND result_rejection_contract_sha256 IS NULL)
    ),
    CHECK (
        (decision_origin='HUMAN'
         AND human_principal_ref IS NOT NULL AND btrim(human_principal_ref)<>''
         AND human_role_ref IS NOT NULL AND btrim(human_role_ref)<>''
         AND human_authn_context_sha256 IS NOT NULL
         AND preview_sha256 IS NOT NULL
         AND confirmation_sha256 IS NOT NULL
         AND service_principal_ref IS NULL AND policy_ref IS NULL
         AND policy_version IS NULL AND policy_publication_sha256 IS NULL
         AND policy_predicate_version IS NULL AND policy_predicate_result_sha256 IS NULL
         AND (authority_kind IS NULL OR authority_kind='HUMAN_APPROVED'))
        OR
        (decision_origin='POLICY' AND action='APPROVE_MAPPING'
         AND authority_kind='POLICY_APPROVED'
         AND human_principal_ref IS NULL AND human_role_ref IS NULL
         AND human_authn_context_sha256 IS NULL
         AND preview_sha256 IS NULL AND confirmation_sha256 IS NULL
         AND service_principal_ref IS NOT NULL AND btrim(service_principal_ref)<>''
         AND policy_ref IS NOT NULL AND btrim(policy_ref)<>''
         AND policy_version IS NOT NULL AND btrim(policy_version)<>''
         AND policy_publication_sha256 IS NOT NULL
         AND policy_predicate_version IS NOT NULL AND btrim(policy_predicate_version)<>''
         AND policy_predicate_result_sha256 IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_decision_preview_sha256(
    d "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(
        (to_jsonb(d)-ARRAY[
            'mapping_decision_id','request_sha256','canonical_payload','payload_sha256',
            'decided_at','decided_txid','human_principal_ref','human_role_ref',
            'human_authn_context_sha256','preview_sha256','confirmation_sha256',
            'service_principal_ref','policy_ref','policy_version',
            'policy_publication_sha256','policy_predicate_version',
            'policy_predicate_result_sha256','result_offer_id','result_rejection_id'
        ]) || jsonb_build_object(
            'confirmation_contract_version','HUMAN_MAPPING_PREVIEW_V1',
            'confirmed_existing_offer_id',CASE
                WHEN d.offer_link_kind='LINKED_EXISTING' THEN d.result_offer_id
                ELSE NULL
            END
        )
    )
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_decision_confirmation_sha256(
    d "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'confirmation_contract_version','HUMAN_MAPPING_CONFIRMATION_V1',
        'preview_sha256',d.preview_sha256,
        'principal_ref',d.human_principal_ref,
        'role_ref',d.human_role_ref,
        'action',d.action,
        'idempotency_key',d.decision_idempotency_key
    ))
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_decision_request_sha256(
    d "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(
        (to_jsonb(d)-ARRAY[
            'mapping_decision_id','request_sha256','canonical_payload',
            'payload_sha256','decided_at','decided_txid',
            'result_offer_id','result_rejection_id'
        ]) || jsonb_build_object(
            'request_contract_version','PERSISTENT_MAPPING_DECISION_REQUEST_V1',
            'confirmed_existing_offer_id',CASE
                WHEN d.offer_link_kind='LINKED_EXISTING' THEN d.result_offer_id
                ELSE NULL
            END
        )
    )
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_decision_root_per_scope
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(decision_scope_sha256)
    WHERE supersedes_mapping_decision_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_decision_successor
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(supersedes_mapping_decision_id)
    WHERE supersedes_mapping_decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_candidate
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(review_batch_id,candidate_id,decided_at);
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_offer_key
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(operational_offer_key_sha256,decided_at);
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_supplier_identity
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(supplier_identity_key_sha256,decided_at);
CREATE INDEX IF NOT EXISTS idx_mapping_decisions_result_offer
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(result_offer_id)
    WHERE result_offer_id IS NOT NULL;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_mapping_decision_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE
    b "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches%ROWTYPE;
    c "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates%ROWTYPE;
    o "__BUFFALO_TARGET_SCHEMA__".supplier_offers%ROWTYPE;
    prior "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions%ROWTYPE;
    expected_payload JSONB;
    rejection_source_key TEXT;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'mapping decisions require SERIALIZABLE isolation';
    END IF;
    PERFORM "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_enabled_capability(
        CASE NEW.decision_origin
          WHEN 'HUMAN' THEN 'human_mapping_writes_enabled'
          ELSE 'policy_mapping_writes_enabled'
        END
    );
    IF NEW.decided_txid<>txid_current() THEN
        RAISE EXCEPTION 'mapping decision transaction identity differs';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('supplier-mapping:'||NEW.decision_scope_sha256,0)
    );
    IF NEW.operational_offer_key_sha256 IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended('supplier-offer-key:'||NEW.operational_offer_key_sha256,0)
        );
    END IF;

    SELECT * INTO b FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches
     WHERE review_batch_id=NEW.review_batch_id FOR SHARE;
    SELECT * INTO c FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
     WHERE review_batch_id=NEW.review_batch_id AND candidate_id=NEW.candidate_id
     FOR SHARE;
    IF b.review_batch_id IS NULL OR c.candidate_id IS NULL THEN
        RAISE EXCEPTION 'decision batch or candidate is absent';
    END IF;

    IF NEW.expected_batch_payload_sha256 IS DISTINCT FROM b.payload_sha256
       OR NEW.expected_candidate_sha256 IS DISTINCT FROM c.candidate_sha256
       OR NEW.decision_scope_sha256 IS DISTINCT FROM c.decision_scope_sha256
       OR NEW.printed_occurrence_sha256 IS DISTINCT FROM c.printed_occurrence_sha256
       OR NEW.distributor_product_id_state IS DISTINCT FROM c.distributor_product_id_state
       OR NEW.distributor_product_id_value IS DISTINCT FROM c.distributor_product_id_value
       OR NEW.supplier_code_state IS DISTINCT FROM c.supplier_code_state
       OR NEW.supplier_code_value IS DISTINCT FROM c.supplier_code_value
       OR NEW.offer_class IS DISTINCT FROM c.offer_class
       OR NEW.size_state IS DISTINCT FROM c.size_state
       OR NEW.size_value IS DISTINCT FROM c.size_value
       OR NEW.raw_pack_state IS DISTINCT FROM c.raw_pack_state
       OR NEW.raw_pack_value IS DISTINCT FROM c.raw_pack_value
       OR NEW.shopify_units_state IS DISTINCT FROM c.shopify_units_state
       OR NEW.shopify_units_value IS DISTINCT FROM c.shopify_units_value
       OR NEW.qualifying_units_state IS DISTINCT FROM c.qualifying_units_state
       OR NEW.qualifying_units_value IS DISTINCT FROM c.qualifying_units_value
       OR NEW.assortment_scope_state IS DISTINCT FROM c.assortment_scope_state
       OR NEW.assortment_scope_value IS DISTINCT FROM c.assortment_scope_value
       OR NEW.assortment_group_state IS DISTINCT FROM c.assortment_group_state
       OR NEW.assortment_group_value IS DISTINCT FROM c.assortment_group_value
       OR NEW.assortable_state IS DISTINCT FROM c.assortable_state
       OR NEW.assortable_value IS DISTINCT FROM c.assortable_value
       OR NEW.reviewed_facts IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_reviewed_facts(c)
       OR NEW.evidence_set_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_candidate_evidence_set_sha256(c)
       OR NEW.component_relationships IS DISTINCT FROM c.component_relationships
       OR NEW.owner_clarifications IS DISTINCT FROM c.owner_clarifications
       OR NEW.historical_capture_scope IS DISTINCT FROM c.historical_capture_scope THEN
        RAISE EXCEPTION 'mapping preview is stale or candidate identity differs';
    END IF;
    IF NEW.action='DEFER' THEN
        IF NEW.variant_id IS NOT NULL OR NEW.vendor_id IS NOT NULL
           OR NEW.supplier_identity_key_sha256 IS NOT NULL
           OR NEW.operational_offer_key_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'defer cannot invent an operational identity';
        END IF;
    ELSIF NEW.variant_id IS DISTINCT FROM c.proposed_variant_id
       OR NEW.vendor_id IS DISTINCT FROM c.proposed_vendor_id
       OR NEW.supplier_identity_key_sha256 IS DISTINCT FROM
            c.supplier_identity_key_sha256
       OR NEW.operational_offer_key_sha256 IS DISTINCT FROM
            c.operational_offer_key_sha256 THEN
        RAISE EXCEPTION 'mapping action target differs from candidate';
    END IF;

    IF NEW.supersedes_mapping_decision_id IS NOT NULL THEN
        SELECT * INTO prior FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions
         WHERE mapping_decision_id=NEW.supersedes_mapping_decision_id FOR UPDATE;
        IF prior.mapping_decision_id IS NULL
           OR prior.decision_scope_sha256<>NEW.decision_scope_sha256
           OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions d
                       WHERE d.supersedes_mapping_decision_id=prior.mapping_decision_id) THEN
            RAISE EXCEPTION 'stale or wrong-scope prior mapping decision';
        END IF;
    END IF;

    IF NEW.decision_origin='HUMAN' THEN
        PERFORM "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_human_context(
            NEW.human_principal_ref,NEW.human_role_ref,
            NEW.human_authn_context_sha256,'SUPPLIER_MAPPING_DECIDE'
        );
        IF NEW.preview_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_decision_preview_sha256(NEW)
           OR NEW.confirmation_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_decision_confirmation_sha256(NEW) THEN
            RAISE EXCEPTION 'mapping preview or separate confirmation is not bound';
        END IF;
    ELSIF NEW.offer_class<>'REGULAR'
       OR NEW.result_offer_package_type<>'STANDARD'
       OR NEW.supplier_code_state<>'VALUE'
       OR btrim(NEW.supplier_code_value)=''
       OR NEW.supplier_code_value<>btrim(NEW.supplier_code_value) THEN
        RAISE EXCEPTION 'policy mapping is limited to an exact regular standard offer';
    ELSIF NOT "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_policy_is_published(
        NEW.policy_ref,NEW.policy_version,NEW.policy_publication_sha256,
        NEW.evidence_set_sha256
    ) THEN
        RAISE EXCEPTION 'no published mapping policy authorizes this decision';
    END IF;

    rejection_source_key := CASE
        WHEN NEW.supplier_identity_key_sha256 IS NULL THEN NULL
        ELSE 'persistent-mapping:'||NEW.supplier_identity_key_sha256
    END;
    IF NEW.action='APPROVE_MAPPING' THEN
        PERFORM 1 FROM "__BUFFALO_TARGET_SCHEMA__".variants WHERE variant_id=NEW.variant_id FOR SHARE;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".vendors v
             WHERE v.vendor_id=NEW.vendor_id AND v.active FOR SHARE
        ) THEN
            RAISE EXCEPTION 'mapping approval requires an active canonical Variant and vendor';
        END IF;
        IF NEW.expected_catalog_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_catalog_fingerprint(NEW.variant_id)
           OR NEW.expected_vendor_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_vendor_fingerprint(NEW.vendor_id)
           OR NEW.expected_rejection_memory_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_rejection_fingerprint(
                    NEW.vendor_id,rejection_source_key,NULL
                ) THEN
            RAISE EXCEPTION 'mapping approval catalog, vendor, or rejection preview is stale';
        END IF;
        IF b.structural_state<>'READY' OR b.source_evidence_state<>'READY'
           OR b.semantic_state<>'READY' OR b.source_is_simulation
           OR jsonb_array_length(c.blockers)<>0 THEN
            RAISE EXCEPTION 'blocked or simulated evidence cannot approve a mapping';
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM jsonb_array_elements(c.independent_linkage_evidence) evidence
             WHERE evidence->>'evidence_mode'='DETERMINISTIC_INDEPENDENT'
               AND evidence->>'origin_sha256' ~ '^[0-9a-f]{64}$'
               AND evidence->>'observation_sha256' ~ '^[0-9a-f]{64}$'
               AND evidence->>'origin_sha256'<>c.source_file_sha256
        ) OR EXISTS (
            SELECT 1
              FROM jsonb_array_elements(c.independent_linkage_evidence) evidence
             WHERE evidence->>'evidence_mode'='FUZZY_SCORE'
               AND evidence->>'authoritative'='true'
        ) THEN
            RAISE EXCEPTION
                'mapping approval requires independent deterministic evidence; fuzzy is never authority';
        END IF;
        IF NEW.distributor_product_id_state<>'VALUE'
           OR btrim(NEW.distributor_product_id_value)=''
           OR (NEW.supplier_code_state='VALUE' AND (
                btrim(NEW.supplier_code_value)=''
                OR NEW.supplier_code_value<>btrim(NEW.supplier_code_value)
              ))
           OR NEW.offer_class='UNKNOWN'
           OR NEW.assortment_scope_state<>'VALUE'
           OR NEW.result_offer_package_type IS DISTINCT FROM CASE NEW.offer_class
                WHEN 'REGULAR' THEN 'STANDARD'
                WHEN 'GIFT' THEN 'GIFT'
                WHEN 'SPECIAL' THEN 'SPECIAL'
                WHEN 'ALTERNATE' THEN 'ALTERNATE'
                WHEN 'COMPONENT' THEN 'COMPONENT'
                WHEN 'COMBO' THEN 'COMBO'
              END THEN
            RAISE EXCEPTION 'approval lacks a supported exact operational offer identity';
        END IF;
        IF NOT "__BUFFALO_TARGET_SCHEMA__".is_procurement_eligible_variant(NEW.variant_id) THEN
            RAISE EXCEPTION 'mapping approval requires an active canonical Variant and vendor';
        END IF;
        IF EXISTS (
            SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".mapping_rejections r
             WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
               AND r.vendor_id=NEW.vendor_id
               AND r.rejected_variant_id=NEW.variant_id
               AND r.source_key=rejection_source_key
        ) THEN
            RAISE EXCEPTION 'active rejection memory blocks mapping approval';
        END IF;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('supplier-offer-id:'||NEW.result_offer_id,0)
        );
        SELECT * INTO o FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offers
         WHERE offer_id=NEW.result_offer_id FOR SHARE;
        IF o.offer_id IS NULL
           OR o.variant_id<>NEW.variant_id OR o.vendor_id<>NEW.vendor_id
           OR o.supplier_sku IS DISTINCT FROM NEW.supplier_code_value
           OR o.package_type<>NEW.result_offer_package_type
           OR o.size_text IS DISTINCT FROM NEW.size_value
           OR o.raw_pack IS DISTINCT FROM NEW.raw_pack_value
           OR o.shopify_units_per_case IS DISTINCT FROM NEW.shopify_units_value
           OR o.qualifying_units_per_case IS DISTINCT FROM NEW.qualifying_units_value
           OR o.assortment_scope IS DISTINCT FROM NEW.assortment_scope_value
           OR o.assortment_group IS DISTINCT FROM NEW.assortment_group_value
           OR o.assortable IS DISTINCT FROM NEW.assortable_value
           OR o.confidence<>'VERIFIED'
           OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_offer_fingerprint(o.offer_id)
                IS DISTINCT FROM NEW.result_offer_contract_sha256 THEN
            RAISE EXCEPTION 'resulting supplier offer contract differs';
        END IF;
        IF NEW.offer_link_kind='CREATED_INACTIVE' AND (
            o.active
            OR o.source_file IS DISTINCT FROM c.source_file_name
            OR o.source_page IS DISTINCT FROM c.source_page_start
            OR o.valid_from IS NOT NULL OR o.valid_to IS NOT NULL
            OR o.replaces_offer_id IS NOT NULL
            OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_offer_has_prior_references(o.offer_id)
        ) THEN
            RAISE EXCEPTION 'mapping-created supplier offer must be inactive and unreferenced';
        END IF;
        IF EXISTS (
            SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions d
             WHERE d.action='APPROVE_MAPPING'
               AND d.operational_offer_key_sha256=NEW.operational_offer_key_sha256
               AND d.result_offer_id<>NEW.result_offer_id
        ) THEN
            RAISE EXCEPTION 'equivalent printed occurrences must share one operational offer';
        END IF;
        IF EXISTS (
            SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions d
             WHERE d.action='APPROVE_MAPPING'
               AND d.result_offer_id=NEW.result_offer_id
               AND d.operational_offer_key_sha256<>NEW.operational_offer_key_sha256
        ) THEN
            RAISE EXCEPTION 'one operational offer cannot represent different material identities';
        END IF;
    ELSIF NEW.action='REJECT_MAPPING' THEN
        PERFORM 1 FROM "__BUFFALO_TARGET_SCHEMA__".variants WHERE variant_id=NEW.variant_id FOR SHARE;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".vendors v WHERE v.vendor_id=NEW.vendor_id FOR SHARE
        ) OR NEW.distributor_product_id_state<>'VALUE'
          OR btrim(NEW.distributor_product_id_value)=''
          OR rejection_source_key IS NULL THEN
            RAISE EXCEPTION 'targeted rejection requires exact Variant, vendor, and supplier identity';
        END IF;
        IF NEW.expected_catalog_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_catalog_fingerprint(NEW.variant_id)
           OR NEW.expected_vendor_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_vendor_fingerprint(NEW.vendor_id)
           OR NEW.expected_rejection_memory_sha256 IS DISTINCT FROM
                "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_rejection_fingerprint(
                    NEW.vendor_id,rejection_source_key,NEW.result_rejection_id
                ) THEN
            RAISE EXCEPTION 'mapping rejection target or evidence preview is stale';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".mapping_rejections r
             WHERE r.rejection_id=NEW.result_rejection_id AND r.active
               AND r.mapping_type='SUPPLIER_OFFER'
               AND r.vendor_id=NEW.vendor_id
               AND r.rejected_variant_id=NEW.variant_id
               AND r.source_key=rejection_source_key
               AND r.rejected_by IS NOT DISTINCT FROM COALESCE(
                    NEW.human_principal_ref,NEW.service_principal_ref
               )
               AND "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_rejection_contract_fingerprint(r.rejection_id)
                    IS NOT DISTINCT FROM NEW.result_rejection_contract_sha256
        ) THEN
            RAISE EXCEPTION 'mapping rejection result differs from decision scope';
        END IF;
    END IF;

    expected_payload := to_jsonb(NEW)-ARRAY[
        'mapping_decision_id','decision_idempotency_key','canonical_payload',
        'payload_sha256','decided_at','decided_txid'
    ];
    IF NEW.request_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_decision_request_sha256(NEW)
       OR NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'mapping decision request, canonical payload, or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_supplier_mapping_decision_insert
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions;
CREATE TRIGGER trg_validate_supplier_mapping_decision_insert
BEFORE INSERT ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_mapping_decision_insert();
DROP TRIGGER IF EXISTS trg_immutable_supplier_mapping_decisions
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions;
CREATE TRIGGER trg_immutable_supplier_mapping_decisions
BEFORE UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".reject_persistent_mapping_mutation();

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".protect_persistent_mapping_rejection_contract()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions d
         WHERE d.action='REJECT_MAPPING'
           AND d.result_rejection_id=OLD.rejection_id
    ) THEN
        RAISE EXCEPTION 'rejection linked by persistent mapping authority is immutable';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_persistent_mapping_rejection_contract
    ON "__BUFFALO_TARGET_SCHEMA__".mapping_rejections;
CREATE TRIGGER trg_protect_persistent_mapping_rejection_contract
BEFORE UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".mapping_rejections
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".protect_persistent_mapping_rejection_contract();

CREATE TABLE IF NOT EXISTS "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events (
    selection_event_id UUID PRIMARY KEY,
    selection_idempotency_key UUID NOT NULL,
    variant_id TEXT NOT NULL REFERENCES "__BUFFALO_TARGET_SCHEMA__".variants(variant_id) ON DELETE RESTRICT,
    selection_scope TEXT NOT NULL DEFAULT 'ROUTINE_PROCUREMENT_STANDARD'
        CHECK (selection_scope='ROUTINE_PROCUREMENT_STANDARD'),
    action TEXT NOT NULL CHECK (action IN ('SELECT','CLEAR')),
    selected_offer_id BIGINT REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_offers(offer_id) ON DELETE RESTRICT,
    mapping_decision_id UUID,
    expected_prior_event_id UUID
        REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events(selection_event_id) ON DELETE RESTRICT,
    expected_prior_head_version BIGINT NOT NULL CHECK (expected_prior_head_version>=0),
    expected_mapping_decision_sha256 TEXT,
    expected_offer_contract_sha256 TEXT,
    expected_catalog_sha256 TEXT NOT NULL CHECK (expected_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    expected_vendor_sha256 TEXT,
    expected_rejection_memory_sha256 TEXT,
    effective_from DATE NOT NULL,
    effective_through DATE,
    human_principal_ref TEXT NOT NULL CHECK (btrim(human_principal_ref)<>''),
    human_role_ref TEXT NOT NULL CHECK (btrim(human_role_ref)<>''),
    human_authn_context_sha256 TEXT NOT NULL
        CHECK (human_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    preview_sha256 TEXT NOT NULL CHECK (preview_sha256 ~ '^[0-9a-f]{64}$'),
    confirmation_sha256 TEXT NOT NULL CHECK (confirmation_sha256 ~ '^[0-9a-f]{64}$'),
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    selected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    selected_txid BIGINT NOT NULL DEFAULT txid_current(),
    FOREIGN KEY (mapping_decision_id,selected_offer_id)
        REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions(mapping_decision_id,result_offer_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_supplier_offer_selection_events_idempotency
        UNIQUE (selection_idempotency_key),
    CHECK (effective_through IS NULL OR effective_through>=effective_from),
    CHECK (
        (action='SELECT' AND selected_offer_id IS NOT NULL
         AND mapping_decision_id IS NOT NULL
         AND expected_mapping_decision_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_offer_contract_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_vendor_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_rejection_memory_sha256 ~ '^[0-9a-f]{64}$')
        OR
        (action='CLEAR' AND selected_offer_id IS NULL
         AND mapping_decision_id IS NULL
         AND expected_mapping_decision_sha256 IS NULL
         AND expected_offer_contract_sha256 IS NULL
         AND expected_vendor_sha256 IS NULL
         AND expected_rejection_memory_sha256 IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads (
    variant_id TEXT NOT NULL REFERENCES "__BUFFALO_TARGET_SCHEMA__".variants(variant_id) ON DELETE RESTRICT,
    selection_scope TEXT NOT NULL DEFAULT 'ROUTINE_PROCUREMENT_STANDARD'
        CHECK (selection_scope='ROUTINE_PROCUREMENT_STANDARD'),
    selection_event_id UUID NOT NULL UNIQUE
        REFERENCES "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events(selection_event_id) ON DELETE RESTRICT,
    head_version BIGINT NOT NULL CHECK (head_version>=1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY (variant_id,selection_scope)
);

CREATE INDEX IF NOT EXISTS idx_offer_selection_events_offer
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events(selected_offer_id)
    WHERE selected_offer_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_offer_selection_events_mapping
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events(mapping_decision_id)
    WHERE mapping_decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_selection_preview_sha256(
    e "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(
        (to_jsonb(e)-ARRAY[
            'selection_event_id','canonical_payload','payload_sha256',
            'selected_at','selected_txid','human_principal_ref','human_role_ref',
            'human_authn_context_sha256','preview_sha256','confirmation_sha256'
        ]) || jsonb_build_object(
            'confirmation_contract_version','HUMAN_ROUTINE_SELECTION_PREVIEW_V1'
        )
    )
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_selection_confirmation_sha256(
    e "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
    SELECT "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(jsonb_build_object(
        'confirmation_contract_version','HUMAN_ROUTINE_SELECTION_CONFIRMATION_V1',
        'preview_sha256',e.preview_sha256,
        'principal_ref',e.human_principal_ref,
        'role_ref',e.human_role_ref,
        'action',e.action,
        'idempotency_key',e.selection_idempotency_key
    ))
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_offer_selection_event_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE
    current_head "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads%ROWTYPE;
    mapping "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions%ROWTYPE;
    offer "__BUFFALO_TARGET_SCHEMA__".supplier_offers%ROWTYPE;
    candidate "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates%ROWTYPE;
    expected_payload JSONB;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'routine selection requires SERIALIZABLE isolation';
    END IF;
    PERFORM "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_enabled_capability(
        'routine_selection_writes_enabled'
    );
    IF NEW.selected_txid<>txid_current() THEN
        RAISE EXCEPTION 'selection event transaction identity differs';
    END IF;
    PERFORM "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_require_human_context(
        NEW.human_principal_ref,NEW.human_role_ref,
        NEW.human_authn_context_sha256,'ROUTINE_OFFER_SELECT'
    );
    IF NEW.preview_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_selection_preview_sha256(NEW)
       OR NEW.confirmation_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_selection_confirmation_sha256(NEW) THEN
        RAISE EXCEPTION 'selection preview or separate confirmation is not bound';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('routine-offer-selection:'||NEW.variant_id,0)
    );
    SELECT * INTO current_head FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads
     WHERE variant_id=NEW.variant_id AND selection_scope=NEW.selection_scope
     FOR UPDATE;
    IF current_head.selection_event_id IS NULL THEN
        IF NEW.expected_prior_event_id IS NOT NULL
           OR NEW.expected_prior_head_version<>0 THEN
            RAISE EXCEPTION 'stale prior selection head';
        END IF;
    ELSIF NEW.expected_prior_event_id IS DISTINCT FROM current_head.selection_event_id
       OR NEW.expected_prior_head_version<>current_head.head_version THEN
        RAISE EXCEPTION 'stale prior selection head';
    END IF;
    IF NEW.expected_catalog_sha256 IS DISTINCT FROM
        "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_catalog_fingerprint(NEW.variant_id) THEN
        RAISE EXCEPTION 'selection catalog preview is stale';
    END IF;

    IF NEW.action='SELECT' THEN
        SELECT * INTO mapping FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions
         WHERE mapping_decision_id=NEW.mapping_decision_id FOR SHARE;
        SELECT * INTO offer FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offers
         WHERE offer_id=NEW.selected_offer_id FOR SHARE;
        SELECT * INTO candidate FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates
         WHERE candidate_id=mapping.candidate_id FOR SHARE;
        IF mapping.mapping_decision_id IS NULL OR mapping.action<>'APPROVE_MAPPING'
           OR mapping.variant_id<>NEW.variant_id
           OR NEW.selection_idempotency_key=mapping.decision_idempotency_key
           OR (mapping.decision_origin='HUMAN' AND (
                NEW.preview_sha256=mapping.preview_sha256
                OR NEW.confirmation_sha256=mapping.confirmation_sha256
              ))
           OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions successor
                       WHERE successor.supersedes_mapping_decision_id=mapping.mapping_decision_id)
           OR candidate.offer_class<>'REGULAR'
           OR mapping.result_offer_package_type<>'STANDARD'
           OR mapping.supplier_code_state<>'VALUE'
           OR offer.offer_id IS NULL OR offer.variant_id<>NEW.variant_id
           OR offer.package_type<>'STANDARD'
           OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(mapping.canonical_payload)
                IS DISTINCT FROM NEW.expected_mapping_decision_sha256
           OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_offer_fingerprint(offer.offer_id)
                IS DISTINCT FROM NEW.expected_offer_contract_sha256
           OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_vendor_fingerprint(mapping.vendor_id)
                IS DISTINCT FROM NEW.expected_vendor_sha256
           OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_rejection_fingerprint(
                mapping.vendor_id,
                'persistent-mapping:'||mapping.supplier_identity_key_sha256,
                NULL
              ) IS DISTINCT FROM NEW.expected_rejection_memory_sha256
           OR NOT "__BUFFALO_TARGET_SCHEMA__".is_procurement_eligible_variant(NEW.variant_id)
           OR NOT EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".vendors v
                           WHERE v.vendor_id=mapping.vendor_id AND v.active)
           OR (offer.valid_from IS NOT NULL AND offer.valid_from>NEW.effective_from)
           OR (offer.valid_to IS NOT NULL AND offer.valid_to<NEW.effective_from)
           OR EXISTS (
                SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".mapping_rejections r
                 WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
                   AND r.vendor_id=mapping.vendor_id
                   AND r.rejected_variant_id=NEW.variant_id
                   AND r.source_key='persistent-mapping:'||mapping.supplier_identity_key_sha256
              )
           OR EXISTS (
                SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offers reused
                 WHERE reused.vendor_id=mapping.vendor_id
                   AND reused.supplier_sku=mapping.supplier_code_value
                   AND reused.offer_id<>offer.offer_id
                   AND reused.active
              ) THEN
            RAISE EXCEPTION 'selected offer is stale, ineligible, rejected, or not regular';
        END IF;
        IF NOT offer.active AND (
            NOT EXISTS (
                SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions origin
                 WHERE origin.result_offer_id=offer.offer_id
                   AND origin.offer_link_kind='CREATED_INACTIVE'
            )
            OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_offer_has_prior_references(offer.offer_id)
        ) THEN
            RAISE EXCEPTION 'inactive selection is not a fresh unpriced mapping result';
        END IF;
    END IF;

    expected_payload := to_jsonb(NEW)-ARRAY[
        'selection_event_id','selection_idempotency_key','canonical_payload',
        'payload_sha256','selected_at','selected_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'selection canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_offer_selection_head_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE event "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'routine selection head cannot be deleted';
    END IF;
    IF NEW.updated_txid<>txid_current() THEN
        RAISE EXCEPTION 'selection head transaction identity differs';
    END IF;
    SELECT * INTO event FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events
     WHERE selection_event_id=NEW.selection_event_id;
    IF event.selection_event_id IS NULL OR event.selected_txid<>txid_current()
       OR event.variant_id<>NEW.variant_id
       OR event.selection_scope<>NEW.selection_scope
       OR NEW.head_version<>event.expected_prior_head_version+1 THEN
        RAISE EXCEPTION 'head must advance beside its exact new event';
    END IF;
    IF TG_OP='UPDATE' AND (
        ROW(NEW.variant_id,NEW.selection_scope)
            IS DISTINCT FROM ROW(OLD.variant_id,OLD.selection_scope)
        OR event.expected_prior_event_id IS DISTINCT FROM OLD.selection_event_id
        OR event.expected_prior_head_version<>OLD.head_version
    ) THEN
        RAISE EXCEPTION 'head update prior state differs';
    ELSIF TG_OP='INSERT' AND event.expected_prior_event_id IS NOT NULL THEN
        RAISE EXCEPTION 'initial head event unexpectedly has a predecessor';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_offer_selection_event_committed()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads h
         WHERE h.variant_id=NEW.variant_id
           AND h.selection_scope=NEW.selection_scope
           AND h.selection_event_id=NEW.selection_event_id
           AND h.head_version=NEW.expected_prior_head_version+1
    ) THEN
        RAISE EXCEPTION 'selection event has no same-transaction head advancement';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_offer_selection_event_insert
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events;
CREATE TRIGGER trg_validate_offer_selection_event_insert
BEFORE INSERT ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_offer_selection_event_insert();
DROP TRIGGER IF EXISTS trg_immutable_offer_selection_events
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events;
CREATE TRIGGER trg_immutable_offer_selection_events
BEFORE UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".reject_persistent_mapping_mutation();
DROP TRIGGER IF EXISTS trg_validate_offer_selection_head_change
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads;
CREATE TRIGGER trg_validate_offer_selection_head_change
BEFORE INSERT OR UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_offer_selection_head_change();
DROP TRIGGER IF EXISTS trg_validate_offer_selection_event_committed
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events;
CREATE CONSTRAINT TRIGGER trg_validate_offer_selection_event_committed
AFTER INSERT ON "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".validate_supplier_offer_selection_event_committed();

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".protect_persistently_mapped_offer_contract()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions d
         WHERE d.action='APPROVE_MAPPING' AND d.result_offer_id=OLD.offer_id
    ) AND (
        TG_OP='DELETE'
        OR ROW(NEW.variant_id,NEW.vendor_id,NEW.supplier_sku,NEW.package_type,
               NEW.size_text,NEW.raw_pack,NEW.shopify_units_per_case,
               NEW.qualifying_units_per_case,NEW.assortment_scope,
               NEW.assortment_group,NEW.assortable,NEW.valid_from,NEW.valid_to,
               NEW.replaces_offer_id,NEW.source_file,NEW.source_page,NEW.confidence)
           IS DISTINCT FROM
           ROW(OLD.variant_id,OLD.vendor_id,OLD.supplier_sku,OLD.package_type,
               OLD.size_text,OLD.raw_pack,OLD.shopify_units_per_case,
               OLD.qualifying_units_per_case,OLD.assortment_scope,
               OLD.assortment_group,OLD.assortable,OLD.valid_from,OLD.valid_to,
               OLD.replaces_offer_id,OLD.source_file,OLD.source_page,OLD.confidence)
        OR NEW.active IS DISTINCT FROM OLD.active
    ) THEN
        RAISE EXCEPTION
            'mapped offer identity/activation requires a separately approved transition';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".protect_unactivated_mapped_offer_price()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE checked_offer_id BIGINT;
BEGIN
    checked_offer_id := CASE WHEN TG_OP='DELETE' THEN OLD.offer_id ELSE NEW.offer_id END;
    IF EXISTS (
        SELECT 1
          FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions d
          JOIN "__BUFFALO_TARGET_SCHEMA__".supplier_offers o ON o.offer_id=d.result_offer_id
         WHERE d.action='APPROVE_MAPPING'
           AND d.offer_link_kind='CREATED_INACTIVE'
           AND d.result_offer_id=checked_offer_id
           AND NOT o.active
    ) THEN
        RAISE EXCEPTION 'inactive mapping result cannot receive operational pricing';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_persistently_mapped_offer_contract
    ON "__BUFFALO_TARGET_SCHEMA__".supplier_offers;
CREATE TRIGGER trg_protect_persistently_mapped_offer_contract
BEFORE UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".supplier_offers
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".protect_persistently_mapped_offer_contract();
DROP TRIGGER IF EXISTS trg_protect_unactivated_mapped_offer_price ON "__BUFFALO_TARGET_SCHEMA__".prices;
CREATE TRIGGER trg_protect_unactivated_mapped_offer_price
BEFORE INSERT OR UPDATE OR DELETE ON "__BUFFALO_TARGET_SCHEMA__".prices
FOR EACH ROW EXECUTE FUNCTION "__BUFFALO_TARGET_SCHEMA__".protect_unactivated_mapped_offer_price();

CREATE OR REPLACE VIEW "__BUFFALO_TARGET_SCHEMA__".v_effective_supplier_mapping_decisions AS
SELECT d.*
  FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions d
 WHERE NOT EXISTS (
    SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions successor
     WHERE successor.supersedes_mapping_decision_id=d.mapping_decision_id
 );

CREATE OR REPLACE VIEW "__BUFFALO_TARGET_SCHEMA__".v_supplier_offer_selection_diagnostics AS
SELECT
    h.variant_id,h.selection_scope,h.selection_event_id,h.head_version,
    e.action,e.selected_offer_id,e.mapping_decision_id,
    o.vendor_id,o.supplier_sku,o.package_type,o.active AS offer_active,
    CASE
      WHEN e.action='CLEAR' THEN 'EXPLICITLY_CLEARED'
      WHEN d.mapping_decision_id IS NULL THEN 'STALE_MAPPING_DECISION'
      WHEN "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_json_sha256(d.canonical_payload)
           IS DISTINCT FROM e.expected_mapping_decision_sha256
        THEN 'STALE_MAPPING_DECISION'
      WHEN o.offer_id IS NULL OR "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_offer_fingerprint(o.offer_id)
           IS DISTINCT FROM e.expected_offer_contract_sha256 THEN 'STALE_OFFER_CONTRACT'
      WHEN "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_catalog_fingerprint(h.variant_id)
           IS DISTINCT FROM e.expected_catalog_sha256 THEN 'STALE_CATALOG'
      WHEN "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_vendor_fingerprint(d.vendor_id)
           IS DISTINCT FROM e.expected_vendor_sha256 THEN 'STALE_VENDOR'
      WHEN "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_rejection_fingerprint(
               d.vendor_id,'persistent-mapping:'||d.supplier_identity_key_sha256,
               NULL
           ) IS DISTINCT FROM e.expected_rejection_memory_sha256
        THEN 'STALE_REJECTION_MEMORY'
      WHEN NOT "__BUFFALO_TARGET_SCHEMA__".is_procurement_eligible_variant(h.variant_id) THEN 'INELIGIBLE_VARIANT'
      WHEN NOT v.active THEN 'INACTIVE_VENDOR'
      WHEN EXISTS (
        SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".mapping_rejections r
         WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
           AND r.vendor_id=d.vendor_id AND r.rejected_variant_id=d.variant_id
           AND r.source_key='persistent-mapping:'||d.supplier_identity_key_sha256
      ) THEN 'ACTIVE_REJECTION'
      WHEN e.effective_from>current_date
        OR (e.effective_through IS NOT NULL AND e.effective_through<current_date)
        OR (o.valid_from IS NOT NULL AND o.valid_from>current_date)
        OR (o.valid_to IS NOT NULL AND o.valid_to<current_date) THEN 'OUTSIDE_VALIDITY'
      WHEN o.active THEN 'SELECTED_ACTIVE'
      ELSE 'SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION'
    END AS selection_state,
    FALSE AS recommendation_cutover_enabled
  FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads h
  JOIN "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events e
    ON e.selection_event_id=h.selection_event_id
  LEFT JOIN "__BUFFALO_TARGET_SCHEMA__".v_effective_supplier_mapping_decisions d
    ON d.mapping_decision_id=e.mapping_decision_id AND d.action='APPROVE_MAPPING'
  LEFT JOIN "__BUFFALO_TARGET_SCHEMA__".supplier_offers o ON o.offer_id=e.selected_offer_id
  LEFT JOIN "__BUFFALO_TARGET_SCHEMA__".vendors v ON v.vendor_id=o.vendor_id;

CREATE OR REPLACE VIEW "__BUFFALO_TARGET_SCHEMA__".v_selected_standard_supplier_offers AS
SELECT *
  FROM "__BUFFALO_TARGET_SCHEMA__".v_supplier_offer_selection_diagnostics
 WHERE selection_state IN
    ('SELECTED_ACTIVE','SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION');

CREATE OR REPLACE VIEW "__BUFFALO_TARGET_SCHEMA__".v_supplier_offer_selection_shadow AS
WITH legacy AS (
    SELECT o.variant_id,count(*) AS active_standard_count,
           min(o.offer_id) AS only_active_standard_offer_id
      FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offers o
     WHERE o.active AND o.package_type='STANDARD'
     GROUP BY o.variant_id
)
SELECT d.*,
       COALESCE(l.active_standard_count,0) AS legacy_active_standard_count,
       CASE
         WHEN d.selection_state='EXPLICITLY_CLEARED' THEN 'HEAD_CLEARED'
         WHEN d.selection_state NOT IN
              ('SELECTED_ACTIVE','SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION')
              THEN 'HEAD_STALE_OR_INELIGIBLE'
         WHEN NOT d.offer_active THEN 'SELECTED_INACTIVE_NO_LEGACY_CHANGE'
         WHEN l.active_standard_count=1
              AND l.only_active_standard_offer_id=d.selected_offer_id THEN 'MATCH'
         WHEN l.active_standard_count=0 THEN 'LEGACY_HAS_NO_ACTIVE_STANDARD'
         WHEN l.active_standard_count>1 THEN 'LEGACY_HAS_MULTIPLE_ACTIVE_STANDARD'
         ELSE 'DIFFERENT_ACTIVE_STANDARD'
       END AS shadow_comparison
  FROM "__BUFFALO_TARGET_SCHEMA__".v_supplier_offer_selection_diagnostics d
  LEFT JOIN legacy l ON l.variant_id=d.variant_id;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_assert_safe_role_topology()
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $role_topology$
DECLARE
    target_schema CONSTANT TEXT := '__BUFFALO_TARGET_SCHEMA_TEXT__';
    target_schema_oid OID;
    unsafe_path JSONB;
BEGIN
    SELECT n.oid INTO target_schema_oid
      FROM pg_catalog.pg_namespace n WHERE n.nspname=target_schema;
    IF target_schema_oid IS NULL
       OR target_schema_oid=pg_catalog.pg_my_temp_schema()
       OR pg_catalog.pg_is_other_temp_schema(target_schema_oid)
       OR target_schema ~ '^pg_(toast_)?temp_[0-9]+$' THEN
        RAISE EXCEPTION 'trusted target schema identity is absent or temporary';
    END IF;

    WITH RECURSIVE protected_owners(owner_oid) AS (
        SELECT n.nspowner FROM pg_catalog.pg_namespace n
         WHERE n.oid=target_schema_oid
        UNION
        SELECT c.relowner FROM pg_catalog.pg_class c
         WHERE c.relnamespace=target_schema_oid AND c.relname IN (
             'supplier_mapping_review_batches','supplier_mapping_review_candidates',
             'supplier_mapping_decisions','supplier_offer_selection_events',
             'supplier_offer_selection_heads','v_effective_supplier_mapping_decisions',
             'v_supplier_offer_selection_diagnostics',
             'v_selected_standard_supplier_offers',
             'v_supplier_offer_selection_shadow','variants','vendors',
             'supplier_offers','mapping_rejections','prices'
         )
        UNION
        SELECT p.proowner FROM pg_catalog.pg_proc p
         WHERE p.pronamespace=target_schema_oid AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    ), untrusted_logins AS (
        SELECT r.oid,r.rolname FROM pg_catalog.pg_roles r
         WHERE r.rolcanlogin AND NOT r.rolsuper
           AND r.oid<>pg_catalog.to_regrole(current_user)::oid
           AND r.oid<>pg_catalog.to_regrole(session_user)::oid
    ), set_reachable(login_oid,login_name,assumed_oid,set_path) AS (
        SELECT u.oid,u.rolname,u.oid,ARRAY[u.oid]::oid[]
          FROM untrusted_logins u
        UNION ALL
        SELECT s.login_oid,s.login_name,m.roleid,s.set_path||m.roleid
          FROM set_reachable s
          JOIN pg_catalog.pg_auth_members m ON m.member=s.assumed_oid
         WHERE m.set_option AND NOT m.roleid=ANY(s.set_path)
    ), effective_after_set(
        login_oid,login_name,assumed_oid,effective_oid,set_path,inherit_path
    ) AS (
        SELECT s.login_oid,s.login_name,s.assumed_oid,s.assumed_oid,
               s.set_path,ARRAY[s.assumed_oid]::oid[]
          FROM set_reachable s
        UNION ALL
        SELECT e.login_oid,e.login_name,e.assumed_oid,m.roleid,
               e.set_path,e.inherit_path||m.roleid
          FROM effective_after_set e
          JOIN pg_catalog.pg_auth_members m ON m.member=e.effective_oid
         WHERE m.inherit_option AND NOT m.roleid=ANY(e.inherit_path)
    )
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
               'login',e.login_name,
               'assumed_role',pg_catalog.pg_get_userbyid(e.assumed_oid),
               'owner',pg_catalog.pg_get_userbyid(o.owner_oid),
               'set_path',e.set_path,'inherit_path',e.inherit_path
           ) ORDER BY e.login_name,e.assumed_oid,o.owner_oid)
      INTO unsafe_path
      FROM effective_after_set e JOIN protected_owners o
        ON o.owner_oid=e.effective_oid;
    IF unsafe_path IS NOT NULL THEN
        RAISE EXCEPTION 'untrusted login has effective owner-role path: %',unsafe_path;
    END IF;

    WITH RECURSIVE untrusted_logins AS (
        SELECT r.oid,r.rolname FROM pg_catalog.pg_roles r
         WHERE r.rolcanlogin AND NOT r.rolsuper
           AND r.oid<>pg_catalog.to_regrole(current_user)::oid
           AND r.oid<>pg_catalog.to_regrole(session_user)::oid
    ), set_reachable(login_oid,login_name,assumed_oid,set_path) AS (
        SELECT u.oid,u.rolname,u.oid,ARRAY[u.oid]::oid[]
          FROM untrusted_logins u
        UNION ALL
        SELECT s.login_oid,s.login_name,m.roleid,s.set_path||m.roleid
          FROM set_reachable s
          JOIN pg_catalog.pg_auth_members m ON m.member=s.assumed_oid
         WHERE m.set_option AND NOT m.roleid=ANY(s.set_path)
    ), effective_principals(
        login_oid,login_name,effective_oid,set_path,inherit_path
    ) AS (
        SELECT s.login_oid,s.login_name,s.assumed_oid,
               s.set_path,ARRAY[s.assumed_oid]::oid[]
          FROM set_reachable s
        UNION ALL
        SELECT e.login_oid,e.login_name,m.roleid,
               e.set_path,e.inherit_path||m.roleid
          FROM effective_principals e
          JOIN pg_catalog.pg_auth_members m ON m.member=e.effective_oid
         WHERE m.inherit_option AND NOT m.roleid=ANY(e.inherit_path)
    ), protected_relations AS (
        SELECT c.oid,c.relname FROM pg_catalog.pg_class c
         WHERE c.relnamespace=target_schema_oid AND c.relname IN (
             'supplier_mapping_review_batches','supplier_mapping_review_candidates',
             'supplier_mapping_decisions','supplier_offer_selection_events',
             'supplier_offer_selection_heads','v_effective_supplier_mapping_decisions',
             'v_supplier_offer_selection_diagnostics',
             'v_selected_standard_supplier_offers',
             'v_supplier_offer_selection_shadow'
         )
    ), protected_functions AS (
        SELECT p.oid,p.oid::regprocedure::text AS identity
          FROM pg_catalog.pg_proc p
         WHERE p.pronamespace=target_schema_oid AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    ), unsafe AS (
        SELECT e.login_name AS rolname,'schema CREATE'::text AS privilege
          FROM effective_principals e
         WHERE pg_catalog.has_schema_privilege(
                   e.effective_oid,target_schema_oid,'CREATE')
        UNION ALL
        SELECT e.login_name,'relation '||r.relname
          FROM effective_principals e CROSS JOIN protected_relations r
         WHERE pg_catalog.has_table_privilege(e.effective_oid,r.oid,
                   'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR pg_catalog.has_any_column_privilege(e.effective_oid,r.oid,
                   'SELECT,INSERT,UPDATE,REFERENCES')
        UNION ALL
        SELECT e.login_name,'function '||f.identity
          FROM effective_principals e CROSS JOIN protected_functions f
         WHERE pg_catalog.has_function_privilege(
                   e.effective_oid,f.oid,'EXECUTE')
    )
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
               'login',rolname,'effective_privilege',privilege
           ) ORDER BY rolname,privilege)
      INTO unsafe_path FROM unsafe;
    IF unsafe_path IS NOT NULL THEN
        RAISE EXCEPTION 'untrusted login has effective mapping privilege: %',unsafe_path;
    END IF;
END
$role_topology$;

CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".assert_persistent_mapping_foundation_contract()
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "__BUFFALO_TARGET_SCHEMA__", "__BUFFALO_PGCRYPTO_SCHEMA__", pg_temp
AS $$
DECLARE
    target_schema CONSTANT TEXT := '__BUFFALO_TARGET_SCHEMA_TEXT__';
    installed_contract TEXT;
    installed_catalog_sha256 TEXT;
BEGIN
    SELECT value INTO installed_contract
      FROM "__BUFFALO_TARGET_SCHEMA__".meta WHERE key='persistent_mapping_foundation_contract';
    SELECT value INTO installed_catalog_sha256
      FROM "__BUFFALO_TARGET_SCHEMA__".meta WHERE key='persistent_mapping_foundation_catalog_sha256';
    IF installed_contract IS DISTINCT FROM 'v1-shadow-only' THEN
        RAISE EXCEPTION 'persistent mapping foundation contract version differs: %',
            installed_contract;
    END IF;
    IF target_schema IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_mapping_review_batches')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_mapping_review_candidates')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_mapping_decisions')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_offer_selection_events')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_offer_selection_heads')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_effective_supplier_mapping_decisions')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_supplier_offer_selection_diagnostics')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_selected_standard_supplier_offers')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_supplier_offer_selection_shadow')) IS NULL THEN
        RAISE EXCEPTION 'persistent mapping foundation object is absent';
    END IF;
    IF current_setting(
           'procurement.persistent_mapping_foundation_initial_install',true
       )='true' AND (
       EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches)
       OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates)
       OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions)
       OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events)
       OR EXISTS (SELECT 1 FROM "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads)
    ) THEN
        RAISE EXCEPTION 'migration must not adopt or synthesize mapping authority';
    END IF;
    IF "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_policy_is_published(NULL,NULL,NULL,NULL) THEN
        RAISE EXCEPTION 'absent mapping policy must fail closed';
    END IF;
    PERFORM "__BUFFALO_TARGET_SCHEMA__".persistent_mapping_assert_safe_role_topology();
    IF EXISTS (
        SELECT 1
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL aclexplode(c.relacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND privilege.grantee<>c.relowner
    ) OR EXISTS (
        SELECT 1
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_attribute a ON a.attrelid=c.oid
          CROSS JOIN LATERAL aclexplode(a.attacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND a.attnum>0 AND NOT a.attisdropped
           AND privilege.grantee<>c.relowner
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          CROSS JOIN LATERAL aclexplode(p.proacl) privilege
         WHERE n.nspname=target_schema AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
           AND privilege.grantee<>p.proowner
    ) THEN
        RAISE EXCEPTION 'persistent mapping objects grant a non-owner principal';
    END IF;
    IF installed_catalog_sha256 IS NULL
       OR installed_catalog_sha256 !~ '^[0-9a-f]{64}$'
       OR "__BUFFALO_TARGET_SCHEMA__".compute_persistent_mapping_catalog_sha256()
            IS DISTINCT FROM installed_catalog_sha256 THEN
        RAISE EXCEPTION 'persistent mapping catalog signature differs';
    END IF;
END
$$;

REVOKE ALL ON TABLE
    "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_batches,
    "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_review_candidates,
    "__BUFFALO_TARGET_SCHEMA__".supplier_mapping_decisions,
    "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_events,
    "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads,
    "__BUFFALO_TARGET_SCHEMA__".v_effective_supplier_mapping_decisions,
    "__BUFFALO_TARGET_SCHEMA__".v_supplier_offer_selection_diagnostics,
    "__BUFFALO_TARGET_SCHEMA__".v_selected_standard_supplier_offers,
    "__BUFFALO_TARGET_SCHEMA__".v_supplier_offer_selection_shadow
FROM PUBLIC;

DO $revoke_public_function_execution$
DECLARE
    target_schema CONSTANT TEXT := '__BUFFALO_TARGET_SCHEMA_TEXT__';
    owned_function REGPROCEDURE;
BEGIN
    FOR owned_function IN
        SELECT p.oid::regprocedure
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname=target_schema AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',owned_function);
    END LOOP;
END
$revoke_public_function_execution$;

-- Objects can inherit named-role privileges from ALTER DEFAULT PRIVILEGES.
-- This first slice authorizes no non-owner role, so strip every such direct
-- relation/function grant before the catalog is signed. Later role grants
-- require their own approved contract-version transition.
DO $revoke_unconfigured_named_principals$
DECLARE
    target_schema CONSTANT TEXT := '__BUFFALO_TARGET_SCHEMA_TEXT__';
    relation_grant RECORD;
    column_grant RECORD;
    function_grant RECORD;
BEGIN
    FOR relation_grant IN
        SELECT DISTINCT c.relname,
               pg_get_userbyid(privilege.grantee) AS grantee_name
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL aclexplode(c.relacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND privilege.grantee<>0
           AND privilege.grantee<>c.relowner
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I',
            target_schema,relation_grant.relname,
            relation_grant.grantee_name
        );
    END LOOP;

    FOR column_grant IN
        SELECT DISTINCT c.relname,a.attname,
               privilege.grantee AS grantee_oid,
               CASE WHEN privilege.grantee=0 THEN NULL
                    ELSE pg_get_userbyid(privilege.grantee) END AS grantee_name
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_attribute a ON a.attrelid=c.oid
          CROSS JOIN LATERAL aclexplode(a.attacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND a.attnum>0 AND NOT a.attisdropped
           AND privilege.grantee<>c.relowner
    LOOP
        IF column_grant.grantee_oid=0 THEN
            EXECUTE format(
                'REVOKE SELECT (%I), INSERT (%I), UPDATE (%I), REFERENCES (%I) '
                'ON TABLE %I.%I FROM PUBLIC',
                column_grant.attname,column_grant.attname,
                column_grant.attname,column_grant.attname,
                target_schema,column_grant.relname
            );
        ELSE
            EXECUTE format(
                'REVOKE SELECT (%I), INSERT (%I), UPDATE (%I), REFERENCES (%I) '
                'ON TABLE %I.%I FROM %I',
                column_grant.attname,column_grant.attname,
                column_grant.attname,column_grant.attname,
                target_schema,column_grant.relname,column_grant.grantee_name
            );
        END IF;
    END LOOP;

    FOR function_grant IN
        SELECT DISTINCT p.oid::regprocedure AS function_identity,
               pg_get_userbyid(privilege.grantee) AS grantee_name
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          CROSS JOIN LATERAL aclexplode(p.proacl) privilege
         WHERE n.nspname=target_schema AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
           AND privilege.grantee<>0
           AND privilege.grantee<>p.proowner
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I',
            function_grant.function_identity,function_grant.grantee_name
        );
    END LOOP;
END
$revoke_unconfigured_named_principals$;

-- Deliberately do not invoke either trust-anchor function and do not publish
-- contract/signature/migration metadata in this SQL body. Still inside this
-- file transaction, the manifest-aware runner independently verifies the new
-- assert/compute/helper definitions, invokes the verified compute function,
-- inserts the exact v1 contract and catalog signature, invokes the verified
-- assertion, and only then publishes this migration's checksum marker. Any
-- failure rolls back this file's objects and all four metadata effects.
```

### SQL review notes that control implementation

- `jsonb::text` is the database canonical form used by the proposed hash
  helpers. Application payload builders must generate the same JSONB object and
  compare the database-returned hash; they must not hash language-specific JSON
  serialization.
- Human previews and confirmations are domain-separated database hashes. A
  mapping preview binds every requested/expected fact and, for an existing
  link, its offer ID; a created offer binds the precomputable contract rather
  than its database-generated ID. A selection preview binds its exact mapping,
  offer, prior head, validity, and fingerprints. Each confirmation then binds
  that preview to the verified principal, role, action, and idempotency key.
- `ABSENT` and `EXPLICIT_NULL` both require a SQL null value but remain distinct
  in their typed state columns and canonical JSON. `VALUE` requires a value.
  Source payload, owner clarifications, relationships, component membership,
  source locator/pages, and historical capture scope remain immutable.
- Candidate supplier-identity and operational-offer keys are nullable derived
  facts: their versioned functions return SQL null until their stated minimum
  identity exists. A DEFER decision deliberately stores no operational target,
  key, result package, or mutable catalog/vendor/rejection fingerprint; its
  exact candidate foreign key and expected immutable batch/candidate hashes
  preserve the unresolved evidence. APPROVE remains fully strict, while REJECT
  requires the narrower exact target/supplier scope stated above.
- Candidate Variant/vendor references are deliberately untrusted text/UUID.
  Only a decision uses foreign keys, and the insert guard requires the reviewed
  values to equal a fresh candidate plus fresh catalog/vendor fingerprints.
- `supplier_mapping_policy_is_published` returns false for every input. A later
  policy migration may replace it only after an owner-approved publication
  table, evidence classes, evaluator, roles, and tests exist. There is no
  approved default.
- Selection is a shadow authority projection only. The two protection triggers
  prevent first-slice code from activating a mapped offer or pricing an inactive
  mapping result. A later activation/pricing change must replace those guards
  explicitly rather than bypass them.
- No table has `ON DELETE CASCADE`. Review, decision, rejection, selection, and
  operational identity histories remain durable.
- The rejection-memory fingerprint covers every active Variant disposition for
  the stable supplier identity, so any new negative evidence stales a preview;
  the actual approval/selection veto is additionally scoped to the target
  Variant. The human preview omits only the not-yet-generated rejection ID while
  including the precomputed rejection contract hash; the decision payload then
  binds both ID and hash. That hash covers substantive evidence and actor, and a
  linked rejection cannot be updated or deleted. This first slice has no
  reversal operation; a later authorized design must append a resolution event
  and derive effective state without rewriting the rejection row.
- Operational-offer identity is enforced in both directions: equal operational
  keys share one offer, and one offer cannot be approved under different keys.
  The offer-ID advisory lock makes that inverse check safe across concurrent
  approvals. Human review may approve a fully specified combo offer, preserving
  exact component relationships on the decision; policy approval remains
  limited to one deterministic regular `STANDARD` occurrence, and only a
  regular `STANDARD` offer may become the routine head.
- The independently reviewed runner manifest verifies the assertion, catalog
  compute function, and all security-relevant helper identities/properties
  before either anchor is invoked. Only then can the verified compute result be
  compared with the co-present stored signature. This combination fails closed
  on changed/missing/extra owned columns, constraints, indexes, functions,
  triggers, views, owners, relation/function/column ACLs, or unsafe effective
  role topology. Deleting either metadata row fails. A later transition adds a
  new ordered manifest record and atomically advances version/signature; replay
  chooses the highest exact installed manifest prefix and never runs historical
  SQL over it.
- The exact predecessor check pins the referenced-offer trigger's nine-column
  `UPDATE OF` attachment as well as its function, events, timing, row scope,
  enablement, predicate, and function bytes. A weakened column list is not an
  acceptable predecessor.
- All new relations, views, functions, and columns explicitly deny non-owner
  principals: the migration removes `PUBLIC`, named-role grants inherited
  through default privileges, and any per-column grant before it signs the
  catalog. Disposable migration tests run as the isolated schema owner. No
  application-role grant belongs in this slice until the private named-role
  configuration is supplied; that later grant must be least-privilege,
  versioned, and must not expose raw GUC setting or direct table mutation to a
  browser/client principal. Effective direct/transitive `INHERIT` and `SET
  ROLE` owner paths and effective privileges are independently refused; role
  memberships are reported, not modified. Superusers/trusted maintenance are
  the explicit administrative boundary, not subjects supposedly constrained by
  these ACLs.
- Existing `variants`, `vendors`, `supplier_offers`, `supplier_aliases`,
  `mapping_rejections`, `prices`, and artifact storage remain the only
  canonical contracts for their facts. This schema stores an opaque durable
  artifact reference plus its hashes rather than copying the source blob. It
  neither creates a catalog/price engine nor writes a convenience alias.

## 5. Exact service and transaction boundaries

No public route is part of this slice. Implement these internal domain methods
only after the authority/config changes are approved:

### Shared authenticated session-lock and retry protocol

All three write methods use this protocol; a method may not invent a narrower
variant. Fixed first-release limits are `SESSION_LOCK_WAIT_SECONDS=5`,
`TOTAL_OPERATION_SECONDS=30`, and `MAX_TRANSACTION_ATTEMPTS=3`, where three
includes the initial attempt. A later limit change is server configuration, not
request data.

1. Authenticate the principal, authorize the exact operation, and check the
   server-loaded capability before acquiring a database connection. This is
   also required before returning an idempotent result: possession of a UUID
   never authorizes replay disclosure.
2. Canonicalize and freeze the complete request. Intake and selection use their
   stored `payload_sha256`; a mapping decision uses
   `PERSISTENT_MAPPING_DECISION_REQUEST_V1`/`request_sha256`, which includes the
   idempotency key, action, actual candidate/source fields, expected state,
   provenance, preview and confirmation, result disposition and precomputable
   result contracts. It excludes generated decision/time/transaction IDs, a
   generated rejection ID, and a generated `CREATED_INACTIVE` offer ID; it
   retains the exact `LINKED_EXISTING` offer ID. The final decision
   `payload_sha256` additionally covers the committed generated results.
3. Borrow a dedicated or demonstrably session-affine backend connection with
   no open or driver-implicit transaction. Do authenticated, read-only preflight
   in autocommit only where an immutable stored candidate is needed to derive
   contention keys. Verify that candidate again inside every business attempt.
4. Acquire each session advisory lock once, track it locally, and use
   `pg_try_advisory_lock` against a monotonic five-second deadline. The first
   lock is
   `persistent-mapping:<operation>:idempotency:<idempotency_uuid>`; it never
   contains the payload hash, so conflicting use of one key must contend.
   Then acquire domain locks in this global order: mapping decision scope,
   nonnull operational-offer key, selection Variant. Within a domain, sort the
   canonical lock strings bytewise. Advisory-hash collision only adds safe
   serialization; it cannot merge identities.
5. Only after all session locks have been obtained, start a **fresh**
   `SERIALIZABLE` transaction. Set the explicit trusted schema/search path and
   transaction-local principal, action, authentication, and capability values.
   Re-read the idempotency key first. Same key and same complete request hash
   returns the already committed original before stale-head/evidence checks;
   different hash raises `IDEMPOTENCY_CONFLICT` with no mutation. Then re-read
   all candidate/head/evidence state and acquire row locks in the documented
   method order.
6. On SQLSTATE `40001` or `40P01`, fully roll back, verify that the connection is
   usable, and begin a new `SERIALIZABLE` transaction with a new snapshot while
   retaining the already balanced session locks. Never retry inside an aborted
   transaction. Stale preview/evidence/head, changed payload, authorization,
   validation, foreign-key, and unrelated uniqueness failures are final. A
   violation of one of the three named idempotency constraints is rolled back
   and resolved by one fresh post-lock lookup: matching payload returns the
   original, differing payload conflicts, and an absent row raises
   `IDEMPOTENCY_PROTOCOL_VIOLATION`; it is not blindly retried. At attempt three
   or the 30-second operation deadline, return
   `CONCURRENT_TRANSACTION_RETRY_EXHAUSTED` after rollback and with no partial
   row.
7. In `finally`, roll back any open transaction, unlock exactly once in reverse
   order, and require each `pg_advisory_unlock` to return true. Cancellation,
   timeout, or an exception follows the same path. If connection state or lock
   ownership is uncertain, close/discard the backend instead of returning it to
   a pool; a dedicated connection may additionally call
   `pg_advisory_unlock_all` before close. Session locks are never held while
   waiting for a human confirmation.

A lost response during `COMMIT` is not reported as rollback. Recovery obtains a
new dedicated connection, re-authenticates, reacquires the same payload-neutral
idempotency lock, and reads the key before any new effect. A matching row is the
committed original; a differing row conflicts. If the terminated original
backend is known gone and the key is absent, a new attempt is permitted only
within the same three-attempt/30-second budget. If the outcome cannot be made
definitive, return `COMMIT_OUTCOME_UNKNOWN` and perform no blind replay or
external action. PostgreSQL fixes a serializable snapshot at the transaction's
first snapshot-taking command; waiting on a transaction lock is not treated as
refreshing it. Full-transaction retry is therefore mandatory. See PostgreSQL
16 [transaction isolation](https://www.postgresql.org/docs/16/transaction-iso.html),
[`SET TRANSACTION`](https://www.postgresql.org/docs/16/sql-set-transaction.html),
and [advisory-lock functions](https://www.postgresql.org/docs/16/functions-admin.html).

### `intake_supplier_mapping_review(request, principal)`

1. Before storage or database access, require the server-loaded
   `persistent_mapping.review_intake_writes_enabled` flag to be exactly true and
   apply the shared protocol with operation `review-intake`. A false or absent
   flag never opens storage/DB access or sets a GUC.
2. Re-run the supported V5 reader without changing the sealed package. Verify
   the artifact/root/seal/relationship/batch/payload hashes, readiness states,
   zero-authority flags, page bounds, prerequisites, and candidate-set digest.
3. After the payload-neutral idempotency session lock and fresh snapshot, look
   up `intake_idempotency_key` and apply the common exact-replay rule.
4. Insert one batch and all candidates in that same transaction. Nullable
   supplier/offer keys are database-derived only when minimum identity exists;
   null keys do not block intake or fabricate authority. Commit-time
   count/digest validation makes a short or altered set roll back completely.
5. Return evidence IDs only. Do not call mapping, pricing, recommendation,
   Shopify, supplier, order, or file-mutating services.

### `record_supplier_mapping_decision(request, principal_or_policy)`

1. Before storage or database access, require the matching server-loaded flag:
   `human_mapping_writes_enabled` for a human origin or
   `policy_mapping_writes_enabled` for a policy origin. Apply the shared
   protocol with operation `mapping-decision`; for the first release accept
   only a server-verified human principal. Set
   transaction-local authorization context and `procurement.enabled_capability`
   to the one exact enabled flag name. Policy origin is modeled, but its flag
   remains false and the database publication stub always refuses it.
2. Before calling this method, build the exact database
   `HUMAN_MAPPING_PREVIEW_V1` hash and collect a distinct owner confirmation.
   The confirmed request fixes whether the result is `CREATED_INACTIVE` or
   `LINKED_EXISTING`; the latter also fixes the exact offer ID. After immutable
   candidate preflight, session-lock idempotency, decision scope, and any
   nonnull operational key in that order. Start the fresh snapshot, perform the
   post-lock exact-replay check, then lock batch/candidate, Variant/vendor,
   rejection rows, and numeric offer IDs in ascending order. The service-side
   operational-key session lock is held **before** any offer lookup or insert;
   trigger locks are defense in depth only.
3. Recompute every action-applicable expected hash. An approval with altered/missing evidence,
   blockers, a simulation, wrong Variant/vendor, or active rejection fails.
4. For `APPROVE_MAPPING`, require nonnull Variant, vendor, supplier identity,
   operational key, supported package/class and all catalog/vendor/rejection
   fingerprints. Compute the supplier-identity, operational-offer, and
   candidate-decision keys in PostgreSQL from their versioned field sets. The
   decision scope is one exact candidate, so repeated printed occurrences may
   each retain a decision while their shared operational key still requires a
   single offer. If
   any historical approval already links that key, require its exact offer and
   contract, even if that decision was later superseded and is no longer in the
   effective-authority view. Otherwise either link an exact existing offer or insert one
   inactive offer. A created row maps exact candidate values into
   `variant_id`, `vendor_id`, `supplier_sku`, the derived package type,
   `size_text`, `raw_pack`, both unit conversions, and all three assortment
   fields; sets `source_file/source_page` to the candidate occurrence,
   `confidence='VERIFIED'`, `active=false`, and unconfigured validity/replacement
   fields to null; and creates no price. Any prior
   offer reference disqualifies `CREATED_INACTIVE`. Supplier-code reuse with a
   different contract always creates an inactive row; never update or
   deactivate the old row. Exact human-reviewed combo/component relationships
   remain on the decision and may yield `package_type='COMBO'`; policy approval
   is constrained to `REGULAR`/`STANDARD`, and no nonregular class can become
   the routine head.
5. For `REJECT_MAPPING`, require an existing exact candidate Variant/vendor, a
   nonblank source distributor-product ID, a nonnull stable supplier-identity
   key, and current catalog/vendor/rejection fingerprints. Only then insert a
   `mapping_rejections` row using source key
   `persistent-mapping:<supplier_identity_key_sha256>`, with the verified
   principal as `rejected_by`, and link its preconfirmed substantive contract
   hash. If that exact target cannot be supported, rejection refuses and the
   only disposition is `DEFER`; it never creates a broad negative record. The
   rejection preflight excludes only that transaction's one new
   `result_rejection_id` when recomputing the expected pre-decision rejection
   fingerprint, then separately verifies the linked row's exact active
   Variant/vendor/source scope, actor, evidence, and hash.
6. For `DEFER`, copy only batch/candidate identity, immutable candidate/source
   evidence, reason, authenticated human provenance, and confirmation. Store
   SQL null for decision Variant/vendor, supplier/operational keys,
   `result_offer_package_type`, result IDs/contracts, and catalog/vendor/
   rejection fingerprints. The candidate continues to preserve every
   `ABSENT`, `EXPLICIT_NULL`, and `VALUE` state and any partial proposed target.
   DEFER creates no offer, rejection, authority kind, selection, or other
   operational effect.
7. Insert the append-only decision last. A late trigger failure rolls back an
   inserted offer/rejection. Commit. Do not touch aliases, prices, heads,
   recommendations, Shopify, POs, artifacts, or the sealed package.

### `record_routine_offer_selection(request, principal)`

1. Before storage or database access, require the server-loaded
   `persistent_mapping.routine_selection_writes_enabled` flag to be exactly
   true and apply the shared protocol with operation `routine-selection`.
   Require an idempotency key, database-built
   `HUMAN_ROUTINE_SELECTION_PREVIEW_V1` hash, separate confirmation hash, and
   the exact prior event/version. The same owner may act again, but the mapping
   confirmation is not reusable. Session-lock idempotency and then Variant;
   after the fresh snapshot, return the same-key/same-payload event before
   stale-head checks or refuse changed payload without changing the head.
2. Set the server-derived named-human authorization context and lock the current
   head after the session locks.
3. Recompute catalog, vendor, offer, mapping, and rejection fingerprints. A
   `SELECT` requires the current effective approval, `REGULAR`/`STANDARD`, exact
   supplier code, eligible Variant/vendor, valid dates, and no rejection. An
   inactive target must be the fresh unpriced `CREATED_INACTIVE` result.
4. Insert the event, then advance the head using one conditional statement:

   ```sql
   INSERT INTO "__BUFFALO_TARGET_SCHEMA__".supplier_offer_selection_heads AS current_head(
       variant_id,selection_scope,selection_event_id,head_version,updated_txid
   ) VALUES ($variant_id,'ROUTINE_PROCUREMENT_STANDARD',$event_id,1,txid_current())
   ON CONFLICT (variant_id,selection_scope) DO UPDATE
      SET selection_event_id=EXCLUDED.selection_event_id,
          head_version=current_head.head_version+1,
          updated_at=clock_timestamp(),updated_txid=txid_current()
    WHERE current_head.selection_event_id=$expected_prior_event_id
      AND current_head.head_version=$expected_prior_head_version
   RETURNING head_version;
   ```

   Require exactly one returned row. A concurrent loser or stale preview rolls
   back its event completely; the deferred trigger prevents an event without a
   head.
5. Commit without updating `supplier_offers`, `prices`, recommendations,
   Shopify, POs, or orders.

### Confirmation-sensitive equivalent-offer race

Two distinct candidate requests that initially confirm `CREATED_INACTIVE` for
the same operational key serialize on the service lock. The first may create
and commit the one inactive offer and its decision. The waiting request then
starts its fresh snapshot and observes that its confirmed create disposition is
stale. It rolls back without an offer, rejection, or decision; releases every
transaction/session lock; and returns `RECONFIRMATION_REQUIRED` with the facts
needed to generate a new `LINKED_EXISTING` preview. Re-preview is not approval:
the owner must give a new explicit confirmation and use a new idempotency key
because the payload, result disposition, and bound offer identity changed.
That new request may append the second candidate's distinct decision against
the same offer. This is not described as two original concurrent approvals
succeeding, and it is separate from same-key/same-payload replay.

Read methods may query the two new views only after the server-loaded
`selected_offer_shadow_reads_enabled` flag is exactly true. They must label the
result `SHADOW ONLY` and must not substitute it into recommendation execution.
The database role that owns or writes these objects remains an internal service
boundary: no browser/client input may set a `procurement.*` GUC, and the
migration grants no new table, function, or GUC-setting privilege.

## 6. Authorization boundary without invented configuration

The database stores opaque principal, role, authentication-context, policy,
publication, and evidence fingerprints. It does not name an identity provider
or grant a role. Client-entered actor strings and the existing shared review
tokens cannot populate the transaction-local human context.

Before any persistent intake/decision/selection route is exposed, a later
authorization change must:

- load the matching fail-closed capability flag from server-owned
  configuration before storage/DB access and set the transaction-local
  `procurement.enabled_capability` only after that check; request data can
  neither name nor override the capability;
- configure the private identity provider and named-account role assignments;
- derive `principal_ref`, `authorized_role_ref`, and authentication-context hash
  on the server from the verified session;
- authorize mapping and selection separately even if one owner has both roles;
- deny list/detail/evidence download before storage/DB access when no valid
  private session exists;
- ensure Shopify app credentials establish transport only, never human identity.

The write-capable database connection and permission to set transaction context
remain internal service capabilities; the first migration grants no new role or
client privilege. The opaque GUC checks are defense in depth for the service
boundary, not a substitute for the unresolved private IdP/named-role setup.

For v1 migration and replay, `current_user` and `session_user` are the explicitly
invoked trusted maintenance principals; they must not also be browser/client
connection roles. Superusers are acknowledged trusted administrators outside
the ordinary ACL threat model: PostgreSQL object ACLs cannot constrain a
malicious database administrator. Every other non-superuser `LOGIN` role is
untrusted until a separately reviewed release classifies and grants a narrow
server role. The runner and installed assertion recursively enumerate every
role reachable from each login through all-`set_option` paths, then every role
inherited through all-`inherit_option` paths from each possible assumed role.
This detects direct/transitive SET, direct/transitive INHERIT, and
SET-then-INHERIT combinations; `pg_has_role` is corroboration, not the sole
mixed-path test. They also evaluate effective schema
`CREATE`, relation, column, and function privileges with the `has_*_privilege`
functions. Unsafe or unclassified topology refuses and names the path; the
migration never edits cluster role memberships. See PostgreSQL 16
[role membership](https://www.postgresql.org/docs/16/role-membership.html),
[`pg_auth_members`](https://www.postgresql.org/docs/16/catalog-pg-auth-members.html),
and [privilege inquiry functions](https://www.postgresql.org/docs/16/functions-info.html).

Consequently, a client-set actor string or forged `procurement.*` custom GUC
cannot confer authority: the client lacks effective table/function privilege
and cannot inherit or assume an owner. The later private route change must
prove that its server principal remains non-owner and least-privilege before
exposure. If the existing cluster already gives an application/client login an
owner path, DDL refuses pending explicit database-administrator correction;
that fail-closed topology check is distinct from choosing the private IdP.

The unresolved IdP/role choice blocks route exposure and real writes; it does
not block schema construction, pure domain tests, or disposable-PostgreSQL
tests with explicitly labeled synthetic principals.

Policy approval additionally requires a real owner-published immutable policy,
published independent-linkage classes, a service principal, exact policy
version/publication hash, predicate version/result, and independently sourced
evidence-set hash. The false stub makes absence fail closed. Publishing the
policy/evaluator is a later implementation step, not a prerequisite for the
human-only schema slice.

## 7. Exact proposed authority and configuration amendments

These diffs are proposals for the **next implementation change**, not changes
made by this branch. They are limited to mapping intake/decision/selection and
shadow reads.

### Canonical authority proposal

Insert the same text below in both current authority bodies so the chain cannot
diverge:

- in `procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md`, immediately
  after `G. Human intelligence that must NEVER disappear`;
- in `procurement/docs/MASTER_PLAN_v2_0.md`, immediately after `Chat history is
  never system memory` and before section 4.

`procurement/docs/CURRENT_AUTHORITY.md` needs no content change because it
already designates the Master Plan, and this proposal does not change authority
priority. `procurement/docs/PHASE_STATUS.md` also remains unchanged until an
actual implementation milestone is authorized and achieved.

```diff
+Persistent supplier mapping authority
+
+A printed source occurrence, an operational supplier offer, a mapping
+decision, and the selected routine offer are separate records. Printed tiers
+or repeated occurrences do not automatically create duplicate operational
+offers. Supplier-code reuse never overwrites a historical offer identity.
+
+Review intake is immutable evidence and carries no mapping, selection, price,
+Shopify, supplier-contact, or order authority. A sealed review package remains
+unapproved evidence; a later application decision references it without
+rewriting package approval/import flags.
+
+Mapping decisions are append-only APPROVE_MAPPING, REJECT_MAPPING, or DEFER
+events. Human approval requires a server-verified named principal and a
+separate exact confirmation. Policy approval requires a service principal,
+an owner-published immutable policy/version, independently corroborated
+deterministic evidence, and exact fingerprints. Missing policy fails closed.
+Fuzzy similarity is supporting evidence only and can never authorize mapping.
+DEFER may preserve a completely or partially unresolved candidate using only
+its immutable source evidence, reason, and authenticated human provenance;
+it fabricates no operational identity or fingerprint and has no operational
+effect. REJECT_MAPPING requires an exact supported Variant/vendor/supplier
+target and never creates broad negative authority from unresolved evidence.
+
+An approved mapping may create an inactive supplier offer. It never activates
+that offer, verifies or activates price, selects a routine offer, writes
+Shopify, or authorizes an order. One append-only selection event and narrow
+ROUTINE_PROCUREMENT_STANDARD head may separately choose one regular offer per
+Shopify Variant. The same named owner may map and select only through separate
+previews, confirmations, idempotency keys, and events. Alternatives and
+explicit reviewed nulls remain preserved.
+
+The selected-offer view is shadow-only until a separately approved
+recommendation cutover. Existing active-offer recommendation semantics remain
+unchanged. Cost, retail-price, and primary-SKU synchronization remain three
+separately controlled later capabilities.
```

Do not change canonical section 11's price lifecycle in this slice. The
supplier-specific last-approved-book carry-forward and scoped deal-overlay
amendments remain required for the later price-authority change; mixing them
into this first mapping schema would enlarge the authority surface.

### `rules.toml` proposal

Add the one fail-closed matching statement and a disabled new section:

```diff
 [matching]
 sku_first = true
 accepted_alias_second = true
 negative_mapping_memory = true
 auto_match_min_score = 0.92
 review_min_score = 0.82
 size_conflict_blocks_auto_match = true
 pack_conflict_blocks_auto_match = true
 fuzzy_is_supporting_evidence_only = true
+fuzzy_can_authorize = false
+
+[persistent_mapping]
+contract_version = "v1-shadow-only"
+routine_selection_scope = "ROUTINE_PROCUREMENT_STANDARD"
+review_intake_writes_enabled = false
+human_mapping_writes_enabled = false
+policy_mapping_writes_enabled = false
+routine_selection_writes_enabled = false
+selected_offer_shadow_reads_enabled = false
+recommendation_cutover_enabled = false
+offer_activation_enabled = false
```

Do not toggle these flags in the migration. Enabling intake, human decisions,
selection, or shadow reads requires the named-identity boundary and separate
owner authorization. Policy remains disabled until publication/evidence policy
exists. Offer activation and recommendation cutover remain false in this slice.

No first-slice diff changes `selling_price_auto_update`,
`auto_write_supplier_sku_to_shopify`, pricing rollover/archive flags, price
cadence, deal overlays, or supplier communication/order settings. Those belong
to later scoped changes.

## 8. Acceptance matrix and planned test registration

Every result below is future acceptance work, not evidence executed by this
documentation task. `PG` means a disposable PostgreSQL database; `PURE` means
no database/network. Synthetic principals and packages must be labeled
simulation in test output.

| # | Invariant/case | Boundary | Later executable test and substantive required result |
|---:|---|---|---|
| 1 | Exact predecessor only | Migration/PG | `test_upgrade_requires_exact_013_marker_set_and_contracts`: 012-only, unknown intervening marker, missing index/trigger, wrong referenced-offer `UPDATE OF` list, or altered contract refuses with no new object |
| 2 | Fresh schema | Migration/PG | `test_fresh_schema_applies_foundation_once_and_reapplies_idempotently`: with pgcrypto genuinely absent, the actual chain lets only schema_postgres install it and verifies exact extension/member/helper-schema trust before that file commits; installs exactly five tables/four views plus signed functions/triggers; direct/default relation, function, and column grants are absent; seeded rows/hashes survive exact replay; catalog tamper refuses |
| 3 | Version-aware full-chain replay | Runner + migration/PG | `test_checksum_pinned_replay_preserves_later_contract_versions`: reviewed v1 applies; reviewed v2 adds an ordered manifest entry and transition marker; replay selects the highest exact installed prefix and executes neither historical SQL file; missing-middle, unknown, legacy, or inconsistent metadata refuses |
| 4 | Independent replay trust anchors | Runner + migration/PG | `test_replay_independently_rejects_anchor_and_helper_tampering`: no-op assertion, compute returning stored hash, coordinated replacement, altered properties/search path, missing anchor/helper, unknown version, or same-named non-extension digest refuses before either anchor call, marker write, or authority mutation |
| 5 | Concurrent first mapping apply | Runner + migration/PG | `test_mapping_family_lock_serializes_marker_without_claiming_whole_run_atomicity`: two real runner invocations may each commit legacy schema-through-013 reapplications before the barrier; at the mapping lock only one different-byte candidate executes/publishes and the loser refuses without mapping SQL/marker overwrite; identical bytes validate/skip |
| 6 | Hostile schema/temp resolution | Runner + migration/PG | `test_explicit_schema_binding_defeats_hostile_search_path_temp_and_helper_decoys`: earlier hostile schema, decoy meta/validators, temporary authority tables, caller path changes, non-16 server fixture, unusable target, and same-named helper substitutes cannot alter resolution or cause false replay; a target absent from the caller path but containing a protected-signature decoy or granting untrusted CREATE also refuses; controlled legacy resolution must select the target and exact extension-member OIDs before effects |
| 7 | Historical upgrade | Migration/PG | `test_exact_013_upgrade_preserves_all_legacy_bytes_and_counts`: build through the actual 013 files, not consolidated schema; seed legacy rows and snapshot table digests/recommendations; apply leaves new authority tables empty |
| 8 | Failed migration/late validation | Migration/PG | `test_migration_failure_and_late_validation_roll_back_every_object`: precondition failure and injected post-DDL/pre-marker failure leave predecessor bytes/counts and mapping metadata unchanged |
| 9 | Late domain validation | Mapping + selection/PG | `test_late_decision_or_head_validation_rolls_back_the_whole_transaction`: failure after offer/rejection/event insertion leaves no such row and no head change |
| 10 | Valid intake, zero authority effects | Intake/PG | `test_valid_intake_adds_only_immutable_batch_and_candidates`: exact counts; READ COMMITTED direct insert denied; service SERIALIZABLE succeeds; zero decisions/heads/events/prices/offer changes/external effects |
| 11 | Intake idempotency | Intake service/PG | `test_intake_exact_replay_returns_existing_and_payload_change_conflicts`: sequential same key/hash returns original IDs even after later state; changed complete request produces no row |
| 12 | Concurrent intake idempotency | Intake service/PG | `test_concurrent_intake_same_key_serializes_exact_replay_and_conflict`: two barriered connections with same key/same payload return one committed batch/candidate set; changed payload contends on the same lock then conflicts; no duplicate/partial candidates |
| 13 | Altered/missing evidence | Reader + intake/PG | `test_intake_rejects_missing_or_altered_source_hash_page_and_prerequisite`: each changed artifact/root/seal/table/page-bound input rolls back fully |
| 14 | Explicit null versus absent | Intake + DB/PG | `test_candidate_preserves_explicit_null_and_absent_states`: states/hashes differ, values remain SQL null, nullable derived keys appear only when minimum identity exists, and replay is stable |
| 15 | Unresolved DEFER | Mapping/PG | `test_defer_accepts_completely_unresolved_and_partially_known_candidates`: ABSENT/EXPLICIT_NULL/VALUE subcases append human-confirmed DEFER with null operational target/keys/package/fingerprints/results; candidate evidence remains exact; no offer/rejection; DEFER never appears as offer authority and superseding an approval removes it from effective authority without erasing its historical key/offer binding; APPROVE and unsupported REJECT refuse |
| 16 | Printed repeats/tiers | Intake + mapping/PG | `test_repeated_occurrences_and_tiers_share_one_operational_offer`: candidates and decisions remain distinct while equal operational keys link one offer ID |
| 17 | Concurrent equivalent-offer race | Mapping service/PG | `test_concurrent_equivalent_create_requires_reconfirmation_then_reuses_one_offer`: first create commits one inactive offer/decision; stale-confirmation loser leaves no orphan and returns reconfirmation required; after new LINKED_EXISTING preview, new confirmation, and new idempotency key, second candidate decision commits to the same offer; a superseded historical approval still forces that same key/offer identity on later reuse |
| 18 | Offer class separation | Mapping/PG | `test_regular_gift_special_alternate_component_and_combo_do_not_collapse`: distinct keys/offers; exact component relationships; only regular can select; two barriered different-key LINKED_EXISTING approvals aimed at one offer (keys differ only in a decision-preserved material qualifier) yield exactly one commit and one fresh-snapshot serialization refusal, with no partial losing decision |
| 19 | Supplier-code reuse | Mapping + offer/PG | `test_reused_supplier_code_preserves_old_offer_and_creates_inactive_history`: no old-row update; active uniqueness valid; materially different key cannot reuse offer; policy path refuses |
| 20 | Wrong Variant/vendor | Mapping/PG | `test_mapping_refuses_wrong_variant_or_vendor_and_stale_fingerprints`: FK-valid candidate mismatch and nonexistent proposed targets fail approval/rejection with no operational result while DEFER remains available |
| 21 | Append-only intake/decisions/events | DB/PG | `test_authority_history_rejects_update_delete_and_cascade`: every UPDATE/DELETE, including linked rejection evidence/actor/active state, fails and rows remain |
| 22 | Exact decision replay | Mapping service/PG | `test_mapping_exact_replay_and_same_key_different_payload`: request hash ignores generated create/rejection IDs but binds complete confirmed intent; exact replay returns original; changed payload has no partial effect |
| 23 | Concurrent mapping idempotency | Mapping service/PG | `test_concurrent_mapping_same_key_replays_before_stale_head_and_conflicts_on_change`: identical requests serialize and both receive one result even though it advanced the head; changed request uses the same lock then conflicts; no second offer/rejection/decision |
| 24 | Stale mapping preview/prior | Mapping/PG | `test_mapping_rejects_stale_preview_and_stale_or_forked_prior`: changed catalog/vendor/rejection/candidate or non-tip predecessor fails and is never auto-rebased/retried |
| 25 | Human identity/capability | Authorization + mapping/PG | `test_mapping_requires_server_named_human_context`: false/absent flags deny before storage/DB; NULL or malformed authentication-context, preview, or confirmation hashes, forged/mismatched GUC, client actor, or shared token cannot insert; only exact enabled synthetic server context succeeds |
| 26 | Effective owner/role topology | Authorization + migration/PG | `test_role_topology_rejects_direct_transitive_inherit_set_and_mixed_owner_paths`: synthetic direct/transitive INHERIT, SET ROLE, mixed path, effective relation/column/function/schema privilege, and forged GUC cases refuse; invoked maintenance principal is permitted; post-install unsafe membership makes assertion fail; no membership is revoked |
| 27 | Policy fail closed | Authorization + mapping/PG | `test_policy_mapping_requires_published_policy_and_independent_evidence`: NULL/malformed policy publication or predicate-result hashes and the false publication stub reject every policy approval and all policy DEFER/REJECT shapes; no approved default/effect |
| 28 | Approval lifecycle | Mapping/PG | `test_mapping_approval_creates_inactive_unpriced_unselected_offer`: decision/offer commit atomically; zero price/head/recommendation effects |
| 29 | Distinct selection | Selection/PG | `test_valid_mapping_then_separate_selection_requires_second_confirmation`: mapping confirmation/idempotency cannot be reused; selection advances only its head; exact sequential replay and changed payload behave correctly |
| 30 | Explicit reviewed null | Selection/PG | `test_clear_appends_event_and_preserves_prior_selection`: CLEAR is a head event, not deletion or deactivation |
| 31 | Stale selection preview/head | Selection/PG | `test_selection_rejects_stale_offer_catalog_vendor_rejection_and_prior_head`: each mismatch rolls back event/head and is not automatically retried |
| 32 | Concurrent selection/idempotency | Selection service/PG | `test_concurrent_selection_replay_and_same_prior_head_have_exact_outcomes`: same key/payload returns one committed event even after its head advance; same key/different payload conflicts; different keys from one prior head yield one complete winner and one stale loser with no partial event |
| 33 | Retry, timeout, cleanup, unknown commit | Service + PG | `test_session_lock_retry_budget_cleanup_and_unknown_commit_recovery`: injected 40001/40P01 use at most three total fresh snapshots; exhaustion/cancel/timeout leaves no partial state and balanced locks; uncertain cleanup discards connection; lost COMMIT response resolves by authenticated locked lookup or returns COMMIT_OUTCOME_UNKNOWN without blind effects |
| 34 | Inactive selection lifecycle | Selection + views/PG | `test_inactive_selected_offer_remains_inactive_unpriced_and_shadow_labelled`: head may select only fresh mapped inactive offer; no active/price mutation; exact state label |
| 35 | Active legacy selection | Selection + views/PG | `test_existing_active_offer_selection_does_not_mutate_offer_or_price`: exact active row may select; nonauthority digests fixed |
| 36 | Rejection memory | Mapping + selection/PG | `test_active_rejection_and_conflicting_evidence_block_approval_and_selection`: exact Variant rejection persists/blocks; Variant A evidence stales shared-identity preview without broadly vetoing freshly confirmed Variant B; source conflict never reactivates |
| 37 | Mapped/priced/reference protections | Offer/PG | `test_mapped_priced_and_referenced_offer_contracts_cannot_be_rewritten`: new and migration 010/011 guards remain effective |
| 38 | V5 non-adoption | Migration + intake/PG | `test_unapproved_v5_package_is_never_backfilled_or_relabelled`: migration creates zero rows; intake preserves NOT_APPROVED/NOT_IMPORT_READY |
| 39 | Legacy recommendations | Recommendation shadow/PG | `test_legacy_recommendations_are_identical_until_cutover`: before/after outputs/blockers exact; `_load_context` never queries new views |
| P1 | Zero external/operational effects | Service boundary/PURE | `test_mapping_foundation_has_no_shopify_price_order_supplier_or_artifact_mutator`: dependency sentinels record zero calls for success/error/replay/retry/unknown-outcome paths |
| P2 | Authority/config diff | Static/PURE | `test_mapping_authority_and_disabled_flags_match_approved_contract`: exact mirrored authority text and false flags; CURRENT priority and price/Shopify/carry-forward flags unchanged |
| P3 | Registration floor | Test runner/PURE | `test_persistent_mapping_modules_are_registered_at_exact_discovery_floors`: exact discovery is 39/3; removing one method trips its module floor and sum-derived global floor |

This matrix names exactly **39 PostgreSQL methods and three pure/static
methods**. Planned files and exact initial module floors are
`procurement/tests/test_persistent_mapping_foundation_postgres.py: 39` and
`procurement/tests/test_persistent_mapping_foundation_contract.py: 3` in
`REQUIRED_MODULE_MINIMUMS`. The runner already discovers `test_*.py`; no
separate module registry exists or is proposed. The registration-floor test
lives in the pure module and uses existing runner seams. If implementation adds
a method, its module floor rises to the actual discovered count; no other floor
may fall. `GLOBAL_MINIMUM_TESTS` remains exactly
`sum(REQUIRED_MODULE_MINIMUMS.values())`, so these two new modules add 42 to the
then-integrated sum rather than hard-coding today's unrelated global count.

Fresh-schema validation must run the actual complete file chain. Upgrade-path
validation must stop at the exact intended predecessor and then apply the new
file; a consolidated `schema_postgres.sql` that already contains these objects
is not an upgrade test. PostgreSQL, identity, package, and concurrency fixtures
are simulations unless real evidence is separately authorized. No live/private
or Shopify result may be reported from this matrix without executing it.
For positive-path database tests, `source_is_simulation=false` means only that
the fixture is exercising the authoritative-format branch of the contract; the
test report must still label the package, principal, and result as synthetic and
must not describe them as a real owner approval or private-package replay.

## 9. Seven-finding remediation closure matrix

Every disposition below means **specified in this design and awaiting
independent static re-review plus later executable proof**. None means migrated,
implemented, or tested.

| Accepted finding | Design correction | Planned proof | Disposition |
|---|---|---|---|
| P1 independent replay trust anchors | Ordered literal runner manifest binds migration bytes, version, schema, both anchor definitions/properties and transitive helpers; trusted catalog inspection precedes invocation; observed markers can select only the highest exact manifest prefix; v2 adds a reviewed record | PG #3 and #4 | Design-remediated; unexecuted |
| P1 explicit schema and safe resolution | Manifest target name is bound to a non-system/non-temp invocation OID; stable hashes exclude OIDs; safe identifiers and literal qualified SQL; fresh pgcrypto bootstrap is post-verified in the same file transaction; maintenance privileges, target/helper writability, controlled-path helper OIDs, signed function paths and explicit `pg_temp` last are checked; decoys never supply authority | PG #6, plus #2 catalog signature | Design-remediated; unexecuted |
| P1 effective ownership/role membership | Independent and installed checks cover effective relation/column/function/schema privilege and direct/transitive INHERIT/SET/mixed owner paths; unsafe topology refuses without membership mutation; administrator boundary is explicit | PG #26 and #2 | Design-remediated; unexecuted |
| P1 DEFER without invented identity | Candidate keys become conditionally nullable; DEFER operational targets/keys/package/fingerprints/results are null while immutable source states remain on the candidate; APPROVE remains strict; REJECT has a narrow exact minimum | PG #14, #15 and #20 | Design-remediated; unexecuted |
| P1 concurrent idempotency and offer reuse | Authenticated payload-neutral session locks precede fresh snapshots; full request identity/post-lock replay, deterministic ordering, three-attempt/30-second budget, balanced cleanup and unknown-commit recovery; offer-key lock precedes lookup/create; changed disposition requires fresh confirmation | PG #12, #17, #22, #23, #32 and #33 | Design-remediated; unexecuted |
| P2 correct test registration | Uses existing `test_*.py` auto-discovery; exact future floors are 39 PG and 3 PURE via `REQUIRED_MODULE_MINIMUMS`; global remains sum-derived | PURE P3 | Corrected in design; unexecuted |
| P2 accurate migration-lock scope | Retains mapping-family transaction lock only; explicitly allows earlier schema-through-013 commits and disclaims invocation serialization/atomicity | PG #5 | Corrected in design; unexecuted |

## 10. Retained owner requirements and unresolved dependencies

The first slice retains one Variant-wide primary regular selection without
deleting alternatives; separate mapping and selection confirmations by the
same authenticated owner; human versus policy provenance; independently
corroborated deterministic matching with no fuzzy authority; and complete
source/rejection/null/relationship/history evidence. The reviewed design's
supplier-specific last-approved-book carry-forward, scoped deal overlays, and
separate cost/retail/primary-SKU controls remain binding later requirements,
not schema shortcuts here.

| Unresolved detail | Does not block | Actually blocks |
|---|---|---|
| Private IdP and named-role assignments | DDL, immutable model, pure and disposable-PG tests | Private route exposure, real intake, human mapping/selection writes |
| Owner-published independent initial-linkage evidence classes | Human-only schema and review UI planning | Policy publication/evaluator and every `POLICY_APPROVED` event |
| Whether a later service policy may execute eligible SKU requests unattended | Entire mapping foundation, price work, owner-confirmed first SKU release | Only a future unattended Shopify SKU executor |
| Supplier/book cadence, validity, and price-scope configuration | Mapping intake/decision/selection schema | Price schedule policies, carry-forward, replacement, and deal-overlay execution |

The six answered owner policy questions and Shopify InventoryItem cost
destination are not reopened. Nothing here grants permissions, selects an IdP,
publishes evidence policy, invents supplier schedules, enables unattended
execution, or approves a real mapping.

## 11. Exact next implementation boundary

After the dependency sequence is integrated and owner authorization is given,
the recommended next change is only:

1. apply the approved first-slice canonical/config amendments with every new
   capability flag false;
2. implement and test the narrow checksum-pinned `apply_schema.py` replay
   boundary, then assign the next migration number after rechecking the exact
   chain and implement the five tables, functions/triggers, empty shadow views,
   and no-backfill contract above;
3. implement internal intake/mapping/selection domain services behind no public
   route, with policy hard-disabled;
4. add and register the two planned test modules at exact initial floors 39 and
   3; prove fresh and exact-013 upgrades, rollback, idempotency, concurrency,
   immutability, and zero effects;
5. obtain independent backend/data review and stop for owner acceptance.

Explicitly excluded are identity-provider selection/configuration, public or
private routes, policy publication/execution, pricing lifecycle, carry-forward,
deal overlays, offer activation, recommendation cutover, Shopify reads/writes,
OAuth scope changes, supplier communication, PO/order actions, production DB
access, deployment, and `CURRENT` activation.

This document is the completed construction specification only. Its SQL and
diffs remain proposals until a new authorization names an approved integrated
target and migration predecessor.

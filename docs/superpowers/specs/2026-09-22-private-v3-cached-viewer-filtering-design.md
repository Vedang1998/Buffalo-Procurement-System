# Private V3 Cached Viewer Filtering Design

**Status:** Owner-authorized bounded design  
**Authorization T0:** 2026-09-22T22:38:28Z  
**Implementation cutoff:** 2026-09-23T01:08:28Z  
**Hard stop:** 2026-09-23T02:38:28Z

## Objective

Remove the demonstrated request-path replay of the complete V3/V2 semantic
validator while preserving the existing cold-start trust boundary. The private
viewer must fully validate its sealed workspace before readiness, then serve
page, pagination, and filter requests from exactly that app-owned in-memory
snapshot without accepting an arbitrary caller-provided projection.

This design changes no research input, history, identity, eligibility,
forecast, policy, projection, workspace, artifact bytes, authentication,
network controls, or purchasing authority.

## Considered approaches

### 1. Private filtering core plus app-owned snapshot capability — selected

Factor the existing deterministic filter body into one private helper that
assumes its projection has already passed the full validation boundary. Keep
the public filter as validation followed by delegation. The app calls the
private helper only after proving the workspace and projection are the exact
objects in its atomically published validated snapshot.

This is the smallest change, shares all filter semantics, preserves public
tamper rejection, and makes the bypass provenance explicit.

### 2. New general validated-workspace/capability framework — rejected

A reusable capability type could encode validation provenance across modules,
but it expands architecture and public surface beyond the demonstrated viewer
defect. It is unnecessary for one private composition root.

### 3. Validation memoization by hash or caller flag — rejected

Caching `_validated_projection` by SHA still requires either trusting a
caller-supplied identity or retaining another large graph. A `validated=True`
flag creates an arbitrary-dict fast path. Both weaken the trust boundary and
violate the authorization.

## Design

### Atomic validated snapshot

Replace the two independently assigned cache globals with one private immutable
snapshot record containing the normalized workspace path and the fully loaded
workspace object. `_workspace()` reads the requested path, returns the cached
workspace only when the path matches, otherwise performs the existing full
`read_private_research_workspace()` replay into a local value and publishes the
new snapshot with one assignment only after success.

A failed load publishes nothing. A changed path cannot receive rows from the
old snapshot. Clearing process state or restarting forces the existing full
validation again.

### Shared filtering core

Move only the already-established query/vendor/status matching semantics into
a compiled private row predicate. The predicate accepts one row plus the fixed
projection contract; it never accepts or authenticates a whole projection. The
public `filter_private_research_rows()` remains the supported external
boundary, still calls `_validated_projection()`, selects rows from that
validated value, and applies the shared predicate exactly as before.

After its snapshot-identity guard, the app selects the sealed row collection
itself and applies that same predicate while performing its existing stockout
filter, count, and pagination. It recursively copies only the at-most-50
selected rows before returning them to request rendering. Request code
therefore cannot mutate nested structures in the cached workspace, no unchecked
whole-projection fast path exists, and an ordinary unfiltered request does not
copy, serialize, or hash all 2,009 rows or the complete 230MB projection.

### App-owned access guard

Add a private app wrapper that accepts the workspace returned by `_workspace()`
and checks all of the following before calling the private core:

1. a cached snapshot exists;
2. the current environment path equals the cached path;
3. the supplied workspace is the cached workspace object by identity;
4. the projection is the cached workspace's projection object by identity;
5. the projection remains a dictionary suitable for the established renderer.

Any mismatch raises `PrivateResearchProjectionError`, which the route already
normalizes to a private 503 response. No request parameter, filename, hash, or
identity string can opt into this path. Only after this guard may the route
select the cached row collection, apply the shared row predicate, and return
its detached selected page.

Artifact and projection download routes continue to return the exact sealed
bytes already held by the validated workspace.

## Error and concurrency behavior

- Validation failure retains the prior snapshot internally but cannot serve it
  for a different requested path because both path and object identity must
  match.
- The one-object snapshot publication prevents a transient new-path/old-value
  mix between concurrent readers.
- Request filtering accepts only strings and preserves existing 422/404/503
  behavior at the route boundary.
- Authentication, exact loopback Host/Origin checks, GET-only routing,
  no-store headers, secret handling, escaping, and network confinement are
  unchanged.

## Test design

Write and execute the request-path regression before the implementation. The
pre-fix run must show that repeated real TestClient page/filter requests call
the public validating filter; the post-fix expectation is zero calls after one
successful startup load.

Registered tests will prove:

- one cold load, then zero public/full-validation filter calls for ordinary,
  pagination, query, vendor, status, no-match, and repeated requests;
- clearing the app snapshot or switching to another valid workspace performs a
  fresh load;
- a failed replacement load cannot produce a usable cached snapshot or leak
  rows from the prior workspace;
- a detached/copied workspace or projection cannot use the private app path;
- the private core and public function return identical IDs/order/filter
  semantics for valid inputs;
- the public function still rejects a tampered projection;
- mutating returned nested values does not mutate the cached projection;
- existing auth, exact artifact, and tamper tests remain intact.

The app-test module floor and global discovered-test floor will be increased by
the number of new test methods; no existing test is removed or hidden.

## Acceptance sequence

1. Run focused app/projection/V2/V3/workspace/browser-harness tests plus compile,
   Node syntax, diff, import-isolation, and test-registration checks.
2. Run a focused authenticated probe against the actual full-size private V3
   workspace. Record cold startup separately from warmed ordinary/pagination/
   query/vendor/status request duration and response bytes.
3. Obtain independent read-only review of the exact frozen material commit and
   remediate only concrete in-scope findings.
4. Run the complete registered suite and startup checks once on the final
   material commit.
5. Run canonical authenticated Chromium initial and restart acceptance on that
   same commit, preserving complete artifact length/SHA and cleanup evidence.
6. Update `docs/CODEX_HANDOFF.md`, preserve `PHASE_STATUS.md`, and seal one
   additive code/acceptance supplement whose manifest includes nested checksum
   files as ordinary payloads.

PASS requires both the exact-target suite and both real browser phases. Any
material change after either gate requires affected gates to run again.

## Scope and authority

This remains private development research only. No Shopify capture/write,
production database, policy change, dependency, migration, supplier decision,
DRAFT, PO/order, deployment, remote push, PR, merge, or purchasing action is
authorized.

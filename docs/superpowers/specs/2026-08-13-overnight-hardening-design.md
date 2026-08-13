# Overnight Engineering Hardening Design

**Authorization:** owner-approved unattended sprint on
`tooling/overnight-hardening-2026-08-12`.

## Outcome

Make deterministic Procurement OS validation portable and trustworthy, document
the verified engineering environment, audit implemented behavior against canonical
authority, and fix only defects whose correct behavior is already explicit. The
sprint must not activate a later operational phase or make any owner decision.

## Chosen approach

Use one incremental branch with reviewable checkpoints:

1. add a single repository-native test entrypoint and GitHub Actions PostgreSQL CI;
2. repair verified handoff drift and document the actual tool topology;
3. audit test discovery, readiness logic, identity handling, and exposed operational
   surfaces, adding adversarial tests before authority-determined fixes;
4. record a structured canonical discrepancy inventory and implementation-ready
   packets for remaining official phases;
5. validate against disposable PostgreSQL, inspect production only through enforced
   read-only transactions, then push without merge or deployment.

This is preferred to a tooling-only change because the owner explicitly authorized
the safety audit, and preferred to broad later-phase scaffolding because unresolved
Phase 4 identity review makes operational expansion risky and unnecessary.

## Components and data flow

- The canonical test entrypoint owns discovery, requires a positive expected test
  count, reports the exact executed count, and supplies a disposable test database
  through the caller.
- GitHub Actions installs from the repository's locked `uv` configuration, starts an
  ephemeral PostgreSQL service, and invokes the same entrypoint used locally.
- Documentation records verified Git/GitHub/tooling facts and preserves Phase 4 gate
  and human-review boundaries.
- Safety changes remain local and deterministic. No code in this sprint may call a
  Shopify mutation, generate/release a real PO, or mutate production data.

## Failure handling

- Zero-test discovery, missing PostgreSQL integration coverage, dependency-lock
  drift, schema failures, and any required test failure are hard failures.
- Material authority conflicts or business ambiguity are documented as owner-review
  findings rather than guessed fixes.
- Production database inspection uses database-enforced read-only mode and reports
  only non-secret gates, counts, and control totals.

## Validation

- baseline and final full deterministic suite against disposable PostgreSQL;
- explicit discovery/count assertion and zero-test adversarial check;
- applicable schema/migration and Python syntax checks;
- GitHub Actions syntax/config review;
- canonical discrepancy review, Git diff review, and secret-safety scan;
- clean worktree and pushed feature branch at closeout.

## Authorization boundary

Independent Claude/Cursor/ChatGPT review, owner acceptance, PR merge, deployment,
identity decisions, price-policy decisions, Shopify writes, PO creation/release, and
all production mutations remain outside this sprint.

# Buffalo Procurement OS — Tooling Setup

**Verified:** 2026-08-13 (UTC)

This document records the current development and review topology. It does not
replace the canonical specification, project governance, readiness gates, or
`docs/CODEX_HANDOFF.md`.

## Roles and active routes

| System | Current role | Verified route / operating boundary |
| --- | --- | --- |
| ChatGPT | Architect, program manager, and business-rule guardian | Defines scope and acceptance evidence with the owner; does not replace deterministic tests or owner approval. |
| Codex | Sole primary implementation writer | Active route: **Mac → SSH → Replit → Codex CLI**. Codex Desktop Remote SSH is not the active route because its connection attempt was unsuccessful. |
| Cursor | Targeted read-only specialist reviewer | **Cursor Remote SSH → Replit workspace**. It is not a concurrent writer during an active Codex task. |
| Claude Code | Independent adversarial reviewer | Runs from a **separate local GitHub clone**, refreshed from the remote branch before review. It reviews the actual diff without editing during the initial review. |
| Replit | Runtime and infrastructure authority | Hosts the application runtime, PostgreSQL, Secrets, storage, deployment, and scheduled jobs. The repository root in Replit is `/home/runner/workspace`. |
| GitHub | Durable source and change-control system | Holds feature branches, pull requests, CI, review evidence, and audit history. The required Procurement CI check is `procurement-tests`. |

## One-writer rule

Only the designated implementation writer may modify the active task/worktree.
Codex is the writer by default. ChatGPT, Cursor, Claude Code, Replit Agent, and
other agents must not concurrently edit it unless the owner explicitly approves
a coordinated exception. Read-only review may occur from a clean separate clone
or worktree after a checkpoint is pushed.

## Environment and secret policy

- Deterministic runtime parity is Python 3.13, PostgreSQL 16, and uv 0.12.3.
  `.python-version`, both Python project constraints, and `.replit` prevent
  silent drift to Python 3.14 or an unqualified PostgreSQL package.
- CI keeps `astral-sh/setup-uv` SHA-pinned, requests uv 0.12.3 and Python 3.13,
  and uses the immutable PostgreSQL 16 image
  `postgres:16@sha256:95206741a5b214807675e14165369d05b93a9cf692223b616d07cca227e74b0b`.
  That digest was independently pulled and verified as PostgreSQL 16.14 on
  2026-08-13.
- Production secrets belong in Replit Secrets/environment, never source files,
  logs, screenshots, issue text, prompts, commits, or CI configuration.
- Never expose database URLs, Shopify credentials, review tokens, API tokens,
  SSH keys, or authentication state.
- `.env` files and `.ai-auth/` are ignored. Verify they remain untracked before
  every push. The tracked `procurement/.env.example` may contain names and safe
  placeholders only.
- GitHub CI uses a disposable PostgreSQL service with test-only credentials. It
  receives no production `DATABASE_URL`, Shopify credential, or Replit secret.
- Deterministic tests use `TEST_DATABASE_URL`; the canonical runner refuses a
  non-loopback database, a database name that does not end in `_test`, URL
  query/redirect parameters, a connected `current_database()` mismatch, or a
  server outside PostgreSQL major 16. It clears inherited libpq `PG*` settings
  and never inherits the runtime `DATABASE_URL`.

## Production-write restrictions

Development and review do not authorize production mutations. Do not write to
Shopify, modify production business data, generate or release a real PO, force a
readiness gate, merge to `main`, or deploy without the separately required phase,
owner, review, and release authorization. Production database inspection must use
database-enforced read-only mode unless a task explicitly authorizes a production
write. Tests use disposable infrastructure only.

## Safe reconnection procedures

### Codex through the active SSH route

1. Connect from the Mac to the Replit workspace using the existing SSH setup; do
   not copy or regenerate authentication material into the repository.
2. `cd /home/runner/workspace` and inspect `git status --short --branch`, the
   current branch, `HEAD`, `origin/main`, and remotes before starting Codex CLI.
3. Confirm no other writer is active. If the worktree is unexpected or dirty,
   follow the recovery procedure below rather than overwriting it.
4. Start Codex CLI from the repository root, then read `AGENTS.md`, canonical
   authority, governance, and the current handoff before any change.

Do not switch to Codex Desktop Remote SSH unless that connection path is separately
re-established and verified.

### Cursor through Remote SSH

1. Open the existing Replit Remote SSH target in Cursor and select
   `/home/runner/workspace`.
2. Verify the expected branch and commit and confirm the worktree is clean.
3. Keep Cursor read-only for targeted specialist review while Codex is the writer.
   Return findings with file/line evidence to the designated writer.

### Claude's local review clone

Claude reviews from a separate local GitHub clone, not the active Replit worktree.
Before each review:

1. confirm the local clone has no unexplained changes;
2. run `git fetch --prune origin`;
3. inspect `origin/main` and the exact pushed feature-branch SHA;
4. check out or detach at that remote feature-branch commit without merging it;
5. review `origin/main...HEAD`, run the canonical validation command against
   disposable infrastructure, and report findings without editing initially.

If the clone is dirty, preserve and identify those changes before refreshing; do
not use destructive reset/checkout commands to erase them.

## Canonical validation sequence

1. Verify repository root, branch, `HEAD`, `origin/main`, remotes, and clean status.
2. Read authorities, schema/migrations, relevant code/tests, and live readiness
   state where read-only inspection is authorized.
3. Run the baseline full suite from the repository root:

   ```bash
   ./scripts/procurement-tests
   ```

   The wrapper uses uv 0.12.3 exactly. If the active uv differs but `uvx` is
   available, it obtains that pinned tool version. With no supplied test URL,
   local PostgreSQL binaries must be major version 16. A disposable loopback
   PostgreSQL 16 service may instead be supplied through `TEST_DATABASE_URL`.

4. Implement only on the authorized feature branch; run focused tests after each
   material change.
5. Run the same full command again. It must report discovered, executed, pass,
   failure, error, skip, expected-failure, and unexpected-success counts; all
   non-pass counts must be zero.
6. Run `uv lock --check` with uv 0.12.3, plus applicable schema/integrity,
   syntax/static, diff, and secret-safety checks.
7. Push the branch and obtain GitHub `procurement-tests` proof.
8. Obtain risk-appropriate independent review, remediate findings through the one
   writer, then obtain ChatGPT/owner acceptance before merge or release.

## Review escalation

- Level 0 documentation/cosmetic: writer review and lightweight validation.
- Level 1 ordinary application logic: deterministic tests and CI.
- Level 2 backend/data pipeline: full and adversarial tests, integrity/idempotency
  controls, and independent Claude review.
- Level 3 identity, pricing, inventory, financial, forecasting, or procurement
  logic: Level 2 plus deterministic expected-answer fixtures, targeted Cursor
  review where useful, ChatGPT business-rule review, and owner approval at defined
  decision boundaries.
- Level 4 actual PO release or irreversible production action: all Level 3 controls,
  explicit owner authorization, and post-action reconciliation.

## Dirty-worktree recovery

1. Stop before switching branches, pulling, formatting, or running a command that
   could overwrite files.
2. Record `git status --short --branch`, `git diff`, staged diff, untracked files,
   branch, and `HEAD` without printing secret contents.
3. Identify whether the changes belong to the current task, another tool/session,
   or an unknown owner. Preserve a patch or separate worktree/branch when safe.
4. Resume only after the writer/owner and intended scope are clear. Never use
   `git reset --hard`, destructive checkout, or broad deletion without explicit
   owner authorization.

## Durable handoff

Refresh `docs/CODEX_HANDOFF.md` at every meaningful milestone or phase boundary
with verified branch/commit/PR, test results and counts, readiness gates, control
totals, blockers/risks, owner decisions needed, and the exact next authorization
boundary. Update `procurement/docs/PHASE_STATUS.md` only when a genuine program or
phase milestone changes.

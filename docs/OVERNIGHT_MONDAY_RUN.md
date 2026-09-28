BUFFALO PROCUREMENT OS — MONDAY OVERNIGHT EXECUTION CONTRACT

Owner: Vedang Patel
Business deadline: Monday, September 7, 2026, 6:00 PM America/New_York.
Target morning handoff: Monday, September 7, 2026, 8:00 AM America/New_York.

SETUP INSTRUCTION

Save this contract as docs/OVERNIGHT_MONDAY_RUN.md in the authorized emergency
worktree, not in the frozen G10 worktree.

Complete the brief readiness/setup checks below, then return:
OVERNIGHT SETUP READY
with the selected branch/worktree, test-database safety result, and any
permissions or session-liveness problem that requires attention now.

Wait for my following /goal message before starting the build queue.
Do not use this setup message to start a new architecture discussion.

MISSION

Deliver the most complete, tested, locally runnable first-purchase-order
workflow possible by the morning target.

The required product is:
validated inputs → recommendations → explicit human review/edit →
separate vendor DRAFT POs → reconciled internal review/export packet.

A working product means real application services and UI operating together
against disposable PostgreSQL, not screenshots, stubs, or isolated functions.

This is NOT authorization to finish every long-term feature.
This is NOT authorization to deploy, access production, or place orders.

AUTHORIZATION AND PRIORITY

This contract explicitly RESUMES the previously paused emergency offline
implementation workstream for the scope below.

It does not reopen production permissions, approve G10, close Phase 4,
unpause formal Phase 6 acceptance, or renumber canonical phases.

Codex is the sole writer. You may complete sequential offline packets and
repair implementation/test defects without asking for approval at each step.

Read applicable AGENTS.md and:
- docs/PROJECT_GOVERNANCE.md
- docs/CODEX_HANDOFF.md
- procurement/docs/PHASE_STATUS.md
- procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md
- procurement/config/rules.toml
- docs/superpowers/specs/2026-09-06-emergency-monday-procurement-mvp-design.md
- the existing emergency implementation plan and packet checkpoints.

Canonical business rules outrank this schedule. An unresolved conflict blocks
the affected feature, not unrelated safe work.

KNOWN GIT CHECKPOINTS — VERIFY, DO NOT ASSUME

Accepted main:
f308ac666a2377f540e528bc873463daecc20cf8
Tree:
0a8a2ea80721a97858c2120545d1e6b6f3805247

Frozen G10 branch:
codex/phase4-autoscale-preflight-bridge
Head:
86da9669a9f83f81fa3a49c59cf62e9fc1a7a3b6
Tree:
045395d55046fa78013b4a79d64e15139ad1f933

Emergency implementation branch:
codex/emergency-monday-procurement-mvp
Last verified remote head:
7068f54fe2fb8b54397888aadba6990d3644b19a

The emergency branch has existing work. Inspect it before rebuilding anything.

WORKTREE AND SESSION SAFETY

1. Identify every relevant worktree and confirm no other writer is active on
   the emergency task. Do not run multiple Codex writers on it.
2. Leave G10 unchanged for independent review. Do not merge or cherry-pick G10
   into the emergency branch.
3. Use the emergency branch's isolated worktree. Preserve existing local work.
   Do not discard, reset --hard, clean -fd, force-checkout, rebase, or force-push.
4. If known emergency files have uncommitted changes, inspect their provenance
   and scope before integrating them. Unknown/unrelated changes must remain
   untouched and be reported.
5. If exclusive use of the existing worktree cannot be established, use an
   isolated child branch from the verified emergency commit. Record this
   explicitly; do not overwrite another worktree or silently omit local work.
6. Do not merge current main or other feature branches overnight. Record
   integration requirements for morning review.
7. Confirm normal coding/testing can run under current permissions. Do not
   disable the sandbox or approval system globally.
8. Verify the active execution host and session. Do not claim persistence
   through disconnect/sleep unless actually established. Do not install new
   loop plugins, schedulers, or an unattended infrastructure framework.

DATABASE / SECRET BOUNDARY

All database-backed work must use the existing validated TEST_DATABASE_URL
contract: disposable loopback PostgreSQL 16, database name ending _test,
verified current_database() and server version before fixture DDL.

Never fall back to DATABASE_URL.
Never connect to production neondb or development heliumdb.
Do not query production, even read-only.
Do not read secret stores, print credentials, or copy deployment credentials.
Test subprocesses must not inherit production DB or Shopify credentials.
Do not mutate shared Replit Secrets or another task's environment.

Disposable local DDL/DML and synthetic test data are authorized.
Production DDL/DML are not.

WHAT TO BUILD — CONTINUE EXISTING PACKETS

Start with a short evidence-based gap assessment. Map what already exists,
what is tested, and what is incomplete. Spend no more than about 20 minutes
planning before implementing the first eligible gap.

Follow the existing emergency design. Prioritize an end-to-end baseline flow
before expanding optional capabilities.

A. INPUTS AND PREREQUISITES
- Verify existing test-harness isolation.
- Finish inventory snapshot ingestion, timestamps, units, and idempotency.
- Finish typed vendor rules, calendars, case/loose rules, fees, and required
  owner-provenance fields.
- Finish outstanding-order/incoming reconciliation. DRAFT POs are not incoming.
- Finish universal price-book staging, deterministic validation, and atomic
  promotion through existing canonical services.
- Make missing mappings, costs, pack sizes, vendor facts, and stale inputs
  visible blockers. Never replace unknowns with convenient values.

B. RECOMMENDATIONS
- Implement the approved deterministic forecast and baseline-need calculation.
- Use only eligible active CURRENT/LIVE identities for replenishment.
- Preserve the approved treatment of historical-only, excluded, allocated,
  routine-excluded, and ONE_BOTTLE items.
- Censor stockout days only when supported by inventory evidence. Unknown
  inventory history is not proof an item was in stock.
- Respect verified incoming, vendor protection periods, case/loose constraints,
  and exact Decimal money arithmetic.
- Preserve baseline versus strategic-extra quantities and the approved
  ascending price-tier/economics rules. No filler or invented demand.
- If strategic optimization is incomplete, explicitly block strategic extras;
  do not present them as validated or silently change the canonical policy.

C. HUMAN REVIEW AND DRAFT OUTPUT
- Build a plain usable review interface: accept, edit quantity, reject/comment.
- Store run-scoped decisions and invalidate approval when material inputs change.
- Recalculate after edits; enforce vendor/offer/variant relationships.
- Generate one idempotent DRAFT PO per vendor/run through the real services.
- No auto-approval, FINAL, release, transmission, or Shopify mutation.
- Produce the approved internal review/export packet.
- If the native Shopify import format is unverified, retain
  SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED.
  An internal CSV must not be advertised as a validated Shopify import.

D. REAL END-TO-END DEMONSTRATION
- Run the actual local app/API and complete the workflow with disposable data.
- Include at least two vendors and multiple pack sizes.
- Exercise imports, recommendations, human-review simulation, edits, drafts,
  downloads, and reruns through the real service chain.
- Label every synthetic approval, draft, export, screenshot, and sample:
  TEST DATA — NOT FOR ORDERING.
- Test the actual browser workflow when installed browser tooling is available.
  If unavailable, use real HTTP/service integration tests and record browser
  acceptance as outstanding. Do not claim a browser test from screenshots alone.

E. MORNING OPERATOR PACKET
- Prepare the input templates and prioritized missing-data checklist needed
  to use real data tomorrow.
- Distinguish current inventory, recent sales coverage, open orders, confirmed
  vendor terms, and current price-book dates.
- Historical sales through August 10 are not current September buying inputs.
  Report freshness gaps explicitly; do not invent subsequent sales.
- Include an internal manual-review worksheet/template as a fallback.
  It must not bypass failed application gates or impersonate a released PO.

QUALITY REQUIREMENTS

Maintain a requirements → implementation → deterministic-test evidence matrix.

Expected results must be independently calculated from approved rules and
fixtures, not generated by calling the implementation under test.

Required adversarial coverage:
- bottle/case/pack conversion and rounding;
- wrong vendor/offer/variant associations;
- historical-only and inactive identities;
- missing/stale inventory, costs, rules, or mappings;
- returns, zero demand, and known versus unknown stockouts;
- duplicate imports, recommendations, and PO creation;
- DRAFTs incorrectly counted as incoming;
- partial receipts, cancellations, and ambiguous outstanding orders;
- stale approval after quantity/price/input changes;
- failed prerequisites and no readiness bypass;
- transaction failure/rollback, retry, and concurrent conflicting actions;
- output line totals and vendor totals matching exact accepted inputs;
- sensitive values absent from logs and generated artifacts.

Use focused tests during coding and affected suites at coherent checkpoints.
Run the complete authoritative suite against the final candidate.
Preserve existing tests and per-module floors. Do not delete, skip, weaken, or
rewrite expected outcomes merely to get green results.

Counts are branch-specific. Do not claim G10's 430 tests ran on an emergency
branch that does not contain them. Report discovered/executed/passed and every
abnormal counter for the exact tested SHA.

No clean acceptance verdict with failures, errors, skips, expected failures,
or unexpected successes. If validation cannot complete, say so.

INDEPENDENT REVIEW WITHOUT IDLE TIME

Use an already installed, authenticated Claude Code reviewer if available
under existing permissions. Do not install a reviewer or fall back silently
to a separately billed API.

Reviewer access is read-only to code and explicitly disposable test resources,
with no production credentials. Freeze the reviewed commit in a separate
worktree or snapshot. The reviewer must not edit files or Git refs.

G10 may receive one independent review at its frozen head. Record the verdict
and findings, but do not modify or deploy G10 under this overnight contract.
G10 review must not become a dependency for offline emergency implementation.

Request an independent review of the completed emergency candidate, focusing
on inventory, money, approvals, transactions, and duplicate ordering.
Codex may fix findings within the authorized emergency scope, then obtain
targeted re-review of material fixes.

Codex /review or another Codex pass is additional review, not evidence that
Claude independently approved.

If the independent reviewer is unavailable, record REVIEW PENDING and prepare
the exact review packet. Continue eligible coding/testing. Do not spend the
night retrying reviewer authentication or waiting for ChatGPT.

CONTINUATION AND TIME MANAGEMENT

Once /goal starts:
- Implement → test → diagnose → fix → retest → checkpoint → next eligible task.
- Do not stop just because a packet, commit, or test suite completed.
- Do not wait for the owner to answer routine implementation choices.
- Missing business facts become explicit blockers; continue synthetic/offline
  work that does not depend on those answers.
- Do not spend hours on Replit deployment, Nix, Railway migration, or hosting.
- After two attempts at the same external/environmental blocker without new
  evidence, park it and continue independent work.
- Do not repeatedly print the same blocked status or poll a completed process.
- When every remaining task is blocked, save a BLOCKED handoff and pause.
  Do not falsely mark the product complete to terminate a loop.

Use actual time checks in America/New_York.
At 06:30 Monday, stop adding features; prioritize integration, correctness,
review fixes, and final validation.
By 08:00 Monday, produce the best verified handoff and pause the goal.
If everything is proven earlier, stop successfully rather than burning usage.
If a safe test is still running at the handoff time, record that fact accurately.

These are execution instructions, not permission to fake a result to meet a
clock deadline.

DURABLE PROGRESS

Keep docs/OVERNIGHT_PROGRESS.md current after each material checkpoint,
before context compaction, and approximately every 30 minutes of active work.

Record:
- branch/head and any uncommitted work;
- completed acceptance criteria;
- tests actually run and their results;
- unresolved defects, owner questions, and blocked tasks;
- exact next eligible action;
- review status and reviewer identity;
- production/Shopify/PO-release actions, which must remain zero.

Commit coherent scoped checkpoints. Normal pushes to the authorized feature
branch are permitted. No force-push and no push to main.

Do not repeatedly push trivial progress updates or trigger redundant CI runs.
Do not create a PR, merge, enable auto-merge, or change workflows overnight.

Update docs/CODEX_HANDOFF.md on the emergency branch with verified state.
Update PHASE_STATUS.md only for accurate milestone wording, never to claim
a formal phase is complete without its acceptance.

MORNING DELIVERABLES

Produce docs/MONDAY_MORNING_HANDOFF.md containing:
1. Exact candidate branch/head/tree and tested commit.
2. Working end-to-end features, with evidence.
3. Incomplete/blocked features without euphemisms.
4. Full validation and independent-review results.
5. Local startup and test instructions that do not connect to production.
6. Clearly labeled test draft POs and reconciled internal packet locations.
7. Prioritized real-data inputs and owner decisions required.
8. Exact reviewed deployment/production prerequisites still outstanding.
9. A feasible morning sequence toward the 6 PM deadline.
10. Explicit readiness:
   OFFLINE CANDIDATE READY FOR REVIEW, or INCOMPLETE/BLOCKED.
   PRODUCTION PURCHASING: NOT AUTHORIZED.

Do not commit sensitive operational data or credentials to the public repository.

STRICTLY OUT OF SCOPE

No production connection, including preflight.
No deployment or republish.
No migration of hosting.
No actual Shopify call/write.
No supplier communication or order submission.
No real-money action.
No auto-approved real purchasing decisions.
No mutation of frozen identity manifests or canonical procurement policy.
No merging G10, emergency work, or other branches into main.
No claims of zero possible defects or completed production acceptance.

AFTER SAVING THIS CONTRACT

Report OVERNIGHT SETUP READY with any immediate permission/session blocker.
Then wait for the short /goal instruction.

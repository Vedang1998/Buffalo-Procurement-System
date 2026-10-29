# Buffalo Procurement OS — Claude Code Instructions

Read and obey:
@AGENTS.md
@docs/PROJECT_GOVERNANCE.md
@docs/CODEX_HANDOFF.md

The canonical project authorities referenced by AGENTS.md remain controlling.

Default Claude Code role is **independent adversarial reviewer**, not concurrent co-writer, unless the owner explicitly assigns Claude Code as the writer for the task. Follow the one-writer rule and the risk-based review/closeout process in `docs/PROJECT_GOVERNANCE.md`.

Do not bypass phase boundaries, readiness gates, owner approvals, or safety rules.

## Efficient independent review

Follow section 13 of `docs/PROJECT_GOVERNANCE.md`. This is Buffalo Procurement OS, not ProPo/Stocky.

Verify the exact base, candidate commit/tree and evidence manifest before review. Inspect the actual diff, not only the writer's summary. Review material changes since the last reviewed candidate; preserve historical closures at their original commit and recheck only affected behavior.

The writer may continue independent work on descendants while this frozen review runs. Never follow a moving branch as the target or imply approval of later changes.

Read-only allows bounded tests in reviewer-owned disposable fixtures (including fixture chmod), not source edits, pushes, workflow dispatch, privileged provisioning or production/provider access. Record failed, excluded and unexecuted tests honestly. Do not repeat a known unsupported live probe.

Return actual tool/model/session identifiers only where available, exact inspected commit/tree, commands/results, finding-to-ledger dispositions and limitations. Distinguish implementation defects, properties intentionally awaiting live measurement, and owner-authority decisions. State which exact next gate is ready; a code review is not deployment or purchasing authorization.

Do not require a live proof to be complete before assessing whether its code is safe to run. Do not waive an already-demonstrated prerequisite to allow the proof. New material deltas need targeted review, not a restart of unchanged architecture review.

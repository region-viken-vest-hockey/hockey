# Implement a GitHub issue (shared coding-agent procedure)

This procedure is for **repository code changes**, not an alternative RVV season-operation interface. Read `AGENTS.md` and the verified session handover first. For season operations, use the existing shared `rvv-miniputt/operate.md` procedure and canonical repository commands. This procedure is harness-neutral; Pi and other agents may expose thin aliases to it.

## Start with live evidence

1. Confirm repository identity, branch, HEAD, worktree, remote main and the current issue/PR. Run `make handover ARGS='--issue N'` when available. A failed or incomplete handover is REVIEW_REQUIRED, not evidence that the season is safe.
2. Fetch issue N, its current comments, linked PRs and relevant open dependencies. Do not rely on an earlier chat or a stale issue snapshot.
3. Read only the task-relevant code, tests and active docs identified by `AGENTS.md`; load the RVV skill and relevant procedures for planning, scraping, canonical-season, export or publication work.
4. State concrete acceptance criteria, affected consumers, authoritative owner, and what must remain unchanged. Clarify only genuinely missing real-world facts or authority.

## Implement at the owner boundary

1. For behavioral defects, reproduce the exact failure, classify the violated contract using the ownership map in `AGENTS.md`, and add a focused regression at its canonical owner.
2. Make the smallest complete change at that owner. Add integration coverage for the original failure and update the smallest active document that owns changed behavior.
3. Trace downstream effects (CLI, persistence, evidence, exports, published projections and other callers) without duplicating semantics across layers.
4. For calendar/scraper work, validate parsed events against source evidence, refresh/reconcile relevant host calendars and distinguish occupancy evidence from a confirmed booking. Test both planning and post-placement booking reconciliation when in scope.
5. For canonical-season changes, preserve accepted requests, locks, unrelated placements and published-sealed reconciliation. Use typed, revision-bound application capabilities; never hand-edit canonical JSON or generated exports.
6. Do not introduce a second agent controller, scraper, scheduler, task tracker or harness-specific domain policy.

## Verify and deliver

1. Run focused regression tests, then `make check` (canonical quick tier). Run `scripts/check full` for substantial planner/lifecycle changes or when the affected integration needs it. For source/parser changes, use the appropriate hermetic tests and `scripts/check live` when live upstream validation is required and available. Record exact commands and outcomes; do not claim unrun checks passed.
2. Inspect the full diff for scope, missing consumers, unintended state changes, secrets, and regressions. Re-run affected checks after fixes.
3. Work on an issue-specific branch unless the operator explicitly authorizes direct-main work. Commit and push, then create/update a PR linked to the issue; do not merge without explicit authorization. If direct-main is authorized, verify current main and worktree immediately before committing and pushing.
4. Report changes, preserved invariants, test evidence, PR/commit link, blockers and remaining risks. For ongoing substantive work, leave one concise issue-comment handover checkpoint following the shared handover convention.

**Authority boundary:** Implementing an issue never authorizes publication, rollback, booking confirmation, or an operational season mutation. Those require the existing canonical procedures and explicit operator authority.

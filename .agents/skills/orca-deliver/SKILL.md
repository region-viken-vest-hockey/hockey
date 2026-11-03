---
name: orca-deliver
description: Coordinate Campsty issue delivery in the single host Orca runtime, dispatching supervised workers to SSH worktrees on Lima. Includes automated recovery, post-merge persona testing, and metrics collection.
---

# Campsty delivery via Orca

Use this for the coordinator pilot; use `orca-flow` when this session implements one issue itself
(the default). Run `npm run orca -- review-open` before dispatching, so a login or Cloudflare check
happens up front, and after the merge run `npm run orca -- cleanup --issue <n>`.

This is a **coordinator skill**, not a coding-worker loop. When invoked with an issue number, deliver only that issue; without a number, select the highest-priority eligible live issue according to `docs/agents/workflow.md`. Never rely on a stale issue snapshot. An optional request to continue the backlog must be bounded and respect dependencies and approval gates.

## Enhanced automation goals (this skill)

These are **coordinator protocols the agent executes step by step**, not standalone CLI automation.
The persona-testing runner (`tools/persona-testing/run.ts`, subcommands `brief`/`preflight`/`record`/
`summary`) records and validates findings/visits and renders metrics; the agent itself drives the
browser, files the issues, and emits the metrics events described below. Do not assume a recorded
finding is auto-filed, or that a persona mission runs unattended.

- **Automated recovery**: Codify common failure patterns (Orca restart, worker loss, lease expiry under live worker, ambiguous start) so the coordinator can self-heal without operator intervention.
- **Post-merge persona testing**: After squash merge, run `persona-testing` missions on the updated `main` branch against the test stack; auto-file GitHub issues for validated findings (ux, accessibility, mobile, visual-polish). Non-blocking — merge proceeds regardless.
- **Structured metrics**: Emit JSONL events for each pipeline stage (claim, dispatch, worker_start, worker_done, review_start, review_complete, repair_count, verify_head, merge, cleanup, persona_run, issues_filed) to `.local/auto/metrics/orca-deliver.jsonl` for dashboarding.
- **Auto-issue filing**: Convert persona-testing findings and repair-ledger patterns into GitHub issues with correct labels (`ux`, `accessibility`, `mobile`, `bug`, `enhancement`, `priority: P<n>`).

## Pipeline

```
CLAIM → DISPATCH → WORKER IMPLEMENTS → PR → CHATGPT REVIEW → REPAIR LOOP → VERIFY-HEAD → MERGE
                                                          ↓
                                              POST-MERGE PERSONA TESTING (async, non-blocking)
                                              → AUTO-FILE ISSUES → METRICS
```

## 1. Prerequisites & Setup

1. Load the **installed, version-matched** Orca skill with `orca skills get orchestration --full`; stop and report if native orchestration cannot be accessed. Read `docs/agents/README.md`, applicable canonical policies, and `docs/operations/orca-coordinator.md`. Do not invent CLI flags or implement another scheduler.
2. Run `mise run agent:doctor` on the Lima worker host. Before dispatch, inspect Orca's live Runs/workers and the current GitHub issue/PR; never start two workers for the same issue. The one host Orca runtime (the Mac IDE, or the headless runtime local to the VM) is the **only Orca runtime**; Lima, when used, is just the SSH execution host. No independently launched coding loops or additional Orca servers.
3. Ensure `chrome-devtools` MCP is connected and test stack is healthy (`node scripts/mcp-test-stack.mjs status` exits 0). Required for post-merge persona testing.
4. Initialize metrics file: `mkdir -p .local/auto/metrics && touch .local/auto/metrics/orca-deliver.jsonl`.

## 2. Issue Selection & Claim

- If the user named an issue, use it. Otherwise select highest-priority eligible issue per `docs/agents/workflow.md` (P0 → P4, respecting `status:ready`, dependencies, blocking PRs).
- Claim the issue: `npm run auto -- claim --issue <n> --owner <agent> --run <run-id>`. Emit `claim` metric with timestamp, issue, owner, run-id.
- On `LEASE_CONFLICT`: if auto-selected, pick next candidate; if user-named, report holder and stop.
- Renew the lease (`npm run auto -- renew --issue <n> --owner <agent> --run <run-id>`) after dispatch, after each worker_done, after every review round and repair push, and before `verify-head`. The 30-minute TTL otherwise lets another session take over mid-flight.
- On `LEASE_CONFLICT`, `LEASE_LOST` or `DIRTY_WORKTREE` after the claim, stop: do not retry, re-claim or clean the worktree. Print the `HANDOFF` notice defined in `orca-flow` step 2 (code, holder, branch, last head, PR, review chats, state) and end the run.

## 3. Dispatch with Automated Pre-flight

Run `npm run orca -- dispatch --issue <n> --owner <agent> --run <run-id>`. The adapter performs:

1. Lease held by same owner/run (non-expired)
2. `auth-check --phase plan` → `CLEAR` or `FOUNDER_APPROVED` (else parks issue, releases lease)
3. Orca runtime reachable
4. Claimed worktree resolved through Orca before side effects; transient `selector_not_found` is retried for about 30 seconds while the lease remains held, then `WORKTREE_UNRESOLVED` fails safely with no record or Run
5. Exclusive dispatch record created (`.local/auto/orca-dispatch/issue-<n>.json`)
6. `run-create` confirmed (`ok: true`)
7. Lease re-read before worker-start
8. `worker-start` uses the resolved stable worktree ID and is confirmed (`ok: true`, `result.state: ready`; the intermediate stage may differ)

**Emit `dispatch` metric** with: issue, run-id, dispatch-id, timestamp, auth-check verdict.

### Automated Recovery (bounded, fail-closed)

If dispatch returns `WORKTREE_UNRESOLVED`, no dispatch record or Run exists; retry the whole dispatch once after confirming `orca worktree show --worktree path:<worktree> --json` succeeds. If dispatch fails at step 5-8, the coordinator may attempt **one** recovery action, and only when the
failure is conclusively inactive. The dispatch record is the ownership fence that stops a second
dispatch for the same issue; never delete it on `worker-list` absence alone — a worker can be
temporarily unreachable yet still running, and `worker-list` is a soft observation, not an
authoritative worker state. Route every decision through the authoritative guards and verdicts
(dispatch record state, `worker-show`/`worker-stop` results) before removing anything.

| Failure                                           | Conclusive evidence required                                                                                             | Recovery action (single attempt)                                                        |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| `DISPATCH_RECORD_EXISTS`                          | Record state is `not-started` (the dispatch never reached a worker), confirmed by reading the record                     | Delete the record, retry dispatch once                                                  |
| `RUNCREATE_FAILED` / `RUNCREATE_UNVERIFIED`       | None — a Run may exist even when the failure or receipt is incomplete                                                    | Stop and report; do not delete the record or invent an unsupported Run deletion command |
| `WORKTREE_SELECTOR_NOT_FOUND`                     | Record state is `not-started` with preserved `selector_not_found` request evidence                                       | Delete the record and retry once; an empty historical Run may remain                    |
| `WORKER_START_FAILED` / `WORKER_START_UNVERIFIED` | `worker-show --dispatch <id>` (or `worker-stop --dispatch <id>`) verdict confirms the worker never started or has exited | Only then delete the record and retry once                                              |

`LEASE_LOST_BEFORE_START` (and any `LEASE_CONFLICT`, `LEASE_LOST` or `DIRTY_WORKTREE`) has **no recovery path**: do not re-claim, retry, reset or clean. Stop and print the `HANDOFF` notice defined in `orca-flow` step 2.

A record in `unconfirmed`, `starting`, or `started` is **ambiguous and treated as a live worker**:
confirm the worker is gone through `worker-show`/`worker-stop` verdicts before removing the record
(the same rule as the `.local/auto/orca-dispatch` `INACTIVE_DISPATCH_STATES = ["not-started"]`
guard). If recovery is not conclusively safe, **stop and report** — do not silently fall back.

## 4. Coordinator Loop (Worker Supervision)

```bash
npm run auto -- renew --issue <n> --owner <agent> --run <run-id>   # before every wait iteration
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```

- Each wait is up to 15 minutes against a 30-minute lease: renew before **every** `check --wait` iteration and before every `review-wait`, including after a timed-out or empty wait.
- Answer each `question` with `orca orchestration reply`.
- On `worker_done`: verify IDs match dispatch → `worker-release --dispatch <id>` → ack batch.
- **Emit `worker_done` metric**: issue, run-id, dispatch-id, outcome, duration_ms, commit_sha.
- Only on `succeeded`: proceed to PR creation.

## 5. PR Creation & ChatGPT Review

- Push branch, create PR with body stating slice, changes, tests, gaps, actual implementation agent.
- **Emit `pr_created` metric**: issue, pr_number, pr_url, head_sha.
- Run ChatGPT review via Orca browser (`review-open`, `review-send`, `review-wait`) — never Chrome DevTools, Playwright, or Computer Use.
- **Emit `review_start` / `review_complete` metrics** with verdict, findings_count, blocking_count.

## 6. Repair Loop with Bounded Automation

- Treat every P1/P2/P3 finding as a comment to address. Verify against code before changing.
- Fix on same branch, `mise run check`, commit, push.
- **Fresh context per round**: run each re-review in a new campsty-project chat created with `review-open` and the guarded `review-send`/`review-wait` commands (a reviewer worker may do this only if it uses those same commands and returns the verified chat URL), with a self-contained request naming the PR and new head. Do not continue the prior review chat or paste its verdict; verify earlier fixes against the code. Track every chat for cleanup.
- **Final fresh-reviewer pass**: after the last repair and before `verify-head`, run one more review in a brand-new chat on the exact final head. Merge requires it to report no blocking findings (tooling-only P2/P3 may become follow-ups).
- Renew the lease after each repair push.
- **Before each repair**: run `npm run auto -- repair-record --issue <n> --owner <agent> --run <run-id> --pr <pr> --head <sha> --findings-file <file>`.
- **Automation**: If findings are **tooling-only** (only `tools/`, `.agents/`, `docs/agents/`, `docs/operations/` and tests), allow **one repair round** then convert remaining P2/P3 to follow-up issue (auto-file with `tooling` label). P1 still blocks.
- **Emit `repair` metric** per round: issue, pr, head_sha, findings_fixed, findings_remaining, repair_count.
- Cap: 2 substantial repairs per distinct finding; 6 repaired heads per PR → `REPAIR_LIMIT` parks issue.

## 7. Promote with Squash Merge

- Wait for the final fresh-reviewer pass to say "ready to merge" + all CI `pass`, polled in bounded rounds: renew the lease, run `timeout 600 gh pr checks <pr> --watch --fail-fast`, and on a timeout (exit 124) renew and repeat; a failed renew means `HANDOFF`.
- Run merge-phase `auth-check`; on `REQUIRES_FOUNDER`, request approval, `founder-resume`, re-check.
- **Independent verification**: `npm run orca -- verify-head --issue <n> --pr <pr> --owner <agent> --run <run-id>`. Require `ok: true`, `verified: "npm run verify"`. Record returned `head` SHA.
- **Emit `verify_head` metric**: issue, pr, verified_sha, gate_passed.
- Squash merge: `gh pr merge <pr> --squash --delete-branch --match-head-commit <verified-head-SHA>`.
- **Emit `merge` metric**: issue, pr, merge_commit_sha, merge_time_ms.
- `git checkout main && git pull --ff-only`.
- Release lease: `npm run auto -- release --issue <n> --owner <agent> --run <run-id>`.
- Cleanup: `npm run orca -- cleanup --issue <n>`.
- **Emit `cleanup` metric**: issue, worktree_removed, branch_deleted.

## 8. Post-Merge Persona Testing (NEW — Non-blocking)

**Run asynchronously after merge completes.** Does not block merge or issue closure.

```bash
# Ensure test stack is up
node scripts/mcp-test-stack.mjs status || exit 1

# Run persona missions on updated main against test stack (http://localhost:3100)
# Rotate through core personas: owner, camper, reception (landing-page-visitor optional)
# Each mission: naturalistic exploration + targeted boundary verification
```

Use the `persona-testing` skill protocol:

1. Preflight (chrome-devtools, test stack, seeded fixtures)
2. For each persona: new isolated browser context, actor briefing, naturalistic mission
3. After actor completes: targeted boundary verification (same-day booking, zero-night, payment retry, staff permission boundaries, admin support flows)
4. Collect findings: functional `PASS|FAIL|BLOCKED|ABANDONED`, usability `GOOD|NEEDS_IMPROVEMENT|NOT_ASSESSED`, visual polish `CLARITY|VISUAL_POLISH|ACTION_CONFIDENCE`

### Auto-File Issues from Findings

For each validated finding:

- **Functional FAIL / BLOCKED** → `bug` + `priority: P1` or `P2`
- **Usability NEEDS_IMPROVEMENT** → `enhancement` + `ux` + `priority: P2` or `P3`
- **Visual polish NEEDS_IMPROVEMENT** → `enhancement` + `visual-polish` + `priority: P3` or `P4`
- **Accessibility gaps** → add `accessibility` label
- **Mobile-specific** → add `mobile` label
- Include in issue body: persona, mission, viewport/locale, observed screen/action, expected, evidence (screenshot path), impact, suggested direction, reproducibility
- Deduplicate against existing open issues (title + labels + area) before creating
- **Emit `persona_run` metric**: issue (origin), personas_tested, findings_total, issues_filed, issues_deduplicated, duration_ms
- **Emit `issue_filed` metric** per issue: number, title, labels, source_finding

## 9. Update/Close Origin Issue

- Apply documentation cleanup per `docs/agents/documentation.md`.
- If PR body had `Closes #<n>`, issue closes automatically. Verify with `gh issue view`.
- If slice (`Refs #<n>`): comment on issue with what was merged and what remains.
- **Emit `issue_update` metric**: issue, action (closed/commented), remaining_scope.

## 10. Cleanup Review Chats

Delete only flow-created ChatGPT chats (review + consult) using `npm run orca -- review-delete --page <pageId> --message-file <file>`. Verify deletion.

## 11. Final Report

Emit `delivery_complete` metric with summary:

```json
{
  "issue": <n>,
  "pr": <pr>,
  "merge_commit": <sha>,
  "chatgpt_verdict": "ready|no_blocking_findings",
  "findings_fixed": <count>,
  "repair_rounds": <count>,
  "persona_issues_filed": <count>,
  "persona_issues_deduplicated": <count>,
  "total_duration_ms": <ms>,
  "blockers": ["..."],
  "untracked_files": ["..."],
  "metrics_file": ".local/auto/metrics/orca-deliver.jsonl"
}
```

Print this report to the user.

## Backlog Continuation (Optional)

If user requested "continue backlog":

- After delivery complete, re-query live issues.
- Select next eligible issue per workflow.md.
- Repeat from step 2.
- Bound: max 3 issues per session unless user specifies otherwise.
- Respect dependencies, approval gates, and daily dispatch budget if `#1073` dispatcher is enabled.

## Rollback & Guards

- Do not run legacy `/goal /orca-flow` as nested worker.
- Keep `orca-flow` skill as standalone fallback during migration.
- Do not delete guards before single-runtime collision and recovery pilots pass.
- All automated recovery actions are **single-attempt only**; on repeated failure, stop and require operator.
- Persona testing runs **only on test stack** (`camping_test` DB, `http://localhost:3100`); never production.
- Auto-filed issues are **proposals** — they appear in backlog for normal triage/prioritization.

---

## Metrics Schema (JSONL)

Each line: `{"ts": "ISO8601", "event": "claim|dispatch|worker_done|pr_created|review_start|review_complete|repair|verify_head|merge|cleanup|persona_run|issue_filed|issue_update|delivery_complete", "data": {...}}`

Example:

```json
{
  "ts": "2026-11-02T10:15:00Z",
  "event": "dispatch",
  "data": {
    "issue": 1089,
    "run_id": "uuid",
    "dispatch_id": "dispatch-uuid",
    "auth_verdict": "CLEAR"
  }
}
```

Consumers: `jq`, `sqlite-utils`, or import to dashboard (Grafana, etc.).
---
name: orca-deliver
description: Coordinate hockey issue delivery through Orca-supervised workers in the single host Orca runtime (coordinator pilot). The coordinator claims the issue, dispatches one supervised worker to the issue worktree, supervises it, then runs the shared review/verify/hand-off steps from orca-flow. Use only when the user asks for a supervised-worker or coordinator run; otherwise use orca-flow.
---

# orca-deliver (coordinator pilot)

Use [`../orca-flow/SKILL.md`](../orca-flow/SKILL.md) when this session implements the issue itself (the default). This
skill is a **coordinator**, not a coding loop: it dispatches one supervised worker and owns the lease, review and
hand-off. It is a pilot — the dispatch path has not yet been validated end to end in this repository, so report
limitations you hit instead of assuming they work, and never run a second coordinator or scheduler.

Policy is not repeated here: workers follow `AGENTS.md` and `.agents/commands/implement-issue.md`; the coordinator
follows orca-flow for claim, authority, review, repair ledger, verification and (non-)merge rules. Delivering an issue
never authorizes publication, a live-season mutation, or merging.

## 1. Setup
1. Load the installed, version-matched Orca skill: `orca skills get orchestration --full`. Stop and report if native
   orchestration is not reachable. Do not invent CLI flags.
2. `orca status` must show `runtimeReachable: true`. One Orca runtime is the only runtime; no independently launched
   coding loops. Inspect live Orca Runs/workers and the current GitHub issue/PRs first; never start two workers for one
   issue.
3. `npm run orca -- review-open` before dispatching (login/Cloudflare check).

## 2. Claim and dispatch
- Pick the issue as in orca-flow (named issue, otherwise highest `P<n>` in the title, skip blocked/delivered).
- `npm run auto -- claim --issue <n> --owner <agent> --run <uuid>`; `npm run auto -- auth-check --issue <n> --phase plan ...`
  (`REQUIRES_FOUNDER` → ask the operator; see orca-flow).
- `npm run orca -- dispatch --issue <n> --owner <agent> --run <uuid>` re-checks the lease, authority and runtime,
  resolves the claimed worktree through Orca, writes an exclusive dispatch record (`.local/auto/orca-dispatch/issue-<n>.json`),
  creates the Run and starts one worker. `npm run orca -- plan ...` prints the Orca calls and starts nothing.
- Renew the lease after dispatch, after each `worker_done`, after every review round and repair push, before every wait
  and before `verify-head`.

### Recovery (single attempt, fail closed)
| Failure | Evidence required | Action |
|---|---|---|
| `WORKTREE_UNRESOLVED` | no record or Run exists; `orca worktree show --worktree path:<worktree> --json` succeeds | retry the dispatch once |
| `DISPATCH_RECORD_EXISTS` | record state is `not-started`, confirmed by reading it | delete the record, retry once |
| `WORKTREE_SELECTOR_NOT_FOUND` | record is `not-started` with `selector_not_found` evidence | delete the record, retry once |
| `WORKER_START_FAILED` / `_UNVERIFIED` | `worker-show`/`worker-stop --dispatch <id>` confirms the worker never started or exited | delete the record, retry once |
| `RUNCREATE_FAILED` / `_UNVERIFIED` | none — a Run may exist | stop and report |

A record in `unconfirmed`, `starting` or `started` is treated as a live worker. Never delete a record on `worker-list`
absence alone. `LEASE_LOST_BEFORE_START`, `LEASE_CONFLICT`, `LEASE_LOST`, `DIRTY_WORKTREE` have no recovery: stop and print
the HANDOFF notice from orca-flow.

## 3. Supervise
```bash
npm run auto -- renew --issue <n> --owner <agent> --run <uuid>        # before every wait
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```
Answer `question` with `orca orchestration reply`. On `worker_done`: verify the IDs match the dispatch, `worker-release
--dispatch <id>`, ack the batch. Proceed only on outcome `succeeded`; a failed worker is reported, not retried blindly.

## 4. Review, repair and hand-off
Exactly orca-flow steps 3–7: open the PR (body names the real implementing agent), `review-open/send/wait` in the Hockey
project, fresh chat per round, `repair-record` before each repair (repairs are done by a worker or by the coordinator
inside the same worktree, never concurrently), final fresh review on the exact head, bounded CI polling, `verify-head`,
then **stop** and report. Merge, `release`, `cleanup` and issue update only when the operator authorizes merging. Delete
the chats you created with `review-delete` (list URLs for manual deletion if it fails).

## Report
Issue, PR, head SHA, worker lifecycle (dispatch id, outcome, duration), review verdicts, repair count, CI and
`verify-head` results, chats deleted/left, limitations of the pilot that you observed, and the single blocker if the run
stopped early.

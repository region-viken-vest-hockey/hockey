---
name: orca-fleet
description: Launch and coordinate delivery of several independent GitHub issues in parallel in any Orca-managed repo. The session that starts the fleet stays the coordinator; every issue gets its own claimed worktree registered in Orca as a child of the coordinator's worktree, one supervised worker, and then a serialized review, verify and merge pipeline. Use when the user asks to run a fleet, work several issues at once, or fan work out across worktrees. Use orca-flow for a single issue this session implements itself.
---

# orca-fleet

Fan out N independent issues from one coordinating session. The coordinator **launches, supervises, reviews and merges**; workers only implement and commit. This skill composes existing pieces and owns no delivery logic of its own:

| Concern | Owner (do not re-implement) |
|---|---|
| Per-issue lease, worktree, authority gate, repair ledger | `npm run auto -- …` |
| Worker dispatch, final-head verification, cleanup, ChatGPT browser | `npm run orca -- …` |
| Single-issue steps (implement, gate, PR, review, repair, merge, chat cleanup) | [`orca-flow`](../orca-flow/SKILL.md) steps 3-10 |
| Orca runtime, worker messaging, `worker_done` waits | `orchestration` skill (`orca skills get orchestration`) |

The repo's own instructions (`AGENTS.md`/`CLAUDE.md`) define its gate, conventions and protected domains; this skill never restates them. Read `orca-flow` first; this file only adds what changes when several issues run at once. If a step here conflicts with `orca-flow` on a safety rule (lease, authority, review, verify-head, merge gate), `orca-flow` wins.

## Roles and lineage

- **Coordinator** = the session that starts the fleet. It never edits an issue worktree and never implements. Its worktree is the **parent** of every issue worktree in the fleet.
- **Worker** = one supervised agent per issue, working only in `.worktrees/issue-<n>` on `agent/issue-<n>`. Per the task spec it does not push, open a PR, merge, close the issue, or touch the lease.
- Lineage makes the fleet visible and removable as a unit in Orca: every child is registered under the coordinator's worktree, so `orca worktree ps` shows the tree and ending the fleet means cleaning up that subtree.

Resolve the Orca CLI once (see the `orchestration` skill: `ORCA_CLI_COMMAND`, else `orca-ide` on Linux outside an Orca terminal, else `orca`) and use it everywhere below as `orca`.

## Preconditions (stop and tell the user if any fails)

- `orca status` shows `runtimeReachable: true`; `npm run orca -- review-open` returns `ok: true`; `gh auth status` is logged in.
- Every shell that runs the repo's gate or `git push` has the toolchain the repo expects on `PATH` (virtualenv, node version manager, hooks' linters). A gate or pre-push hook that fails with a missing tool is an environment problem: fix `PATH`, never bypass the hook, and note that tests which skip when an optional dependency is absent prove nothing.
- The coordinator's checkout is clean on `main` and up to date (`git pull --ff-only`). Record its identity: `orca worktree current --json` → keep `result.worktree.identity.key` as `PARENT`.
- Never run `git checkout`, `git stash` or `git restore` with a path or `.` in the coordinator checkout. The lease store (`.local/auto/`) may be tracked there, and reverting it erases every lease and repair record. Check with `git ls-files .local/auto`.

## 1. Choose the fleet

1. List every open issue (`gh issue list --state open --limit 200 --json number,title,labels`) and read bodies of candidates. Order by the `P<n>` prefix/label. Skip issues that are blocked, already delivered (`git log --all --grep "#<n>"`, merged PRs), explicitly deferred or scheduled for later, or in need of a decision from the user.
2. Cap concurrency. Default **3 workers**, never more than the user asked for. The repo's full gate is CPU-heavy, so more than 3 mostly adds queueing, not throughput; lower the cap further if one gate run is slow on this machine.
3. **Independence check** before launching a set. Two issues may run together only if their expected edit sets do not overlap: compare the modules/tests each issue names and search the owners (`rg`, call graph). Overlap on a shared file (a hot module, a docs index, a baseline/snapshot/lockfile, a generated catalog) means **serialize them**, do not parallelize. When unsure, serialize.
4. Print the plan to the user before launching: issue, title, priority, expected files, who depends on whom, and which are serialized. Proceed without further confirmation only when the user's request already named the issues or said to run the fleet.

## 2. Launch each worker

For each chosen issue, in order, with a fresh `RUN=$(python3 -c 'import uuid;print(uuid.uuid4())')` per issue (keep issue → run-id in a scratch file; a lease is per issue):

```bash
npm run auto -- claim --issue <n> --owner claude --run $RUN        # creates .worktrees/issue-<n>
npm run auto -- auth-check --issue <n> --phase plan --owner claude --run $RUN
npm run orca -- dispatch --issue <n> --owner claude --run $RUN     # waits for Orca to resolve the worktree, then run-create + worker-start
orca worktree set --worktree path:<repo>/.worktrees/issue-<n> --parent-worktree identity:$PARENT --issue <n> --json
```

Set the parent **after** `dispatch`: Orca must already track the new git worktree, and `dispatch` is the step that waits for that. A `selector_not_found` from `set` means Orca does not (or no longer) track that path; it is not a reason to retry in a loop.

- `LEASE_CONFLICT` on a chosen issue: drop it from the fleet and pick the next candidate; never retry in a loop.
- `REQUIRES_FOUNDER` from `auth-check`: that issue leaves the fleet (the command parks it and releases the lease). Ask the user in chat; the rest of the fleet continues.
- Verify lineage once all are launched: `orca worktree ps --json` must show each child's `parentWorktreeId` equal to the coordinator's worktree id (`id:<repo-id>::<path>`). If `set --parent-worktree` is rejected, report the exact error and continue without lineage rather than blocking the fleet.
- Any `dispatch` failure follows the recovery table in `orca-deliver` §3; if recovery is not conclusively safe, stop that issue and report.

## 3. Supervise (one loop for the whole fleet)

```bash
# renew EVERY live issue's lease before each wait; 15 min wait vs a 30 min TTL
for each live issue: npm run auto -- renew --issue <n> --owner claude --run <run-id>
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 600000 --json
```

- Answer a `question` with `orca orchestration reply`. An `escalation` for one issue stops only that issue.
- On `worker_done`: match the IDs to that issue's dispatch, `worker-release --dispatch <id>`, ack. Only `outcome: succeeded` continues; otherwise report the worker's failure summary and take that issue out of the fleet (no automatic re-dispatch beyond the single recovery step above).
- A lease problem on any issue (`LEASE_LOST`, `LEASE_CONFLICT`, `DIRTY_WORKTREE`) ends **that** issue with the `HANDOFF` notice from `orca-flow` step 2. Do not touch its worktree. Other issues continue.

## 4. Per-issue pipeline: parallel to build, serial to ship

As each worker finishes, move that issue through `orca-flow` steps 4-9, with these fleet rules:

- **Local gate and PR** (step 4-5) may run for several issues at the same time, but not more than 2 concurrent runs of the repo's gate on the machine; queue the rest.
- **ChatGPT review is serialized**: there is one Orca browser tab, so run one `review-open` / `review-send` / `review-wait` at a time. Each review and repair round is still a fresh chat, and every chat's first-message file is recorded for deletion (keep issue, page id and message file together in scratch).
- **Merge is serialized, one PR at a time**:
  1. take the next issue whose final fresh-reviewer pass found no blocking issues and whose CI is green;
  2. if `main` moved since its head was verified, rebase the branch onto `origin/main` in its worktree (never merge `main` in: many repos forbid merge commits, so rebase everywhere), rerun the repo's gate, push, and treat the new head as needing `verify-head` again; a rebase that changes behavior or conflicts in a non-trivial way sends that issue back through review;
  3. `verify-head`, merge `auth-check`, `gh pr merge <pr> --squash --delete-branch --match-head-commit <sha>`;
  4. `git pull --ff-only` in the coordinator checkout (only that command; see precondition on `state.db`), `release` the lease, then merge the next.
- After each merge, re-check the remaining fleet for newly overlapping files; if two open PRs now touch the same file, serialize them and rebase the later one.
- Tooling-only issues follow the single-repair-round rule from `orca-flow` step 7. All repair caps and the `repair-record` ledger apply per issue.

## 5. Finish

- For every merged issue run `npm run orca -- cleanup --issue <n>` and delete its flow-created ChatGPT chats with `review-delete` (step 10 of `orca-flow`). `LIVE_SESSION` from cleanup usually means editor or language-server processes hold the worktree as their cwd: report it, do not force removal. Remove stale children so no orphan lineage remains in `orca worktree ps`.
- Update or close each issue per `orca-flow` step 9; partial slices stay open with a comment naming what remains.
- Final report, one table: issue → PR → merge commit → verdict/findings fixed → chats deleted → issue action, followed by the fleet-level facts: which issues were serialized and why, anything dropped or handed off (with the exact code), lease or ledger anomalies, chats that could not be deleted, and anything skipped.

## Hard limits

- The coordinator does not implement. If it must change code (for example resolving a rebase), it does so inside that issue's worktree under the issue's own lease and records it in the report.
- No second scheduler, timer, daemon or extra Orca server. This skill is a procedure the coordinating session executes; `npm run orca -- tick` (the optional dispatcher) is a separate, operator-enabled mechanism and is not part of this flow.
- Issues in a protected domain (anything `auth-check` reports as `REQUIRES_FOUNDER`, such as payments, pricing, publication, irreversible data or live production state) are not fleet work: they are parked and go to the user. Never use live or production systems as a test.
- A run ends with merged PRs and a written per-issue blocker for everything else. Do not leave an issue claimed without saying so in the report.

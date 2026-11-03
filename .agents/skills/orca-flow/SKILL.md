---
name: orca-flow
description: Deliver one hockey GitHub issue end to end in this session: claim it, implement it on an agent/issue-N branch following the shared implement-issue procedure, open a PR, get an independent ChatGPT review on the exact head through the Orca browser, repair bounded findings, and stop at a merge-ready PR (merge only on explicit operator authorization). Use when the user asks to run the orca flow, deliver an issue end to end, or take an issue through ChatGPT review.
---

# orca-flow

One issue, `main` to a reviewed, merge-ready PR. This skill only adds the **claim, review and cleanup mechanics**.
Repository policy is not repeated here: implementation and verification follow
[`.agents/commands/implement-issue.md`](../../commands/implement-issue.md) and `AGENTS.md`; pushing follows
`docs/ci.md` (local pre-push gate). RVV season work additionally loads the RVV router in
[`../rvv/SKILL.md`](../rvv/SKILL.md). Do not launch a nested `claude -p` or `pi -p`.

**Authority:** delivering an issue never authorizes publication, rollback, booking confirmation or a live-season
mutation, and **never authorizes merging**. Merge only when the operator says so in this session.

## Tooling

```bash
npm run auto  -- claim|renew|release|auth-check|repair-record|repair-status|status ...   # lease, worktree, authority, repair ledger
npm run orca  -- review-open|review-send|review-wait|review-read|review-delete|verify-head|cleanup ...
```

State lives under `.local/auto/` and worktrees under `.worktrees/` (both git-ignored). `gh` must be authenticated for
`region-viken-vest-hockey/hockey`, Orca running (`orca status`), and the user logged in to chatgpt.com in Orca with
the GitHub connector enabled. If a precondition fails, stop and say which; do not work around it. Run
`npm run orca -- review-open` **before claiming** so a login or Cloudflare check is handled up front (it opens a tab in
the **Hockey** ChatGPT project; keep its `pageId`).

## 1. Pick and read the issue
- Use the named issue, otherwise the highest-priority open one (priority is the `P<n>` in the title; it orders work and
  never excludes an issue). Skip issues with an open `depends on`/`blocked by` or waiting on an external decision.
- Read it with `gh issue view <n> --json title,body,comments` (plain `--comments` is broken by the classic-Projects
  GraphQL deprecation).
- **Already-delivered check:** `gh pr list --state all --search "<n>"` and `git log --all --grep "#<n>"`; confirm the
  feature is absent from `main` by reading the code. An issue partly delivered is a *slice*; name the slice in the PR.
- Then follow the *Start with live evidence* steps of `implement-issue.md`.

## 2. Claim
```bash
git status --short && git checkout main && git pull --ff-only       # no tracked changes allowed
npm run auto -- claim --issue <n> --owner <claude|pi> --run <fresh-uuid>   # prints lease + worktree
npm run auto -- auth-check --issue <n> --phase plan --owner ... --run ...
```
Work only inside the printed `.worktrees/issue-<n>` on branch `agent/issue-<n>`. `LEASE_CONFLICT`, `LEASE_LOST` or
`DIRTY_WORKTREE` anywhere means another session may hold the issue: stop, do not retry/reset/clean, and report the
holder, branch, last head, PR and review chats. The 30-minute lease must be renewed
(`npm run auto -- renew ...`) after the local gate, after every `review-wait`, after every repair push and before
`verify-head`.

`auth-check` returns `REQUIRES_FOUNDER` for work touching the live season, publication, or shared scheduling
rules/hosting. Show the operator the domain, evidence and your intended choice; only their explicit approval in this
chat counts (never an owner-attributed GitHub comment/label), then `npm run auto -- founder-resume ...` and re-run the
check. `CHECK_UNAVAILABLE` is never a pass; `CLEAR` only means no signal was detected.

## 3. Implement, verify, open the PR
Follow `implement-issue.md` (owner-boundary fix, regression test first, `scripts/check lint quick` before handoff,
exact commands/outcomes recorded). Conventional commit prefixes; do not invent a model identity or add a vendor
`Co-Authored-By` trailer; stage files by explicit path. Push and `gh pr create --base main --head agent/issue-<n>`
with a body that states the slice, behaviour changes, tests, what is not covered and the implementing agent. Use
`Refs #<n>` unless the PR fully delivers the issue (`Closes #<n>`). Write PR bodies to a scratch file outside the repo
and update with `gh api -X PATCH repos/<owner>/<repo>/pulls/<pr> -F body=@file` (`gh pr edit` fails on the Projects
deprecation).

## 4. ChatGPT review (Orca browser only)
Use only the `review-*` commands; they own project-id checks, focus, draft checks, Send and reply polling. Do not use
Chrome DevTools, Playwright or hand-rolled `orca` clicks. Every review/consult chat must be inside the **Hockey**
project; a chat elsewhere is invalid. A failing `review-*` command is a blocker: report its `code`.

```bash
npm run orca -- review-open                                   # {ok,pageId}
npm run orca -- review-send --page <pageId> --message-file <scratch>/review.txt
npm run orca -- review-wait --page <pageId>                   # run again if REPLY_TIMEOUT or still "working"; read {text}
```
Request text (one self-contained message; ChatGPT cannot see the checkout): *"Use your GitHub connector to review pull
request #<pr> in region-viken-vest-hockey/hockey at head <full-sha>. Focus on correctness bugs, canonical-write safety
(revision binding, atomicity, idempotency), ownership of the canonical mutation path, sealed-season/published
reconciliation, and weak tests. Report blocking findings first, ranked by severity, with file and line, the failure
scenario and your confidence; if none, say so plainly."* Read the verdict from `text`, not from memory.

## 5. Repair loop
- Verify each P1/P2/P3 finding against the code before changing it; if wrong, say why in the PR.
- Before each repair record it: `npm run auto -- repair-record ... --pr <pr> --head <40-char sha> --findings-file <f>`
  (`REPAIR_LIMIT` parks the issue; stop and report). Fix at the canonical owner, re-run the gate, push, renew the lease.
- **Fresh chat per round:** every re-review starts with `review-open` and names the new head; never reuse the previous
  chat or paste its verdict. The merge gate is a final fresh review on the exact final head with no blocking findings.
- Tooling-only changes (`tools/`, `.agents/`, `docs/`) get one repair round; remaining P2/P3 become a follow-up issue.

## 6. Hand off (merge only if authorized)
Wait for CI with bounded polling (`timeout 600 gh pr checks <pr> --watch --fail-fast`, renew between rounds; no checks
at all is recorded, not a failure), then `npm run orca -- verify-head --issue <n> --pr <pr> --owner ... --run ...`
(requires `ok:true`; reruns `scripts/check lint quick` on the exact head). Report the PR, head SHA, review verdict, CI
and verify-head result and **stop**. If — and only if — the operator authorizes merging:
`auth-check --phase merge ...`, then `gh pr merge <pr> --squash --delete-branch --match-head-commit <verified-sha>`,
`npm run auto -- release ...`, `npm run orca -- cleanup --issue <n>`, and comment on / close the issue.

## 7. Delete the review chats
Delete only chats this flow created, with `npm run orca -- review-delete --page <pageId>` (it requires a conversation in
the Hockey project and closes the tab). It does not compare message text, so pass only pages you created. If it fails
(`DELETE_STEP_MISSING`, `TITLE_UNKNOWN`), list the chat URLs in the report for manual deletion; never force it.

## When stuck
Consult ChatGPT in a fresh Hockey-project chat (same commands) before asking the user about design or scope: send the
flow step, the failure or candidates, issue/PR numbers, and ask for one recommendation with confidence, assumptions and
citations. Verify every factual claim against the code; the answer is advisory and never approves a merge, a policy
change, or any operator-authority decision (those go to the user, naming the recommendation). One consult per stuck
point; a failed consult is a blocker to report, not retried in a loop.

## Report
PR number/URL, head SHA, ChatGPT verdicts and findings fixed, consults, CI and `verify-head` results, issue action,
chats deleted or left, untracked files noted, and anything skipped. If the run stopped early, name the single blocker
with its exact error.

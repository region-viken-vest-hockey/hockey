---
name: orca-flow
description: End-to-end issue delivery for the Campsty repo (niclas-lindgren/camping-booking). Checks out main and pulls, picks the next (or a named) GitHub issue by its priority label, implements it on an agent/issue-N branch, runs the local gate (`mise run check`), opens a PR, requests a review from ChatGPT through the Orca browser tab using its GitHub connector, fixes the findings, squash-merges to main with `gh pr merge` once ChatGPT and CI are satisfied, then updates or closes the issue and deletes the ChatGPT chats it created. Use when the user asks to run the orca flow, deliver an issue end to end, or take an issue through review to merge.
---

# orca-flow

Deliver one GitHub issue from `main` to merged PR, with a ChatGPT review in between.
This canonical skill works in an existing authenticated **Claude Code or Pi.dev session**. In a multi-issue
session, `/goal` (Claude Code) or a repeated `/skill:orca-flow` (Pi) applies this
single-issue procedure once per issue (#991). Do not launch a nested `claude -p` or `pi -p` to
perform this flow.
The review runs in the user's own ChatGPT account, in the **campsty** project, through the
Orca browser tab. It never pastes the diff: ChatGPT reads the PR through its GitHub connector.

Running this flow is the user's explicit request for a branch and pull request, which
`.claude/rules/git-workflow.md` otherwise forbids for this repo.

## Which Orca skill?

| Skill                       | Use when                                                                                 |
| --------------------------- | ---------------------------------------------------------------------------------------- |
| `orca-flow` (this)          | This session implements one issue itself, end to end. Default choice.                    |
| `orca-deliver`              | A coordinator dispatches supervised workers (pilot); it reuses step 6 for review.        |
| `orca-cli`, `orchestration` | Low-level Orca operations or worker protocol; invoked by the two above, rarely directly. |

## Preconditions

- Orca is running (`orca status` shows `runtimeReachable: true`).
- The user is logged in to chatgpt.com in Orca, the **campsty** project exists there, and the
  GitHub connector is enabled.
- `gh` is authenticated for `niclas-lindgren/camping-booking`.
- `npm run orca -- review-open` returns `ok: true` (right project, logged in, composer present).
  Run it **before claiming an issue**, so a login or Cloudflare check is handled up front rather
  than after the PR is ready. Keep its `pageId` for step 6 (run `review-open` again if it is lost).

If any precondition fails, stop and tell the user which one. Do not work around it.

## Ask ChatGPT before you ask the user

This is a hard rule, not a fallback. Before you write any question to the user about behaviour,
scope, priority, UX copy, design, or which of several semantic options to implement, consult
ChatGPT first (see _When stuck_). This applies inside the flow and to work outside it.

The user is asked only when:

- the decision is one only the user can make: a legal, tax, payment or go-live approval, or a
  tooling or git-guard policy (`.claude/rules/product-decisions.md`);
- the consult failed (no complete answer, login prompt, page not ready); or
- the consult's answer is contradicted by the code.

When you do ask the user, name the consult's recommendation and say why you did not take it.

Inside this flow, do not use AskUserQuestion for anything else. In a harness without that tool (Pi), put the same question to the user as a plain chat message, stop, and continue only on their reply in that session. A run ends in one of two ways: a
merged PR, or a written blocker in the final report that names the failed precondition or failed
consult. Do not end a run without one of them.

## 1. Start from main

```bash
git status --short            # no tracked changes allowed; if there are any, stop and report
git checkout main
git pull --ff-only
```

Untracked files this flow does not touch (for example `.claude/worktrees/`) do not stop the
flow. Name them in the final report. Stage files by explicit path, never `git add -A`.

## 2. Pick the issue

- If the user named an issue, use it.
- Otherwise list **every** open issue, unlabeled ones included:
  `gh issue list --state open --limit 200 --json number,title,labels`. Read each title before
  filtering. Do not stop after the priority-labelled subset.
- Order the candidates by their single `priority: P<n>` label, following
  `docs/engineering/issue-priority-policy.md` and `docs/agents/workflow.md`. Priority orders the
  work and never excludes an issue: a P3, P4 or unlabeled issue is a valid pick. Skip issues with
  an open blocking dependency or whose body says they wait for an external decision.
- Before claiming a candidate, check whether its work already landed:
  `git log --all -i --grep "#<n>"` and `gh pr list --state all --search "<n> in:title,body"`.
  Confirm the feature is absent from `main` (look at the code, not only the issue comments).
  Comments can be stale; a claim on delivered work wastes a lease.
- "No pickable issue" is a valid outcome only when you have checked every open issue. Report it
  as a table with one line per issue giving its reason (blocked, delivered, verification only,
  product decision, or too large for one slice). Never report it from a subset.
- If the choice is still ambiguous, consult ChatGPT (see _When stuck_) with the candidates and
  your most likely reading, then act on its answer only if it names one issue and the issue
  bodies support it.
- Read the issue and any linked PR or branch first (`gh issue view <n> --comments`).
  An issue already partly delivered by open or merged PRs is a _slice_. Name the slice
  you are taking in the PR body.
- If the remaining acceptance is only verification (for example browser checks), name that in
  the report, then check the code paths the issue names. If they contain a semantic choice the
  issue leaves open, consult ChatGPT before deciding it. Do not stop silently.

Claim the issue before any work, so another local Campsty agent using the same SQLite lease store cannot take it at the same time:

```bash
npm run auto -- claim --issue <n> --owner <agent-name> --run <run-id>
```

`<agent-name>` is the harness running this flow, for example `claude` under Claude Code or `pi` under pi.
Use a fresh UUID for `<run-id>` and keep them for every later call. The
command prints one JSON line with `lease` and `worktree`. On `LEASE_CONFLICT`, pick another
issue if the choice was yours; if the user named the issue, stop and report the holder. Never
retry the same claim in a loop.

**Lease heartbeat (required).** The claim TTL is short (30 minutes), and a manual review loop can
outlast it; an expired lease lets another session legally take the issue over and redo the review.
Renew with `npm run auto -- renew --issue <n> --owner <agent-name> --run <run-id>` at each of these
points: after the local gate, after every `review-wait` returns, after every repair push, and
immediately before `verify-head`. Any single wait or fix step that may exceed 15 minutes needs a
renew before it starts.

**Takeover detection and handoff.** `LEASE_CONFLICT`, `LEASE_LOST` or `DIRTY_WORKTREE` from
`claim`, `renew`, `repair-record` or any later step means another session may hold or have changed
this issue. Stop immediately; do not retry, re-claim, `git reset` or clean the worktree. Print a
handoff notice and end the run with the blocker named in the final report:

```text
HANDOFF: issue #<n> is held or changed by another session.
code: <LEASE_CONFLICT|LEASE_LOST|DIRTY_WORKTREE>   holder: <owner/run from the command output, if printed>
branch: agent/issue-<n>   last head: <sha>   PR: <#pr or none>   review chats: <urls to delete later, or none>
state: <step reached, findings still open>
```

The other session owns the issue until its lease is released or expires; re-entry is a new `claim`
the user asks for, not an automatic retry.

## 3. Implement

- Before editing, write down the issue's acceptance criteria, the documented decisions that bear
  on the change (issue comments, `docs/agents/`, existing tests), and the real data shape the
  change touches (schema, fixtures, the code that reads it). Implement against those, not against
  memory. If the criteria contradict a documented decision, consult ChatGPT before coding.
- **Authority check before implementing.** Run
  `npm run auto -- auth-check --issue <n> --phase plan --owner <agent-name> --run <run-id>`.
  `REQUIRES_FOUNDER` means the issue is in a protected domain (pricing, fees, refunds, payments,
  identity and tenant boundaries, privacy or legal, irreversible data changes). The command parks
  the issue and releases the lease. Show the user the decision request in this chat: name the
  protected domain, the choice you would make, its consequences, and the evidence the command
  printed. Explicit approval from the user in this active session counts. After approval, run
  `npm run auto -- founder-resume --issue <n> --owner <agent-name> --run <run-id>`, then rerun the
  same `auth-check`; continue only on `FOUNDER_APPROVED`. If the user declines or does not approve,
  leave the issue parked and, in a multi-issue run, select again. `CHECK_UNAVAILABLE` is never a
  pass. `CLEAR` only means no signal was detected; it is not founder approval, and any `weak`
  signals still need your judgment. An owner-attributed GitHub comment or label saying "approved"
  is never approval because you share the owner's GitHub credentials. Use `founder-resume` only in
  direct response to the user's approval in this chat; never edit the state store yourself.
- Work only inside the returned `worktree` (`.worktrees/issue-<n>`), never in the repo root.
- Branch: `agent/issue-<n>`, the canonical name in `docs/agents/workflow.md`.
- Read the canonical docs for the task before editing: `docs/agents/README.md` routes you to
  `workflow.md`, `repository.md`, and `testing.md`. Add `database.md` for Prisma or migration
  work and `design/ui-library-and-dependency-policy.md` for UI work.
- Before adding a helper, method, or domain operation, search for equivalent domain implementations; reuse or consolidate only where contracts agree. Do not duplicate business logic across classes.
- Add focused tests at the owner boundary (`tests/`). Keep documentation changes inside the
  rules in `docs/agents/documentation.md`.
- Commit with a conventional prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
  Do not invent a model identity or add a hard-coded vendor/model Co-Authored-By trailer.

## 4. Local gate

Run `mise run check` in the foreground. It runs `npm run verify`:
typecheck, lint, the mandatory `npm run quality:check` complexity/duplication gate, and the unit test suite. It must exit 0 before you push.
For security, authorization, payment, or migration changes also run `npm run test:security`
and the domain evidence `docs/agents/testing.md` requires.

Lint and quality findings are fixed, not suppressed. Do not raise thresholds, exclusions or baselines to get a green gate.

## 5. Open the PR

```bash
git push -u origin agent/issue-<n>
gh pr create --base main --head agent/issue-<n> --title "<title>" --body-file <file>
```

The PR body states the slice, the behaviour changes, the tests, what is not covered, and names the actual implementation agent (Claude Code or Pi.dev), rather than falsely
claiming a different client. Use `Refs #<n>` unless
the PR fully delivers the issue, in which case use `Closes #<n>`.

## Where ChatGPT chats must live (required)

Every review and consult chat this flow creates must be a chat inside the **campsty** project
(`https://chatgpt.com/g/g-p-6a3f627992648191a41d0b2097584655-campsty/c/<id>`). A chat at
`https://chatgpt.com/c/<id>` has no project segment and is not valid, even if it was started from
the sidebar. Never type into, send to, or delete a chat the flow did not start.

## 6. Request the ChatGPT review (Orca browser)

The ChatGPT review uses the **Orca browser only**, through the `review-*` commands below. They own
the project URL, focus handling, the `-campsty/` URL check, the draft check, the Send button and
reply polling, so do not hand-roll `orca tab`, `snapshot`, `fill`, `click` or `eval` for this, and
do not use Chrome DevTools, Playwright, Computer Use or another browser profile for it. Do not
judge availability from `orca browser --help`; that namespace only manages browser identity.
If a `review-*` command fails, stop and report its `code`; do not work around it.

```bash
npm run orca -- review-open                                   # {ok, pageId}: right project, logged in, composer present
printf '%s' "Use your GitHub connector to review pull request #<pr> in niclas-lindgren/camping-booking at head <sha>. Focus on correctness bugs, payment and booking-inventory correctness, permission and tenant boundaries, data migrations, and weak tests. Report blocking findings first, ranked by severity, with file and line, the failure scenario and your confidence. If there are no blocking findings, say so plainly." > <scratchpad>/review.txt
npm run orca -- review-send --page <pageId> --message-file <scratchpad>/review.txt   # {ok, url} only for a project chat
npm run orca -- review-wait --page <pageId>                   # polls until two identical complete reads; prints {text}
```

`review-read --page <pageId>` is a single read (`complete: true` once the reply ends with
`ChatGPT can make mistakes`). Read the verdict from `text`, not from memory or from
`orca snapshot` (long answers show bare `paragraph` nodes). A login or Cloudflare prompt shows up
as `NOT_LOGGED_IN` or `COMPOSER_MISSING`: stop and tell the user. Same-chat follow-ups with `review-send`/`review-wait` on the same `--page` are only for polling an
incomplete reply, _When stuck_ consults and challenges; every new review round of a PR head must start
with a new `review-open` and a newly created project chat (step 7). `REPLY_TIMEOUT` means no complete
reply yet; run `review-wait` once more, not a loop of retries.

## 7. Fix findings, then re-review

- Treat every P1/P2/P3 finding as a comment to address. Verify each one against the code
  before changing it. If a finding is wrong, say why in the PR, not by silently ignoring it.
- Fix on the same branch, run `mise run check`, commit, push.
- Renew the lease after every repair push (see step 2).
- **Fresh context per review round.** Do not continue the previous review chat. Anchoring on an
  earlier verdict made five same-chat rounds converge on "approved" while one fresh review found
  a real correctness bug. Each round runs `review-open` for a new chat in the campsty project and
  sends a self-contained request naming the PR, the new head commit and the review focus. Do not
  paste the earlier findings or verdict into it; verify the earlier fixes yourself against the
  code. Record every chat as a flow chat for step 10. Repeat until a round reports no blocking issues.
  (Follow-ups in the same chat remain valid for _When stuck_ consults and for challenging an
  incorrect claim, not for re-reviewing a head.)
- **Final fresh-reviewer pass (merge gate).** After the last repair, and before `verify-head`, run
  one more review in a brand-new chat on the exact final head with the step 6 request unchanged.
  Merge needs this pass to report no blocking findings. If it finds blocking issues, repair
  (subject to the repair cap), push, and run another fresh pass on the new head. Under
  _Tooling-only changes_ below, this pass is still required; only its P2/P3 findings may become a
  follow-up issue.
  In a multi-issue session, cap substantial repairs at **two per distinct
  finding**; unresolved blocking findings stop promotion and must not be waived.
  The count is kept in the shared ledger, not in the conversation, so it survives a context
  reset or a hand-off to another session. Before each repair, write the review's P1/P2/P3
  findings (`[{"severity","file","summary"}]`) to a file in the scratchpad and run, on the
  exact reviewed head with a clean worktree:

  ```bash
  npm run auto -- repair-record --issue <n> --owner <agent-name> --run <run-id> --pr <pr> --head <40-char head> --findings-file <file>
  ```

  `RECORDED` or `ALREADY_RECORD` allows the repair. `REPAIR_LIMIT` (a finding already repaired
  from two other heads, or six repaired heads on the pull request) parks the issue and releases
  the lease: stop and report it. `STALE_HEAD`, `DIRTY_WORKTREE`, `ISSUE_BLOCKED`, `LEASE_LOST`
  and `GITHUB_UNAVAILABLE` also mean do not repair. `npm run auto -- repair-status --issue <n>`
  shows the ledger.

- **Tooling-only changes** (only `tools/`, `.agents/`, `docs/agents/`, `docs/operations/` and their
  tests, no product code) get one repair round. After it, remaining P2/P3 findings become a
  follow-up issue, and only a P1 correctness or safety finding blocks the merge. Name this in the
  PR and the final report. Do not keep repairing infrastructure to satisfy every review remark.
- If a finding needs a product or policy decision, or you cannot tell how to resolve it,
  consult ChatGPT (see _When stuck_) before deciding.

## 8. Promote with squash merge

Promote only when **both** hold:

- The final fresh-reviewer pass (step 7) on the exact head being merged says ready to merge, or
  has no blocking findings.
- Every CI check on the PR is `pass`. Wait with bounded polling, not an open-ended watch: renew the lease (`npm run auto -- renew ...`), then run
  `timeout 600 gh pr checks <pr> --watch --fail-fast` and read its exit status; if it timed out (exit 124), renew
  and repeat, and stop with a `HANDOFF` if a renew fails. Do not parse its output with `--json` or any other flag the installed
  `gh` does not list in `gh pr checks --help`; an unsupported flag can make a check look passed.
  If `gh pr checks` reports no checks at all, record that in the report and rely on the
  local gate (`mise run check` on the PR head). Do not treat "no checks" as a failure.

Before merge, the coordinating session must independently verify the exact latest PR head in addition to the review and CI conditions. A worker-reported green gate is not independent confirmation. Do not add unrelated finalize steps or authority fingerprints.

After the last review/repair/push, run from the coordinator checkout:

```bash
npm run orca -- verify-head --issue <n> --pr <pr> --owner <agent-name> --run <run-id>
```

Require exit 0 and JSON `ok: true`, `verified: "npm run verify"`. Record the returned `head` SHA. This fetches the current base, ensures the PR and clean worktree match, independently reruns the full canonical gate (including `quality:check`) and checks both SHAs afterward. Any failure, changed PR or missing verification blocks merge. Do not commit or push after verification without rerunning it.

Also run, on the pushed head with a clean worktree:
`npm run auto -- auth-check --issue <n> --phase merge --pr <pr> --owner <agent-name> --run <run-id>`.
Merge only on `CLEAR` or `FOUNDER_APPROVED`. `REQUIRES_FOUNDER` (a newly detected protected domain
in the diff, for example under `src/lib/payments/`, `prisma/migrations/` or auth code) parks the
issue and releases the lease. Ask for approval in this chat with the same decision-request format
as the plan check. On explicit approval, run `founder-resume`, rerun the merge `auth-check`, and
merge only when it returns `FOUNDER_APPROVED`; otherwise the PR stays unmerged. This check is a
procedure you follow, not a technical barrier against a session that skips it.

Then squash-merge the PR and delete its branch:

```bash
gh pr merge <pr> --squash --delete-branch --match-head-commit <verified-head-SHA>
```

The squash commit title is the PR title. Keep `Refs #<n>` in the PR body for a partial
slice, so the merge does not close the issue. Use `Closes #<n>` only when the PR fully
delivers it.

After the merge:

- `git checkout main && git pull --ff-only` in the repo root.
- Run `release` with the same owner flags to free the issue lease.
- Remove the worktree and local branch (see _Cleanup_).

Never merge with a failing check, an open blocking finding, or an unresolved "changes requested".
If a check fails and you cannot see a fix, consult ChatGPT (see _When stuck_).

## Cleanup

After a merged PR, run:

```bash
npm run orca -- cleanup --issue <n>
```

It removes `.worktrees/issue-<n>` and `agent/issue-<n>` only when a merged PR exists for the
branch, the worktree is clean, and no process (for example another Claude or Pi session) has its
working directory inside it. On `DIRTY_WORKTREE`, `LIVE_SESSION` or `NOT_MERGED`, report the
code instead of forcing removal.

## 9. Update or close the issue

- Before closing, apply the documentation cleanup rules in `docs/agents/documentation.md`:
  update any canonical source the work made stale, and remove temporary WIP notes.
- Squash merge closes the issue only when the PR body says `Closes #<n>`. Check the issue
  state with `gh issue view <n> --json state`.
- If the PR delivers only a slice, or the issue is still open: `gh issue comment <n> --body "..."`
  naming what was merged and what remains. Leave the issue open.

## 10. Delete the review and consult chats

Delete only the ChatGPT chats this flow created (review and consult chats), and nothing else.

- Identify them by the first message text you sent (the review request or the consult question),
  not by title. Open each and confirm that first message, then:
  `Chat actions` → `Delete` → `Delete chat`.
- Include flow chats outside the project. A review sent to `chatgpt.com/c/<id>` by mistake is
  still a flow chat and must be deleted.
- The sidebar can list one chat twice (in the project list and under Recents). Delete it once,
  then re-read the sidebar and confirm no entry with that first message remains.
- Delete with `npm run orca -- review-delete --page <pageId>`. It requires a conversation inside the campsty
  project, deletes through the sidebar entry, confirms no entry with that title remains and closes
  the tab. It does not compare the first message, so pass only a page this flow created. A chat that
  has no title yet (`TITLE_UNKNOWN`) needs its reply first.

- Keep scratch files in the scratchpad directory, never in the repo.
- `review-delete` verifies the deletion itself; trust its `ok`, not a click result.
- Never delete a chat the flow did not create, even if its title looks similar.

## When stuck: consult ChatGPT

Whenever the flow reaches a point without a known next action, consult ChatGPT before you
stop to ask the user. Stuck points include: an ambiguous issue choice, an issue that does
not say how to slice or scope the work, a finding or CI failure you cannot resolve, a
design choice the issue leaves open, a merge conflict you cannot resolve safely, and a
slice boundary you cannot name.

Not stuck, so do not consult: a failed precondition (Orca not running, not logged in to
chatgpt.com, `gh` unauthenticated). ChatGPT cannot fix those. Stop and tell the user.

Procedure:

1. Run `review-open`, following _Where ChatGPT chats must live_, then send with `review-send`
   (step 6). Record the chat as a consult chat for step 10.
2. Send one self-contained question. ChatGPT cannot see the local repo or uncommitted
   state, so the message must carry the facts: the flow step, the candidates or the
   failure text (short), the issue or PR numbers, and the decision needed. Ask it to use
   the GitHub connector to read the named issues or PR, and to recommend one option with
   the reasons and a confidence. Ask it to list every assumption it made about the codebase,
   labelled as an assumption, and to say which it could not verify. Ask it to cite the issue
   comment, file path or PR line behind each factual claim, so the claim can be checked (step 4)
   and challenged (step 4a). Do not paste diffs, secrets or tokens.
3. Wait for the complete reply with `review-wait` (step 6) and read the answer from its `text`.
4. Verify the answer before acting on it. ChatGPT is advisory and cannot see the repo. Check
   each factual claim about the code or the issue bodies against the source, and check each
   listed assumption. Its answer is not a merge approval. A recommendation that rests on a claim
   the code contradicts must not be applied.
   4a. Challenge an answer you find incorrect or implausible. Do not drop it silently and do not
   accept it silently. Send a follow-up in the same consult chat (`review-send` again, same `--page`) that states:
   - the specific claim you think is wrong, quoted from the reply;
   - the evidence, with `file:line` references or the issue text you checked, short, no diffs;
   - a request that ChatGPT re-check that claim against the GitHub connector and either
     correct it with evidence or defend it with evidence, and restate its recommendation.
     Wait for the complete reply and read it as in step 3. Then verify it again as in step 4.
     Challenge rounds are capped at two per consult. A follow-up does not start a new consult.
     If the answer still contradicts the code after two rounds, go to step 5 and say so.
     Challenge when the claim is factually wrong against the code or the issue, when it rests on
     something you can disprove, or when its reasoning does not follow from the facts. A
     recommendation that is plausible but unverified is not a reason to challenge; check its
     assumptions instead.
5. If the answer is supported by the evidence, act on it and record in the final report
   that ChatGPT recommended it and what you checked. Product, behaviour, UX and design choices
   are decided this way. If the answer is unsupported or contradicts the code after step 4a, do
   not act on it. Ask the user, and name ChatGPT's recommendation, the challenge you sent and its
   reply, and say why you did not take it. Ask the user directly only for the decisions listed in
   _Ask ChatGPT before you ask the user_.

Tooling and git-guard questions (hooks, CI policy) are repo decisions. Ask the user in the
terminal rather than ChatGPT, as `.claude/rules/product-decisions.md` specifies.

Also consult when a fix changes the meaning of data other code reads. Examples: an occupancy
or capacity figure used by the dashboard, reports or insights; a status used by payments. Check
every consumer in the repo first (`rg`), then ask.

Consult at most once per stuck point. Challenge rounds (step 4a) continue that same consult and
do not count as a new one. If the consult itself fails (no complete answer, login prompt, page
not ready), stop and tell the user. Do not retry in a loop.

## Consults outside the flow

The same rules apply to any ChatGPT consult the assistant starts, including product, UX and
accessibility questions that do not belong to an issue step. Such a consult must use the
campsty project chat and the `review-*` commands (step 6). Verify its factual claims against the code (step 4), and delete the chat
afterwards (step 10). A product decision is asked in this chat first. Only the decisions in
_Ask ChatGPT before you ask the user_ go to the user, as `.claude/rules/product-decisions.md`
requires.

## Report back

Finish with: the PR number and URL, the merge commit, the ChatGPT verdict and the findings
fixed, every ChatGPT consult (question, answer, and whether you acted on it), the issue
action taken, the chats deleted, and the untracked files noted. State anything skipped.

If the run did not reach a merged PR, the report must name the single blocker: the failed
precondition, the failed consult, or the failing check, with its exact error. Run it as a
backlog loop only when the user asked for one; then continue to the next unblocked issue
instead of stopping after the first.
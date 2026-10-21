---
name: orca-flow
description: End-to-end issue delivery for this repo. Checks out main and pulls, implements the next (or a named) GitHub issue, runs the local gate, opens a PR, requests a review from ChatGPT through the Orca browser tab using its GitHub connector, fixes the findings, merges once ChatGPT and CI are satisfied, then updates or closes the issue and deletes the review chats it created. Use when the user asks to run the orca flow, deliver an issue end to end, or take an issue through review to merge.
---

# orca-flow

Deliver one GitHub issue from `main` to merged PR, with a ChatGPT review in between.
The review runs in the user's own ChatGPT account, through the Orca browser tab. It
never pastes the diff: ChatGPT reads the PR through its GitHub connector.

## Preconditions

- Orca is running (`orca status` shows `runtimeReachable: true`).
- `orca snapshot` on ChatGPT shows a logged-in session (checked again in step 6).
- The user is logged in to chatgpt.com in Orca, and the GitHub connector is enabled there.
- `gh` is authenticated for `region-viken-vest-hockey/hockey`.

If any precondition fails, stop and tell the user which one. Do not work around it.

## 1. Start from main

```bash
git status --short            # no tracked changes allowed; if there are any, stop and report
git checkout main
git pull --ff-only
```

Untracked files that this flow does not touch (for example `.claude/skills/`) do not
stop the flow. Name them in the final report. Stage files by explicit path, never `git add -A`.

## 2. Pick the issue

- If the user named an issue, use it.
- Otherwise list open issues (`gh issue list --state open`). Take the one the user
  means by "next". If that is ambiguous, consult ChatGPT (see *When stuck*) with the
  candidates and your most likely reading, then act on its answer only if it names one
  issue and the issue bodies support it.
- An issue whose comments leave a decision open ("whether X should replace Y", an unset
  bar) is a consult question, not a stop. Send the consult with the full context: the
  issue body and comments, the merged slices and their PR titles, and the open decision with
  its options. ChatGPT settles most of these from that text. Ask the user only when, after
  one consult, the decision is still a product or policy call (see step 5).
- Read the issue body and comments with `gh issue view <n> --json title,body,comments`.
  `gh issue view <n> --comments` prints nothing here, because the classic Projects GraphQL
  deprecation error replaces the output, so do not rely on it.
- **Merged-PR check (required before taking the issue):** list PRs that reference it, merged
  and open, and read each one's body and diff stat:
  `gh pr list --state merged --search "<n>" --json number,title,mergedAt,url`
  `gh pr list --state open --search "<n>" --json number,title,url`
  An issue whose slice already merged is stale. Re-read what remains before you choose it.
  A ChatGPT or connector answer does not replace this check: a connector read once missed
  a merged PR and recommended work that was already delivered.
- An issue already partly delivered by open or merged PRs is a *slice*. Name the slice
  you are taking in the PR body.

## 3. Implement

- Branch: `agent/<issue>-<short-slug>`.
- Follow `AGENTS.md` for ownership and placement. Add focused tests at the owner boundary.
- Commit with the trailer `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>`.

## 4. Local gate

Run `make check` in the foreground. It must exit 0 before you push.
Lint findings are fixed, not suppressed. A file-length failure is fixed by splitting
the file, not by raising its baseline. `make check` runs `scripts/check`, which runs ruff,
the file-length ratchet, the fast suite, the CLI contracts, the rules sync and the secret scan.

- Setup: `make check` needs a working `venv/`. Run `venv/bin/python3 --version` first. If
  `venv/` is missing, or `venv/bin/python3` does not run (for example a dangling link to the
  system Python), run `make install`. It installs from PyPI only and changes no tracked files.
  Without a working venv, tests fail on imports such as `ortools`, which look like code
  failures but are not.
- Focused iteration: `venv/bin/python3 -m pytest --no-cov -q -p no:cacheprovider <files>`.
  Never use the system `python3` for this repo's tests.
- Lint after moving code: ruff reports `F401` for imports the move left unused. Remove
  them; do not add `noqa`.
- Moving a function into a new module: pick a name that no caller already uses as a local
  variable. A module-level import with the same name as a local makes the function an
  unbound local and fails at run time.
- Run `make check` again after every change you push, including test-only changes.

## 5. Open the PR

Before pushing, confirm the trailer is in the commit message:
`git log -1 --format=%B | grep "Co-Authored-By: Claude Haiku 5.5"`. Amend if it is missing.

```bash
git push -u origin <branch>            # pre-push hook re-runs the gate; run in the foreground
gh pr create --base main --head <branch> --title "<title>" --body-file <file>
```

- If `-u` fails with "could not lock config file" in the sandbox, the `.git/config.lock`
  mount is the cause. Use `git switch --no-track -c <branch>` and push without `-u`.
  Verify with `git ls-remote --heads origin <branch>` against `git rev-parse HEAD`.
- Write the PR body to a file in the session scratchpad, not in the repo. Shell variables do
  not persist between Bash calls, so write the literal scratchpad path, not `$SCRATCHPAD`.
- `gh pr create` warns about "uncommitted change" when untracked files exist. That is
  harmless if only untracked files that the flow does not touch are present.

The PR body states the slice, the behaviour changes, the tests, what is not covered,
and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
Use `Refs #<n>` unless the PR fully delivers the issue, in which case use `Closes #<n>`.

## 6. Request the ChatGPT review (Orca)

Start with an initial check. Run `orca snapshot` on the current ChatGPT tab (or
`orca tab list` to find one) and confirm the page is chatgpt.com and logged in: the
sidebar shows the user's projects and a composer is present: `textbox "New chat in Hockey"` on the Hockey project page, or `textbox "Ask ChatGPT"` in an open chat. Creating a review or consult chat is covered in step 6 and must not start from the top-level page.
If the page shows a login or sign-up screen, stop and ask the user to log in in Orca.
Do not attempt to log in for them.

Every chat this flow creates (review, follow-up and consult) must live in the ChatGPT
project **Hockey**. A chat outside that project is wrong, even when it works, because step 10
can only find and clean up chats the flow created when they are in one known place.
Do not use the top-level `chatgpt.com/` new chat.

The Hockey project's URL is `https://chatgpt.com/g/g-p-6a6859a57a0881919c2f9a9266cec28c/project`.
Its sidebar entry is the `button "Hockey"` under `Projects`, and its composer is the
`textbox "New chat in Hockey"`. Verify the project id against the sidebar before use. If the id
has changed, find the Hockey project in the sidebar and use its link, not the stale one.

Create the chat from the project and nowhere else:

```bash
orca tab create --url https://chatgpt.com/g/g-p-6a6859a57a0881919c2f9a9266cec28c/project
orca snapshot                  # TEST READ: the page is the Hockey project, the user is logged in
```

Test read first. Before typing anything, the snapshot must show:
- the heading or `button "Hockey"` of the Hockey project,
- the page URL is the Hockey project (`https://chatgpt.com/g/g-p-6a6859a57a0881919c2f9a9266cec28c-hockey/...`, or the project page URL itself). A sidebar entry or heading named Hockey is not enough: confirm the URL from `orca tab list` before filling the composer,
- the composer `textbox "New chat in Hockey" [ref=eN]` (or `textbox "Ask ChatGPT"` in an open chat in the project), and
- no login or sign-up prompt, and no "Sign in" button in place of the composer.

**Composer drafts:** the composer may already hold text the flow did not write. If it does,
stop. Do not clear, overwrite or send it. Tell the user what the draft says and ask whether
to clear it. Only a composer the flow has checked to be empty may receive the request. Record
the new chat's page id and title for step 10.

If the snapshot shows a login page, a missing composer, or an error, stop and tell the user. Do not type the review request into a page that is not ready.

```bash
orca snapshot                  # find the composer: textbox "New chat in Hockey" [ref=eN] on the project page, or textbox "Ask ChatGPT" in an open project chat
orca fill --element <composer-ref> --value "Use your GitHub connector to review pull request #<pr> in region-viken-vest-hockey/hockey. Focus on correctness bugs, preview/apply divergence, and weak tests. Rank findings by severity, give file and line, the failure scenario and your confidence. If there are no significant findings, say so plainly."
orca snapshot                  # find button "Send" [ref=eM]
orca click --element <send-ref>
```

Before every `orca fill` and `orca click`, bring the project tab to the front and confirm its URL:
`orca tab switch --page <id> --focus`, then `orca tab list`. `fill` and `click` fail with "could not
protect the user's keyboard focus" when the page is not in front. Read the composer value before
sending and confirm it holds only the text you intend to send, because a draft can survive navigation.

Resolve each ref and act on it in the same command. If a click or fill returns `Unknown ref`,
re-snapshot once, resolve the ref again, and retry once. If it fails again, stop and report it.

Wait for the reply with `orca snapshot` until the response is complete. The completion
marker is the `StaticText "Response complete"` line, which appears only when the newest
reply has finished. `Copy message` and `Regenerate response` appear under earlier replies
too, so they do not show that a new reply is done. Do not count markers; a count check
never matched. Right after sending, `button "Stop"` in the snapshot confirms generation started.

Do not chain `sleep` loops. Use a Monitor:
`until orca snapshot 2>/dev/null | grep -q "Response complete"; do sleep 5; done; echo DONE`.
A Monitor expires after about 8 to 15 minutes without an event. If it expires before the
marker appears, re-arm it. Do not treat an expired Monitor as a result.

Read the verdict from the page text, not from the snapshot and not from memory. Long replies
show only bare `paragraph` nodes in the snapshot, so read the text with
`orca eval --page <id> --expression "(() => { const m = document.querySelector('main')?.innerText || ''; const i = m.lastIndexOf('ChatGPT said:'); return i < 0 ? '' : m.slice(i); })()"`.
The reply is complete when that text ends with `ChatGPT can make mistakes` and two reads in a row
return the same text. Fill the composer before looking for `button "Send"`: the Send button
appears only once the composer has text.

## 7. Fix findings, then re-review

- Treat every P1/P2/P3 finding as a comment to address. Verify each one against the code
  before changing it. If a finding is wrong, say why in the PR, not by silently ignoring it.
- Fix on the same branch, run `make check`, commit, push.
- Re-request in the same chat with a follow-up that names the new head commit and the
  specific comments fixed. Repeat until ChatGPT reports no blocking issues.
- If a finding needs a product or policy decision, or you cannot tell how to resolve it,
  consult ChatGPT (see *When stuck*) before deciding.

## 8. Merge

Merge only when **both** hold:

- ChatGPT's latest review says ready to merge, or has no blocking findings.
- Every CI check on the PR is `pass`. Wait with a Monitor on
  `gh pr checks <pr> --watch --fail-fast`, then read the full list with `gh pr checks <pr>`.
  If the list has no checks at all, record that in the report and rely on the local gate.
  An error from `gh` is never a pass. Do not merge on a partial list.
- ChatGPT may answer "Ready to merge once CI passes" with no blocking findings. That
  counts as ready to merge; CI still has to pass.

```bash
gh pr merge <pr> --squash --delete-branch
git checkout main && git pull --ff-only
```

Never merge with a failing check, an open blocking finding, or an unresolved "changes requested".
If a check fails and you cannot see a fix, consult ChatGPT (see *When stuck*).

## 9. Update or close the issue

- If the PR fully delivers the issue: `gh issue close <n> --comment "Delivered in #<pr>."`
- If it delivers a slice: `gh issue comment <n> --body "..."` naming what was merged and
  what remains. Leave the issue open.

## 10. Delete the review and consult chats

Delete only the ChatGPT chats this flow created (review and consult chats), and nothing else.

- Identify them by the first message you sent (the review request or the consult question),
  not by title. Switch to the tab with `orca tab switch --page <page-id>` (the id from
  `orca tab list`) and confirm that first message before deleting.
- Delete from the chat's header, not the sidebar. New tabs open with the sidebar collapsed,
  so the sidebar `Chat actions` button is not reachable. Use the header `button "More"`,
  then `menuitem "Delete"`, then `button "Delete chat"` in the `dialog "Delete chat?"`.
- Verify the chat is gone: its title no longer appears in the tab list or the snapshot.
  Then close the flow's tabs with `orca tab close --page <page-id>`. A deleted chat's tab otherwise
  stays open and looks like a live chat.
- Never delete a chat you did not create in this flow, even if it looks similar.

## When stuck: consult ChatGPT

Whenever the flow reaches a point without a known next action, consult ChatGPT before you
stop to ask the user. Stuck points include: an ambiguous issue choice, an issue that does
not say how to slice or scope the work, a finding or CI failure you cannot resolve, a
design choice the issue leaves open, a merge conflict you cannot resolve safely, and a
slice boundary you cannot name.

Not stuck, so do not consult: a failed precondition (Orca not running, not logged in to
chatgpt.com, `gh` unauthenticated). ChatGPT cannot fix those. Stop and tell the user.

Procedure:

1. Open a fresh chat in the Hockey project, as step 6 describes: `orca tab create --url
   https://chatgpt.com/g/g-p-6a6859a57a0881919c2f9a9266cec28c/project`. Run the same test read
   as step 6 before typing, including the composer-draft rule. Record the chat as a consult
   chat for step 10.
2. Send one self-contained question. ChatGPT cannot see the local repo or uncommitted
   state, so the message must carry the facts: the flow step, the candidates or the
   failure text (short), the issue or PR numbers, and the decision needed. Ask it to use
   the GitHub connector to read the named issues and to list every PR that references
   them, merged and open, including their diffs. Include the issue comments and the merged
   slice titles in the message, so ChatGPT judges from the full history and not from the
   body alone. Ask it to recommend one option with the reasons and a confidence, and to
   decide any open design or policy question it can settle from the issue text. Ask it to
   list every assumption it made about the codebase, labelled as an assumption, and to say
   which it could not verify. Do not paste diffs, secrets or tokens.
3. Wait for the complete reply (same polling rule as step 6). Read the answer from the
   page text, as step 6 describes, not from the snapshot.
4. Verify the answer before acting on it. ChatGPT is advisory. Check it against the issue
   bodies, the code, and the merged-PR check from step 2 (`gh pr list --state merged
   --search "<n>"`, then `gh pr view <pr>`). Its answer is not a merge approval and not a
   product decision. The #719 consult recommended work that PR #776 had already merged, so
   a consult answer that names an issue must pass the merged-PR check before you act on it.
5. If the answer is supported by the evidence, act on it and record in the final report
   that ChatGPT recommended it and what you checked. If the answer is unsupported,
   contradicts the code, or asks for a product or policy decision that the issue text cannot
   settle, stop and ask the user. Name ChatGPT's recommendation and say why you did not take
   it. Stop only then, not for an answer that merely leaves a choice open.
6. When you stop to ask the user after a consult, leave the consult chat and its tab open,
   record their page id, and name them in the stop message so the user can read the answer
   in Orca before deciding. Delete the chat at step 10 only once the flow resumes or ends.

Consult at most once per stuck point. If the consult itself fails (no complete answer,
login prompt, page not ready), stop and tell the user. Do not retry in a loop.

## Report back

Finish with: the PR number and URL, the merge commit, the ChatGPT verdict and the findings
fixed, every ChatGPT consult (question, answer, and whether you acted on it), the issue
action taken, the chats deleted, and the untracked files noted. State anything skipped.

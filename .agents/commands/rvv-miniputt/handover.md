# Verified session handover (all agent harnesses)

This is a **startup procedure**, not a saved session, task backlog or independent source of
truth. Follow it at the start of a new ChatGPT, Claude, Codex or other harness session
working on this repository, and whenever an old conversation/context is no longer reliable.
GitHub Issues remain the only live implementation backlog.

## One-line operator invocation

"Continue hockey from the repo handover; verify live state first."

Local checkout: from the repository root run `make handover`, optionally
`make handover ARGS='--issue N --json'`. This command is read-only. Season
defaults to the single canonical `season/` directory. The repository is inferred
from the origin remote; if the origin cannot be verified, `--repo` is required
rather than guessed at the upstream default. Pass `--season`/`--repo` to
override. It uses the canonical
`season lifecycle --json` report for the season and GitHub read-only API
queries through `gh api`, and reads publication evidence through the canonical
export-lifecycle owner. If GitHub CLI is unavailable or not authenticated, the
report says REVIEW_REQUIRED; do not fill in gaps from memory.

## Required startup verification

1. Read `AGENTS.md`, this procedure and only the task-relevant shared RVV
   instructions. Current code/tests/controlled inputs outrank prose.
2. Inspect local branch/HEAD/worktree, live remote `main` SHA and CI. If
   working in a feature branch, compare its base/current changes to main; the
   handover deliberately does not treat a divergent branch as current main.
3. Select the **active issue/PR from the user's current request** or the live
   GitHub Issues list. Do not claim there is a unique active task merely because
   an earlier chat worked on it. Read current issue body, comments and linked
   PR/CI; treat handover comments as leads subject to verification.
4. Distinguish four different states: (a) actual public `gh-pages/latest`,
   (b) its historical exported manifest/baseline, (c) current canonical
   `season/<season>/`, (d) unpublished export candidates and calendar evidence.
   Never treat the most recent generated export or Pages branch HEAD alone as
   the current season identity. Activity-page publishing can advance gh-pages
   without changing the season plan.
5. For season operations, require live `season lifecycle --json`,
   `published_sealed`, and `reconciliation.ok == true`; investigate any
   missing/unexplained delta. Historic published canonical revision may
   legitimately differ from current working canonical revision due to recorded
   narrow mutations. That difference alone is NOT an error.
6. Read the relevant current code and tests before coding. Re-run current
   checks after edits; old issue checkboxes and old CI results are not proof.
   Calendar overlap is occupancy evidence, never automatic booking confirmation.
7. Publishing is a separate human authority boundary. A `CONTEXT_VERIFIED`
   handover NEVER says that the specific export/audit is ready to publish. It
   does not grant authority to replan, reopen, export, mutate or publish.

The machine-readable command has a schema version, separate historical and
working revisions, source references, missing evidence and risks. A failure
to obtain GitHub or lifecycle data MUST yield `REVIEW_REQUIRED`, not a green
summary. It does not fetch/scrape calendars or mutate anything.

## GitHub-only ChatGPT session (no local checkout)

Use the connected GitHub app to fetch, in this order:

- HEAD SHA and current `AGENTS.md` + this procedure from `main`;
- linked task issue/PR and latest comments/diff; current open dependencies;
- current CI on `main`, and `gh-pages` branch SHA;
- `export/<publication-id>/export_manifest.json` for authoritative
  historical publication, plus the `season-revision` metadata in
  `gh-pages/latest/index.html`;
- inspect `season/<season>/` blob identity and relevant code or tests;
  if large canonical files cannot be processed, **do not** claim the live
  lifecycle/reconciliation has passed. Ask a checkout-capable agent to run
  `make handover ARGS='--issue N'` or the canonical lifecycle command.

Do not claim a local CLI or integration test was run from GitHub-only metadata.
Never copy a previous response's commit SHA, revision, number of findings or
published identity forward without checking current refs. Cite URLs/commit SHA
and identify `not verified` facts plainly.

## Issue-comment checkpoint convention

Only for an ongoing substantive issue, append one concise **issue comment**
when work is handed to another session: verified HEAD/branch, canonical revision
if actually checked, change/PR link, test evidence, blockers, and the next
specific verification/action. Use the issue's existing comments, not a second
`status.md`, generated task file, notes directory or transcript dump. On
resume, compare saved SHA/revision against live state before trusting any claim.
If evidence changed, re-evaluate instead of replaying old commands.

Example (values are placeholders, never copied as evidence):

```text
Handover checkpoint — HEAD <sha>; PR <link>; canonical revision <verified/unknown>
Done: <specific change and evidence>.
Tests: <exact run/link or not run>.
Blockers: <specific live dependency>.
Next: <one bounded verification/action>; re-check current refs first.
No publication or season mutation authorized by this comment.
```

For ChatGPT Project instructions, the durable pointer is simply:
"On hockey requests read repo AGENTS.md and its session-handover procedure,
then verify live GitHub and canonical/publication evidence. Previous chats
are historical context only." Do not paste a mutable schedule summary into
Project instructions.

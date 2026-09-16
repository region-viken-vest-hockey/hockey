---
name: rvv-move-tournament
description: Safely move one canonical RVV tournament to a requested date/arena/host/time, respecting approval locks and preserving the prior confirmed state if the move fails.
---

# RVV move tournament

Use this skill when the operator asks to move one already promoted canonical tournament to a specific new placement.

This is a scenario/orchestration skill only. Read `AGENTS.md`, `.agents/skills/rvv/SKILL.md`, and `.agents/commands/rvv-miniputt/season.md` first. Repository code owns hard verification, lock enforcement, mutation, revisioning and persistence.

## 1. Resolve the exact tournament and requested placement

This skill operates only on `season/<season>/` canonical state.

Prefer a durable tournament id. If no id is supplied, inspect `season/<season>/schedule.json` read-only and match the operator's facts. Proceed only when exactly one tournament matches; never guess between multiple same-age/same-club tournaments.

Require at least one concrete requested placement change:

- `--date YYYY-MM-DD`;
- `--arena "..."`;
- `--host-club "..."`;
- `--start-time HH:MM` (or the repository-supported placement value).

If the operator only says "move it somewhere else" without a concrete target, do not invent a slot. Use the canonical replanning workflow instead.

Before mutation, snapshot the target tournament's current placement and the current approval/lock record. Also record the canonical revision so the result can be verified.

## 2. Inspect approval/lock state

Run:

```bash
scripts/rvv-miniputt season approvals --season <season> --json
```

If the tournament is currently approved/placement-locked and the operator has explicitly asked to move it, that request authorizes revoking the old placement confirmation. Revoke it first:

```bash
scripts/rvv-miniputt season unapprove \
  --season <season> \
  --tournament-id <durable-id> \
  --note "Move requested by operator; old placement no longer confirmed"
```

Preserve the previous lock scopes in memory so they can be restored if the move itself fails.

Do not unapprove unrelated tournaments.

## 3. Apply only the requested placement mutation

Build the command with only fields the operator actually requested:

```bash
scripts/rvv-miniputt season move \
  --season <season> \
  --tournament-id <durable-id> \
  [--date YYYY-MM-DD] \
  [--arena "..."] \
  [--host-club "..."] \
  [--start-time HH:MM] \
  --work-dir <work-dir>
```

Do not change participants or unrelated tournaments to make the move fit. If the requested placement violates a hard rule, let repository verification reject it and report the concrete violation.

The move must preserve the tournament's durable id.

## 4. Restore the old approval if the move fails

Revoking approval and moving are separate canonical commands. Make the scenario operationally safe:

- if the move fails;
- and the canonical schedule/placement is still unchanged from the pre-move snapshot;
- and the tournament had a current valid approval before this skill started;

restore the previous approval/lock scopes immediately with `season approve`.

Use the original placement-lock setting and add `--participants-lock` only if it was previously set. Record a note such as `Restored after failed move attempt`.

If the schedule changed despite a reported failure, do **not** guess or auto-restore. Stop and inspect canonical state.

## 5. Verify a successful move

After success, inspect canonical state and approval state:

```bash
scripts/rvv-miniputt season status --season <season>
scripts/rvv-miniputt season approvals --season <season> --json
```

Compare the target tournament before/after and require:

- durable id unchanged;
- every requested placement field equals the requested value;
- fields not requested by the operator remain unchanged unless the repository necessarily normalizes representation;
- unrelated tournaments are unchanged;
- canonical revision changed;
- a previously approved tournament is now unapproved/pending review unless the operator explicitly confirmed the **new** placement too.

Do not silently reapprove the new placement. A move means the old booking confirmation is no longer valid. If the operator also says the new slot is confirmed/booked, delegate the final approval step to `.agents/skills/rvv-confirm-tournament/SKILL.md` after the move verifies successfully.

## 6. Persist the canonical change in Git

Inspect the worktree:

```bash
git status --short
```

A normal successful move may change both:

```text
season/<season>/schedule.json
season/<season>/decisions.json
```

Stage only those canonical files. Do not stage `.pipeline/`, generated exports, scratch files or unrelated changes.

Use a concise commit such as:

```text
Move tournament <tournament-id>
```

Push when the active repository/harness policy authorizes updating the shared branch. Moving a tournament does **not** authorize public GitHub Pages publication.

## Review/export handling

Do not overwrite the existing club-review export. Canonical state is authoritative after the move.

If the operator wants the review artifact refreshed, create a **new timestamped** export:

```bash
scripts/rvv-miniputt season export --season <season>
```

Do not use `--flat` to overwrite the older review bundle unless explicitly requested.

## Completion report

Report:

1. tournament id;
2. old placement -> new placement;
3. whether an old approval was revoked;
4. current approval/lock state after the move;
5. new canonical revision;
6. Git commit/push result when performed;
7. whether a fresh review export was generated.

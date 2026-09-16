---
name: rvv-confirm-tournament
description: Confirm/approve one canonical RVV tournament and lock its booked placement safely, preserving participant editability unless the operator explicitly locks the roster too.
---

# RVV confirm tournament

Use this skill when the operator says a canonical tournament, host slot or ice booking is confirmed/approved/booked and should no longer be moved by later planning.

This is a scenario/orchestration skill only. Read `AGENTS.md`, `.agents/skills/rvv/SKILL.md`, and `.agents/commands/rvv-miniputt/season.md` first. Repository code owns verification, approval fingerprints, lock semantics and persistence.

## 1. Resolve the exact canonical tournament

This skill operates only on an already promoted `season/<season>/` state. Do not promote a season here; use `rvv-review-baseline` for that lifecycle transition.

Prefer a durable tournament id supplied by the operator. If no id is supplied, inspect `season/<season>/schedule.json` read-only and match the facts the operator gave (for example age group, date, host, arena, club/team). Proceed only when exactly one canonical tournament matches. If several match, present the candidate ids/current placements instead of guessing.

Before mutation, record the tournament's current:

- durable id;
- age group and date;
- arena and physical host;
- current participants;
- canonical revision.

## 2. Inspect existing approval state

Run:

```bash
scripts/rvv-miniputt season approvals --season <season> --json
```

If the tournament already has a current, non-stale approval with the requested lock scopes, treat the operation as idempotent and report that it is already confirmed.

A `stale_approval` is not a valid confirmation. The current canonical placement must be explicitly approved again.

## 3. Apply the intended lock semantics

For normal "confirmed", "approved" or "ice booked" language, lock **placement only**:

```bash
scripts/rvv-miniputt season approve \
  --season <season> \
  --tournament-id <durable-id> \
  --note "<concise confirmation note>" \
  --work-dir <work-dir>
```

Placement locking is the default. Do **not** add `--no-placement-lock` for a confirmed booking.

Participants remain editable by default. Add:

```text
--participants-lock
```

only when the operator explicitly says the participating teams/roster are also final or locked. Do not infer a participant lock merely from the ice slot being confirmed.

Add `--actor <name>` when a meaningful operator identity is available.

Approval is not a waiver. The repository command re-verifies the current canonical tournament against the available canonical verification problem and must fail rather than approve a hard-invalid placement.

## 4. Verify the confirmation

Re-run:

```bash
scripts/rvv-miniputt season approvals --season <season> --json
scripts/rvv-miniputt season status --season <season>
```

Require the target record to be current/non-stale with:

- `status=approved`;
- `placement_locked=true`;
- `participants_locked` matching the operator's explicit intent;
- an approval fingerprint for the current protected fields.

The schedule itself should not have moved or changed as a side effect of approval. If schedule facts changed unexpectedly, stop and investigate rather than committing.

## 5. Persist the canonical decision in Git

`decisions.json` is canonical Git-backed operational state. Inspect the worktree:

```bash
git status --short
```

Stage only the intended canonical decision file for this operation:

```text
season/<season>/decisions.json
```

Do not stage `.pipeline/`, generated exports, scratch files, or unrelated changes.

Use a concise commit such as:

```text
Confirm <tournament-id> placement
```

Push when the active repository/harness policy authorizes updating the shared branch. Confirming a tournament does **not** authorize public GitHub Pages publication.

## Review/export handling

Do not overwrite or publish an existing review export merely because approval state changed. Canonical state is the source of truth.

If the operator also wants review/publication artifacts to show the new approval state, create a fresh timestamped canonical export afterwards:

```bash
scripts/rvv-miniputt season export --season <season>
```

Never use `--flat` to overwrite the existing review bundle unless the operator explicitly requests that exact behavior.

## Completion report

Report:

1. tournament id and current placement;
2. confirmation status;
3. placement-lock and participant-lock state;
4. canonical season/revision;
5. Git commit/push result when performed;
6. whether review artifacts were regenerated or still represent the prior decision state.

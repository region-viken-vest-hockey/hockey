# RVV Miniputt: season — Removing and swapping participants

Load from `../season.md` when routed here. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` first; the lifecycle boundary and inspection path in `../season.md` still apply.

## Remove a participant with no replacement

Use this when a registered team drops out and there is no same-age replacement team to substitute in. The command removes exactly one participant from one or more same-age tournaments, keeps every date/time/arena/host and booked occupancy interval, regenerates each affected tournament's games through the configured-rounds generator, records a change protection keyed to the request, and runs the full-season hard-verification/hosting-responsibility/consequence gates.

```bash
scripts/rvv-miniputt season remove-participant \
  --season <season> \
  --tournament-id <id> --tournament-id <id> \
  --remove-team "<team>" \
  --reconcile-withdrawal \
  --request-id <request-id> \
  --dry-run --json
```

Distinguish the two intents at the eligibility boundary -- do not choose the weaker one just because it makes the candidate pass:

- **one-tournament participant absence** (omit `--reconcile-withdrawal`) changes only the named tournaments and keeps the full registered eligible pool. If the reduced shape is avoidably underfilled the command fails closed and no canonical state is written; make an explicit withdrawal decision or choose a different legal action.
- **genuine season/age-group withdrawal** (`--reconcile-withdrawal`) additionally records a durable, revision-bound eligible-pool decision that makes the team ineligible for the **whole age group** from the earliest affected tournament (`effective_from`); the named tournament ids are the roster-mutation scope and provenance, not the eligibility scope. The verifier and game generator therefore see the correct active pool for every current and future tournament in the age group. The registered `Lag` roster and every earlier historical/completed tournament are never rewritten; the record is additive canonical provenance and is part of the canonical-state revision.

The dry-run report returns `removal.can_apply_unchanged`, the `remove_team` consequence, the per-remaining-team `team_consequences`, the `withdrawals_to_add` records, guest-reservation integrity, hosting-responsibility and request-constraint verdicts, and the regenerated game counts. `--dry-run` never writes. A bare label must identify exactly one participant in each named tournament; a guest participant is refused (use the guest-slot lifecycle). Participant-locked tournaments must be unapproved explicitly first.

When the same team withdraws from several tournaments in one request, use one atomic batch so nothing is written unless every tournament is updated together:

```bash
cat > /tmp/batch.json <<'JSON'
[
  {"op": "remove_participant", "tournament_id": "rvv-0158", "remove_team": "<team>", "reconcile_withdrawal": true},
  {"op": "remove_participant", "tournament_id": "rvv-0162", "remove_team": "<team>", "reconcile_withdrawal": true}
]
JSON
scripts/rvv-miniputt season batch \
  --season <season> \
  --operations /tmp/batch.json \
  --scope rvv-0158 --scope rvv-0162 \
  --request-id <request-id> \
  --dry-run --json
```

### Superseding a withdrawal

A genuine season/age-group withdrawal is **durable**: it reduces the eligible shape pool for the whole age group, so a later maintenance, rebuild or newly materialized tournament cannot silently reintroduce the team. The record is revision-bound and scoped with an `effective_from` date (the earliest affected tournament), so earlier historical/completed tournaments keep the team as provenance. An active withdrawal also makes the team **ineligible** for the age group: a roster that regains the team fails verification with `withdrawn_team_participating` instead of quietly restoring eligibility. Registration reconciliation (removing the team from the authoritative pool) ends the effect automatically; otherwise release the record explicitly:

```bash
scripts/rvv-miniputt season withdrawals --season <season> --json
scripts/rvv-miniputt season release-withdrawal \
  --season <season> \
  --request-id <withdraw-request-id> \
  --restore-participant \
  --note "team returns to the age group"
```

`--withdrawal-id <id>` (repeatable) selects individual records; `--request-id` selects every active record created by one withdrawal request. Release is **verified, not a silent decision-only write**: the current schedule (or the explicitly restored one) is re-verified against the post-release eligible pool before anything is written, so a premature release that would leave underfilled fields in a now-larger pool is refused with no canonical write. `--restore-participant` is the authorized reversal and runs through the same complete canonical mutation boundary as apply/batch (typed and replayable on a sealed season, hard verification, request constraints, locks, change protections, guest integrity, operational acceptability, hosting responsibility and published-baseline replay): it adds the withdrawn team(s) back to the recorded tournaments, regenerates their games, releases the removal's `must_not_participate` guards and the withdrawal record in one atomic commit. Provenance is never erased -- only the record's `status` changes.

## Swap tournament participants safely

When the operator wants one team moved out of a tournament and another team exchanged into it, use the first-class canonical swap capability rather than hand-editing rosters:

```bash
scripts/rvv-miniputt season swap-participants \
  --season <season> \
  --tournament-a <id> --team-a "<team>" \
  --tournament-b <id> --team-b "<team>" \
  --request-id <request-id> \
  --dry-run --json
```

Use `--dry-run` to compare plausible exchange partners. A preview may be returned even when the candidate is poor; inspect `change_protection_acceptable`, `existing_change_protection_violations`, `consequence_acceptable`, and the per-team `team_consequences`. Apply only a candidate that preserves earlier accepted requests and does not materially worsen either affected team's schedule.

Once selected, repeat the same command without `--dry-run`. The repository regenerates both tournaments' games, runs full hard verification, preserves hosting responsibility/guest reservations/locks, and writes durable protections for both resulting assignments.

The per-team consequence gate is a default refusal, not a soft goal. When the operator explicitly accepts one named trade-off (for example "Sandefjord may play 11 and 17 October"), `swap-participants` and `batch` accept a narrow opt-in:

```bash
  --accept-team-regression "<team label>=<regression code>" \
  --accept-regression-reason "<operator's reason>"
```

A bare label must identify exactly one affected team; when a label is shared by several affected teams (for example the same label in two age groups within one batch) qualify it as `"<club>|<team label>|<age group>=<code>"`, otherwise the command is refused as ambiguous. It covers only that exact material regression code for that exact affected team (`more_gaps_under_7_days`, `more_gaps_under_14_days`, `temporal_coverage_materially_worse`, `more_concentrated_club_exposure`, `travel_materially_worse`). Every other material regression still refuses. An acceptance that matches no regression in the candidate refuses the command, and the reason is mandatory. The dry-run reports `regression_acceptance` (accepted, unaccepted, unmatched), and a committed change records the accepted regressions and reason in decision history. Codes are never broadened or merged: a new gap under 7 days often also adds a gap under 14 days, and each code the dry-run lists under `unaccepted_regressions` must be accepted separately. Use it only when the operator has accepted that specific team/regression. Never infer it from a candidate merely being the best available, and never pass it pre-emptively.

A genuine team/season retirement sometimes has no replacement host: the operator cancels the retired team's tournament while withdrawing the team from the rest, and the remaining participants each lose exactly that one appearance. Cancelling a tournament removes one appearance from every participant, so the batch derives the same before/after per-team consequences for those participants and a bare `cancel` (including a cancel-only batch) is refused until the resulting material regressions are accepted explicitly. Because the loss is an explicitly directed consequence rather than a quality trade-off, a `batch` that contains a `cancel` operation also accepts `participation_count_changed` for teams that actually played in a cancelled tournament in that batch; the acceptance covers only the exact participation loss the cancellation explains, so a team that lost an additional appearance for any other reason cannot have its combined loss waived, and a team the batch also withdrew keeps its deliberate withdrawal shortfall exemption. The code is never available to `move`/`swap`/`remove` on their own, or to teams outside the cancelled tournament's roster; supplying it anywhere else is refused. The same mandatory `--accept-regression-reason` and per-team qualification apply, and the accepted shortfall is recorded in decision history. Keep this separate from the host-reassignment alternative: choosing another club to host the tournament remains a distinct, non-default path.

If all otherwise-good candidates are blocked by a prior accepted request, do not release that request automatically. Follow the supersession rules in **Process incoming club/operator change requests**.


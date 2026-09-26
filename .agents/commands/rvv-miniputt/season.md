# RVV Miniputt: season

Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` before changing canonical season state.

Use this procedure once a verified schedule is becoming or has become the durable operational baseline for club review/ice booking. Repository code owns validation, locks, change cost, verification, persistence and export. The harness chooses the operator-intended action and reports the result; it must not hand-edit `season/<season>/schedule.json`, `decisions.json`, checkpoints or generated exports.

## Lifecycle boundary

Before promotion, create/review the season through the canonical interactive Stage 1–4 flow in `run.md`.

When the operator deliberately accepts that verified schedule as the operational baseline, promote it once:

```bash
scripts/rvv-miniputt season promote --work-dir .pipeline --season <season>
```

Promotion is bound to the reviewed Stage 4 handoff. It verifies the exact reviewed candidate against the verification context that accepted that Stage 4 export, then records the source run/fingerprint and context provenance in `promoted_from`. It refuses with an explicit stale/missing-provenance error if the Stage 4 export is incomplete/stale, the Stage 3 candidate no longer matches the reviewed export, the source run has changed, or the verification context is missing. It never silently rebuilds the problem from newer Stage 1/2 state or falls back to context-free verification.

Promotion is deliberate and exclusive. Do not use `--force` merely because a canonical season already exists; a normal later Stage 3 run is baseline-aware and must optimize around that canonical state instead of replacing it. `--force` only replaces existing canonical state; it is not a verification bypass.

After promotion, prefer the canonical season commands for normal club feedback and stabilization work.

## Published-season lifecycle

Publication is what makes the season operational. The **first successful publication seals the season automatically**; a season published before this feature existed is backfilled explicitly:

```bash
scripts/rvv-miniputt season lifecycle --season <season> --json
scripts/rvv-miniputt season seal-published --season <season>
```

`season seal-published` locates the real published baseline through authoritative publication history (never a supplied export directory), records the immutable baseline, derives publication omissions from the canonical plan at the publication revision, and requires explicit provenance for any post-publication materialization it must accept (`--attest-materialization <id>=<provenance>`). It fails closed if `published baseline + attested additions + recorded canonical mutations` does not exactly equal the current canonical projection. Do not work around a failure by snapshotting current state.

Once `published_sealed`, the season is maintenance-only. `season replan`, planner-generated `season apply`, mutating `season normalize-placements`, `season promote --force`, and a full/new pipeline run for the same window are refused by the service layer. Continue through the targeted canonical operations below. If a deliberate full restructuring is ever required, use the operator-only emergency escape hatch and record why:

```bash
scripts/rvv-miniputt season reopen-planning --season <season> \
  --reason "<operator reason>" --confirm-break-published-baseline
```

Never infer or auto-select reopening because a repair/replan path is convenient.

## Inspect state

```bash
scripts/rvv-miniputt season status --season <season>
scripts/rvv-miniputt season approvals --season <season>
```

Treat `season/<season>/schedule.json` and `decisions.json` as the current operational truth. `.pipeline` remains transient run/search/evidence state.


## Process incoming club/operator change requests

Treat each accepted feedback item as durable intent that later maintenance must preserve.

Before a manual placement or participant change:

```bash
scripts/rvv-miniputt season status --season <season>
scripts/rvv-miniputt season approvals --season <season> --json
scripts/rvv-miniputt season protections --season <season> --json
scripts/rvv-miniputt season constraints --season <season> --json
```

Give the incoming request a stable `request_id`. Prefer an existing external/message/request id when one exists. Otherwise synthesize a deterministic local id from the source and intent (for example `club-feedback:<club>:<date>:<short-purpose>`) and check `season protections` so it does not collide. The operator should not need to invent this internal id.

Classify the requested outcome before choosing a command:

- **global date unusable for every tournament** (ice hall closed, a holiday/weekend nobody can host or play) -> record a canonical **banned date** (`season ban-date`), not a per-team request constraint;
- **semantic constraint, not an exact placement** (a team unavailable on a date/range, a minimum gap between a team's tournaments, an opponent to avoid within a date range) -> record a typed request constraint first (see **Record semantic request constraints** below), then search for any legal result satisfying all active constraints;
- **specific date/arena/host/time change** -> use the targeted `season move --request-id <id>` flow below;
- **pure team-name/identity-label correction across the canonical season** (same club, same age group, same underlying team) -> evaluate `season rename-team --dry-run --request-id <id>` and apply it atomically; do not use registration-set reconciliation, `replace-participant`, or a replan;
- **specific one-tournament participant substitution** ("replace team A with team B here") -> evaluate `season replace-participant --dry-run --request-id <id>`; an existing finding is not required;
- **specific participant/roster exchange between two tournaments** -> evaluate `season swap-participants --dry-run --request-id <id>`; an existing finding is not required;
- **one participant drops out with no same-age replacement** -> evaluate `season remove-participant --dry-run --request-id <id>` for one affected tournament, or an atomic `batch` of `remove_participant` operations when several tournaments lose the same team; pass `--reconcile-withdrawal` only for a genuine season/age-group withdrawal, never merely to make an underfilled candidate legal;
- **general request to improve participation/placement** -> use current findings/repair-options/search first, then the smallest verified change;
- **broader rebalance** -> only then escalate to baseline-aware `season replan` / `diff` / verified `apply`; on a `published_sealed` season these are refused, so complete the outcome through targeted canonical operations or a deliberate, operator-authorised `season reopen-planning`.

For a participant replacement or swap, preview alternatives before applying. The repository reports before/after consequences for **all** affected teams (spacing, temporal coverage, opponent repetition/diversity and travel), and applying a materially regressive change is refused. Do not optimize the requesting club by treating the displaced team as free capacity.

Accepted swaps and moves create granular protections in canonical `decisions.json`. A later candidate that would undo one is a conflict with prior accepted intent, not permission to discard it. When a protection blocks a candidate:

1. prefer another legal candidate that preserves all accepted requests;
2. if the new request **explicitly supersedes or reverses** the earlier request, release only the relevant earlier protection(s), recording the newer request in the note;
3. if the relationship is ambiguous and no safe alternative exists, ask for the real-world clarification rather than silently releasing the earlier request.

Keep evaluating the operator's *original outcome* across intermediate mutations. A finding disappearing after a temporary/partial change does not prove the real problem is resolved (a clustered team moved onto unusable ice can clear a `temporal_clustering` finding while failing "produce a usable, better-spaced schedule"). Prefer `current canonical state -> repository search -> final coupled candidate -> one atomic apply` over chains of temporary canonical moves used only to unlock a later search. If a temporary mutation is genuinely necessary, treat it as temporary and release only the protections that operation created -- never unrelated accepted protections.

Explicit supersession:

```bash
scripts/rvv-miniputt season release-protection \
  --season <season> \
  --request-id <old-request-id> \
  --note "superseded by <new-request-id>"
```

Then apply the newer change with `--request-id <new-request-id>`. Never release a protection merely to make an optimizer or convenient swap candidate fit. Generic `season apply` is also protection-aware, so a broader replan cannot quietly reverse accepted feedback.

After any accepted mutation, re-run `season protections --json` and `season constraints --json`, and report which prior requests remain protected and which new protections/constraints were added.

## Record semantic request constraints

### Date scope first: global ban vs. team constraint

Choose the narrowest canonical representation that matches the real-world fact:

```text
Global date unavailable for all tournaments
    -> canonical banned date (`season ban-date`)

Specific club/team cannot participate
    -> typed request constraint (`season add-constraint`)
```

A **banned date** is a global planning restriction: no tournament may be scheduled on it, regardless of host or participants. It reuses the existing `banned_dates` rule the planner, verifier, candidate-weekend enumeration, repair/search, optimizer and the `season batch` boundary all read -- there is no separate date-policy engine. Record it through:

```bash
scripts/rvv-miniputt season ban-date \
  --season <season> \
  --date 2027-02-27 \
  --request-id operator:vinterferie-2027 \
  --note "winter break - no ice"
```

`season ban-date` is a **policy/decision-only write**. It is deliberately allowed even when tournaments are already scheduled on that date, exactly like `season add-constraint`: the ban is persisted, the command reports the currently affected tournament ids, and the canonical-state revision advances so every stale maintenance/search/batch option is invalidated. Policy mutation and schedule repair stay separate operations.

```text
New banned date already contains tournaments
    -> record the ban first (`season ban-date`)
    -> derive the affected tournament ids (`season banned-dates --json`)
    -> repair them with one scoped atomic batch (`season batch`)
```

Inspect and remove bans with:

```bash
scripts/rvv-miniputt season banned-dates --season <season> --json
scripts/rvv-miniputt season unban-date --season <season> --date 2027-02-27 --note "winter break over"
```

`season banned-dates --json` returns each active date, its source/request id, whether it is currently satisfied, and the exact `affected_tournament_ids`, so the repair scope comes from one authoritative read path. Do not duplicate the ban as a per-team request constraint, do not store it only in plan/decisions free text, and do not hand-edit JSON. A banned date blocks `season move`, `season batch` moves, generated repair/search options, candidate-weekend recommendations, optimizer/replan moves and final canonical verification until it is repaired or explicitly unbanned.

### Record semantic request constraints

Club feedback frequently states intent rather than an exact replacement schedule. Do not collapse it into an exact placement lock. Translate it into the narrowest supported canonical typed constraint and record it **before** searching or mutating:

```bash
# team cannot play a date or inclusive range
scripts/rvv-miniputt season add-constraint --season <season> \
  --type team_unavailable \
  --team-club <club> --team-label <label> --team-age-group <age> \
  --date-from <YYYY-MM-DD> [--date-to <YYYY-MM-DD>] \
  --request-id <request-id> --note "<club wording>"

# at least N days between a team's tournaments
scripts/rvv-miniputt season add-constraint --season <season> \
  --type minimum_gap \
  --team-club <club> --team-label <label> --team-age-group <age> \
  --min-days <N> \
  --request-id <request-id>

# two teams must not meet within a date range
scripts/rvv-miniputt season add-constraint --season <season> \
  --type opponent_avoidance \
  --team-club <club> --team-label <label> --team-age-group <age> \
  --team2-club <club2> --team2-label <label2> --team2-age-group <age2> \
  --date-from <YYYY-MM-DD> [--date-to <YYYY-MM-DD>] \
  --request-id <request-id>
```

Scopes use stable team identity (club + label + age group). Constraint ids are deterministic from the semantic payload plus `--request-id`, so retrying the same request is idempotent. Malformed/ambiguous definitions (unknown or ambiguous team identity, inverted date range, non-positive gap, a team avoiding itself, unsupported type) are rejected at creation time.

Persist a typed constraint only from a *concrete durable semantic requirement*: "Kongsberg U9 must have at least 7 days between tournaments" -> `minimum_gap`; "Kongsberg cannot play 21 February" -> `team_unavailable`. Do **not** invent a numeric threshold from vague wording such as "three tournaments in eight days is a problem": preserve that as the operator's intent and address it through findings/search unless the operator states the actual policy threshold.

`season add-constraint` is a decision-only write and is **allowed even when the current schedule violates the new constraint**. It persists the validated constraint, reports its current structured violation(s), and advances the canonical-state revision (invalidating previously generated repair/search options). Confirm the recorded status with:

```bash
scripts/rvv-miniputt season constraints --season <season> --json
```

Each active constraint reports `satisfied` plus any `violations`. A single isolated violation is repaired through the ordinary capabilities (`season move`, `season swap-participants`, `repair-options`/`search`/`apply-repair`, `season replan`/`apply`). The repository enforces the full active constraint set at the canonical apply boundary; previews/`repair-options` also report constraint violations instead of hiding them. Search for a result satisfying **all** active constraints -- never auto-release a constraint because it blocks an easy candidate. Schedule-changing commits are additionally checked for operational acceptability (see **Repair a localized finding**): a candidate must not newly place a tournament on `fixed_busy`/untrusted/external-conflict ice or introduce manual/host-confirmation work merely because it satisfies the typed constraints.

### Repair several simultaneous violations atomically

When one operator request records **several independent constraints** that invalidate several tournaments at once, no individual schedule-changing commit can make progress: every `move`/`swap`/`apply` still has to satisfy the whole active set, so the first repair is refused because the other pre-existing violations remain. Do **not** release valid constraints just to allow an intermediate mutation, do **not** hand-edit canonical JSON, and do **not** use approvals or a broad `season replan` to fence the work.

Use the atomic scoped batch boundary instead. It applies several operations to one in-memory copy of the current canonical plan, runs the complete authoritative gates once on the final candidate, and commits exactly once. Nothing is written unless the entire batch is valid:

```bash
cat > /tmp/batch.json <<'JSON'
[
  {"op": "move", "tournament_id": "rvv-0147", "date": "2027-02-27"},
  {"op": "swap_participants",
   "tournament_a": "rvv-0158", "team_a": "K9",
   "tournament_b": "rvv-0162", "team_b": "J9"}
]
JSON
scripts/rvv-miniputt season batch \
  --season <season> \
  --operations /tmp/batch.json \
  --scope rvv-0147 --scope rvv-0158 --scope rvv-0162 \
  --request-id <request-id> \
  --dry-run --json
```

`--scope` declares the affected tournament ids; every operation must reference only in-scope ids, and any tournament that changes outside the scope refuses the whole batch. Supported operations are `move` (any placement fields, `allow_cross_half` as needed), `swap_participants` (same-age roster exchange using the ordinary safe roster semantics), `cancel` (mark a tournament cancelled) and `remove_participant` (drop one participant from a tournament with no replacement; set `reconcile_withdrawal: true` per operation for a genuine season/age-group withdrawal, exactly like the single `season remove-participant` command). The dry-run report returns the declared scope, the ids actually changed, any changed ids outside scope (must be empty), the requested operations, the remaining request-constraint violations, the hard-verification and operational-acceptability verdicts, protection/approval/lock conflicts, guest-reservation integrity, the hosting-responsibility verdict, before/after canonical fingerprints/revisions, and per-team consequences where participants change. Repeat the same command without `--dry-run` to commit once. A batch that fixes only some of the pre-existing violations is refused without writing anything.

Use this path only when several independent active constraints genuinely require a combined repair. One isolated violation still goes through the ordinary `season move` / `season swap-participants` commands, whose full-season constraint gate is unchanged.

Release only when a newer request explicitly supersedes/revokes it:

```bash
scripts/rvv-miniputt season release-constraint --season <season> \
  --constraint-id <id> \
  --note "superseded by <new-request-id>"
# or release every active constraint created by an earlier request:
scripts/rvv-miniputt season release-constraint --season <season> \
  --request-id <old-request-id> \
  --note "superseded by <new-request-id>"
```

If the semantic request cannot be represented by a supported type, surface that capability gap instead of silently reducing it to an exact placement lock. A request constraint stays authoritative after a successful repair: the granular change protections may still guard the exact accepted result, but they never replace the higher-level request.

## Approve / lock booked ice

When the operator says a tournament placement is confirmed/booked, use:

```bash
scripts/rvv-miniputt season approve \
  --season <season> \
  --tournament-id <durable-id> \
  --note "<concise operator note>"
```

Approval is never a hard-rule waiver. The repository re-verifies the current placement before approval. Approved/placement-locked tournaments are hard-preserve constraints for later planning.

If an approval is reported as `stale_approval`, do not silently keep or recreate it. Inspect why the protected fields changed and require explicit reapproval after the intended current state is confirmed.

### Manual club booking/rejection when no calendar can resolve it

A blocked/empty calendar, a generic overlap, or an explicit club email that its bookings are made but not reflected in the public calendar is a normal source, not an emergency. Do **not** regenerate, reopen or replan the published season because a scrape failed; record the operator-accepted conclusion directly:

```bash
scripts/rvv-miniputt season booking-set --season <season> --tournament-id <id> --status booked \
  --reference "<email id/date/sender>" --note "<concise source summary>"
scripts/rvv-miniputt season booking-set --season <season> --tournament-id <id> --status not-booked \
  --note "club rejected the assigned slot"
scripts/rvv-miniputt season booking-clear --season <season> --tournament-id <id> --note "recorded against the wrong id"
```

This is durable, revision-bound source authority, not a scrape result: it lives in `manual_booking_assertions`, projects as `manually_booked`/`manually_not_booked` with `authority=manual_club_confirmation`, and a later `season reconcile-calendar-bookings`/`season refresh-calendars` never erases or demotes it. A contradicting calendar event surfaces as a review conflict instead of silently overwriting, and independent actionable calendar warnings (for example a stale association) stay visible as follow-up without demoting the club confirmation. A positive confirmation needs a traceable source: pass at least `--reference` or `--note`. Record the real source with `--reference`, and use `--source-scope club_wide_interpretation` when the assertion is one deliberately accepted per-tournament interpretation of a club-wide statement rather than a fabricated itemized confirmation; that scope projects as `authority=manual_club_confirmation_interpretation` (shown as `SKJØNNSVURDERT` in the plan). A source-stated interval that differs from canonical occupancy is captured with `--stated-start`/`--stated-end` as follow-up; canonical occupancy is never silently shrunk, the two values must be a same-day `HH:MM` window with end strictly after start (malformed, zero-length, reversed or overnight windows are rejected), and `--expected-revision` fails closed on stale state. Repeating the same assertion is idempotent. After a move or other material slot change the assertion becomes `stale`: re-confirm the new slot with a fresh `--reference`/`--note` and it is replaced directly, while the old record is kept as `superseded` audit history (no `--supersede` needed); changing the conclusion about a still-current slot requires `--supersede --note`; `season booking-clear` revokes an assertion recorded in error. Rejection never cancels or deletes the tournament (it stays visible as follow-up). Recording a manual booking assertion does not itself approve/lock the placement; use `season approve` when the booking is confirmed and the placement should be protected.

## Change an approved tournament

Do not bypass approval protection. First revoke the approval/locks explicitly:

```bash
scripts/rvv-miniputt season unapprove \
  --season <season> \
  --tournament-id <durable-id> \
  --note "<reason>"
```

Then apply the requested canonical move:

```bash
scripts/rvv-miniputt season move \
  --season <season> \
  --tournament-id <durable-id> \
  [--date YYYY-MM-DD] \
  [--arena "..."] \
  [--host-club "..."] \
  [--start-time HH:MM] \
  --request-id <request-id>
```

Do not alter participants or unrelated tournaments to make a targeted move fit unless the operator instead asked for replanning. A rejected move must leave canonical state unchanged.

A move is refused by default when it would newly place the tournament inside the host's `fixed_busy`/external-calendar interval, at a host with no trustworthy calendar, or in a host-controlled (`movable_busy`) slot that needs host confirmation -- even though `verify_candidate` may report such a placement as hard-valid manual work. This is deliberately stricter than hard validity: an automatic maintenance move must not trade a usable placement for known manual work. Use `--dry-run` to inspect `move_preview.operational_acceptability` (`regressions`, `requires_operational_opt_in`). Only when the operator explicitly asks for that exact provisional placement, repeat the command with `--allow-manual-placement` and/or `--allow-host-confirmation`; the opt-in is recorded in `decisions.json` history. Never infer the opt-in from a technically-successful verification.

Reapprove only when the new placement is actually confirmed/booked.


## Replace one tournament participant safely

Use this for a one-tournament substitution: "replace team A with registered same-age team B in this tournament". It keeps the date, time, arena and host unchanged, regenerates games, and reports consequences for the outgoing and incoming teams.

```bash
scripts/rvv-miniputt season replace-participant \
  --season <season> \
  --tournament-id <id> \
  --remove-team "<outgoing team>" \
  --add-team "<incoming team>" \
  --request-id <request-id> \
  --dry-run --json
```

Do not invent a second tournament for a plain substitution. Use `swap-participants` only when the operator requested a two-tournament exchange.

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

A bare label must identify exactly one affected team; when a label is shared by several affected teams (for example the same label in two age groups within one batch) qualify it as `"<club>|<team label>|<age group>=<code>"`, otherwise the command is refused as ambiguous. It covers only that exact material regression code for that exact affected team (`more_gaps_under_7_days`, `more_gaps_under_14_days`, `temporal_coverage_materially_worse`, `more_repeated_opponent_excess`, `travel_materially_worse`). Every other material regression still refuses. An acceptance that matches no regression in the candidate refuses the command, and the reason is mandatory. The dry-run reports `regression_acceptance` (accepted, unaccepted, unmatched), and a committed change records the accepted regressions and reason in decision history. Codes are never broadened or merged: a new gap under 7 days often also adds a gap under 14 days, and each code the dry-run lists under `unaccepted_regressions` must be accepted separately. Use it only when the operator has accepted that specific team/regression. Never infer it from a candidate merely being the best available, and never pass it pre-emptively.

If all otherwise-good candidates are blocked by a prior accepted request, do not release that request automatically. Follow the supersession rules in **Process incoming club/operator change requests**.

## Repair a localized finding against the promoted season

Do not re-run Stage 1–4 merely to reach the repair capabilities when the canonical season already has a valid factual baseline. Ask the repository what is actually wrong on the current revision:

```bash
scripts/rvv-miniputt season findings --season <season>
```

Findings are fresh, revision-bound facts (recomputed from the canonical plan, never the snapshot promoted with the season). They include unresolved hosting obligations, hosting-balance deficits, manual placements, hard violations and participation strong-goal deviations with their `avoidability`. A tournament the planner marked as an unresolved manual placement is reported even when its provisional slot is calendar-free (the marker is a plan-level fact verification cannot rediscover); when the responsible host has trusted calendar evidence with host-controlled movable ice, that opportunity is additionally exposed as its own `movable_capacity` finding that carries `requires_host_confirmation: true`, so the ice can be selected directly. A genuine unplaced obligation (a tournament the slot search could not place, so it is planning work with no `Tournament`) is reported as an `unplaced_tournament_placement` finding whose `search_coverage` states which supported repair dimensions were attempted and which remain untried. `bounded_repair_exhausted` on the obligation describes only the planner search that ran; it is not a claim that all meaningful repair dimensions are exhausted. Findings are independent: address whichever one the operator cares about next; there is no repository-imposed queue.

For one selected finding, enumerate the repository's verified alternatives (direct/cheap options first, then coupled options):

```bash
scripts/rvv-miniputt season repair-options --season <season> --finding <finding-id>
```

When the cheap options are insufficient, request a bounded, finding-directed search that preserves unrelated tournaments and approvals:

```bash
scripts/rvv-miniputt season search --season <season> --finding <finding-id>
```

Apply exactly one verified option from the current canonical revision:

```bash
scripts/rvv-miniputt season apply-repair --season <season> \
  --option-id <id> \
  --expected-revision <revision> \
  [--finding <finding-id>]
```

Every option (including the `coupled_placement` and `movable_capacity` families) is checked against the repository-owned operational-acceptability predicate in addition to hard verification: relative to the current canonical baseline it may not *newly* introduce a `fixed_busy`/manual external-calendar placement, a host with an untrusted calendar, unresolved/manual placement work, or a host-confirmation (`movable_busy`) dependency. Such options carry `operational_acceptable: false` and `requires_operational_opt_in`, are listed under `pareto.operational_rejected_option_ids`, and are kept out of the auto-applicable `pareto.non_dominated_option_ids`. Pareto scoring alone is not protection -- an option that fixes one defect while introducing a manual placement can remain non-dominated.

The apply boundary re-runs the predicate independently of the provider: a provider self-report never bypasses it. A refused option returns `reason: operational_acceptability_regression` with `required_opt_in_flags` and leaves canonical state byte-unchanged. Only when the operator explicitly accepts the provisional/manual placement, repeat the apply with `--allow-manual-placement` and/or `--allow-host-confirmation` (also accepted by `season repair-options`/`search` to keep such options on the reported Pareto front). Do not use the opt-in to make an ordinary automatic improvement look acceptable.

`--expected-revision` is the `revision` reported by `season findings`/`repair-options`. An apply against a changed revision is rejected as stale and leaves canonical state byte-unchanged. A successful apply is atomic, full-season verified, returns the new revision and a deterministic before/after delta, and re-derives findings from the new revision. For an `unplaced_tournament_placement` finding, `repair-options`/`search` return materialization options that build a real verified `Tournament` (the responsible host's arena, a verified date/start time, the final roster and regenerated games) and remove the obligation; the escalating dimensions are same-date start time, same-host date, bounded participant reselection, capacity release (move a scheduled tournament off the shared arena/time, including across age groups) and the full bounded neighborhood. Applying one commits the placement (and any paired blocker move) atomically. The finding's `search_coverage` distinguishes `option_available`/`search_incomplete`/`bounded_search_exhausted` and never claims `proven_infeasible`; when dimensions remain untried, request `season search` rather than treating the obligation as infeasible. A supported dimension whose hard precondition is unmet for this obligation (participant reselection when the roster collides on no tried date) is reported under `inapplicable`, not `untried`, so it never keeps the finding `search_incomplete` and requestable forever; reselection applicability is checked against the source date and every alternate date it would run on. A bounded `search` option carries its own dimension tag, so applying it does not require repeating the `--dimensions` it was produced with. Hosting responsibility stays authoritative: a repair may draw down a deficit only from a surplus, never by relocating the shortfall or transferring burden to a club that does not owe it. A `bounded_search_exhausted` participation deviation is never proof of infeasibility. Respect the repair-cost order: when a tournament's host/date/arena/start time are already valid and only the selected roster conflicts, the first options are verified placement-preserving participant substitutions -- do not move the slot or request a broader search while such a substitution exists.

When a club's own tournaments are clustered (a `temporal_clustering` finding), `repair-options`/`search` enumerate a bounded `coupled_placement` family: a cross-age placement exchange plus, where the exchange double-books a displaced tournament's team, a bounded **same-age** participant reselection for that tournament with regenerated games. The candidate is the fully verified coupled state, so a placement-only intermediate conflict does not reject a valid repair. Cross-age applies only to the *placement exchange*; participants never cross age groups, and a candidate that moves hosting responsibility is rejected. The option's consequence evidence covers every team whose schedule changed -- both moved tournaments' participants and any team the roster repair added or removed -- and an option that materially regresses any of them (new <7/<14-day gap, new participation shortfall, materially worse coverage/opponent repetition/travel) reports `consequence_acceptable: false`, is listed under `pareto.consequence_rejected_option_ids`, kept off `pareto.non_dominated_option_ids` and refused at apply with `reason: team_schedule_regression`. `season apply-repair` is still the only apply path. A `search` that finds nothing reports `bounded_search_exhausted`, not `proven_infeasible`. Apply the selected option through the ordinary `season apply-repair` boundary -- the coupled provider introduces no separate apply/persistence path.

`repair-options`/`search` measure every returned option on one canonical objective vector (hard violations, unresolved hosting obligations, hosting-balance imbalances, manual placements, participation deviations and avoidable deviations, host-confirmation dependencies, and changed-tournament count, merged with the shared Stage-3 soft-quality objectives and canonical travel) and report a bounded non-dominated `pareto` set (`non_dominated_option_ids` plus a small `representative_option_ids` down-select). Each option also carries `quality_vs_current` and `travel`, and the apply delta returns the same quality/travel before/after evidence. Prefer a non-dominated option; a verified option that is strictly worse everywhere than another is not an independent trade-off and must not be presented as one.

### Accept a participation deviation explicitly

When the operator deliberately decides to live with a remaining participation strong-goal deviation, persist that decision instead of leaving it as an unresolved finding or editing the target:

```bash
scripts/rvv-miniputt season accept-deviation --season <season> \
  --finding <participation-finding-id> \
  --note "<why this deviation is accepted>"
```

The acceptance is stored as an `operator_accepted` decision in `decisions.json` and injected into the same independent verifier, so the deviation stays visible with `avoidability: operator_accepted` but is no longer treated as an open finding. It never changes the schedule or the configured target. It is bounded to its scope, target and accepted deviation: if the target later changes or the deviation gets worse, the acceptance stops applying and the finding re-appears with `acceptance_stale: true`. Reopen it explicitly with:

```bash
scripts/rvv-miniputt season revoke-acceptance --season <season> \
  --finding <participation-finding-id> \
  --note "<reason>"
```

Do not use acceptance to hide an `avoidable` deviation that a bounded search can still improve; prefer `repair-options`/`search` first.

## Replan around the published baseline

Whole-season replanning is an escalation, not the default response to every localized defect. For broader unresolved/quality problems, keep approved/locked commitments fixed and search around the current canonical schedule:

```bash
scripts/rvv-miniputt season replan --season <season> --iterations <n>
```

Inspect the resulting candidate/change cost before applying it:

```bash
scripts/rvv-miniputt season diff --season <season> --candidate <candidate.json>
```

Prefer the smallest hard-valid change that resolves the problem. Published-but-unapproved tournaments are mutable but carry change cost; approved/locked fields are not negotiable.

Persist only through the verified apply boundary:

```bash
scripts/rvv-miniputt season apply --season <season> --candidate <candidate.json>
```

Never hand-edit canonical JSON or select a candidate that fails hard verification.

## Export after canonical changes

When canonical schedule **or decision state** changes and the result is meant for review/publication, regenerate the derived export from canonical state:

```bash
scripts/rvv-miniputt season export --season <season>
```

Then run the semantic safety-net audit from `.agents/skills/rvv/SKILL.md` and only afterwards use the shared `publish.md` procedure.

Do not knowingly audit or publish an older Stage 4 projection after the canonical season/approval state has changed. The export carries the canonical season revision/fingerprint; treat a mismatched or stale projection as requiring `season export`, not as permission to publish anyway.

## Normal promoted-season flow

```text
canonical season
  -> season findings (fresh, revision-bound)
  -> season repair-options / season search for ONE selected finding
  -> season apply-repair (atomic, revision-bound) when a local fix is enough
  -> OR approve/unapprove, targeted season move, or baseline-aware season replan
  -> diff + verified apply when replanning
  -> season export
  -> semantic audit
  -> publish
```

Use the ordinary `run --interactive` procedure again only when the user's goal genuinely requires a full pipeline run (for example refreshed upstream input/calendar evidence). Even then, Stage 3 adopts the canonical baseline for the matching promoted season rather than regenerating from scratch.

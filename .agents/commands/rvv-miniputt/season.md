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
```

Give the incoming request a stable `request_id`. Prefer an existing external/message/request id when one exists. Otherwise synthesize a deterministic local id from the source and intent (for example `club-feedback:<club>:<date>:<short-purpose>`) and check `season protections` so it does not collide. The operator should not need to invent this internal id.

Classify the requested outcome before choosing a command:

- **specific date/arena/host/time change** -> use the targeted `season move --request-id <id>` flow below;
- **specific participant/roster exchange** -> evaluate `season swap-participants --dry-run --request-id <id>`; an existing finding is not required;
- **general request to improve participation/placement** -> use current findings/repair-options/search first, then the smallest verified change;
- **broader rebalance** -> only then escalate to baseline-aware `season replan` / `diff` / verified `apply`.

For a participant swap, preview alternatives before applying. The repository reports before/after consequences for **both** affected teams (spacing, temporal coverage, opponent repetition/diversity and travel), and applying a materially regressive swap is refused. Do not optimize the requesting club by treating the displaced team as free capacity.

Accepted swaps and moves create granular protections in canonical `decisions.json`. A later candidate that would undo one is a conflict with prior accepted intent, not permission to discard it. When a protection blocks a candidate:

1. prefer another legal candidate that preserves all accepted requests;
2. if the new request **explicitly supersedes or reverses** the earlier request, release only the relevant earlier protection(s), recording the newer request in the note;
3. if the relationship is ambiguous and no safe alternative exists, ask for the real-world clarification rather than silently releasing the earlier request.

Explicit supersession:

```bash
scripts/rvv-miniputt season release-protection \
  --season <season> \
  --request-id <old-request-id> \
  --note "superseded by <new-request-id>"
```

Then apply the newer change with `--request-id <new-request-id>`. Never release a protection merely to make an optimizer or convenient swap candidate fit. Generic `season apply` is also protection-aware, so a broader replan cannot quietly reverse accepted feedback.

After any accepted mutation, re-run `season protections --json` and report which prior requests remain protected and which new protections were added.

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

Reapprove only when the new placement is actually confirmed/booked.


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

`--expected-revision` is the `revision` reported by `season findings`/`repair-options`. An apply against a changed revision is rejected as stale and leaves canonical state byte-unchanged. A successful apply is atomic, full-season verified, returns the new revision and a deterministic before/after delta, and re-derives findings from the new revision. For an `unplaced_tournament_placement` finding, `repair-options`/`search` return materialization options that build a real verified `Tournament` (the responsible host's arena, a verified date/start time, the final roster and regenerated games) and remove the obligation; the escalating dimensions are same-date start time, same-host date, bounded participant reselection, capacity release (move a scheduled tournament off the shared arena/time, including across age groups) and the full bounded neighborhood. Applying one commits the placement (and any paired blocker move) atomically. The finding's `search_coverage` distinguishes `option_available`/`search_incomplete`/`bounded_search_exhausted` and never claims `proven_infeasible`; when dimensions remain untried, request `season search` rather than treating the obligation as infeasible. A supported dimension whose hard precondition is unmet for this obligation (participant reselection when the roster collides on no tried date) is reported under `inapplicable`, not `untried`, so it never keeps the finding `search_incomplete` and requestable forever; reselection applicability is checked against the source date and every alternate date it would run on. A bounded `search` option carries its own dimension tag, so applying it does not require repeating the `--dimensions` it was produced with. Hosting responsibility stays authoritative: a repair may draw down a deficit only from a surplus, never by relocating the shortfall or transferring burden to a club that does not owe it. A `bounded_search_exhausted` participation deviation is never proof of infeasibility. Respect the repair-cost order: when a tournament's host/date/arena/start time are already valid and only the selected roster conflicts, the first options are verified placement-preserving participant substitutions -- do not move the slot or request a broader search while such a substitution exists.

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

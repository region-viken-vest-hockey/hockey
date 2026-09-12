# Handover: Issue #314 P0 (final-candidate integrity)

Status: **investigation done, no code changes made yet.** Paused by request to
do another task first ("actually we will do another task first, write a
handover document ... then we can do this later").

## Scope agreed with user

Issue #314 ("Steering: restore final-candidate integrity and season-wide
schedule quality before publication") bundles a P0 correctness bug plus
several P1 tuning/design items (cadence-aware temporal scoring, date-skeleton
steering, regression-protection views, production-run checklist).

User explicitly chose: **P0 only** for now. P1 items are out of scope for this
pass. A second issue for the *unrelated* `deltakelser_per_lag` /
per-team-participation-from-Aldersgrupper-sheet work will be filed separately
by the user — that is a **different, still-unimplemented task**, not part of
#314.

## The P0 bug, root-caused

Symptom from the issue: the final evidence bundle says
`ok = true, violations = []` for the applied CP-SAT candidate, but the
generated Regler page still shows `Harde krav: 12/13 oppfylt` /
`Arena-/tidskollisjon: fail — 2` — stale data from the baseline plan the
candidate replaced.

Root causes found (two independent bugs, both required to fully explain the
symptom):

### Bug 1 — `apply_stage3_candidate` never invalidates candidate-derived state

`tournament_scheduler/stage3_decision.py:273-289`, `apply_stage3_candidate`:

```python
checkpoint = dict(state.read_stage(StageName.PLANNING) or {})
checkpoint["plan"] = candidate
state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
```

Only swaps the `plan` key. Its own docstring says it "preserves every other
key already in the checkpoint (e.g. `warnings`, `rules_report`)" — that's the
bug. `rules_report` (top-level, prose from `rules_report.py`, built from a
live `SeasonPlanner`) and plan-nested fields (`fairness_gate`,
`arena_day_collisions`, `unresolved_hosting_obligations`,
`unresolved_external_conflicts`, `unresolved_participation_shortfalls`,
`shared_host_decisions`, `diversity_score`, `pairwise_matchup_score`,
`month_balance_score`, etc. — see `pipeline/stage3_helpers.py:61-83`
`_plan_to_dict` for the full field list) describe the *old* plan, not the
newly-applied `candidate`.

Constraint discovered: `rules_report(planner)` in `rules_report.py:11` and
`build_fairness_gate(planner, plan)` in `fairness_scoring.py:71` both require
a live `SeasonPlanner` instance (`planner.fairness_thresholds`,
`planner._per_team_share_warnings`, etc.) — **not** just a candidate dict.
`apply_stage3_candidate` only has `work_dir` + `candidate` (a plain dict), so
these cannot be recomputed at this call site without much larger plumbing
(reconstructing a planner from Stage 1 config + the candidate). That's true
architectural work belonging to the issue's "canonical planning problem"
pipeline, not a P0-sized fix.

**Verified, deterministic, planner-independent** functions that *can* be used
to detect/repair staleness without a planner:
- `find_arena_interval_collisions(plan.tournaments, round_length_for_age_group)` (already used in Stage 4)
- `verify_candidate(candidate, problem=None)` — `planning_contract.py:339`
- `score_candidate(candidate, ...)` — `planning_contract.py:716`
- `temporal_coverage.py` (`team_temporal_coverage`, `season_temporal_coverage`, `temporal_offenders`) — genuinely planner/candidate-agnostic, works off `plan.tournaments` + season window + roster meta
- `rules_model.build_rules_model(plan)` / `rules_summary_counts` — already planner-independent (issue #277/#305), reads only public `SeasonPlan` fields. **This is what the HTML Regler page already uses** (`html/html_exporter.py:183`), so it's automatically "fresh" for anything sourced directly from `plan.tournaments` / `plan.manual_adjustments`. The only genuinely-stale input it consumes is `plan.fairness_gate` (baked snapshot metrics, incl. an `arena_day_collisions` metric key — see `rules_model.py` `_METRIC_SCOPES`).
- The **Excel** "Regler og avgjørelser" sheet (`excel/plan_exporter.py:90-93`) is a *different, older* path that uses the stale top-level `checkpoint["rules_report"]` prose directly (not `build_rules_model`). Both `_write_rules_sheet` (only runs `if rules_report:`) and `_write_fairness_sheet` (only runs `if plan.fairness_gate:`) already no-op gracefully on falsy input — so setting these to `None`/`{}` after a candidate swap is safe, no crash risk, already verified by reading the code.

### Bug 2 — truthy-fallback AND conditional-write bug in Stage 4 collision handling

`tournament_scheduler/pipeline/stage4_export.py:212-213`:

```python
derived_collisions = find_arena_interval_collisions(plan.tournaments, round_length_for_age_group)
stored_collisions = derived_collisions or list(plan.arena_day_collisions or [])
```

If the fresh recomputation legitimately finds **zero** collisions, `[] or X`
falls back to `plan.arena_day_collisions` — which, per Bug 1, can still be
carrying the old baseline's stale 2-collision list. This is exactly the
`or`-fallback pattern issue #314 calls out by name ("recomputed empty is
authoritative... do not use truthiness fallbacks").

There is a **second, compounding** bug at line 316-317:

```python
if collision_entries:
    plan.arena_day_collisions = collision_entries
    ...
```

`plan.arena_day_collisions` is only *reassigned* when the freshly-derived
list is non-empty. Even after fixing the `or`-fallback above, a legitimately
empty `collision_entries` never overwrites `plan.arena_day_collisions` —
so the stale value set by `_dict_to_plan` when the checkpoint was loaded
survives untouched and is what `build_rules_model(plan)` (and any dedicated
new arena-collision rule) would read. **Both bugs must be fixed together**
for the "no stale baseline collision list survives" regression test to
actually pass.

## Planned fix (not yet applied)

1. **`stage4_export.py:213`** — drop the `or` fallback entirely:
   `stored_collisions = list(derived_collisions)`. Fresh recomputation from
   `plan.tournaments` is always available (both inputs — tournaments and
   `round_length_for_age_group` — are always present), so there's no
   legitimate case for trusting a stored value over it.

2. **`stage4_export.py:316-317`** — unconditionally assign
   `plan.arena_day_collisions = collision_entries` (move the assignment out
   of the `if collision_entries:` guard; keep the warning-log block gated on
   `if collision_entries:` since that part is genuinely conditional).

3. **`rules_model.py`** — add a dedicated `_arena_collision_rule(plan)`
   sourced directly from `plan.arena_day_collisions` (same pattern as the
   existing `_hosting_obligation_rule` / `_external_conflict_rule`), and
   append it in `build_rules_model`. Exclude the `arena_day_collisions` key
   from the generic `_metric_rule(m)` loop over `fairness_gate` metrics
   (filter `m.get("key") != "arena_day_collisions"` when building
   `all_metrics`), so a stale `fairness_gate` snapshot can never re-introduce
   a conflicting "fail — 2" line alongside the now-always-fresh dedicated
   rule. Remove the now-unused `"arena_day_collisions"` entry from
   `_METRIC_SCOPES`.

   This fixes the *general* case too (not just post-`apply_candidate`):
   `fairness_gate` is a one-time snapshot taken during Stage 3 planning and
   can already be stale relative to Stage 4's own collision recomputation
   even without a candidate swap — this was the literal reported symptom in
   the issue.

4. **`stage3_decision.py`, `apply_stage3_candidate`** — when swapping in a
   new candidate:
   - Strip known planner-dependent candidate-derived keys that cannot be
     honestly recomputed here from the candidate dict before writing it into
     `checkpoint["plan"]`: `fairness_gate`, `unresolved_hosting_obligations`,
     `unresolved_external_conflicts`, `unresolved_participation_shortfalls`,
     `shared_host_decisions`, `diversity_score`, `pairwise_matchup_score`,
     `month_balance_score`, `arena_counts`, `team_game_counts`,
     `game_count_spread`, `game_count_spread_by_age_group`,
     `team_last_game_dates`, `skipped_age_groups`. (`arena_day_collisions`
     itself doesn't need special handling here once fixes 1–2 land, since
     Stage 4 always freshly overwrites it regardless of what's inherited.)
   - Set `checkpoint["rules_report"] = None` (explicit "not recomputed for
     this candidate" rather than stale prose) — safe no-op downstream per the
     `if rules_report:` / `if plan.fairness_gate:` guards already in
     `plan_exporter.py`.
   - Update the function's docstring (currently claims it "preserves...
     rules_report", which is precisely the behavior being removed).

5. **Regression tests** to add (per issue's "Regression tests" section,
   scoped to what's fixed here):
   - Final-candidate derived-state invalidation: baseline plan with 2 arena
     collisions in checkpoint → apply a candidate with 0 real collisions →
     assert final export/HTML Regler shows 0, and re-run with a candidate
     that *introduces* a collision the baseline didn't have → assert it
     *does* show up (the inverse test the issue explicitly asks for, to
     prove there's no "hide everything" overcorrection).
   - `apply_stage3_candidate` unit test: assert `fairness_gate` and friends
     are absent from the checkpoint's `plan` after applying, and
     `checkpoint["rules_report"] is None`.
   - `rules_model.build_rules_model` unit test: a `SeasonPlan` with
     `arena_day_collisions` set but a `fairness_gate` claiming a different
     (stale) collision count → assert the rendered rule reflects
     `plan.arena_day_collisions`, not the fairness_gate metric.

## Explicitly NOT in this pass (P1, deferred per user's scope choice)

- Fingerprint invariant / reconciliation across selected/verified/exported
  candidates (issue asks for this; `pipeline/fingerprints.py`,
  `pipeline/evidence_bundle.py` already have prior art —
  `stable_payload_sha256`, `fingerprint_consistent` — but wiring a
  candidate-derived-state fingerprint through `apply_stage3_candidate` is
  more plumbing than this pass's scope).
- Full `fairness_gate` recomputation from a reconstructed planner after
  candidate swap (architecturally the "right" fix per the issue's "canonical
  planning problem" diagram, but requires passing Stage 1 config/roster
  through to `apply_stage3_candidate`, which today only receives
  `work_dir` + `candidate`).
- All P1 items: cadence-aware temporal scoring, date-skeleton steering,
  regression-protection per-team delta view, candidate-capability
  truthfulness, production-run acceptance checklist.
- `keep_baseline` at `cli/plan_command.py:840` is a pure no-op today (prints
  a message, touches nothing) — fine as-is, not in scope, just noting it was
  checked.

## Unrelated task mentioned in the same session (do not conflate)

User separately asked to implement "use per-team `deltakelser_per_lag` from
the Aldersgrupper sheet instead of one common number" after deleting the
common `deltakelser_per_lag` value from `input.xlsx`. That is **not** issue
#314 — no GitHub issue exists for it yet. User said they will file a ticket
for it later. No investigation has been done on that task yet (no file
locations identified, no root cause work started).

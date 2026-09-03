# Stage 3 v2 A/B benchmark — 2026-09-03, verifier fix + weight tuning follow-up

Follow-up to `docs/issue-257/ab-2026-09-03/`. Same production `.pipeline`
checkpoint (152 tournaments, 9 age groups), regenerated after fixing the
verifier bug that snapshot surfaced, plus a weight-tuning sweep against the
optimizer's `DEFAULT_WEIGHTS`.

## 1. Root-caused and fixed the 110 `participation_target_mismatch` violations

The previous snapshot's baseline had 113 hard-verification violations, 110 of
them `participation_target_mismatch` (e.g. JU12 teams expected to
participate 10 times because `before_christmas: 5, after_christmas: 5`, but
actually participating 6 times in a verified-correct plan).

Root cause: `verify_candidate` (`tournament_scheduler/planning_contract.py`)
treated an age group's `before_christmas`/`after_christmas` split as a
**per-team participation target** (`target = before + after`) when no
explicit per-team override was set. That's wrong — those fields are weights
`SeasonPlanner._split_tournament_counts_for_age_groups`
(`tournament_scheduler/season_planner.py:1135`) uses to divide an age
group's **tournament count** across the two halves of the season. The actual
per-team participation target, when no explicit override or global default
applies, is planner-inferred from tournament capacity
(`SeasonPlanner._team_target_tournament_count`,
`tournament_scheduler/season_planner.py:219`) — a heuristic the verifier's
own docstring already said it deliberately doesn't reproduce, so the check
should be skipped in that case, not silently point at the wrong number.

Fix: removed the before/after-split branch; the verifier now only checks
`participation_target_mismatch` when an explicit per-team or global default
target exists, matching what the docstring always claimed. Re-verifying the
same baseline plan against the same problem now reports 3 hard violations
(2× `arena_interval_conflict`, 1× `duplicate_participation_same_date` — both
pre-existing and unrelated to this bug), not 113.

## 2. Added `--weight NAME=VALUE` to `plan optimize` / `plan ab`

Per the previous snapshot's follow-up #1 ("retune `DEFAULT_WEIGHTS`... and
re-run `plan ab`"), `rvv-miniputt plan optimize` and `rvv-miniputt plan ab`
now accept repeatable `--weight NAME=VALUE` overrides
(`tournament_scheduler/cli/plan_command.py`, `cli/args.py`), so weight
tuning no longer requires editing `stage3_optimizer.DEFAULT_WEIGHTS` and
reinstalling. Example:

```bash
python3 -m tournament_scheduler.cli.rvv_cli plan ab --work-dir .pipeline \
  --iterations 40000 --seed 1 \
  --weight gap_under_7=8.0 --weight same_club_pairing=1.5
```

## 3. Weight sweep: still not promotable — this looks like a real Pareto frontier

With the verifier fix applied, re-running `plan ab` at `DEFAULT_WEIGHTS`
(`old_candidate.json`/`new_candidate.json`/`ab_report.json` in this
directory) still isn't promotable:

| metric | old | new | direction | regressed? |
|---|---|---|---|---|
| unique opponent pairs | 1301 | 1343 | higher better | no (▲) |
| pairwise novelty | 52.2% | 53.9% | higher better | no (▲) |
| pairs meeting 3+ times | 255 | 217 | lower better | no (▲) |
| max same-club teams/tournament | 5 | 3 | lower better | no (▲) |
| same-club pairing count | 112 | 145 | lower better | **yes** |
| gaps under 7 days | 24 | 28 | lower better | **yes** |
| gaps under 14 days | 110 | 167 | lower better | **yes** |

Same three regressions as before (this bug didn't affect the optimizer's
own quality tradeoffs, only which hard-constraint violations were reported).

Tried raising `same_club_pairing`/`gap_under_7`/`gap_under_14` relative to
`pair_repeat` (several combinations, 40k-80k iterations each, e.g.
`pair_repeat=2.0, same_club_pairing=1.5, gap_under_7=8.0, gap_under_14=2.0`
and more aggressive variants). Result in every case tried: turnaround and
same-club-pairing regressions shrink but don't disappear, and pushing them
further starts regressing opponent-pair diversity/novelty instead — moving
along the frontier, not off it. Isolating `same_club_pairing` alone (all
other weights zeroed) does reduce same-club pairings on its own (112 → 98
at 60k iterations), confirming the optimizer *can* address that metric in
isolation; it just can't satisfy all axes simultaneously with one global
weight vector applied uniformly across all 9 age groups.

**Conclusion:** a single global `DEFAULT_WEIGHTS` change is unlikely to make
this optimizer promotable as-is. Not changing `DEFAULT_WEIGHTS` in this
pass — none of the tried alternatives strictly dominate it. Next steps for
whoever picks this up:

1. Per-age-group weights (the 6 regressing age groups may not all need the
   same tradeoff).
2. Let the optimizer touch more of the skeleton (currently team-only swaps
   with dates/arenas/hosts fixed) — turnaround spacing is fundamentally
   date-driven, and this optimizer can't move dates.
3. A proper multi-objective solver (e.g. weighted-sum restarts with Pareto
   filtering, or CP-SAT with hard bounds on the regressing metrics instead
   of soft penalties) rather than single-weighted-sum simulated annealing.

Re-run with `scripts/rvv-miniputt` or
`python3 -m tournament_scheduler.cli.rvv_cli plan ab --work-dir .pipeline`
against the live checkpoint for fresh numbers; this snapshot will drift as
the pipeline is re-run.

# Stage 3 v2 — baseline-bounded participant + schedule-repair A/B (2026-09-04)

Issue #257 Task 1-5 (participant optimization), plus the skeleton follow-up:
extend the baseline-bounded approach to also repair hard schedule conflicts
by moving tournament dates, not just reassigning teams. Evaluated against
the real, live production `.pipeline` checkpoint — the same one behind the
currently published season plan.

## What this is, vs. the earlier `plan ab` snapshots

The earlier snapshots under `docs/issue-257/ab-2026-09-*` used
`optimize_candidate` (single weighted-sum simulated annealing), which trades
every quality metric against every other and — as those snapshots record —
could not clear the "zero material regressions" promotion bar.

This snapshot uses two baseline-bounded passes from `stage3_optimizer.py`,
run in sequence:

1. **`optimize_candidate_participants_bounded_multi_seed`** — per age group,
   the current assignment is a hard non-regression baseline: a team-swap
   move is rejected outright, not merely penalized, if it would push *any*
   metric `stage3_ab`'s A/B comparison tracks worse than baseline (same-club
   pairing/clustering, turnaround gaps, hosting spread, pair-repeat counts,
   unique pairs/novelty/inter-club diversity, min turnaround). Inside that
   feasible region, it minimizes pairs meeting 3+ times lexicographically
   ahead of maximizing unique pairs as a tie-breaker.
2. **`repair_schedule_conflicts_bounded_multi_seed`** — a participant-only
   optimizer never moves a tournament's date, so it cannot fix a violation
   like `arena_interval_conflict`, which comes from *when*/*where* two
   tournaments overlap and is often cross-age-group. This pass searches
   date swaps only (arena/host/roster untouched) across the *whole season*
   at once, bounded per age group against the season's hard-violation count
   and turnaround gaps — never allowed to make either worse than the input.

Both passes retain the baseline byte-for-byte wherever no improving,
in-bounds candidate exists — nothing is forced.

## Reproduction

```bash
python3 -m tournament_scheduler.cli.rvv_cli plan ab-participants \
  --work-dir .pipeline --seeds 1,2,3,4,5 --iterations 8000 \
  --output-dir docs/issue-257/ab-participants-2026-09-04 --json
```

`--repair-schedule` runs by default (pass `--no-repair-schedule` to compare
against participant-only optimization). `--seeds 1,2,3,4,5` is 5
deterministic restarts per age group / per repair attempt (issue #257 Task
4's minimum); whichever seed wins the lexicographic/violation-count
comparison is kept.

## Headline result

Both `report["dominates_baseline"]` and `report["production_ready"]` are
now **`true`**: the combined candidate has **zero hard violations** (down
from 2 pre-existing `arena_interval_conflict`s in the baseline) and **zero
protected-metric regressions**, overall or in any of 9 age groups.

| metric (season total) | baseline | optimized | delta |
|---|---|---|---|
| hard violations | 2 | **0** | **-2** |
| pairs meeting 3+ times | 255 | 145 | **-110 (-43%)** |
| unique opponent pairs | 1301 | 1312 | +11 |
| pairwise novelty | 52.2% | 52.6% | +0.4pp |
| inter-club diversity | 94.4% | 95.3% | +0.9pp |
| max pair repeat | 6 | 6 | = |
| same-club pairing count | 112 | 112 | = |
| max same-club teams/tournament | 5 | 3 | -2 |
| gaps under 7 days | 24 | 11 | -13 |
| gaps under 14 days | 110 | 93 | -17 |
| min turnaround (days) | 0 | 1 | +1 |
| hosting spread | 35 | 35 | = |

Note the baseline in this table is the *original* Stage 3 checkpoint before
either pass; the gaps-under-7/14 improvement is larger than the earlier
participant-only snapshot's because the schedule-repair pass's date swaps
also happened to shorten some turnarounds while resolving the arena
conflicts, without regressing any single age group (checked explicitly —
see "Fixing a masking bug" below).

## Per age group (participant pass)

| age group | status | seed | pairs meeting 3+ | unique pairs |
|---|---|---|---|---|
| JU10 | unchanged | — | 21 → 21 | 21 → 21 |
| JU12 | improved | 1 | 5 → 2 | 51 → 51 |
| JU8 | unchanged | — | 21 → 21 | 21 → 21 |
| U10 | improved | 1 | 17 → 4 | 244 → 246 |
| U11 | improved | 4 | 11 → 1 | 257 → 260 |
| U12 | improved | 1 | 1 → 0 | 135 → 136 |
| U7 | improved | 2 | 61 → 30 | 120 → 120 |
| U8 | improved | 1 | 50 → 32 | 245 → 247 |
| U9 | improved | 1 | 68 → 34 | 207 → 210 |

`JU10`/`JU8` report `unchanged` because those groups are a single fixed
tournament each (no second slot to swap a team into). Full per-group and
schedule-repair status is in `new_candidate.json`'s
`source.base_source.per_age_group_status` (participant pass) and
`source.{status,seed_used,baseline_violations,best_violations}` (repair
pass, which ran on top).

## Schedule-repair pass detail

- Baseline (post-participant-optimization) hard violations: 2 (both
  `arena_interval_conflict`, unchanged from the original baseline since
  team-swap-only moves can't touch them).
- Repaired: **0**, via `seed=4`, entirely by swapping tournament dates —
  arenas, hosts and rosters in `new_candidate.json` are identical to the
  participant-optimized candidate for every tournament ID.
- Turnaround gaps also improved as a side effect (gaps<7d: 21→11, gaps<14d:
  109→93, measured immediately after participant optimization, before
  repair) — the repair search is bounded to *never* make turnaround worse,
  per age group, but is free to make it better while resolving conflicts,
  and several of the found swaps did.

## Fixing a masking bug found while building this

An earlier version of `repair_schedule_conflicts_bounded` bounded
season-wide turnaround gap totals but not gaps *per age group* — exactly
the kind of aggregate-hides-a-regression bug issue #257 Task 1 fixed for
the A/B report itself. The first real run against this checkpoint caught it
directly: violations went to 0, but two age groups (`JU8`, `U7`) had their
`min_turnaround_days` quietly get worse while the season-wide total still
looked fine. `_per_age_group_gaps`/`_gaps_within_bounds` in
`stage3_optimizer.py` now bound gaps-under-7/14 *and* min-turnaround per age
group, computed cheaply from slot state (no `score_candidate` call needed
per search step) so the per-group check runs before the much more expensive
full-candidate `verify_candidate` call. The result above is the corrected,
fully per-age-group-bounded run.

## Decision per the issue's promotion gate

> If the combined participant candidate has zero protected regressions and
> improves at least some age groups, treat that as evidence that generic
> optimization can replace the participant-selection part of `SeasonPlanner`.

Both gates now pass: `dominates_baseline == true` and `production_ready ==
true` — the combined candidate has zero hard violations of its own, not
merely no-worse-than a baseline that had some. `SeasonPlanner` remains the
production fallback per the issue's non-goals (no full-season promotion
decision has been made outside this benchmark), but this is now real
evidence that a generic, non-hockey-specific bounded search can match the
production baseline on every tracked quality metric while additionally
fixing 2 real hard violations the current production plan has today.

## Files

- `problem.json` — normalized `planning_problem` used for both sides of the comparison.
- `old_candidate.json` — Stage 3 baseline (live `.pipeline` checkpoint), unchanged from the original snapshot.
- `new_candidate.json` — participant-optimized + schedule-repaired candidate; see `source` and `source.base_source.per_age_group_status`.
- `ab_report.json` — full `build_ab_report` output (verification, overall/per-age-group scores, `dominates_baseline`/`production_ready`).

No secrets/credentials in any of these — team/club names and calendar data
are the same public information already published in the season plan.

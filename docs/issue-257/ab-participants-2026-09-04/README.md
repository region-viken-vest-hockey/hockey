# Stage 3 v2 — baseline-bounded participant-optimization A/B (2026-09-04)

Issue #257 Task 1-5: implement and evaluate `optimize_candidate_participants_bounded_multi_seed`
(`tournament_scheduler/stage3_optimizer.py`) against the real, live production
`.pipeline` checkpoint — the same one behind the currently published season
plan.

## What this is, vs. the earlier `plan ab` snapshots

The earlier snapshots under `docs/issue-257/ab-2026-09-*` used
`optimize_candidate` (single weighted-sum simulated annealing), which trades
every quality metric against every other and — as those snapshots record —
could not clear the "zero material regressions" promotion bar: gains in
opponent diversity were bought by spending down same-club clustering or
turnaround spacing.

This snapshot uses the new `optimize_candidate_participants_bounded_multi_seed`
path instead. Per age group, the *current* Stage 3 assignment is a hard
non-regression baseline: a candidate move is rejected outright — not merely
penalized — if it would push any of these worse than baseline:

- same-club pairing count
- max same-club teams per tournament
- turnaround gaps under 7 / under 14 days
- hosting fairness spread
- pairs meeting 3+ times / max pair repeat
- unique opponent pairs / pairwise novelty / inter-club diversity
- minimum turnaround days

(Participation counts and each tournament's roster/host are already
invariant by construction — this optimizer only reassigns which team fills
an existing slot, never which slots exist.) Inside that feasible region, the
search minimizes repeated inter-club opponent pairs lexicographically ahead
of maximizing unique pairings/novelty as a tie-breaker. An age group with no
improving, in-bounds candidate is returned byte-for-byte unchanged from the
baseline.

## Reproduction

```bash
python3 -m tournament_scheduler.cli.rvv_cli plan ab-participants \
  --work-dir .pipeline --seeds 1,2,3,4,5 --iterations 8000 \
  --output-dir docs/issue-257/ab-participants-2026-09-04 --json
```

`--seeds 1,2,3,4,5` is 5 deterministic restarts per age group (issue #257
Task 4's minimum); per age group, whichever seed's result is the best
lexicographic improvement within bounds wins, so the combined candidate
never depends on getting lucky with one seed.

## Headline result

`report["dominates_baseline"]` is **`true`**: zero new hard violations,
zero protected-metric regressions, overall or in any single age group.
`report["production_ready"]` is **`false`** — the baseline itself carries
2 pre-existing hard violations (both `arena_interval_conflict`) that a
team-swap-only optimizer structurally cannot fix, since it never moves a
tournament's date or arena. See "Known baseline issues" below.

| metric (season total) | baseline | optimized | delta |
|---|---|---|---|
| pairs meeting 3+ times | 255 | 145 | **-110 (-43%)** |
| unique opponent pairs | 1301 | 1312 | +11 |
| pairwise novelty | 52.2% | 52.6% | +0.4pp |
| inter-club diversity | 94.4% | 95.3% | +0.9pp |
| max pair repeat | 6 | 6 | = |
| same-club pairing count | 112 | 112 | = |
| max same-club teams/tournament | 5 | 3 | -2 |
| gaps under 7 days | 24 | 21 | -3 |
| gaps under 14 days | 110 | 109 | -1 |
| min turnaround (days) | 0 | 1 | +1 |
| hosting spread | 35 | 35 | = |
| hard violations | 3 | 2 | -1 |

Every metric is at least as good as baseline (see `dominates_baseline`
above) — the two hard violations left are the pre-existing arena conflicts,
unchanged from baseline; the pre-existing `duplicate_participation_same_date`
violation happened to be resolved as a side effect of the swap search (not a
guarantee of this optimizer — it only prevents *introducing* new
double-bookings).

## Per age group

| age group | status | seed | pairs meeting 3+ | unique pairs | same-club pairing | gaps<7d |
|---|---|---|---|---|---|---|
| JU10 | unchanged | — | 21 → 21 | 21 → 21 | 4 → 4 | 0 → 0 |
| JU12 | improved | 1 | 5 → 2 | 51 → 51 | 0 → 0 | 1 → 1 |
| JU8 | unchanged | — | 21 → 21 | 21 → 21 | 2 → 2 | 0 → 0 |
| U10 | improved | 1 | 17 → 4 | 244 → 246 | 15 → 15 | 3 → 3 |
| U11 | improved | 4 | 11 → 1 | 257 → 260 | 22 → 22 | 9 → 9 |
| U12 | improved | 1 | 1 → 0 | 135 → 136 | 1 → 1 | 3 → 3 |
| U7 | improved | 2 | 61 → 30 | 120 → 120 | 19 → 19 | 0 → 0 |
| U8 | improved | 1 | 50 → 32 | 245 → 247 | 24 → 24 | 3 → 1 |
| U9 | improved | 1 | 68 → 34 | 207 → 210 | 25 → 25 | 5 → 4 |

`JU10`/`JU8` report `unchanged` because those groups are a single fixed
tournament each (no second slot to swap a team into), not because the
search failed to find an improvement — see `new_candidate.json`'s
`source.per_age_group_status[age_group].reason` for the exact fallback
reason per group.

## Known baseline issues (issue #257 Task 4's "verify the baseline too")

The **baseline itself** (`old_candidate.json`, the live Stage 3 checkpoint)
fails `plan verify` with 3 hard violations, unrelated to this optimizer:

- `duplicate_participation_same_date`: Ringerike 1 (U11) scheduled in two
  tournaments on 2026-10-25.
- `arena_interval_conflict` × 2: Holmenkollen ishall and Tønsberghallen each
  have an overlapping tournament on 2026-11-22.

These are pre-existing skeleton (date/arena) problems the currently
published plan already has. A participant-only optimizer cannot fix them by
construction; fixing them requires touching tournament dates/arenas, which
is explicitly out of scope for this task (see "Next step" below).

## Decision per the issue's promotion gate

> If the combined participant candidate has zero protected regressions and
> improves at least some age groups, treat that as evidence that generic
> optimization can replace the participant-selection part of `SeasonPlanner`.

`dominates_baseline == true` and 7 of 9 age groups improved (the other 2
have nothing to swap). This clears that bar. `SeasonPlanner` remains the
production fallback per the issue's non-goals; this snapshot is evidence for
expanding Stage 3 v2 to the tournament skeleton (dates/arenas/hosts) next,
which is also what's required to fix the 2 remaining real
`arena_interval_conflict` violations.

## Files

- `problem.json` — normalized `planning_problem` used for both sides of the comparison.
- `old_candidate.json` — Stage 3 baseline (live `.pipeline` checkpoint).
- `new_candidate.json` — baseline-bounded optimized candidate; see `source.per_age_group_status`.
- `ab_report.json` — full `build_ab_report` output (verification, overall/per-age-group scores, `dominates_baseline`/`production_ready`).

No secrets/credentials in any of these — team/club names and calendar data
are the same public information already published in the season plan.

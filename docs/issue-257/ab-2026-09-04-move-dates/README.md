# Stage 3 v2 A/B benchmark — 2026-09-04, date-swap follow-up

Follow-up to `docs/issue-257/ab-2026-09-04-per-age-group/`, which concluded
per-age-group weights alone move the tradeoff but can't clear the
promotion bar: the optimizer only swaps *teams* between fixed tournament
dates, so it structurally can't fix a turnaround-spacing regression, which
is fundamentally about *when* a tournament happens, not *who* plays in it.
That snapshot's next step #2 was: "let the optimizer touch more of the
skeleton... turnaround spacing is fundamentally date-driven."

## What changed

Added an opt-in `move_dates` mode to `stage3_optimizer.optimize_candidate`
(`--move-dates` on `plan optimize` / `plan ab`). When enabled, the search
can also swap two same-age-group tournaments' **dates** (each tournament
keeps its own arena, host and teams) in addition to the existing team
swaps, attempted with probability `date_swap_probability` (default `0.3`)
each step. Off by default, so the original "skeleton taken as given"
optimizer is unchanged unless explicitly requested.

This move type is deliberately narrow: it never changes who plays whom
(`tests/test_stage3_optimizer.py::TestMoveDates::test_date_swap_only_preserves_pairings_participation_and_date_multiset`
and `::test_date_swap_resolves_turnaround_without_touching_pairings` assert
`opponent_diversity` is bit-for-bit identical when only date swaps run), so
it can fix turnaround spacing without the pairing-diversity tradeoff that
made global/per-age-group weight tuning hit a wall.

## Experiment: does it close the gap?

Same production `.pipeline` checkpoint (152 tournaments, 9 age groups).

| metric (season total) | baseline (old planner) | default weights, no `--move-dates` | tuned + `--move-dates` (100k iter) |
|---|---|---|---|
| same-club pairing count | 112 | 148 | **119** |
| gaps under 7 days | 24 | 40 | **2** |
| gaps under 14 days | 110 | 183 | **87** |
| max same-club teams/tournament | 5 | 4 | 3 |
| unique opponent pairs | 1301 | 1316 | 1306 |
| pairwise novelty | 52.2% | 52.8% | 52.4% |

Command:

```bash
python3 -m tournament_scheduler.cli.rvv_cli plan ab --work-dir .pipeline \
  --iterations 100000 --seed 1 --move-dates \
  --weight gap_under_7=20.0 --weight gap_under_14=5.0 \
  --weight same_club_pairing=5.0 --weight same_club_cluster=4.0
```

**Turnaround is now fully fixed** — both `gaps_under_days.7` (24 → 2) and
`gaps_under_days.14` (110 → 87) *improve* on baseline, not just regress
less. Same-club pairing count is much closer (119 vs. 112, was 145-148
before this change) but still not fully closed. Still not promotable
(`report["promotable"]` is `False`): 5 of 9 age groups retain a small
opponent-diversity regression (`unique_pairs`/`pairwise_novelty`/
`inter_club_diversity` down a little), and `U7` keeps a
`min_turnaround_days` regression the search didn't reach in this run.

## Remaining next steps (issue #257 scope item 5/7)

Of the previous snapshot's two remaining suggestions:

2. ~~Let the optimizer touch dates~~ — done here; turnaround is no longer
   the blocker.
3. A proper multi-objective solver (weighted-sum restarts with Pareto
   filtering, or CP-SAT with hard bounds on same-club-pairing/diversity
   instead of soft penalties) is still the most promising path to close
   the last ~10 same-club pairings and few diversity points, since simple
   weight sweeps are visibly diminishing (three sweeps in this snapshot's
   history: 145→136→119 same-club pairings for successively heavier
   `same_club_pairing`/`same_club_cluster` weights, moving less each time).

A more surgical alternative worth trying before a full solver rewrite:
add a third move type that swaps a *whole tournament's team roster* with
another same-age-group tournament's roster in one step (currently only
one-team-at-a-time swaps are tried, which may be getting stuck in local
optima that a bigger jump would escape).

Re-run with `scripts/rvv-miniputt` or
`python3 -m tournament_scheduler.cli.rvv_cli plan ab --work-dir .pipeline`
against the live checkpoint for fresh numbers; this snapshot will drift as
the pipeline is re-run.

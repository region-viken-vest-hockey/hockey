# Stage 3 v2 A/B benchmark — 2026-09-04, per-age-group weight follow-up

Follow-up to `docs/issue-257/ab-2026-09-03-v2/`, which found that a single
global `DEFAULT_WEIGHTS` vector can't make the optimizer promotable against
the same-club-pairing/turnaround regressions it introduces, and suggested
per-age-group weights as next step #1.

## What changed

Added `AGE_GROUP:NAME=VALUE` as a second form for `--weight` on
`rvv-miniputt plan optimize` / `plan ab` (`tournament_scheduler/stage3_optimizer.py`,
`tournament_scheduler/cli/plan_command.py`, `cli/args.py`), so a weight can
target one age group instead of the whole season. `optimize_candidate` takes
a new `per_age_group_weights` parameter; `_objective` resolves, per pair/
cluster/gap term, which age group it belongs to and applies that group's
override on top of the global base weight.

## Experiment: does per-age-group tuning close the gap?

Same production `.pipeline` checkpoint (152 tournaments, 9 age groups) as
the previous snapshot. Baseline default-weights run (`ab_report_default_weights.json`,
20k iterations, seed 1) regresses on the same axes as before: `JU12`, `U10`,
`U11`, `U12`, `U7`, `U8`, `U9` all show same-club-pairing and/or turnaround
regressions.

Applied `same_club_pairing=3.0`, `same_club_cluster=4.0`, `gap_under_7=15.0`,
`gap_under_14=3.0` (vs. `DEFAULT_WEIGHTS` of `1.0`/`2.0`/`5.0`/`1.0`) to just
the six regressing age groups (`JU12`, `U10`, `U12`, `U7`, `U8`, `U9`),
leaving `JU10`/`JU8` (already regression-free) and `U11` (different failure
shape — regressed on diversity, not clustering) at defaults
(`ab_report_per_age_group_tuned.json`).

| metric (season total) | old (baseline) | new, default weights | new, per-age-group tuned |
|---|---|---|---|
| same-club pairing count | 112 | 148 | 136 |
| gaps under 7 days | 24 | 40 | 19 |
| gaps under 14 days | 110 | 183 | 143 |
| max same-club teams/tournament | 5 | 4 | 3 |
| unique opponent pairs | 1301 | 1316 | 1318 |
| pairwise novelty | 52.2% | 52.8% | 52.9% |

**Conclusion:** per-age-group weights move the tradeoff (`gaps_under_7`: 40 →
19, better than default-weights *and* close to baseline's 24) without
sacrificing opponent diversity, confirming the previous snapshot's
hypothesis that different age groups need different tradeoffs. But it does
**not** eliminate the regressions — `same_club_pairing_count` (136 vs. 112)
and `gaps_under_14` (143 vs. 110) are still worse than baseline, and `JU12`
picked up a *new* `pairs_meeting_3_plus` regression it didn't have before
(pushing harder on clustering/turnaround for that group traded away some of
its opponent-diversity gain). Not promotable at this weighting either;
`report["promotable"]` is `False` in both runs.

This is consistent with the earlier snapshot's read: the optimizer's
constraint is structural, not just a weight-tuning problem. It only swaps
teams between fixed dates/arenas/hosts, so it cannot fix a short-turnaround
regression that requires moving a tournament's *date*, and per-age-group
weights can shift where the season sits on the frontier but not move the
frontier itself.

## Remaining next steps (issue #257 scope item 5/7)

Per-age-group weights are now available as a tool but, on their own, still
don't clear the "zero material regressions" bar in the issue's promotion
criteria. Of the previous snapshot's three suggestions, #1 is now done;
still open:

2. Let the optimizer touch more of the skeleton (dates/arenas/hosts are
   currently fixed) — turnaround spacing is fundamentally date-driven.
3. A proper multi-objective solver (weighted-sum restarts with Pareto
   filtering, or CP-SAT with hard bounds on the regressing metrics instead
   of soft penalties) rather than single-weighted-sum simulated annealing.

Re-run with `scripts/rvv-miniputt` or
`python3 -m tournament_scheduler.cli.rvv_cli plan ab --work-dir .pipeline`
against the live checkpoint for fresh numbers; this snapshot will drift as
the pipeline is re-run. Example per-age-group invocation:

```bash
python3 -m tournament_scheduler.cli.rvv_cli plan ab --work-dir .pipeline \
  --iterations 20000 --seed 1 \
  --weight U10:same_club_pairing=3.0 --weight U10:gap_under_7=15.0
```

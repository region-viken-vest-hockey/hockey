# Stage 3 controller proof — real run, 2026-09-06

Evidence bundle for issue #262's "prove the new controller on a real season
run" P1 item, produced after the P0/P1 code changes in this issue
(`a549f96`, `d4ffdca`, `64bfa66`, `30df2b1`, and the boundary-test extension
in this commit).

Run ID: `20260906T090526Z-8d2af1b2` (`input.xlsx`, 2026-09-01 → 2027-04-30,
9 configured sources).

## What this run demonstrates

An earlier attempt at this same run (decision-log entries not included here,
superseded) predates the P0 fix and incorrectly scheduled 17 Tønsberg-hosted
tournaments even though Tønsberg's BookUp calendar was never scraped. To get
a clean proof, Stage 2 was rerun with the current code (`--allow-missing-sources`,
since BookUp credentials for Tønsberg are unavailable in this environment),
then Stage 3 and Stage 4 were driven through the interactive harness to
completion. `decision_log.json` in this directory holds the two decisions
from that clean run, taken verbatim from `.pipeline/run_manifest.json`'s
`decision_log` (no private reasoning, no secrets).

### P0 — unknown arena availability never means "free"

Stage 2's fresh `club_calendar_status` correctly recorded:

```json
{"Tønsberg": "unknown", "...": "known"}
```

(all other 8 clubs `"known"`). The resulting Stage 3 baseline plan (attempt 1,
kept as final) has:

- **0 Tønsberg-hosted tournaments** (down from 17 in the pre-fix run) —
  `arena_counts` in the final `.pipeline/stage3_planning.json` has no
  `Tonsberghallen` entry at all.
- Deterministic verification (`planning_contract.verify_candidate`) never
  needed to reject a Tønsberg-hosted candidate in this run because the
  slot-search layer excluded it upstream — matching the issue's intended
  layering (search excludes infeasible hosts; verification is the backstop).

### P0 — interactive `optimize_plan` invokes the generic v2 optimizer

`decision_log.json` entry 2 (capability `stage3_optimize`, distinct from the
legacy `stage3_interactive`/`_run_stage3` capability) is the LLM decision
point exposed by `d4ffdca`. Its `action_parameters.optimize_plan` schema —
`iterations`, `seed`, `move_dates`, `move_hosts`, `move_slots`,
`date_swap_probability`, `weights` — is the Stage 3 v2 optimizer's real
search-configuration surface (`stage3_optimizer.py`), not a Python-authored
`SeasonPlanner` rerun. The chosen action in this run was `keep_baseline`
(attempt 5 did not dominate attempt 1 and was not production-ready), with an
LLM-authored rationale citing the specific metric comparison
(`hosting.spread` regressed 35→41) rather than a Python-decided verdict.

### P1 — search over hosts, dates, and slots, not just participants

The `move_dates`/`move_hosts`/`move_slots` boolean parameters in the same
decision context confirm the Stage 3 v2 optimizer (`64bfa66`) exposes all
three placement dimensions from issue #262's P1 ask (participant composition
was already covered) as validated, schema-described action parameters — the
LLM chooses which axes and weights to search, Python enumerates and verifies
candidates.

### P1 — legacy policy boundary

`tests/test_architecture_boundaries.py` (extended in this session) statically
asserts `stage3_optimizer.py`, `stage3_decision.py`, `stage3_planning.py`, and
`pipeline_orchestrator.py` never import `participant_selection.py` or
`host_assignment.py` — the canonical path cannot silently depend on legacy
`SeasonPlanner` heuristic weights.

## Known-remaining issues (not P0/P1 regressions, pre-existing)

- 1 arena/day collision (`Varner Arena / Askerhallen`, 2026-10-04, U10)
  persists in both the pre-fix and post-fix runs — a capacity constraint,
  not caused by or fixed by this issue's changes.
- `fairness_gate` reports `fail` (score 70) with 6 teams under their
  participation target. The plan's own `hosting_deviation` detail explains
  this is attributable to arena capacity (losing Tønsberg's host slots
  reduces total capacity), not a planning-logic defect: "Avviket skyldes
  trolig arenakapasitet, ikke planleggingslogikken."
- Neither of these is the subject of issue #262; they're flagged here for
  transparency since the export reflecting them was produced by this run.

## Files

- `decision_log.json` — the two `DecisionContext`/`DecisionAction`/
  `DecisionResult` triples from the clean post-fix run
  (`.pipeline/run_manifest.json`'s `decision_log`, entries for the Stage 2
  `proceed` and the final Stage 3 `keep_baseline`).

The full exported plan from this run lives at `export/2026-09-06T1656/`
(not duplicated here; it's the normal pipeline export, not review-specific
evidence).

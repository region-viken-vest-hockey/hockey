# Stage 3 v2 export/publication compatibility (2026-09-04)

Issue #257 acceptance criterion: "Existing Excel/CSV/HTML/iCal/Spond/export/publication behavior remains compatible."

## Why no conversion step was needed

`candidate_from_plan_dict` (`tournament_scheduler/planning_contract.py`) wraps a
Stage 3 checkpoint's `plan` payload in the candidate envelope by adding only
`schema_version` and `source` — it does not change the shape of `tournaments`,
`teams`, `games`, or any of the score/gate fields. `stage4_export._dict_to_plan`
reads exactly that shape. So a Stage 3 v2 candidate is a valid Stage 4 input
by construction, with no adapter or format-translation layer required.

## Verification

1. **Real production checkpoint.** Ran `tournament_scheduler.pipeline.stage4_export.run`
   directly against both `old_candidate.json` (live baseline) and
   `new_candidate.json` (Stage 3 v2 output) from
   `docs/issue-257/ab-participants-2026-09-04/`, wrapped as `{"plan": candidate}`.
   Both produced the full output surface — Excel, iCal, CSV (games + overview),
   HTML overview, HTML diagnostics report, manual-schedule view, Spond
   attachment, Spond game sheet, and per-club review packets — with zero
   export errors. The one flagged arena/day collision and the 22
   missing-calendar manual-booking tournaments are identical between the two
   runs (pre-existing production data-source gaps, not something Stage 3 v2
   introduced).
2. **Regression test.** Added
   `TestRunStage4.test_exports_stage3_v2_candidate_envelope_unchanged` in
   `tests/test_stage4_export.py`, which runs a minimal plan dict through
   `candidate_from_plan_dict` and then through `stage4_export.run`, asserting
   the full output-file set is produced with no errors. This pins the
   compatibility contract in CI rather than relying only on the one-off
   production run above.

## Conclusion

Stage 3 v2 candidates are already fully compatible with the existing
Excel/CSV/HTML/iCal/Spond/review-packet export pipeline and GitHub Pages
publication path — no changes to Stage 4 were required or made. This closes
the last unverified item of issue #257's acceptance criteria; see the issue
for the remaining checkbox updates.

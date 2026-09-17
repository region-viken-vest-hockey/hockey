# RVV Miniputt: waiver

Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` before operating the pipeline.

`waiver` is the operator-only surface for explicitly authorizing a narrowly
scoped exception to a hard planning rule. Agents and planners may *suggest* a
waiver through `request_operator`, but they must never create, broaden or
silently infer one.

```bash
scripts/rvv-miniputt waiver list [--all]
scripts/rvv-miniputt waiver create --rule participation_hard_max_exceeded \
  --club "<club>" --team "<team label>" --age-group <age group> \
  --tournament <tournament id> \
  --allowed-value <accepted actual> --reason "<operator reason>" [--actor <id>]
# Legacy target-based scope (kept for existing records):
scripts/rvv-miniputt waiver create --rule participation_target_exceeded \
  --club "<club>" --team "<team label>" --age-group <age group> \
  --tournament <tournament id> --half before_christmas|after_christmas \
  --allowed-value <accepted actual> --reason "<operator reason>" [--actor <id>]
scripts/rvv-miniputt waiver revoke <waiver-id> --reason "<why>" [--actor <id>]
```

`create` validates the scope against the current Stage 1/Stage 3 checkpoints
(team identity, tournament membership, configured value, and the exact
resulting count) before writing anything, then persists an audited record in
`<work-dir>/operator_waivers.json`. The verifier honors a matching active
waiver as an explicit, non-blocking `waived_violations` finding and reports
publication readiness as `REVIEW_REQUIRED`; revoking it (or any scope/value
change) restores the normal hard failure.

A participation *target* is a strong operational goal, not a hard ceiling, so
an ordinary bounded target deviation never needs a waiver. Only an explicitly
configured `participation_hard_max` is a genuine, waivable hard rule; use
`participation_hard_max_exceeded` for that. Use this instead of editing
checkpoints or changing a global participation cap for a one-off exception.
Structural invariants (unknown team ids, corrupt/inconsistent data) are never
waivable.

Shared waiver policy belongs in `.agents/skills/rvv/SKILL.md`, not in harness
adapters.

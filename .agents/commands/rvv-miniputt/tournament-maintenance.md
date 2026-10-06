# Tournament maintenance (lazy-loaded)

Load for adding, moving, postponing, cancelling, repairing or changing a tournament roster. Read the relevant sections of `season.md` for the actual canonical command contract; this file orchestrates, never substitutes for repository validation. For calendar/booking claims also load `booking-management.md`; for distribution trade-offs load `schedule-quality.md`.

## Common workflow
Establish lifecycle, canonical revision, tournament identity, approvals/locks, protections, constraints, accepted booking evidence and publication state. Assign a stable request id. Determine whether the operator requests investigation, a proposed change or execution. Preserve previous accepted intent. Use repository dry-runs, findings, revision-bound repair options and bounded search to compare complete candidates before writing. Prefer one atomic final mutation, then verify, refresh findings, export and audit as appropriate. Never infer permission to unapprove, accept regressions, reopen planning or publish.

## Move or postpone
For an existing canonical tournament, load `.agents/skills/rvv-move-tournament/SKILL.md` and follow its calendar-first evidence contract before unapproving or mutating. Resolve the registered host/arena source, refresh and prove coverage of the proposed full interval, compare existing RVV bookings and conflicts, and distinguish a free candidate from accepted booking evidence. A failed/incomplete scrape does not mean the calendar does not exist or that ice is free. Reconcile to an authoritative accepted calendar booking's exact interval when present; otherwise keep the replacement pending until actual confirmation. Do not transfer old interval-specific booking evidence to a new placement. After mutation, run scoped publication validation before any authorized publication.
Translate club feedback into the narrowest supported scoped constraint before searching. Host/arena unavailability is not team unavailability or a global date ban. If that scope is unsupported, report the gap and do not claim it is enforced. Compare time changes, dates across the permitted season (including both sides of Christmas), and legal coupled repairs; evaluate exact-interval booking evidence, all affected teams, spacing, temporal balance, hosting and churn. A first hard-valid date is not sufficient. For approved tournaments follow the explicit unapproval and reapproval authority in `season.md`; reapprove only on accepted confirmation.

## Cancel
Distinguish cancellation from postponement. Inspect booking and publication status, the reason and affected roster, participant appearance deficits, host obligation and downstream tournaments. Preview the cancellation and feasible replacement/reassignment alternatives; never silently remove accepted commitments. Apply only through supported canonical cancel/batch operations and explicit narrow regression acceptance when actually authorized; for a reviewed cancellation candidate, `season batch` can accept the exact reviewed consequence set with `--accept-reviewed-consequences` instead of reconstructing per-team arguments (see `season.md`). Preserve cancellation provenance and verify every affected team.

## Add or materialize
Distinguish a new tournament obligation from an existing unplaced obligation. Check authoritative registrations, host responsibility, age eligibility, capacity, calendar evidence, participation targets, spacing and other tournaments. Prefer repository `unplaced_tournament_placement` findings and revision-bound materialization options when applicable. Never hand-create a tournament in canonical JSON or infer booking from a free calendar slot. If there is no supported canonical creation path, report the capability gap.

## Change roster
Differentiate one-tournament replacement, true two-tournament swap, removal without replacement, season/age-group withdrawal, guest-place change and registration-set change. Use the matching dry-run in `season.md`; evaluate consequences for every displaced and added team. Never cross age groups or treat the displaced team as free capacity. Use atomic batch for coupled changes; explicit acceptance is required for named material regressions. A team rename uses the identity correction path, not roster replacement.

## Team retirement / season-age withdrawal
Before treating a season/age-group withdrawal as roster removals, determine whether the withdrawing team or club owns any future hosting obligations in that age group. A genuine team retirement is not equivalent to `remove-participant` and must not leave an active home tournament behind with only visiting teams.

Classify future affected tournaments from the effective date into hosted obligations and away participation. Hosted obligations must be cancelled or otherwise explicitly resolved as hosting obligations; do not silently transfer hosting responsibility. Away appearances use the durable participation-withdrawal path and preserve completed/historical participation. After the withdrawal, evaluate affected away tournaments for safe same-age rebalancing using booked-reality participant fairness, spacing, travel, accepted requests/protections and locks; unresolved vacancies may remain explicit rather than forcing a materially worse substitution.

Use the first-class repository-owned retirement capability:

```bash
scripts/rvv-miniputt season retire-team \
  --season <season> \
  --club <host-club> \
  --team <team-label> \
  --age-group <age-group> \
  --effective-from <YYYY-MM-DD> \
  --request-id <stable-request-id> \
  --dry-run
```

The dry-run classifies every future affected tournament into:
1. **Hosted obligations** (tournaments the retiring club hosts) -> cancelled with `cancellation_reason: team_retirement` and provenance recorded
2. **Away participation** (tournaments hosted by other clubs) -> team withdrawn with durable age-group withdrawal record (`effective_from`)
3. **Rebalance proposals** for affected away tournaments, evaluated against booked-reality fairness, spacing, travel, locks, protections and active requests; unresolved vacancies reported explicitly

The preview exposes hosted cancellations, away withdrawals, rebalance proposals, unresolved vacancies, and material consequences (verification, hosting responsibility, change protections, request constraints) separately so the operator can inspect before applying.

To apply, repeat the same command without `--dry-run`. Optionally accept rebalance proposals with `--accept-rebalance --rebalance-proposals <json-file>`.

The operation is atomic, revision-bound, and runs through the normal canonical consequence/verification gates. It preserves existing booked reality everywhere outside the retirement scope. It never reopens planning or globally replans the season.

Do not interpret a genuine team retirement as a batch of `remove-participant` operations; that preserves hosting obligations and leaves an active home tournament with only visiting teams. Use the `retire-team` command instead.

## Repair
Inspect fresh findings, direct options and bounded search; preserve valid placements when only roster is defective. Use the repository's verified option, operational-acceptability and consequence gates. Search exhaustion is not infeasibility. Do not mutate the season through temporary moves merely to expose another option.

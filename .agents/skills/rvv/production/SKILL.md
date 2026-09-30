---
name: rvv-production
description: Shared harness-neutral policy for maintaining an already promoted or published RVV season. Load for canonical-season booking, calendar, participant, constraint, placement, cancellation, audit/export or other incremental maintenance.
---

# RVV published-season production maintenance

Read `AGENTS.md` and `../SKILL.md` first. This file is shared by every harness.

## Boundary

The current published/sealed season is an operational baseline. Maintain it through targeted canonical operations; do not invoke Stage 3, regenerate the season, normalize placements mutably, force-promote, or reopen planning merely because a maintenance operation is difficult. A fundamental restructuring requires explicit operator authority through the supported reopen lifecycle.

Start from the smallest production capability (booking, calendars, participants, constraints, placement, cancellation, audit/export or publication) and load its command procedure and focused code/tests only.

## Canonical mutation contract

All production writers must preserve one revision-bound shape:

```text
authoritative revision
-> proposed change in memory
-> effective baseline/candidate facts
-> classify resolved/unchanged/new/worsened findings
-> capability policy
-> exact preview
-> atomic commit against same revision
-> reload / verify / audit
```

Do not create caller-local grandfathering, waiver, evidence, verification or acceptability semantics. Rejected operations leave active canonical state unchanged except for deliberately durable rejected evidence defined by the reconciliation contract.

Verification, reconciliation, mutation, audit/export and publication consume one revision-bound effective canonical projection. Their policy gates remain distinct: accepted evidence is not approval, approval is not booking proof, and neither is publication authority.

Read ADR 0005 for booking/reconciliation architecture. Calendar absence alone is not cancellation evidence. Reconciliation records reality; it does not plan/search/repair or fabricate duration. Preserve exact authoritative evidence even when it cannot be applied to active placement.

## Routing

- General canonical lifecycle and targeted changes: `../../../commands/rvv-miniputt/season.md`
- Tournament/participant/cancellation maintenance: `../../../commands/rvv-miniputt/tournament-maintenance.md`
- Booking state/assertions: `../../../commands/rvv-miniputt/booking-management.md`
- Booking/calendar reconciliation: `../../../commands/rvv-miniputt/booking-reconciliation.md`
- Calendar operations: `../../../commands/rvv-miniputt/calendars.md`
- Export/delivery: `../../../commands/rvv-miniputt/season-delivery.md`
- Publication/republish: additionally load `../publication/SKILL.md`

Use dry-run/preview where exposed. Never hand-edit canonical JSON, decisions, generated exports or audit artifacts.

## Agent locality

If an ordinary published-season booking, calendar, participant, constraint, move or cancellation task appears to require broad `SeasonPlanner` or Stage 3 context, stop and re-check ownership. Production architecture convergence is tracked separately from next-season planner decomposition; do not hide semantic changes in structural cleanup.

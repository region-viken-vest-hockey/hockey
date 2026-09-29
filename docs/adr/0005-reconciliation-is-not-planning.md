# ADR 0005: Reconciliation records booking reality; planning proposes placements

Status: Accepted (2026-09-29). Implementation tracked by [#504](https://github.com/region-viken-vest-hockey/hockey/issues/504). This decision specifies intended architecture; it does not assert that current code already conforms.

## Context
The 2026–2027 booking incidents (#500/#502/#515/#533/#536/#542/#544/#550/#556/#558/#504) exposed repeated conflation of source-confirmed bookings with proposed placements, partial canonical overlay reconstruction, and divergent findings between reconciliation, export and publication. In particular, a real shorter interval must not be stretched to meet a planning floor or rejected merely because the planning verifier treats it as a new placement.

## Decision
1. **Planning proposes changes to reality; reconciliation records reality.** A planning candidate is subject to the governing placement floor and explicit authorization for exceptions. Reconciliation identifies authoritative, sufficiently scoped evidence for an existing booking and proposes only the exact observed change. It does not search, optimize, repair, change participants, inflate duration, or implicitly approve, lock or publish.
2. **Evidence acceptance and active placement acceptance are separate outcomes.** Preserve traceable source evidence even if its proposed canonical placement fails a hard operational check. A failed change must not mutate the active placement, grant approval/lock, or mark it fully reconciled. Report a specific conflict for operator resolution.
3. **Validate the exact changed facts.** Moving a booking's day/time/arena must check team availability and same-day participation, host/arena/date restrictions, occupied-ice overlap, accepted protections, source identity and actual playing feasibility. Genuine hard conflicts remain hard; accepted evidence is not a universal waiver.
4. **Classify findings in their workflow.** A confirmed interval shorter than the governing *planning* floor remains exact and creates a durable audit/follow-up finding, not a new-placement hard violation. Inadequate actual playing time or genuine overlap remains independently hard. Ordinary unverified proposed below-floor placements remain hard-invalid without their separately authorized scoped exception.
5. **One effective canonical projection.** Canonical schedule, decisions, accepted evidence and overlays must be projected by a single revision-bound owner. Reconciliation, findings, semantic audit, export, preflight and publication seal consume equivalent effective facts; consumers must not reconstruct partial overlays or compare different contexts while claiming same-revision parity.
6. **One set of underlying facts and rule calculations.** Share source identity, occupancy and hard-constraint calculations. Keep workflow interpretation at the application boundary; do not fork a second evidence engine, verifier, state store or rule table.
7. **Atomicity and publication authority.** Failed canonical changes leave no incidental approval, lock or other mutation. Source confirmation does not itself authorize public publication. Publication retains its independent explicit authorization, privacy, integrity, revision and safety gates.

## Rejected alternatives
- Teaching the planning verifier to accept reconciled bookings by injecting provisional assertions or broad evidence-dependent floor bypasses.
- Fabricating occupancy or default-duration extensions to make an existing booking fit planning.
- Reimplementing canonical overlay projection independently in CLI, export, audit or publication.
- Automatically repairing conflicts or discarding genuine source evidence when canonical application is rejected.
- Treating a booking badge, generic calendar overlap, incomplete scrape or historical association as sufficient current exact-interval authority.

## Required regression contract
Exercise confirmation/booking-set -> persistence -> reload -> findings -> semantic audit -> export -> preflight/seal for the same revision. Include exact short booking, unverified proposal, stale/wrong owner evidence, moved-day unavailable or already-scheduled team, true arena overlap, actual playing shortfall, rejected-operation atomicity and retained conflicting evidence. Validate effective interval, provenance, classification and publication status across consumers. Use hermetic fixtures; do not mutate live season or publish to prove the architecture.

## Ownership and rollout
[#504](https://github.com/region-viken-vest-hockey/hockey/issues/504) owns the replacement implementation and test contract. [#513](https://github.com/region-viken-vest-hockey/hockey/issues/513) owns new-placement exception authorization; [#490](https://github.com/region-viken-vest-hockey/hockey/issues/490) owns operator acceptance; [#457](https://github.com/region-viken-vest-hockey/hockey/issues/457) is an optional XLSX adapter; [#535](https://github.com/region-viken-vest-hockey/hockey/issues/535) is behavior-preserving modularization. The generated rule catalog remains generated from its code owner.

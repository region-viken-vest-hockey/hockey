# ADR 0005: Reconciliation records booking reality; planning proposes placements

Status: Accepted (2026-09-29). Implementation tracked by [#504](https://github.com/region-viken-vest-hockey/hockey/issues/504). This decision specifies intended architecture; it does not assert that current code already conforms.

## Context
The 2026–2027 booking incidents (#500/#502/#515/#533/#536/#542/#544/#550/#556/#558/#504) exposed repeated conflation of source-confirmed bookings with proposed placements, partial canonical overlay reconstruction, and divergent findings between reconciliation, export and publication. In particular, a real shorter interval must not be stretched to meet a planning floor or rejected merely because the planning verifier treats it as a new placement.

## Decision
1. **Planning proposes changes to reality; reconciliation records reality.** A planning candidate is subject to the governing placement floor and explicit authorization for exceptions. Reconciliation identifies authoritative, sufficiently scoped evidence for an existing booking and proposes only the exact observed change. It does not search, optimize, repair, change participants, inflate duration, or implicitly approve, lock or publish.
2. **Evidence acceptance and active placement acceptance are separate outcomes.** Preserve traceable source evidence even if its proposed canonical placement fails a hard operational check. A failed change must not mutate the active placement, grant approval/lock, or mark it fully reconciled. Report a specific conflict for operator resolution.
3. **Validate the exact changed facts.** Moving a booking's day/time/arena must check team availability and same-day participation, host/arena/date restrictions, occupied-ice overlap, accepted protections, source identity and actual playing feasibility. Genuine hard conflicts remain hard; accepted evidence is not a universal waiver.
4. **Classify findings in their workflow.** A confirmed interval shorter than the governing *planning* floor or than the *planned* round structure remains exact and creates a durable audit/follow-up finding, not a new-placement hard violation; the accepted booking is reality and the plan must adapt. Recording the authoritative interval is not an approval of its format, so even an operationally unusable accepted interval is recorded exactly and its playing-time concern stays a separate durable finding/action. A genuine overlap and ordinary unverified proposed below-floor placements remain hard-invalid without their separately authorized scoped exception. Planning minimums still constrain empty-slot proposals strictly; only an exact, fact-matching accepted booking may be reclassified.
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

## Implementation evidence

Each scenario in the required regression contract maps to a test that locks the behaviour at its owner boundary. The mapping was checked against the code in #821; it does not claim that one fixture covers every row.

| Contract scenario | Evidence (file::test) |
| --- | --- |
| Exact short booking through confirmation, reload, findings, audit, export, preflight and seal | `tests/test_published_season_sealing.py::test_confirmed_short_booking_agrees_across_findings_audit_export_preflight_and_seal` (hermetic, one reloaded state) |
| Accepted short interval stays a warning, not a hard violation | `tests/test_final_verification.py::test_accepted_short_booking_with_valid_schedule_is_publishable_with_warning_only` |
| Stage 4 export rebuilds the canonical effective projection | `tests/test_approval_lifecycle.py::test_below_floor_booking_stage4_export_builder_stays_hard_valid` |
| Semantic audit uses the accepted duration | `tests/test_audit_context.py::test_semantic_audit_uses_host_confirmed_duration_override_for_tournament` |
| Unverified below-floor proposal stays hard | `tests/test_final_verification.py::test_unverified_below_floor_override_remains_hard_invalid` |
| Stale or wrong owner evidence is not authority | `tests/test_final_verification.py::test_stale_or_wrong_source_authority_does_not_reclassify_below_floor`; `tests/test_approval_lifecycle.py::test_stale_calendar_booking_association_fails_closed_when_tournament_moves`; `tests/test_club_booking_source.py::test_wrong_calendar_owner_retains_exact_rejected_source_fact` |
| Rejected calendar association is not publication authority | `tests/test_final_verification.py::test_rejected_calendar_association_is_not_publication_authority` |
| Moved-day team double-booked or unavailable is rejected, evidence kept | `tests/test_club_booking_source.py::test_source_confirmed_moved_day_double_booked_team_is_rejected_and_evidence_retained`; `tests/test_club_booking_source.py::test_moved_day_unavailable_team_rejects_atomically_then_resolves_on_retry` |
| True arena overlap stays hard | `tests/test_final_verification.py::test_accepted_short_booking_does_not_mask_real_arena_overlap`; `tests/test_final_verification.py::test_arena_collision_uses_exact_accepted_interval_at_the_boundary` |
| Actual playing shortfall stays hard | `tests/test_final_verification.py::test_accepted_short_booking_does_not_mask_actual_playing_shortfall` |
| Host busy conflict is surfaced, not silently cleared | `tests/test_final_verification.py::test_host_busy_conflict_beside_accepted_booking_is_unresolved_not_silently_clear` |
| Rejected operation leaves no partial mutation | `tests/test_approval_lifecycle.py::test_confirm_calendar_booking_refuses_cancelled_tournament_atomically`; `tests/test_published_season_sealing.py::test_refused_seal_after_canonical_change_leaves_no_partial_mutation` |
| Retained conflicting evidence is refused at seal | `tests/test_published_season_sealing.py::test_replacement_seal_rejects_conflicting_retained_evidence` |
| Verification bound to the canonical revision | `tests/test_published_season_sealing.py::test_seal_refuses_verified_revision_superseded_by_later_canonical_change`; `tests/test_published_season_sealing.py::test_export_verified_before_decision_only_change_cannot_seal_changed_state` |

### Known gaps

- **Failed evidence write after seal.** The seal commits before the publication evidence record is written. A failed write leaves the season sealed without its evidence record. This is the intended recoverable design (#469), and rule 7 above does not require a rollback. Detection and the rerun path are tracked in #818.
- **Copied-season dry run.** No run of the full publication path against a copied season yet. Tracked in #820.

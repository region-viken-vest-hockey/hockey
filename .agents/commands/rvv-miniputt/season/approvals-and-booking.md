# RVV Miniputt: season — Approval, locks and booked ice

Load from `../season.md` when routed here. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` first; the lifecycle boundary and inspection path in `../season.md` still apply.

## Approve / lock booked ice

When the operator says a tournament placement is confirmed/booked, use:

```bash
scripts/rvv-miniputt season approve \
  --season <season> \
  --tournament-id <durable-id> \
  --note "<concise operator note>"
```

Approval is never a hard-rule waiver. The repository re-verifies the current placement before approval. Approved/placement-locked tournaments are hard-preserve constraints for later planning.

If an approval is reported as `stale_approval`, do not silently keep or recreate it. Inspect why the protected fields changed and require explicit reapproval after the intended current state is confirmed.

### Manual club booking/rejection when no calendar can resolve it

A blocked/empty calendar, a generic overlap, or an explicit club email that its bookings are made but not reflected in the public calendar is a normal source, not an emergency. Do **not** regenerate, reopen or replan the published season because a scrape failed; record the operator-accepted conclusion directly:

```bash
scripts/rvv-miniputt season booking-set --season <season> --tournament-id <id> --status booked \
  --reference "<email id/date/sender>" --note "<concise source summary>"
scripts/rvv-miniputt season booking-set --season <season> --tournament-id <id> --status not-booked \
  --note "club rejected the assigned slot"
scripts/rvv-miniputt season booking-clear --season <season> --tournament-id <id> --note "recorded against the wrong id"
```

```bash
scripts/rvv-miniputt season booking-source-set --season <season> --club <club> \
  --source-document "changes_and_confirmations/<season>/<club>.xlsx" \
  --source-version "<reviewed version/date>" \
  --reference "<email id/date/sender>" --note "<source summary>"
scripts/rvv-miniputt season booking-set --season <season> --tournament-id <id> --status booked \
  --source-scope club_wide_interpretation --source-assertion-id <club-booking-source-id> \
  --reference "<email id/date/sender>" --note "<concise source summary>"
scripts/rvv-miniputt season booking-sources --season <season> [--club <club>] --json
scripts/rvv-miniputt season booking-set --season <season> --tournament-id <id> --status not-booked \
  --note "club rejected the assigned slot"
scripts/rvv-miniputt season booking-clear --season <season> --tournament-id <id> --note "recorded against the wrong id"
```

When a club supplies one booking list covering many home tournaments, record the original source document first with `booking-source-set` and link each deliberate per-tournament interpretation to it with `--source-assertion-id`, so the club scope and the reviewed source version survive even after individual IDs are re-confirmed. `booking-sources` answers the per-ID disposition question: canonical interval, booking status/authority, stale reasons, stated-vs-canonical duration follow-up, and whether the club-stated windows in one source overlap (a source whose own windows overlap is review-required until the club maps them -- never guess which ID owns which window).

This is durable, revision-bound source authority, not a scrape result: it lives in `manual_booking_assertions`, projects as `manually_booked`/`manually_not_booked` with `authority=manual_club_confirmation`, and a later `season reconcile-calendar-bookings`/`season refresh-calendars` never erases or demotes it. A contradicting calendar event surfaces as a review conflict instead of silently overwriting, and independent actionable calendar warnings (for example a stale association) stay visible as follow-up without demoting the club confirmation. A positive confirmation needs a traceable source: pass at least `--reference` or `--note`. Record the real source with `--reference`, and use `--source-scope club_wide_interpretation` when the assertion is one deliberately accepted per-tournament interpretation of a club-wide statement rather than a fabricated itemized confirmation; that scope projects as `authority=manual_club_confirmation_interpretation` (shown as `SKJØNNSVURDERT` in the plan). A booked assertion with a source-stated interval (`--stated-date` when the date changed, plus `--stated-start`/`--stated-end`) applies that actual interval canonically through the same override-backed interval writer as calendar-event confirmation; it must not leave a booked badge beside stale/default start/end times. The two times must be a same-day `HH:MM` window with end strictly after start (malformed, zero-length, reversed or overnight windows are rejected), and any below-floor format/governing concern remains visible as booking feasibility follow-up instead of stretching the real booking. `--expected-revision` fails closed on stale state. Repeating the same assertion is idempotent. After a move or other material slot change the assertion becomes `stale`: re-confirm the new slot with a fresh `--reference`/`--note` and it is replaced directly, while the old record is kept as `superseded` audit history (no `--supersede` needed); changing the conclusion about a still-current slot requires `--supersede --note`; `season booking-clear` revokes an assertion recorded in error. Rejection never cancels or deletes the tournament (it stays visible as follow-up). Recording a manual booking assertion does not itself approve/lock the placement; use `season approve` when the booking is confirmed and the placement should be protected.

### Host-confirmed per-tournament ice time

A host may confirm that one specific tournament instance really uses less (or more) ice than its age-group default -- for example two U12 tournaments sharing a two-hour window. Do not leave the inflated default in place (it makes the arena-interval conflict check refuse a legal move) and do not hand-edit canonical JSON. Record an explicit, audited override instead:

```bash
scripts/rvv-miniputt season set-ice-time-minutes --season <season> --tournament-id <id> \
  --minutes <host-confirmed-minutes> \
  --request-id <host-request-id> \
  --reference "<email id/date/sender>" \
  --note "<concise host-confirmation summary>"
scripts/rvv-miniputt season ice-time-overrides --season <season>
scripts/rvv-miniputt season clear-ice-time-minutes --season <season> --tournament-id <id> --note "window reverted"
```

This is a decision-only write in the canonical `ice_time_minutes_overrides` overlay. It never moves a tournament, changes participants or edits the age-group configuration, but it advances the canonical revision and is projected into the verifier. Arena-interval conflict verification, `confirm-calendar-booking` interval coverage and export/projection end times all resolve the same per-instance duration, so use it before retrying a move that failed with an `arena_interval_conflict` caused only by the default window. The value must satisfy the actual-round format minimum (`actual_round_count * (round_length_minutes + 5)`) and any applicable governing booking floor; a value below that is refused, and a positive confirmation requires `--reference` or `--note`. Repeating the same decision is idempotent; a different value supersedes the previous active record (kept as released audit history). `season clear-ice-time-minutes` restores the age-group default. The override is evidence of a real booked window, not a hard-rule waiver: it changes only this tournament's occupied duration, never its placement, participants or approval state.

This command stays a *planning/override* mutation and still enforces the floors. When the authoritative host calendar itself records the actual booking, reconcile it with `season confirm-calendar-booking` instead of demanding a below-floor manual override: that evidence-backed action binds the event, applies its actual start/end and booked duration atomically (moving the tournament if needed), and reports any below-floor format/governing concern as `booking_feasibility_warnings` plus the override's `minimum_minutes` rather than rejecting a correctly observed shorter window. `confirm-calendar-booking` writes the occupancy change through the same canonical override decision owner as `set-ice-time-minutes`, so sealed-season replay can prove its provenance. Use `calendar-booking-candidates` to select the match and `booking-status`/`ice-time-overrides` to review the recorded interval and its floor deviation. `confirm-calendar-booking` refuses a cancelled tournament outright and writes nothing, because a cancelled tournament is not an active placement and must not carry an approval/placement lock. Ordinary empty-slot planning and `set-ice-time-minutes` remain strict.


# Booking authority: accepted bookings and provisional manual replacement (lazy-loaded)

Load from `booking-reconciliation.md` when reconciling an accepted booking, choosing which mutation owns a changed interval, or replacing a provisional manual booking. The shared evidence steps and completion boundary in `booking-reconciliation.md` still apply.

## Reconcile accepted bookings

- Match each source assertion to exactly one durable tournament, host, arena and interval; detect duplicates, missing rows, changed dates and cancelled records. Inspect existing decisions/protections before applying.
- Use the repository-owned source registration and `season booking-set` assertion path with concrete stated date/start/end, source/version/reference and stable request identity. For an unambiguous calendar event, use the repository-owned `confirm-calendar-booking` acceptance path to adopt its **exact** interval, whether shorter or longer than the planner proposal and even when it falls below a planning minimum. Do not use a planning-floor mutation to replace an accepted booked window with a default duration. Preserve real effective occupancy; retain below-floor feasibility warnings independently.
- Check calendar association trust, venue, event coverage and **raw active ownership**, including stale active associations. Auto-link only a single unambiguous supported event; otherwise use explicit release/rebind with evidence and authorization. A cancelled tournament must not silently donate or retain a booking for another tournament. Do not invent an event or conceal a real overlap.
- Re-verify worksheet/live source -> accepted assertion -> effective canonical date/start/end/duration -> booking status -> export/rendered HTML. Export matching canonical is insufficient if canonical disagrees with accepted source facts. Recheck idempotency and affected neighboring tournaments.

### Choose the mutation owner from the source of the changed fact

Do **not** choose `booking-set` versus `confirm-calendar-booking` from the interval you want to end up with. First identify which evidence actually asserted each changed field, and preserve that source semantics in canonical provenance.

- If a worksheet/email/manual source explicitly states the accepted date/start/end, use `booking-set` with exactly those source-stated fields. `stated_interval` must be a faithful transcription of that source. Never copy a calendar observation or current canonical value into `--stated-*` merely to make the manual assertion match.
- If the manual source confirms only that the tournament is booked and states **no interval**, while one trusted unambiguous calendar event supplies the actual date/start/end, the calendar event owns the interval correction. Use `confirm-calendar-booking` for the exact event. Keep the no-window manual assertion as separate booking-existence provenance; do not re-record it merely because the effective interval changed, and never add stated fields the source did not contain.
- If an older manual source explicitly stated one interval but later accepted calendar/booker evidence establishes a different real interval, preserve the older manual statement unchanged as historical evidence and adopt the newer real interval under the newer authority. Do not rewrite or re-record the older source as though it stated the new calendar value.
- `booking-set --supersede` changes the lifecycle of a manual assertion; it does not transfer calendar facts into manual authority. Use it only when the replacement assertion is itself supported by manual/operator source evidence and its `stated_interval` still mirrors that evidence.
- Retire a conflicting club-wide manual interpretation with `confirm-calendar-booking --supersede-manual-assertion` (see below). Only when that command cannot express the case while preserving the original source statement and the newer calendar authority, stop and report a repository capability gap. Do not manufacture a replacement assertion to make status output green.
- After any correction, verify provenance as well as the interval: the intended authority must be active, ended records must remain in acceptance history, and `accepted_evidence` must point to the active record whose accepted interval equals the effective canonical interval. Also inspect the active occupancy/ice-time override that projects the accepted duration: its authority, request/source identity and minutes must agree with the evidence that actually supplied the current interval. An active calendar association beside a manual-derived override is not a clean calendar-authority transition merely because the rendered interval is correct. For event-fingerprint replacement, exactly one association for that tournament/event lineage should remain active; old fingerprints belong in released/superseded history.
- Canonical history is evidence, not a scratchpad. Dry-run before mutation. If an unmerged/local rebuild accidentally records fabricated or misattributed source facts, do not preserve that mistake by superseding it into permanent history. Discard/rebuild the candidate branch from the clean canonical base and replay only source-faithful repository-owned mutations. This rule does not erase genuine already-accepted production history; it prevents exploratory branch mistakes from becoming canonical evidence.

These rules are generic source-authority rules, not a special case for one club or season.

### Replacing a provisional manual booking

A calendar confirmation that corrects a tournament's interval does not by itself replace an earlier manual positive (`booking-set --status booked`) on the old window. Interval staleness alone does not prove that both describe the same booking, so the earlier assertion stays active and is reported as `stale` for an operator to resolve.

To replace it, name the assertion in the same confirmation and record why:

```
scripts/rvv-miniputt season confirm-calendar-booking --season <season> --event-fingerprint <fp> \
  --tournament-id <id> --supersede-manual-assertion <manual-booking-id> --note "<why this is the same booking>"
```

The command refuses, and writes nothing, when the id is not the tournament's active positive assertion, when another active negative or conflicting assertion exists, when several active positives exist, when the assertion also changed a non-interval fact (date, arena, host), or when `--note` is empty. A dry run returns the same `superseded_manual_assertions` delta without writing. A retried confirmation with the same flag does not supersede again or duplicate the manual record, and it still refuses if a conflicting assertion has since appeared. This supported atomic supersession applies to a tournament-scoped assertion and to the per-tournament record of a club-wide interpretation (#886). Superseding the latter retires only that tournament's record: the shared club source statement and the other tournaments' records stay active, and the old statement is kept as history while the calendar association becomes authoritative. Do not manufacture a replacement manual assertion or use `booking-clear` to erase the conflict. If a case still cannot be expressed through this command, report it as a capability gap.

When an already accepted calendar event changes identity/fingerprint but is established to be the same tournament booking, completion requires a single current association for that tournament booking. The prior fingerprint must become released/superseded history and the new fingerprint must own `accepted_evidence`. If `confirm-calendar-booking` cannot atomically replace the prior fingerprint, treat that as a lifecycle capability gap rather than leaving multiple raw-active associations or relying on a renderer to ignore one.

# Booking calendar audit: operator wording, audit buckets and source links (lazy-loaded)

Load from `booking-reconciliation.md` when presenting a booking-status or calendar-audit result, classifying an active tournament into an audit bucket, or citing the source link for an observation. The shared evidence steps and completion boundary in `booking-reconciliation.md` still apply.

### Operator-facing reconciliation wording

Keep machine/audit status values intact, but do not expose a conservative internal `ambiguous` label as if event identity itself were uncertain when the assessment already establishes a single strong candidate. In operator tables and summaries, prefer these meanings:

- `proposed_unchanged` / one exact or near-exact supported candidate -> **Exact match — confirmation required** (or **Likely match — confirmation required** when evidence is supportive but not exact).
- `proposed_changed_slot` / one supported shifted candidate -> **Moved booking candidate — confirmation required**, showing both canonical and observed date/time.
- multiple plausible identity candidates -> **Competing candidates — review required**.
- a wrong-age/clearly unrelated event occupying the current slot -> **Calendar conflict**, not a tournament match.
- no defensible identity candidate -> **No matching booking found** (subject to source-coverage caveats).

### Active calendar audit buckets

`season booking-status` carries one `calendar_audit` block built from the same rows as the operator tables. Each active tournament falls into exactly one bucket; cancelled tournaments are `cancelled_historical` and never active discrepancies.

Each active tournament has one `actions` entry: `none`, `refresh_source`, `optional_identity_rebind`, `explicit_booking_change_approval`, `confirm_calendar_interval` or `resolve_candidate`. None de-books an accepted booking.

A confirmed booking whose event is retitled, re-typed or gone keeps its authority and lock (booked) and is an identity review. No other event is inherited as its match. A changed venue, a conflict, or a date/start/end field that **was explicitly stated by the authoritative source** is a contradiction. When a trusted, unambiguous calendar observation differs only on interval fields the club/source did **not** explicitly state, the differing planner-derived value is not opposing authority: classify it as a proposed canonical interval correction requiring scoped acceptance, not as a material mismatch.

- `confirmed_matching`: accepted, observed interval matches. Fingerprint drift alone never moves it.
- `accepted_uncheckable`: accepted, but the calendar cannot show it (untrusted, partial or unreadable). Not a mismatch.
- `accepted_identity_review`: accepted, complete calendar, no plausible event. Review the identity; the booking stays booked.
- `material_mismatch`: accepted, an observed field contradicts an explicitly authoritative interval fact, or there is another explicit conflict. Show effective canonical and observed intervals when an observation exists.
- `proposed_interval_correction`: booked/accepted authority exists, one trusted unambiguous calendar observation differs only on interval fields that the source did not explicitly state, and the current value is planner-derived. Action is `confirm_calendar_interval`; after accepted canonical mutation, the effective interval must match the event and the row becomes `confirmed_matching`.
- `unconfirmed_ambiguous`: not booked and undecided, or booked with unresolved plausible candidates (identity unresolved, not lost).
- `no_calendar_coverage`: no usable evidence. Missing evidence is not proof of absence.
- `explicit_not_booked`: explicit rejection with no conflict flagged against it.

Reserve the word **ambiguous** for genuine identity ambiguity. When a low-level command still returns `BOOKING_AMBIGUOUS` solely because explicit `confirm-calendar-booking` authority has not yet been recorded, explain that as an authorization state in audit detail rather than making it the primary human-readable result. In operator-facing comparisons, prefer **effective canonical** over **planned** once a season is promoted; historical planner values may be shown separately as provenance.

### Source links in operator output

For every calendar event reported as an exact match, likely match, moved-booking candidate, competing candidate or blocking conflict, include the authoritative source link used for the observation in the operator-facing table or immediately adjacent detail. Prefer an evidence-provided event/deep link when one exists. Otherwise print the registered human calendar URL; for a source whose repository-owned contract supports a deterministic date-qualified view, the date-specific calendar URL may be shown as the observation link. Clearly label a generic calendar page as **Source calendar** rather than implying that it is an event-specific link. Never invent or guess an event URL that the source/evidence did not provide. If no usable URL is retained, say **Source URL unavailable in evidence** rather than omitting provenance silently.

When several rows come from the same source, the table may use a compact `Source` column and print the full URL once below the table, but a moved booking or conflict should always make the directly relevant observation link easy to identify.

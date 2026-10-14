# ADR 0007: Skien miniputt tournaments need the full ice rink

- **Status:** Accepted (operator decision)
- **Date:** 2026-10-14
- **Decision owner:** RVV hockey project (repository owner)
- **Source:** Operator confirmation in the PR #696 review thread, 2026-10-14
- **Related:** PR #696; `tournament_scheduler/booking/ice_resources.py`

## Context

Skien's BRP/Exigo calendar books its hockey ice as two separately bookable
resources, `Bane 1` and `Bane 2`. A booking can occupy one or both. The booking
assessment needs to tell whether a calendar booking on one resource could be a
miniputt tournament, or only partial occupancy of the rink.

The screenshot that motivated the resource model shows that the two banes are
separately bookable. The operator then confirmed which combination a miniputt
tournament needs (see Decision).

## Decision

For Skien, `Bane 1` and `Bane 2` together make up the rink for miniputt
availability:

- The rink is available for a miniputt tournament only when **both** Bane 1 and
  Bane 2 are free.
- If **either** Bane 1 or Bane 2 is occupied, the rink is not available for that
  miniputt tournament.

The footprint `Bane 1` + `Bane 2` is declared in the validated structured
profile `tournament_scheduler/club_profiles/skien.json` and exposed through
`tournament_scheduler.clubs.profiles`; it is authoritative for Skien. It shapes evidence
and actionability:

- A calendar event that occupies both banes covers the footprint. It gets
  `event_covers_resource_footprint` evidence and can be an actionable association
  candidate.
- An event on one bane only is partial occupancy. It is kept visible with
  `event_occupies_partial_resource_footprint` counterevidence, is not actionable,
  and blocks a negative absence finding for that interval. It is a ranking and
  blocking fact, not sufficient tournament availability.
- An event on other resources only is disjoint. It is kept visible with
  `event_occupies_other_resources_only` counterevidence and is not actionable.
- No observed booking is discarded because of the footprint. Date proximity is
  evidence only and never bounds the search.

## Consequences

- Clubs without a declared footprint keep resource-agnostic evidence.
- Changing the Skien footprint, or adding a footprint for another club, needs an
  explicit operator decision recorded in this ADR or a successor.

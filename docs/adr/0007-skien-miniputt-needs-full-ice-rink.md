# ADR 0007: Skien miniputt tournaments require the full ice rink

- **Status:** Accepted
- **Date:** 2026-10-14
- **Decision owner:** RVV hockey project (repository owner)
- **Related:** PR #696 review; `tournament_scheduler/booking/ice_resources.py`

## Context

Skien's BRP/Exigo calendar books its hockey ice as two separately bookable
resources, `Bane 1` and `Bane 2`. A booking can occupy one or both. The booking
assessment has to decide whether a calendar booking on one resource is the
booking for a miniputt tournament, or only partial occupancy of the rink.

The screenshot that motivated the resource model shows that the two banes are
separately bookable. It does not by itself say which tournaments need which
resources.

## Decision

Every miniputt tournament hosted by Skien needs the full ice surface, so it
requires both `Bane 1` and `Bane 2`. The footprint is declared in
`MINIPUTT_RESOURCE_FOOTPRINTS` and applies to all Skien miniputt tournaments.

Consequences for the booking assessment:

- A calendar event that occupies both banes covers the tournament footprint.
- An event on one bane only is partial occupancy. It is kept as visible
  counterevidence and never as a credible match, and it blocks a negative
  absence finding for that interval.
- An event on the other bane only is disjoint and is not a candidate.

## Consequences and open points

- The footprint is code-owned. Changing it requires a new decision in this
  record, not only a code change.
- Clubs without a declared footprint keep resource-agnostic evidence.

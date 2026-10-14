# ADR 0007: Skien miniputt footprint is an unconfirmed assumption

- **Status:** Proposed (assumption, not operator-confirmed)
- **Date:** 2026-10-14
- **Decision owner:** RVV hockey project (repository owner); confirmation pending
- **Related:** PR #696 review; `tournament_scheduler/booking/ice_resources.py`

## Context

Skien's BRP/Exigo calendar books its hockey ice as two separately bookable
resources, `Bane 1` and `Bane 2`. A booking can occupy one or both. The booking
assessment needs to tell whether a calendar booking on one resource could be a
miniputt tournament, or only partial occupancy of the rink.

The screenshot that motivated the resource model shows that the two banes are
separately bookable. It does not establish which tournaments need which
resources, and no operator or repository source has confirmed that every Skien
miniputt tournament needs both.

## Decision

Until the operator confirms it, the footprint `Bane 1` + `Bane 2` is treated as
an assumption declared in `MINIPUTT_RESOURCE_FOOTPRINTS`. It shapes evidence and
actionability only:

- A calendar event that occupies both banes covers the assumed footprint. It
  gets `event_covers_resource_footprint` evidence.
- An event on one bane only is partial occupancy. It is kept visible with
  `event_occupies_partial_resource_footprint` counterevidence, is not actionable,
  and blocks a negative absence finding for that interval.
- An event on other resources only is disjoint. It is kept visible with
  `event_occupies_other_resources_only` counterevidence and is not actionable.
- No observed booking is discarded because of the footprint.

## Consequences and open points

- The footprint does not gate hard compatibility. Changing it or promoting it to
  an authoritative rule requires the operator to confirm it, and then a new
  decision in this record.
- Until confirmed, the footprint should not be cited as an operator decision
  anywhere in the code or the PR.
- Clubs without a declared footprint keep resource-agnostic evidence.

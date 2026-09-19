# ADR 0004: Reserved guest slots are first-class tournament capacity

- **Status:** Accepted
- **Date:** 2026-09-16
- **Decision owner:** RVV hockey project
- **Related ADR:** ADR 0001, ADR 0002

## Context

RVV wants to reserve a small number of participant places on selected JU10/JU12
tournaments so a team from another league/region can apply to participate
later. The model had no safe representation for "a place that exists but is
deliberately not filled by an RVV team":

- leaving a tournament one team short is interpreted by the verifier as an
  avoidable underfilled/bye shape, and participant optimization/repair may fill
  it with an RVV team;
- adding a fake `Guest` team to the Stage-1 roster would incorrectly affect
  participation fairness, hosting entitlement, team counts, travel and audit
  metrics;
- reducing the global JU10/JU12 tournament size would distort every other
  tournament of that age group.

The reservation therefore has to be explicit scheduler state that counts toward
capacity/ice-time shape but never as an RVV participant, and it has to have an
auditable open → filled/released lifecycle that survives replanning.

## Decision

A reservation is a per-tournament `guest_slot` record list on canonical
tournament state (`tournament_scheduler/guest_slots.py`). Each record is one
place with a lifecycle status:

- `open` — reserved, no external team accepted yet;
- `filled` — accepted by a known external team, stored as a non-RVV `guest`
  participant with a real game schedule;
- `released` — withdrawn; no longer reserves a place.

The derived integer `reserved_guest_slots` is persisted for readability, but the
record list is authoritative; a simple payload that only carries the integer is
normalized into that many `open` records.

Consequences encoded in the canonical owners:

- **Verifier / shape** (`planning_contract.verify_candidate`,
  `final_verification.verify_final_candidate`): capacity and the no-bye shape
  are evaluated against real RVV participants plus active reservations. Byes
  produced by an *open* reservation are intentional, and games are provisional
  (missing pairs/round counts are not defects) until the place is filled or
  released. Host representation, the club-team ceiling, registration checks and
  the same-date check consider real RVV participants only.
- **Metrics** (`participation_targets`, `planning_contract` scoring,
  `serialization`): guest participants are excluded from participation,
  hosting, fairness, travel and team-count projections.
- **Participant optimization/repair**: because a reserved tournament is no
  longer reported as `bye_team_not_allowed`, the deterministic repair vocabulary
  cannot consume the reserved place. The global tournament size is unchanged.
- **Lifecycle** (`application.canonical_season_service`): only explicit
  `reserve_guest_slot` / `fill_guest_slot` / `release_guest_slot` operations
  change reservations. They re-verify the full candidate before committing,
  record a durable decision history entry, and `apply_candidate` refuses a
  replan candidate that would silently change or drop a reservation. A full
  tournament is never reduced by dropping an arbitrary participant: the caller
  names a legal displaced team from deterministic candidate facts, host
  representation must survive, and any resulting participation shortfall stays
  explicit.
- **Export/audit**: `season_plan.html` shows open places as
  "N ledige gjesteplasser" and filled guests as guest participants; the
  semantic audit carries a `guest_reservations` evidence category and the rules
  report explains capacity-vs-fairness.

The model is age-group-agnostic. The immediate production requirement is
JU10/JU12, exposed as the default candidate age groups, so enabling another age
group needs no state-model migration.

## Alternatives considered

- **Fake roster team** — rejected: distorts participation/hosting/fairness and
  registration.
- **Global smaller tournament size** — rejected: penalizes every tournament in
  the age group.
- **Implicit underfilled tournament** — rejected: indistinguishable from a
  defect and actively repaired by the optimizer.
- **Reservation only in `decisions.json`** — rejected: it changes capacity and
  games, so it belongs to schedule content and must participate in the schedule
  fingerprint and independent verification.

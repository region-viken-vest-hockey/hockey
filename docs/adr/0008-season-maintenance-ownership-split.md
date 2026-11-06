# ADR 0008: Season maintenance ownership split (domain, CP-SAT, harness)

Status: Accepted (2026-10-19). Tracked by [#715](https://github.com/region-viken-vest-hockey/hockey/issues/715); enforced by [#716](https://github.com/region-viken-vest-hockey/hockey/issues/716), [#717](https://github.com/region-viken-vest-hockey/hockey/issues/717) and [#718](https://github.com/region-viken-vest-hockey/hockey/issues/718). This records the ownership boundary and where each part is implemented.

## Context
A promoted season is the operational baseline, so day-to-day work is maintenance of existing canonical state, not global replanning. Maintenance now has several search-capable components: `SeasonPlanner` (initial construction), the fixed-skeleton CP-SAT optimizer in `stage3/cpsat.py`, the bounded local search in `repair/search_neighborhood.py`, the deterministic repair families under `repair/`, and the finding-directed controller in `season/maintenance.py`. Without an explicit split, each one can start to own rules, scoring or mutation, and the same hard rule can drift between them.

## Decision
Each kind of logic has one owner:

1. **Domain and canonical facts** own booked reality, eligibility, approvals and locks, protections, requests, withdrawals, mutation scope and authoritative verification. The independent `verify_candidate` contract in `planning/contract.py` decides hard legality. Shared rule math lives there and is not copied into solvers or repair providers.
2. **CP-SAT** owns combinatorial search over an explicitly bounded mutable neighbourhood. It may prune with hard rules, but every emitted candidate is still re-checked by `verify_candidate`. A CP-SAT model must never be the only place a hard rule is enforced.
3. **Deterministic repair providers** own cheap, local, rule-directed mutations (for example the host-team-missing, underfilled-roster and host-placement families). They run before solver search.
4. **Scoring and objective policy** live in the shared quality/Pareto modules. Solvers and providers consume them and do not define their own objectives.
5. **Harness/semantic layer** decides which problem to solve, compares and explains alternatives, escalates, and interacts with the operator. It does not verify, persist or mutate.
6. **Canonical maintenance commands are the only mutation boundary.** A candidate becomes canonical only through the atomic, full-season-verified apply path (`application.canonical_season`). Search results are candidates until then.
7. **Published seasons stay sealed.** Maintenance may change only the mutable dimensions named by the neighbourhood. It must not reopen global planning for a `published_sealed` season. Locks are scope-aware: only the dimensions that are actually locked, or that fall outside the requested mutable scope, are projected into solver constraints as fixed values, and they are not soft preferences. A placement-only lock (for example a booked tournament's date, host, arena or start time) freezes placement and nothing else. Participant locks freeze the roster. Placement-only approval must not freeze participants. Dimensions that are not locked and are inside the mutable scope remain editable.

## Classification of current logic
| Area | Classification |
|---|---|
| `planning/contract.py` `verify_candidate`, shared rule math | Domain semantics / canonical facts |
| `application/canonical_season` apply, promote, lock, approval | Canonical facts and deterministic mutation |
| `repair/host_team_missing.py`, `underfilled_roster.py`, `host_placement.py` | Deterministic mutation (local families) |
| `stage3/cpsat.py` and `stage3/cpsat_*.py` | Combinatorial search over a fixed skeleton |
| `repair/search_neighborhood.py` | Bounded local search over an explicit neighbourhood |
| `planning/season_planner.py` | Initial construction (domain-specific); not a maintenance owner |
| `season/maintenance.py` | Finding-directed controller; composes providers, owns no rule |
| Choosing which finding to address, comparing alternatives, operator escalation | Semantic/operator judgement |

## Default for new combinatorial maintenance work
When a maintenance problem is genuinely combinatorial (choosing among many rosters, swaps or rotations) extend the generic bounded CP-SAT path below. Do **not** add another enumerate-and-score loop beside it. A direct deterministic mutation is still the right tool when the operator names the exact change, or when one local rule produces the single legal fix (the deterministic families in the table above); those never invoke an optimizer.

## The bounded maintenance path
| Step | Owner | Implementation |
|---|---|---|
| Project canonical state into scope and fixed values | Domain | `stage3/maintenance_problem.py` `build_participant_maintenance_problem` -> `MaintenanceProblem` (solver-neutral, JSON-safe, deterministic, bound to the baseline candidate and planning-problem fingerprints) |
| Search inside the scope | CP-SAT | `stage3/cpsat_maintenance.py` `solve_maintenance_problem`, reusing the fixed-skeleton model in `stage3/cpsat.py`; returns `MaintenanceSolveResult` (patches plus a candidate), never mutates state |
| Decide legality | Domain | `verify_candidate` re-runs on every returned candidate in the repair family; hard-invalid output is never offered |
| Decide acceptability and consequences | Maintenance gates | existing request/operational/consequence gates in `repair/` and `season/maintenance.py` |
| Apply | Canonical commands | the revision-bound repair apply path, unchanged |

Scope rules the projection enforces (and tests pin in `tests/test_cpsat_maintenance.py`):

- A participant lock or an operator pin fixes a roster. A placement-only lock does **not** fix participants, even though the planning-problem contract folds every canonical lock into `manual_adjustments.pinned_tournament_ids`; the projection subtracts placement-only locks again and the solver input overrides the pin list.
- Cancelled and undated tournaments are excluded, never searched and never counted as participation opportunity.
- Everything outside the scoped age groups is outside the problem; everything inside it but not mutable is pinned to its exact baseline roster, so untouched tournaments are byte-equal to the baseline.
- A solve is refused (`StaleMaintenanceBaseline`) unless the candidate and planning problem match the fingerprints the problem was built from.
- Outcomes distinguish `solved`, `unchanged`, `infeasible` (a proof or deterministic structural finding), `budget_exhausted` (never proof of infeasibility), `no_mutable_scope` and `unavailable` (backend missing).
- Booked versus unconfirmed tournaments is a property of the plan, not of the search. Fairness and consequence reporting (`participation/club_age_group_fairness.py`, the preview gates) keep that distinction; the solver only sees active, non-cancelled tournaments.

Objective ordering is lexicographic: tiers are solved in priority order and each optimum is fixed before the next, so a lower tier cannot trade away a higher one. Tiers, highest first: `participation` (closing a configured participation gap, with overshoot), `club_concentration` (3rd-or-later team of one club), `opponent_variety` (repeat opponents), `sibling_and_spacing` (same-club pairings, close turnaround). Hard validity, locks, protections and booked reality are constraints, not tiers.

## Hard-rule map: solver pruning versus authority
Every rule below stays authoritative in `verify_candidate` or the maintenance gates. The CP-SAT column says only whether the participant model also encodes it to prune.

| Rule | In the CP-SAT model | Authority |
|---|---|---|
| Roster size equals the effective tournament shape | yes (size fixed to baseline; shape violations reported as infeasible) | `verify_candidate`, `shape/effective_shape.py` |
| Full registered pool in fixed-cohort age groups | yes (membership fixed) | `verify_candidate`, `planning/participant_roster_sizing.py` |
| Host club represented in the roster | yes | `verify_candidate`, `hosting/representation.py` |
| No team twice on one date | yes (hard; a conflicting baseline is reported as such) | `verify_candidate` |
| Participant lock / operator pin | yes (roster fixed) | canonical lock records, `verify_canonical_locks` |
| Participation hard maximum | yes | `participation/targets.py`, `verify_candidate` |
| Participation target | goal, top objective tier | `participation/targets.py` (classification and avoidability) |
| Club hard maximum per tournament | yes (hard cap, plus the soft club-concentration tier below it) | `verify_candidate` |
| Same-club pairings no worse than baseline | yes | `verify_candidate`, scoring |
| Date, holiday, arena and host rules | not modelled; placement is never mutable here | `verify_candidate`, placement providers |
| Withdrawals, cancellations, constraints and protections | not reinterpreted; projected by the domain into the registered pool and fixed values | canonical season state |
| Host responsibility and approval state | outside the solver | canonical commands |

A rule that is not encoded for pruning can only make a returned candidate fail verification; it can never be silently weakened.

## Consequences
- New search logic goes behind the bounded CP-SAT boundary, not into a new bespoke loop.
- The participation-deviation repair family is the first family served end to end by this path. It re-staffs participants only; a request for host, date or slot movement is reported as `unserved_dimensions` rather than widening the search. Its former participant local-search loop is retired.
- Other families (host placement, coupled placement, intra-club distribution, home representation, the generic neighborhood search) are not migrated. Where their search is purely participant re-staffing they are the next candidates, each only after differential coverage shows an equal-or-better verified outcome.
- CP-SAT depends on the optional `cpsat` extra (OR-Tools). Without it the participation-deviation family reports `maintenance_search_unavailable` instead of falling back to a second implementation.

## Rejected alternatives
- A harness-local scheduler or verifier that decides legality outside `verify_candidate`.
- Letting CP-SAT or a repair provider adopt its own output without the canonical apply gate.
- Re-running global planning to fix a localized finding on a sealed season.

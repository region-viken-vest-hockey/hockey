# ADR 0008: Season maintenance ownership split (domain, CP-SAT, harness)

Status: Accepted (2026-10-19). Tracked by [#715](https://github.com/region-viken-vest-hockey/hockey/issues/715). This records the ownership boundary; code that predates it may not yet conform, and the gaps are listed under Consequences.

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

## Consequences
- New search logic goes behind the bounded CP-SAT or neighbourhood boundary, not into a new bespoke loop.
- Duplicated maintenance heuristics are candidates for retirement once CP-SAT covers them ([#718](https://github.com/region-viken-vest-hockey/hockey/issues/718)).
- Known gaps still open: duplicated search logic across providers has not yet been consolidated, and the mapping from hard rules to CP-SAT pruning constraints is not yet written down per rule. Both are tracked by #716 and #717.

## Rejected alternatives
- A harness-local scheduler or verifier that decides legality outside `verify_candidate`.
- Letting CP-SAT or a repair provider adopt its own output without the canonical apply gate.
- Re-running global planning to fix a localized finding on a sealed season.

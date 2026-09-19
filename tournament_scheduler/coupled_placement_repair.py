"""Bounded cross-age coupled placement + same-age roster repair generation.

A single canonical move changes one tournament. When the remaining defect is a
*cluster* of one club's tournaments, the smallest repair is often a coupled
exchange: move one clustered tournament into a better placement and move the
displaced tournament back, then repair only the participants that actually
become conflicted because of that placement change.

A placement-only exchange can be rejected because the displaced tournament's
current roster is already scheduled elsewhere on its new date. That is not
evidence the repair is impossible -- it only means a *roster* mutation also has
to be considered. This module owns that bounded candidate generation:

- :func:`apply_placement_swap` exchanges the named placement fields of exactly
  two tournaments in memory (identity, age group and participants stay put);
- :func:`enumerate_coupled_placement_repairs` pairs each target tournament with
  a bounded set of partners, applies the exchange, and -- when the placement
  candidate fails on a roster-shaped conflict -- searches same-age participant
  reselection for the affected tournaments using the shared movable-capacity
  roster machinery;
- every produced candidate is re-checked by the full independent verifier, and
  candidates that move hosting responsibility are rejected.

This module deliberately contains **no apply/persistence path**. The verified
candidate is returned as an option and is committed only through the existing
canonical candidate verification/apply boundary (``season apply`` /
``season apply-repair``), which owns persistence, revision binding, locks,
protections and audit history.
"""

from __future__ import annotations

import copy
from itertools import product
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .canonical_baseline import PLACEMENT_FIELDS, change_cost
from .host_team_missing_repair import (
    RepairOption,
    _identity,
    _regenerate_games,
    _same_date_identities,
    _team_ref,
    candidate_fingerprint,
)
from .hosting_responsibility import (
    hosting_responsibility_facts,
    unexplained_responsibility_transfers,
)
from .movable_capacity_repair import _roster_variants
from .operational_acceptability import (
    check_operational_acceptability,
    required_opt_in_flags,
)
from .pipeline.fingerprints import stable_payload_sha256
from .planning_contract import verify_candidate
from .team_schedule_quality import TeamIdentity, compare_team_schedule_consequence

# A repair always exchanges at least the concrete booking day/time. ``arena``
# and ``host_club`` move only when the caller explicitly asks for the full
# placement tuple, because exchanging the physical host is a hosting
# obligation transfer that needs its own audited evidence.
DEFAULT_SWAP_FIELDS: Tuple[str, ...] = ("date", "start_time")
SUPPORTED_SWAP_FIELDS: Tuple[str, ...] = tuple(PLACEMENT_FIELDS)

# Hard violation codes a same-age roster reselection can plausibly resolve.
# A placement candidate that fails for any other reason (season window, arena
# interval collision, ...) is rejected without paying for a roster search.
ROSTER_REPAIRABLE_VIOLATION_CODES = frozenset(
    {
        "duplicate_participation_same_date",
        "duplicate_team_in_tournament",
        "host_team_missing",
        "excluded_host_club_used",
        "club_hard_max_exceeded",
    }
)

# Bounds so the neighborhood stays a bounded localized repair, not a
# whole-season search.
DEFAULT_MAX_OPTIONS = 6
DEFAULT_MAX_PARTNERS = 40
DEFAULT_MAX_CANDIDATES = 200
DEFAULT_MAX_ROSTER_COMBINATIONS = 16


class CoupledPlacementRepairError(ValueError):
    """Raised when a requested coupled placement mutation cannot be expressed."""


def resolve_swap_fields(fields: Optional[Iterable[str]]) -> Tuple[str, ...]:
    """Normalize a requested placement-field exchange to canonical order."""

    requested = {str(field) for field in (fields or DEFAULT_SWAP_FIELDS)}
    unknown = sorted(requested - set(SUPPORTED_SWAP_FIELDS))
    if unknown:
        raise CoupledPlacementRepairError(
            f"Unsupported placement field(s) {unknown}; supported fields are "
            f"{list(SUPPORTED_SWAP_FIELDS)}"
        )
    resolved = tuple(field for field in SUPPORTED_SWAP_FIELDS if field in requested)
    if not resolved:
        raise CoupledPlacementRepairError(
            "A placement exchange must move at least one placement field"
        )
    if "arena" in resolved and "host_club" not in resolved:
        raise CoupledPlacementRepairError(
            "Swapping arena requires swapping host_club too: the booked arena and the "
            "physical host belong to the same placement tuple"
        )
    return resolved


def placement_tuple(tournament: Mapping[str, Any]) -> Dict[str, Any]:
    """Return every placement field for one tournament."""

    return {field: tournament.get(field) for field in SUPPORTED_SWAP_FIELDS}


def _tournaments_by_id(plan: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id")
    }


def apply_placement_swap(
    plan: Mapping[str, Any],
    tournament_a_id: str,
    tournament_b_id: str,
    *,
    fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Return a deep-copied plan with the requested placement fields exchanged.

    The two tournaments keep their durable id, age group, participants, games
    and hosting responsibility; only the named placement fields move. Host
    confirmation flags are cleared because the confirmed slot no longer applies.
    """

    if tournament_a_id == tournament_b_id:
        raise CoupledPlacementRepairError(
            "A placement exchange requires two different tournaments"
        )
    resolved_fields = resolve_swap_fields(fields)
    new_plan = copy.deepcopy(dict(plan))
    by_id = _tournaments_by_id(new_plan)
    tournament_a = by_id.get(tournament_a_id)
    tournament_b = by_id.get(tournament_b_id)
    if tournament_a is None:
        raise CoupledPlacementRepairError(
            f"Unknown tournament id in plan: {tournament_a_id}"
        )
    if tournament_b is None:
        raise CoupledPlacementRepairError(
            f"Unknown tournament id in plan: {tournament_b_id}"
        )
    for tournament_id, tournament in (
        (tournament_a_id, tournament_a),
        (tournament_b_id, tournament_b),
    ):
        if tournament.get("cancelled"):
            raise CoupledPlacementRepairError(
                f"Tournament {tournament_id} is cancelled and cannot participate in a "
                "placement exchange"
            )

    for field in resolved_fields:
        old_a = tournament_a.get(field)
        old_b = tournament_b.get(field)
        tournament_a[field] = old_b
        tournament_b[field] = old_a

    for tournament in (tournament_a, tournament_b):
        tournament.pop("requires_host_confirmation", None)
        tournament.pop("host_confirmation_reason", None)
    return new_plan


def affected_team_identities(
    plan: Mapping[str, Any],
    tournament_a_id: str,
    tournament_b_id: str,
) -> List[TeamIdentity]:
    """Return the distinct RVV participants of both exchanged tournaments."""

    by_id = _tournaments_by_id(plan)
    identities: Dict[TeamIdentity, None] = {}
    for tournament_id in (tournament_a_id, tournament_b_id):
        tournament = by_id.get(tournament_id)
        if tournament is None:
            continue
        age_group = str(tournament.get("age_group") or "")
        for team in tournament.get("teams", []) or []:
            if bool(team.get("guest", False)):
                continue
            identities[
                (
                    str(team.get("club") or ""),
                    str(team.get("label") or ""),
                    str(team.get("age_group") or age_group),
                )
            ] = None
    return list(identities)


def placement_swap_consequences(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    tournament_a_id: str,
    tournament_b_id: str,
    *,
    problem: Optional[Mapping[str, Any]] = None,
    max_teams: int = 8,
    focus_team: Optional[TeamIdentity] = None,
) -> Dict[str, Any]:
    """Deterministic before/after schedule consequences for the affected teams."""

    identities = affected_team_identities(before_plan, tournament_a_id, tournament_b_id)
    ordered: List[TeamIdentity] = []
    if focus_team is not None and focus_team in identities:
        ordered.append(focus_team)
    for identity in identities:
        if identity not in ordered:
            ordered.append(identity)
    measured = ordered[: max(1, int(max_teams))]

    team_consequences: Dict[str, Any] = {}
    for identity in measured:
        key = f"{identity[0]}|{identity[1]}|{identity[2]}"
        team_consequences[key] = {
            "team": {"club": identity[0], "label": identity[1], "age_group": identity[2]},
            **compare_team_schedule_consequence(
                before_plan, after_plan, identity, problem=problem
            ),
        }

    before_by_id = _tournaments_by_id(before_plan)
    after_by_id = _tournaments_by_id(after_plan)
    return {
        "placements": {
            tournament_a_id: {
                "before": placement_tuple(before_by_id.get(tournament_a_id) or {}),
                "after": placement_tuple(after_by_id.get(tournament_a_id) or {}),
            },
            tournament_b_id: {
                "before": placement_tuple(before_by_id.get(tournament_b_id) or {}),
                "after": placement_tuple(after_by_id.get(tournament_b_id) or {}),
            },
        },
        "affected_teams": [
            {"club": identity[0], "label": identity[1], "age_group": identity[2]}
            for identity in ordered
        ],
        "team_consequences": team_consequences,
        "team_consequence_count": len(team_consequences),
        "consequence_acceptable": all(
            analysis.get("acceptable", False) for analysis in team_consequences.values()
        ),
    }


def _parse_date(value: Any):
    from datetime import date as _date

    if isinstance(value, _date):
        return value
    try:
        return _date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _violation_codes(verification: Mapping[str, Any]) -> List[str]:
    return [
        str(violation.get("code") or "hard_violation")
        for violation in verification.get("violations") or []
    ]


def _placement_only_reason(verification: Mapping[str, Any]) -> Optional[str]:
    codes = set(_violation_codes(verification))
    if codes and codes <= ROSTER_REPAIRABLE_VIOLATION_CODES:
        return None
    return "placement_conflict_not_roster_repairable"


def _conflicting_tournament_ids(
    placement_candidate: Mapping[str, Any],
    tournament_ids: Sequence[str],
) -> List[str]:
    """Moved tournaments whose roster is already busy on its new date."""

    by_id = _tournaments_by_id(placement_candidate)
    conflicting: List[str] = []
    for tournament_id in tournament_ids:
        tournament = by_id.get(tournament_id)
        if tournament is None:
            continue
        tournament_date = _parse_date(tournament.get("date"))
        if tournament_date is None:
            continue
        occupied = _same_date_identities(
            placement_candidate,
            tournament_date,
            except_tournament_id=tournament_id,
        )
        identities = {_identity(team) for team in tournament.get("teams", []) or []}
        if identities & occupied:
            conflicting.append(tournament_id)
    return conflicting


def _roster_variants_for_tournaments(
    placement_candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    conflicting_ids: Sequence[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """Bounded same-age roster variants per conflicting tournament."""

    by_id = _tournaments_by_id(placement_candidate)
    variants_by_tournament: Dict[str, List[Dict[str, Any]]] = {}
    rejections: List[Dict[str, Any]] = []
    for tournament_id in conflicting_ids:
        tournament = by_id.get(tournament_id)
        if tournament is None:
            continue
        tournament_date = _parse_date(tournament.get("date"))
        occupied = (
            _same_date_identities(
                placement_candidate,
                tournament_date,
                except_tournament_id=tournament_id,
            )
            if tournament_date is not None
            else set()
        )
        host = str(tournament.get("host_club") or "")
        variants, rejected = _roster_variants(
            placement_candidate,
            problem,
            tournament,
            occupied,
            host,
        )
        variants_by_tournament[tournament_id] = [
            {
                "teams": [dict(team) for team in roster],
                "removed": [_team_ref(team) for team in removed],
                "added": [_team_ref(team) for team in added],
            }
            for roster, removed, added in variants
        ]
        for rejection in rejected:
            rejections.append({"tournament_id": tournament_id, **dict(rejection)})
    return variants_by_tournament, rejections


def _roster_repaired_candidate(
    placement_candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    variants_by_tournament: Mapping[str, List[Dict[str, Any]]],
    *,
    max_combinations: int,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Apply bounded same-age roster variants and return the first verified candidate.

    Returns the verified trial plus the concrete variant chosen for each
    repaired tournament (empty when no variant verified).
    """

    tournament_ids = [
        tournament_id
        for tournament_id, variants in variants_by_tournament.items()
        if variants
    ]
    if not tournament_ids:
        return None, {}
    variant_lists = [variants_by_tournament[tournament_id] for tournament_id in tournament_ids]
    examined = 0
    for combination in product(*variant_lists):
        examined += 1
        if examined > max(1, int(max_combinations)):
            break
        trial = copy.deepcopy(dict(placement_candidate))
        by_id = _tournaments_by_id(trial)
        applied: Dict[str, Dict[str, Any]] = {}
        for tournament_id, variant in zip(tournament_ids, combination):
            tournament = by_id[tournament_id]
            tournament["teams"] = [dict(team) for team in variant["teams"]]
            _regenerate_games(tournament, problem)
            applied[tournament_id] = variant
        if verify_candidate(dict(trial), dict(problem)).get("ok", True):
            return trial, applied
    return None, {}


def _option_id(arguments: Mapping[str, Any]) -> str:
    digest = stable_payload_sha256(dict(arguments))[:12]
    return (
        f"coupled_placement:{digest}:"
        f"{arguments.get('tournament_a_id')}|{arguments.get('tournament_b_id')}|"
        f"{','.join(arguments.get('fields') or [])}"
    )


def _build_coupled_option(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    tournament_a_id: str,
    tournament_b_id: str,
    fields: Tuple[str, ...],
    finding_id: str,
    focus_team: Optional[TeamIdentity],
    baseline: Optional[Mapping[str, Any]],
    max_roster_combinations: int,
    before_verification: Optional[Mapping[str, Any]] = None,
) -> Tuple[Optional[RepairOption], Dict[str, Any]]:
    """Build, roster-repair and verify one coupled placement candidate.

    Returns ``(option, rejection)`` where exactly one is populated. A candidate
    that is hard-valid but newly introduces fixed-busy/manual placement work or
    a host-confirmation dependency is classified with deterministic evidence
    (``operational_acceptable: false`` / ``requires_operational_opt_in``) and
    kept out of the auto-applicable Pareto set rather than presented as an
    ordinary automatic repair.
    """

    placement_candidate = apply_placement_swap(
        plan, tournament_a_id, tournament_b_id, fields=fields
    )
    placement_verification = verify_candidate(dict(placement_candidate), dict(problem))
    placement_ok = bool(placement_verification.get("ok", True))
    roster_repaired_ids: List[str] = []
    roster_rejections: List[Dict[str, Any]] = []
    applied_variants: Dict[str, Dict[str, Any]] = {}
    candidate: Mapping[str, Any] = placement_candidate

    if not placement_ok:
        reason = _placement_only_reason(placement_verification)
        if reason is not None:
            return None, {
                "finding_id": finding_id,
                "tournament_ids": [tournament_a_id, tournament_b_id],
                "reason": reason,
                "placement_violations": _violation_codes(placement_verification),
                "roster_repair_attempted": False,
            }
        conflicting_ids = _conflicting_tournament_ids(
            placement_candidate, (tournament_a_id, tournament_b_id)
        )
        if not conflicting_ids or not problem:
            return None, {
                "finding_id": finding_id,
                "tournament_ids": [tournament_a_id, tournament_b_id],
                "reason": "no_roster_conflict_to_repair",
                "placement_violations": _violation_codes(placement_verification),
                "roster_repair_attempted": bool(conflicting_ids),
            }
        variants_by_tournament, roster_rejections = _roster_variants_for_tournaments(
            placement_candidate, problem, conflicting_ids
        )
        repaired, applied_variants = _roster_repaired_candidate(
            placement_candidate,
            problem,
            variants_by_tournament,
            max_combinations=max_roster_combinations,
        )
        if repaired is None:
            return None, {
                "finding_id": finding_id,
                "tournament_ids": [tournament_a_id, tournament_b_id],
                "reason": "no_verified_coupled_roster_repair",
                "placement_violations": _violation_codes(placement_verification),
                "roster_repair_attempted": True,
                "roster_repair_tournament_ids": conflicting_ids,
                "roster_rejections": roster_rejections,
            }
        candidate = repaired
        roster_repaired_ids = conflicting_ids

    # The candidate is hard-valid, but "hard-valid" is not "acceptable as an
    # ordinary automatic repair". A candidate that is verified only because a
    # new external-calendar collision is represented as manual work (or that
    # newly depends on host confirmation) is classified explicitly here -- with
    # deterministic evidence -- and kept out of the auto-applicable Pareto set
    # by ``season_maintenance`` unless the operator opts in.
    operational_acceptability: Optional[Dict[str, Any]] = None
    if problem:
        final_verification = (
            placement_verification
            if not roster_repaired_ids
            else verify_candidate(dict(candidate), dict(problem))
        )
        operational_acceptability = check_operational_acceptability(
            plan,
            before_verification if before_verification is not None else verify_candidate(dict(plan), dict(problem)),
            candidate,
            final_verification,
        )

    total_roster_replacements = sum(
        len(variant.get("removed") or []) for variant in applied_variants.values()
    )
    if problem:
        transfers = unexplained_responsibility_transfers(plan, candidate, problem)
        if transfers:
            return None, {
                "finding_id": finding_id,
                "tournament_ids": [tournament_a_id, tournament_b_id],
                "reason": "unexplained_hosting_responsibility_transfer",
                "transfers": transfers,
                "roster_repair_attempted": bool(roster_repaired_ids),
            }

    consequences = placement_swap_consequences(
        plan,
        candidate,
        tournament_a_id,
        tournament_b_id,
        problem=problem,
        focus_team=focus_team,
    )
    focus_analysis: Optional[Mapping[str, Any]] = None
    if focus_team is not None:
        key = f"{focus_team[0]}|{focus_team[1]}|{focus_team[2]}"
        focus_analysis = consequences["team_consequences"].get(key)
        if focus_analysis is not None:
            before_gap = (focus_analysis.get("before") or {}).get("spacing", {}).get(
                "min_gap_days"
            )
            after_gap = (focus_analysis.get("after") or {}).get("spacing", {}).get(
                "min_gap_days"
            )
            if (
                isinstance(before_gap, int)
                and isinstance(after_gap, int)
                and after_gap <= before_gap
            ):
                # The exchange verifies but leaves the finding's own defect
                # untouched, so it is not a repair for this finding.
                return None, {
                    "finding_id": finding_id,
                    "tournament_ids": [tournament_a_id, tournament_b_id],
                    "reason": "no_focus_team_improvement",
                    "min_gap_before": before_gap,
                    "min_gap_after": after_gap,
                    "roster_repair_attempted": bool(roster_repaired_ids),
                }
    hosted_age_groups = {
        str((_tournaments_by_id(plan).get(tournament_id) or {}).get("age_group") or "")
        for tournament_id in (tournament_a_id, tournament_b_id)
    }
    hosted_age_groups.discard("")
    hosting_evidence: Dict[str, Any] = {
        "preserved": True,
        "age_groups": sorted(hosted_age_groups),
        "transfers": [],
    }
    if problem:
        hosting_evidence["before"] = [
            row
            for row in hosting_responsibility_facts(problem, plan)
            if str(row.get("age_group") or "") in hosted_age_groups
        ]
        hosting_evidence["after"] = [
            row
            for row in hosting_responsibility_facts(problem, candidate)
            if str(row.get("age_group") or "") in hosted_age_groups
        ]
    cost = change_cost(dict(baseline) if baseline is not None else dict(plan), candidate)
    arguments = {
        "tournament_a_id": tournament_a_id,
        "tournament_b_id": tournament_b_id,
        "fields": list(fields),
        "roster_changes": {
            tournament_id: {
                "teams": [dict(team) for team in variant["teams"]],
                "removed": list(variant.get("removed") or []),
                "added": list(variant.get("added") or []),
            }
            for tournament_id, variant in applied_variants.items()
        },
    }
    focus_summary: Dict[str, Any] = {}
    if focus_analysis is not None:
        focus_summary = {
            "team": focus_analysis.get("team"),
            "min_gap_before": (focus_analysis.get("before") or {})
            .get("spacing", {})
            .get("min_gap_days"),
            "min_gap_after": (focus_analysis.get("after") or {})
            .get("spacing", {})
            .get("min_gap_days"),
            "acceptable": focus_analysis.get("acceptable"),
        }
    effects = {
        "swapped_tournament_ids": [tournament_a_id, tournament_b_id],
        "changed_tournament_count": 2,
        "fields": list(fields),
        "placement_only_verified": placement_ok,
        "roster_repair_applied": bool(roster_repaired_ids),
        "roster_repair_tournament_ids": roster_repaired_ids,
        "participant_replacements": total_roster_replacements,
        "change_cost_total": cost.get("total"),
        "focus_team": focus_summary or None,
        "consequence_acceptable": consequences["consequence_acceptable"],
    }
    evidence: Dict[str, Any] = {
        "verification_ok": True,
        "placement_only_verification": placement_verification,
        "change_cost": cost,
        "consequences": consequences,
        "hosting": hosting_evidence,
        "roster_rejections": roster_rejections,
    }
    if operational_acceptability is not None:
        effects["operational_acceptable"] = bool(operational_acceptability["ok"])
        effects["requires_operational_opt_in"] = required_opt_in_flags(
            operational_acceptability
        )
        evidence["operational_acceptability"] = operational_acceptability
    option = RepairOption(
        option_id=_option_id(arguments),
        finding_id=finding_id,
        action="coupled_placement_repair",
        tournament_id=tournament_a_id,
        arguments=arguments,
        hard_feasible=True,
        effects=effects,
        evidence=evidence,
    )
    return option, {}


def enumerate_coupled_placement_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    target_tournament_ids: Optional[Iterable[str]] = None,
    focus_team: Optional[TeamIdentity] = None,
    fields: Optional[Iterable[str]] = None,
    finding_id: str = "",
    baseline: Optional[Mapping[str, Any]] = None,
    max_options: int = DEFAULT_MAX_OPTIONS,
    max_partners: int = DEFAULT_MAX_PARTNERS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_roster_combinations: int = DEFAULT_MAX_ROSTER_COMBINATIONS,
    run_id: str = "",
) -> Dict[str, Any]:
    """Enumerate verified coupled placement + roster repair candidates.

    With no explicit target the provider returns no options: a whole-season
    O(tournaments^2) enumeration is not a bounded localized repair, so callers
    must scope the search to a finding.
    """

    resolved_fields = resolve_swap_fields(fields)
    plan = dict(candidate)
    targets = [str(item) for item in (target_tournament_ids or []) if str(item)]
    targets = list(dict.fromkeys(targets))
    if not targets:
        return {
            "options": [],
            "rejected_candidates": [],
            "skipped": "no_target_tournament",
            "families": {
                "coupled_placement": {
                    "option_count": 0,
                    "rejected_count": 0,
                    "skipped": "no_target_tournament",
                }
            },
        }

    by_id = _tournaments_by_id(plan)
    scheduled = [
        tournament
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id") and not tournament.get("cancelled")
    ]
    scheduled_ids = {str(tournament.get("id") or "") for tournament in scheduled}

    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_pairs: set[Tuple[str, str]] = set()
    # The non-regression baseline is the same for every candidate in this
    # enumeration, so it is verified once rather than per candidate.
    before_verification = verify_candidate(dict(plan), dict(problem))
    attempts = 0
    for target_id in targets:
        if target_id not in by_id or target_id not in scheduled_ids:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": target_id,
                    "reason": "unknown_target_tournament",
                }
            )
            continue
        partners = [str(tournament.get("id") or "") for tournament in scheduled]
        partners = [partner for partner in partners if partner != target_id]
        target_date = str(by_id[target_id].get("date") or "")
        partners.sort(
            key=lambda partner: (
                _date_distance(str(by_id[partner].get("date") or ""), target_date),
                str(by_id[partner].get("date") or ""),
                partner,
            )
        )
        for partner in partners[: max(1, int(max_partners))]:
            pair = tuple(sorted((target_id, partner)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if attempts >= max(1, int(max_candidates)):
                break
            attempts += 1
            option, rejection = _build_coupled_option(
                plan,
                problem,
                tournament_a_id=target_id,
                tournament_b_id=partner,
                fields=resolved_fields,
                finding_id=finding_id,
                focus_team=focus_team,
                baseline=baseline,
                max_roster_combinations=max_roster_combinations,
                before_verification=before_verification,
            )
            if option is None:
                rejected.append(rejection)
                continue
            if option.option_id in seen_ids:
                continue
            seen_ids.add(option.option_id)
            options.append(option)
        if attempts >= max(1, int(max_candidates)):
            break

    options.sort(key=_option_rank)
    ranked = options[: max(1, int(max_options))]
    for index, option in enumerate(ranked, start=1):
        option.effects["rank"] = index

    return {
        "options": [option.to_dict() for option in ranked],
        "rejected_candidates": rejected,
        "candidate_fingerprint": candidate_fingerprint(plan),
        "verified_candidate_count": len(options),
        "families": {
            "coupled_placement": {
                "option_count": len(ranked),
                "rejected_count": len(rejected),
            }
        },
        "run_id": run_id,
    }


def _option_rank(option: RepairOption) -> Tuple[int, float, int, int, str]:
    """Prefer automatically acceptable repairs, then the smallest change.

    A candidate that requires an explicit operator opt-in is ranked after every
    automatically acceptable candidate so it can never crowd a normal repair
    out of the bounded option set (it is still classified and returned for a
    deliberate opt-in apply).
    """

    effects = option.effects or {}
    requires_opt_in = 1 if effects.get("requires_operational_opt_in") else 0
    cost = effects.get("change_cost_total")
    try:
        cost_value = float(cost)
    except (TypeError, ValueError):
        cost_value = float("inf")
    focus = effects.get("focus_team") or {}
    min_gap_after = focus.get("min_gap_after")
    if min_gap_after is None:
        min_gap_after = -1
    before = focus.get("min_gap_before")
    improvement = 0
    if isinstance(min_gap_after, int) and isinstance(before, int):
        improvement = min_gap_after - before
    return (requires_opt_in, cost_value, -improvement, -int(min_gap_after), option.option_id)


def _date_distance(left: str, right: str) -> int:
    from datetime import date as _date

    try:
        return abs((_date.fromisoformat(left) - _date.fromisoformat(right)).days)
    except (TypeError, ValueError):
        return 10_000


def apply_coupled_placement_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
    scope: Optional[Mapping[str, Any]] = None,
    dimensions: Optional[Iterable[str]] = None,
    arguments: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Atomically rebuild and verify one previously enumerated coupled option.

    No persistence happens here: the caller (``season apply-repair``) commits
    the returned candidate through the existing canonical apply boundary.
    """

    fingerprint = candidate_fingerprint(candidate)
    if fingerprint != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": fingerprint,
            "expected_fingerprint": expected_fingerprint,
        }
    if not isinstance(arguments, Mapping) or not arguments:
        return {"ok": False, "reason": "unknown_or_stale_option", "option_id": option_id}
    tournament_a_id = str(arguments.get("tournament_a_id") or "")
    tournament_b_id = str(arguments.get("tournament_b_id") or "")
    fields = tuple(str(field) for field in (arguments.get("fields") or []))
    if not tournament_a_id or not tournament_b_id or not fields:
        return {"ok": False, "reason": "unknown_or_stale_option", "option_id": option_id}
    try:
        trial = apply_placement_swap(
            candidate, tournament_a_id, tournament_b_id, fields=fields
        )
    except CoupledPlacementRepairError as exc:
        return {"ok": False, "reason": "invalid_placement_exchange", "message": str(exc)}

    roster_changes = arguments.get("roster_changes") or {}
    if isinstance(roster_changes, Mapping):
        by_id = _tournaments_by_id(trial)
        for tournament_id, change in roster_changes.items():
            tournament = by_id.get(str(tournament_id))
            if tournament is None:
                return {
                    "ok": False,
                    "reason": "unknown_tournament_in_option",
                    "tournament_id": str(tournament_id),
                }
            teams = (change or {}).get("teams") if isinstance(change, Mapping) else None
            if not isinstance(teams, list) or not teams:
                return {
                    "ok": False,
                    "reason": "invalid_roster_change",
                    "tournament_id": str(tournament_id),
                }
            tournament["teams"] = [dict(team) for team in teams]
            _regenerate_games(tournament, problem)

    verification = verify_candidate(dict(trial), dict(problem))
    if not verification.get("ok", True):
        return {
            "ok": False,
            "reason": "verification_failed",
            "verification": verification,
        }
    if problem:
        transfers = unexplained_responsibility_transfers(candidate, trial, problem)
        if transfers:
            return {
                "ok": False,
                "reason": "unexplained_hosting_responsibility_transfer",
                "transfers": transfers,
            }
    return {
        "ok": True,
        "option_id": option_id,
        "candidate": trial,
        "verification": verification,
    }


__all__ = [
    "CoupledPlacementRepairError",
    "DEFAULT_SWAP_FIELDS",
    "ROSTER_REPAIRABLE_VIOLATION_CODES",
    "SUPPORTED_SWAP_FIELDS",
    "affected_team_identities",
    "apply_coupled_placement_repair_option",
    "apply_placement_swap",
    "enumerate_coupled_placement_repairs",
    "placement_swap_consequences",
    "placement_tuple",
    "resolve_swap_fields",
]

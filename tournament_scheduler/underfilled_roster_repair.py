"""Deterministic repair options for locally underfilled tournament rosters.

This is the second planner-neutral repair-option capability (after
``host_team_missing_repair``). When independent verification reports
``bye_team_not_allowed`` for a tournament whose materialized participant count
is smaller than the independently derived ``effective_team_count`` -- even
though the canonical registered pool can support the effective shape -- Python
enumerates concrete fill/swap repairs. Applying a selected option mutates a
copy, regenerates games for every affected tournament and reruns the
independent verifier; the candidate is committed only when verification passes.

The harness/controller chooses which legal option to apply: this module never
decides how much repair policy to run. Every registered team that could fill
the tournament is either turned into a hard-feasible option or recorded in
``rejected_candidates`` with an explicit reason (``already_plays_same_date``,
``at_participation_max``, ``incompatible_half_target``, the canonical
verifier's own code, ...), so no legal option is hidden by an ad-hoc ranking
policy and no illegal option is exposed.

Scope boundary: this module owns *participant* repair for a local size defect.
It deliberately does not move dates, hosts or arenas -- those findings have
their own providers. When the missing slot cannot be filled from the canonical
pool (e.g. the whole registered pool is smaller than the effective shape) the
finding is reported as ``registered_pool_too_small`` rather than guessed at.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .application.decisions import DecisionContext
from .effective_tournament_shape import compute_effective_tournament_shape
from .host_team_missing_repair import (
    RepairOption,
    _codes,
    _count_code,
    _find_tournament,
    _identity,
    _participation_counts,
    _participation_limit_reason,
    _participations_by_half,
    _primary_violation_reason,
    _regenerate_games,
    _same_date_identities,
    _slug,
    _team_ref,
    candidate_fingerprint,
)
from .planning_contract import (
    HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT,
    _parse_date,
    verify_candidate,
)

# A bounded local neighborhood: enough verified swap alternatives for the
# controller to choose from, without turning a local size defect into a
# season-wide search.
_MAX_SWAP_OPTIONS = 5


@dataclass(frozen=True)
class _RosterShape:
    age_group: str
    effective_team_count: int
    actual_team_count: int
    missing_slots: int
    input_constrained: bool


def enumerate_underfilled_roster_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
) -> Dict[str, Any]:
    """Return legal fill/swap options and rejected evidence for underfilled rosters."""
    verification = verify_candidate(dict(candidate), dict(problem))
    findings = [
        violation
        for violation in verification.get("violations", [])
        if violation.get("code") == "bye_team_not_allowed"
    ]
    fingerprint = candidate_fingerprint(candidate)
    pinned_ids = {
        str(item) for item in (problem.get("manual_adjustments") or {}).get("pinned_tournament_ids", [])
    }
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        tournament_id = str(finding.get("tournament_id") or "")
        finding_id = f"bye_team_not_allowed:{tournament_id or index}"
        tournament = _find_tournament(candidate, tournament_id)
        if tournament is None:
            rejected.append({"finding_id": finding_id, "reason": "tournament_not_found"})
            continue
        shape = _roster_shape(problem, tournament)
        if shape is None:
            # No single-instance size target for this age group: an even
            # subset is fully legal, so there is nothing to fill here.
            continue
        if shape.missing_slots <= 0:
            # Not a fill deficit (e.g. an odd / leftover-bye shape). Other
            # deterministic repairs own that case; do not invent a fill.
            continue
        if shape.input_constrained:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": tournament.get("id"),
                    "age_group": shape.age_group,
                    "reason": "registered_pool_too_small",
                    "registered_effective_team_count": shape.effective_team_count,
                    "missing_slots": shape.missing_slots,
                }
            )
            continue
        if str(tournament.get("id")) in pinned_ids:
            for team in _registered_same_age(problem, shape.age_group):
                rejected.append(
                    {
                        **_option_base(finding_id, tournament, team),
                        "reason": "manual_restriction_forbids_mutation",
                    }
                )
            continue
        fill_options, fill_rejected = _fill_options(
            candidate, problem, tournament, shape, finding_id, fingerprint
        )
        options.extend(fill_options)
        rejected.extend(fill_rejected)
        # #347 repair order: a bounded same-age swap is the next local family,
        # attempted only when no direct fill is legal. Exposing it up front
        # would multiply verifier work without adding a legal option that the
        # ordered fallback would not still reach.
        if not fill_options:
            swap_options, swap_rejected = _swap_options(
                candidate, problem, tournament, shape, finding_id, fingerprint, pinned_ids
            )
            options.extend(swap_options)
            rejected.extend(swap_rejected)
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": verification,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
    }


def build_underfilled_roster_decision_context(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str,
    candidate_ref: Optional[str] = None,
) -> DecisionContext:
    repair_set = enumerate_underfilled_roster_repairs(candidate, problem, run_id=run_id)
    legal_ids = [option["option_id"] for option in repair_set["options"]]
    verification = repair_set["verification"]
    return DecisionContext(
        run_id=run_id,
        capability="underfilled_roster_repair",
        stage="stage3",
        objective=(
            "Choose one repository-generated local fill/swap option for a locally "
            "underfilled tournament, or escalate if none is legal."
        ),
        facts={
            "candidate_fingerprint": repair_set["candidate_fingerprint"],
            "repair_options": repair_set["options"],
            "rejected_candidates": repair_set["rejected_candidates"],
        },
        baseline_hard_violations=tuple(
            f"{violation.get('code')}: {violation.get('message')}"
            for violation in verification.get("violations", [])
        ),
        warnings=()
        if legal_ids
        else (
            "No legal local roster fill/swap option was found; rejected_candidates explains why.",
        ),
        candidate_ref=candidate_ref,
        available_actions=("apply_repair_option", "optimize_plan", "request_operator")
        if legal_ids
        else ("optimize_plan", "request_operator"),
        action_parameters={
            "apply_repair_option": {
                "option_id": {
                    "type": "string",
                    "enum": legal_ids,
                    "description": "Repository-generated repair option id to apply.",
                },
                "candidate_fingerprint": {
                    "type": "string",
                    "enum": [repair_set["candidate_fingerprint"]],
                },
            }
        }
        if legal_ids
        else {},
    )


def apply_underfilled_roster_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Atomically apply a selected option id and verify the resulting candidate."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    repair_set = enumerate_underfilled_roster_repairs(candidate, problem, run_id=run_id)
    option = next(
        (entry for entry in repair_set["options"] if entry["option_id"] == option_id), None
    )
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}

    mutated = copy.deepcopy(candidate)
    if option["action"] == "fill_participant":
        arguments = option["arguments"]
        if arguments.get("add_teams"):
            for team_ref in arguments["add_teams"]:
                _edit_roster(mutated, option["tournament_id"], add=team_ref, problem=problem)
        else:
            _edit_roster(
                mutated, option["tournament_id"], add=arguments["add_team"], problem=problem
            )
    elif option["action"] == "swap_participant":
        arguments = option["arguments"]
        _edit_roster(
            mutated,
            arguments["from_tournament_id"],
            remove=arguments["move_team"],
            add=arguments.get("donor_add_team"),
            problem=problem,
        )
        _edit_roster(
            mutated, option["tournament_id"], add=arguments["add_team"], problem=problem
        )
    else:
        return {"ok": False, "reason": "unsupported_action", "before_fingerprint": before}

    verification = verify_candidate(mutated, dict(problem))
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "before_fingerprint": before,
            "verification": verification,
        }
    return {
        "ok": True,
        "candidate": mutated,
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": candidate_fingerprint(mutated),
        "verification": verification,
        "effects": option.get("effects", {}),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _edit_roster(
    candidate: Mapping[str, Any],
    tournament_id: str,
    *,
    remove: Optional[Mapping[str, Any]] = None,
    add: Optional[Mapping[str, Any]] = None,
    problem: Mapping[str, Any],
) -> None:
    """Remove and/or add one participant and regenerate that tournament's games.

    ``host_team_missing_repair._mutate_participants`` always appends a team, so
    it cannot express a remove-only relocation. This is the same mutation with
    optional remove/add halves, kept local to the fill/swap family.
    """
    tournament = _find_tournament(candidate, tournament_id)
    teams = [dict(team) for team in tournament.get("teams") or []]
    if remove is not None:
        removed = _identity(remove)
        teams = [team for team in teams if _identity(team) != removed]
    if add is not None:
        teams.append(dict(add))
    tournament["teams"] = teams
    _regenerate_games(tournament, problem)


def _roster_shape(problem: Mapping[str, Any], tournament: Mapping[str, Any]) -> Optional[_RosterShape]:
    age_group = tournament.get("age_group")
    if not age_group:
        return None
    registered_count = sum(
        1 for team in problem.get("teams", []) if team.get("age_group") == age_group
    )
    shape = compute_effective_tournament_shape(
        str(age_group),
        registered_count,
        configured_rounds=(problem.get("rounds_per_tournament") or {}).get(age_group),
        parallel_game_capacity=(problem.get("parallel_games") or {}).get(age_group),
    )
    if not shape.has_explicit_target:
        return None
    actual = len(tournament.get("teams") or [])
    return _RosterShape(
        age_group=str(age_group),
        effective_team_count=shape.effective_team_count,
        actual_team_count=actual,
        missing_slots=max(0, shape.effective_team_count - actual),
        input_constrained=shape.input_constrained,
    )


def _registered_same_age(problem: Mapping[str, Any], age_group: str) -> List[Mapping[str, Any]]:
    return [
        team
        for team in problem.get("teams", [])
        if team.get("age_group") == age_group
    ]


def _option_base(finding_id: str, tournament: Mapping[str, Any], team: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "finding_id": finding_id,
        "tournament_id": tournament.get("id"),
        "team": _team_ref(team),
    }


def _deficit(problem: Mapping[str, Any], team: Mapping[str, Any], counts: Mapping[Any, int]) -> int:
    target = team.get("target_tournament_count", problem.get("target_tournament_count"))
    if not isinstance(target, int) or isinstance(target, bool):
        return 0
    return max(0, target - counts.get(_identity(team), 0))


def _rank_key(problem, team, counts):
    return (-_deficit(problem, team, counts), str(team.get("club", "")), str(team.get("label", "")))


def _fill_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    shape: _RosterShape,
    finding_id: str,
    fingerprint: str,
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    """Legal single-team fills, or one deterministic bounded bundle for a
    deeper deficit, plus explicit per-candidate rejections."""
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    tournament_id = str(tournament.get("id"))
    tournament_date = _parse_date(tournament.get("date"))
    current = {_identity(team) for team in tournament.get("teams") or []}
    existing_counts = _participation_counts(candidate)
    half_counts = _participations_by_half(candidate, problem)
    same_date = _same_date_identities(
        candidate, tournament_date, except_tournament_id=tournament_id
    )
    verification_before = verify_candidate(dict(candidate), dict(problem))

    legal: List[Mapping[str, Any]] = []
    for team in _registered_same_age(problem, shape.age_group):
        base = _option_base(finding_id, tournament, team)
        identity = _identity(team)
        if identity in current:
            rejected.append({**base, "reason": "already_participating"})
            continue
        if identity in same_date:
            rejected.append({**base, "reason": "already_plays_same_date"})
            continue
        limit = _participation_limit_reason(
            identity, existing_counts, half_counts, problem, tournament_date, tournament_id
        )
        if limit is not None:
            rejected.append({**base, "reason": limit})
            continue
        legal.append(team)

    legal.sort(key=lambda team: _rank_key(problem, team, existing_counts))
    if not legal:
        return out, rejected

    if shape.missing_slots == 1:
        for team in legal:
            trial = copy.deepcopy(candidate)
            _edit_roster(trial, tournament_id, add=team, problem=problem)
            result = verify_candidate(trial, dict(problem))
            if not result.get("ok"):
                rejected.append(
                    {
                        **_option_base(finding_id, tournament, team),
                        "reason": _primary_violation_reason(result),
                        "violations": _codes(result),
                    }
                )
                continue
            deficit = _deficit(problem, team, existing_counts)
            out.append(
                RepairOption(
                    option_id=f"{fingerprint[:12]}:{finding_id}:fill_participant:{_slug(_identity(team))}",
                    finding_id=finding_id,
                    action="fill_participant",
                    tournament_id=tournament_id,
                    arguments={"add_team": _team_ref(team)},
                    hard_feasible=True,
                    effects={
                        "bye_team_not_allowed": _count_code(result, "bye_team_not_allowed")
                        - _count_code(verification_before, "bye_team_not_allowed"),
                        "roster_size_delta": 1,
                        "participation_deficit_delta": -1 if deficit > 0 else 0,
                        "participation_deficit": deficit,
                    },
                    evidence={
                        "verification_ok": True,
                        "effective_team_count": shape.effective_team_count,
                        "actual_team_count": shape.actual_team_count,
                        "missing_slots": 1,
                    },
                )
            )
        return out, rejected

    # Deeper deficit: expose one deterministic, verified bundle instead of a
    # combinatorial sequence. The bundle is the ranked legal set; the harness
    # still owns whether to apply it or request a broader search.
    chosen = legal[: shape.missing_slots]
    trial = copy.deepcopy(candidate)
    for team in chosen:
        _edit_roster(trial, tournament_id, add=team, problem=problem)
    result = verify_candidate(trial, dict(problem))
    if not result.get("ok"):
        rejected.append(
            {
                "finding_id": finding_id,
                "tournament_id": tournament.get("id"),
                "reason": _primary_violation_reason(result),
                "violations": _codes(result),
                "add_teams": [_team_ref(team) for team in chosen],
            }
        )
        return out, rejected
    out.append(
        RepairOption(
            option_id=f"{fingerprint[:12]}:{finding_id}:fill_participant:bundle",
            finding_id=finding_id,
            action="fill_participant",
            tournament_id=tournament_id,
            arguments={"add_teams": [_team_ref(team) for team in chosen]},
            hard_feasible=True,
            effects={
                "bye_team_not_allowed": _count_code(result, "bye_team_not_allowed")
                - _count_code(verification_before, "bye_team_not_allowed"),
                "roster_size_delta": len(chosen),
                "participation_deficit_delta": -len(chosen),
            },
            evidence={
                "verification_ok": True,
                "effective_team_count": shape.effective_team_count,
                "actual_team_count": shape.actual_team_count,
                "missing_slots": shape.missing_slots,
                "bundled": True,
            },
        )
    )
    return out, rejected


def _swap_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    shape: _RosterShape,
    finding_id: str,
    fingerprint: str,
    pinned_ids: set,
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    """Bounded same-age relocations that fill the target and keep the donor legal.

    A source team moves from a donor same-age tournament into the underfilled
    tournament, and the donor is refilled from the remaining registered pool so
    both rosters stay legal. Only a single donor is involved (bounded
    neighborhood); anything wider is a solver/broader-search request, not a
    local option.
    """
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    target_id = str(tournament.get("id"))
    target_date = _parse_date(tournament.get("date"))
    target_teams = list(tournament.get("teams") or [])
    target_identities = {_identity(team) for team in target_teams}
    target_club_counts: Dict[str, int] = {}
    for team in target_teams:
        club = str(team.get("club", ""))
        target_club_counts[club] = target_club_counts.get(club, 0) + 1
    existing_counts = _participation_counts(candidate)
    half_counts = _participations_by_half(candidate, problem)
    target_same_date = _same_date_identities(
        candidate, target_date, except_tournament_id=target_id
    )
    verification_before = verify_candidate(dict(candidate), dict(problem))

    for donor in candidate.get("tournaments", []):
        donor_id = str(donor.get("id"))
        if donor_id == target_id or donor.get("cancelled"):
            continue
        if donor.get("age_group") != shape.age_group:
            continue
        if donor_id in pinned_ids:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": tournament.get("id"),
                    "source_tournament": donor.get("id"),
                    "reason": "manual_restriction_forbids_mutation",
                }
            )
            continue
        donor_date = _parse_date(donor.get("date"))
        donor_teams = list(donor.get("teams") or [])
        donor_identities = {_identity(team) for team in donor_teams}
        donor_same_date = _same_date_identities(
            candidate, donor_date, except_tournament_id=donor_id
        )
        for source in donor_teams:
            source_identity = _identity(source)
            base = {
                "finding_id": finding_id,
                "tournament_id": tournament.get("id"),
                "source_tournament": donor.get("id"),
                "source_team": _team_ref(source),
            }
            if source_identity in target_identities:
                rejected.append({**base, "reason": "already_participating_target"})
                continue
            if source_identity in target_same_date:
                rejected.append({**base, "reason": "already_plays_target_date"})
                continue
            if target_club_counts.get(str(source.get("club", "")), 0) >= HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT:
                rejected.append({**base, "reason": "club_hard_max_exceeded"})
                continue
            donor_after_source = [
                team for team in donor_teams if _identity(team) != source_identity
            ]
            donor_fill = _donor_fill_candidate(
                problem,
                shape.age_group,
                donor_after_source,
                donor_identities,
                source_identity,
                donor_same_date,
                existing_counts,
                half_counts,
                donor_id,
                donor_date,
            )
            for replacement in donor_fill:
                trial = copy.deepcopy(candidate)
                _edit_roster(
                    trial, donor_id, remove=source, add=replacement, problem=problem
                )
                _edit_roster(trial, target_id, add=source, problem=problem)
                result = verify_candidate(trial, dict(problem))
                if not result.get("ok"):
                    rejected.append(
                        {
                            **base,
                            "donor_add_team": _team_ref(replacement) if replacement else None,
                            "reason": _primary_violation_reason(result),
                            "violations": _codes(result),
                        }
                    )
                    continue
                args = {
                    "add_team": _team_ref(source),
                    "from_tournament_id": donor_id,
                    "move_team": _team_ref(source),
                }
                if replacement is not None:
                    args["donor_add_team"] = _team_ref(replacement)
                deficit = _deficit(problem, source, existing_counts)
                out.append(
                    RepairOption(
                        option_id=(
                            f"{fingerprint[:12]}:{finding_id}:swap_participant:"
                            f"{_slug((donor_id, source_identity))}"
                        ),
                        finding_id=finding_id,
                        action="swap_participant",
                        tournament_id=target_id,
                        arguments=args,
                        hard_feasible=True,
                        effects={
                            "bye_team_not_allowed": _count_code(result, "bye_team_not_allowed")
                            - _count_code(verification_before, "bye_team_not_allowed"),
                            "roster_size_delta": 0,
                            "participation_deficit_delta": -1 if deficit > 0 else 0,
                            "participation_deficit": deficit,
                        },
                        evidence={
                            "verification_ok": True,
                            "effective_team_count": shape.effective_team_count,
                            "missing_slots": shape.missing_slots,
                            "donor_tournament": donor.get("id"),
                            "donor_refilled": replacement is not None,
                        },
                    )
                )
                if len(out) >= _MAX_SWAP_OPTIONS:
                    return out, rejected
    return out, rejected


def _donor_fill_candidate(
    problem,
    age_group: str,
    donor_teams,
    donor_identities,
    source_identity,
    donor_same_date,
    existing_counts,
    half_counts,
    donor_id: str,
    donor_date: Optional[date],
) -> Sequence[Optional[Mapping[str, Any]]]:
    """Replacement choices for a donor that just lost *source*.

    Always offers ``None`` (could the donor stand legal without a replacement?)
    plus the ranked legal pool candidates that could refill it. The canonical
    verifier decides which of these is actually legal.
    """
    options: List[Optional[Mapping[str, Any]]] = [None]
    donor_club_counts: Dict[str, int] = {}
    for team in donor_teams:
        if _identity(team) == source_identity:
            continue
        club = str(team.get("club", ""))
        donor_club_counts[club] = donor_club_counts.get(club, 0) + 1
    for team in _registered_same_age(problem, age_group):
        identity = _identity(team)
        if identity == source_identity or identity in donor_identities:
            continue
        if identity in donor_same_date:
            continue
        if donor_club_counts.get(str(team.get("club", "")), 0) >= HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT:
            continue
        limit = _participation_limit_reason(
            identity, existing_counts, half_counts, problem, donor_date, donor_id
        )
        if limit is not None:
            continue
        options.append(team)
    # Try the smallest change first: a donor with a legal surplus needs no
    # refill at all. The verifier rejects it when the donor does need one.
    return [None, *options[1:]]

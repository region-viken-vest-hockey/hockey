"""Deterministic repair options for hosting-balance / coverage deficits.

``hosting_responsibility`` already owns the invariant: every club x age-group
with a registered team must host at least one tournament there, and physical
hosting burden should track the fairness model's proportional target. This
provider turns a deficit row in that ledger into concrete, verified repair
options, so a controller (Stage 3 or promoted-season maintenance) never has to
reconstruct a host reallocation from raw schedule state.

Two families are exposed, cheapest first:

- ``rehost`` -- a tournament of the deficit age group where the deficit club is
  already a participant but not the physical host. Reassigning host/arena to the
  deficit club preserves the whole season and only relabels responsibility.
- ``search`` -- a bounded ``stage3_optimizer.optimize_candidate`` neighborhood
  over the finding's age group (participant swaps plus any requested
  host/date/slot dimensions), keeping every other tournament frozen. Only
  results that keep full verification valid *and* reduce the deficit are
  exposed; a result that simply shifts the shortfall elsewhere is rejected.

Like the other provider modules it is planner-neutral (plain
``problem``/``candidate`` dicts) and never decides on its own: it enumerates
options and applies exactly one selected option id against an unchanged
candidate fingerprint, rerunning the independent verifier and the responsibility
guard.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .host_representation import clubs_represent_same_club, constituent_clubs, host_represented_in
from .host_team_missing_repair import RepairOption, candidate_fingerprint, search_dimension_tag
from .hosting_coverage import hosting_balance_matrix
from .hosting_responsibility import unexplained_responsibility_transfers
from .planning_contract import verify_candidate
from .stage3_optimizer import optimize_candidate

HOSTING_FINDING_PREFIX = "hosting_balance"
_DEFAULT_ITERATIONS = 800
_DEFAULT_SEEDS: Tuple[int, ...] = (0, 1)
_MAX_SEARCH_OPTIONS = 3


def hosting_finding_id(age_group: str, club: str) -> str:
    """Stable finding identity shared by findings and option enumeration."""
    return f"{HOSTING_FINDING_PREFIX}:{age_group}:{club}"


def hosting_deficit_rows(
    problem: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Deficit/coverage rows that constitute an actionable hosting finding."""
    rows = hosting_balance_matrix((problem or {}).get("teams") or [], (candidate or {}).get("tournaments") or [])
    return [row for row in rows if int(row.get("deficit", 0)) > 0]


def enumerate_hosting_balance_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
    allow_search: bool = False,
    scope: Mapping[str, Any] | None = None,
    iterations: int = _DEFAULT_ITERATIONS,
    seeds: Sequence[int] = _DEFAULT_SEEDS,
    dimensions: Iterable[str] = ("participants", "host"),
) -> Dict[str, Any]:
    """Return legal hosting repairs and explicit rejection evidence."""
    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    result_candidates: Dict[str, Any] = {}
    rows = hosting_deficit_rows(problem, candidate)
    scope = scope or {}
    scope_age = str(scope.get("age_group") or "")
    scope_club = str(scope.get("club") or "")
    if scope_age or scope_club:
        rows = [
            row
            for row in rows
            if (not scope_age or row.get("age_group") == scope_age)
            and (not scope_club or row.get("club") == scope_club)
        ]
    if not rows:
        return _result(run_id, fingerprint, verification, options, rejected, result_candidates, [], applicable=False)

    excess_by_key = _excess_index(problem, candidate)
    for row in rows:
        finding_id = hosting_finding_id(str(row["age_group"]), str(row["club"]))
        before = _ledger_facts(problem, candidate)
        direct, direct_rejected = _rehost_options(
            candidate, problem, row, finding_id, fingerprint, before, excess_by_key
        )
        options.extend(direct)
        rejected.extend(direct_rejected)
        if allow_search:
            searched, search_rejected, results = _search_options(
                candidate,
                problem,
                row,
                finding_id,
                fingerprint,
                before,
                iterations=iterations,
                seeds=seeds,
                dimensions=dimensions,
            )
            options.extend(searched)
            result_candidates.update(results)
            rejected.extend(search_rejected)
    options.sort(
        key=lambda option: (
            0 if option.action == "rehost" else 1,
            -int(option.effects.get("donor_host_excess_before", 0)),
            option.option_id,
        )
    )
    return _result(
        run_id,
        fingerprint,
        verification,
        options,
        rejected,
        result_candidates,
        rows,
        applicable=True,
    )


def apply_hosting_balance_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
    scope: Mapping[str, Any] | None = None,
    dimensions: Iterable[str] = ("participants", "host"),
) -> Dict[str, Any]:
    """Atomically apply a selected hosting option and re-verify the result."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    allow_search = ":search:" in option_id
    repair_set = enumerate_hosting_balance_repairs(
        candidate,
        problem,
        run_id=run_id,
        allow_search=allow_search,
        scope=scope,
        dimensions=dimensions,
    )
    option = next((entry for entry in repair_set["options"] if entry["option_id"] == option_id), None)
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
    result = repair_set["result_candidates"].get(option_id)
    if result is None:
        result = _reconstruct_rehost(candidate, problem, option)
    if result is None:
        return {"ok": False, "reason": "repair_result_unavailable", "before_fingerprint": before}
    verification = verify_candidate(dict(result), dict(problem))
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "before_fingerprint": before,
            "verification": verification,
        }
    transfers = unexplained_responsibility_transfers(candidate, result, problem)
    if transfers:
        return {
            "ok": False,
            "reason": "unexplained_hosting_responsibility_transfer",
            "before_fingerprint": before,
            "responsibility_transfers": transfers,
        }
    return {
        "ok": True,
        "candidate": result,
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": candidate_fingerprint(result),
        "verification": verification,
        "effects": option.get("effects", {}),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _rehost_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
    finding_id: str,
    fingerprint: str,
    before: Mapping[Tuple[str, str], Mapping[str, Any]],
    excess_by_key: Mapping[Tuple[str, str], int],
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    age_group = str(row["age_group"])
    club = str(row["club"])
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    donors = _donor_tournaments(candidate, row)
    if not donors:
        rejected.append(
            {
                "finding_id": finding_id,
                "reason": "no_same_age_donor_tournament",
                "club": club,
                "age_group": age_group,
            }
        )
    for tournament in donors:
        tournament_id = str(tournament.get("id") or "")
        host_club = _physical_host_for(club, tournament)
        if not host_club:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": tournament_id,
                    "reason": "deficit_club_not_representable_in_donor",
                }
            )
            continue
        mutated = _with_host(candidate, problem, tournament_id, host_club)
        result = verify_candidate(dict(mutated), dict(problem))
        if not result.get("ok"):
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": tournament_id,
                    "reason": "not_hard_valid",
                    "violation_codes": sorted({str(v.get("code")) for v in result.get("violations", [])}),
                }
            )
            continue
        transfers = unexplained_responsibility_transfers(candidate, mutated, problem)
        if transfers:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": tournament_id,
                    "reason": "unexplained_hosting_responsibility_transfer",
                    "responsibility_transfers": transfers,
                }
            )
            continue
        if not _hosting_improves(problem, candidate, mutated, age_group, club):
            rejected.append(
                {"finding_id": finding_id, "tournament_id": tournament_id, "reason": "no_deficit_improvement"}
            )
            continue
        donor_host = str(tournament.get("host_club") or "")
        option_id = f"{fingerprint[:12]}:{HOSTING_FINDING_PREFIX}:{age_group}:{club}:rehost:{tournament_id}"
        options.append(
            RepairOption(
                option_id=option_id,
                finding_id=finding_id,
                action="rehost",
                tournament_id=tournament_id,
                arguments={"host_club": host_club, "arena": _arena_for(problem, host_club)},
                hard_feasible=True,
                effects=_effects(problem, candidate, mutated, [tournament_id], before, age_group, club, donor_host, excess_by_key),
                evidence={"verification_ok": True, "donor_host": donor_host},
            )
        )
    return options, rejected


def _search_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
    finding_id: str,
    fingerprint: str,
    before: Mapping[Tuple[str, str], Mapping[str, Any]],
    *,
    iterations: int,
    seeds: Sequence[int],
    dimensions: Iterable[str],
) -> Tuple[List[RepairOption], List[Dict[str, Any]], Dict[str, Any]]:
    age_group = str(row["age_group"])
    club = str(row["club"])
    dims = {str(d) for d in dimensions}
    frozen = [
        str(t.get("id"))
        for t in candidate.get("tournaments", [])
        if t.get("age_group") != age_group
    ]
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    results: Dict[str, Any] = {}
    excess_by_key = _excess_index(problem, candidate)
    seen: set[str] = set()
    for seed in seeds:
        outcome = optimize_candidate(
            dict(candidate),
            dict(problem),
            iterations=max(1, int(iterations)),
            seed=int(seed),
            move_hosts="host" in dims,
            move_dates="date" in dims,
            move_slots="slot" in dims,
            frozen_tournament_ids=frozen,
        )
        verification = verify_candidate(dict(outcome), dict(problem))
        if not verification.get("ok"):
            rejected.append(
                {
                    "finding_id": finding_id,
                    "reason": "search_candidate_not_hard_valid",
                    "seed": int(seed),
                    "violation_codes": sorted({str(v.get("code")) for v in verification.get("violations", [])}),
                }
            )
            continue
        transfers = unexplained_responsibility_transfers(candidate, outcome, problem)
        if transfers:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "reason": "unexplained_hosting_responsibility_transfer",
                    "seed": int(seed),
                    "responsibility_transfers": transfers,
                }
            )
            continue
        if not _hosting_improves(problem, candidate, outcome, age_group, club):
            rejected.append(
                {"finding_id": finding_id, "reason": "no_deficit_improvement", "seed": int(seed)}
            )
            continue
        after_fp = candidate_fingerprint(outcome)
        if after_fp in seen:
            rejected.append({"finding_id": finding_id, "reason": "duplicate_candidate", "seed": int(seed)})
            continue
        seen.add(after_fp)
        changed = _changed_ids(candidate, outcome)
        # Option identity is the deterministic request that reconstructs it
        # (before-fingerprint + finding scope + seed), never the fingerprint of
        # the produced candidate: a search result carries run instrumentation
        # (``source.timings``) that differs on every enumeration, so embedding
        # its fingerprint would make the option unapplyable. Match the scheme
        # ``search_neighborhood_repair`` already uses.
        option_id = (
            f"{fingerprint[:12]}:{HOSTING_FINDING_PREFIX}:{age_group}:{club}:"
            f"search:{int(seed)}:{search_dimension_tag(dims)}"
        )
        options.append(
            RepairOption(
                option_id=option_id,
                finding_id=finding_id,
                action="search",
                tournament_id="",
                arguments={
                    "seed": int(seed),
                    "iterations": max(1, int(iterations)),
                    "dimensions": sorted(dims),
                    "frozen_tournament_ids": frozen,
                },
                hard_feasible=True,
                effects=_effects(
                    problem, candidate, outcome, changed, before, age_group, club, "", excess_by_key
                ),
                evidence={"verification_ok": True, "seed": int(seed), "result_fingerprint": after_fp},
            )
        )
        results[option_id] = outcome
        if len(options) >= _MAX_SEARCH_OPTIONS:
            break
    return options, rejected, results


def _reconstruct_rehost(
    candidate: Mapping[str, Any], problem: Mapping[str, Any], option: Mapping[str, Any]
) -> Dict[str, Any] | None:
    if option.get("action") != "rehost":
        return None
    argument_host = str((option.get("arguments") or {}).get("host_club") or "")
    if not argument_host:
        return None
    return _with_host(candidate, problem, str(option.get("tournament_id") or ""), argument_host)


def _with_host(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament_id: str,
    host_club: str,
) -> Dict[str, Any]:
    mutated = copy.deepcopy(dict(candidate))
    for tournament in mutated.get("tournaments", []) or []:
        if str(tournament.get("id")) != tournament_id:
            continue
        tournament["host_club"] = host_club
        arena = _arena_for(problem, host_club)
        if arena:
            tournament["arena"] = arena
        tournament.pop("requires_host_confirmation", None)
        tournament.pop("host_confirmation_reason", None)
        break
    return mutated


def _arena_for(problem: Mapping[str, Any], club: str) -> str:
    arenas = problem.get("club_arenas") or problem.get("clubs") or {}
    if not isinstance(arenas, Mapping):
        return ""
    for constituent in constituent_clubs(club):
        arena = arenas.get(constituent)
        if arena:
            return str(arena)
    return ""


def _donor_tournaments(candidate: Mapping[str, Any], row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    age_group = str(row["age_group"])
    club = str(row["club"])
    donors: List[Dict[str, Any]] = []
    for tournament in candidate.get("tournaments", []) or []:
        if tournament.get("cancelled") or str(tournament.get("age_group")) != age_group:
            continue
        if clubs_represent_same_club(str(tournament.get("host_club") or ""), club):
            continue
        if not _represented(tournament, club):
            continue
        donors.append(tournament)
    return donors


def _represented(tournament: Mapping[str, Any], club: str) -> bool:
    return host_represented_in(tournament.get("teams") or [], club)


def _physical_host_for(club: str, tournament: Mapping[str, Any]) -> str:
    for constituent in constituent_clubs(club):
        if _represented(tournament, constituent):
            return constituent
    if _represented(tournament, club):
        return club
    return ""


def _hosting_improves(
    problem: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    age_group: str,
    club: str,
) -> bool:
    """True when a change reduces the selected deficit without creating another.

    The responsibility invariant is generic: a repair may draw down a deficit
    only by drawing on a surplus. Moving a tournament away from a club that was
    exactly at its target merely relocates the shortfall, so any change that
    increases *any* club's deficit (or creates a new one) is rejected.
    """
    before_facts = _ledger_facts(problem, before)
    after_facts = _ledger_facts(problem, after)
    for key in set(before_facts) | set(after_facts):
        before_deficit = int(before_facts.get(key, {}).get("deficit", 0))
        after_deficit = int(after_facts.get(key, {}).get("deficit", 0))
        if after_deficit > before_deficit:
            return False
    key = (age_group, club)
    if key not in after_facts:
        return False
    return int(after_facts[key]["deficit"]) < int(before_facts.get(key, {}).get("deficit", 0))


def _ledger_facts(
    problem: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    rows = hosting_balance_matrix((problem or {}).get("teams") or [], (candidate or {}).get("tournaments") or [])
    return {(str(row["age_group"]), str(row["club"])): row for row in rows}


def _excess_index(
    problem: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Dict[Tuple[str, str], int]:
    facts = _ledger_facts(problem, candidate)
    return {key: int(row.get("excess", 0)) for key, row in facts.items()}


def _effects(
    problem: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    changed: Iterable[str],
    before_facts: Mapping[Tuple[str, str], Mapping[str, Any]],
    age_group: str,
    club: str,
    donor_host: str,
    excess_by_key: Mapping[Tuple[str, str], int],
) -> Dict[str, Any]:
    after_facts = _ledger_facts(problem, after)
    before_row = before_facts.get((age_group, club), {})
    after_row = after_facts.get((age_group, club), {})
    changed_ids = sorted({str(item) for item in changed})
    effects: Dict[str, Any] = {
        "hosting_deficit_before": int(before_row.get("deficit", 0)),
        "hosting_deficit_after": int(after_row.get("deficit", 0)),
        "coverage_unresolved_before": bool(before_row.get("coverage_unresolved")),
        "coverage_unresolved_after": bool(after_row.get("coverage_unresolved")),
        "changed_tournament_ids": changed_ids,
        "changed_tournament_count": len(changed_ids),
    }
    if donor_host:
        effects["donor_host"] = donor_host
        effects["donor_host_excess_before"] = int(excess_by_key.get((age_group, donor_host), 0))
    return effects


def _changed_ids(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    before_by_id = {str(t.get("id")): t for t in before.get("tournaments", [])}
    after_by_id = {str(t.get("id")): t for t in after.get("tournaments", [])}
    changed: List[str] = []
    for tournament_id in sorted(set(before_by_id) | set(after_by_id)):
        if _signature(before_by_id.get(tournament_id)) != _signature(after_by_id.get(tournament_id)):
            changed.append(tournament_id)
    return changed


def _signature(tournament: Mapping[str, Any] | None) -> Any:
    if tournament is None:
        return None
    teams = tuple(
        sorted(
            (
                str(team.get("club") or ""),
                str(team.get("label") or ""),
                str(team.get("age_group") or ""),
            )
            for team in tournament.get("teams", []) or []
        )
    )
    return (
        tournament.get("date"),
        tournament.get("arena"),
        tournament.get("host_club"),
        tournament.get("start_time"),
        teams,
    )


def _result(
    run_id: str,
    fingerprint: str,
    verification: Mapping[str, Any],
    options: List[RepairOption],
    rejected: List[Dict[str, Any]],
    result_candidates: Dict[str, Any],
    rows: List[Dict[str, Any]],
    *,
    applicable: bool,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": dict(verification),
        "applicable": applicable,
        "applicable_findings": [hosting_finding_id(str(r["age_group"]), str(r["club"])) for r in rows],
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
        "result_candidates": result_candidates,
    }

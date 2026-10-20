"""Read-only participant-swap discovery over the shared consequence preview.

The operator names a bounded set of tournaments. Discovery enumerates same-age
pairs and their non-guest participants inside that scope, then asks the shared
preview for each pair. It never writes, never ranks and never reimplements the
swap gates: verdicts, blockers and fingerprints come from the preview unchanged.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.baseline_overlay import resolve_approval
from tournament_scheduler.season.request_constraints import team_identity
from tournament_scheduler.season.state import canonical_state_revision

from .consequence_preview import preview_canonical_operation

MAX_SCOPE_TOURNAMENTS = 10
MAX_EVALUATED_CANDIDATES = 100
RANKING_POLICY = "none: deterministic order by (tournament_a_id, tournament_b_id, team_a_label, team_b_label)"


def search_participant_swaps(
    service,
    *,
    season: str,
    tournament_ids: list[str],
) -> dict[str, Any]:
    """Return every evaluated same-age swap in the scope, unranked, with shared verdicts."""

    scope = sorted({str(item) for item in tournament_ids if str(item)})
    if not 2 <= len(scope) <= MAX_SCOPE_TOURNAMENTS:
        raise ValueError(
            f"Swap discovery needs 2 to {MAX_SCOPE_TOURNAMENTS} distinct tournament ids; got {len(scope)}"
        )

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    revision = canonical_state_revision(schedule, decisions)
    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in (schedule.get("plan") or {}).get("tournaments", []) or []
    }

    excluded: list[dict[str, str]] = []
    eligible: list[dict[str, Any]] = []
    for tournament_id in scope:
        tournament = by_id.get(tournament_id)
        if tournament is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
        if tournament.get("cancelled"):
            excluded.append({"tournament_id": tournament_id, "reason": "cancelled"})
            continue
        resolved = resolve_approval(
            decisions.get("decisions", {}).get(tournament_id, {}),
            tournament,
        )
        if resolved["participants_locked"]:
            excluded.append({"tournament_id": tournament_id, "reason": "participants_locked"})
            continue
        eligible.append(tournament)

    pairs = [
        (first, second)
        for first, second in combinations(eligible, 2)
        if str(first.get("age_group") or "") == str(second.get("age_group") or "")
    ]
    combos = [
        (first, team_a, second, team_b)
        for first, second in pairs
        for team_a in _participants(first)
        for team_b in _participants(second)
        if _identity(team_a, first) not in _identities(second)
        and _identity(team_b, second) not in _identities(first)
    ]
    combos.sort(key=lambda item: (
        str(item[0]["id"]), str(item[2]["id"]), str(item[1].get("label") or ""), str(item[3].get("label") or ""),
    ))

    candidates = [
        _candidate(service, season, revision, first, team_a, second, team_b)
        for first, team_a, second, team_b in combos[:MAX_EVALUATED_CANDIDATES]
    ]

    after = service.load(season)
    if canonical_state_revision(after.schedule, after.decisions) != revision:
        raise RuntimeError("Swap candidate discovery changed canonical state; discovery must never write")

    if not pairs:
        empty_reason = "no_eligible_pairs"
    elif not combos:
        empty_reason = "no_eligible_participants"
    else:
        empty_reason = None
    return {
        "season": season,
        "canonical_state_revision": revision,
        "scope": scope,
        "excluded_tournaments": excluded,
        "limits": {
            "max_scope_tournaments": MAX_SCOPE_TOURNAMENTS,
            "max_evaluated_candidates": MAX_EVALUATED_CANDIDATES,
        },
        "truncated": len(combos) > MAX_EVALUATED_CANDIDATES,
        "candidate_count": len(candidates),
        "safe_candidate_count": sum(1 for item in candidates if item["safe_to_apply"]),
        "empty_reason": empty_reason,
        "ranking_policy": RANKING_POLICY,
        "candidates": candidates,
        "apply_command": "season swap-participants requires an explicit request id and is never invoked by this discovery",
    }


def _participants(tournament: dict[str, Any]) -> list[dict[str, Any]]:
    return [team for team in tournament.get("teams", []) or [] if not bool((team or {}).get("guest", False))]


def _identity(team: dict[str, Any], tournament: dict[str, Any]) -> tuple[str, str]:
    return team_identity(team, str(tournament.get("age_group") or ""))


def _identities(tournament: dict[str, Any]) -> set[tuple[str, str]]:
    return {_identity(team, tournament) for team in tournament.get("teams", []) or []}


def _candidate(service, season, revision, first, team_a, second, team_b) -> dict[str, Any]:
    preview = preview_canonical_operation(
        service,
        operation="swap",
        season=season,
        expected_revision=revision,
        tournament_a_id=str(first["id"]),
        team_a_label=str(team_a.get("label") or ""),
        tournament_b_id=str(second["id"]),
        team_b_label=str(team_b.get("label") or ""),
    )
    verdict = preview["verdict"]
    return {
        "tournament_a_id": str(first["id"]),
        "team_a_label": str(team_a.get("label") or ""),
        "tournament_b_id": str(second["id"]),
        "team_b_label": str(team_b.get("label") or ""),
        "age_group": str(first.get("age_group") or ""),
        "verdict": verdict,
        "safe_to_apply": bool(verdict["applicable"]),
        "blockers": list(verdict["blockers"]),
        "candidate_fingerprint": preview.get("candidate_fingerprint"),
        "canonical_preview": {key: value for key, value in preview.items() if key != "details"},
    }


__all__ = ["MAX_EVALUATED_CANDIDATES", "MAX_SCOPE_TOURNAMENTS", "search_participant_swaps"]

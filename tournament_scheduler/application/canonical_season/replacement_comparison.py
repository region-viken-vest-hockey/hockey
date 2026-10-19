"""Read-only comparison of participant-replacement candidates.

Orders candidates by the shared lexicographic priority vector
(``replacement_search_evidence.priority_vector``) and reports Pareto-nondominated
choices, deterministic ties and full deltas without collapsing components into a
scalar. It owns no rule or scorer: legality comes from the replacement dry-run
gates, and ordering comes from the shared vector.

Nothing here writes. A ``no_legal_option`` result is relative to the bounded
pool that was searched and never claims that no other legal roster exists.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from tournament_scheduler.rules import catalog

from .replacement_search import search_participant_replacements
from .replacement_search_evidence import (
    PRIORITY_IDENTITY_COMPONENTS,
    priority_vector,
)

COMPARISON_CONTRACT = "replacement-candidate-comparison/1"

CLASS_HARD_INELIGIBLE = "hard_ineligible"
CLASS_OUT_OF_SCOPE = "out_of_scope"

# Gates the rule catalog does not declare as verifier codes. Gates that are
# catalog codes resolve their owner through ``rules/catalog.py`` instead.
GATE_OWNERS: dict[str, str] = {
    "schedule_acceptable": "planning.verification",
    "consequences_acceptable": "participation.team_schedule_consequence",
    "change_protection_acceptable": "season.change_protections",
    "request_constraints_acceptable": "season.request_constraints",
    "hosting_responsibility_acceptable": "canonical_season.hosting_responsibility",
}

# Verdict checks emitted by ``replacement_verdict`` that are dry-run gates.
GATE_CHECKS = frozenset({*GATE_OWNERS, "guest_reservation_integrity"})

# Gate evidence that the dry-run already carries for each failed check.
CHECK_EVIDENCE_KEYS: dict[str, str] = {
    "change_protection_acceptable": "change_protection_violations",
    "request_constraints_acceptable": "request_constraint_regressions",
}


def gate_owner(check: str) -> str:
    for entry in catalog.RULE_CATALOG:
        if check in entry.verifier_codes:
            return entry.canonical_owner
    return GATE_OWNERS[check]


def exclusion_reason(
    code: str,
    classification: str,
    owner: str,
    evidence: Any = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "classification": classification,
        "owner": owner,
        "message": message,
        "evidence": list(evidence or []),
    }


def blocker_reasons(
    blockers: Sequence[str],
    evidence: Mapping[str, Sequence[Any]] | None = None,
) -> list[dict[str, Any]]:
    """Map verdict blockers to stable, owner-tagged exclusion reasons.

    Known gate names map to their canonical owner. Any other blocker is a
    precondition message raised before the gates ran (for example an unknown
    registered team) and is reported as such, never as a gate verdict.
    """

    reasons: list[dict[str, Any]] = []
    for blocker in blockers:
        name = str(blocker)
        if name in GATE_CHECKS:
            key = CHECK_EVIDENCE_KEYS.get(name)
            reasons.append(
                exclusion_reason(
                    name,
                    CLASS_HARD_INELIGIBLE,
                    gate_owner(name),
                    (evidence or {}).get(key, []) if key else [],
                )
            )
        else:
            reasons.append(
                exclusion_reason(
                    "replacement_precondition",
                    CLASS_HARD_INELIGIBLE,
                    "canonical_season.replacement_preconditions",
                    [name],
                )
            )
    return reasons


def gate_reasons(
    blockers: Sequence[str],
    refusals: Sequence[Mapping[str, Any]] | None,
    evidence: Mapping[str, Sequence[Any]] | None = None,
) -> list[dict[str, Any]]:
    """Exclusion reasons for one blocked dry-run, forwarding structured refusals.

    Early refusals carry their own stable code and owner from the canonical
    decision point and are forwarded as-is. Gate failures without structured
    refusals are attributed through the verdict check names.
    """

    if refusals:
        return [
            exclusion_reason(
                str(item["code"]),
                CLASS_HARD_INELIGIBLE,
                str(item["owner"]),
                item.get("evidence"),
                message=item.get("message"),
            )
            for item in refusals
        ]
    return blocker_reasons(blockers, evidence)


def pair_key(candidate: Mapping[str, Any]) -> str:
    return f"{candidate['remove_team']['label']}->{candidate['add_team']['label']}"


def _numeric_vector(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        value
        for name, value in priority_vector(candidate)
        if name != "legality" and name not in PRIORITY_IDENTITY_COMPONENTS
    )


def _dominates(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    return all(a <= b for a, b in zip(left, right)) and any(
        a < b for a, b in zip(left, right)
    )


def _deltas(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "club_age_booked_participation": copy.deepcopy(candidate["club_age_booked_participation"]),
        "affected_team_booked_coverage": copy.deepcopy(candidate["affected_team_booked_coverage"]),
        "spacing_clustering_delta": copy.deepcopy(candidate["spacing_clustering_delta"]),
        "opponent_exposure_delta": copy.deepcopy(candidate["opponent_exposure_delta"]),
        "travel_delta": copy.deepcopy(candidate["travel_delta"]),
        "soft_preference_effects": copy.deepcopy(candidate["soft_preference_effects"]),
    }


def _evidence_from_candidate(candidate: Mapping[str, Any]) -> dict[str, list[Any]]:
    effects = candidate.get("active_protection_request_effects") or {}
    return {
        "change_protection_violations": list(effects.get("change_protection_violations") or []),
        "request_constraint_regressions": list(effects.get("request_constraint_regressions") or []),
    }


def compare_candidates(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Order replacement candidates by priority and identify nondominated choices.

    ``candidates`` are replacement-search candidate records. Legal candidates
    become ``options``; the rest are returned as ``excluded`` with their
    owner-tagged reasons. Dominance is judged over the numeric priority
    components only (legality and identity are not objectives), so a candidate
    is dominated only when another legal candidate is no worse on every
    component and strictly better on at least one.
    """

    legal = [item for item in candidates if item.get("safe_to_apply")]
    illegal = [item for item in candidates if not item.get("safe_to_apply")]

    ordered = sorted(
        legal,
        key=lambda item: tuple(value for _, value in priority_vector(item)),
    )
    numeric = {pair_key(item): _numeric_vector(item) for item in ordered}

    options: list[dict[str, Any]] = []
    for rank, candidate in enumerate(ordered, 1):
        key = pair_key(candidate)
        vector = numeric[key]
        dominated_by = [
            pair_key(other)
            for other in ordered
            if pair_key(other) != key and _dominates(numeric[pair_key(other)], vector)
        ]
        ties = [
            pair_key(other)
            for other in ordered
            if pair_key(other) != key and numeric[pair_key(other)] == vector
        ]
        options.append(
            {
                "rank": rank,
                "remove_team": dict(candidate["remove_team"]),
                "add_team": dict(candidate["add_team"]),
                "priority_vector": [
                    {"component": name, "value": value}
                    for name, value in priority_vector(candidate)
                ],
                "pareto_nondominated": not dominated_by,
                "dominated_by": dominated_by,
                "tied_on_objectives_with": ties,
                "provisional": bool(candidate["booking_evidence"].get("provisional")),
                "deltas": _deltas(candidate),
                "ranking_reason": candidate.get("ranking_reason"),
            }
        )

    excluded = [
        {
            "remove_team": dict(item["remove_team"]),
            "add_team": dict(item["add_team"]),
            "reasons": gate_reasons(
                item["verdict"].get("blockers") or [],
                item.get("refusals"),
                _evidence_from_candidate(item),
            ),
        }
        for item in illegal
    ]
    return {
        "contract": COMPARISON_CONTRACT,
        "ordering_policy": [name for name, _ in priority_vector(ordered[0])]
        if ordered
        else [],
        "status": "options" if options else "no_legal_option",
        "option_count": len(options),
        "pareto_choice_count": sum(1 for item in options if item["pareto_nondominated"]),
        "options": options,
        "excluded": excluded,
    }


def compare_replacement_candidates(
    service,
    *,
    season: str,
    tournament_id: str,
    replace_team_label: str | None = None,
    eligible_clubs: list[str] | None = None,
    eligible_team_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Read-only comparison of the replacement pool for one tournament."""

    report = search_participant_replacements(
        service,
        season=season,
        tournament_id=tournament_id,
        replace_team_label=replace_team_label,
        eligible_clubs=eligible_clubs,
        eligible_team_labels=eligible_team_labels,
    )
    comparison = compare_candidates(report["candidates"])
    return {
        "season": season,
        "revision": report["revision"],
        "canonical_state_revision": report["canonical_state_revision"],
        "tournament_id": tournament_id,
        "age_group": report["age_group"],
        "date": report["date"],
        "replace_team": replace_team_label,
        "filters": report["filters"],
        "booking_evidence": report["booking_evidence"],
        "provisional": bool(report["booking_evidence"].get("provisional")),
        "scope": {
            "pool": "registered same-age teams under the given filters",
            "exhaustive": False,
        },
        "read_only": True,
        **comparison,
    }


__all__ = [
    "COMPARISON_CONTRACT",
    "blocker_reasons",
    "compare_candidates",
    "gate_reasons",
    "compare_replacement_candidates",
    "exclusion_reason",
    "pair_key",
]

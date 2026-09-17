"""Independently checkable hosting-responsibility semantics (issue #361).

``hosting_coverage.hosting_balance_matrix`` already derives the canonical
target / actual / assigned / manual-placement ledger for every club x
age-group from the registration facts + candidate. This module is the
planner-independent *semantic owner* on top of that ledger: it answers
whether a candidate change kept hosting responsibility with the clubs the
fairness model assigned it to, or silently moved burden to another club.

The required invariant is generic (every age group, every club or
shared-registration hosting responsibility, every planner/repair path):

    Decide who is responsible for hosting from the registration/fairness
    model first. Calendar/ice availability decides whether that
    responsibility can be placed automatically; it must not silently decide
    who becomes responsible instead.

A club whose *physical* hosting exceeds its canonical target has absorbed
hosting responsibility it does not owe. Because targets are derived from the
same registration facts and the same tournament volume, that excess is the
deterministic, planner-independent signature of an unexplained responsibility
transfer: if it grows after a candidate-changing operation and no canonical
target changed, another club's obligation was moved onto this one.

The functions here take plain ``problem``/``candidate`` dicts (the same
contracts ``planning_contract.verify_candidate`` consumes), so any candidate
boundary -- Stage 3 repair providers, bounded search, candidate adoption, a
future solver/MCP action -- can re-check the same rule without importing a
planner or the interactive session.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Tuple

from tournament_scheduler.hosting_coverage import hosting_balance_matrix

# Stable finding code shared by every consumer (provider rejection evidence,
# Stage 3 transition guard, review evidence).
RESPONSIBILITY_TRANSFER_CODE = "unexplained_hosting_responsibility_transfer"


def hosting_responsibility_facts(
    problem: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
) -> List[Dict[str, Any]]:
    """Canonical responsibility facts per club x age-group for one candidate.

    Delegates the proportional/coverage math to
    ``hosting_coverage.hosting_balance_matrix`` and re-projects it into a
    stable, JSON-serializable shape so callers never depend on the internal
    reporting row layout.
    """
    teams = (problem or {}).get("teams") or []
    tournaments = (candidate or {}).get("tournaments") or []
    return [
        {
            "club": row["club"],
            "age_group": row["age_group"],
            "target": int(row.get("target", 0)),
            "actual": int(row.get("actual", 0)),
            "excess": int(row.get("excess", 0)),
            "deficit": int(row.get("deficit", 0)),
            "assigned_responsibility": int(row.get("assigned_responsibility", 0)),
            "placed_automatically": int(row.get("placed_automatically", 0)),
            "manual_placement_required": int(row.get("manual_placement_required", 0)),
            "coverage_unresolved": bool(row.get("coverage_unresolved")),
        }
        for row in hosting_balance_matrix(teams, tournaments)
    ]


def unexplained_responsibility_transfers(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
) -> List[Dict[str, Any]]:
    """Find responsibility moves a candidate change made *without* authorization.

    Compares the canonical target/actual ledger of *before* and *after* keyed
    by ``(age_group, club)``. A finding is emitted for a club whose physical
    hosting excess over its target grew. Because targets are canonical facts,
    a growing excess means this club absorbed hosting another club owes.

    A registration or tournament-volume change is *not* an unexplained
    placement transfer: the canonical fairness model itself changed, so the
    caller must recompute responsibility rather than treat the resulting
    physical redistribution as drift. Such age groups are skipped here (even
    when one club's rounded target happens to stay the same).

    The findings are stable dicts (club, age group, before/after target and
    actual) so a provider can reject the option, the Stage 3 boundary can
    refuse to commit it, and the review evidence can explain exactly what
    moved.
    """
    before_facts = _index_facts(hosting_responsibility_facts(problem, before))
    after_facts = _index_facts(hosting_responsibility_facts(problem, after))
    recomputed_age_groups = _age_groups_with_changed_canonical_facts(
        before, after, before_facts, after_facts
    )

    findings: List[Dict[str, Any]] = []
    for key, after_fact in after_facts.items():
        before_fact = before_facts.get(key)
        if before_fact is None:
            # No canonical row existed for this club/age group before, so the
            # registration facts changed rather than a placement moving.
            continue
        if after_fact["age_group"] in recomputed_age_groups:
            # The canonical fairness model changed for this age group;
            # responsibility was recomputed, not silently transferred by a
            # physical placement.
            continue
        if before_fact["target"] != after_fact["target"]:
            # The fair target itself changed; responsibility was recomputed,
            # not silently transferred by a physical placement.
            continue
        if after_fact["excess"] <= before_fact["excess"]:
            continue
        findings.append(
            {
                "code": RESPONSIBILITY_TRANSFER_CODE,
                "age_group": after_fact["age_group"],
                "club": after_fact["club"],
                "target": after_fact["target"],
                "actual_before": before_fact["actual"],
                "actual_after": after_fact["actual"],
                "excess_before": before_fact["excess"],
                "excess_after": after_fact["excess"],
                "message": (
                    f"{after_fact['club']} hosts {after_fact['actual']} {after_fact['age_group']} "
                    f"tournament(s) against a target of {after_fact['target']}; the change moved "
                    "hosting responsibility onto a club that does not owe it"
                ),
            }
        )
    return findings


def responsibility_regression_reason(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
) -> str:
    """Return the stable rejection reason when a change transfers burden, else ``""``."""
    if unexplained_responsibility_transfers(before, after, problem):
        return RESPONSIBILITY_TRANSFER_CODE
    return ""


def _index_facts(facts: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {(fact["age_group"], fact["club"]): fact for fact in facts}


def _age_groups_with_changed_canonical_facts(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    before_facts: Mapping[Tuple[str, str], Dict[str, Any]],
    after_facts: Mapping[Tuple[str, str], Dict[str, Any]],
) -> set[str]:
    """Age groups whose canonical responsibility model changed, not just placement.

    Compares registered clubs and active tournament volume per age group. When
    either differs the targets were recomputed from changed canonical facts, so
    physical redistribution inside that age group is not an unexplained
    transfer.
    """
    changed: set[str] = set()
    before_clubs = _clubs_by_age_group(before_facts)
    after_clubs = _clubs_by_age_group(after_facts)
    before_volume = _active_tournaments_by_age(before)
    after_volume = _active_tournaments_by_age(after)
    for age_group in set(before_clubs) | set(after_clubs):
        if before_clubs.get(age_group, set()) != after_clubs.get(age_group, set()):
            changed.add(age_group)
        if before_volume.get(age_group, 0) != after_volume.get(age_group, 0):
            changed.add(age_group)
    return changed


def _clubs_by_age_group(
    facts: Mapping[Tuple[str, str], Dict[str, Any]]
) -> Dict[str, set[str]]:
    result: Dict[str, set[str]] = {}
    for (age_group, club) in facts:
        result.setdefault(age_group, set()).add(club)
    return result


def _active_tournaments_by_age(candidate: Mapping[str, Any] | None) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for tournament in (candidate or {}).get("tournaments") or []:
        if not isinstance(tournament, Mapping) or tournament.get("cancelled"):
            continue
        age_group = str(tournament.get("age_group") or "")
        if age_group:
            counts[age_group] = counts.get(age_group, 0) + 1
    return counts

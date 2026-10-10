"""Tests for the independently checkable hosting-responsibility semantics (#361).

The generic invariant: calendar/ice availability decides whether the intended
host can be placed automatically; it must never silently move hosting
responsibility to another club. These tests use the JU12 regression shape
(Frisk Asker / Ringerike / Skien / Kongsberg-Tønsberg / Jutul-Jar) as one
fixture and synthetic shapes as the generic contract.
"""

from __future__ import annotations

from tournament_scheduler.hosting.responsibility import (
    RESPONSIBILITY_TRANSFER_CODE,
    hosting_responsibility_facts,
    responsibility_regression_reason,
    unexplained_responsibility_transfers,
)


def _team(club: str, age_group: str) -> dict:
    return {"club": club, "age_group": age_group, "label": f"{club} {age_group}"}


def _tournament(host_club: str, age_group: str = "JU12", **extra) -> dict:
    row = {
        "host_club": host_club,
        "age_group": age_group,
        "date": "2026-10-25",
        "arena": f"{host_club} Arena",
        "cancelled": False,
    }
    row.update(extra)
    return row


def _ju12_problem() -> dict:
    return {
        "teams": (
            [_team("Frisk Asker", "JU12"), _team("Frisk Asker", "JU12")]
            + [_team("Ringerike", "JU12"), _team("Ringerike", "JU12")]
            + [_team("Kongsberg/Tønsberg", "JU12")]
            + [_team("Skien", "JU12")]
            + [_team("Jutul/Jar Kittens", "JU12")]
        )
    }


def _ju12_candidate(host_counts: dict[str, int]) -> dict:
    return {
        "tournaments": [
            _tournament(host) for host, count in host_counts.items() for _ in range(count)
        ]
    }


def test_balanced_candidate_has_no_responsibility_excess():
    problem = _ju12_problem()
    candidate = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )

    facts = {fact["club"]: fact for fact in hosting_responsibility_facts(problem, candidate)}

    assert facts["Ringerike"]["target"] == 5
    assert facts["Ringerike"]["excess"] == 0
    assert all(fact["excess"] == 0 for fact in facts.values())


def test_manual_placement_keeps_intended_hosts_responsibility():
    """A manual placement counts as the intended host's responsibility."""
    problem = _ju12_problem()
    candidate = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2}
    )
    # Jutul/Jar still owes 2; represent it as Jutul manual placements.
    candidate["tournaments"].append(
        _tournament("Jutul", manual_booking_reason="ingen fri bane for vertsklubben")
    )
    candidate["tournaments"].append(
        _tournament("Jutul", manual_booking_reason="ingen fri bane for vertsklubben")
    )

    facts = {fact["club"]: fact for fact in hosting_responsibility_facts(problem, candidate)}

    assert facts["Jutul/Jar Kittens"]["target"] == 2
    assert facts["Jutul/Jar Kittens"]["actual"] == 2
    assert facts["Jutul/Jar Kittens"]["excess"] == 0
    assert facts["Jutul/Jar Kittens"]["manual_placement_required"] == 2


def test_convenience_rehost_onto_ringerike_is_an_unexplained_transfer():
    """The production regression: Jutul obligation absorbed by Ringerike ice."""
    problem = _ju12_problem()
    before = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    after = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 7, "Kongsberg": 2, "Skien": 2}
    )

    findings = unexplained_responsibility_transfers(before, after, problem)

    assert [finding["club"] for finding in findings] == ["Ringerike"]
    finding = findings[0]
    assert finding["code"] == RESPONSIBILITY_TRANSFER_CODE
    assert finding["age_group"] == "JU12"
    assert finding["actual_before"] == 5
    assert finding["actual_after"] == 7
    assert finding["target"] == 5
    assert finding["excess_after"] == 2
    assert responsibility_regression_reason(before, after, problem) == RESPONSIBILITY_TRANSFER_CODE


def test_repairing_a_deficit_is_not_a_transfer():
    """Moving hosting back to the owed club reduces excess instead of adding it."""
    problem = _ju12_problem()
    before = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 7, "Kongsberg": 2, "Skien": 1, "Jutul": 1}
    )
    after = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )

    assert unexplained_responsibility_transfers(before, after, problem) == []
    assert responsibility_regression_reason(before, after, problem) == ""


def test_same_club_manual_demotion_is_not_a_transfer():
    """Clearing a start time keeps the host, so responsibility has not moved."""
    problem = _ju12_problem()
    before = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    after = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 1}
    )
    after["tournaments"].append(
        _tournament("Jutul", manual_booking_reason="arena/time-kollisjon; må planlegges manuelt")
    )

    assert unexplained_responsibility_transfers(before, after, problem) == []


def test_canonical_target_change_is_not_a_placement_transfer():
    """An added tournament changes every target; that is a fairness recompute."""
    problem = _ju12_problem()
    before = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    after = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 6, "Kongsberg": 3, "Skien": 2, "Jutul": 2}
    )

    assert unexplained_responsibility_transfers(before, after, problem) == []


def test_transfer_between_non_ringerike_clubs_is_detected():
    """The rule is generic, not a Ringerike-specific special case."""
    problem = {"teams": [_team("Alpha", "U10"), _team("Beta", "U10")]}
    before = {
        "tournaments": [_tournament("Alpha", "U10"), _tournament("Beta", "U10")]
    }
    after = {
        "tournaments": [_tournament("Alpha", "U10"), _tournament("Alpha", "U10")]
    }

    findings = unexplained_responsibility_transfers(before, after, problem)

    assert [finding["club"] for finding in findings] == ["Alpha"]


def test_targets_are_stable_regardless_of_which_candidate_is_compared():
    problem = _ju12_problem()
    candidate = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    targets = {
        (fact["age_group"], fact["club"]): fact["target"]
        for fact in hosting_responsibility_facts(problem, candidate)
    }
    assert sum(targets.values()) == 16
    assert targets[("JU12", "Ringerike")] == 5
    assert targets[("JU12", "Skien")] == 2
    assert targets[("JU12", "Jutul/Jar Kittens")] == 2
    assert targets[("JU12", "Kongsberg/Tønsberg")] == 2


def test_unresolved_obligation_keeps_responsible_hosts_target_and_no_spurious_excess():
    """A failure to place an obligation must not re-proportion hosting burden.

    Without counting the unresolved obligation in the planned volume, the two
    physically placed tournaments shrink every target and the club that hosted
    them looks like it absorbed responsibility it does not owe. The unresolved
    obligation stays the responsible host's target instead.
    """

    problem = {
        "teams": [_team("Big", "U10"), _team("Big", "U10"), _team("Small", "U10")]
    }
    candidate = {
        "tournaments": [_tournament("Big", "U10"), _tournament("Big", "U10")],
        "unresolved_tournament_placements": [
            {
                "id": "unplaced_placement:U10:2026-10-10:1",
                "age_group": "U10",
                "date": "2026-10-10",
                "responsible_host": "Small",
            }
        ],
    }

    facts = {fact["club"]: fact for fact in hosting_responsibility_facts(problem, candidate)}

    assert facts["Big"]["target"] == 2
    assert facts["Big"]["excess"] == 0
    assert facts["Small"]["target"] == 1
    assert facts["Small"]["assigned_responsibility"] == 1
    assert facts["Small"]["deficit"] == 1


def test_cancelled_planned_tournament_does_not_transfer_responsibility():
    """A cancellation reduces physical hosting but not the planned target volume."""

    problem = {
        "teams": [_team("Big", "U10"), _team("Big", "U10"), _team("Small", "U10")]
    }
    cancelled = _tournament("Small", "U10", cancelled=True)
    candidate = {
        "tournaments": [
            _tournament("Big", "U10"),
            _tournament("Big", "U10"),
            cancelled,
        ]
    }

    facts = {fact["club"]: fact for fact in hosting_responsibility_facts(problem, candidate)}

    assert facts["Big"]["excess"] == 0
    assert facts["Small"]["deficit"] == 1

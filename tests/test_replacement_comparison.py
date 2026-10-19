"""Read-only replacement comparison and ineligibility explanations.

Pure fixtures pin the priority order, Pareto dominance, deterministic ties and
no-option output. Integration tests run the same capabilities against the
canonical replacement fixtures and assert that they never mutate state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_season_inspection import (
    _promote,
    _promote_replacement_ranking_fixture,
)
from tournament_scheduler.application.canonical_season.replacement_comparison import (
    compare_candidates,
    compare_replacement_candidates,
)
from tournament_scheduler.application.canonical_season.replacement_explanation import (
    explain_replacement_ineligibility,
)
from tournament_scheduler.application.canonical_season.store_facade import (
    _service,
    load_schedule,
)


def _explain(
    *,
    season: str,
    tournament_id: str,
    remove_team_label: str,
    add_team_label: str,
    root: Path,
    eligible_clubs: list[str] | None = None,
    eligible_team_labels: list[str] | None = None,
) -> dict[str, Any]:
    return explain_replacement_ineligibility(
        _service(root),
        season=season,
        tournament_id=tournament_id,
        remove_team_label=remove_team_label,
        add_team_label=add_team_label,
        eligible_clubs=eligible_clubs,
        eligible_team_labels=eligible_team_labels,
    )


def _compare(
    *,
    season: str,
    tournament_id: str,
    root: Path,
    replace_team_label: str | None = None,
    eligible_clubs: list[str] | None = None,
    eligible_team_labels: list[str] | None = None,
) -> dict[str, Any]:
    return compare_replacement_candidates(
        _service(root),
        season=season,
        tournament_id=tournament_id,
        replace_team_label=replace_team_label,
        eligible_clubs=eligible_clubs,
        eligible_team_labels=eligible_team_labels,
    )


def _fairness(shortfall_before: float, shortfall_after: float = 0.0) -> dict[str, Any]:
    return {
        "before": {"normalized_shortfall": shortfall_before},
        "after": {"normalized_shortfall": shortfall_after},
    }


def _candidate(
    add: str,
    *,
    applicable: bool = True,
    shortfall: float = 1.0,
    net_improvement_clubs: float = 0.0,
    donor_half_loss: bool = False,
    recipient_halves: tuple[int, int] = (1, 1),
    back_to_back: int = 0,
    gaps_7: int = 0,
    travel_km: float = 0.0,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    removed_before = {"before_christmas": 1, "after_christmas": 1, "max_inactive_gap_days": 10}
    removed_after = {
        "before_christmas": 0 if donor_half_loss else 1,
        "after_christmas": 1,
        "max_inactive_gap_days": 10,
    }
    return {
        "remove_team": {"club": "Donor", "label": "D1", "age_group": "U10"},
        "add_team": {"club": add, "label": f"{add}1", "age_group": "U10"},
        "verdict": {"applicable": applicable, "blockers": list(blockers or [])},
        "safe_to_apply": applicable,
        "blockers": list(blockers or []),
        "ranking_reason": "fixture",
        "booking_evidence": {"provisional": False},
        "club_age_booked_participation": {
            "added_club": _fairness(shortfall, shortfall - net_improvement_clubs),
            "removed_club": _fairness(0.0, 0.0),
        },
        "affected_team_booked_coverage": {
            "removed_team": {"before": removed_before, "after": removed_after},
            "added_team": {
                "after": {
                    "before_christmas": recipient_halves[0],
                    "after_christmas": recipient_halves[1],
                    "max_inactive_gap_days": 0,
                }
            },
        },
        "spacing_clustering_delta": {
            "back_to_back_weekends": back_to_back,
            "gaps_under_7_days": gaps_7,
            "gaps_under_14_days": 0,
        },
        "opponent_exposure_delta": {"material_regressions": [], "distinct_clubs_delta": 0},
        "travel_delta": {"warnings": [], "delta_km": travel_km},
        "soft_preference_effects": {"host_sibling_violations_delta": 0},
        "active_protection_request_effects": {},
    }


def test_compare_orders_by_named_priority_not_scalar_sum() -> None:
    # Shortfall is the same, so the donor losing a half season must decide the
    # order even though the other candidate has worse cadence.
    donor_loss = _candidate("G", donor_half_loss=True)
    cadence_worse = _candidate("W", back_to_back=3, gaps_7=4)

    result = compare_candidates([donor_loss, cadence_worse])

    assert [option["add_team"]["club"] for option in result["options"]] == ["W", "G"]
    assert result["status"] == "options"
    first = result["options"][0]
    assert first["priority_vector"][0] == {"component": "legality", "value": 0}
    assert first["priority_vector"][1] == {"component": "donor_half_season_losses", "value": 0}


def test_pareto_marks_trade_offs_as_nondominated_and_dominated_choices() -> None:
    # G is strictly better on every objective than X: X is dominated.
    # W trades cadence for travel against G, so neither dominates the other.
    g = _candidate("G", shortfall=2.0, back_to_back=0, travel_km=5.0)
    x = _candidate("X", shortfall=1.0, back_to_back=2, travel_km=9.0)
    w = _candidate("W", shortfall=2.0, back_to_back=1, travel_km=1.0)

    result = compare_candidates([x, g, w])
    by_club = {option["add_team"]["club"]: option for option in result["options"]}

    assert by_club["X"]["pareto_nondominated"] is False
    assert set(by_club["X"]["dominated_by"]) == {"D1->G1", "D1->W1"}
    assert by_club["G"]["pareto_nondominated"] is True
    assert by_club["W"]["pareto_nondominated"] is True
    assert result["pareto_choice_count"] == 2


def test_identical_objectives_tie_deterministically_on_identity() -> None:
    first = _candidate("G")
    second = _candidate("W")

    forward = compare_candidates([first, second])
    reverse = compare_candidates([second, first])

    assert [o["add_team"]["label"] for o in forward["options"]] == ["G1", "W1"]
    assert [o["add_team"]["label"] for o in reverse["options"]] == ["G1", "W1"]
    assert forward["options"][0]["tied_on_objectives_with"] == ["D1->W1"]
    assert forward["options"][0]["pareto_nondominated"] is True
    assert forward["options"][1]["pareto_nondominated"] is True


def test_no_legal_option_reports_owner_tagged_exclusions_without_options() -> None:
    blocked = _candidate(
        "E",
        applicable=False,
        blockers=["consequences_acceptable", "Team is scheduled in 2 tournaments"],
    )

    result = compare_candidates([blocked])

    assert result["status"] == "no_legal_option"
    assert result["options"] == []
    assert result["ordering_policy"] == []
    reasons = result["excluded"][0]["reasons"]
    assert reasons[0] == {
        "code": "consequences_acceptable",
        "classification": "hard_ineligible",
        "owner": "participation.team_schedule_consequence",
        "evidence": [],
    }
    assert reasons[1]["code"] == "replacement_precondition"
    assert reasons[1]["classification"] == "hard_ineligible"


def test_comparison_is_read_only_and_matches_ranking_on_registered_fixture(
    tmp_path: Path,
) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)
    revision = load_schedule("2026-2027", root=root)["revision"]

    result = _compare(
        season="2026-2027",
        tournament_id="t1",
        replace_team_label="D1",
        eligible_clubs=["G", "W"],
        root=root,
    )

    assert result["read_only"] is True
    assert result["scope"]["exhaustive"] is False
    assert [o["add_team"]["club"] for o in result["options"]] == ["G", "W"]
    assert result["options"][0]["rank"] == 1
    assert load_schedule("2026-2027", root=root)["revision"] == revision


def test_donor_protection_still_decides_before_recipient_fairness(tmp_path: Path) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)

    result = _compare(
        season="2026-2027", tournament_id="t1", root=root
    )
    by_removed = {}
    for option in result["options"]:
        by_removed.setdefault(option["remove_team"]["label"], option)

    # Removing D1 loses its only booked first-half appearance; removing B1 does
    # not. Donor half-season coverage is ranked above recipient allocation.
    assert by_removed["B1"]["rank"] < by_removed["D1"]["rank"]
    donor = by_removed["D1"]["priority_vector"][1]
    assert donor == {"component": "donor_half_season_losses", "value": 1}


def test_explain_reports_legal_pair_with_dominance_context(tmp_path: Path) -> None:
    root = _promote(tmp_path)
    revision = load_schedule("2026-2027", root=root)["revision"]

    explanation = _explain(
        season="2026-2027",
        tournament_id="t1",
        remove_team_label="D1",
        add_team_label="G1",
        root=root,
    )

    assert explanation["status"] in {"legal_pareto_choice", "legal_dominated"}
    assert explanation["reasons"] == []
    assert explanation["scope"]["exhaustive"] is False
    assert load_schedule("2026-2027", root=root)["revision"] == revision


def test_explain_names_canonical_gate_for_blocked_pair(tmp_path: Path) -> None:
    root = _promote(tmp_path)

    explanation = _explain(
        season="2026-2027",
        tournament_id="t1",
        remove_team_label="D1",
        add_team_label="E1",
        root=root,
    )

    # E1 is already scheduled in another tournament on this date. The dry-run
    # raises that as a precondition before any gate verdict, so it is reported
    # as a precondition with the canonical message, not as a gate.
    assert explanation["status"] == "hard_ineligible"
    [reason] = explanation["reasons"]
    assert reason["code"] == "replacement_precondition"
    assert reason["owner"] == "canonical_season.replacement_preconditions"
    assert any("scheduled in 2 tournaments" in str(item) for item in reason["evidence"])
    assert explanation["dominated_by"] == []


def test_explain_distinguishes_out_of_scope_from_hard_ineligible(tmp_path: Path) -> None:
    root = _promote(tmp_path)

    not_participant = _explain(
        season="2026-2027",
        tournament_id="t1",
        remove_team_label="Not In Tournament",
        add_team_label="G1",
        root=root,
    )
    assert not_participant["status"] == "out_of_scope"
    assert not_participant["reasons"][0]["code"] == "not_a_removable_participant"

    other_age_group = _explain(
        season="2026-2027",
        tournament_id="t1",
        remove_team_label="D1",
        add_team_label="F1",
        root=root,
    )
    assert other_age_group["status"] == "hard_ineligible"
    assert other_age_group["reasons"][0]["code"] == "not_a_registered_same_age_team"

    already_playing = _explain(
        season="2026-2027",
        tournament_id="t1",
        remove_team_label="D1",
        add_team_label="A1",
        root=root,
    )
    assert already_playing["status"] == "hard_ineligible"
    assert already_playing["reasons"][0]["code"] == "already_participant"


def test_explain_search_filter_exclusion_is_out_of_scope(tmp_path: Path) -> None:
    root = _promote(tmp_path)

    explanation = _explain(
        season="2026-2027",
        tournament_id="t1",
        remove_team_label="D1",
        add_team_label="G1",
        eligible_clubs=["Nobody"],
        root=root,
    )

    assert explanation["status"] == "out_of_scope"
    assert explanation["reasons"][0]["code"] == "outside_search_filters"
    assert explanation["reasons"][0]["owner"] == (
        "canonical_season.replacement_search_filters"
    )


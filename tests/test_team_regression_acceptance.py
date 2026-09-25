"""Explicit operator acceptance of named team-schedule regressions.

The per-team consequence gate stays a default refusal. An operator may accept
one exact regression code for one named team, with a mandatory reason; any
other material regression still refuses, and an acceptance that matches
nothing in the candidate is itself refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_canonical_batch_maintenance import (
    _candidate,
    _decisions_bytes,
    _promote,
    _schedule_bytes,
    _tournament,
    _tournaments_by_id,
)
from tournament_scheduler.season_state import (
    SeasonStateError,
    batch_maintenance,
    load_decisions,
    swap_participants,
)
from tournament_scheduler.team_schedule_quality import (
    RegressionAcceptanceError,
    evaluate_regression_acceptances,
    parse_regression_acceptances,
)

SEASON = "2026-2027"
GAP_CODE = "more_gaps_under_7_days"


def _analysis(label: str, *codes: str, club: str = "C", age_group: str = "U10") -> dict:
    team = {"club": club, "label": label, "age_group": age_group}
    return {
        "acceptable": not codes,
        "material_regressions": [{"code": code} for code in codes],
        "before": {"team": dict(team)},
        "after": {"team": dict(team)},
    }


def test_parse_requires_reason_and_known_quality_code() -> None:
    assert parse_regression_acceptances(None, None) == []
    with pytest.raises(RegressionAcceptanceError, match="reason"):
        parse_regression_acceptances([f"W1={GAP_CODE}"], "  ")
    with pytest.raises(RegressionAcceptanceError, match="cannot be accepted"):
        parse_regression_acceptances(["W1=participation_hard_max_exceeded"], "ok")
    with pytest.raises(RegressionAcceptanceError, match="TEAM|team label"):
        parse_regression_acceptances(["W1"], "ok")
    parsed = parse_regression_acceptances(
        [f"Frisk Asker 2={GAP_CODE}", {"team": "W1", "code": "travel_materially_worse"}],
        "club agreed",
    )
    assert parsed == [
        {"team": "Frisk Asker 2", "code": GAP_CODE, "reason": "club agreed"},
        {"team": "W1", "code": "travel_materially_worse", "reason": "club agreed"},
    ]


def test_evaluate_accepts_only_the_named_team_and_code() -> None:
    consequences = {
        "a:W1": _analysis("W1", GAP_CODE, "travel_materially_worse"),
        "b:Y1": _analysis("Y1", GAP_CODE),
    }
    acceptances = parse_regression_acceptances([f"W1={GAP_CODE}"], "operator accepts")
    result = evaluate_regression_acceptances(consequences, acceptances)

    assert result["acceptable"] is False
    assert [(item["team"], item["code"]) for item in result["accepted_regressions"]] == [
        ("W1", GAP_CODE)
    ]
    assert {(item["team"], item["code"]) for item in result["unaccepted_regressions"]} == {
        ("W1", "travel_materially_worse"),
        ("Y1", GAP_CODE),
    }


def test_evaluate_rejects_acceptance_that_matches_nothing() -> None:
    acceptances = parse_regression_acceptances([f"Z1={GAP_CODE}"], "pre-emptive")
    result = evaluate_regression_acceptances({"a:W1": _analysis("W1")}, acceptances)

    assert result["acceptable"] is False
    assert result["unmatched_acceptances"] == [
        {"acceptance": f"Z1={GAP_CODE}", "code": GAP_CODE}
    ]


def test_parse_accepts_fully_qualified_identity() -> None:
    parsed = parse_regression_acceptances([f"Ringerike|Ringerike 2|U11={GAP_CODE}"], "ok")
    assert parsed == [
        {
            "team": "Ringerike 2",
            "code": GAP_CODE,
            "reason": "ok",
            "club": "Ringerike",
            "age_group": "U11",
        }
    ]
    with pytest.raises(RegressionAcceptanceError, match="club"):
        parse_regression_acceptances([f"Ringerike|Ringerike 2={GAP_CODE}"], "ok")


def test_evaluate_refuses_label_shared_by_several_affected_teams() -> None:
    consequences = {
        "a:Ringerike 2": _analysis("Ringerike 2", GAP_CODE, club="Ringerike", age_group="U11"),
        "b:Ringerike 2": _analysis("Ringerike 2", GAP_CODE, club="Ringerike", age_group="U12"),
    }
    ambiguous = evaluate_regression_acceptances(
        consequences, parse_regression_acceptances([f"Ringerike 2={GAP_CODE}"], "ok")
    )
    assert ambiguous["acceptable"] is False
    assert ambiguous["accepted_regressions"] == []
    assert ambiguous["ambiguous_acceptances"][0]["candidates"] == [
        "Ringerike|Ringerike 2|U11",
        "Ringerike|Ringerike 2|U12",
    ]

    qualified = evaluate_regression_acceptances(
        consequences,
        parse_regression_acceptances([f"Ringerike|Ringerike 2|U11={GAP_CODE}"], "ok"),
    )
    assert [(item["age_group"], item["code"]) for item in qualified["accepted_regressions"]] == [
        ("U11", GAP_CODE)
    ]
    assert [item["age_group"] for item in qualified["unaccepted_regressions"]] == ["U12"]


def _regressing_candidate() -> dict:
    """Swapping W1 (B, 20 Sep) into A (12 Sep) puts W1 one day before D (13 Sep)."""

    candidate = _candidate()
    candidate["tournaments"].append(
        _tournament(
            "u10-d-20260913",
            "2026-09-13",
            "E",
            "Arena D",
            [("E", "E1"), ("E", "E2"), ("W", "W1"), ("F", "F1")],
            "10:00",
        )
    )
    return candidate


def _swap_operation() -> dict:
    return {
        "op": "swap_participants",
        "tournament_a": "u10-a-20260912",
        "team_a": "Y1",
        "tournament_b": "u10-b-20260920",
        "team_b": "W1",
    }


def _batch(root: Path, **kwargs) -> dict:
    return batch_maintenance(
        season=SEASON,
        root=root,
        operations=[_swap_operation()],
        scope=["u10-a-20260912", "u10-b-20260920"],
        request_id="accept-regression",
        actor="tester",
        **kwargs,
    )


def test_batch_refuses_regression_by_default(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path, candidate=_regressing_candidate())
    before = (_schedule_bytes(root), _decisions_bytes(root))

    preview = _batch(root, dry_run=True)
    assert preview["consequence_acceptable"] is False
    assert ("W1", GAP_CODE) in {
        (item["team"], item["code"])
        for item in preview["regression_acceptance"]["unaccepted_regressions"]
    }
    with pytest.raises(SeasonStateError, match="materially worsens"):
        _batch(root)
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_batch_commits_with_explicit_acceptance_and_records_it(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path, candidate=_regressing_candidate())

    with pytest.raises(SeasonStateError, match="reason"):
        _batch(root, accept_regressions=[f"W1={GAP_CODE}"])

    result = _batch(
        root,
        accept_regressions=[f"W1={GAP_CODE}"],
        accept_regression_reason="W club accepts back-to-back weekend",
    )
    assert result["committed"] is True
    assert result["consequence_acceptable"] is True
    assert {team["label"] for team in _tournaments_by_id(root)["u10-a-20260912"]["teams"]} >= {
        "W1"
    }

    batches = [
        event
        for event in load_decisions(SEASON, root=root).get("history", [])
        if event.get("event") == "batch_maintenance"
    ]
    accepted = batches[-1]["details"]["accepted_regressions"]
    assert [(item["team"], item["code"], item["reason"]) for item in accepted] == [
        ("W1", GAP_CODE, "W club accepts back-to-back weekend")
    ]


def test_batch_refuses_unmatched_acceptance(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path, candidate=_regressing_candidate())
    before = (_schedule_bytes(root), _decisions_bytes(root))

    kwargs = dict(
        accept_regressions=[f"W1={GAP_CODE}", "Y1=travel_materially_worse"],
        accept_regression_reason="blanket",
    )
    preview = _batch(root, dry_run=True, **kwargs)
    assert preview["consequence_acceptable"] is False
    assert preview["regression_acceptance"]["acceptable"] is False
    with pytest.raises(SeasonStateError, match="match no material regression"):
        _batch(root, **kwargs)
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_swap_participants_honours_the_same_acceptance(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path, candidate=_regressing_candidate())
    kwargs = dict(
        season=SEASON,
        root=root,
        tournament_a_id="u10-a-20260912",
        team_a_label="Y1",
        tournament_b_id="u10-b-20260920",
        team_b_label="W1",
        request_id="accept-regression-swap",
        actor="tester",
    )

    with pytest.raises(SeasonStateError, match="materially worsens"):
        swap_participants(**kwargs)

    result = swap_participants(
        **kwargs,
        accept_regressions=[f"W1={GAP_CODE}"],
        accept_regression_reason="W club accepts back-to-back weekend",
    )
    assert result["dry_run"] is False
    accepted = result["swap"]["regression_acceptance"]["accepted_regressions"]
    assert [(item["team"], item["code"]) for item in accepted] == [("W1", GAP_CODE)]

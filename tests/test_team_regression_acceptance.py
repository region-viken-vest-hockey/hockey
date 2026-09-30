"""Explicit operator acceptance of named team-schedule regressions.

The per-team consequence gate stays a default refusal. An operator may accept
one exact regression code for one named team, with a mandatory reason; any
other material regression still refuses, and an acceptance that matches
nothing in the candidate is itself refused.
"""

from __future__ import annotations

import json
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
from tests.test_participant_removal import _write_canonical
from tournament_scheduler.season_state import (
    SeasonStateError,
    batch_maintenance,
    load_decisions,
    swap_participants,
)
from tournament_scheduler.team_schedule_quality import (
    CANCEL_ACCEPTABLE_REGRESSION_CODES,
    PARTICIPATION_COUNT_CHANGED,
    RegressionAcceptanceError,
    evaluate_regression_acceptances,
    parse_regression_acceptances,
)

SEASON = "2026-2027"
GAP_CODE = "more_gaps_under_7_days"
PARTICIPATION_CODE = PARTICIPATION_COUNT_CHANGED


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


def test_batch_analyses_each_same_label_team_passing_through_one_tournament(
    tmp_path: Path,
) -> None:
    # Two distinct teams labelled "Common" (clubs P and Q) pass through A in
    # one chained batch; each must keep its own consequence analysis.
    candidate = _candidate()
    tournaments = {tournament["id"]: tournament for tournament in candidate["tournaments"]}
    tournaments["u10-a-20260912"] = _tournament(
        "u10-a-20260912",
        "2026-09-12",
        "Kongsberg",
        "Arena A",
        [("Kongsberg", "K1"), ("X", "X1"), ("P", "Common"), ("Z", "Z1")],
        "10:00",
    )
    tournaments["u10-c-20261018"] = _tournament(
        "u10-c-20261018",
        "2026-10-18",
        "C",
        "Arena C",
        [("C", "C1"), ("C", "C2"), ("D", "D1"), ("Q", "Common")],
        "14:00",
    )
    tournaments["u10-d-20261101"] = _tournament(
        "u10-d-20261101",
        "2026-11-01",
        "E",
        "Arena D",
        [("E", "E1"), ("E", "E2"), ("F", "F1"), ("T", "T1")],
        "10:00",
    )
    candidate["tournaments"] = list(tournaments.values())
    _work_dir, root = _promote(tmp_path, candidate=candidate)

    operations = [
        {"op": "swap_participants", "tournament_a": "u10-a-20260912", "team_a": "Common",
         "tournament_b": "u10-b-20260920", "team_b": "W1"},
        {"op": "swap_participants", "tournament_a": "u10-a-20260912", "team_a": "W1",
         "tournament_b": "u10-c-20261018", "team_b": "Common"},
        {"op": "swap_participants", "tournament_a": "u10-a-20260912", "team_a": "Common",
         "tournament_b": "u10-d-20261101", "team_b": "T1"},
    ]
    preview = batch_maintenance(
        season=SEASON,
        root=root,
        operations=operations,
        scope=["u10-a-20260912", "u10-b-20260920", "u10-c-20261018", "u10-d-20261101"],
        request_id="chained-same-label",
        actor="tester",
        dry_run=True,
    )

    analysed = set(preview["team_consequences"])
    assert {"P|Common|U10", "Q|Common|U10", "W|W1|U10", "T|T1|U10"} <= analysed
    by_identity = preview["team_consequences"]
    assert "2026-09-20" in by_identity["P|Common|U10"]["after"]["tournament_dates"]
    assert "2026-11-01" in by_identity["Q|Common|U10"]["after"]["tournament_dates"]


def test_parse_allows_participation_code_only_when_explicitly_enabled() -> None:
    with pytest.raises(RegressionAcceptanceError, match="cannot be accepted"):
        parse_regression_acceptances([f"K1={PARTICIPATION_CODE}"], "operator reason")

    parsed = parse_regression_acceptances(
        [f"K1={PARTICIPATION_CODE}"],
        "operator reason",
        allowed_codes=CANCEL_ACCEPTABLE_REGRESSION_CODES,
    )
    assert parsed == [{"team": "K1", "code": PARTICIPATION_CODE, "reason": "operator reason"}]


def test_evaluate_scopes_participation_acceptance_to_cancelled_participants() -> None:
    consequences = {"a:K1": _analysis("K1", PARTICIPATION_CODE, club="Kongsberg")}
    acceptance = parse_regression_acceptances(
        [f"K1={PARTICIPATION_CODE}"],
        "operator reason",
        allowed_codes=CANCEL_ACCEPTABLE_REGRESSION_CODES,
    )

    eligible = evaluate_regression_acceptances(
        consequences,
        acceptance,
        code_scope={PARTICIPATION_CODE: {("Kongsberg", "K1", "U10")}},
    )
    assert eligible["acceptable"] is True
    assert [(item["team"], item["code"]) for item in eligible["accepted_regressions"]] == [
        ("K1", PARTICIPATION_CODE)
    ]

    ineligible = evaluate_regression_acceptances(
        consequences, acceptance, code_scope={PARTICIPATION_CODE: set()}
    )
    assert ineligible["acceptable"] is False
    assert ineligible["ineligible_acceptances"] == [
        {
            "acceptance": f"K1={PARTICIPATION_CODE}",
            "code": PARTICIPATION_CODE,
            "team": "K1",
            "club": "Kongsberg",
            "age_group": "U10",
        }
    ]


def _cancel_and_remove_operations() -> list[dict]:
    # Cancel u10-b and reconcile Echo's withdrawal from u10-a in one batch.
    # Every remaining u10-a participant also played u10-b, so each loses exactly
    # that cancelled appearance while Echo's own shortfall is the withdrawal.
    return [
        {"op": "cancel", "tournament_id": "u10-b", "reason": "host team retired"},
        {
            "op": "remove_participant",
            "tournament_id": "u10-a",
            "remove_team": "Echo 1",
            "reconcile_withdrawal": True,
        },
    ]


def _cancel_and_remove_batch(root: Path, **kwargs) -> dict:
    return batch_maintenance(
        season=SEASON,
        root=root,
        operations=_cancel_and_remove_operations(),
        scope=["u10-a", "u10-b"],
        request_id="cancel-participation-acceptance",
        actor="tester",
        **kwargs,
    )


def _cancelled_participants() -> set[tuple[str, str]]:
    return {
        (club, f"{club} 1")
        for club in ("Alfa", "Bravo", "Charlie", "Delta")
    }


def test_batch_requires_acceptance_for_a_cancellation_shortfall(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    before = (_schedule_bytes(root), _decisions_bytes(root))

    preview = _cancel_and_remove_batch(root, dry_run=True)
    assert preview["consequence_acceptable"] is False
    assert {
        (item["team"], item["code"])
        for item in preview["regression_acceptance"]["unaccepted_regressions"]
    } == {(team, PARTICIPATION_CODE) for _club, team in _cancelled_participants()}

    with pytest.raises(SeasonStateError, match="materially worsens"):
        _cancel_and_remove_batch(root)
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_batch_commits_cancellation_shortfall_with_explicit_acceptance(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)

    with pytest.raises(SeasonStateError, match="reason"):
        _cancel_and_remove_batch(
            root,
            accept_regressions=["Alfa|Alfa 1|U10=" + PARTICIPATION_CODE],
        )

    acceptances = [
        f"{club}|{team}|U10={PARTICIPATION_CODE}"
        for club, team in sorted(_cancelled_participants())
    ]
    result = _cancel_and_remove_batch(
        root,
        accept_regressions=acceptances,
        accept_regression_reason="the host team retires; the remaining U10 field accepts one fewer appearance",
    )
    assert result["committed"] is True
    assert result["consequence_acceptable"] is True
    accepted = result["regression_acceptance"]["accepted_regressions"]
    assert {(item["team"], item["code"]) for item in accepted} == {
        (team, PARTICIPATION_CODE) for _club, team in _cancelled_participants()
    }

    by_id = _tournaments_by_id(root)
    assert by_id["u10-b"]["cancelled"] is True
    assert "Echo 1" not in {team["label"] for team in by_id["u10-a"]["teams"]}

    batches = [
        event
        for event in load_decisions(SEASON, root=root).get("history", [])
        if event.get("event") == "batch_maintenance"
    ]
    recorded = batches[-1]["details"]["accepted_regressions"]
    assert {(item["team"], item["code"]) for item in recorded} == {
        (team, PARTICIPATION_CODE) for _club, team in _cancelled_participants()
    }


def test_batch_without_cancel_rejects_participation_code(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path, candidate=_regressing_candidate())
    with pytest.raises(SeasonStateError, match="cannot be accepted"):
        _batch(
            root,
            accept_regressions=[f"W1={PARTICIPATION_CODE}"],
            accept_regression_reason="blanket override",
        )


def _cancel_only_batch(root: Path, **kwargs) -> dict:
    return batch_maintenance(
        season=SEASON,
        root=root,
        operations=[{"op": "cancel", "tournament_id": "u10-b", "reason": "host team retired"}],
        scope=["u10-b"],
        request_id="cancel-only-acceptance",
        actor="tester",
        **kwargs,
    )


def _cancel_only_participants() -> set[tuple[str, str]]:
    return {(club, f"{club} 1") for club in ("Alfa", "Bravo", "Charlie", "Delta", "Echo")}


def test_cancel_only_batch_requires_participation_acceptance(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    before = (_schedule_bytes(root), _decisions_bytes(root))

    preview = _cancel_only_batch(root, dry_run=True)
    assert preview["refused"] is True
    assert preview["consequence_acceptable"] is False
    assert {
        (item["team"], item["code"])
        for item in preview["regression_acceptance"]["unaccepted_regressions"]
    } == {(team, PARTICIPATION_CODE) for _club, team in _cancel_only_participants()}

    with pytest.raises(SeasonStateError, match="materially worsens"):
        _cancel_only_batch(root)
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before

    acceptances = [
        f"{club}|{team}|U10={PARTICIPATION_CODE}"
        for club, team in sorted(_cancel_only_participants())
    ]
    result = _cancel_only_batch(
        root,
        accept_regressions=acceptances,
        accept_regression_reason="host team retires; the whole U10 field accepts one fewer appearance",
    )
    assert result["committed"] is True
    assert result["consequence_acceptable"] is True
    assert _tournaments_by_id(root)["u10-b"]["cancelled"] is True


def test_cancel_acceptance_cannot_cover_a_withdrawn_team(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    # Echo is withdrawn from u10-a in the same batch, so its own cancellation
    # loss is the deliberate withdrawal decision and must not be waivable with
    # a bare participation_count_changed acceptance.
    acceptances = [
        f"{club}|{team}|U10={PARTICIPATION_CODE}"
        for club, team in sorted(_cancelled_participants())
    ] + [f"Echo|Echo 1|U10={PARTICIPATION_CODE}"]

    with pytest.raises(SeasonStateError, match="match no material regression"):
        _cancel_and_remove_batch(
            root,
            accept_regressions=acceptances,
            accept_regression_reason="mixed withdrawal and cancellation",
        )


def test_cancel_acceptance_scope_rejects_an_extra_unrelated_loss() -> None:
    from tournament_scheduler.application.canonical_season.batch import (
        _participation_acceptance_scope,
    )

    # Synthetic mixed-operation consequences: K1 loses exactly the one
    # cancelled appearance, X1 loses two (one cancellation + one unrelated
    # count change), and Y1 has no participation regression.
    attributable = _analysis("K1", PARTICIPATION_CODE, club="Kongsberg")
    attributable["material_regressions"] = [
        {"code": PARTICIPATION_CODE, "before": 3, "after": 2}
    ]
    unattributable = _analysis("X1", PARTICIPATION_CODE, club="X")
    unattributable["material_regressions"] = [
        {"code": PARTICIPATION_CODE, "before": 3, "after": 1}
    ]

    scope = _participation_acceptance_scope(
        {
            "a:K1": attributable,
            "b:X1": unattributable,
            "c:Y1": _analysis("Y1", club="Y"),
        },
        {("Kongsberg", "K1", "U10"): 1, ("X", "X1", "U10"): 1},
    )
    assert scope == {("Kongsberg", "K1", "U10")}


# --- Reviewed-consequence acceptance (#586) ---------------------------------
#
# A modern cancellation dry-run reports the exact attributable consequence set
# and a token binding that candidate. The operator can review it once and apply
# the same plan unchanged with one flag instead of reconstructing dozens of
# per-team `--accept-team-regression` arguments.


def test_reviewed_consequence_token_round_trips_the_exact_plan(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)

    preview = _cancel_only_batch(root, dry_run=True)
    review = preview["verdict"]["review"]
    assert review["required"] is True
    assert isinstance(review["token"], str) and review["token"].startswith("rc-")
    reviewed = {
        (item["club"], item["team"], item["age_group"], item["code"])
        for item in review["consequences"]
    }
    assert reviewed == {
        (club, team, "U10", PARTICIPATION_CODE)
        for club, team in sorted(_cancel_only_participants())
    }

    result = _cancel_only_batch(
        root,
        accept_reviewed_consequences=review["token"],
        accept_regression_reason="host retires; reviewed one-appearance loss for the U10 field",
    )
    assert result["committed"] is True
    assert result["consequence_acceptable"] is True
    accepted = {
        (item["club"], item["team"], item["age_group"], item["code"])
        for item in result["regression_acceptance"]["accepted_regressions"]
    }
    assert accepted == reviewed
    assert _tournaments_by_id(root)["u10-b"]["cancelled"] is True

    batches = [
        event
        for event in load_decisions(SEASON, root=root).get("history", [])
        if event.get("event") == "batch_maintenance"
    ]
    assert batches[-1]["details"]["accepted_regressions"]


def test_reviewed_consequence_token_requires_a_reason(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    token = _cancel_only_batch(root, dry_run=True)["verdict"]["review"]["token"]

    with pytest.raises(SeasonStateError, match="reason"):
        _cancel_only_batch(root, accept_reviewed_consequences=token)


def test_reviewed_consequence_token_must_match_and_is_exclusive(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    before = (_schedule_bytes(root), _decisions_bytes(root))
    token = _cancel_only_batch(root, dry_run=True)["verdict"]["review"]["token"]

    with pytest.raises(SeasonStateError, match="does not match"):
        _cancel_only_batch(
            root,
            accept_reviewed_consequences="rc-" + "0" * 64,
            accept_regression_reason="stale token",
        )
    with pytest.raises(SeasonStateError, match="not both"):
        _cancel_only_batch(
            root,
            accept_reviewed_consequences=token,
            accept_regressions=["Alfa|Alfa 1|U10=" + PARTICIPATION_CODE],
            accept_regression_reason="mixed acceptance styles",
        )
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_reviewed_consequence_token_is_bound_to_the_reviewed_operations(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    token_b = _cancel_only_batch(root, dry_run=True)["verdict"]["review"]["token"]

    cancel_a = [{"op": "cancel", "tournament_id": "u10-a", "reason": "host retires"}]
    other = batch_maintenance(
        season=SEASON,
        root=root,
        operations=cancel_a,
        scope=["u10-a"],
        request_id="cancel-a",
        actor="tester",
        dry_run=True,
    )
    assert other["verdict"]["review"]["token"] != token_b

    with pytest.raises(SeasonStateError, match="does not match"):
        batch_maintenance(
            season=SEASON,
            root=root,
            operations=cancel_a,
            scope=["u10-a"],
            request_id="cancel-a",
            actor="tester",
            accept_reviewed_consequences=token_b,
            accept_regression_reason="reviewed a different plan",
        )


def test_reviewed_consequence_token_refused_when_nothing_to_accept(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    preview = batch_maintenance(
        season=SEASON,
        root=root,
        operations=[{"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"}],
        scope=["u10-a-20260912"],
        request_id="no-consequence-review",
        actor="tester",
        dry_run=True,
    )
    assert preview["verdict"]["review"]["required"] is False
    assert preview["verdict"]["review"]["token"] is None

    with pytest.raises(SeasonStateError, match="unnecessary"):
        batch_maintenance(
            season=SEASON,
            root=root,
            operations=[{"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"}],
            scope=["u10-a-20260912"],
            request_id="no-consequence-review",
            actor="tester",
            accept_reviewed_consequences="rc-" + "0" * 64,
            accept_regression_reason="no consequences to accept",
        )


def test_reviewed_consequence_token_is_deterministic_and_sensitive() -> None:
    from tournament_scheduler.team_schedule_quality import reviewed_consequence_token

    consequence = {"club": "C", "team": "C1", "age_group": "U10", "code": GAP_CODE}
    token = reviewed_consequence_token(
        plan_fingerprint="fp", baseline_revision="rev", unaccepted_regressions=[consequence]
    )
    assert token == reviewed_consequence_token(
        plan_fingerprint="fp",
        baseline_revision="rev",
        unaccepted_regressions=[dict(consequence)],
    )
    assert token != reviewed_consequence_token(
        plan_fingerprint="other", baseline_revision="rev", unaccepted_regressions=[consequence]
    )
    assert token != reviewed_consequence_token(
        plan_fingerprint="fp", baseline_revision="other", unaccepted_regressions=[consequence]
    )
    assert token != reviewed_consequence_token(
        plan_fingerprint="fp",
        baseline_revision="rev",
        unaccepted_regressions=[{**consequence, "code": "travel_materially_worse"}],
    )


def test_cli_batch_reviewed_consequence_round_trip(tmp_path: Path, capsys) -> None:
    from tournament_scheduler.cli.rvv_cli import main as cli_main

    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    operations_path = tmp_path / "cancel.json"
    operations_path.write_text(
        json.dumps([{"op": "cancel", "tournament_id": "u10-b", "reason": "host retires"}]),
        encoding="utf-8",
    )
    base_args = [
        "season",
        "batch",
        "--season",
        SEASON,
        "--root",
        str(root),
        "--operations",
        str(operations_path),
        "--scope",
        "u10-b",
        "--request-id",
        "cli-reviewed",
        "--dry-run",
        "--json",
    ]
    assert cli_main(base_args) == 0
    preview = json.loads(capsys.readouterr().out)
    token = preview["verdict"]["review"]["token"]
    assert token

    assert cli_main([*base_args, "--fail-on-blocked"]) == 3
    capsys.readouterr()

    apply_args = [arg for arg in base_args if arg != "--dry-run"] + [
        "--accept-reviewed-consequences",
        token,
        "--accept-regression-reason",
        "reviewed via the CLI dry-run",
    ]
    assert cli_main(apply_args) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["committed"] is True
    assert _tournaments_by_id(root)["u10-b"]["cancelled"] is True

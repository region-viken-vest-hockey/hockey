from tournament_scheduler.participation.team_schedule_quality import (
    compare_team_schedule_consequence,
)
from tournament_scheduler.season.findings import _spacing_findings


def _team(club: str, label: str, age_group: str = "U9") -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(
    tournament_id: str,
    date: str,
    host: str,
    teams: list[dict],
    *,
    arena: str = "",
) -> dict:
    return {
        "id": tournament_id,
        "date": date,
        "arena": arena,
        "age_group": "U9",
        "host_club": host,
        "teams": teams,
        "games": [],
    }


def test_team_consequence_flags_new_short_gap_for_displaced_team() -> None:
    affected = ("Jar", "Jar Rød", "U9")
    jar = _team(*affected)
    other = _team("Frisk Asker", "Frisk 1")
    before = {
        "tournaments": [
            _tournament("a", "2027-01-10", "Jar", [jar, other]),
            _tournament("b", "2027-01-24", "Frisk Asker", [jar, other]),
            _tournament("c", "2027-02-14", "Frisk Asker", [jar, other]),
        ]
    }
    after = {
        "tournaments": [
            _tournament("a", "2027-01-10", "Jar", [jar, other]),
            _tournament("b", "2027-02-09", "Frisk Asker", [jar, other]),
            _tournament("c", "2027-02-14", "Frisk Asker", [jar, other]),
        ]
    }
    comparison = compare_team_schedule_consequence(before, after, affected)
    assert comparison["acceptable"] is False
    assert "more_gaps_under_7_days" in {
        entry["code"] for entry in comparison["material_regressions"]
    }


def test_team_consequence_flags_material_travel_regression() -> None:
    affected = ("Kongsberg", "Kongsberg", "U9")
    team = _team(*affected)
    other = _team("Jar", "Jar Rød")
    before = {
        "tournaments": [
            _tournament("a", "2027-01-10", "Kongsberg", [team, other], arena="Kongsberghallen"),
            _tournament("b", "2027-01-24", "Kongsberg", [team, other], arena="Kongsberghallen"),
        ]
    }
    after = {
        "tournaments": [
            _tournament("a", "2027-01-10", "Jar", [team, other], arena="Jar Isforum"),
            _tournament("b", "2027-01-24", "Kongsberg", [team, other], arena="Kongsberghallen"),
        ]
    }
    comparison = compare_team_schedule_consequence(before, after, affected)
    assert comparison["acceptable"] is False
    regression = next(
        entry
        for entry in comparison["material_regressions"]
        if entry["code"] == "travel_materially_worse"
    )
    assert regression["delta_km"] >= 50


def _rotatable_problem(*teams: dict) -> dict:
    return {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": list(teams),
        "rotatable_participant_pools": [
            {
                "pool_id": "jutul-u9",
                "club": "Jutul",
                "age_group": "U9",
                "team_labels": ["Jutul Rød", "Jutul Grønn"],
            }
        ],
    }


def test_rotatable_pool_keeps_exact_spacing_diagnostic_without_blocking_distributable_gap() -> None:
    red = _team("Jutul", "Jutul Rød")
    green = _team("Jutul", "Jutul Grønn")
    other = _team("Jar", "Jar 1")
    before = {
        "tournaments": [
            _tournament("a", "2026-11-01", "Jar", [red, other]),
            _tournament("b", "2026-11-06", "Jar", [green, other]),
            _tournament("c", "2026-11-15", "Jar", [red, other]),
            _tournament("d", "2026-11-25", "Jar", [green, other]),
        ]
    }
    after = {
        "tournaments": [
            _tournament("a", "2026-11-01", "Jar", [red, other]),
            _tournament("b", "2026-11-06", "Jar", [red, other]),
            _tournament("c", "2026-11-15", "Jar", [red, other]),
            _tournament("d", "2026-11-25", "Jar", [green, other]),
        ]
    }

    comparison = compare_team_schedule_consequence(
        before, after, ("Jutul", "Jutul Rød", "U9"), problem=_rotatable_problem(red, green, other)
    )

    assert "more_gaps_under_7_days" in {
        item["code"] for item in comparison["team_level"]["material_regressions"]
    }
    assert "more_gaps_under_7_days" not in {
        item["code"] for item in comparison["material_regressions"]
    }
    pool = comparison["participant_pool"]
    assert pool["before"]["spacing"]["unavoidable_gaps_under_7"] == 0
    assert pool["after"]["spacing"]["unavoidable_gaps_under_7"] == 0
    assert pool["after"]["label_distribution"]["counts"] == {
        "Jutul Grønn": 1,
        "Jutul Rød": 3,
    }
    assert not [
        finding
        for finding in _spacing_findings(_rotatable_problem(red, green, other), after)
        if finding["club"] == "Jutul"
    ]


def test_rotatable_pool_blocks_unavoidable_back_to_back_load() -> None:
    red = _team("Jutul", "Jutul Rød")
    green = _team("Jutul", "Jutul Grønn")
    other = _team("Jar", "Jar 1")
    before = {
        "tournaments": [
            _tournament("a", "2026-11-01", "Jar", [red, green, other]),
        ]
    }
    after = {
        "tournaments": [
            _tournament("a", "2026-11-01", "Jar", [red, green, other]),
            _tournament("b", "2026-11-02", "Jar", [red, other]),
        ]
    }

    comparison = compare_team_schedule_consequence(
        before, after, ("Jutul", "Jutul Rød", "U9"), problem=_rotatable_problem(red, green, other)
    )

    assert comparison["acceptable"] is False
    assert "more_unavoidable_back_to_back_days" in {
        item["code"] for item in comparison["material_regressions"]
    }
    assert comparison["participant_pool"]["after"]["spacing"][
        "unavoidable_back_to_back_days"
    ] == 1
    findings = [
        finding
        for finding in _spacing_findings(_rotatable_problem(red, green, other), after)
        if finding["club"] == "Jutul"
    ]
    assert len(findings) == 1
    assert findings[0]["consequence_scope"] == "rotatable_participant_pool"


def test_rotatable_pool_finding_uses_complete_pool_cluster_once() -> None:
    red = _team("Jutul", "Jutul Rød")
    green = _team("Jutul", "Jutul Grønn")
    other = _team("Jar", "Jar 1")
    plan = {
        "tournaments": [
            _tournament("red-first", "2026-11-01", "Jar", [red, other]),
            _tournament("green-middle", "2026-11-03", "Jar", [green, other]),
            _tournament("red-last", "2026-11-05", "Jar", [red, other]),
        ]
    }

    findings = [
        finding
        for finding in _spacing_findings(_rotatable_problem(red, green, other), plan)
        if finding["club"] == "Jutul"
    ]

    assert len(findings) == 1
    finding = findings[0]
    assert finding["finding_id"] == (
        "temporal_clustering:Jutul:participant_pool=jutul-u9:U9"
    )
    assert finding["participant_pool"]["team_labels"] == ["Jutul Grønn", "Jutul Rød"]
    assert finding["clustered_dates"] == ["2026-11-01", "2026-11-03", "2026-11-05"]
    assert finding["tournament_ids"] == ["green-middle", "red-first", "red-last"]
    assert finding["team_identity"] == {
        "club": "Jutul",
        "label": "Jutul Grønn",
        "age_group": "U9",
    }


def test_multi_team_club_without_explicit_pool_keeps_exact_team_gate() -> None:
    red = _team("Jutul", "Jutul Rød")
    green = _team("Jutul", "Jutul Grønn")
    other = _team("Jar", "Jar 1")
    before = {
        "tournaments": [
            _tournament("a", "2026-11-01", "Jar", [red, other]),
            _tournament("b", "2026-11-15", "Jar", [red, green, other]),
        ]
    }
    after = {
        "tournaments": [
            _tournament("a", "2026-11-01", "Jar", [red, other]),
            _tournament("x", "2026-11-06", "Jar", [red, other]),
            _tournament("b", "2026-11-15", "Jar", [red, green, other]),
        ]
    }

    comparison = compare_team_schedule_consequence(
        before,
        after,
        ("Jutul", "Jutul Rød", "U9"),
        problem={"teams": [red, green, other]},
    )

    assert "participant_pool" not in comparison
    assert "more_gaps_under_7_days" in {
        item["code"] for item in comparison["material_regressions"]
    }


def test_team_consequence_allows_small_nonmaterial_tradeoff() -> None:
    affected = ("Jar", "Jar Rød", "U9")
    jar = _team(*affected)
    other = _team("Frisk Asker", "Frisk 1")
    before = {
        "tournaments": [
            _tournament("a", "2027-01-10", "Jar", [jar, other]),
            _tournament("b", "2027-01-31", "Frisk Asker", [jar, other]),
        ]
    }
    after = {
        "tournaments": [
            _tournament("a", "2027-01-10", "Jar", [jar, other]),
            _tournament("b", "2027-01-30", "Frisk Asker", [jar, other]),
        ]
    }
    comparison = compare_team_schedule_consequence(before, after, affected)
    assert comparison["acceptable"] is True
    assert comparison["material_regressions"] == []

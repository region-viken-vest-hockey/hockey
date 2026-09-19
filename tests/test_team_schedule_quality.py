from tournament_scheduler.team_schedule_quality import (
    compare_team_schedule_consequence,
)


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

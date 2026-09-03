"""Unit tests for tournament_scheduler.planning_contract (issue #257)."""

from __future__ import annotations

from tournament_scheduler.planning_contract import (
    CANDIDATE_SCHEMA_VERSION,
    candidate_from_plan_dict,
    extract_candidate,
    score_candidate,
    verify_candidate,
)


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict], **extra) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
        **extra,
    }


class TestExtractCandidate:
    def test_raw_candidate(self):
        raw = {"tournaments": [], "schema_version": 1}
        assert extract_candidate(raw)["tournaments"] == []

    def test_checkpoint_shape(self):
        data = {"plan": {"tournaments": []}, "rules_report": {}}
        candidate = extract_candidate(data)
        assert candidate["tournaments"] == []
        assert candidate["schema_version"] == CANDIDATE_SCHEMA_VERSION

    def test_full_envelope_shape(self):
        data = {"stage": "planning", "status": "done", "data": {"plan": {"tournaments": []}}}
        candidate = extract_candidate(data)
        assert candidate["tournaments"] == []

    def test_missing_plan_raises(self):
        import pytest

        with pytest.raises(ValueError):
            extract_candidate({"foo": "bar"})

    def test_candidate_from_plan_dict_envelope(self):
        wrapped = candidate_from_plan_dict({"tournaments": []}, source="SeasonPlanner", planner_version="1.2.3")
        assert wrapped["schema_version"] == CANDIDATE_SCHEMA_VERSION
        assert wrapped["source"] == {"planner": "SeasonPlanner", "version": "1.2.3"}


class TestVerifyCandidateSelfConsistency:
    def test_clean_candidate_passes_without_problem(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)]}
        result = verify_candidate(candidate)
        assert result["ok"], result["violations"]
        assert "registered_teams" in result["skipped"]

    def test_duplicate_participation_same_date_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        t1 = _tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)
        t2 = _tournament("t2", "2026-01-10", "Kongsberghallen", "U10", teams)
        result = verify_candidate({"tournaments": [t1, t2]})
        assert not result["ok"]
        codes = {v["code"] for v in result["violations"]}
        assert "duplicate_participation_same_date" in codes

    def test_reused_label_across_age_groups_is_not_a_false_positive(self):
        # Same club+label reused in two different age groups (common in this
        # roster — see models.team_key) must not be treated as one team
        # double-booked on the same date.
        u10_teams = [_team("Ringerike", "Ringerike 1", "U10"), _team("Jar", "Jar 1", "U10")]
        u11_teams = [_team("Ringerike", "Ringerike 1", "U11"), _team("Jar", "Jar 1", "U11")]
        t1 = _tournament("t1", "2026-01-10", "Jarhallen", "U10", u10_teams)
        t2 = _tournament("t2", "2026-01-10", "Kongsberghallen", "U11", u11_teams)
        result = verify_candidate({"tournaments": [t1, t2]})
        assert result["ok"], result["violations"]

    def test_duplicate_team_in_same_tournament_flagged(self):
        team = _team("Jar", "Jar 1", "U10")
        candidate = {
            "tournaments": [
                {
                    "id": "t1",
                    "date": "2026-01-10",
                    "arena": "Jarhallen",
                    "age_group": "U10",
                    "teams": [team, dict(team)],
                    "games": [],
                }
            ]
        }
        result = verify_candidate(candidate)
        codes = {v["code"] for v in result["violations"]}
        assert "duplicate_team_in_tournament" in codes

    def test_cancelled_tournaments_are_ignored(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        t1 = _tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)
        t2 = _tournament("t2", "2026-01-10", "Kongsberghallen", "U10", teams, cancelled=True)
        result = verify_candidate({"tournaments": [t1, t2]})
        assert result["ok"], result["violations"]


class TestVerifyCandidateWithProblem:
    def _problem(self, **overrides) -> dict:
        base = {
            "schema_version": 1,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "teams": [
                {"club": "Jar", "label": "Jar 1", "age_group": "U10", "target_tournament_count": None},
                {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U10", "target_tournament_count": None},
            ],
            "parallel_games": {},
            "round_length_minutes": {},
            "target_tournament_count": None,
            "target_tournament_counts_by_age_group": {},
            "manual_adjustments": {
                "locked_dates": [],
                "banned_dates": [],
                "forced_host_clubs": [],
                "excluded_host_clubs": [],
                "pinned_tournament_ids": [],
            },
            "date_preferences": [],
            "club_busy_dates": {},
        }
        base.update(overrides)
        return base

    def test_unregistered_team_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Ghost", "Ghost 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)]}
        result = verify_candidate(candidate, self._problem())
        codes = {v["code"] for v in result["violations"]}
        assert "unregistered_team" in codes

    def test_date_outside_window_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2027-06-01", "Jarhallen", "U10", teams)]}
        result = verify_candidate(candidate, self._problem())
        codes = {v["code"] for v in result["violations"]}
        assert "date_outside_window" in codes

    def test_banned_date_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jarhallen", "U10", teams)]}
        problem = self._problem(
            manual_adjustments={
                "locked_dates": [],
                "banned_dates": ["2026-06-01"],
                "forced_host_clubs": [],
                "excluded_host_clubs": [],
                "pinned_tournament_ids": [],
            }
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "banned_date_used" in codes

    def test_excluded_host_club_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jarhallen", "U10", teams)]}
        problem = self._problem(
            manual_adjustments={
                "locked_dates": [],
                "banned_dates": [],
                "forced_host_clubs": [],
                "excluded_host_clubs": ["Jar"],
                "pinned_tournament_ids": [],
            }
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "excluded_host_club_used" in codes

    def test_capacity_exceeded_flagged(self):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Kongsberg", "Kongsberg 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
        ]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jarhallen", "U10", teams)]}
        problem = self._problem(
            parallel_games={"U10": 1},
            teams=[
                {"club": "Jar", "label": "Jar 1", "age_group": "U10", "target_tournament_count": None},
                {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U10", "target_tournament_count": None},
                {"club": "Skien", "label": "Skien 1", "age_group": "U10", "target_tournament_count": None},
            ],
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "tournament_over_capacity" in codes

    def test_participation_target_mismatch_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jarhallen", "U10", teams)]}
        problem = self._problem(target_tournament_count=3)
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "participation_target_mismatch" in codes

    def test_pinned_tournament_missing_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jarhallen", "U10", teams)]}
        problem = self._problem(
            manual_adjustments={
                "locked_dates": [],
                "banned_dates": [],
                "forced_host_clubs": [],
                "excluded_host_clubs": [],
                "pinned_tournament_ids": ["does-not-exist"],
            }
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "pinned_tournament_missing" in codes


class TestScoreCandidate:
    def test_basic_metrics_on_two_tournaments(self):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Kongsberg", "Kongsberg 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Ringerike", "Ringerike 1", "U10"),
        ]
        t1 = _tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)
        t2 = _tournament("t2", "2026-01-24", "Kongsberghallen", "U10", teams)
        report = score_candidate({"tournaments": [t1, t2]})

        assert report["participation"]["spread"] == 0
        assert report["participation"]["counts_by_team"]["Jar 1"] == 2
        # Every pair played twice (round-robin both weekends): 6 unique pairs,
        # each repeated once -> no "first-time" games on the second showing.
        assert report["opponent_diversity"]["unique_pairs"] == 6
        assert report["opponent_diversity"]["max_pair_repeat"] == 2
        assert report["turnaround"]["min_turnaround_days"] == 14
        assert report["turnaround"]["gaps_under_days"][7] == 0
        # The gap is exactly 14 days, which does not count as "under 14".
        assert report["turnaround"]["gaps_under_days"][14] == 0

    def test_same_club_vs_inter_club_pairing_kept_separate(self):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Jar", "Jar 2", "U10"),
            _team("Kongsberg", "Kongsberg 1", "U10"),
            _team("Kongsberg", "Kongsberg 2", "U10"),
        ]
        t1 = _tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)
        report = score_candidate({"tournaments": [t1]})
        diversity = report["opponent_diversity"]
        # Jar1-Jar2 and Kongsberg1-Kongsberg2 are the two same-club games.
        assert diversity["same_club_pairing_count"] == 2
        # The remaining 4 of the 6 round-robin games are inter-club.
        assert diversity["unique_pairs"] - diversity["same_club_pairing_count"] == 4

    def test_reused_label_across_age_groups_scored_separately(self):
        u10_teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        u11_teams = [_team("Jar", "Jar 1", "U11"), _team("Ringerike", "Ringerike 1", "U11")]
        t1 = _tournament("t1", "2026-01-10", "Jarhallen", "U10", u10_teams)
        t2 = _tournament("t2", "2026-01-10", "Kongsberghallen", "U11", u11_teams)
        report = score_candidate({"tournaments": [t1, t2]})
        # "Jar 1" appears once per age group; must not be merged into one
        # team with 2 appearances, and the two age groups' games must not be
        # treated as a repeated matchup between the same pair.
        assert report["opponent_diversity"]["unique_pairs"] == 2
        assert report["opponent_diversity"]["max_pair_repeat"] == 1

    def test_hosting_and_month_distribution(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        t1 = _tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)
        t2 = _tournament("t2", "2026-02-14", "Jarhallen", "U10", teams)
        report = score_candidate({"tournaments": [t1, t2]})
        assert report["hosting"]["counts_by_host"]["Jar"] == 2
        assert report["month_distribution"] == {"2026-01": 1, "2026-02": 1}

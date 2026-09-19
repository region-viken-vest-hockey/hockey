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
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)]}
        result = verify_candidate(candidate)
        assert result["ok"], result["violations"]
        assert "registered_teams" in result["skipped"]

    def test_odd_sized_tournament_rejected_for_any_age_group(self):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Kongsberg", "Kongsberg 1", "U10"),
            _team("Holmen", "Holmen 1", "U10"),
        ]
        result = verify_candidate({"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)]})
        assert not result["ok"]
        assert "bye_team_not_allowed" in {v["code"] for v in result["violations"]}

    def test_even_sized_tournament_passes_no_bye_rule(self):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Kongsberg", "Kongsberg 1", "U10"),
            _team("Holmen", "Holmen 1", "U10"),
            _team("Jutul", "Jutul 1", "U10"),
        ]
        result = verify_candidate({"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)]})
        assert result["ok"], result["violations"]

    def test_u12_ju12_must_be_exactly_four_teams(self):
        for age_group in ("U12", "JU12"):
            teams = [
                _team("Jar", "Jar 1", age_group),
                _team("Kongsberg", "Kongsberg 1", age_group),
                _team("Holmen", "Holmen 1", age_group),
                _team("Jutul", "Jutul 1", age_group),
            ]
            valid = verify_candidate({"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", age_group, teams)]})
            assert valid["ok"], valid["violations"]
            two_team = verify_candidate({"tournaments": [_tournament("t2", "2026-01-11", "Jar Isforum", age_group, teams[:2])]})
            assert not two_team["ok"]
            assert "bye_team_not_allowed" in {v["code"] for v in two_team["violations"]}

    def test_duplicate_participation_same_date_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)
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
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", u10_teams)
        t2 = _tournament("t2", "2026-01-10", "Kongsberghallen", "U11", u11_teams)
        result = verify_candidate({"tournaments": [t1, t2]})
        assert result["ok"], result["violations"]

    def test_four_teams_from_same_club_is_hard_violation(self):
        # issue #326: 3 teams from one club is the allowed fallback max; 4+
        # must always be a hard violation, with or without a `problem`.
        teams = [
            _team("Jar", "Jar 1", "U11"),
            _team("Jar", "Jar 2", "U11"),
            _team("Jar", "Jar 3", "U11"),
            _team("Jar", "Jar 4", "U11"),
            _team("Kongsberg", "Kongsberg 1", "U11"),
        ]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "U11", teams)]}
        result = verify_candidate(candidate)
        assert not result["ok"]
        codes = {v["code"] for v in result["violations"]}
        assert "club_hard_max_exceeded" in codes

    def test_three_teams_from_same_club_is_not_a_hard_violation(self):
        teams = [
            _team("Jar", "Jar 1", "U11"),
            _team("Jar", "Jar 2", "U11"),
            _team("Jar", "Jar 3", "U11"),
            _team("Kongsberg", "Kongsberg 1", "U11"),
        ]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "U11", teams)]}
        result = verify_candidate(candidate)
        codes = {v["code"] for v in result["violations"]}
        assert "club_hard_max_exceeded" not in codes

    def test_duplicate_team_in_same_tournament_flagged(self):
        team = _team("Jar", "Jar 1", "U10")
        candidate = {
            "tournaments": [
                {
                    "id": "t1",
                    "date": "2026-01-10",
                    "arena": "Jar Isforum",
                    "age_group": "U10",
                    "teams": [team, dict(team)],
                    "games": [],
                }
            ]
        }
        result = verify_candidate(candidate)
        codes = {v["code"] for v in result["violations"]}
        assert "duplicate_team_in_tournament" in codes

    def test_u_team_in_ju_tournament_is_hard_violation(self):
        # U and JU are never the same category, even when they share the
        # same numeric age.
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "JU10")]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "JU10", teams)]}
        result = verify_candidate(candidate)
        assert not result["ok"]
        codes = {v["code"] for v in result["violations"]}
        assert "age_group_mismatch" in codes

    def test_ju_team_in_u_tournament_is_hard_violation(self):
        teams = [_team("Jar", "Jar 1", "JU10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)]}
        result = verify_candidate(candidate)
        assert not result["ok"]
        codes = {v["code"] for v in result["violations"]}
        assert "age_group_mismatch" in codes

    def test_reused_label_across_u_and_ju_is_distinct_identity(self):
        # Same club+label reused across U and JU (the numeric part is
        # shared) must not be collapsed into one team identity.
        u10_teams = [_team("Ringerike", "Ringerike 1", "U10"), _team("Jar", "Jar 1", "U10")]
        ju10_teams = [_team("Ringerike", "Ringerike 1", "JU10"), _team("Jar", "Jar 1", "JU10")]
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", u10_teams)
        t2 = _tournament("t2", "2026-01-10", "Kongsberghallen", "JU10", ju10_teams)
        result = verify_candidate({"tournaments": [t1, t2]})
        assert result["ok"], result["violations"]

    def test_cancelled_tournaments_are_ignored(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)
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
            "participation_targets_by_age_group": {},
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
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)]}
        result = verify_candidate(candidate, self._problem())
        codes = {v["code"] for v in result["violations"]}
        assert "unregistered_team" in codes

    def test_date_outside_window_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2027-06-01", "Jar Isforum", "U10", teams)]}
        result = verify_candidate(candidate, self._problem())
        codes = {v["code"] for v in result["violations"]}
        assert "date_outside_window" in codes

    def test_banned_date_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
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
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
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

    def test_unknown_host_calendar_status_surfaced_for_manual_placement(self):
        """A host with no trustworthy calendar evidence this run is not
        silently treated as available, but it also does not hard-block
        verification -- it's surfaced non-blocking for manual placement,
        mirroring unresolved_hosting_obligations (issue #266)."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
        problem = self._problem(club_calendar_status={"Jar": "unknown", "Kongsberg": "known"})
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "host_calendar_status_unknown" not in codes
        assert result["ok"] is True
        placements = result["manual_calendar_placements"]
        assert len(placements) == 1
        assert placements[0]["host_club"] == "Jar"
        assert placements[0]["tournament_id"] == "t1"

    def test_known_host_calendar_status_not_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
        problem = self._problem(club_calendar_status={"Jar": "known", "Kongsberg": "known"})
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "host_calendar_status_unknown" not in codes
        assert result["manual_calendar_placements"] == []

    def test_missing_club_calendar_status_skips_check(self):
        """No status map at all (older/hand-built problem dicts) must not
        falsely flag every host as unknown."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
        result = verify_candidate(candidate, self._problem())
        codes = {v["code"] for v in result["violations"]}
        assert "host_calendar_status_unknown" not in codes
        assert result["manual_calendar_placements"] == []

    def test_host_team_missing_is_hard_violation(self):
        """issue #322: a home tournament with no participating team from the
        host club, even though the host has a registered team in this exact
        age group, must hard-fail verification."""
        teams = [_team("Frisk", "Frisk Orange", "JU12"), _team("Ringerike", "Ringerike 2", "JU12")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Skienhallen", "JU12", teams, host_club="Skien")]}
        problem = self._problem(
            teams=[
                {"club": "Skien", "label": "Skien 1", "age_group": "JU12", "target_tournament_count": None},
                {"club": "Frisk", "label": "Frisk Orange", "age_group": "JU12", "target_tournament_count": None},
                {"club": "Ringerike", "label": "Ringerike 2", "age_group": "JU12", "target_tournament_count": None},
            ]
        )
        result = verify_candidate(candidate, problem)
        assert result["ok"] is False
        codes = {v["code"] for v in result["violations"]}
        assert "host_team_missing" in codes

    def test_host_team_present_not_flagged(self):
        teams = [
            _team("Skien", "Skien 1", "JU12"),
            _team("Ringerike", "Ringerike 2", "JU12"),
            _team("Jar", "Jar 1", "JU12"),
            _team("Holmen", "Holmen 1", "JU12"),
        ]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Skienhallen", "JU12", teams, host_club="Skien")]}
        problem = self._problem(teams=teams)
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "host_team_missing" not in codes
        assert result["ok"] is True

    def test_shared_registration_satisfies_either_constituent_host(self):
        """issue #322: a joint registration ("Jutul/Jar") represents either
        physical constituent host it names."""
        teams = [
            _team("Jutul/Jar", "Jutul/Jar Kittens", "JU12"),
            _team("Ringerike", "Ringerike 2", "JU12"),
            _team("Frisk", "Frisk Orange", "JU12"),
            _team("Holmen", "Holmen 1", "JU12"),
        ]
        problem = self._problem(teams=teams)
        for host_club in ("Jutul", "Jar"):
            candidate = {
                "tournaments": [
                    _tournament("t1", "2026-06-01", "Hallen", "JU12", teams, host_club=host_club)
                ]
            }
            result = verify_candidate(candidate, problem)
            codes = {v["code"] for v in result["violations"]}
            assert "host_team_missing" not in codes, host_club
            assert result["ok"] is True, host_club

    def test_host_with_no_registered_team_in_age_group_not_falsely_rejected(self):
        """issue #322: the invariant only applies when the host actually has
        an eligible registered team in this exact age group -- a host with
        none must not be falsely rejected solely for host-team absence."""
        teams = [
            _team("Frisk", "Frisk Orange", "JU12"),
            _team("Ringerike", "Ringerike 2", "JU12"),
            _team("Jar", "Jar 1", "JU12"),
            _team("Holmen", "Holmen 1", "JU12"),
        ]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Hallen", "JU12", teams, host_club="Skien")]}
        problem = self._problem(teams=teams)
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "host_team_missing" not in codes
        assert result["ok"] is True

    def test_capacity_exceeded_flagged(self):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Kongsberg", "Kongsberg 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
        ]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
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

    def test_participation_target_mismatch_surfaced_for_manual_placement(self):
        """A participation shortfall doesn't hard-block verification -- it's
        surfaced non-blocking for manual placement (e.g. an operator
        arranging an extra game by hand), same as manual_calendar_placements."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
        problem = self._problem(target_tournament_count=3)
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "participation_target_mismatch" not in codes
        assert result["ok"] is True
        placements = result["manual_participation_placements"]
        assert len(placements) == 2
        clubs = {p["club"] for p in placements}
        assert clubs == {"Jar", "Kongsberg"}
        assert all(p["actual"] == "1" and p["target"] == "3" for p in placements)

    def test_over_participation_is_bounded_evidence_not_hard_violation(self):
        """issue #376: over-participation against a strong target is bounded
        deviation evidence, not a hard verifier failure. The planner should
        avoid it, but it is not an absolute legality boundary."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {
            "tournaments": [
                _tournament(f"t{i}", f"2026-0{i}-15", "Jar Isforum", "U10", teams) for i in range(1, 5)
            ]
        }
        problem = self._problem(target_tournament_count=3)
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "participation_target_exceeded" not in codes
        assert "participation_hard_max_exceeded" not in codes
        assert "holiday_date_used" not in codes
        assert result["ok"] is True
        over = [d for d in result["participation_deviations"] if d["direction"] == "over_target"]
        assert over
        assert over[0]["actual"] == 4 and over[0]["target"] == 3
        # No operator waiver is required for an ordinary bounded target
        # deviation; over-target is not a manual slot-scarcity placement item.
        assert result["waived_violations"] == []
        assert result["manual_participation_placements"] == []

    def test_under_participation_still_surfaced_non_blocking(self):
        """issue #301: genuine under-participation caused by slot scarcity
        remains distinct from over-participation -- it stays a non-blocking
        manual placement item, not a hard violation."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
        problem = self._problem(target_tournament_count=3)
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "participation_target_exceeded" not in codes
        assert result["ok"] is True
        assert len(result["manual_participation_placements"]) == 2

    def test_before_after_christmas_split_is_authoritative_per_half_target(self):
        # before_christmas/after_christmas are the authoritative per-team,
        # per-half participation target -- not a weight for splitting some
        # other season-wide tournament count. A U11 team with an
        # after-Christmas target of 3 that only gets 2 must surface as a
        # non-blocking shortfall tagged with its age group and half.
        teams = [_team("Jar", "Jar 1", "U11"), _team("Kongsberg", "Kongsberg 1", "U11")]
        candidate = {
            "tournaments": [
                # Before Christmas (2025): 3 participations, matches target.
                _tournament("t1", "2025-09-06", "Jar Isforum", "U11", teams),
                _tournament("t2", "2025-10-11", "Jar Isforum", "U11", teams),
                _tournament("t3", "2025-11-15", "Jar Isforum", "U11", teams),
                # After Christmas (2026): only 2 participations, below target of 3.
                _tournament("t4", "2026-01-24", "Jar Isforum", "U11", teams),
                _tournament("t5", "2026-02-21", "Jar Isforum", "U11", teams),
            ]
        }
        problem = self._problem(
            start_date="2025-09-01",
            end_date="2026-06-30",
            teams=[
                {"club": "Jar", "label": "Jar 1", "age_group": "U11", "target_tournament_count": None},
                {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U11", "target_tournament_count": None},
            ],
            participation_targets_by_age_group={"U11": {"before_christmas": 3, "after_christmas": 3}},
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "participation_target_exceeded" not in codes
        assert result["ok"] is True
        shortfalls = {
            (p["label"], p["half"]): p
            for p in result["manual_participation_placements"]
        }
        for label in ("Jar 1", "Kongsberg 1"):
            shortfall = shortfalls[(label, "after_christmas")]
            assert shortfall["age_group"] == "U11"
            assert shortfall["actual"] == "2"
            assert shortfall["target"] == "3"
        # Before Christmas met its target exactly, so no shortfall for it.
        assert ("Jar 1", "before_christmas") not in shortfalls

    def test_over_target_after_christmas_is_bounded_evidence(self):
        """issue #376: exceeding an authoritative per-half target is bounded
        strong-goal deviation evidence, not a hard failure -- the target is a
        goal, not a ceiling."""
        teams = [_team("Jar", "Jar 1", "U11"), _team("Kongsberg", "Kongsberg 1", "U11")]
        candidate = {
            "tournaments": [
                _tournament("t1", "2026-01-10", "Jar Isforum", "U11", teams),
                _tournament("t2", "2026-02-10", "Jar Isforum", "U11", teams),
                _tournament("t3", "2026-03-10", "Jar Isforum", "U11", teams),
                _tournament("t4", "2026-04-15", "Jar Isforum", "U11", teams),
            ]
        }
        problem = self._problem(
            start_date="2025-09-01",
            end_date="2026-06-30",
            teams=[
                {"club": "Jar", "label": "Jar 1", "age_group": "U11", "target_tournament_count": None},
                {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U11", "target_tournament_count": None},
            ],
            participation_targets_by_age_group={"U11": {"before_christmas": 3, "after_christmas": 3}},
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "participation_target_exceeded" not in codes
        assert result["ok"] is True
        after = [
            d
            for d in result["participation_deviations"]
            if d["scope"] == "after_christmas" and d["direction"] == "over_target"
        ]
        assert after and after[0]["actual"] == 4 and after[0]["target"] == 3

    def test_external_calendar_conflict_surfaced_for_manual_placement(self):
        """issue #264 P0: a 'known' host calendar status is not itself proof
        that every start time on that date is free -- an actual overlapping
        external booking is independently detected, but (unlike before)
        doesn't hard-block verification -- it's surfaced non-blocking for
        manual placement instead."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {
            "tournaments": [
                _tournament(
                    "t1", "2026-06-01", "Jar Isforum", "U10", teams, start_time="10:00"
                )
            ]
        }
        problem = self._problem(
            round_length_minutes={"U10": 60},
            club_calendar_status={"Jar": "known", "Kongsberg": "known"},
            club_busy_intervals={
                "Jar": [{"date": "2026-06-01", "start": "10:30", "end": "12:00"}]
            },
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "external_calendar_conflict" not in codes
        assert result["ok"] is True
        placements = result["manual_external_conflict_placements"]
        assert len(placements) == 1
        assert placements[0]["host_club"] == "Jar"
        assert placements[0]["tournament_id"] == "t1"

    def test_external_calendar_partial_day_availability_not_flagged(self):
        """A booking that ends before the tournament starts must not be
        treated as an all-day conflict."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {
            "tournaments": [
                _tournament(
                    "t1", "2026-06-01", "Jar Isforum", "U10", teams, start_time="13:00"
                )
            ]
        }
        problem = self._problem(
            round_length_minutes={"U10": 60},
            club_calendar_status={"Jar": "known", "Kongsberg": "known"},
            club_busy_intervals={
                "Jar": [{"date": "2026-06-01", "start": "08:00", "end": "12:00"}]
            },
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "external_calendar_conflict" not in codes

    def test_external_calendar_conflict_skipped_when_host_status_unknown(self):
        """An unknown host is already surfaced via manual_calendar_placements
        -- it must not also be double-flagged as an external conflict just
        because it happens to have no busy_intervals entry."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {
            "tournaments": [
                _tournament(
                    "t1", "2026-06-01", "Jar Isforum", "U10", teams, start_time="10:00"
                )
            ]
        }
        problem = self._problem(
            round_length_minutes={"U10": 60},
            club_calendar_status={"Jar": "unknown", "Kongsberg": "known"},
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "host_calendar_status_unknown" not in codes
        assert "external_calendar_conflict" not in codes
        assert len(result["manual_calendar_placements"]) == 1

    def test_club_controlled_allocation_not_flagged_as_hard_conflict(self):
        """issue #264: a busy interval tagged 'kind': 'club_controlled' is a
        generic allocation the host club itself may still use, not a genuine
        external booking -- it must not become a hard external_calendar_conflict,
        but should still be recorded (non-blocking) for the evidence bundle."""
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {
            "tournaments": [
                _tournament(
                    "t1", "2026-06-01", "Jar Isforum", "U10", teams, start_time="10:00"
                )
            ]
        }
        problem = self._problem(
            round_length_minutes={"U10": 60},
            club_calendar_status={"Jar": "known", "Kongsberg": "known"},
            club_busy_intervals={
                "Jar": [
                    {
                        "date": "2026-06-01",
                        "start": "10:30",
                        "end": "12:00",
                        "kind": "club_controlled",
                    }
                ]
            },
        )
        result = verify_candidate(candidate, problem)
        codes = {v["code"] for v in result["violations"]}
        assert "external_calendar_conflict" not in codes
        used = result["club_controlled_allocations_used"]
        assert len(used) == 1
        assert used[0]["tournament_id"] == "t1"
        assert used[0]["host_club"] == "Jar"

    def test_movable_busy_interval_is_candidate_with_host_confirmation(self):
        """issue #373: a host-controlled movable_busy interval (Kongsberg open
        ice) is a legitimate placement candidate for that host, not a fixed
        external conflict. The verifier must record it with the normalized
        availability, event title/reason and an explicit
        requires_host_confirmation flag."""
        teams = [_team("Kongsberg", "Kongsberg 1", "U10"), _team("Jar", "Jar 1", "U10")]
        candidate = {
            "tournaments": [
                _tournament(
                    "t1", "2026-11-21", "Kongsberghallen", "U10", teams, start_time="10:00"
                )
            ]
        }
        problem = self._problem(
            round_length_minutes={"U10": 60},
            club_calendar_status={"Kongsberg": "known", "Jar": "known"},
            club_busy_intervals={
                "Kongsberg": [
                    {
                        "date": "2026-11-21",
                        "start": "10:00",
                        "end": "14:00",
                        "kind": "club_controlled",
                        "availability": "movable_busy",
                        "calendar_event": "Åpen ishall",
                        "reason": "host-controlled open ice; may be moved",
                    }
                ]
            },
        )
        result = verify_candidate(candidate, problem)
        # Never a hard violation and never a fixed external conflict.
        assert result["ok"] is True
        assert "external_calendar_conflict" not in {v["code"] for v in result["violations"]}
        assert result["manual_external_conflict_placements"] == []
        used = result["movable_allocations_used"]
        assert len(used) == 1
        assert used[0]["availability"] == "movable_busy"
        assert used[0]["calendar_event"] == "Åpen ishall"
        assert used[0]["requires_host_confirmation"] is True
        assert used[0]["host_club"] == "Kongsberg"
        # Backward-compatible evidence key carries the same record.
        assert result["club_controlled_allocations_used"] == used

    def test_unclassified_interval_is_exposed_but_still_blocking(self):
        """An event nothing configured has classified is an ambiguous fact.
        The planning problem exposes it separately from a configured booking,
        while hard verification still treats it as occupied rather than
        assuming it is free."""
        from tournament_scheduler.calendar_availability import unclassified_intervals

        teams = [_team("Kongsberg", "Kongsberg 1", "U10"), _team("Jar", "Jar 1", "U10")]
        candidate = {
            "tournaments": [
                _tournament(
                    "t1", "2026-11-21", "Kongsberghallen", "U10", teams, start_time="10:00"
                )
            ]
        }
        busy_intervals = {
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "10:00",
                    "end": "14:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                    "classification_source": "unclassified",
                    "calendar_event": "Ukjent arrangement",
                }
            ]
        }
        problem = self._problem(
            round_length_minutes={"U10": 60},
            club_calendar_status={"Kongsberg": "known", "Jar": "known"},
            club_busy_intervals=busy_intervals,
        )
        result = verify_candidate(candidate, problem)
        assert result["movable_allocations_used"] == []
        assert result["manual_external_conflict_placements"]
        assert unclassified_intervals(busy_intervals) == [
            {
                "club": "Kongsberg",
                "date": "2026-11-21",
                "start": "10:00",
                "end": "14:00",
                "calendar_event": "Ukjent arrangement",
                "availability": "fixed_busy",
            }
        ]

    def test_inferred_interpretation_makes_unclassified_interval_a_candidate(self):
        """A controller-requested inferred interpretation turns an ambiguous
        event into a movable_busy candidate *without* mutating the source
        calendar, and marks the placement as requiring host confirmation."""
        teams = [_team("Kongsberg", "Kongsberg 1", "U10"), _team("Jar", "Jar 1", "U10")]
        candidate = {
            "calendar_interpretations": [
                {
                    "club": "Kongsberg",
                    "date": "2026-11-21",
                    "start": "10:00",
                    "calendar_event": "Ukjent arrangement",
                    "reason": "controller-inferred host-controlled interval",
                }
            ],
            "tournaments": [
                _tournament(
                    "t1", "2026-11-21", "Kongsberghallen", "U10", teams, start_time="10:00"
                )
            ],
        }
        problem = self._problem(
            round_length_minutes={"U10": 60},
            club_calendar_status={"Kongsberg": "known", "Jar": "known"},
            club_busy_intervals={
                "Kongsberg": [
                    {
                        "date": "2026-11-21",
                        "start": "10:00",
                        "end": "14:00",
                        "kind": "external",
                        "availability": "fixed_busy",
                        "classification_source": "unclassified",
                        "calendar_event": "Ukjent arrangement",
                    }
                ]
            },
        )
        result = verify_candidate(candidate, problem)
        assert result["ok"] is True
        assert result["manual_external_conflict_placements"] == []
        used = result["movable_allocations_used"]
        assert len(used) == 1
        assert used[0]["classification_source"] == "inferred"
        assert used[0]["requires_host_confirmation"] is True
        assert used[0]["calendar_event"] == "Ukjent arrangement"
        assert result["calendar_interpretations_used"] == candidate["calendar_interpretations"]
        # The problem's own calendar fact is untouched -- only the candidate
        # carries the inferred interpretation.
        assert problem["club_busy_intervals"]["Kongsberg"][0]["availability"] == "fixed_busy"

    def test_configured_fixed_booking_cannot_be_reinterpreted(self):
        """A configured fixed booking is deterministic knowledge, never an
        ambiguous fact: a candidate-borne interpretation must not be able to
        turn it into movable capacity."""
        from tournament_scheduler.planning_contract import apply_calendar_interpretations

        event = "Sandefjord Penguins fast istid (opptatt utenom tildelt helgevindu)"
        busy = {
            "Sandefjord Penguins": [
                {
                    "date": "2026-11-21",
                    "start": "10:00",
                    "end": "14:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                    "calendar_event": event,
                }
            ]
        }
        interpretation = {
            "club": "Sandefjord Penguins",
            "date": "2026-11-21",
            "start": "10:00",
            "calendar_event": event,
        }
        merged = apply_calendar_interpretations(busy, [interpretation])
        entry = merged["Sandefjord Penguins"][0]
        assert entry["availability"] == "fixed_busy"
        assert "classification_source" not in entry

    def test_inferred_interpretation_needs_an_unclassified_title(self):
        """Only a title no configured rule classifies is eligible, so the same
        overlay is a no-op for a configured movable interval."""
        from tournament_scheduler.planning_contract import apply_calendar_interpretations

        busy = {
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "10:00",
                    "end": "14:00",
                    "kind": "club_controlled",
                    "availability": "movable_busy",
                    "calendar_event": "Åpen ishall",
                }
            ]
        }
        merged = apply_calendar_interpretations(
            busy,
            [{"club": "Kongsberg", "date": "2026-11-21", "calendar_event": "Åpen ishall"}],
        )
        assert "classification_source" not in merged["Kongsberg"][0]

    def test_pinned_tournament_missing_flagged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-06-01", "Jar Isforum", "U10", teams)]}
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
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)
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
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)
        report = score_candidate({"tournaments": [t1]})
        diversity = report["opponent_diversity"]
        # Jar1-Jar2 and Kongsberg1-Kongsberg2 are the two same-club games.
        assert diversity["same_club_pairing_count"] == 2
        # The remaining 4 of the 6 round-robin games are inter-club.
        assert diversity["unique_pairs"] - diversity["same_club_pairing_count"] == 4

    def test_reused_label_across_age_groups_scored_separately(self):
        u10_teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        u11_teams = [_team("Jar", "Jar 1", "U11"), _team("Ringerike", "Ringerike 1", "U11")]
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", u10_teams)
        t2 = _tournament("t2", "2026-01-10", "Kongsberghallen", "U11", u11_teams)
        report = score_candidate({"tournaments": [t1, t2]})
        # "Jar 1" appears once per age group; must not be merged into one
        # team with 2 appearances, and the two age groups' games must not be
        # treated as a repeated matchup between the same pair.
        assert report["opponent_diversity"]["unique_pairs"] == 2
        assert report["opponent_diversity"]["max_pair_repeat"] == 1

    def test_hosting_and_month_distribution(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        t1 = _tournament("t1", "2026-01-10", "Jar Isforum", "U10", teams)
        t2 = _tournament("t2", "2026-02-14", "Jar Isforum", "U10", teams)
        report = score_candidate({"tournaments": [t1, t2]})
        assert report["hosting"]["counts_by_host"]["Jar"] == 2
        assert report["month_distribution"] == {"2026-01": 1, "2026-02": 1}


class TestVerifyCandidateInputConstrainedShape:
    """Effective-shape rule: avoidable bye/underscheduling vs input-constrained adaptation."""

    def test_small_full_registered_pool_is_input_constrained_not_a_violation(self):
        # Only 5 teams registered for JU10 in the whole roster -- the
        # tournament uses all of them, so the resulting odd/bye shape is
        # unavoidable, not a planner defect.
        registered = [_team(f"Club{i}", f"Team {i}", "JU10") for i in range(5)]
        tournament = _tournament("t1", "2026-01-10", "Arena1", "JU10", registered)
        problem = {"teams": registered, "rounds_per_tournament": {"JU10": 5}}
        result = verify_candidate({"tournaments": [tournament]}, problem)
        assert result["ok"], result["violations"]
        assert "bye_team_not_allowed" not in {v["code"] for v in result["violations"]}
        shapes = result["input_constrained_shapes"]
        assert len(shapes) == 1
        assert shapes[0]["registered_team_count"] == 5
        assert shapes[0]["effective_team_count"] == 5

    def test_large_registered_pool_with_small_subset_is_still_avoidable(self):
        # 8 teams registered for U10, but the tournament only uses 4 of
        # them -- the full pool could have supported a bigger no-bye shape,
        # so this remains a hard violation.
        registered = [_team(f"Club{i}", f"Team {i}", "U10") for i in range(8)]
        tournament = _tournament("t1", "2026-01-10", "Arena1", "U10", registered[:4])
        problem = {"teams": registered, "rounds_per_tournament": {"U10": 5}, "parallel_games": {"U10": 3}}
        result = verify_candidate({"tournaments": [tournament]}, problem)
        assert not result["ok"]
        assert "bye_team_not_allowed" in {v["code"] for v in result["violations"]}
        assert result["input_constrained_shapes"] == []

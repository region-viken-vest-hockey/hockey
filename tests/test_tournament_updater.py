"""Tests for tournament update and rescheduling (backlog item 23).

Covers:
  1. Dropping a team from a 6-team tournament regenerates correct round-robin games
  2. Dropping a non-existent team returns a non-success result with a clear message
  3. Moving a date without conflict checkers produces a success result (no conflicts found)
  4. Cascading move — moving tournament A to tournament B's date swaps the two dates
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tournament_scheduler.models import SeasonPlan, Tournament, Team
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.pipeline.tournament_updater import (
    TournamentUpdater,
    TournamentUpdateError,
    TournamentValidationError,
)
from tournament_scheduler.pipeline.stage3_planning import _plan_to_dict
from tournament_scheduler.season_planner import SeasonPlanner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def six_team_tournament() -> Tournament:
    """A 6-team U10 tournament with round-robin games (distinct clubs)."""
    teams = [
        Team(club="Jar", label="Jar 1", age_group="U10"),
        Team(club="Holmen", label="Holmen 1", age_group="U10"),
        Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        Team(club="Ringerike", label="Ringerike 1", age_group="U10"),
        Team(club="Skien", label="Skien 1", age_group="U10"),
        Team(club="Tønsberg", label="Tønsberg 1", age_group="U10"),
    ]
    t = Tournament(
        date=date(2027, 1, 16),
        arena="Jarhallen",
        age_group="U10",
        host_club="Jar",
        teams=teams,
    )
    t.games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=2)
    return t


@pytest.fixture
def two_tournament_plan() -> tuple[SeasonPlan, Tournament, Tournament]:
    """A plan with two tournaments on different dates (used for cascade test)."""
    teams_a = [
        Team(club="Jar", label="Jar 1", age_group="U10"),
        Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        Team(club="Skien", label="Skien 1", age_group="U10"),
    ]
    teams_b = [
        Team(club="Jar", label="Jar 2", age_group="U10"),
        Team(club="Ringerike", label="Ringerike 1", age_group="U10"),
        Team(club="Tønsberg", label="Tønsberg 1", age_group="U10"),
    ]
    t1 = Tournament(
        date=date(2027, 1, 16),
        arena="Jarhallen",
        age_group="U10",
        host_club="Jar",
        teams=teams_a,
        games=SeasonPlanner.generate_round_robin_games(teams_a, 2),
    )
    t2 = Tournament(
        date=date(2027, 2, 20),
        arena="Ringerikshallen",
        age_group="U11",
        host_club="Ringerike",
        teams=teams_b,
        games=SeasonPlanner.generate_round_robin_games(teams_b, 2),
    )
    plan = SeasonPlan(tournaments=[t1, t2], start_date=date(2027, 1, 1), end_date=date(2027, 4, 30))
    return plan, t1, t2


def make_state_with_plan(plan: SeasonPlan, tmp_path: Any) -> PipelineState:
    """Write a SeasonPlan to a temp pipeline checkpoint and return the state."""
    state = PipelineState(str(tmp_path / "pipeline"))
    plan_dict = {
        "plan": _plan_to_dict(plan),
        "llm_confidence": 0.0,
        "llm_reasoning": "",
        "attempts": 1,
        "llm_skipped": True,
    }
    state.write_stage(StageName.PLANNING, plan_dict, status=StageStatus.DONE)
    return state


# ---------------------------------------------------------------------------
# Exception class tests
# ---------------------------------------------------------------------------


class TestExceptionClasses:
    """Verify the custom exception hierarchy introduced to replace sys.exit calls."""

    def test_tournament_update_error_is_exception(self):
        with pytest.raises(TournamentUpdateError):
            raise TournamentUpdateError("update failed")

    def test_tournament_validation_error_is_update_error(self):
        """TournamentValidationError must be a subclass of TournamentUpdateError."""
        with pytest.raises(TournamentUpdateError):
            raise TournamentValidationError("validation failed")

    def test_tournament_validation_error_is_exception(self):
        with pytest.raises(TournamentValidationError):
            raise TournamentValidationError("validation failed")

    def test_error_message_preserved(self):
        msg = "Kan ikke bruke --drop-team og --new-date samtidig."
        exc = TournamentValidationError(msg)
        assert str(exc) == msg


# ---------------------------------------------------------------------------
# Test 1: Drop a team — 6 teams → 5 teams, correct round-robin
# ---------------------------------------------------------------------------


class TestDropTeam:
    def test_drop_team_regenerates_games(self, six_team_tournament: Tournament, tmp_path: Any):
        """Dropping one team from a 6-team tournament should produce 5-team round-robin."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id

        result = updater.drop_team(tid, "Jar 1", plan=plan)

        assert result.success is True, f"Drop failed: {result.summary_nb}"
        assert result.operation == "team_drop"

        # Check the tournament in the plan (modified in-place)
        tournament = plan.tournaments[0]
        assert len(tournament.teams) == 5
        assert "Jar 1" not in {t.label for t in tournament.teams}

        # A 5-team round-robin with 2 parallel games should produce 10 games
        # (5 teams → 5 rounds × 2 games per round = 10 games, minus 0 byes since 5 is odd)
        # Actually: 5 teams → circle method with 6 slots (1 bye), 5 rounds, 2 games per round = 10
        assert len(tournament.games) == 10, f"Expected 10 games, got {len(tournament.games)}"

        # Verify it's a full round-robin — every pair of teams plays exactly once
        from collections import Counter
        pair_counts: Counter = Counter()
        for game in tournament.games:
            pair = frozenset((game.home.label, game.away.label))
            pair_counts[pair] += 1

        # With 5 teams, there are C(5,2) = 10 possible pairings; each should appear once
        assert len(pair_counts) == 10, f"Expected 10 unique pairings, got {len(pair_counts)}"
        for pair, count in pair_counts.items():
            assert count == 1, f"Pair {pair} appears {count} times (expected 1)"

    def test_drop_nonexistent_team_returns_error(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """Dropping a team that doesn't exist should return a non-success result."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id

        result = updater.drop_team(tid, "NonExistentTeam", plan=plan)

        assert result.success is False
        assert "ble ikke funnet" in result.summary_nb.lower() or "ikke funnet" in result.summary_nb.lower()

    def test_drop_last_two_teams_returns_error(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """Dropping enough teams that only 1 remains should be rejected."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id

        # First drop 4 teams (6 → 2)
        for label in ["Jar 1", "Holmen 1", "Kongsberg 1", "Ringerike 1"]:
            result = updater.drop_team(tid, label, plan=plan)
            assert result.success is True

        # Now try to drop one more (2 → 1) — should fail
        result = updater.drop_team(tid, "Skien 1", plan=plan)
        assert result.success is False
        assert "kun" in result.summary_nb.lower() or "minst" in result.summary_nb.lower()


# ---------------------------------------------------------------------------
# Test 2: Date move
# ---------------------------------------------------------------------------


class TestMoveDate:
    def test_move_date_no_conflicts(self, six_team_tournament: Tournament, tmp_path: Any):
        """Moving a tournament to a new date without conflict checkers succeeds."""
        plan = SeasonPlan(
            tournaments=[six_team_tournament],
            start_date=date(2027, 1, 1),
            end_date=date(2027, 4, 30),
        )
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id
        new_date = date(2027, 2, 13)

        result = updater.move_date(tid, new_date, plan=plan)

        assert result.success is True, f"Move failed: {result.summary_nb}"
        assert result.operation == "date_move"
        assert plan.tournaments[0].date == new_date

    def test_move_date_to_non_weekend_reports_conflict(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """Moving to a Wednesday flags a non-weekend conflict."""
        plan = SeasonPlan(
            tournaments=[six_team_tournament],
            start_date=date(2027, 1, 1),
            end_date=date(2027, 4, 30),
        )
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id
        wednesday = date(2027, 1, 20)  # A Wednesday
        assert wednesday.weekday() == 2

        # Without force, conflict should block the move
        result = updater.move_date(tid, wednesday, plan=plan, force=False)
        assert result.success is False
        assert "helgedag" in result.summary_nb.lower() or "ikke en" in result.summary_nb.lower()

        # With force, the move should succeed despite the conflict
        result = updater.move_date(tid, wednesday, plan=plan, force=True)
        assert result.success is True
        assert plan.tournaments[0].date == wednesday

    def test_move_date_with_force_ignores_conflicts(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """With force=True, move succeeds even when the date has plan-internal conflicts."""
        # Create a plan with two tournaments on different dates
        team_a = [Team(club="Jar", label="Jar 1", age_group="U10")]
        team_b = [Team(club="Skien", label="Skien 1", age_group="U10")]
        t1 = Tournament(
            date=date(2027, 1, 16), arena="Jarhallen", age_group="U10",
            teams=team_a, games=SeasonPlanner.generate_round_robin_games(team_a, 1),
        )
        t2 = Tournament(
            date=date(2027, 2, 20), arena="Skien ishall", age_group="U11",
            teams=team_b, games=SeasonPlanner.generate_round_robin_games(team_b, 1),
        )
        plan = SeasonPlan(tournaments=[t1, t2], start_date=date(2027, 1, 1), end_date=date(2027, 4, 30))
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        # Move t1 to t2's date with force
        result = updater.move_date(t1.id, date(2027, 2, 20), plan=plan, force=False)
        assert result.success is False  # plan-internal conflict blocks
        assert "allerede" in result.summary_nb.lower()

        result = updater.move_date(t1.id, date(2027, 2, 20), plan=plan, force=True)
        assert result.success is True
        assert t1.date == date(2027, 2, 20)


# ---------------------------------------------------------------------------
# Test 3: Cascade move — swapping dates
# ---------------------------------------------------------------------------


class TestCascadeMove:
    def test_cascade_swap_dates(self, two_tournament_plan, tmp_path: Any):
        """Moving tournament A to tournament B's date should swap the dates."""
        plan, t1, t2 = two_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        original_t1_date = t1.date
        original_t2_date = t2.date

        # Move t1 to t2's date with cascade=True
        result = updater.move_date(t1.id, t2.date, plan=plan, force=True, cascade=True)

        assert result.success is True
        # t1 should now be on t2's original date
        assert t1.date == original_t2_date, f"t1 should be on {original_t2_date}, got {t1.date}"
        # t2 (the displaced tournament) should now be on t1's original date
        assert t2.date == original_t1_date, f"t2 should be on {original_t1_date}, got {t2.date}"
        assert len(result.cascade) == 1
        assert result.cascade[0]["displaced_tournament_id"] == t2.id

    def test_cascade_no_swap_when_disabled(self, two_tournament_plan, tmp_path: Any):
        """With cascade=False, moving to an occupied date forces both onto the same date."""
        plan, t1, t2 = two_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        original_t1_date = t1.date
        original_t2_date = t2.date

        result = updater.move_date(t1.id, t2.date, plan=plan, force=True, cascade=False)

        assert result.success is True
        # t1 moved to t2's date
        assert t1.date == original_t2_date
        # t2 should NOT have been moved (cascade=False)
        assert t2.date == original_t2_date
        assert len(result.cascade) == 0


# ---------------------------------------------------------------------------
# Test 4: Checkpoint write and read round-trip
# ---------------------------------------------------------------------------


class TestCheckpointRoundTrip:
    def test_write_and_read_updated_checkpoint(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """After a drop_team, writing the checkpoint and re-reading should preserve the change."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        plan.manual_adjustments = {
            "locked_dates": ["2027-01-16"],
            "banned_dates": ["2027-03-14"],
            "forced_host_clubs": ["Jar"],
            "excluded_host_clubs": ["Skien"],
            "pinned_tournament_ids": [six_team_tournament.id],
        }
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id

        result = updater.drop_team(tid, "Jar 1", plan=plan)
        assert result.success is True

        # Write the updated checkpoint
        updater.write_updated_checkpoint(plan, log_entry=result)

        # Re-read from a fresh state
        state2 = PipelineState(state.work_dir)
        plan2 = updater.load_plan()

        assert len(plan2.tournaments) == 1
        assert len(plan2.tournaments[0].teams) == 5
        assert "Jar 1" not in {t.label for t in plan2.tournaments[0].teams}
        assert len(plan2.tournaments[0].games) == 10
        assert plan2.manual_adjustments["locked_dates"] == ["2027-01-16"]
        assert plan2.manual_adjustments["pinned_tournament_ids"] == [six_team_tournament.id]

    def test_log_update_writes_jsonl_entry(self, six_team_tournament: Tournament, tmp_path: Any):
        """log_update should append a JSONL entry to the active export-folder log."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id

        export_dir = tmp_path / "export" / "2026-07-27T2230"
        export_dir.mkdir(parents=True)
        state.write_stage(
            StageName.EXPORT,
            {"output_files": {"excel": str(export_dir / "season_plan.xlsx")}},
            status=StageStatus.DONE,
        )
        legacy_log_dir = state.work_dir / "logs"
        legacy_log_dir.mkdir(parents=True, exist_ok=True)
        (legacy_log_dir / "run-legacy.jsonl").write_text("legacy\n", encoding="utf-8")

        # First create a log entry
        result = updater.drop_team(tid, "Jar 1", plan=plan)
        log_path = Path(updater.log_update(result))

        import json
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.read())

        assert log_path.parent == export_dir
        assert entry["type"] == "tournament_update"
        assert entry["tournament_id"] == tid
        assert entry["operation"] == "team_drop"
        assert entry["success"] is True
        assert "Jar 1" in entry.get("changes", {}).get("team_removed", "")


# ---------------------------------------------------------------------------
# Test 5: Add tournament
# ---------------------------------------------------------------------------


class TestAddTournament:
    def test_add_valid_tournament(self, six_team_tournament: Tournament, tmp_path: Any):
        """Adding a tournament with valid teams should succeed and generate round-robin."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        team_labels = ["Jar 1", "Kongsberg 1", "Skien 1"]
        new_date = date(2027, 3, 14)

        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=team_labels,
            tournament_date=new_date,
            arena="Jarhallen",
            host_club="Jar",
        )

        assert result.success is True, f"Add failed: {result.summary_nb}"
        assert result.operation == "add_tournament"
        assert result.tournament_id != ""

        # Plan should now have 2 tournaments
        assert len(plan.tournaments) == 2
        new_t = plan.tournaments[-1]
        assert new_t.date == new_date
        assert new_t.age_group == "U10"
        assert new_t.arena == "Jarhallen"
        assert len(new_t.teams) == 3
        assert len(new_t.games) > 0, "Should have round-robin games generated"

        # Verify games are valid (every pair plays once)
        from collections import Counter
        pair_counts: Counter = Counter()
        for game in new_t.games:
            pair = frozenset((game.home.label, game.away.label))
            pair_counts[pair] += 1
        assert len(pair_counts) == 3  # C(3,2) = 3 pairings
        for pair, count in pair_counts.items():
            assert count == 1, f"Pair {pair} appears {count} times (expected 1)"

    def test_add_tournament_missing_team(self, six_team_tournament: Tournament, tmp_path: Any):
        """Adding with a team not in the plan should fail with a clear message."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=["Jar 1", "Nonexistent Team"],
            tournament_date=date(2027, 3, 14),
            arena="Jarhallen",
        )

        assert result.success is False
        assert "ikke funnet" in result.summary_nb.lower()
        assert "Nonexistent" in result.summary_nb

    def test_add_tournament_wrong_age_group(self, six_team_tournament: Tournament, tmp_path: Any):
        """Adding with a team from a different age group should fail."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        # All teams in six_team_tournament are U10. Try to add a U12 tournament.
        result = updater.add_tournament(
            plan=plan,
            age_group="U12",
            team_labels=["Jar 1", "Kongsberg 1"],
            tournament_date=date(2027, 3, 14),
            arena="Jarhallen",
        )

        assert result.success is False
        assert "feil aldersgruppe" in result.summary_nb.lower() or "U12" in result.summary_nb

    def test_add_tournament_too_few_teams(self, six_team_tournament: Tournament, tmp_path: Any):
        """Adding with fewer than 2 teams should fail."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=["Jar 1"],
            tournament_date=date(2027, 3, 14),
            arena="Jarhallen",
        )

        assert result.success is False
        assert "minst 2" in result.summary_nb.lower()

    def test_add_tournament_date_conflict_blocked(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """Adding on an already-occupied date should be blocked unless forced."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        same_date = six_team_tournament.date  # Same date as existing tournament

        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=["Jar 1", "Ringerike 1", "Skien 1"],
            tournament_date=same_date,
            arena="Ringerikshallen",
            force=False,
        )

        assert result.success is False
        assert "konflikter" in result.summary_nb.lower() or "allerede" in result.summary_nb.lower()

        # With force=True, should succeed
        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=["Jar 1", "Ringerike 1", "Skien 1"],
            tournament_date=same_date,
            arena="Ringerikshallen",
            force=True,
        )

        assert result.success is True
        assert len(plan.tournaments) == 2

    def test_add_tournament_non_weekend_blocked(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """Adding on a weekday should be blocked."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        wednesday = date(2027, 3, 10)  # Wednesday
        assert wednesday.weekday() == 2

        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=["Jar 1", "Kongsberg 1"],
            tournament_date=wednesday,
            arena="Jarhallen",
            force=False,
        )

        assert result.success is False

    def test_add_tournament_plans_sorted_by_date(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """Tournaments should remain sorted by date after adding."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        # Add a tournament earlier than the existing one
        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=["Jar 1", "Skien 1"],
            tournament_date=date(2026, 10, 17),  # Earlier than 2027-01-16
            arena="Jarhallen",
            force=True,
        )
        assert result.success is True

        # Add one later
        result = updater.add_tournament(
            plan=plan,
            age_group="U10",
            team_labels=["Kongsberg 1", "Ringerike 1"],
            tournament_date=date(2027, 4, 4),
            arena="Kongsberghallen",
            force=True,
        )
        assert result.success is True

        # Verify sorting
        dates = [t.date for t in plan.tournaments]
        assert dates == sorted(dates), f"Tournaments not sorted: {dates}"


# ---------------------------------------------------------------------------
# Test 6: Remove tournament
# ---------------------------------------------------------------------------


class TestRemoveTournament:
    def test_remove_valid_tournament(self, six_team_tournament: Tournament, tmp_path: Any):
        """Removing a tournament should delete it from the plan."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)
        tid = six_team_tournament.id

        assert len(plan.tournaments) == 1

        result = updater.remove_tournament(plan, tid)

        assert result.success is True, f"Remove failed: {result.summary_nb}"
        assert result.operation == "remove_tournament"
        assert len(plan.tournaments) == 0

        # Changes should contain the removed tournament's data
        assert result.changes["id"] == tid
        assert result.changes["age_group"] == "U10"

    def test_remove_nonexistent_tournament(
        self, six_team_tournament: Tournament, tmp_path: Any
    ):
        """Removing a non-existent tournament ID should raise ValueError."""
        plan = SeasonPlan(tournaments=[six_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        with pytest.raises(ValueError, match="ikke funnet"):
            updater.remove_tournament(plan, "nonexistent-id")

    def test_remove_preserves_other_tournaments(
        self, two_tournament_plan, tmp_path: Any
    ):
        """Removing one tournament should leave others intact."""
        plan, t1, t2 = two_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        result = updater.remove_tournament(plan, t1.id)

        assert result.success is True
        assert len(plan.tournaments) == 1
        remaining = plan.tournaments[0]
        assert remaining.id == t2.id
        assert remaining.date == t2.date
        assert len(remaining.teams) == len(t2.teams)

    def test_remove_and_add_sequence(self, two_tournament_plan, tmp_path: Any):
        """Removing one tournament and adding a new one should work as a sequence."""
        plan, t1, t2 = two_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        updater = TournamentUpdater(state=state)

        original_count = len(plan.tournaments)

        # Remove t1
        result = updater.remove_tournament(plan, t1.id)
        assert result.success is True
        assert len(plan.tournaments) == original_count - 1

        # Add a new tournament using teams from the remaining tournament (t2)
        team_labels = [t.label for t in t2.teams]
        # t2 tournament is U11 but its teams are U10 — use team's actual age group
        actual_age_group = t2.teams[0].age_group if t2.teams else t2.age_group
        result = updater.add_tournament(
            plan=plan,
            age_group=actual_age_group,
            team_labels=team_labels,
            tournament_date=date(2027, 3, 14),
            arena="Kongsberghallen",
            force=True,
        )
        assert result.success is True
        assert len(plan.tournaments) == original_count

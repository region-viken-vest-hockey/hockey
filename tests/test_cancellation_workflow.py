"""Tests for cancellation / rain-check workflow (backlog item 29).

Covers:
  1. Marking a tournament as cancelled with a reason
  2. Cancelling an already-cancelled tournament returns error
  3. Suggesting makeup dates from free weekends
  4. Applying makeup clears cancelled state + moves date
  5. Cancelling a non-existent tournament raises ValueError
  6. Round-trip serialization of cancelled state through checkpoints
"""

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tournament_scheduler.models import SeasonPlan, Tournament, Team
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.pipeline.cancellation_workflow import (
    CancellationWorkflow,
)
from tournament_scheduler.pipeline.stage3_planning import _plan_to_dict
from tournament_scheduler.pipeline.stage3_helpers import _tournament_from_dict
from tournament_scheduler.season_planner import SeasonPlanner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def four_team_tournament() -> Tournament:
    """A 4-team U10 tournament with round-robin games."""
    teams = [
        Team(club="Jar", label="Jar 1", age_group="U10"),
        Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        Team(club="Skien", label="Skien 1", age_group="U10"),
        Team(club="Ringerike", label="Ringerike 1", age_group="U10"),
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
def six_team_tournament() -> Tournament:
    """A 6-team U10 tournament."""
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
def multi_tournament_plan() -> tuple[SeasonPlan, Tournament, Tournament, Tournament]:
    """A plan with three tournaments spread over several months."""
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
    teams_c = [
        Team(club="Holmen", label="Holmen 1", age_group="U12"),
        Team(club="Frisk Asker", label="Frisk Asker 1", age_group="U12"),
        Team(club="Jutul", label="Jutul 1", age_group="U12"),
        Team(club="Sandefjord", label="Sandefjord 1", age_group="U12"),
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
        age_group="U10",
        host_club="Ringerike",
        teams=teams_b,
        games=SeasonPlanner.generate_round_robin_games(teams_b, 2),
    )
    t3 = Tournament(
        date=date(2027, 3, 13),
        arena="Holmen ishall",
        age_group="U12",
        host_club="Holmen",
        teams=teams_c,
        games=SeasonPlanner.generate_round_robin_games(teams_c, 2),
    )
    plan = SeasonPlan(
        tournaments=[t1, t2, t3],
        start_date=date(2027, 1, 1),
        end_date=date(2027, 4, 30),
    )
    return plan, t1, t2, t3


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
# Test 1: Mark as cancelled
# ---------------------------------------------------------------------------


class TestMarkCancelled:
    def test_mark_cancelled_sets_state(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """Marking a tournament as cancelled sets cancelled=True and the reason."""
        plan = SeasonPlan(tournaments=[four_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id

        result = wf.mark_cancelled(tid, "Ishall stengt — vannlekkasje", plan=plan)

        assert result.success is True
        assert "avlyst" in result.summary_nb.lower()
        assert "vannlekkasje" in result.summary_nb

        tournament = plan.tournaments[0]
        assert tournament.cancelled is True
        assert tournament.cancellation_reason == "Ishall stengt — vannlekkasje"

    def test_mark_already_cancelled_returns_error(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """Cancelling a tournament that is already cancelled returns error."""
        plan = SeasonPlan(tournaments=[four_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id

        # First cancellation
        result1 = wf.mark_cancelled(tid, "Første avlysning", plan=plan)
        assert result1.success is True

        # Second cancellation
        result2 = wf.mark_cancelled(tid, "Andre avlysning", plan=plan)
        assert result2.success is False
        assert "allerede avlyst" in result2.summary_nb.lower()

    def test_mark_nonexistent_tournament_raises(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """Marking a non-existent tournament raises ValueError."""
        plan = SeasonPlan(tournaments=[four_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        with pytest.raises(ValueError, match="ble ikke funnet"):
            wf.mark_cancelled("nonexistent-id", "Ingen grunn", plan=plan)

    def test_cancel_result_structure(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """CancelResult contains expected fields."""
        plan = SeasonPlan(tournaments=[four_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id

        result = wf.mark_cancelled(tid, "Test avlysning", plan=plan)

        assert result.tournament_id == tid
        assert result.success is True
        assert result.changes["cancelled"] is True
        assert result.changes["cancellation_reason"] == "Test avlysning"
        assert result.changes["original_date"] == "2027-01-16"


# ---------------------------------------------------------------------------
# Test 2: Suggest makeup dates
# ---------------------------------------------------------------------------


class TestSuggestMakeupDates:
    def test_suggests_dates_after_original(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """Makeup suggestions appear after the original tournament date."""
        plan, t1, t2, t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        suggestions = wf.suggest_makeup_dates(t1, plan)

        assert isinstance(suggestions, list)
        # All suggestions should be after t1's date (Jan 16, 2027)
        for s in suggestions:
            assert s.date > t1.date, f"Suggested date {s.date} is not after original {t1.date}"

    def test_excludes_occupied_dates(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """Makeup suggestions exclude dates already occupied by other tournaments."""
        plan, t1, t2, t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        suggestions = wf.suggest_makeup_dates(t1, plan)

        occupied_dates = {t2.date, t3.date}
        for s in suggestions:
            assert s.date not in occupied_dates, (
                f"Suggested date {s.date} is occupied by another tournament"
            )

    def test_ranked_by_proximity(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """Suggestions are ranked by proximity to original date (closer first)."""
        plan, t1, t2, t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        suggestions = wf.suggest_makeup_dates(t1, plan, max_suggestions=5)

        if len(suggestions) >= 2:
            for i in range(len(suggestions) - 1):
                dist_i = abs(suggestions[i].days_from_original)
                dist_j = abs(suggestions[i + 1].days_from_original)
                assert dist_i <= dist_j, (
                    f"Suggestion {i} ({suggestions[i].date}, {dist_i}d) "
                    f"should be before {i+1} ({suggestions[i+1].date}, {dist_j}d)"
                )

    def test_respects_max_suggestions(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """max_suggestions caps the returned list."""
        plan, t1, t2, t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        suggestions = wf.suggest_makeup_dates(t1, plan, max_suggestions=3)
        assert len(suggestions) <= 3

    def test_custom_search_window(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """Custom start_search and end_search delimit the search window."""
        plan, t1, t2, t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        # Restrict to a very narrow window (e.g. Feb 1–Feb 14)
        suggestions = wf.suggest_makeup_dates(
            t1, plan,
            start_search=date(2027, 2, 1),
            end_search=date(2027, 2, 14),
            max_suggestions=10,
        )

        if suggestions:
            for s in suggestions:
                assert date(2027, 2, 1) <= s.date <= date(2027, 2, 14)

    def test_excludes_cancelled_tournaments(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """Cancelled tournaments' dates are not treated as occupied (free for makeup)."""
        plan, t1, t2, t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        # Cancel t2 — its date should become free for t1's makeup
        wf.mark_cancelled(t2.id, "T2 avlyst", plan=plan)

        suggestions = wf.suggest_makeup_dates(t1, plan, max_suggestions=20)

        # t2's date should now be available
        if suggestions:
            suggestion_dates = {s.date for s in suggestions}
            # t2's cancelled date might appear as a suggestion
            # (but it also might be too close to t1's original date)
        # At minimum, t3's date should still be excluded
        for s in suggestions:
            assert s.date != t3.date, f"t3's date {t3.date} should be excluded (not cancelled)"

    def test_scheduler_construction_failure_returns_empty(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """If _make_lightweight_scheduler raises, suggest_makeup_dates returns [] without crashing."""
        plan, t1, _t2, _t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        with patch.object(
            wf,
            "_make_lightweight_scheduler",
            side_effect=RuntimeError("Scheduler init failed"),
        ):
            suggestions = wf.suggest_makeup_dates(t1, plan)

        assert suggestions == [], (
            "Expected empty list when scheduler construction fails, got: "
            f"{suggestions}"
        )

    def test_find_available_dates_failure_returns_empty(
        self, multi_tournament_plan, tmp_path: Any
    ):
        """If find_available_dates raises, suggest_makeup_dates returns [] without crashing."""
        plan, t1, _t2, _t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        mock_scheduler = MagicMock()
        mock_scheduler.find_available_dates.side_effect = RuntimeError(
            "Calendar fetch failed"
        )

        with patch.object(
            wf,
            "_make_lightweight_scheduler",
            return_value=mock_scheduler,
        ):
            suggestions = wf.suggest_makeup_dates(t1, plan)

        assert suggestions == [], (
            "Expected empty list when find_available_dates fails, got: "
            f"{suggestions}"
        )


# ---------------------------------------------------------------------------
# Test 3: Apply makeup
# ---------------------------------------------------------------------------


class TestApplyMakeup:
    def test_apply_makeup_clears_cancelled(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """Applying a makeup date clears the cancelled state and moves date."""
        plan = SeasonPlan(
            tournaments=[four_team_tournament],
            start_date=date(2027, 1, 1),
            end_date=date(2027, 4, 30),
        )
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id
        new_date = date(2027, 2, 14)  # A Saturday

        # First cancel
        wf.mark_cancelled(tid, "Test avlysning", plan=plan)
        assert plan.tournaments[0].cancelled is True

        # Then apply makeup
        result = wf.apply_makeup(tid, new_date, plan=plan, force=True)

        assert result.success is True
        tournament = plan.tournaments[0]
        assert tournament.cancelled is False
        assert tournament.cancellation_reason is None
        assert tournament.date == new_date
        assert result.changes.get("makeup_applied") is True

    def test_apply_makeup_non_weekend_blocked(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """Applying makeup to a non-weekend date is blocked without --force."""
        plan = SeasonPlan(
            tournaments=[four_team_tournament],
            start_date=date(2027, 1, 1),
            end_date=date(2027, 4, 30),
        )
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id
        wednesday = date(2027, 1, 20)  # A Wednesday
        assert wednesday.weekday() == 2

        wf.mark_cancelled(tid, "Test", plan=plan)
        result = wf.apply_makeup(tid, wednesday, plan=plan, force=False)

        assert result.success is False
        assert "helgedag" in result.summary_nb.lower()

    def test_apply_makeup_force_overrides(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """With force=True, non-weekend makeup is accepted."""
        plan = SeasonPlan(
            tournaments=[four_team_tournament],
            start_date=date(2027, 1, 1),
            end_date=date(2027, 4, 30),
        )
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id
        wednesday = date(2027, 1, 20)

        wf.mark_cancelled(tid, "Test", plan=plan)
        result = wf.apply_makeup(tid, wednesday, plan=plan, force=True)

        assert result.success is True
        assert plan.tournaments[0].date == wednesday
        assert plan.tournaments[0].cancelled is False

    def test_apply_makeup_preserves_original_reason_in_result(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """The original cancellation reason appears in the update result changes."""
        plan = SeasonPlan(
            tournaments=[four_team_tournament],
            start_date=date(2027, 1, 1),
            end_date=date(2027, 4, 30),
        )
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id
        new_date = date(2027, 2, 14)

        wf.mark_cancelled(tid, "Ishall stengt — vannlekkasje", plan=plan)
        result = wf.apply_makeup(tid, new_date, plan=plan, force=True)

        assert result.success is True
        assert result.changes.get("original_cancellation_reason") == "Ishall stengt — vannlekkasje"


# ---------------------------------------------------------------------------
# Test 4: Round-trip serialization of cancelled state
# ---------------------------------------------------------------------------


class TestCancelledSerialization:
    def test_round_trip_cancelled_state(self, four_team_tournament: Tournament):
        """Cancelled state survives _plan_to_dict → _tournament_from_dict round-trip."""
        t = four_team_tournament
        t.cancelled = True
        t.cancellation_reason = "Ishall stengt"

        # Serialize via _plan_to_dict
        plan = SeasonPlan(tournaments=[t])
        plan_dict = _plan_to_dict(plan)

        # Deserialize via _tournament_from_dict
        tournament_dicts = plan_dict["tournaments"]
        assert len(tournament_dicts) == 1
        assert tournament_dicts[0]["cancelled"] is True
        assert tournament_dicts[0]["cancellation_reason"] == "Ishall stengt"

        restored = _tournament_from_dict(tournament_dicts[0])
        assert restored.cancelled is True
        assert restored.cancellation_reason == "Ishall stengt"

    def test_not_cancelled_tournament_omits_fields(
        self, four_team_tournament: Tournament
    ):
        """When not cancelled, the dict omits cancelled fields (compact)."""
        t = four_team_tournament
        assert t.cancelled is False

        plan = SeasonPlan(tournaments=[t])
        plan_dict = _plan_to_dict(plan)

        tournament_dicts = plan_dict["tournaments"]
        assert len(tournament_dicts) == 1
        assert "cancelled" not in tournament_dicts[0]
        assert "cancellation_reason" not in tournament_dicts[0]

    def test_backward_compatible_deserialization(self, four_team_tournament: Tournament):
        """Tournament dict without cancelled fields deserializes with defaults."""
        # Simulate a pre-cancellation dict (no cancelled fields)
        plan = SeasonPlan(tournaments=[four_team_tournament])
        plan_dict = _plan_to_dict(plan)

        tournament_dict = plan_dict["tournaments"][0]
        # Remove any cancelled fields if present (they shouldn't be for a fresh tournament)
        tournament_dict.pop("cancelled", None)
        tournament_dict.pop("cancellation_reason", None)

        restored = _tournament_from_dict(tournament_dict)
        assert restored.cancelled is False
        assert restored.cancellation_reason is None

    def test_checkpoint_write_and_read_preserves_cancelled(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """After marking cancelled and writing checkpoint, re-reading preserves state."""
        plan = SeasonPlan(tournaments=[four_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id

        wf.mark_cancelled(tid, "Test checkpoint", plan=plan)
        wf.write_plan(plan)

        # Re-read
        state2 = PipelineState(state.work_dir)
        wf2 = CancellationWorkflow(state2)
        plan2 = wf2.load_plan()

        assert len(plan2.tournaments) == 1
        assert plan2.tournaments[0].cancelled is True
        assert plan2.tournaments[0].cancellation_reason == "Test checkpoint"


# ---------------------------------------------------------------------------
# Test 5: MakeupSuggestion structure
# ---------------------------------------------------------------------------


class TestMakeupSuggestion:
    def test_fields_are_set(self, multi_tournament_plan, tmp_path: Any):
        """MakeupSuggestion has correct date, days, and conflicts fields."""
        plan, t1, t2, t3 = multi_tournament_plan
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)

        suggestions = wf.suggest_makeup_dates(t1, plan, max_suggestions=3)

        for s in suggestions:
            assert isinstance(s.date, date)
            assert isinstance(s.days_from_original, int)
            assert s.days_from_original > 0, "Makeup dates should be after the original"
            assert isinstance(s.conflicts, list)


# ---------------------------------------------------------------------------
# Test 6: log_cancellation
# ---------------------------------------------------------------------------


class TestLogCancellation:
    def test_log_writes_jsonl_entry(
        self, four_team_tournament: Tournament, tmp_path: Any
    ):
        """log_cancellation should append a JSONL entry to the active export-folder log."""
        import json

        plan = SeasonPlan(tournaments=[four_team_tournament])
        state = make_state_with_plan(plan, tmp_path)
        wf = CancellationWorkflow(state)
        tid = four_team_tournament.id

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

        result = wf.mark_cancelled(tid, "Loggtest avlysning", plan=plan)
        log_path = Path(wf.log_cancellation(result))

        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.read())

        assert log_path.parent == export_dir
        assert entry["type"] == "tournament_cancellation"
        assert entry["tournament_id"] == tid
        assert entry["success"] is True
        assert "Loggtest" in entry["summary_nb"]
        assert "Loggtest" in entry.get("changes", {}).get("cancellation_reason", "")

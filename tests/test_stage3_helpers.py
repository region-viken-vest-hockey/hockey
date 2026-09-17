"""Unit tests for stage3_helpers._build_events_by_club logging and _resolve_plan_dict."""

from __future__ import annotations

from unittest.mock import patch


from tournament_scheduler.pipeline.stage3_helpers import (
    _build_club_busy_intervals,
    _build_club_calendar_status,
    _build_events_by_club,
    _resolve_plan_dict,
)
from tournament_scheduler.serialization.season_plan import season_plan_from_dict, season_plan_to_dict
from tournament_scheduler.models import SeasonPlan


VALID_EVENT = {
    "date": "2025-11-01",
    "name": "Ishockey",
    "datetime": "2025-11-01T10:00:00",
    "duration_hours": 2.0,
}

MISSING_DATETIME_EVENT = {
    "date": "2025-11-02",
    "name": "Bad event",
    # "datetime" key intentionally omitted — will raise KeyError
}

BAD_DATETIME_EVENT = {
    "date": "2025-11-03",
    "name": "Also bad",
    "datetime": "not-a-valid-iso-string",  # will raise ValueError
}


class TestBuildEventsByClubLogging:
    """_build_events_by_club should log a warning for each malformed event."""

    def test_warning_emitted_for_missing_datetime_key(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Kongsberg": [MISSING_DATETIME_EVENT],
            }
        }
        with patch(
            "tournament_scheduler.pipeline.stage3_helpers.logger"
        ) as mock_logger:
            _build_events_by_club(scraping_result)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        # First positional arg is the format string; remaining args fill placeholders.
        assert "Kongsberg" in call_args.args

    def test_warning_emitted_for_bad_iso_string(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Ringerike": [BAD_DATETIME_EVENT],
            }
        }
        with patch(
            "tournament_scheduler.pipeline.stage3_helpers.logger"
        ) as mock_logger:
            _build_events_by_club(scraping_result)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "Ringerike" in call_args.args

    def test_well_formed_events_still_returned(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Skien": [VALID_EVENT],
            }
        }
        result = _build_events_by_club(scraping_result)

        assert "Skien" in result
        assert len(result["Skien"]) == 1
        assert result["Skien"][0].name == "Ishockey"

    def test_well_formed_events_returned_alongside_malformed(self) -> None:
        """Good events in the same club list must survive a bad neighbour."""
        scraping_result = {
            "events_by_club": {
                "Jutul": [MISSING_DATETIME_EVENT, VALID_EVENT],
            }
        }
        with patch(
            "tournament_scheduler.pipeline.stage3_helpers.logger"
        ) as mock_logger:
            result = _build_events_by_club(scraping_result)

        # One warning for the bad event.
        mock_logger.warning.assert_called_once()
        # Good event survives.
        assert len(result["Jutul"]) == 1
        assert result["Jutul"][0].name == "Ishockey"

    def test_warning_count_matches_malformed_event_count(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Holmen": [
                    MISSING_DATETIME_EVENT,
                    BAD_DATETIME_EVENT,
                    VALID_EVENT,
                ],
            }
        }
        with patch(
            "tournament_scheduler.pipeline.stage3_helpers.logger"
        ) as mock_logger:
            result = _build_events_by_club(scraping_result)

        assert mock_logger.warning.call_count == 2
        assert len(result["Holmen"]) == 1

    def test_no_warnings_for_entirely_valid_input(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Jar": [VALID_EVENT],
            }
        }
        with patch(
            "tournament_scheduler.pipeline.stage3_helpers.logger"
        ) as mock_logger:
            _build_events_by_club(scraping_result)

        mock_logger.warning.assert_not_called()

    def test_returns_empty_dict_for_none_input(self) -> None:
        result = _build_events_by_club(None)
        assert result == {}

    def test_returns_empty_dict_for_missing_events_by_club_key(self) -> None:
        result = _build_events_by_club({"other_key": "value"})
        assert result == {}


# ---------------------------------------------------------------------------
# _build_club_calendar_status (issue #262 P0)
# ---------------------------------------------------------------------------


class TestBuildClubCalendarStatus:
    """A missing/absent status must default to 'unknown', never silently 'known'."""

    def test_returns_empty_dict_for_none_input(self) -> None:
        assert _build_club_calendar_status(None) == {}

    def test_returns_empty_dict_for_missing_key(self) -> None:
        assert _build_club_calendar_status({"other_key": "value"}) == {}

    def test_returns_empty_dict_for_non_dict_value(self) -> None:
        assert _build_club_calendar_status({"club_calendar_status": "not-a-dict"}) == {}

    def test_passes_through_known_and_unknown_values(self) -> None:
        result = _build_club_calendar_status(
            {"club_calendar_status": {"Sandefjord Penguins": "known", "Tønsberg": "unknown"}}
        )
        assert result == {"Sandefjord Penguins": "known", "Tønsberg": "unknown"}


# ---------------------------------------------------------------------------
# _build_club_busy_intervals (issue #264 P0)
# ---------------------------------------------------------------------------


class TestBuildClubBusyIntervals:
    """club_busy_intervals must carry real interval-level evidence, not just
    coarse per-club/per-date facts (see club_busy_dates)."""

    def test_returns_empty_dict_for_none_input(self) -> None:
        assert _build_club_busy_intervals(None) == {}

    def test_returns_empty_dict_for_missing_events_by_club(self) -> None:
        assert _build_club_busy_intervals({"other_key": "value"}) == {}

    def test_single_event_produces_one_interval(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Jar": [
                    {
                        "date": "01.11.2025",
                        "name": "Trening",
                        "datetime": "2025-11-01T10:00:00",
                        "duration_hours": 2.0,
                    }
                ]
            }
        }
        result = _build_club_busy_intervals(scraping_result)
        assert result == {
            "Jar": [
                {
                    "date": "2025-11-01",
                    "start": "10:00",
                    "end": "12:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                    "calendar_event": "Trening",
                }
            ]
        }

    def test_partial_day_leaves_rest_of_day_implicitly_free(self) -> None:
        """A single morning booking must not make the whole date look busy --
        callers reading only this club/date's entries must be able to see the
        booking ends at noon, leaving the afternoon uncovered here."""
        scraping_result = {
            "events_by_club": {
                "Jar": [
                    {
                        "date": "01.11.2025",
                        "name": "Trening",
                        "datetime": "2025-11-01T08:00:00",
                        "duration_hours": 4.0,
                    }
                ]
            }
        }
        result = _build_club_busy_intervals(scraping_result)
        assert result["Jar"] == [
            {
                "date": "2025-11-01",
                "start": "08:00",
                "end": "12:00",
                "kind": "external",
                "availability": "fixed_busy",
                "calendar_event": "Trening",
            }
        ]

    def test_overnight_event_splits_across_two_dates(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Jar": [
                    {
                        "date": "01.11.2025",
                        "name": "Sen trening",
                        "datetime": "2025-11-01T23:00:00",
                        "duration_hours": 3.0,
                    }
                ]
            }
        }
        result = _build_club_busy_intervals(scraping_result)
        assert result["Jar"] == [
            {
                "date": "2025-11-01",
                "start": "23:00",
                "end": "24:00",
                "kind": "external",
                "availability": "fixed_busy",
                "calendar_event": "Sen trening",
            },
            {
                "date": "2025-11-02",
                "start": "00:00",
                "end": "02:00",
                "kind": "external",
                "availability": "fixed_busy",
                "calendar_event": "Sen trening",
            },
        ]

    def test_zero_duration_event_produces_no_interval(self) -> None:
        scraping_result = {
            "events_by_club": {
                "Jar": [
                    {
                        "date": "01.11.2025",
                        "name": "Placeholder",
                        "datetime": "2025-11-01T10:00:00",
                        "duration_hours": 0.0,
                    }
                ]
            }
        }
        assert _build_club_busy_intervals(scraping_result) == {}

    def test_sandefjord_fixed_allocation_encodes_as_busy_intervals(self) -> None:
        """issue #264 P0 acceptance: Sandefjord's fixed weekend allocation
        (issue #261) must keep working as deterministic availability
        evidence through the same events_by_club -> busy-intervals path as a
        real scrape, not a special case."""
        from datetime import date

        from tournament_scheduler.sandefjord_allocation import (
            SANDEFJORD_CLUB_NAME,
            sandefjord_fixed_busy_events,
        )

        saturday = date(2026, 10, 3)  # a Saturday
        events = sandefjord_fixed_busy_events(saturday, saturday)
        scraping_result = {
            "events_by_club": {
                SANDEFJORD_CLUB_NAME: [
                    {
                        "date": e.date,
                        "name": e.name,
                        "datetime": e.datetime.isoformat(),
                        "duration_hours": e.duration_hours,
                    }
                    for e in events
                ]
            }
        }
        result = _build_club_busy_intervals(scraping_result)
        intervals = result[SANDEFJORD_CLUB_NAME]
        # Busy 00:00-15:00 and 18:00-24:00, free 15:00-18:00 (the fixed window).
        # Sandefjord's fixed allocation is genuinely unavailable ice, so it
        # stays fixed_busy -- never movable host-controlled capacity.
        assert intervals == [
            {
                "date": "2026-10-03",
                "start": "00:00",
                "end": "15:00",
                "kind": "external",
                "availability": "fixed_busy",
                "calendar_event": events[0].name,
            },
            {
                "date": "2026-10-03",
                "start": "18:00",
                "end": "24:00",
                "kind": "external",
                "availability": "fixed_busy",
                "calendar_event": events[1].name,
            },
        ]

    def test_kongsberg_open_ice_is_movable_busy(self) -> None:
        """issue #373: Kongsberg's 'Åpen ishall' is host-controlled open ice,
        not an external booking -- it must normalize to availability
        'movable_busy' (with the event title and host-action reason preserved),
        while the same phrase at a club without the configured rule stays a
        fixed external booking."""
        scraping_result = {
            "events_by_club": {
                "Kongsberg": [
                    {
                        "date": "21.11.2026",
                        "name": "Åpen ishall",
                        "datetime": "2026-11-21T10:00:00",
                        "duration_hours": 4.0,
                    }
                ],
                "Jar": [
                    {
                        "date": "21.11.2026",
                        "name": "Åpen ishall",
                        "datetime": "2026-11-21T10:00:00",
                        "duration_hours": 4.0,
                    }
                ],
            }
        }
        result = _build_club_busy_intervals(scraping_result)
        kongsberg_interval = result["Kongsberg"][0]
        assert kongsberg_interval["availability"] == "movable_busy"
        assert kongsberg_interval["kind"] == "club_controlled"
        assert kongsberg_interval["calendar_event"] == "Åpen ishall"
        assert "open ice" in kongsberg_interval["reason"]
        # The classification is source/club-scoped, not a global phrase table.
        assert result["Jar"][0]["availability"] == "fixed_busy"

# ---------------------------------------------------------------------------
# _resolve_plan_dict
# ---------------------------------------------------------------------------


class TestResolvePlanDict:
    def test_returns_plain_dict_unchanged(self):
        d = {"key": "value", "tournaments": []}
        assert _resolve_plan_dict(d) is d

    def test_converts_object_with_dunder_dict(self):
        """An object with __dict__ (e.g. SeasonPlan) should be converted via _plan_to_dict."""
        from unittest.mock import MagicMock, patch

        mock_plan = MagicMock(spec=[])
        # Give it a __dict__ so hasattr check passes
        mock_plan.__dict__ = {"tournaments": []}
        expected = {"converted": True}
        with patch(
            "tournament_scheduler.serialization.season_plan.season_plan_to_dict",
            return_value=expected,
        ) as mock_p2d:
            result = _resolve_plan_dict(mock_plan)
        mock_p2d.assert_called_once_with(mock_plan)
        assert result == expected

    def test_returns_empty_dict_for_none(self):
        result = _resolve_plan_dict(None)
        assert result == {}

    def test_returns_empty_dict_for_non_dict_non_object(self):
        result = _resolve_plan_dict(42)
        assert result == {}

    def test_returns_empty_dict_for_empty_input(self):
        result = _resolve_plan_dict({})
        assert result == {}


# ---------------------------------------------------------------------------
# Round-trip: game_count_spread_by_age_group
# ---------------------------------------------------------------------------


class TestGameCountSpreadByAgeGroupRoundTrip:
    """Ensure game_count_spread_by_age_group survives _plan_to_dict → _dict_to_plan."""

    def _make_minimal_plan(self, spread_by_ag: dict) -> SeasonPlan:
        return SeasonPlan(
            tournaments=[],
            team_game_counts={},
            game_count_spread=0,
            game_count_spread_by_age_group=spread_by_ag,
        )

    def test_populated_dict_survives_round_trip(self):
        original = {"U7": 0, "U10": 3, "U12": 6}
        plan = self._make_minimal_plan(original)
        d = season_plan_to_dict(plan)
        restored = season_plan_from_dict(d)
        assert restored.game_count_spread_by_age_group == original

    def test_empty_dict_survives_round_trip(self):
        plan = self._make_minimal_plan({})
        d = season_plan_to_dict(plan)
        restored = season_plan_from_dict(d)
        assert restored.game_count_spread_by_age_group == {}

    def test_field_present_in_serialized_dict(self):
        spread = {"U9": 2}
        plan = self._make_minimal_plan(spread)
        d = season_plan_to_dict(plan)
        assert "game_count_spread_by_age_group" in d
        assert d["game_count_spread_by_age_group"] == spread

    def test_missing_key_defaults_to_empty_dict_on_deserialize(self):
        """Older checkpoints without the key should deserialize safely."""
        d = {"tournaments": [], "game_count_spread": 0}
        plan = season_plan_from_dict(d)
        assert plan.game_count_spread_by_age_group == {}


# ---------------------------------------------------------------------------
# Round-trip: unresolved_hosting_obligations / unresolved_external_conflicts /
# unresolved_participation_shortfalls
# ---------------------------------------------------------------------------


class TestUnresolvedManualPlacementFieldsRoundTrip:
    """These three non-blocking manual-placement lists must survive
    _plan_to_dict -> _dict_to_plan, since Stage 3 and Stage 4 routinely run
    as separate process invocations (each `--resume-from N` call) and the
    only channel between them is this JSON checkpoint dict."""

    def _make_minimal_plan(self, **kwargs) -> SeasonPlan:
        return SeasonPlan(tournaments=[], team_game_counts={}, game_count_spread=0, **kwargs)

    def test_all_three_fields_survive_round_trip(self):
        hosting = [{"club": "Jar", "age_group": "U10", "reason": "r1"}]
        external = [{"tournament_id": "t1", "host_club": "Jar", "age_group": "U10", "date": "2026-06-01", "reason": "r2"}]
        participation = [{"club": "Jar", "label": "Jar 1", "age_group": "U10", "actual": "1", "target": "3", "reason": "r3"}]
        placements = [
            {
                "age_group": "U10",
                "date": "2026-06-01",
                "period": "before_christmas",
                "candidate_hosts": ["Jar"],
                "participant_clubs": ["Jar", "Kongsberg"],
                "reason": "no_participant_host_slot",
            }
        ]
        plan = self._make_minimal_plan(
            unresolved_hosting_obligations=hosting,
            unresolved_external_conflicts=external,
            unresolved_participation_shortfalls=participation,
            unresolved_tournament_placements=placements,
        )
        d = season_plan_to_dict(plan)
        assert d["unresolved_hosting_obligations"] == hosting
        assert d["unresolved_external_conflicts"] == external
        assert d["unresolved_participation_shortfalls"] == participation
        assert d["unresolved_tournament_placements"] == placements

        restored = season_plan_from_dict(d)
        assert restored.unresolved_hosting_obligations == hosting
        assert restored.unresolved_external_conflicts == external
        assert restored.unresolved_participation_shortfalls == participation
        assert restored.unresolved_tournament_placements == placements

    def test_missing_keys_default_to_empty_lists_on_deserialize(self):
        """Older checkpoints without these keys should deserialize safely."""
        d = {"tournaments": [], "game_count_spread": 0}
        plan = season_plan_from_dict(d)
        assert plan.unresolved_hosting_obligations == []
        assert plan.unresolved_external_conflicts == []
        assert plan.unresolved_participation_shortfalls == []
        assert plan.unresolved_tournament_placements == []


class TestSharedHostDecisionsRoundTrip:
    """issue #274: shared_host_decisions (LLM/controller provenance for
    joint-club registrations) must survive the same checkpoint round-trip
    as the unresolved_* manual-placement lists."""

    def _make_minimal_plan(self, **kwargs) -> SeasonPlan:
        return SeasonPlan(tournaments=[], team_game_counts={}, game_count_spread=0, **kwargs)

    def test_survives_round_trip(self):
        decisions = [
            {
                "registration": "Kongsberg/Tønsberg",
                "age_group": "JU12",
                "chosen_club": "Tønsberg",
                "rationale": "Kongsberg already carries materially more hosting burden.",
                "decided_by": "llm",
                "decided_at": "2026-09-07T00:00:00",
            }
        ]
        plan = self._make_minimal_plan(shared_host_decisions=decisions)

        d = season_plan_to_dict(plan)
        assert d["shared_host_decisions"] == decisions

        restored = season_plan_from_dict(d)
        assert restored.shared_host_decisions == decisions

    def test_missing_key_defaults_to_empty_list_on_deserialize(self):
        d = {"tournaments": [], "game_count_spread": 0}
        plan = season_plan_from_dict(d)
        assert plan.shared_host_decisions == []

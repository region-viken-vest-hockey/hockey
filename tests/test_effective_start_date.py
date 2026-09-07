"""Tests for the issue #272 Stage 3 effective-start-date derivation."""

from datetime import date

from tournament_scheduler.effective_start_date import compute_effective_start_date


class TestComputeEffectiveStartDate:
    def test_future_configured_date_on_a_weekend_is_kept_unchanged(self):
        result = compute_effective_start_date(date(2026, 9, 19), today=date(2026, 9, 7))
        assert result.configured_start_date == date(2026, 9, 19)
        assert result.effective_start_date == date(2026, 9, 19)
        assert result.start_date_adjustment is None

    def test_future_configured_date_on_a_weekday_aligns_to_next_weekend(self):
        # Issue example: run on Monday 2026-09-07, configured 2026-10-01 ->
        # effective 2026-10-03 (first Sat/Sun on/after the configured date).
        result = compute_effective_start_date(date(2026, 10, 1), today=date(2026, 9, 7))
        assert result.effective_start_date == date(2026, 10, 3)
        assert result.start_date_adjustment is not None

    def test_past_configured_date_starts_on_first_future_weekend(self):
        # Issue example: run on Monday 2026-09-07, configured 2026-09-01 ->
        # effective 2026-09-12.
        result = compute_effective_start_date(date(2026, 9, 1), today=date(2026, 9, 7))
        assert result.effective_start_date == date(2026, 9, 12)
        assert result.start_date_adjustment == "configured start date was in the past"

    def test_configured_date_equal_to_today_is_treated_like_past(self):
        result = compute_effective_start_date(date(2026, 9, 7), today=date(2026, 9, 7))
        assert result.effective_start_date > date(2026, 9, 7)
        assert result.start_date_adjustment == "configured start date was today"

    def test_run_on_saturday_never_creates_a_same_day_tournament(self):
        saturday = date(2026, 9, 12)
        assert saturday.weekday() == 5
        result = compute_effective_start_date(date(2026, 9, 1), today=saturday)
        assert result.effective_start_date != saturday
        assert result.effective_start_date > saturday
        assert result.effective_start_date.weekday() in (5, 6)

    def test_run_on_sunday_never_creates_a_same_day_tournament(self):
        sunday = date(2026, 9, 13)
        assert sunday.weekday() == 6
        result = compute_effective_start_date(date(2026, 9, 1), today=sunday)
        assert result.effective_start_date != sunday
        assert result.effective_start_date > sunday

    def test_effective_start_date_is_always_a_weekend_day(self):
        for today_offset_weekday in range(7):
            today = date(2026, 9, 7 + today_offset_weekday)
            result = compute_effective_start_date(date(2026, 8, 1), today=today)
            assert result.effective_start_date.weekday() in (5, 6)

    def test_defaults_today_to_real_clock_when_not_provided(self):
        # Smoke test: no `today=` override still returns a well-formed result.
        result = compute_effective_start_date(date(2020, 1, 1))
        assert result.effective_start_date.weekday() in (5, 6)
        assert result.effective_start_date > date(2020, 1, 1)

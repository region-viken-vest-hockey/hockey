"""Unit tests for tournament_scheduler.pipeline.scraper_strategies."""

import pytest

from tournament_scheduler.pipeline.scraper_strategies import (
    CalendarEngine,
    ScraperStrategy,
    STRATEGIES,
    get_deterministic_scraper_type,
)


def _strategy(engine: CalendarEngine) -> ScraperStrategy:
    """Build a minimal ScraperStrategy with the given engine for testing."""
    return ScraperStrategy(engine=engine, url="https://example.com")


@pytest.mark.parametrize(
    "engine, expected",
    [
        (CalendarEngine.STYLED_CALENDAR, "styledcalendar"),
        (CalendarEngine.BOOKUP_SPA, "bookup"),
        (CalendarEngine.OUTLOOK_IFRAME, "browser"),
        (CalendarEngine.DATE_PARAM, "brp_exigo"),
        (CalendarEngine.FORUMBOOKING, "forumbooking"),
        (CalendarEngine.SPORTELLO, "sportello"),
        (CalendarEngine.TEAMUP_ICAL, "ical"),
        (CalendarEngine.GENERIC_ICAL, "ical"),
    ],
)
def test_get_deterministic_scraper_type(engine: CalendarEngine, expected: str) -> None:
    """get_deterministic_scraper_type returns the correct string for each CalendarEngine."""
    strategy = _strategy(engine)
    assert get_deterministic_scraper_type(strategy) == expected


def test_get_deterministic_scraper_type_covers_all_engines() -> None:
    """Every CalendarEngine member maps to a non-None scraper type."""
    for engine in CalendarEngine:
        strategy = _strategy(engine)
        result = get_deterministic_scraper_type(strategy)
        assert result is not None, (
            f"CalendarEngine.{engine.name} returned None — add it to get_deterministic_scraper_type"
        )


def test_sandefjord_has_no_scraper_strategy() -> None:
    """Issue #261: Sandefjord Penguins is a fixed_allocation source, not scraped."""
    assert "Sandefjord Penguins" not in STRATEGIES

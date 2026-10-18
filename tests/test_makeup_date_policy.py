"""Makeup-date suggestions apply the canonical season's holiday policy.

The makeup path must honour the same precedence as the planner and verifier:
derived holiday exclusion < explicit holiday-date exception < explicit ban.
"""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tournament_scheduler.pipeline import makeup_date_policy as policy

EXCEPTION = date(2026, 12, 19)
DERIVED_HOLIDAY_WEEKEND = date(2026, 12, 20)
BANNED = date(2026, 12, 12)


def _decisions():
    return {
        "holiday_date_exceptions": [{"date": EXCEPTION.isoformat(), "status": "active"}],
        "banned_dates": [{"date": BANNED.isoformat(), "status": "active"}],
    }


def _plan():
    return SimpleNamespace(start_date=date(2026, 9, 1), end_date=date(2027, 4, 30))


def test_inputs_come_from_canonical_season_decisions() -> None:
    canonical = {"decisions": _decisions()}
    with patch.object(policy, "load_effective_config", return_value={}), patch.object(
        policy, "resolve_canonical_state", return_value=canonical
    ):
        exceptions, bans = policy.makeup_date_policy_inputs(object(), _plan())

    assert exceptions == [EXCEPTION]
    assert bans == [BANNED]


def test_inputs_are_empty_without_canonical_season() -> None:
    with patch.object(policy, "load_effective_config", return_value={}), patch.object(
        policy, "resolve_canonical_state", return_value=None
    ):
        assert policy.makeup_date_policy_inputs(object(), _plan()) == ([], [])


def test_scheduler_applies_exception_ban_and_derived_precedence() -> None:
    scheduler = policy.make_holiday_scheduler([EXCEPTION], [BANNED])

    result = scheduler.find_available_dates(
        datetime(2026, 12, 1),
        datetime(2026, 12, 31),
    )

    assert EXCEPTION in result.available_dates
    assert DERIVED_HOLIDAY_WEEKEND not in result.available_dates
    assert BANNED not in result.available_dates

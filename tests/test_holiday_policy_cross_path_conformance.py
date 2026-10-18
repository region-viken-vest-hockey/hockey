"""Cross-path conformance for the effective holiday-date policy (issue #423).

For one window with an accepted holiday-date exception and an operator ban,
every admissibility consumer must agree on which dates are legal. A date that
is legal in one path and illegal in another is the divergence this guards.

The paths compared are the initial scheduler's checker, the normalized
planning problem's exclusions, and the forbidden-date union that the optimizer,
candidate-weekend enumeration and repair relocation read.
"""

from __future__ import annotations

from datetime import date, datetime

from tournament_scheduler.conflict_checkers.holiday_checker import HolidayConflictChecker
from tournament_scheduler.models import ConflictContext
from tournament_scheduler.planning.date_policy import (
    holiday_excluded_dates,
    problem_date_exclusions,
    problem_forbidden_dates,
)

WINDOW_START = date(2026, 12, 1)
WINDOW_END = date(2026, 12, 31)

#: Derived holiday weekend (Sat before Christmas) with an accepted exception.
EXCEPTED = date(2026, 12, 19)
#: Derived holiday weekend that is also an explicit operator ban.
BANNED = date(2026, 12, 20)
#: Derived holiday-week weekend with neither exception nor ban.
UNEXCEPTED = date(2026, 12, 26)
#: Ordinary, non-holiday weekend inside the same window.
ORDINARY = date(2026, 12, 12)


def _problem() -> dict:
    return {
        "start_date": WINDOW_START.isoformat(),
        "end_date": WINDOW_END.isoformat(),
        "holiday_date_exceptions": [EXCEPTED.isoformat()],
        "manual_adjustments": {"banned_dates": [BANNED.isoformat()]},
        "date_exclusions": [],
    }


def _checker_excluded() -> set[date]:
    checker = HolidayConflictChecker(date_exceptions=[EXCEPTED], banned_dates=[BANNED])
    context = ConflictContext(
        start_date=datetime.combine(WINDOW_START, datetime.min.time()),
        end_date=datetime.combine(WINDOW_END, datetime.min.time()),
    )
    candidates = [EXCEPTED, BANNED, UNEXCEPTED, ORDINARY]
    return set(checker.check_conflicts(candidates, context).excluded_dates)


def test_checker_and_forbidden_dates_agree_on_every_probe_date() -> None:
    forbidden = set(problem_forbidden_dates(_problem()))

    probes = {EXCEPTED, BANNED, UNEXCEPTED, ORDINARY}
    assert _checker_excluded() & probes == forbidden & probes


def test_exception_frees_only_its_own_date_on_every_path() -> None:
    problem = _problem()

    assert EXCEPTED not in problem_date_exclusions(problem)
    assert EXCEPTED not in problem_forbidden_dates(problem)
    assert EXCEPTED not in _checker_excluded()


def test_unexcepted_derived_date_stays_excluded_on_every_path() -> None:
    problem = _problem()

    assert UNEXCEPTED in holiday_excluded_dates(WINDOW_START, WINDOW_END)
    assert UNEXCEPTED in problem_date_exclusions(problem)
    assert UNEXCEPTED in problem_forbidden_dates(problem)
    assert UNEXCEPTED in _checker_excluded()


def test_operator_ban_wins_over_exception_on_every_path() -> None:
    problem = _problem()

    assert BANNED in problem_forbidden_dates(problem)
    assert BANNED in _checker_excluded()


def test_ordinary_date_is_admissible_on_every_path() -> None:
    problem = _problem()

    assert ORDINARY not in problem_forbidden_dates(problem)
    assert ORDINARY not in _checker_excluded()

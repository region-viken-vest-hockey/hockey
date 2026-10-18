"""Canonical holiday exceptions and bans reach the report plan (#423)."""

from datetime import date

from tournament_scheduler.models import SeasonPlan, Tournament
from tournament_scheduler.rules.model import build_rules_model
from tournament_scheduler.season.holiday_exceptions import project_date_policy_into_plan


def _plan() -> SeasonPlan:
    plan = SeasonPlan()
    plan.start_date = date(2026, 9, 1)
    plan.end_date = date(2027, 6, 30)
    plan.tournaments = [Tournament(id="t1", date=date(2026, 12, 19), arena="A", age_group="U10", teams=[])]
    return plan


def _decisions(*, exception_dates=(), ban_dates=()) -> dict:
    return {
        "holiday_date_exceptions": [
            {"id": f"hx-{d}", "date": d, "status": "active"} for d in exception_dates
        ],
        "banned_dates": [{"id": f"bd-{d}", "date": d, "status": "active"} for d in ban_dates],
    }


def _holiday_rule(plan: SeasonPlan) -> dict:
    return {rule["id"]: rule for rule in build_rules_model(plan)}["holiday_dates_not_used"]


def test_active_holiday_exception_lifts_report_violation():
    plan = _plan()
    project_date_policy_into_plan(plan, _decisions(exception_dates=["2026-12-19"]))

    rule = _holiday_rule(plan)

    assert plan.manual_adjustments["holiday_date_exceptions"] == ["2026-12-19"]
    assert rule["ok"] is True


def test_active_ban_keeps_date_excluded_despite_exception():
    plan = _plan()
    project_date_policy_into_plan(
        plan,
        _decisions(exception_dates=["2026-12-19"], ban_dates=["2026-12-19"]),
    )

    rule = _holiday_rule(plan)

    assert plan.manual_adjustments["banned_dates"] == ["2026-12-19"]
    assert rule["ok"] is False


def test_released_exception_is_not_projected():
    plan = _plan()
    decisions = {
        "holiday_date_exceptions": [{"id": "hx", "date": "2026-12-19", "status": "released"}],
    }
    project_date_policy_into_plan(plan, decisions)

    assert _holiday_rule(plan)["ok"] is False
    assert "holiday_date_exceptions" not in plan.manual_adjustments


def test_projection_merges_with_existing_manual_adjustments():
    plan = _plan()
    plan.manual_adjustments = {"holiday_date_exceptions": ["2026-12-26"]}
    project_date_policy_into_plan(plan, _decisions(exception_dates=["2026-12-19"]))

    assert plan.manual_adjustments["holiday_date_exceptions"] == ["2026-12-19", "2026-12-26"]


def test_empty_decisions_leave_plan_untouched():
    plan = _plan()
    project_date_policy_into_plan(plan, {})

    assert plan.manual_adjustments == {}

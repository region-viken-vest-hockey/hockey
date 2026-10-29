"""Per-tournament refresh consequences are read-only and omit unchanged tournaments (#869)."""

from __future__ import annotations

from tournament_scheduler.booking.refresh_consequences import refresh_tournament_consequences


def _report(*, bucket: str, observed: dict | None, action: str = "none") -> dict:
    comparison = {
        "status": "no_observed_interval" if observed is None else "matches",
        "effective_canonical": {"date": "2026-09-12", "start": "10:00", "end": "12:00"},
        "observed": observed,
        "same_date": True,
        "start_delta_minutes": None,
        "end_delta_minutes": None,
    }
    row = {
        "tournament_id": "t1",
        "interval_comparison": comparison,
        "latest_observed_interval": observed,
        "authority": "calendar_event_association",
        "follow_up_reasons": [],
    }
    return {
        "tournaments": [row],
        "calendar_audit": {"buckets": {bucket: ["t1"]}, "actions": {"t1": action}},
    }


def test_unchanged_tournament_produces_no_consequence_row():
    observed = {"date": "2026-09-12", "start": "10:00", "end": "12:00"}
    current = _report(bucket="confirmed_matching", observed=observed)
    proposed = _report(bucket="confirmed_matching", observed=observed)

    assert refresh_tournament_consequences(current, proposed) == []


def test_changed_bucket_and_observation_are_reported_with_action_and_delta():
    current = _report(bucket="no_calendar_coverage", observed=None)
    proposed = _report(
        bucket="unconfirmed_ambiguous",
        observed={"date": "2026-09-12", "start": "10:00", "end": "12:00"},
        action="resolve_candidate",
    )

    rows = refresh_tournament_consequences(current, proposed)

    assert [row["tournament_id"] for row in rows] == ["t1"]
    row = rows[0]
    assert row["current_bucket"] == "no_calendar_coverage"
    assert row["proposed_bucket"] == "unconfirmed_ambiguous"
    assert row["action"] == "resolve_candidate"
    assert row["fresh_observed_interval"] == {"date": "2026-09-12", "start": "10:00", "end": "12:00"}
    assert row["proposed_delta"]["status"] == "matches"

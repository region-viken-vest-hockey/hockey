"""Host-club booking status must state its evidence scope and that it is read-only (#853 item 8)."""

from __future__ import annotations

from tournament_scheduler.cli.booking_status_text import format_host_club_booking_status


def _report(rows):
    return {
        "host_club": "Jar",
        "season": "2026-2027",
        "canonical_state_revision": "rev-1",
        "tournaments": rows,
        "counts": {"needs_attention": 0},
    }


def _row(tid, *, covers):
    return {
        "tournament_id": tid,
        "age_group": "U10",
        "date": "2026-11-14",
        "start_time": "12:00",
        "arena": "Hall",
        "status": "manually_booked",
        "operational_state": "booked",
        "authority": "manual_club_confirmation",
        "calendar_covers_window": covers,
    }


def test_host_text_states_coverage_and_read_only_projection():
    text = format_host_club_booking_status(_report([_row("rvv-1", covers=True), _row("rvv-2", covers=False)]), source_scope=None)

    assert "Calendar window coverage: 1/2 tournaments fully covered" in text
    assert "absence is proof only where covered" in text
    assert "Read-only projection: no booking, placement or approval was changed." in text


def test_unknown_snapshot_scope_is_reported_as_unknown_not_complete():
    text = format_host_club_booking_status(_report([_row("rvv-1", covers=True)]), source_scope=None)

    assert "Source snapshot scope: unknown" in text
    assert "Source snapshot scope: complete" not in text


def test_partial_snapshot_scope_names_missing_sources():
    scope = {"complete": False, "missing_sources": ["holmen", "jar"]}

    text = format_host_club_booking_status(_report([_row("rvv-1", covers=True)]), source_scope=scope)

    assert "Source snapshot scope: partial, missing sources: holmen, jar" in text


def test_complete_snapshot_scope_is_reported_as_complete():
    text = format_host_club_booking_status(_report([_row("rvv-1", covers=True)]), source_scope={"complete": True})

    assert "Source snapshot scope: complete" in text


def test_complete_flag_with_recorded_missing_sources_is_not_complete():
    # Contradictory metadata fails closed: missing sources are never hidden by a complete flag.
    scope = {"complete": True, "missing_sources": ["jar"]}

    text = format_host_club_booking_status(_report([_row("rvv-1", covers=True)]), source_scope=scope)

    assert "Source snapshot scope: complete" not in text
    assert "Source snapshot scope: partial, missing sources: jar" in text


def test_non_boolean_complete_flag_is_not_complete():
    # A truthy non-boolean such as the string "false" must not read as complete.
    scope = {"complete": "false", "missing_sources": []}

    text = format_host_club_booking_status(_report([_row("rvv-1", covers=True)]), source_scope=scope)

    assert "Source snapshot scope: complete" not in text

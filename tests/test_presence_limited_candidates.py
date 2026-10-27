"""Identity candidates on a calendar that does not list its tournament bookings (#853).

Sandefjord Penguins' calendar shows its standing ice use ("fast istid", fixed
busy outside its weekend window) but not its tournaments. Generic ice-use
blocks are therefore not proof of a tournament booking's identity. A complete
calendar would otherwise contest every Sandefjord tournament in the window with
twenty such blocks, which is the live `resolve_candidate` demand.
"""

from __future__ import annotations

from tournament_scheduler.booking.assessment_candidates import assessment_candidate

GENERIC_TITLE = "Sandefjord Penguins fast istid (opptatt utenom tildelt helgevindu)"


def _tournament(**overrides):
    tournament = {
        "id": "rvv-0024",
        "age_group": "U10",
        "date": "2026-10-10",
        "start_time": "13:00",
        "host_club": "Sandefjord Penguins",
        "arena": "Bugården ishall",
    }
    tournament.update(overrides)
    return tournament


def _event(start, end, *, title=GENERIC_TITLE, date="2026-10-17"):
    return {
        "club": "Sandefjord Penguins",
        "date": date,
        "start": start,
        "end": end,
        "title": title,
        "calendar_event": title,
        "arena": "Bugården ishall",
        "fingerprint": f"{date}-{start}-{end}",
    }


def _candidate(event, tournament=None):
    candidate = assessment_candidate(
        tournament or _tournament(),
        event,
        problem={"ice_time_minutes": {"U10": 120}},
        date_window_days=14,
    )
    assert candidate is not None
    return candidate


def test_generic_ice_use_block_on_other_day_is_context_not_identity_on_unpublished_calendar():
    # Same club and arena, no age token, no interval match: the generic block
    # is listed for the harness but does not contest the booking.
    candidate = _candidate(_event("08:00", "10:00"))

    assert candidate["season_context_only"] is True


def test_generic_block_overlapping_the_booked_interval_is_still_a_contest():
    # A genuine conflict on the same slot is never silenced by the limitation.
    overlapping = _event("13:30", "14:30", date="2026-10-10")

    candidate = _candidate(overlapping)

    assert candidate["season_context_only"] is False


def test_age_group_titled_event_on_unpublished_calendar_is_still_identity_evidence():
    titled = _event("08:00", "10:00", title="Sandefjord U10 miniputt", date="2026-10-17")

    candidate = _candidate(titled)

    assert candidate["season_context_only"] is False


def test_generic_block_on_a_publishing_club_keeps_window_rule():
    # Clubs that publish their bookings keep the existing observation-window rule.
    tournament = _tournament(host_club="Jar")
    event = _event("08:00", "10:00", date="2026-10-17")
    event["club"] = "Jar"

    candidate = assessment_candidate(
        tournament,
        event,
        problem={"ice_time_minutes": {"U10": 120}},
        date_window_days=14,
    )

    assert candidate is not None
    assert candidate["season_context_only"] is False

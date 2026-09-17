"""Tests for Stage 4 manual-placement report helpers."""

from tournament_scheduler.models import SeasonPlan
from tournament_scheduler.pipeline.stage4_helpers import build_tournament_placement_entries


def _plan(items):
    return SeasonPlan(
        tournaments=[],
        start_date=None,
        end_date=None,
        unresolved_tournament_placements=items,
    )


def test_searched_hosts_are_reported_instead_of_merely_known_candidates():
    """A manual-placement row must only claim the hosts/dates that were
    actually searched, not participant-derived candidates that merely
    existed."""
    plan = _plan([
        {
            "age_group": "U10",
            "date": "2026-10-10",
            "candidate_hosts": ["Jar", "Holmen", "Kongsberg"],
            "participant_clubs": ["Jar", "Holmen"],
            "responsible_host": "Jar",
            "search_hosts_tried": ["Jar"],
            "same_host_dates_checked": ["2026-10-17"],
            "bounded_repair_exhausted": True,
            "reason": "no_participant_host_slot",
        }
    ])

    (entry,) = build_tournament_placement_entries(plan)
    message = entry["message"]

    assert "Hosts searched: Jar." in message
    assert "Responsible host: Jar." in message
    assert "Andre datoer søkt for samme vertsklubb: 2026-10-17." in message
    # Candidates that were never searched must not be presented as tried.
    assert "Holmen" not in message.split("Hosts searched:")[1]
    assert "Kongsberg" not in message.split("Hosts searched:")[1]


def test_legacy_record_without_search_hosts_falls_back_to_candidate_hosts():
    plan = _plan([
        {
            "age_group": "U10",
            "date": "2026-10-10",
            "candidate_hosts": ["Jar", "Kongsberg"],
            "participant_clubs": ["Jar", "Kongsberg"],
            "reason": "no_participant_host_slot",
        }
    ])

    (entry,) = build_tournament_placement_entries(plan)

    assert "Hosts searched: Jar, Kongsberg." in entry["message"]

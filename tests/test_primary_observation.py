"""An exact same-day covering event is the primary observation among far same-age candidates (#853).

Frisk Asker's calendar shows its U12 series rounds without a resource footprint,
so every age-matching U12 event in the window is actionable regardless of date.
An accepted rvv-0048 at 17:30-18:50 then had 62 actionable candidates, and the
one exact covering event was never chosen as its observation.
"""

from __future__ import annotations

from tournament_scheduler.booking.assessment_candidates import RELATION_PROXIMATE_DATE_SHIFT, RELATION_SAME_DATE_OVERLAP
from tournament_scheduler.booking.primary_observation import select_primary_observation, shared_event_fingerprints

AGE = "event_title_age_group_matches"


def _candidate(fp, relation, *, covers, evidence=(AGE,), date_delta_days=0):
    return {
        "event_fingerprint": fp,
        "relation": relation,
        "covers_current_interval": covers,
        "evidence": list(evidence),
        "actionable": True,
        "date_delta_days": date_delta_days,
    }


def test_single_exact_covering_event_wins_over_far_same_age_candidates():
    exact = _candidate("exact", RELATION_SAME_DATE_OVERLAP, covers=True)
    far = [
        _candidate(f"far-{i}", RELATION_PROXIMATE_DATE_SHIFT, covers=False, date_delta_days=20 + i)
        for i in range(61)
    ]

    chosen = select_primary_observation([*far, exact], shared_event_fingerprints=set(), association_unresolved=False)

    assert chosen is not None
    assert chosen["event_fingerprint"] == "exact"


def test_same_day_overlapping_competitor_keeps_the_observation_ambiguous():
    # A second same-day overlapping candidate is a real contest for the slot.
    exact = _candidate("exact", RELATION_SAME_DATE_OVERLAP, covers=True)
    competitor = _candidate("competitor", RELATION_SAME_DATE_OVERLAP, covers=False)

    assert select_primary_observation([exact, competitor], shared_event_fingerprints=set(), association_unresolved=False) is None


def test_shared_exact_event_is_never_chosen():
    exact = _candidate("exact", RELATION_SAME_DATE_OVERLAP, covers=True)
    far = _candidate("far", RELATION_PROXIMATE_DATE_SHIFT, covers=False, date_delta_days=5)

    assert select_primary_observation([exact, far], shared_event_fingerprints={"exact"}, association_unresolved=False) is None


def test_unresolved_association_requires_age_evidence_for_the_exact_event():
    exact = _candidate("exact", RELATION_SAME_DATE_OVERLAP, covers=True, evidence=())
    far = _candidate("far", RELATION_PROXIMATE_DATE_SHIFT, covers=False, date_delta_days=5)

    assert select_primary_observation([exact, far], shared_event_fingerprints=set(), association_unresolved=True) is None


def test_single_actionable_candidate_keeps_existing_rule():
    only = _candidate("only", RELATION_PROXIMATE_DATE_SHIFT, covers=False, date_delta_days=5)

    chosen = select_primary_observation([only], shared_event_fingerprints=set(), association_unresolved=False)

    assert chosen is not None
    assert chosen["event_fingerprint"] == "only"


def test_neighbouring_date_claim_does_not_make_an_exact_same_day_event_shared():
    # Frisk rvv-0048 case: rvv-0023 claims the same U12 event 34 days away.
    candidates_by_event = {
        "exact": [
            ("rvv-0048", _candidate("exact", RELATION_SAME_DATE_OVERLAP, covers=True)),
            ("rvv-0023", _candidate("exact", RELATION_PROXIMATE_DATE_SHIFT, covers=False, date_delta_days=34)),
        ]
    }

    assert shared_event_fingerprints(candidates_by_event) == set()


def test_two_same_day_claims_make_an_event_shared():
    candidates_by_event = {
        "exact": [
            ("t1", _candidate("exact", RELATION_SAME_DATE_OVERLAP, covers=True)),
            ("t2", _candidate("exact", RELATION_SAME_DATE_OVERLAP, covers=True)),
        ]
    }

    assert shared_event_fingerprints(candidates_by_event) == {"exact"}


def test_generic_full_footprint_overlap_contests_but_is_never_the_observation():
    # #859 review: an overlap on a footprint club without identity evidence is a
    # venue contest only. It must not become the tournament's observed booking.
    generic = {
        "event_fingerprint": "generic",
        "relation": RELATION_SAME_DATE_OVERLAP,
        "covers_current_interval": False,
        "evidence": ["same_date_interval_overlap"],
        "actionable": True,
        "date_delta_days": 0,
        "resource_coverage": "covers",
        "footprint_aware": True,
    }

    assert select_primary_observation([generic], shared_event_fingerprints=set(), association_unresolved=False) is None


def test_near_exact_full_footprint_event_is_still_an_observation():
    # A near-exact interval supplies identity on a footprint club (ADR 0007).
    near = {
        "event_fingerprint": "near",
        "relation": RELATION_SAME_DATE_OVERLAP,
        "covers_current_interval": False,
        "evidence": ["same_date_interval_overlap", "event_interval_within_tolerance_of_canonical"],
        "actionable": True,
        "date_delta_days": 0,
        "resource_coverage": "covers",
    }

    chosen = select_primary_observation([near], shared_event_fingerprints=set(), association_unresolved=False)

    assert chosen is not None
    assert chosen["event_fingerprint"] == "near"


def test_generic_overlap_with_missing_resource_data_on_footprint_club_is_not_the_observation():
    # #859 review: missing resource information must not bypass the venue-only guard.
    generic = {
        "event_fingerprint": "generic-unknown",
        "relation": RELATION_SAME_DATE_OVERLAP,
        "covers_current_interval": False,
        "evidence": ["same_date_interval_overlap"],
        "actionable": True,
        "date_delta_days": 0,
        "resource_coverage": None,
        "footprint_aware": True,
    }

    assert select_primary_observation([generic], shared_event_fingerprints=set(), association_unresolved=False) is None


def test_resource_agnostic_club_keeps_generic_single_observation():
    # Resource-agnostic clubs keep the window rule and their existing observation.
    generic = {
        "event_fingerprint": "agnostic",
        "relation": RELATION_SAME_DATE_OVERLAP,
        "covers_current_interval": False,
        "evidence": ["same_date_interval_overlap"],
        "actionable": True,
        "date_delta_days": 0,
        "resource_coverage": None,
        "footprint_aware": False,
    }

    chosen = select_primary_observation([generic], shared_event_fingerprints=set(), association_unresolved=False)

    assert chosen is not None
    assert chosen["event_fingerprint"] == "agnostic"

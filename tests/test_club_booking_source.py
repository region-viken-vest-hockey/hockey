"""Club-wide booking source authority persistence and per-ID disposition.

Regression coverage for the Holmen-style workflow: a club provides one booking
list (a spreadsheet or email listing every home tournament), the operator
records that source as durable authority and links each per-tournament
interpretation to it. The source version, the club scope and the per-ID
disposition survive calendar refresh, and an ambiguous club list whose canonical
intervals overlap stays review-required instead of being silently treated as two
bookings.
"""

from __future__ import annotations

import pytest

from tournament_scheduler.calendar_bookings import (
    CLUB_BOOKING_SOURCE_ASSERTIONS_KEY,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    _club_booking_source_projection,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.published_mutation_history import replay_recorded_mutations
from tournament_scheduler.season_state import (
    SeasonStateError,
    booking_status_report,
    club_booking_sources,
    load_decisions,
    load_schedule,
    reconcile_calendar_bookings,
    set_club_booking_source,
    set_ice_time_minutes,
    set_manual_booking_assertion,
)
from tests.test_approval_lifecycle import (
    _host_a_problem,
    _promote,
    _teams,
    _tournament,
)


def _source_set(root, *, club="A", **overrides):
    kwargs = {
        "season": "2026-2027",
        "root": root,
        "club": club,
        "source_document": "changes_and_confirmations/2026-27/club.xlsx",
        "source_version": "2026-09-27",
        "reference": "email:club-2026-09-27",
        "actor": "booker",
        "note": "club booking list reviewed",
    }
    kwargs.update(overrides)
    return set_club_booking_source(**kwargs)


def _interpretation(root, *, tournament_id="t1", source_id, **overrides):
    kwargs = {
        "season": "2026-2027",
        "root": root,
        "tournament_id": tournament_id,
        "booking_status": "booked",
        "source_scope": "club_wide_interpretation",
        "source_assertion_id": source_id,
        "reference": "email:club-2026-09-27",
        "actor": "booker",
    }
    kwargs.update(overrides)
    return set_manual_booking_assertion(**kwargs)


def test_source_provenance_and_interpretation_survive_refresh(tmp_path):
    """The source version survives a calendar refresh and stays on the per-ID row."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    source = _source_set(root)["source"]

    _interpretation(root, source_id=source["id"], problem=problem)
    before = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in before["tournaments"] if r["tournament_id"] == "t1")
    assert row["status"] == "manually_booked"
    assert row["authority"] == "manual_club_confirmation_interpretation"
    assert row["source_assertion_id"] == source["id"]
    assert row["source_document"] == "changes_and_confirmations/2026-27/club.xlsx"
    assert row["source_version"] == "2026-09-27"

    reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)

    after = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in after["tournaments"] if r["tournament_id"] == "t1")
    assert row["status"] == "manually_booked"
    assert row["source_assertion_id"] == source["id"]
    assert row["source_document"] == "changes_and_confirmations/2026-27/club.xlsx"


def test_club_booking_source_report_groups_per_id_disposition(tmp_path):
    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-09-13")])
    problem = _host_a_problem([])
    source = _source_set(root)["source"]
    _interpretation(root, tournament_id="t1", source_id=source["id"], problem=problem)
    _interpretation(root, tournament_id="t2", source_id=source["id"], problem=problem)

    report = club_booking_sources(season="2026-2027", root=root, problem=problem)
    assert report["count"] == 1
    entry = report["sources"][0]
    assert entry["source_document"].endswith("club.xlsx")
    assert entry["source_version"] == "2026-09-27"
    assert entry["host_club"] == "A"
    assert entry["tournament_ids"] == ["t1", "t2"]
    assert {item["booking_status"] for item in entry["tournaments"]} == {"manually_booked"}
    assert entry["requires_operator_review"] is False


def test_overlapping_club_stated_intervals_are_review_required():
    """The 2026-10-10 Holmen ambiguity must not read as two clean bookings.

    The canonical plan may be legally shortened while the club's own list still
    states overlapping windows. The overlap is a property of the club source,
    so it is flagged even when the canonical intervals no longer collide.
    """

    source_id = "club_booking_source:Holmen:abc"
    decisions = {
        CLUB_BOOKING_SOURCE_ASSERTIONS_KEY: [
            {
                "id": source_id,
                "status": "active",
                "host_club": "Holmen",
                "source_document": "changes_and_confirmations/2026-27/Holmen.xlsx",
                "source_version": "2026-09-27",
                "source_fingerprint": "abc",
                "asserted_at": "2026-09-27T00:00:00+00:00",
            }
        ],
        MANUAL_BOOKING_ASSERTIONS_KEY: [
            {
                "id": "manual_booking:rvv-0004:1",
                "status": "active",
                "booking_status": "booked",
                "source_scope": "club_wide_interpretation",
                "source_assertion_id": source_id,
                "tournament_id": "rvv-0004",
                "asserted_interval": {
                    "date": "2026-10-10",
                    "start_time": "13:00",
                    "end_time": "14:00",
                },
                "stated_interval": {
                    "date": "2026-10-10",
                    "start": "13:00",
                    "end": "14:50",
                    "duration_minutes": "110",
                },
            },
            {
                "id": "manual_booking:rvv-0003:1",
                "status": "active",
                "booking_status": "booked",
                "source_scope": "club_wide_interpretation",
                "source_assertion_id": source_id,
                "tournament_id": "rvv-0003",
                "asserted_interval": {
                    "date": "2026-10-10",
                    "start_time": "14:00",
                    "end_time": "15:00",
                },
                "stated_interval": {
                    "date": "2026-10-10",
                    "start": "14:00",
                    "end": "14:50",
                    "duration_minutes": "50",
                },
            },
        ],
    }
    plan = {
        "tournaments": [
            {
                "id": "rvv-0004",
                "age_group": "U12",
                "date": "2026-10-10",
                "start_time": "13:00",
                "host_club": "Holmen",
            },
            {
                "id": "rvv-0003",
                "age_group": "U12",
                "date": "2026-10-10",
                "start_time": "14:00",
                "host_club": "Holmen",
            },
        ]
    }
    problem = {"ice_time_minutes": {"U12": 60}}

    sources = _club_booking_source_projection(
        decisions=decisions, rows=[], plan=plan, problem=problem
    )
    entry = sources[0]
    assert entry["requires_operator_review"] is True
    assert entry["overlapping_source_intervals"] == [["rvv-0003", "rvv-0004"]]
    # The canonical plan itself is legal: only the club-stated windows overlap.
    by_id = {item["tournament_id"]: item for item in entry["tournaments"]}
    assert by_id["rvv-0004"]["canonical_interval"]["end_time"] == "14:00"
    assert by_id["rvv-0003"]["canonical_interval"]["start_time"] == "14:00"
    assert "manual_booking_stated_end_differs_from_canonical" in by_id["rvv-0004"]["interval_follow_up"]


def test_booked_source_interval_updates_canonical_occupancy(tmp_path):
    """A sourced booked interval is the canonical effective interval, not follow-up only."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    source = _source_set(root)["source"]
    result = _interpretation(
        root,
        source_id=source["id"],
        problem=problem,
        stated_date="2026-09-13",
        stated_start="12:30",
        stated_end="14:20",
    )

    assert result["interval_alignment"]["accepted_source_interval"] == {
        "date": "2026-09-13",
        "start_time": "12:30",
        "duration_minutes": 110,
        "end_time": "14:20",
    }
    tournament = load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]
    assert tournament["date"] == "2026-09-13"
    assert tournament["start_time"] == "12:30"

    report = club_booking_sources(season="2026-2027", root=root, problem=problem)
    item = report["sources"][0]["tournaments"][0]
    assert item["canonical_interval"]["end_time"] == "14:20"
    assert item["interval_follow_up"] == ["ice_time_governing_minimum"]


def test_real_overlapping_source_intervals_are_recorded_with_blocking_follow_up(tmp_path):
    t2_teams = [
        {"club": "A", "label": "A2", "age_group": "U10"},
        *_teams(("E", "F", "G")),
    ]
    root = _promote(
        tmp_path,
        [
            _tournament("t1", date_str="2026-09-13"),
            {**_tournament("t2", date_str="2026-09-13", teams=t2_teams), "start_time": "14:00"},
        ],
    )
    problem = {**_host_a_problem([]), "teams": _teams(("A", "B", "C", "D", "E", "F", "G")) + [t2_teams[0]]}
    source = _source_set(root)["source"]
    _interpretation(
        root,
        tournament_id="t1",
        source_id=source["id"],
        problem=problem,
        stated_date="2026-09-13",
        stated_start="12:30",
        stated_end="14:30",
    )
    result = _interpretation(
        root,
        tournament_id="t2",
        source_id=source["id"],
        problem=problem,
        stated_date="2026-09-13",
        stated_start="14:00",
        stated_end="16:00",
    )

    assert [warning["code"] for warning in result["booking_feasibility_warnings"]] == [
        "arena_interval_conflict"
    ]
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(row for row in report["tournaments"] if row["tournament_id"] == "t2")
    assert row["status"] == "manually_booked"
    assert "arena_interval_conflict" in row["follow_up_reasons"]
    sources = club_booking_sources(season="2026-2027", root=root, problem=problem)
    t2 = next(item for item in sources["sources"][0]["tournaments"] if item["tournament_id"] == "t2")
    assert "arena_interval_conflict" in t2["interval_follow_up"]


def test_adjacent_booked_source_intervals_use_actual_duration_for_conflicts(tmp_path):
    t2_teams = [
        {"club": "A", "label": "A2", "age_group": "U10"},
        *_teams(("E", "F", "G")),
    ]
    root = _promote(
        tmp_path,
        [
            _tournament("t1", date_str="2026-09-13"),
            {**_tournament("t2", date_str="2026-09-13", teams=t2_teams), "start_time": "14:20"},
        ],
    )
    problem = {**_host_a_problem([]), "teams": _teams(("A", "B", "C", "D", "E", "F", "G")) + [t2_teams[0]]}
    source = _source_set(root)["source"]
    _interpretation(
        root,
        tournament_id="t1",
        source_id=source["id"],
        problem=problem,
        stated_date="2026-09-13",
        stated_start="12:30",
        stated_end="14:20",
    )
    result = _interpretation(
        root,
        tournament_id="t2",
        source_id=source["id"],
        problem=problem,
        stated_date="2026-09-13",
        stated_start="14:20",
        stated_end="16:10",
    )

    assert result["changed"] is True
    by_id = {t["id"]: t for t in load_schedule("2026-2027", root=root)["plan"]["tournaments"]}
    assert by_id["t1"]["start_time"] == "12:30"
    assert by_id["t2"]["start_time"] == "14:20"


def test_reconfirming_prior_assertion_supersedes_and_retry_is_idempotent(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    source = _source_set(root)["source"]
    old = _interpretation(root, source_id=source["id"], problem=problem)

    changed = _interpretation(
        root,
        source_id=source["id"],
        problem=problem,
        stated_date="2026-09-13",
        stated_start="12:30",
        stated_end="14:20",
        reference="email:club-2026-09-27",
    )
    assert changed["previous_assertion"]["id"] == old["assertion"]["id"]
    assert changed["assertion"]["supersedes"] == old["assertion"]["id"]
    assert changed["assertion"]["source_assertion_id"] == source["id"]

    retry = _interpretation(
        root,
        source_id=source["id"],
        problem=problem,
        stated_date="2026-09-13",
        stated_start="12:30",
        stated_end="14:20",
        reference="email:club-2026-09-27",
    )
    assert retry["idempotent"] is True


def test_matching_default_interval_does_not_create_redundant_override(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    source = _source_set(root)["source"]
    result = _interpretation(
        root,
        source_id=source["id"],
        problem=problem,
        stated_start="10:00",
        stated_end="12:00",
    )
    assert result["interval_alignment"]["changed"] is False
    assert result["ice_time_override"] is None
    assert "ice_time_minutes_overrides" not in load_decisions("2026-2027", root=root)


def test_existing_matching_override_keeps_source_provenance_without_new_override(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=120,
        request_id="host-confirmed-120",
        reference="old host confirmation",
        problem=problem,
    )
    before_overrides = list(load_decisions("2026-2027", root=root)["ice_time_minutes_overrides"])
    source = _source_set(root)["source"]
    result = _interpretation(
        root,
        source_id=source["id"],
        problem=problem,
        stated_start="10:00",
        stated_end="12:00",
    )

    assert result["ice_time_override"] is None
    assert result["assertion"]["source_assertion_id"] == source["id"]
    assert load_decisions("2026-2027", root=root)["ice_time_minutes_overrides"] == before_overrides


def test_stated_date_must_be_date_only(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    source = _source_set(root)["source"]
    with pytest.raises(SeasonStateError, match="YYYY-MM-DD"):
        _interpretation(
            root,
            source_id=source["id"],
            problem=problem,
            stated_date="2026-09-13T10:00:00",
            stated_start="10:00",
            stated_end="11:50",
        )


def test_source_supersession_and_idempotency(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    first = _source_set(root)["source"]
    again = _source_set(root)
    assert again["idempotent"] is True

    second = _source_set(root, source_version="2026-09-28")["source"]
    assert second["id"] != first["id"]
    assert second["supersedes"] == first["id"]

    decisions = load_decisions("2026-2027", root=root)
    records = {r["id"]: r for r in decisions["club_booking_source_assertions"]}
    assert records[first["id"]]["status"] == "superseded"
    assert records[first["id"]]["superseded_by"] == second["id"]
    assert records[second["id"]]["status"] == "active"


def test_linking_requires_same_club_and_club_wide_scope(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    source = _source_set(root, club="A")["source"]
    problem = _host_a_problem([])

    # A source for another club cannot be linked to this host's tournament.
    with pytest.raises(SeasonStateError, match="same host club"):
        _source_set(root, club="B")
        _interpretation(
            root,
            tournament_id="t1",
            source_id=_source_set(root, club="B")["source"]["id"],
            problem=problem,
        )

    # A direct per-tournament assertion cannot claim a club-wide source.
    with pytest.raises(SeasonStateError, match="club_wide_interpretation"):
        set_manual_booking_assertion(
            season="2026-2027",
            root=root,
            tournament_id="t1",
            booking_status="booked",
            source_scope="tournament",
            source_assertion_id=source["id"],
            reference="email:club",
            problem=problem,
        )

    # An unknown source id fails closed.
    with pytest.raises(SeasonStateError, match="Unknown active club booking source"):
        _interpretation(root, tournament_id="t1", source_id="club_booking_source:missing:0")


def test_unlinked_club_wide_interpretations_are_visible(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        source_scope="club_wide_interpretation",
        reference="legacy club-wide email",
        problem=problem,
    )

    report = club_booking_sources(season="2026-2027", root=root, problem=problem)
    assert report["count"] == 1
    entry = report["sources"][0]
    assert entry["unprovenanced"] is True
    assert entry["tournament_ids"] == ["t1"]
    assert entry["requires_operator_review"] is True


def test_source_assertion_is_decision_only_in_sealed_replay():
    """The source-document write must not be replayed as a schedule mutation."""

    baseline = {
        "t1": {
            "id": "t1",
            "age_group": "U10",
            "date": "2026-09-12",
            "start_time": "10:00",
            "placement": {"date": "2026-09-12"},
        }
    }
    history = [
        {
            "event": "set_club_booking_source",
            "tournament_id": "",
            "details": {
                "club": "A",
                "source_assertion_id": "club_booking_source:A:deadbeef",
            },
        }
    ]
    projection, applied = replay_recorded_mutations(baseline, history)
    assert projection["t1"]["date"] == "2026-09-12"
    assert applied == []


def test_manual_booking_interval_replays_as_schedule_projection_mutation():
    baseline = {
        "t1": {
            "id": "t1",
            "age_group": "U10",
            "date": "2026-09-12",
            "start_time": "10:00",
            "duration_minutes": 120,
            "placement": {"date": "2026-09-12"},
        }
    }
    history = [
        {
            "event": "set_manual_booking_assertion",
            "tournament_id": "t1",
            "details": {
                "interval_alignment": {
                    "changed": True,
                    "accepted_source_interval": {
                        "date": "2026-09-13",
                        "start_time": "12:30",
                        "duration_minutes": 110,
                        "end_time": "14:20",
                    },
                }
            },
        }
    ]
    projection, applied = replay_recorded_mutations(baseline, history)
    assert projection["t1"]["date"] == "2026-09-13"
    assert projection["t1"]["start_time"] == "12:30"
    assert projection["t1"]["end_time"] == "14:20"
    assert applied == [{"event": "set_manual_booking_assertion", "tournament_id": "t1"}]


def test_superseding_source_keeps_linked_provenance_and_flags_relink(tmp_path):
    """Replacing a source version must not drop existing per-ID provenance."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    v1 = _source_set(root, source_version="2026-09-27")["source"]
    _interpretation(root, tournament_id="t1", source_id=v1["id"], problem=problem)

    v2 = _source_set(root, source_version="2026-09-28")["source"]

    # The per-ID row keeps resolving the old document/version, and is flagged for
    # relinking rather than silently losing its provenance.
    row = next(
        r
        for r in booking_status_report(season="2026-2027", root=root, problem=problem)["tournaments"]
        if r["tournament_id"] == "t1"
    )
    assert row["status"] == "manually_booked"
    assert row["source_document"] == v1["source_document"]
    assert row["source_version"] == "2026-09-27"
    assert row["source_is_current"] is False
    assert "club_booking_source_superseded" in row["follow_up_reasons"]
    assert row["needs_attention"] is True

    sources = club_booking_sources(season="2026-2027", root=root, problem=problem)
    by_id = {source["id"]: source for source in sources["sources"]}
    assert by_id[v2["id"]]["is_active"] is True
    assert by_id[v2["id"]]["tournament_ids"] == []
    assert by_id[v1["id"]]["is_active"] is False
    assert by_id[v1["id"]]["stale_link"] is True
    assert by_id[v1["id"]]["requires_operator_review"] is True
    assert by_id[v1["id"]]["tournament_ids"] == ["t1"]

    # Relinking to the new active version clears the stale flag.
    _interpretation(
        root,
        tournament_id="t1",
        source_id=v2["id"],
        problem=problem,
        reference="email:club-2026-09-28",
        supersede=True,
        note="relinked to the reviewed source version",
    )
    row = next(
        r
        for r in booking_status_report(season="2026-2027", root=root, problem=problem)["tournaments"]
        if r["tournament_id"] == "t1"
    )
    assert row["source_version"] == "2026-09-28"
    assert row["source_is_current"] is True
    assert "club_booking_source_superseded" not in row["follow_up_reasons"]


def test_explicit_fingerprint_cannot_be_reused_for_a_different_version(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    _source_set(root, source_fingerprint="fixed", source_version="2026-09-27")
    # Same fingerprint and same version stays idempotent.
    repeated = _source_set(root, source_fingerprint="fixed", source_version="2026-09-27")
    assert repeated["idempotent"] is True
    with pytest.raises(SeasonStateError, match="different document/version"):
        _source_set(root, source_fingerprint="fixed", source_version="2026-09-28")


def test_source_fingerprint_is_deterministic_and_explicit_override_is_honored(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    expected = stable_payload_sha256(
        {"club": "A", "source_document": "changes_and_confirmations/2026-27/club.xlsx", "source_version": "2026-09-27"}
    )
    source = _source_set(root)["source"]
    assert source["source_fingerprint"] == expected

    root2 = _promote(tmp_path / "second", [_tournament("t1")])
    explicit = _source_set(root2, source_fingerprint="explicit-fingerprint")["source"]
    assert explicit["source_fingerprint"] == "explicit-fingerprint"
    assert explicit["id"].endswith(":explicit-fingerprint")


def test_rejection_queue_item_carries_club_source_provenance(tmp_path):
    """A rejected club-list row stays traceable to the source version in the queue."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    source = _source_set(root)["source"]
    _interpretation(
        root,
        tournament_id="t1",
        source_id=source["id"],
        booking_status="not-booked",
        problem=problem,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    item = next(i for i in report["manual_booking_queue"] if i["tournament_id"] == "t1")
    assert item["source"]["source_assertion_id"] == source["id"]
    assert item["source"]["source_document"].endswith("club.xlsx")
    assert item["source"]["source_version"] == "2026-09-27"


def test_source_creation_requires_version_and_reference(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    with pytest.raises(SeasonStateError, match="--source-version"):
        _source_set(root, source_version="")
    with pytest.raises(SeasonStateError, match="--source-document"):
        _source_set(root, source_document="")
    with pytest.raises(SeasonStateError, match="traceable source reference"):
        _source_set(root, reference="", note="")

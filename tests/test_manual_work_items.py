"""Tests for grouping manual findings into operator work items (issue #330)."""

from tournament_scheduler.pipeline.manual_work_items import (
    build_work_items,
    render_candidate_weekends_html,
    render_findings_html,
    work_item_key,
)


def _entry(**overrides):
    entry = {
        "category": "manual_tournament_placement",
        "type": "MANUAL PLACEMENT REQUIRED — ingen vertsklubb blant deltakerne",
        "tournament_id": "rvv-0002",
        "date": "2026-10-10",
        "age_group": "U11",
        "host_club": "Jar",
        "arena": "Jar Isforum",
        "interval": "10:00",
        "message": "MANUAL PLACEMENT REQUIRED.",
    }
    entry.update(overrides)
    return entry


def test_several_findings_for_one_tournament_group_into_one_work_item():
    """One underlying intervention must not inflate the operator work count
    just because several evidence records describe it."""
    entries = [
        _entry(category="manual_tournament_placement"),
        _entry(
            category="manual_calendar_verification",
            type="Kalender utilgjengelig — istid må bookes manuelt",
        ),
        _entry(category="manual_external_conflict", type="MANUAL PLACEMENT REQUIRED — ekstern kalenderkonflikt"),
    ]

    items = build_work_items(entries)

    assert len(items) == 1
    item = items[0]
    assert item["tournament_id"] == "rvv-0002"
    assert item["categories"] == [
        "manual_calendar_verification",
        "manual_external_conflict",
        "manual_tournament_placement",
    ]
    assert len(item["findings"]) == 3
    assert item["unconfirmed"] is True


def test_hosting_obligation_without_a_tournament_groups_by_club_and_age_group():
    entries = [
        _entry(category="manual_hosting_obligation", tournament_id="", date="", interval=""),
        _entry(
            category="manual_hosting_obligation",
            tournament_id="",
            date="",
            interval="",
            message="second observation",
        ),
    ]

    items = build_work_items(entries)

    assert len(items) == 1
    assert items[0]["findings"][1]["message"] == "second observation"
    assert work_item_key(entries[0]) == ("obligation", "Jar", "U11")


def test_distinct_tournaments_stay_distinct_work_items():
    items = build_work_items(
        [
            _entry(tournament_id="rvv-0002"),
            _entry(tournament_id="rvv-0003", age_group="U12"),
        ]
    )

    assert {item["tournament_id"] for item in items} == {"rvv-0002", "rvv-0003"}


def test_candidate_weekends_render_suggestions_and_rejections():
    bundle = {
        "status": "suggestions",
        "candidate_weekends": [
            {
                "date": "2026-11-21",
                "start_time": "10:00",
                "end_time": "12:20",
                "availability": "free",
                "requires_host_confirmation": False,
                "roster_source": "current",
                "calendar_event": "",
                "roster": [{"club": "Kongsberg", "label": "Kongsberg U11"}],
            },
            {
                "date": "2026-11-22",
                "start_time": "12:00",
                "end_time": "14:20",
                "availability": "movable_busy",
                "requires_host_confirmation": True,
                "roster_source": "alternate",
                "calendar_event": "Åpen ishall",
                "roster": [],
            },
        ],
        "rejected_candidate_dates": [
            {
                "date": "2026-11-15",
                "reason": "team_already_plays",
                "team_conflicts": ["Jar Grønn"],
            }
        ],
    }

    rendered = render_candidate_weekends_html(bundle)

    assert "Forslag" in rendered
    assert "21.11.2026" in rendered and "10:00–12:20" in rendered
    assert "ledig istid" in rendered
    assert "Åpen ishall" in rendered and "krever bekreftelse fra vertsklubben" in rendered
    assert "krever alternativ lagsammensetning" in rendered
    assert "Nærmeste avviste datoer" in rendered
    assert "15.11.2026" in rendered and "Jar Grønn" in rendered


def test_no_suggestion_note_distinguishes_budget_from_exhaustion():
    budget = render_candidate_weekends_html({"status": "search_budget_exhausted", "candidate_weekends": []})
    exhausted = render_candidate_weekends_html(
        {"status": "bounded_date_set_exhausted", "candidate_weekends": []}
    )

    assert "søkebudsjettet" in budget
    assert "avgrensede" in exhausted
    assert budget != exhausted


def test_render_findings_prefers_the_operator_facing_type():
    rendered = render_findings_html(
        [_entry(message="detalj", type="MANUAL PLACEMENT REQUIRED — ingen vertsklubb blant deltakerne")]
    )

    assert "ingen vertsklubb blant deltakerne" in rendered
    assert "detalj" in rendered


def test_unplaced_placements_stay_distinct_via_their_stable_finding_id():
    """Parallel same-age-group/same-date obligations have no tournament id;
    their stable finding id must keep them separate work items instead of
    collapsing into one row."""
    entries = [
        _entry(
            tournament_id="",
            finding_id="unplaced_placement:U11:2026-10-10:1",
            date="2026-10-10",
        ),
        _entry(
            tournament_id="",
            finding_id="unplaced_placement:U11:2026-10-10:2",
            date="2026-10-10",
        ),
    ]

    items = build_work_items(entries)

    assert len(items) == 2
    assert work_item_key(entries[0]) == (
        "finding",
        "unplaced_placement:U11:2026-10-10:1",
    )

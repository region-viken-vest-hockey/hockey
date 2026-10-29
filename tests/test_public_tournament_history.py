"""Public history of sealed-season tournament changes is derived, public-safe and read-only (#869 item 3).

A republished sealed season must let a later reader see that a tournament
changed after an earlier publication. The history is derived from the immutable
publication records already kept in canonical state, not from Git diffs or the
generated HTML. Only public tournament facts are shown. Internal notes, actors
and source references never leak, and unchanged tournaments get no change.
"""

from __future__ import annotations

import copy

from tournament_scheduler.publication.public_history import public_change_history


def _record(publication_id: str, published_at: str, tournaments: list[dict], **internal) -> dict:
    return {
        "publication_id": publication_id,
        "published_at": published_at,
        "tournaments": tournaments,
        **internal,
    }


def _t(tournament_id: str, **fields) -> dict:
    base = {
        "tournament_id": tournament_id,
        "id": tournament_id,
        "date": "2026-09-12",
        "start_time": "10:00",
        "end_time": "12:00",
        "duration_minutes": 120,
        "arena": "Askerhallen",
        "host_club": "Asker",
        "cancelled": False,
        "cancellation_reason": "",
        "participants": ["Asker A", "Bærum B"],
    }
    base.update(fields)
    return base


def test_changed_tournament_records_public_before_and_after_per_publication():
    history = [
        _record("pub-1", "2026-09-01T10:00:00+00:00", [_t("t1"), _t("t2")], actor="operator-a", note="internal"),
        _record(
            "pub-2",
            "2026-09-05T10:00:00+00:00",
            [_t("t1", start_time="11:00", end_time="13:00"), _t("t2")],
            actor="operator-b",
            note="internal source reference",
        ),
    ]

    history_view = public_change_history(history)

    entries = history_view["tournaments"]["t1"]
    assert len(entries) == 1
    assert entries[0]["publication_id"] == "pub-2"
    assert entries[0]["published_at"] == "2026-09-05T10:00:00+00:00"
    assert {"field": "start_time", "before": "10:00", "after": "11:00"} in entries[0]["changes"]
    assert {"field": "end_time", "before": "12:00", "after": "13:00"} in entries[0]["changes"]


def test_unchanged_tournament_gets_no_history_marker():
    history = [
        _record("pub-1", "2026-09-01T10:00:00+00:00", [_t("t1"), _t("t2")]),
        _record("pub-2", "2026-09-05T10:00:00+00:00", [_t("t1", start_time="11:00", end_time="13:00"), _t("t2")]),
    ]

    history_view = public_change_history(history)

    assert "t2" not in history_view["tournaments"]


def test_repeated_republishes_keep_the_earlier_change_trail():
    history = [
        _record("pub-1", "2026-09-01T10:00:00+00:00", [_t("t1")]),
        _record("pub-2", "2026-09-05T10:00:00+00:00", [_t("t1", start_time="11:00", end_time="13:00")]),
        _record(
            "pub-3",
            "2026-09-09T10:00:00+00:00",
            [_t("t1", start_time="11:00", end_time="13:00", arena="Kongsberg Ishall")],
        ),
    ]

    entries = public_change_history(history)["tournaments"]["t1"]

    assert [entry["publication_id"] for entry in entries] == ["pub-2", "pub-3"]
    assert entries[1]["changes"] == [{"field": "arena", "before": "Askerhallen", "after": "Kongsberg Ishall"}]


def test_internal_fields_never_appear_in_public_history():
    history = [
        _record("pub-1", "2026-09-01T10:00:00+00:00", [_t("t1", operator_note="internal")], actor="secret-operator"),
        _record(
            "pub-2",
            "2026-09-05T10:00:00+00:00",
            [_t("t1", start_time="11:00", end_time="13:00", operator_note="another internal")],
            actor="secret-operator",
            note="source reference",
        ),
    ]

    rendered = repr(public_change_history(history))

    assert "secret-operator" not in rendered
    assert "source reference" not in rendered
    assert "operator_note" not in rendered
    assert "internal" not in rendered


def test_derivation_is_read_only():
    history = [
        _record("pub-1", "2026-09-01T10:00:00+00:00", [_t("t1")]),
        _record("pub-2", "2026-09-05T10:00:00+00:00", [_t("t1", start_time="11:00", end_time="13:00")]),
    ]
    before = copy.deepcopy(history)

    public_change_history(history)

    assert history == before

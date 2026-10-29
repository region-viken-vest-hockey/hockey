"""The public history page marks only changed tournaments and never shows internal fields (#869 item 3)."""

from __future__ import annotations

from tournament_scheduler.pipeline.public_history_view import PUBLIC_HISTORY_FILENAME, render_html, write_html
from tournament_scheduler.publication.public_history import public_change_history


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
        "participants": ["Asker A"],
    }
    base.update(fields)
    return base


def _history(*publications):
    return public_change_history(
        [
            {"publication_id": pub_id, "published_at": f"2026-09-0{index}T10:00:00+00:00", "tournaments": tournaments}
            for index, (pub_id, tournaments) in enumerate(publications, start=1)
        ]
    )


def test_page_marks_only_the_changed_tournament_and_shows_before_and_after():
    history = _history(
        ("pub-1", [_t("t1"), _t("t2", host_club="Bærum")]),
        ("pub-2", [_t("t1", start_time="11:00", end_time="13:00"), _t("t2", host_club="Bærum")]),
    )

    page = render_html(history, season="2026-2027")

    assert 'id="t-t1"' in page
    assert 'id="t-t2"' not in page
    assert "Starttid:</strong> 10:00 &rarr; 11:00" in page
    assert "Publisering pub-2" in page


def test_page_shows_a_readable_header_for_the_changed_tournament():
    history = _history(("pub-1", [_t("t1")]), ("pub-2", [_t("t1", arena="Kongsberg Ishall")]))

    assert "Asker 2026-09-12 10:00" in render_html(history)


def test_page_never_shows_internal_fields():
    history = _history(
        ("pub-1", [_t("t1", operator_note="internal note")]),
        ("pub-2", [_t("t1", start_time="11:00", operator_note="another internal note")]),
    )

    page = render_html(history)

    assert "internal note" not in page
    assert "operator_note" not in page


def test_write_removes_a_stale_page_when_no_tournament_changed(tmp_path):
    unchanged = _history(("pub-1", [_t("t1")]), ("pub-2", [_t("t1")]))
    stale = tmp_path / PUBLIC_HISTORY_FILENAME
    stale.write_text("old", encoding="utf-8")

    assert write_html(unchanged, tmp_path) == ""
    assert not stale.exists()


def test_write_creates_the_page_when_a_tournament_changed(tmp_path):
    changed = _history(("pub-1", [_t("t1")]), ("pub-2", [_t("t1", arena="Kongsberg Ishall")]))

    path = write_html(changed, tmp_path)

    assert path == str(tmp_path / PUBLIC_HISTORY_FILENAME)
    assert 'id="t-t1"' in (tmp_path / PUBLIC_HISTORY_FILENAME).read_text(encoding="utf-8")


def test_unreadable_canonical_state_gives_no_history_and_keeps_the_existing_page(tmp_path):
    from tournament_scheduler.pipeline.public_history_view import public_change_history_for_season

    previous = tmp_path / PUBLIC_HISTORY_FILENAME
    previous.write_text("published earlier", encoding="utf-8")

    history = public_change_history_for_season(season="2026-2027", root=tmp_path)

    assert history is None
    assert write_html(history, tmp_path) == ""
    assert previous.read_text(encoding="utf-8") == "published earlier"


def test_added_and_removed_tournaments_are_shown_as_events():
    history = _history(
        ("pub-1", [_t("t1"), _t("t2")]),
        ("pub-2", [_t("t1"), _t("t3")]),
    )

    page = render_html(history)

    assert "Fjernet fra publiseringen" in page
    assert "Lagt til i publiseringen" in page
    assert {entry["event"] for entries in history["tournaments"].values() for entry in entries} == {"added", "removed"}


def test_participant_only_change_shows_the_before_and_after_roster():
    history = _history(
        ("pub-1", [_t("t1", participants=["Asker A", "Bærum B"])]),
        ("pub-2", [_t("t1", participants=["Asker A", "Lillehammer C"])]),
    )

    page = render_html(history)

    assert "Lag:</strong> Asker A, Bærum B &rarr; Asker A, Lillehammer C" in page


def test_history_for_an_unresolved_season_is_none_and_never_raises(monkeypatch, tmp_path):
    from tournament_scheduler.pipeline import public_history_view

    assert public_history_view.public_change_history_for_resolved(None) is None

    def unreadable(season, *, root):
        raise RuntimeError("decisions partition is corrupt")

    monkeypatch.setattr(
        "tournament_scheduler.application.canonical_season.store_facade.load_decisions",
        unreadable,
    )

    assert public_history_view.public_change_history_for_resolved({"season": "2026-2027", "root": tmp_path}) is None

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


def test_season_without_canonical_state_gives_an_empty_history(tmp_path):
    from tournament_scheduler.pipeline.public_history_view import public_change_history_for_season

    assert public_change_history_for_season(season="2026-2027", root=tmp_path) == {"tournaments": {}, "labels": {}}

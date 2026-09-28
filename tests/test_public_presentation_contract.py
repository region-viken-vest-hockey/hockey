"""The public-presentation contract for proposed placements.

Publication scope may classify an affected tournament as ``proposed`` (no
accepted, source-backed booking yet). That is only safe when the exact exported
artifacts label it as awaiting confirmation. These tests pin the deterministic
checker that fails closed on a proposal presented as booked or with no booking
presentation at all.
"""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.export_parity.presentation import (
    verify_proposed_presentation,
)
from tournament_scheduler.pipeline.export_parity.records import (
    STATUS_FAIL,
    STATUS_NOT_CHECKABLE,
    STATUS_PASS,
)

_ID = "t1"
_HTML_HEADERS = ("Dato", "Aldersgruppe", "Arena", "Vertsklubb", "Lag", "Starttid", "Sluttid")
_XLSX_HEADERS = _HTML_HEADERS + ("Turnerings-ID", "Bookingsstatus", "Booking krever oppfølging")


def _write_html(path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "const TOURNAMENTS = " + json.dumps(entries) + ";"
    path.write_text("<html>\n" + payload + "\n</html>", encoding="utf-8")


def _write_xlsx(path, *, booking_status: str) -> None:
    import openpyxl

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sesongoversikt"
    sheet.append(list(_XLSX_HEADERS))
    sheet.append(
        [
            "10.10.2026",
            "U10",
            "Hall",
            "A",
            "A-1",
            "10:00",
            "12:00",
            _ID,
            booking_status,
            "Ja" if booking_status in {"stale", "ambiguous"} else "",
        ]
    )
    workbook.save(path)
    workbook.close()


def _verify(tmp_path, *, proposed=None):
    ids = [_ID] if proposed is None else proposed
    return verify_proposed_presentation(export_dir=tmp_path, proposed_tournament_ids=ids)


class TestProposedPresentationContract:
    def test_awaiting_confirmation_html_passes(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": _ID, "obs": "action_required", "bs": "stale"}])

        report = _verify(tmp_path)

        assert report["status"] == STATUS_PASS, report
        assert report["checked_tournament_ids"] == [_ID]
        assert report["problems"] == []

    def test_not_booked_html_passes(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": _ID, "obs": "not_booked", "bs": "unknown"}])

        assert _verify(tmp_path)["status"] == STATUS_PASS

    def test_proposal_presented_as_booked_fails(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": _ID, "obs": "booked", "bs": "manually_booked"}])

        report = _verify(tmp_path)

        assert report["status"] == STATUS_FAIL, report
        assert any("booked" in problem["message"] for problem in report["problems"])

    def test_proposal_with_raw_booked_status_fails(self, tmp_path):
        # A regression that drops ``obs`` but keeps a booked raw status would
        # still render as booked through the fallback resolver.
        _write_html(tmp_path / "season_plan.html", [{"id": _ID, "bs": "confirmed_booked"}])

        report = _verify(tmp_path)

        assert report["status"] == STATUS_FAIL, report

    def test_proposal_without_any_booking_presentation_fails(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": _ID}])

        report = _verify(tmp_path)

        assert report["status"] == STATUS_FAIL, report
        assert any("no booking presentation" in problem["message"] for problem in report["problems"])

    def test_unknown_operational_state_fails_closed(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": _ID, "obs": "typo_state", "bs": "unknown"}])

        report = _verify(tmp_path)

        assert report["status"] == STATUS_FAIL, report

    def test_proposal_missing_from_html_fails(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": "other", "obs": "not_booked", "bs": "unknown"}])

        report = _verify(tmp_path)

        assert report["status"] == STATUS_FAIL, report

    def test_unreadable_html_is_not_checkable(self, tmp_path):
        report = _verify(tmp_path)

        assert report["status"] == STATUS_NOT_CHECKABLE, report

    def test_booked_workbook_cell_blocks_even_when_html_is_awaiting(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": _ID, "obs": "action_required", "bs": "stale"}])
        _write_xlsx(tmp_path / "season_plan.xlsx", booking_status="confirmed_booked")

        report = _verify(tmp_path)

        assert report["status"] == STATUS_FAIL, report
        assert any(problem["artifact"] == "xlsx" for problem in report["problems"])

    def test_awaiting_workbook_cell_passes(self, tmp_path):
        _write_html(tmp_path / "season_plan.html", [{"id": _ID, "obs": "action_required", "bs": "stale"}])
        _write_xlsx(tmp_path / "season_plan.xlsx", booking_status="stale")

        assert _verify(tmp_path)["status"] == STATUS_PASS

    def test_no_proposed_ids_passes_without_reading_artifacts(self, tmp_path):
        report = _verify(tmp_path, proposed=[])

        assert report["status"] == STATUS_PASS
        assert report["artifacts"] == {}

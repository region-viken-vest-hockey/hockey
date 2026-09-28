"""Verify the exported public projection never presents a proposal as booked.

The tournament-scoped publication gate classifies an affected tournament as
``proposed`` when it has no accepted, source-backed booking for its exact
public interval. That classification is only safe if the *actual exported
artifacts* label the tournament as awaiting host confirmation: an exporter
regression that rendered a proposal with a ``booked`` badge (or with no booking
badge at all) would silently tell host clubs the ice is reserved.

This module reads the on-disk ``season_plan.html``/``season_plan.xlsx`` bytes
back through the independent parity readers -- never the exporter's in-memory
objects -- and fails closed when a proposed tournament is presented as booked
or carries no recognizable booking presentation. It never plans, mutates
canonical state or reinterprets booking authority.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .html_reader import read_html
from .records import STATUS_FAIL, STATUS_NOT_CHECKABLE, STATUS_PASS
from .xlsx_reader import read_xlsx

# The canonical public vocabulary is owned by
# :mod:`tournament_scheduler.calendar_bookings`; imported (not re-declared) so a
# renamed or added state cannot silently desynchronize this contract.
from tournament_scheduler.calendar_bookings import (
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_MANUALLY_BOOKED,
    OPERATIONAL_ACTION_REQUIRED,
    OPERATIONAL_BOOKED,
    OPERATIONAL_CHANGED_SLOT_REVIEW,
    OPERATIONAL_NOT_BOOKED,
    OPERATIONAL_PRESUMED_UNSCHEDULED,
    OPERATIONAL_UNKNOWN,
)

#: States that read as accepted/reserved ice in any public projection.
BOOKED_OPERATIONAL_STATES = frozenset({OPERATIONAL_BOOKED})
BOOKED_BOOKING_STATUSES = frozenset({BOOKING_CONFIRMED_BOOKED, BOOKING_MANUALLY_BOOKED})

#: The non-booked states the shipped renderers know how to label. An unknown or
#: empty operational state renders *no* top-level badge, so it fails closed.
AWAITING_OPERATIONAL_STATES = frozenset(
    {
        OPERATIONAL_NOT_BOOKED,
        OPERATIONAL_ACTION_REQUIRED,
        OPERATIONAL_CHANGED_SLOT_REVIEW,
        OPERATIONAL_PRESUMED_UNSCHEDULED,
        OPERATIONAL_UNKNOWN,
    }
)


def _problem(artifact: str, tournament_id: str, message: str) -> dict[str, str]:
    return {"artifact": artifact, "tournament_id": tournament_id, "message": message}


def _check_html_records(records_by_id: dict[str, Any], proposed_ids: list[str]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for tournament_id in proposed_ids:
        record = records_by_id.get(tournament_id)
        if record is None:
            problems.append(
                _problem("html", tournament_id, "proposed tournament is missing from the public HTML")
            )
            continue
        operational_state = str(record.operational_state or "")
        booking_status = str(record.booking_status or "")
        if operational_state in BOOKED_OPERATIONAL_STATES or booking_status in BOOKED_BOOKING_STATUSES:
            problems.append(
                _problem(
                    "html",
                    tournament_id,
                    "proposed placement is presented as booked in the public HTML",
                )
            )
        elif operational_state:
            if operational_state not in AWAITING_OPERATIONAL_STATES:
                problems.append(
                    _problem(
                        "html",
                        tournament_id,
                        f"unrecognized public operational booking state {operational_state!r}; "
                        "the page would render no awaiting-confirmation badge",
                    )
                )
        elif not booking_status:
            problems.append(
                _problem(
                    "html",
                    tournament_id,
                    "proposed placement carries no booking presentation at all",
                )
            )
    return problems


def _check_xlsx_records(records_by_id: dict[str, Any], proposed_ids: list[str]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for tournament_id in proposed_ids:
        record = records_by_id.get(tournament_id)
        if record is None:
            problems.append(
                _problem("xlsx", tournament_id, "proposed tournament is missing from the workbook")
            )
            continue
        booking_status = str(record.booking_status or "")
        if not booking_status:
            problems.append(
                _problem("xlsx", tournament_id, "proposed placement carries no booking status")
            )
        elif booking_status in BOOKED_BOOKING_STATUSES:
            problems.append(
                _problem(
                    "xlsx",
                    tournament_id,
                    "proposed placement is presented as booked in the workbook",
                )
            )
    return problems


def verify_proposed_presentation(
    *,
    export_dir: str | Path,
    proposed_tournament_ids: list[str],
    basename: str = "season_plan",
) -> dict[str, Any]:
    """Return the public-presentation contract result for one export.

    ``PASS`` when every proposed tournament is explicitly presented as
    not-confirmed in the readable artifacts. ``FAIL`` when one is presented as
    booked or with no recognizable presentation. ``NOT_CHECKABLE`` when the
    primary HTML projection cannot be read -- publication must never treat an
    unverified presentation as safe.
    """

    proposed = sorted({str(item) for item in proposed_tournament_ids if str(item)})
    result: dict[str, Any] = {
        "status": STATUS_PASS,
        "checked_tournament_ids": proposed,
        "problems": [],
        "artifacts": {},
    }
    if not proposed:
        return result

    root = Path(export_dir)
    html = read_html(root / f"{basename}.html")
    xlsx_path = root / f"{basename}.xlsx"
    xlsx = read_xlsx(xlsx_path)
    result["artifacts"] = {"html": html.artifact_ref(), "xlsx": xlsx.artifact_ref()}

    if not html.readable:
        result["status"] = STATUS_NOT_CHECKABLE
        result["problems"] = [
            _problem(
                "html",
                "",
                f"public HTML projection could not be verified: {html.read_error or 'missing artifact'}",
            )
        ]
        return result

    problems = _check_html_records(html.records_by_id(), proposed)

    # A missing workbook is owned by the artifact-parity/freshness gate; only a
    # present-but-unreadable or mislabelled workbook is a presentation failure.
    if xlsx_path.exists():
        if not xlsx.readable:
            result["status"] = STATUS_NOT_CHECKABLE
            problems.append(
                _problem(
                    "xlsx",
                    "",
                    f"workbook projection could not be verified: {xlsx.read_error or 'unreadable'}",
                )
            )
        else:
            problems.extend(_check_xlsx_records(xlsx.records_by_id(), proposed))

    result["problems"] = problems
    if problems and result["status"] != STATUS_NOT_CHECKABLE:
        result["status"] = STATUS_FAIL
    return result


__all__ = [
    "AWAITING_OPERATIONAL_STATES",
    "BOOKED_BOOKING_STATUSES",
    "BOOKED_OPERATIONAL_STATES",
    "verify_proposed_presentation",
]

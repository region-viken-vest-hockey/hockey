"""Single-operation gate with genuine accepted booking evidence.

The stubbed-debt tests in ``test_single_operation_baseline_gate.py`` do not
exercise real accepted-exception matching. These use the same exact accepted
booking interval as the canonical baseline tests, so the real reclassification
runs for both the preview and the writer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season.consequence_preview import (
    STATUS_BLOCKED,
    STATUS_SAFE_TO_APPLY,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import CanonicalSeasonStore, SeasonStateError
from tournament_scheduler.booking.calendar_bookings import (
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    with_event_fingerprint,
)
from tournament_scheduler.season.ice_time_overrides import append_overrides, validate_and_normalize
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.application.canonical_season.store_facade import promote_from_stage3
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export

SEASON = "2026-2027"
CLUBS = ("A", "B", "C")


def _teams() -> list[dict]:
    return [{"club": club, "label": f"{club}1", "age_group": "U10"} for club in CLUBS]


def _tournament(tournament_id: str, *, date_str: str) -> dict:
    labels = [team["label"] for team in _teams()]
    games = [
        {"home": labels[i], "away": labels[j], "parallel_slot": 0, "round_number": 1}
        for i in range(len(labels))
        for j in range(i + 1, len(labels))
    ]
    return {
        "id": tournament_id,
        "date": date_str,
        "arena": "Arena A",
        "age_group": "U10",
        "host_club": "A",
        "teams": _teams(),
        "games": games,
        "start_time": "10:00",
    }


# The club calendar event that t1's accepted booking interval came from. The
# problem carries it as calendar evidence; the decisions carry the durable
# association that accepts it for exactly this tournament.
ACCEPTED_EVENT = {"date": "2026-09-12", "start": "10:00", "end": "11:50", "title": "Turnering t1"}
ACCEPTED_EVENT_FINGERPRINT = with_event_fingerprint("A", ACCEPTED_EVENT)["fingerprint"]


def _accepted_booking_problem() -> dict:
    return {
        "teams": _teams(),
        "ice_time_minutes": {"U10": 120},
        "club_busy_intervals": {"A": [dict(ACCEPTED_EVENT)]},
        "club_calendar_status": {club: "known" for club in CLUBS},
    }


def _accepted_association() -> dict:
    return {
        "id": "calendar_booking:event:t1",
        "status": "active",
        "event_fingerprint": ACCEPTED_EVENT_FINGERPRINT,
        "tournament_id": "t1",
        "club": "A",
        "date": "2026-09-12",
        "start": "10:00",
        "end": "11:50",
        "tournament_facts": {
            "host_club": "A",
            "arena": "Arena A",
            "date": "2026-09-12",
            "start_time": "10:00",
            "age_group": "U10",
        },
        "tournament_interval": {
            "date": "2026-09-12",
            "start_time": "10:00",
            "duration_minutes": "110",
            "end_time": "11:50",
        },
    }


def _promote(tmp_path: Path) -> Path:
    """Promote t1 and t2, then record t1's accepted booking and 110-minute override in decisions."""

    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    plan = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [_tournament("t1", date_str="2026-09-12"), _tournament("t2", date_str="2026-10-10")],
    }
    state.write_stage(StageName.PLANNING, {"plan": plan}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    service = CanonicalSeasonService(root=root)
    snapshot = service.load(SEASON)
    record = validate_and_normalize(
        {"tournament_id": "t1", "minutes": 110, "request_id": "accepted-t1", "note": "accepted booking"}
    )
    append_overrides(snapshot.decisions, [record])
    snapshot.decisions[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = [_accepted_association()]
    CanonicalSeasonStore(root).write(snapshot)
    return root


def _preview(service: CanonicalSeasonService, **arguments) -> dict:
    return preview_canonical_operation(
        service,
        operation="move",
        season=SEASON,
        actor="tester",
        request_id="accepted-evidence",
        problem=_accepted_booking_problem(),
        **arguments,
    )


def test_unrelated_move_is_admitted_while_the_exact_accepted_interval_stays(tmp_path: Path) -> None:
    root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, tournament_id="t2", date="2026-10-17")

    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    findings = preview["findings"]
    assert findings["classified"] is True
    assert findings["buckets"]["unchanged_accepted"], "the accepted floor exception must stay visible"
    assert findings["buckets"]["unchanged_unaccepted"] == []
    assert findings["acceptable"] is True

    service.move_tournament(
        season=SEASON,
        tournament_id="t2",
        date="2026-10-17",
        actor="tester",
        request_id="accepted-evidence",
        problem=_accepted_booking_problem(),
    )
    schedule = service.load(SEASON).schedule
    dates = {item["id"]: item["date"] for item in schedule["plan"]["tournaments"]}
    assert dates == {"t1": "2026-09-12", "t2": "2026-10-17"}


def test_moving_the_accepted_tournament_is_refused_by_the_preview_and_the_writer(tmp_path: Path) -> None:
    # Moving t1 changes the exact interval the accepted evidence covers, so the
    # exception no longer matches and the shortfall is hard debt again.
    root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, tournament_id="t1", date="2026-09-19")

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert preview["findings"]["acceptable"] is False
    assert preview["findings"]["blocking"], "a changed accepted interval must block"
    with pytest.raises(SeasonStateError):
        service.move_tournament(
            season=SEASON,
            tournament_id="t1",
            date="2026-09-19",
            actor="tester",
            request_id="accepted-evidence",
            problem=_accepted_booking_problem(),
        )
    schedule = service.load(SEASON).schedule
    dates = {item["id"]: item["date"] for item in schedule["plan"]["tournaments"]}
    assert dates["t1"] == "2026-09-12"

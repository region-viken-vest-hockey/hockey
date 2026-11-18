"""Ice-time source port: manual, file and live adapters (#968)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tournament_scheduler.application.ice_time_sources.diff import ChangeKind, diff_snapshots
from tournament_scheduler.application.ice_time_sources.validation import IceTimeValidationError
from tournament_scheduler.application.ice_time_sources.verdict import (
    HealthStatus, Verdict, availability_verdict, find_conflicts, source_health,
)
from tournament_scheduler.application.ports.common import PortError, PortErrorCode
from tournament_scheduler.application.ports.ice_time import (
    Completeness, ConflictKind, IceTimeSourcePort, SourceScope, WindowKind,
)
from tournament_scheduler.infrastructure.ice_time.file_import import file_source, import_snapshot
from tournament_scheduler.infrastructure.ice_time.live import LiveCalendarSource, LivePayload
from tournament_scheduler.infrastructure.ice_time.manual import (
    ManualSnapshotStore, build_manual_snapshot, manual_source,
)

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
START = datetime(2026, 11, 7, 9, tzinfo=OSLO)
END = datetime(2026, 11, 7, 12, tzinfo=OSLO)
SCOPE = SourceScope("rink-a", datetime(2026, 11, 1, tzinfo=OSLO), datetime(2026, 12, 1, tzinfo=OSLO))
AVAIL = WindowKind.AVAILABLE


def manual(windows=((START, END, AVAIL),), complete=True, scope=SCOPE):
    return build_manual_snapshot(source_id="m1", actor="ola", timezone="Europe/Oslo", scope=scope,
                                 windows=list(windows), attested_complete=complete, captured_at=NOW)


def verdict(src, start=START, end=END):
    return availability_verdict(src, SCOPE, "rink-a", start, end, NOW)


def test_manual_is_offline_and_port_conformant():
    src = manual_source(manual())
    assert isinstance(src, IceTimeSourcePort)
    assert not src.capabilities().requires_network
    assert verdict(src) is Verdict.AVAILABLE


def test_empty_manual_does_not_assert_free_ice_and_complete_none_is_distinct():
    unknown = manual_source(manual(windows=(), complete=False))
    assert unknown.coverage(SCOPE)[0].completeness is Completeness.UNKNOWN
    assert verdict(unknown) is Verdict.UNKNOWN
    declared = manual_source(manual(windows=(), complete=True))
    assert declared.coverage(SCOPE)[0].completeness is Completeness.COMPLETE
    # complete declaration needs a fresh bound to count as evidence
    assert verdict(declared) is Verdict.UNKNOWN
    partial = manual_source(manual(complete=False))
    assert partial.coverage(SCOPE)[0].completeness is Completeness.PARTIAL


@pytest.mark.parametrize("windows,needle", [
    (((START, END, AVAIL), (START + timedelta(hours=1), END + timedelta(hours=1), AVAIL)), "overlapping"),
    (((START, END, AVAIL), (START, END, AVAIL)), "duplicate"),
    (((START, END + timedelta(seconds=30), AVAIL),), "sub-minute"),
    (((datetime(2027, 1, 1, tzinfo=OSLO), datetime(2027, 1, 1, 2, tzinfo=OSLO), AVAIL),), "outside"),
])
def test_bad_manual_snapshots_rejected(windows, needle):
    with pytest.raises(IceTimeValidationError, match=needle):
        manual(windows)


def test_wrong_rink_rejected(tmp_path):
    scope = SourceScope("rink-b", SCOPE.from_date, SCOPE.to_date)
    snap = manual(scope=scope)
    assert snap.scope.venue_id == "rink-b"  # windows adopt the scope venue
    csv = tmp_path / "a.csv"
    csv.write_text("venue_id,date,start,end,kind\nrink-a,2026-11-07,09:00,12:00,available\n")
    with pytest.raises(IceTimeValidationError, match="wrong rink"):
        import_snapshot(csv, source_id="f", timezone="Europe/Oslo", scope=scope, captured_at=NOW)


def test_manual_store_roundtrip_cas_and_diff(tmp_path):
    store = ManualSnapshotStore(tmp_path)
    first = manual()
    assert store.replace(first, None).changes[0].kind is ChangeKind.ADDED
    assert store.load("m1").revision == first.revision
    with pytest.raises(PortError) as err:
        store.replace(manual(windows=()), None)
    assert err.value.code is PortErrorCode.REVISION_CONFLICT
    shorter = manual(windows=((START, END - timedelta(hours=1), AVAIL),))
    diff = store.replace(shorter, first.revision)
    assert [c.kind for c in diff.changes] == [ChangeKind.REDUCED]
    assert diff.invalidates_previews
    revoked = manual(windows=())
    assert store.replace(revoked, shorter.revision).changes[0].kind is ChangeKind.REMOVED
    with pytest.raises(PortError):
        store.load("../x")


def test_additions_alone_do_not_invalidate():
    d = diff_snapshots((), manual().windows, Completeness.COMPLETE, Completeness.COMPLETE)
    assert not d.invalidates_previews and not d.is_empty


def csv_file(tmp_path, body):
    p = tmp_path / "ice.csv"
    p.write_text("venue_id,date,start,end,kind\n" + body)
    return p


def test_csv_matches_manual_verdict(tmp_path):
    p = csv_file(tmp_path, "rink-a,2026-11-07,09:00,12:00,available\n")
    snap = import_snapshot(p, source_id="f", timezone="Europe/Oslo", scope=SCOPE,
                           captured_at=NOW, attested_complete=True)
    assert snap.source.fingerprint and snap.source.document == "ice.csv"
    assert [w.starts_at for w in snap.windows] == [START]
    assert verdict(file_source(snap)) is verdict(manual_source(manual())) is Verdict.AVAILABLE
    assert snap.revision == manual().revision


def test_csv_bad_rows_fail_whole_import(tmp_path):
    p = csv_file(tmp_path, "rink-a,2026-11-07,09:00,12:00,available\nrink-a,2026-11-08,xx,12:00,available\n")
    with pytest.raises(IceTimeValidationError, match="row 3"):
        import_snapshot(p, source_id="f", timezone="Europe/Oslo", scope=SCOPE, captured_at=NOW)


@pytest.mark.parametrize("day,clock", [("2026-03-29", "02:30"), ("2026-10-25", "02:30")])
def test_dst_gap_and_fold_rejected(tmp_path, day, clock):
    p = csv_file(tmp_path, f"rink-a,{day},{clock},04:00,available\n")
    scope = SourceScope("rink-a", datetime(2026, 3, 1, tzinfo=OSLO), datetime(2026, 11, 30, tzinfo=OSLO))
    with pytest.raises(IceTimeValidationError, match="ambiguous or nonexistent"):
        import_snapshot(p, source_id="f", timezone="Europe/Oslo", scope=scope, captured_at=NOW)


def test_xlsx_import(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["venue_id", "date", "start", "end", "kind"])
    ws.append(["rink-a", "2026-11-07", "09:00", "12:00", "available"])
    p = tmp_path / "ice.xlsx"
    wb.save(p)
    snap = import_snapshot(p, source_id="x", timezone="Europe/Oslo", scope=SCOPE, captured_at=NOW)
    assert [w.ends_at for w in snap.windows] == [END]
    with pytest.raises(PortError):
        import_snapshot(tmp_path / "a.ics", source_id="x", timezone="Europe/Oslo", scope=SCOPE, captured_at=NOW)


def live(fail=None, events=(), completeness=Completeness.COMPLETE, clock=None):
    state = {"fail": fail}

    def fetch(scope):
        if state["fail"]:
            raise PortError(PortErrorCode.UNAVAILABLE, "down")
        return LivePayload("sha", events, completeness)

    src = LiveCalendarSource("rvv", "Europe/Oslo", fetch, clock or (lambda: NOW))
    return src, state


def test_live_events_block_and_never_assert_booking():
    src, _ = live(events=(("e1", "rink-a", START, END, "Cup"),))
    src.refresh(SCOPE)
    assert src.capabilities().requires_network
    assert verdict(src) is Verdict.UNAVAILABLE
    assert src.observed_events(SCOPE)[0].title == "Cup"


def test_live_failure_retains_stale_limited_evidence():
    src, state = live()
    with pytest.raises(PortError):
        src.coverage(SCOPE)
    src.refresh(SCOPE)
    assert src.coverage(SCOPE)[0].is_fresh(NOW)
    state["fail"] = True
    src.refresh(SCOPE)
    cov = src.coverage(SCOPE)[0]
    assert cov.completeness is Completeness.PARTIAL and not cov.is_fresh(NOW + timedelta(seconds=1))
    assert source_health(src.coverage(SCOPE), NOW + timedelta(hours=1)).status is HealthStatus.PARTIAL


def test_live_failure_beyond_policy_is_error():
    times = iter([NOW, NOW + timedelta(days=5)])
    src, state = live(clock=lambda: next(times))
    src.refresh(SCOPE)
    state["fail"] = True
    with pytest.raises(PortError):
        src.refresh(SCOPE)


def test_health_manual_vs_unconfigured():
    assert source_health((), NOW).status is HealthStatus.UNCONFIGURED
    cov = manual_source(manual()).coverage(SCOPE)
    assert source_health(cov, NOW).status is HealthStatus.CURRENT
    assert source_health(cov, NOW + timedelta(days=60)).status is HealthStatus.NEEDS_REATTESTATION
    unk = manual_source(manual(windows=(), complete=False)).coverage(SCOPE)
    assert source_health(unk, NOW).status is HealthStatus.UNVERIFIED


def test_independent_contradictory_sources_require_review(tmp_path):
    a = manual().windows
    b = import_snapshot(csv_file(tmp_path, "rink-a,2026-11-07,09:00,12:00,blocked\n"),
                        source_id="f", timezone="Europe/Oslo", scope=SCOPE, captured_at=NOW).windows
    conflicts = find_conflicts(a + b)
    assert [c.kind for c in conflicts] == [ConflictKind.CONTRADICTORY_SOURCES]
    assert find_conflicts(a + a) == ()


def test_blocked_window_overrides_available_for_revoked_allocation():
    src = manual_source(manual(((START, END, AVAIL), (START, START + timedelta(hours=1), WindowKind.BLOCKED))))
    assert verdict(src, START, START + timedelta(hours=1)) is Verdict.UNAVAILABLE
    assert verdict(src, START + timedelta(hours=1), END) is Verdict.AVAILABLE

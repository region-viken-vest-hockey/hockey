"""#958: normalized season-input adapters and evidence composition."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import openpyxl
import pytest

from tournament_scheduler.application.input_composition import (
    Absence,
    FactOrigin,
    VenueCapacity,
    compose_planning_evidence,
    origin_of,
)
from tournament_scheduler.application.ports.common import (
    PortCapabilities,
    PortError,
    PortErrorCode,
    SourceKind,
    SourceRef,
)
from tournament_scheduler.application.ports.ice_time import (
    AssertionTrust,
    Completeness,
    ConflictKind,
    IceTimeSourcePort,
    ObservedCalendarEvent,
    SourceCoverage,
    SourceScope,
)
from tournament_scheduler.application.ports.season_input import (
    DiagnosticCode,
    SeasonInputError,
    SeasonInputPort,
)
from tournament_scheduler.input_adapters import (
    LocalBundleAdapter,
    WorkbookSeasonInputAdapter,
    to_legacy_config,
)
from tournament_scheduler.pipeline.input_workbook import load_workbook_config

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
clock = lambda: NOW  # noqa: E731

TEAMS = [("Kongsberg", "Kongsberg U10A", "U10"), ("Skien", "Skien U10A", "U10"), ("Jar", "Jar U12A", "U12")]


def _workbook(path: Path, *, with_sources: bool) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Innstillinger"
    ws.append(["felt", "verdi"])
    ws.append(["start_date", "2026-09-01"])
    ws.append(["end_date", "2027-03-31"])
    ws.append(["max_hosting_days_per_month", 4])
    ag = wb.create_sheet("Aldersgrupper")
    ag.append(["age_group", "parallel_games", "ice_time_minutes", "deltakelser_per_lag_før_jul"])
    ag.append(["U10", 3, 135, 2])
    ag.append(["U12", 2, 120, 3])
    lag = wb.create_sheet("Lag")
    lag.append(["club", "label", "age_group"])
    for row in TEAMS:
        lag.append(list(row))
    if with_sources:
        k = wb.create_sheet("Kilder")
        k.append(["name", "type", "url"])
        k.append(["Kongsberg kalender", "ical", "https://example.invalid/feed.ics"])
    wb.save(path)
    return path


def _bundle(root: Path) -> Path:
    root.mkdir()
    (root / "settings.csv").write_text("felt,verdi\nstart_date,2026-09-01\nend_date,2027-03-31\nmax_hosting_days_per_month,4\n")
    (root / "age_groups.csv").write_text(
        "age_group,parallel_games,ice_time_minutes,deltakelser_per_lag_før_jul\nU10,3,135,2\nU12,2,120,3\n"
    )
    (root / "teams.csv").write_text("club,label,age_group\n" + "\n".join(",".join(t) for t in TEAMS) + "\n")
    return root


def test_workbook_adapter_matches_existing_loader_and_port(tmp_path):
    path = _workbook(tmp_path / "season.xlsx", with_sources=True)
    adapter = WorkbookSeasonInputAdapter(path, clock=clock)
    assert isinstance(adapter, SeasonInputPort)
    season = adapter.read()
    assert to_legacy_config(season) == load_workbook_config(path)
    assert season.origin.kind is SourceKind.FILE and season.origin.fingerprint
    assert season.sources[0].url == "https://example.invalid/feed.ics"


def test_repo_input_workbook_round_trips_through_adapter():
    root = Path("input.xlsx")
    if not root.exists():
        pytest.skip("repo input.xlsx not present")
    legacy = load_workbook_config(root)
    try:
        season = WorkbookSeasonInputAdapter(root, clock=clock).read()
    except SeasonInputError as exc:  # real workbook may be mid-edit; diagnostics must be typed
        assert exc.diagnostics
        return
    assert to_legacy_config(season) == legacy


def test_local_csv_bundle_has_identical_problem_semantics_to_workbook(tmp_path):
    from_wb = WorkbookSeasonInputAdapter(_workbook(tmp_path / "w.xlsx", with_sources=False), clock=clock).read()
    from_csv = LocalBundleAdapter(_bundle(tmp_path / "b"), clock=clock).read()
    assert from_csv.problem_fingerprint() == from_wb.problem_fingerprint()
    assert from_csv.origin.source_id != from_wb.origin.source_id


def test_local_bundle_runs_without_repo_root_input_or_git(tmp_path):
    bundle = _bundle(tmp_path / "region")
    script = (
        "import os;from tournament_scheduler.input_adapters import LocalBundleAdapter as A;"
        f"s=A({str(bundle)!r}).read();print(len(s.teams), os.path.exists('input.xlsx'))"
    )
    env = {"PATH": "", "PYTHONPATH": str(Path.cwd()), "GIT_DIR": str(tmp_path / "nogit")}
    out = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["3", "False"]


def test_local_json_and_manual_only_allocations_need_no_source_row(tmp_path):
    (tmp_path / "s.json").write_text(
        '{"start_date":"2026-09-01","end_date":"2027-03-31","teams":[{"club":"A","label":"A1","age_group":"U10"}],'
        '"allocations":[{"venue_id":"hall","starts_at":"2026-10-03T09:00","ends_at":"2026-10-03T15:00"}],'
        '"bookings":[{"tournament_id":"t1","venue_id":"hall","starts_at":"2026-10-03T09:00","ends_at":"2026-10-03T12:00","trust":"confirmed"}]}'
    )
    season = LocalBundleAdapter(tmp_path / "s.json", actor="niclas", clock=clock).read()
    assert season.sources == ()
    window = season.operator_allocations[0]
    assert window.source.kind is SourceKind.MANUAL and window.starts_at.utcoffset() is not None
    assert season.operator_assertions[0].trust is AssertionTrust.CONFIRMED
    ev = compose_planning_evidence(season, [], [], now=NOW)
    assert dict(ev.venue_capacity) == {"hall": VenueCapacity.OFFERED}
    assert ev.assertions == season.operator_assertions and ev.complete


def test_operator_facts_without_actor_are_rejected(tmp_path):
    (tmp_path / "s.json").write_text(
        '{"start_date":"2026-09-01","end_date":"2027-03-31","teams":[{"club":"A","label":"A1","age_group":"U10"}],'
        '"allocations":[{"venue_id":"hall","starts_at":"2026-10-03T09:00","ends_at":"2026-10-03T15:00"}]}'
    )
    with pytest.raises(SeasonInputError) as exc:
        LocalBundleAdapter(tmp_path / "s.json", clock=clock).read()
    assert [d.location for d in exc.value.diagnostics] == ["actor"]


def test_invalid_schema_reports_every_typed_diagnostic(tmp_path):
    (tmp_path / "s.json").write_text(
        '{"start_date":"2027-01-01","end_date":"2026-01-01","age_groups":["U10"],'
        '"teams":[{"club":"A","label":"A1","age_group":"U12"},{"club":"A","label":"A1","age_group":"U10"},'
        '{"club":"A","label":"A1","age_group":"U10"},{"label":"x"}],'
        '"sources":[{"name":"s","type":"ical"},{"name":"s","type":"google"}]}'
    )
    with pytest.raises(SeasonInputError) as exc:
        LocalBundleAdapter(tmp_path / "s.json", clock=clock).read()
    codes = {d.code for d in exc.value.diagnostics}
    assert codes == {
        DiagnosticCode.INVALID_VALUE, DiagnosticCode.UNKNOWN_REFERENCE, DiagnosticCode.DUPLICATE,
        DiagnosticCode.MISSING_FIELD, DiagnosticCode.AMBIGUOUS,
    }
    assert exc.value.code is PortErrorCode.SCHEMA_INCOMPATIBLE


@pytest.mark.parametrize("make,code", [
    (lambda p: p / "absent", DiagnosticCode.MISSING_INPUT),
    (lambda p: p / "x.txt", DiagnosticCode.UNSUPPORTED_FORMAT),
    (lambda p: p / "bad.json", DiagnosticCode.UNREADABLE),
])
def test_missing_unsupported_or_unreadable_inputs_fail_typed(tmp_path, make, code):
    (tmp_path / "x.txt").write_text("x")
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(SeasonInputError) as exc:
        LocalBundleAdapter(make(tmp_path), clock=clock).read()
    assert exc.value.diagnostics[0].code is code


def test_workbook_adapter_failures_are_typed_and_write_nothing(tmp_path):
    with pytest.raises(SeasonInputError) as exc:
        WorkbookSeasonInputAdapter(tmp_path / "none.xlsx").read()
    assert exc.value.diagnostics[0].code is DiagnosticCode.MISSING_INPUT and exc.value.code is PortErrorCode.NOT_FOUND
    wb = openpyxl.Workbook()
    wb.active.title = "Innstillinger"
    wb.save(tmp_path / "bad.xlsx")
    with pytest.raises(SeasonInputError) as exc:
        WorkbookSeasonInputAdapter(tmp_path / "bad.xlsx").read()
    assert exc.value.diagnostics[0].code is DiagnosticCode.UNREADABLE
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.xlsx"]


# -- composition ---------------------------------------------------------------------

REF = SourceRef("cal", SourceKind.LIVE, NOW, "Europe/Oslo", fingerprint="abc")
SCOPE = SourceScope("hall", NOW, NOW + timedelta(days=7))


class FakeSource:
    def __init__(self, *, events=(), completeness=Completeness.COMPLETE, fresh=True, fail=None, cover=True):
        self.events, self.completeness, self.fresh, self.fail, self.cover = events, completeness, fresh, fail, cover

    def capabilities(self):
        return PortCapabilities("fake")

    def _check(self):
        if self.fail:
            raise PortError(self.fail, "boom")

    def allocations(self, scope):
        self._check()
        return ()

    def observed_events(self, scope):
        self._check()
        return tuple(self.events)

    def coverage(self, scope):
        self._check()
        if not self.cover:
            return ()
        until = NOW + timedelta(hours=1) if self.fresh else NOW - timedelta(hours=1)
        return (SourceCoverage(REF, scope, self.completeness, NOW, until),)


@pytest.fixture
def season(tmp_path):
    return LocalBundleAdapter(_bundle(tmp_path / "b"), clock=clock).read()


def _absence(season, source):
    ev = compose_planning_evidence(season, [source], [SCOPE], now=NOW)
    return ev, ev.findings[0].absence


def test_fake_source_satisfies_port():
    assert isinstance(FakeSource(), IceTimeSourcePort)


def test_absence_is_confirmed_only_for_complete_fresh_coverage(season):
    ev, absence = _absence(season, FakeSource())
    assert absence is Absence.CONFIRMED_ABSENT and ev.complete and ev.assertions == ()
    assert dict(ev.venue_capacity)["hall"] is VenueCapacity.UNKNOWN  # empty != free


@pytest.mark.parametrize("kwargs", [
    {"completeness": Completeness.PARTIAL}, {"completeness": Completeness.UNKNOWN},
    {"fresh": False}, {"cover": False},
])
def test_incomplete_stale_or_uncovered_is_not_confirmed(season, kwargs):
    ev, absence = _absence(season, FakeSource(**kwargs))
    assert absence is Absence.NOT_CONFIRMED and not ev.complete


def test_source_error_is_not_an_empty_result(season):
    ev, absence = _absence(season, FakeSource(fail=PortErrorCode.UNAVAILABLE))
    assert absence is Absence.SOURCE_ERROR
    assert ev.failures[0].code is PortErrorCode.UNAVAILABLE and ev.observed_events == () and not ev.complete


def test_observed_events_never_become_assertions(season):
    event = ObservedCalendarEvent(REF, "e1", "hall", NOW, NOW + timedelta(hours=2), "Turnering")
    ev, absence = _absence(season, FakeSource(events=[event]))
    assert absence is Absence.PRESENT and ev.observed_events == (event,) and ev.assertions == ()


def test_stale_complete_coverage_is_an_explicit_conflict(season):
    ev, _ = _absence(season, FakeSource(fresh=False))
    assert [c.kind for c in ev.conflicts] == [ConflictKind.STALE_COVERAGE]


def test_one_failing_source_does_not_hide_the_other(season):
    ev = compose_planning_evidence(season, [FakeSource(fail=PortErrorCode.UNAVAILABLE), FakeSource()], [SCOPE], now=NOW)
    assert [f.absence for f in ev.findings] == [Absence.SOURCE_ERROR, Absence.CONFIRMED_ABSENT]


def test_fact_origin_distinguishes_operator_from_source_backed():
    assert origin_of(SourceKind.MANUAL) is FactOrigin.OPERATOR_SUPPLIED
    assert origin_of(SourceKind.LIVE) is FactOrigin.SOURCE_BACKED
    assert origin_of(SourceKind.FILE) is FactOrigin.IMPORTED_FILE


def test_assertion_conflicts_are_explicit(tmp_path):
    rows = (
        '"bookings":[{"tournament_id":"t1","venue_id":"hall","starts_at":"2026-10-03T09:00","ends_at":"2026-10-03T12:00","trust":"confirmed"},'
        '{"tournament_id":"t2","venue_id":"hall","starts_at":"2026-10-03T11:00","ends_at":"2026-10-03T13:00"}],'
        '"allocations":[{"venue_id":"hall","starts_at":"2026-10-03T10:00","ends_at":"2026-10-03T11:00","kind":"blocked"}]'
    )
    (tmp_path / "s.json").write_text(
        '{"start_date":"2026-09-01","end_date":"2027-03-31","teams":[{"club":"A","label":"A1","age_group":"U10"}],' + rows + "}"
    )
    season = LocalBundleAdapter(tmp_path / "s.json", actor="x", clock=clock).read()
    ev = compose_planning_evidence(season, [], [], now=NOW)
    kinds = {c.kind for c in ev.conflicts}
    assert kinds == {ConflictKind.OVERLAPPING_ASSERTIONS, ConflictKind.ASSERTION_VS_BLOCKED_WINDOW}
    assert dict(ev.venue_capacity)["hall"] is VenueCapacity.BLOCKED_ONLY

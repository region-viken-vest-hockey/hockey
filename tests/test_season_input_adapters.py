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
    EvidenceNotReady,
    Readiness,
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


def _rvv_fixture(path: Path) -> Path:
    """Frozen RVV-shaped workbook: every sheet, a fixed-allocation row with no URL."""
    _workbook(path, with_sources=True)
    wb = openpyxl.load_workbook(path)
    wb["Kilder"].append(["Sandefjord Penguins", "fixed_allocation", None])
    wb["Kilder"].append(["Ingen url", "ical", None])  # dropped by the legacy reader
    prefs = wb.create_sheet("Datopreferanser")
    prefs.append(["fra", "til", "vekt"])
    prefs.append(["2026-12-20", "2027-01-03", -2.5])
    wb.save(path)
    return path


def test_frozen_rvv_fixture_normalizes_with_legacy_semantics(tmp_path):
    path = _rvv_fixture(tmp_path / "rvv.xlsx")
    legacy = load_workbook_config(path)
    season = WorkbookSeasonInputAdapter(path, clock=clock).read()
    assert to_legacy_config(season) == legacy
    assert [s.name for s in season.sources] == ["Kongsberg kalender", "Sandefjord Penguins"]
    assert season.sources[1].url is None and season.sources[1].source_type == "fixed_allocation"
    assert season.age_groups == ("U10", "U12") and season.age_groups_declared
    assert season.rules["datopreferanser"] == legacy["datopreferanser"]


def test_workbook_missing_required_sheet_is_a_typed_negative(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Lag"
    wb.save(tmp_path / "w.xlsx")
    with pytest.raises(SeasonInputError) as exc:
        WorkbookSeasonInputAdapter(tmp_path / "w.xlsx", clock=clock).read()
    assert exc.value.diagnostics[0].code is DiagnosticCode.UNREADABLE


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
    def __init__(self, *, events=(), completeness=Completeness.COMPLETE, fresh=True, fail=None, cover=True, cover_scope=None, cover_ref=None):
        self.events, self.completeness, self.fresh, self.fail, self.cover = events, completeness, fresh, fail, cover
        self.cover_scope, self.cover_ref = cover_scope, cover_ref

    def capabilities(self):
        return PortCapabilities("cal")

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
        return (SourceCoverage(self.cover_ref or REF, self.cover_scope or scope, self.completeness, NOW, until),)


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


# -- fail-closed gate, identity and scope ----------------------------------------------

class ScriptedSource(FakeSource):
    """Succeeds with an offered window for the first scope, then fails."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def allocations(self, scope):
        self.calls += 1
        if self.calls > 1:
            raise PortError(PortErrorCode.UNAVAILABLE, "second scope down")
        from tournament_scheduler.application.ports.ice_time import AllocationWindow, WindowKind
        return (AllocationWindow(REF, scope.venue_id, NOW, NOW + timedelta(hours=2), WindowKind.AVAILABLE),)


def test_partial_success_then_failure_cannot_be_consumed_as_capacity(season):
    other = SourceScope("hall2", NOW, NOW + timedelta(days=7))
    ev = compose_planning_evidence(season, [ScriptedSource()], [SCOPE, other], now=NOW)
    assert dict(ev.venue_capacity)["hall"] is VenueCapacity.OFFERED  # diagnostic only
    assert ev.readiness is Readiness.FAILED and not ev.complete
    with pytest.raises(EvidenceNotReady) as exc:
        ev.trusted_capacity()
    assert "second scope down" in exc.value.message and exc.value.evidence.failures


def test_ready_evidence_yields_trusted_capacity(season):
    ev = compose_planning_evidence(season, [FakeSource()], [SCOPE], now=NOW)
    assert ev.readiness is Readiness.READY and ev.trusted_capacity() == ev.venue_capacity


def test_incomplete_or_conflicting_evidence_is_not_trusted(season):
    incomplete = compose_planning_evidence(season, [FakeSource(completeness=Completeness.PARTIAL)], [SCOPE], now=NOW)
    assert incomplete.readiness is Readiness.INCOMPLETE
    with pytest.raises(EvidenceNotReady):
        incomplete.trusted_capacity()


@pytest.mark.parametrize("source,needle", [
    (FakeSource(cover_ref=SourceRef("other", SourceKind.LIVE, NOW, "Europe/Oslo", fingerprint="x")), "other"),
    (FakeSource(cover_scope=SourceScope("elsewhere", NOW, NOW + timedelta(days=7))), "elsewhere"),
    (FakeSource(cover_scope=SourceScope("hall", NOW, NOW + timedelta(days=1))), "coverage is for scope"),
    (FakeSource(events=[ObservedCalendarEvent(REF, "e", "elsewhere", NOW, NOW + timedelta(hours=1))]), "elsewhere"),
    (FakeSource(events=[ObservedCalendarEvent(REF, "e", "hall", NOW + timedelta(days=30), NOW + timedelta(days=31))]), "outside"),
    (FakeSource(events=[ObservedCalendarEvent(SourceRef("zzz", SourceKind.LIVE, NOW, "Europe/Oslo", fingerprint="x"), "e", "hall", NOW, NOW + timedelta(hours=1))]), "mixes"),
])
def test_foreign_or_out_of_scope_evidence_is_a_typed_failure(season, source, needle):
    ev = compose_planning_evidence(season, [source], [SCOPE], now=NOW)
    assert ev.failures[0].code is PortErrorCode.SCHEMA_INCOMPATIBLE and needle in ev.failures[0].message
    assert ev.coverage == () and ev.observed_events == () and ev.readiness is Readiness.FAILED


# -- strict CSV / JSON shapes -----------------------------------------------------------

def _diags(location):
    with pytest.raises(SeasonInputError) as exc:
        LocalBundleAdapter(location, actor="op", clock=clock).read()
    return exc.value.diagnostics


def test_csv_missing_header_and_wrong_field_count_are_located(tmp_path):
    root = _bundle(tmp_path / "b")
    (root / "teams.csv").write_text("club,label\nA,A1\n")
    (root / "allocations.csv").write_text("venue_id,starts_at,ends_at\nhall,2026-10-03T09:00\nhall,2026-10-03T09:00,2026-10-03T10:00,extra\n")
    got = {(d.code, d.location) for d in _diags(root)}
    assert got == {
        (DiagnosticCode.MISSING_FIELD, "teams.csv#header"),
        (DiagnosticCode.INVALID_VALUE, "allocations.csv#1"),
        (DiagnosticCode.INVALID_VALUE, "allocations.csv#2"),
    }


def test_csv_malformed_rows_report_file_and_row(tmp_path):
    root = _bundle(tmp_path / "b")
    (root / "allocations.csv").write_text("venue_id,starts_at,ends_at\nhall,2026-10-03T09:00\nhall,2026-10-03T09:00,2026-10-03T10:00,extra\n")
    got = {(d.code, d.location) for d in _diags(root)}
    assert got == {(DiagnosticCode.INVALID_VALUE, "allocations.csv#1"), (DiagnosticCode.INVALID_VALUE, "allocations.csv#2")}


def test_csv_blank_cell_and_bad_values_are_located(tmp_path):
    root = _bundle(tmp_path / "b")
    (root / "allocations.csv").write_text("venue_id,starts_at,ends_at,kind\n,2026-10-03T09:00,2026-10-03T10:00,\nhall,nope,2026-10-03T10:00,\nhall,2026-10-03T09:00,2026-10-03T10:00,weird\n")
    got = {d.location for d in _diags(root)}
    assert got == {"b:allocations#1", "b:allocations#2.starts_at", "b:allocations#3"}


def test_csv_empty_and_duplicate_header_files_fail_typed(tmp_path):
    root = _bundle(tmp_path / "b")
    (root / "settings.csv").write_text("")
    (root / "teams.csv").write_text("club,club,label,age_group\nA,A,A1,U10\n")
    got = {(d.code, d.location) for d in _diags(root)}
    assert got == {(DiagnosticCode.MISSING_FIELD, "settings.csv#header"), (DiagnosticCode.DUPLICATE, "teams.csv#header")}


def test_missing_optional_csv_is_fine_but_missing_required_is_not(tmp_path):
    root = _bundle(tmp_path / "b")
    assert LocalBundleAdapter(root, clock=clock).read().operator_allocations == ()
    (root / "teams.csv").unlink()
    assert [(d.code, d.location) for d in _diags(root)] == [(DiagnosticCode.MISSING_INPUT, "teams.csv")]


def test_csv_duplicate_setting_is_ambiguous(tmp_path):
    root = _bundle(tmp_path / "b")
    (root / "settings.csv").write_text("felt,verdi\nstart_date,2026-09-01\nstart_date,2026-10-01\nend_date,2027-03-31\n")
    assert [(d.code, d.location) for d in _diags(root)] == [(DiagnosticCode.DUPLICATE, "settings.csv#2.felt")]


@pytest.mark.parametrize("payload", ['"allocations":[null]', '"bookings":["x",3]', '"allocations":{"a":1}', '"sources":[null]', '"teams":[null]'])
def test_json_malformed_shapes_never_leak_attribute_errors(tmp_path, payload):
    base = '"start_date":"2026-09-01","end_date":"2027-03-31","teams":[{"club":"A","label":"A1","age_group":"U10"}]'
    if payload.startswith('"teams"'):
        base = '"start_date":"2026-09-01","end_date":"2027-03-31"'
    (tmp_path / "s.json").write_text("{" + base + "," + payload + "}")
    diags = _diags(tmp_path / "s.json")
    assert diags and all(d.code is DiagnosticCode.INVALID_VALUE or d.code is DiagnosticCode.MISSING_FIELD for d in diags)
    assert all(d.location.startswith("s.json") for d in diags)


def test_json_row_location_names_the_row(tmp_path):
    (tmp_path / "s.json").write_text('{"start_date":"2026-09-01","end_date":"2027-03-31","teams":[{"club":"A","label":"A1","age_group":"U10"}],"allocations":[null]}')
    assert [d.location for d in _diags(tmp_path / "s.json")] == ["s.json:allocations#1"]


# -- age groups and parameters ----------------------------------------------------------

def _json(tmp_path, extra="", teams='[{"club":"A","label":"A1","age_group":"U10"}]'):
    (tmp_path / "s.json").write_text('{"start_date":"2026-09-01","end_date":"2027-03-31","teams":' + teams + extra + "}")
    return tmp_path / "s.json"


def test_undeclared_age_groups_are_derived_explicitly_from_teams(tmp_path):
    season = LocalBundleAdapter(_json(tmp_path), clock=clock).read()
    assert season.age_groups == ("U10",) and not season.age_groups_declared
    assert "age_groups" not in to_legacy_config(season)


def test_declared_age_groups_reject_unknown_team_group(tmp_path):
    got = _diags(_json(tmp_path, ',"age_groups":["U12"]'))
    assert [(d.code, d.location) for d in got] == [(DiagnosticCode.UNKNOWN_REFERENCE, "s.json:teams#1.age_group")]


@pytest.mark.parametrize("extra,code", [
    (',"parallel_games":{"U10":"three"}', DiagnosticCode.INVALID_VALUE),
    (',"parallel_games":{"U10":0}', DiagnosticCode.INVALID_VALUE),
    (',"ice_time_minutes":[135]', DiagnosticCode.INVALID_VALUE),
    (',"preferanse_vekt":{"U10":"x"}', DiagnosticCode.INVALID_VALUE),
    (',"participation_targets_by_age_group":{"U10":{"before_christmas":-1}}', DiagnosticCode.INVALID_VALUE),
    (',"participation_targets_by_age_group":{"U10":{"whenever":1}}', DiagnosticCode.INVALID_VALUE),
    (',"parallel_games":{"U99":3}', DiagnosticCode.UNKNOWN_REFERENCE),
    (',"age_groups":"U10"', DiagnosticCode.INVALID_VALUE),
])
def test_malformed_age_group_parameters_are_rejected(tmp_path, extra, code):
    assert code in {d.code for d in _diags(_json(tmp_path, extra))}


def test_csv_non_numeric_age_group_parameter_is_rejected(tmp_path):
    root = _bundle(tmp_path / "b")
    (root / "age_groups.csv").write_text("age_group,parallel_games\nU10,abc\nU12,2\n")
    assert [d.code for d in _diags(root)] == [DiagnosticCode.INVALID_VALUE]

"""Tests for the Spond bulk-import projection and workbooks."""

from datetime import date, time

import openpyxl

from tournament_scheduler.models import Game, SeasonPlan, Team, Tournament
from tournament_scheduler.spond.spond_exporter import SpondExporter
from tournament_scheduler.spond.spond_import import (
    HELPER_HEADERS,
    SPOND_IMPORT_HEADERS,
    SPOND_IMPORT_SHEET,
    project_spond_import_rows,
    team_identity,
    write_per_team_workbooks,
)


def _plan(*, cancel_second: bool = False) -> SeasonPlan:
    jar1, jar2 = Team("Jar", "Jar 1", "U10"), Team("Jar", "Jar 2", "U10")
    holmen, frisk = Team("Holmen", "Holmen 1", "U10"), Team("Frisk Asker", "Frisk Asker 3", "U10")
    first = Tournament(
        date=date(2026, 11, 14),
        arena="Holmen Ishall",
        age_group="U10",
        id="rvv-0123",
        teams=[holmen, frisk, jar2],
        games=[Game(home=holmen, away=frisk, round_number=1)],
        host_club="Holmen",
        start_time="09:00",
    )
    second = Tournament(
        date=date(2026, 12, 5),
        arena="Jar Isforum",
        age_group="U10",
        id="rvv-0200",
        teams=[jar1, jar2],
        games=[Game(home=jar1, away=jar2, round_number=1)],
        host_club="Jar",
        start_time="10:00",
        cancelled=cancel_second,
        cancellation_reason="Ishall stengt" if cancel_second else None,
    )
    return SeasonPlan(tournaments=[second, first])


def test_headers_match_spond_grid_exactly():
    assert SPOND_IMPORT_HEADERS == (
        "Startdato*", "Starttidspunkt", "Oppmøte", "Sluttdato",
        "Sluttidspunkt", "Tittel*", "Beskrivelse", "Sted",
    )


def test_workbook_has_table_over_all_data_rows_with_spond_columns_first(tmp_path):
    out = tmp_path / "spond.xlsx"
    SpondExporter().export(_plan(), str(out), ice_time_for_age_group={"U10": 60})
    sheet = openpyxl.load_workbook(out)[SPOND_IMPORT_SHEET]

    header = [c.value for c in sheet[1]]
    assert header == list(SPOND_IMPORT_HEADERS + HELPER_HEADERS)
    assert len(sheet.tables) == 1
    table = next(iter(sheet.tables.values()))
    assert table.ref == f"A1:N{sheet.max_row}"
    assert sheet.freeze_panes == "A2"


def test_rows_are_per_team_and_deterministic():
    rows = project_spond_import_rows(_plan(), {"U10": 60})
    assert rows == project_spond_import_rows(_plan(), {"U10": 60})
    assert [(r.start_date, r.team) for r in rows] == [
        (date(2026, 11, 14), "Frisk Asker 3"),
        (date(2026, 11, 14), "Holmen 1"),
        (date(2026, 11, 14), "Jar 2"),
        (date(2026, 12, 5), "Jar 1"),
        (date(2026, 12, 5), "Jar 2"),
    ]


def test_selected_team_gets_one_event_per_tournament():
    rows = project_spond_import_rows(_plan(), {"U10": 60}, team=("Jar", "Jar 2", "U10"))
    assert [r.tournament_id for r in rows] == ["rvv-0123", "rvv-0200"]
    assert len({r.tournament_id for r in rows}) == len(rows)


def test_cancelled_tournaments_are_not_importable():
    rows = project_spond_import_rows(_plan(cancel_second=True), {"U10": 60})
    assert {r.tournament_id for r in rows} == {"rvv-0123"}


def test_date_time_and_end_time_mapping_and_description():
    row = project_spond_import_rows(_plan(), {"U10": 60}, team=("Holmen", "Holmen 1", "U10"))[0]
    assert row.start_date == date(2026, 11, 14)
    assert row.start_time == time(9, 0)
    assert row.end_date == date(2026, 11, 14)
    assert row.end_time is not None and row.end_time > time(9, 0)
    assert row.meet_time is None
    assert row.venue == "Holmen Ishall"
    assert row.description == (
        "U10 • Vert: Holmen • Lag: Holmen 1, Frisk Asker 3, Jar 2 • RVV-ID: rvv-0123"
    )


def test_per_team_workbooks_contain_only_that_team(tmp_path):
    written = write_per_team_workbooks(_plan(cancel_second=True), tmp_path / "spond", {"U10": 60})

    # Jar 1 only had the cancelled tournament
    assert sorted(label for _club, label, _age in written) == ["Frisk Asker 3", "Holmen 1", "Jar 2"]
    assert (tmp_path / "spond" / "Jar 2.xlsx").exists()
    sheet = openpyxl.load_workbook(tmp_path / "spond" / "Jar 2.xlsx")[SPOND_IMPORT_SHEET]
    data = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(data) == 1
    assert data[0][13] == "rvv-0123"
    assert data[0][8] == "Jar · Jar 2 · U10"


def test_same_label_in_different_age_groups_gets_separate_workbooks(tmp_path):
    u10, u12 = Team("Jar", "Jar 1", "U10"), Team("Jar", "Jar 1", "U12")
    other = Team("Holmen", "Holmen 1", "U10"), Team("Holmen", "Holmen 1", "U12")
    plan = SeasonPlan(tournaments=[
        Tournament(date=date(2026, 11, 1), arena="A", age_group="U10", id="a", teams=[u10, other[0]], host_club="Jar", start_time="09:00"),
        Tournament(date=date(2026, 11, 8), arena="B", age_group="U12", id="b", teams=[u12, other[1]], host_club="Jar", start_time="09:00"),
    ])
    written = write_per_team_workbooks(plan, tmp_path, {"U10": 60, "U12": 60})

    jar = {key: path for key, path in written.items() if key[1] == "Jar 1"}
    assert len(jar) == 2 and len(set(jar.values())) == 2
    for (club, label, age), path in jar.items():
        data = list(openpyxl.load_workbook(path)[SPOND_IMPORT_SHEET].iter_rows(min_row=2, values_only=True))
        assert [row[11] for row in data] == [age]
    assert len(project_spond_import_rows(plan, team=("Jar", "Jar 1", "U10"))) == 1


def test_reexport_removes_stale_team_workbooks_but_keeps_unrelated_files(tmp_path):
    out = tmp_path / "spond"
    out.mkdir()
    (out / "notes.txt").write_text("keep")
    write_per_team_workbooks(_plan(), out, {"U10": 60})
    assert (out / "Jar 1.xlsx").exists()

    write_per_team_workbooks(_plan(cancel_second=True), out, {"U10": 60})

    assert not (out / "Jar 1.xlsx").exists()
    assert (out / "Jar 2.xlsx").exists()
    assert (out / "notes.txt").read_text() == "keep"
    assert not [p for p in out.iterdir() if p.name.startswith(".staging-")]


def test_team_identity_column_is_unique_per_team_even_with_duplicate_labels():
    u10, u12 = Team("Jar", "Jar 1", "U10"), Team("Jar", "Jar 1", "U12")
    plan = SeasonPlan(tournaments=[
        Tournament(date=date(2026, 11, 1), arena="A", age_group="U10", id="a", teams=[u10], host_club="Jar"),
        Tournament(date=date(2026, 11, 8), arena="B", age_group="U12", id="b", teams=[u12], host_club="Jar"),
    ])
    rows = project_spond_import_rows(plan)
    assert [team_identity(r.team_key) for r in rows] == ["Jar · Jar 1 · U10", "Jar · Jar 1 · U12"]

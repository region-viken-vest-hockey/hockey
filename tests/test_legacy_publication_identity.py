"""Legacy published projection recovery must recover stable identity, not merely
produce a same-sized projection.

Regression for the Spond-workbook fallback that bound unmatched historical rows
to whatever canonical tournament happened to be left over in current list order.
Legacy workbook rows are date-sorted while canonical list order is not, so
positional binding silently assigns historical rows to the wrong stable ids.
The fallback is gone: legacy rows must be bound to the canonical plan at the
publication revision and the recovery must fail closed when a row cannot be
uniquely bound.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import openpyxl
import pytest

from tournament_scheduler.infrastructure.canonical_revision_history import (
    load_canonical_plan_at_revision,
    revision_snapshot_path,
)
from tournament_scheduler.pipeline.export_projection_guard import (
    ExportProjectionError,
    diff_tournament_projection,
    projection_from_export_artifacts,
    tournament_projection,
)


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(tid: str, date: str, start: str, arena: str, host: str, age: str, labels: list[str]) -> dict:
    return {
        "id": tid,
        "date": date,
        "start_time": start,
        "arena": arena,
        "host_club": host,
        "age_group": age,
        "teams": [_team(host, label, age) for label in labels],
        "games": [],
    }


def _problem(plan: dict | None = None) -> dict:
    ages = {
        str(t.get("age_group") or "")
        for t in (plan or {}).get("tournaments", [])
        if isinstance(t, dict) and t.get("age_group")
    }
    return {"ice_time_minutes": {age: 120 for age in ages}}


def _historical_plan() -> dict:
    """Publication-time canonical plan, deliberately not in date order."""

    return {
        "tournaments": [
            _tournament("rvv-0001", "2026-10-11", "10:00", "Alpha Arena", "A", "U10", ["A1", "A2"]),
            _tournament("rvv-0002", "2026-10-18", "10:00", "Beta Arena", "B", "U10", ["B1", "B2"]),
            _tournament("rvv-0003", "2026-10-18", "12:00", "Beta Arena", "B", "U12", ["C1", "C2"]),
            _tournament("rvv-0004", "2026-10-17", "09:00", "Gamma Arena", "C", "U12", ["D1", "D2"]),
            _tournament("rvv-0005", "2026-11-15", "11:00", "Delta Arena", "D", "U9", ["E1", "E2"]),
        ]
    }


def _write_spond_workbook(path: Path, tournaments: list[dict]) -> None:
    """Write the legacy Spond workbook exactly as the Spond exporter does.

    The exporter sorts tournaments by date before writing rows.
    """

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sesongplan"
    sheet.append(
        [
            "Dato",
            "Aktivitet",
            "Sted",
            "Start",
            "Slutt",
            "Aldersgruppe",
            "Vertsklubb",
            "Deltakende klubber",
            "Deltakende lag",
            "Import scope",
        ]
    )
    for tournament in sorted(tournaments, key=lambda item: (item["date"], item["start_time"])):
        date = tournament["date"]
        iso = f"{date[8:10]}.{date[5:7]}.{date[0:4]}"
        labels = ", ".join(team["label"] for team in tournament["teams"])
        sheet.append(
            [
                iso,
                f"{tournament['age_group']} Turnering — {tournament['arena']}",
                tournament["arena"],
                tournament["start_time"],
                "",
                tournament["age_group"],
                tournament["host_club"],
                tournament["host_club"],
                labels,
                "turnering",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def _published_projection(tmp_path: Path) -> tuple[dict, dict]:
    historical = _historical_plan()
    export_dir = tmp_path / "export" / "2026-09-21T0908"
    # rvv-0005 existed canonically but was never actually published.
    published_tournaments = [t for t in historical["tournaments"] if t["id"] != "rvv-0005"]
    _write_spond_workbook(export_dir / "season_plan_spond.xlsx", published_tournaments)
    projection = projection_from_export_artifacts(
        export_dir,
        published_canonical_plan=historical,
        published_canonical_problem=_problem(historical),
    )
    return historical, projection


def test_legacy_workbook_rows_bind_to_publication_snapshot_not_row_order(tmp_path: Path) -> None:
    historical, projection = _published_projection(tmp_path)

    # Every published row binds to the stable id it actually came from, even
    # though canonical list order and date order disagree on the two moved rows.
    assert sorted(projection) == ["rvv-0001", "rvv-0002", "rvv-0003", "rvv-0004"]
    assert projection["rvv-0004"]["date"] == "2026-10-17"
    assert projection["rvv-0002"]["date"] == "2026-10-18"
    # The tournament that existed canonically but was never published is absent.
    assert "rvv-0005" not in projection


def test_legacy_delta_reports_stable_ids_for_moves_omissions_and_additions(tmp_path: Path) -> None:
    historical, published = _published_projection(tmp_path)

    current = {
        "tournaments": [
            _tournament("rvv-0001", "2026-10-11", "10:00", "Alpha Arena", "A", "U10", ["A1", "A2"]),
            # Two post-publication moves.
            _tournament("rvv-0002", "2026-10-25", "10:00", "Beta Arena", "B", "U10", ["B1", "B2"]),
            _tournament("rvv-0003", "2026-10-18", "12:00", "Beta Arena", "B", "U12", ["C1", "C2"]),
            _tournament("rvv-0004", "2026-10-24", "09:00", "Gamma Arena", "C", "U12", ["D1", "D2"]),
            # Still canonically present although never published.
            _tournament("rvv-0005", "2026-11-15", "11:00", "Delta Arena", "D", "U9", ["E1", "E2"]),
            # Materialized after publication.
            _tournament("rvv-0006", "2026-12-19", "10:00", "Epsilon Arena", "E", "U9", ["F1", "F2"]),
        ]
    }

    delta = diff_tournament_projection(published, tournament_projection(current, _problem(current)))
    moved_ids = sorted(change["tournament_id"] for change in delta["placement_changes"])
    assert moved_ids == ["rvv-0002", "rvv-0004"]
    assert delta["added_tournament_ids"] == ["rvv-0005", "rvv-0006"]
    assert delta["removed_tournament_ids"] == []


def test_legacy_workbook_binding_fails_closed_without_publication_snapshot(tmp_path: Path) -> None:
    export_dir = tmp_path / "export" / "2026-09-21T0908"
    _write_spond_workbook(export_dir / "season_plan_spond.xlsx", _historical_plan()["tournaments"])

    with pytest.raises(ExportProjectionError) as excinfo:
        projection_from_export_artifacts(export_dir)

    assert "could not be resolved" in excinfo.value.report["summary"]


def test_legacy_workbook_binding_fails_closed_on_ambiguous_row(tmp_path: Path) -> None:
    export_dir = tmp_path / "export" / "2026-09-21T0908"
    _write_spond_workbook(export_dir / "season_plan_spond.xlsx", _historical_plan()["tournaments"])
    # A snapshot with two tournaments sharing the full published identity makes
    # the row->id binding ambiguous; identity must not be guessed.
    duplicate = _tournament(
        "rvv-0001b", "2026-10-11", "10:00", "Alpha Arena", "A", "U10", ["A1", "A2"]
    )
    ambiguous = {"tournaments": [*_historical_plan()["tournaments"], duplicate]}

    with pytest.raises(ExportProjectionError) as excinfo:
        projection_from_export_artifacts(
            export_dir,
            published_canonical_plan=ambiguous,
            published_canonical_problem=_problem(ambiguous),
        )

    report = excinfo.value.report
    assert report["unbound_rows"]
    assert report["unbound_rows"][0]["candidate_count"] == 2


def test_durable_revision_snapshot_resolves_only_matching_revision(tmp_path: Path) -> None:
    season_root = tmp_path / "season"
    revision = "rev-abc"
    historical = _historical_plan()
    payload = {
        "schema_version": 1,
        "season": "2026-2027",
        "revision": revision,
        "plan": historical,
        "verification_context": {"problem": _problem(historical)},
    }
    path = revision_snapshot_path("2026-2027", revision, season_root=season_root)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    resolved = load_canonical_plan_at_revision("2026-2027", revision, season_root=season_root)
    assert resolved == _historical_plan()
    assert load_canonical_plan_at_revision("2026-2027", "other-rev", season_root=season_root) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_committed_history_resolves_canonical_revision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    season_root = repo / "season"
    season_dir = season_root / "2026-2027"
    season_dir.mkdir(parents=True)
    revision = "8f1c3161deadbeef"
    historical = _historical_plan()
    payload = {
        "schema_version": 1,
        "season": "2026-2027",
        "revision": revision,
        "plan": historical,
        "verification_context": {"problem": _problem(historical)},
    }
    (season_dir / "schedule.json").write_text(json.dumps(payload), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=repo,
        check=True,
    )

    resolved = load_canonical_plan_at_revision("2026-2027", revision, season_root=season_root)
    assert resolved == _historical_plan()
    assert load_canonical_plan_at_revision("2026-2027", "does-not-exist", season_root=season_root) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_real_sep_21_baseline_reconstructs_unique_stable_ids() -> None:
    """Cross-check the committed 2026-09-21 publication when available."""

    repo_root = Path(__file__).resolve().parents[1]
    manifest_path = repo_root / "export" / "2026-09-21T0908" / "export_manifest.json"
    if not manifest_path.exists():
        pytest.skip("committed Sep-21 publication is not present")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    revision = str(manifest.get("canonical_revision") or "")
    snapshot = load_canonical_plan_at_revision(
        "2026-2027",
        revision,
        season_root=repo_root / "season",
    )
    if snapshot is None:
        pytest.skip("canonical history for the publication revision is unavailable")

    from tournament_scheduler.infrastructure.canonical_revision_history import load_canonical_schedule_at_revision
    from tournament_scheduler.published_baseline import projection_problem_from_schedule

    schedule_snapshot = load_canonical_schedule_at_revision(
        "2026-2027",
        revision,
        season_root=repo_root / "season",
    )
    published = projection_from_export_artifacts(
        manifest_path.parent,
        published_canonical_plan=snapshot,
        published_canonical_problem=projection_problem_from_schedule(schedule_snapshot),
    )

    assert len(published) == 178
    assert len(set(published)) == 178
    # Canonically present but never actually published. They must not be
    # reconstructed as published rows from leftover list positions.
    assert "rvv-0057" not in published
    assert "rvv-0033" not in published
    # Materialized only after publication.
    assert "rvv-0009" not in published
    assert "rvv-0026" not in published
    assert "rvv-0031" not in published


def test_real_sep_21_baseline_reconciles_with_durable_history() -> None:
    """The real legacy-current migration shape reconciles, or there is drift.

    Skipped when the committed Sep-21 publication or its canonical revision
    history is unavailable (for example a shallow CI checkout).
    """

    from tournament_scheduler.infrastructure.canonical_season_store import (
        load_decisions,
        load_schedule,
    )
    from tournament_scheduler.published_mutation_history import (
        omission_projection,
        reconcile_published_baseline,
    )

    repo_root = Path(__file__).resolve().parents[1]
    manifest_path = repo_root / "export" / "2026-09-21T0908" / "export_manifest.json"
    if not manifest_path.exists():
        pytest.skip("committed Sep-21 publication is not present")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    revision = str(manifest.get("canonical_revision") or "")
    season_root = repo_root / "season"
    publication_plan = load_canonical_plan_at_revision("2026-2027", revision, season_root=season_root)
    if publication_plan is None:
        pytest.skip("canonical history for the publication revision is unavailable")

    from tournament_scheduler.infrastructure.canonical_revision_history import load_canonical_schedule_at_revision
    from tournament_scheduler.published_baseline import projection_problem_from_schedule

    publication_schedule = load_canonical_schedule_at_revision("2026-2027", revision, season_root=season_root)
    published = projection_from_export_artifacts(
        manifest_path.parent,
        published_canonical_plan=publication_plan,
        published_canonical_problem=projection_problem_from_schedule(publication_schedule),
    )
    current_schedule = load_schedule("2026-2027", root=season_root)
    current = tournament_projection(
        current_schedule["plan"],
        projection_problem_from_schedule(current_schedule),
    )
    decisions = load_decisions("2026-2027", root=season_root)

    omissions = omission_projection(
        tournament_projection(publication_plan, projection_problem_from_schedule(publication_schedule)),
        published,
    )
    materializations = {
        tournament_id: current[tournament_id]
        for tournament_id in ("rvv-0009", "rvv-0026", "rvv-0031")
    }
    report = reconcile_published_baseline(
        published_projection=published,
        current_projection=current,
        history=decisions.get("history") or [],
        attested_additions={**omissions, **materializations},
    )
    assert report["ok"] is True, report["unexplained_delta"]
    assert sorted(omissions) == ["rvv-0033", "rvv-0057"]


def test_manual_booking_assertion_history_is_decision_only_for_reconciliation() -> None:
    """A recorded manual booking assertion never moves or roster-changes a
    tournament, so publication reconciliation must treat both
    ``set_manual_booking_assertion`` and ``clear_manual_booking_assertion``
    as decision-only history, not an unreplayable schedule mutation.
    """

    from tournament_scheduler.published_mutation_history import reconcile_published_baseline

    plan = {"tournaments": [_tournament("rvv-0001", "2026-10-17", "14:15", "Tonsberghallen", "Tønsberg", "U12", ["A", "B"])]}
    projection = tournament_projection(plan, _problem(plan))

    history = [
        {
            "event": "set_manual_booking_assertion",
            "tournament_id": "rvv-0001",
            "assertion": {"booking_status": "booked"},
        },
        {
            "event": "clear_manual_booking_assertion",
            "tournament_id": "rvv-0001",
        },
    ]

    report = reconcile_published_baseline(
        published_projection=projection,
        current_projection=projection,
        history=history,
        attested_additions={},
    )
    assert report["ok"] is True, report["unexplained_delta"]

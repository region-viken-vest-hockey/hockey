"""Published-season sealing and sealed-lifecycle regression tests.

Covers the core invariant from the published-season lifecycle work:

    published projection + attested additions + recorded mutations
    = current canonical projection

plus the guards that stop a published season from silently returning to
whole-season replanning/regeneration, the emergency reopen escape hatch, and the
publication-boundary auto-seal hook.
"""

from __future__ import annotations

from pathlib import Path
import copy

import pytest

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.canonical_state import canonical_state_revision, schedule_fingerprint
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)
from tournament_scheduler.pipeline.export_lifecycle import write_draft_manifest
from tournament_scheduler.pipeline.export_projection_guard import tournament_projection
from tournament_scheduler.pipeline.publication_lifecycle import (
    assert_publication_allowed,
    record_publication_seal,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.published_baseline import (
    PublishedBaselineError,
    SeasonSealedError,
    is_published_sealed,
)
from tournament_scheduler.published_mutation_history import (
    reconcile_published_baseline,
)
from tournament_scheduler.application.canonical_season.scoped_mutation import (
    contract_history_details,
    make_scoped_mutation_contract,
)
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export

_tp = tournament_projection


def _team(club: str, label: str, age_group: str = "U10") -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(tid: str, date: str, start: str, arena: str, host: str, age: str = "U10") -> dict:
    return {
        "id": tid,
        "date": date,
        "start_time": start,
        "arena": arena,
        "host_club": host,
        "age_group": age,
        "teams": [_team(host, f"{host}1", age), _team("Guest", f"G-{tid}", age)],
        "games": [],
    }


def _write_canonical(
    root: Path,
    tournaments: list[dict],
    *,
    history: list[dict] | None = None,
    season: str = "2026-2027",
) -> None:
    now = "2026-09-22T00:00:00+00:00"
    plan = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": tournaments}
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": season,
        "created_at": now,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": 1,
        "plan": plan,
    }
    decisions = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": season,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": "tester",
        "decisions": {str(t["id"]): {"status": "pending_review", "placement_locked": False, "participants_locked": False, "approved_fingerprint": None} for t in tournaments},
        "history": list(history or []),
    }
    CanonicalSeasonStore(root).write(
        CanonicalSeasonSnapshot(season=season, schedule=schedule, decisions=decisions)
    )


def _move_event(tournament_id: str, **placement: str) -> dict:
    return {
        "event": "move",
        "tournament_id": tournament_id,
        "at": "2026-09-22T00:00:00+00:00",
        "actor": "tester",
        "details": {"new_placement": dict(placement)},
    }


# ---------------------------------------------------------------------------
# Pure reconciliation invariant
# ---------------------------------------------------------------------------


def test_reconciliation_applies_move_omission_and_materialization() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp(
        {
            "tournaments": [
                _tournament("rvv-1", "2026-10-11", "12:30", "A", "Alpha"),
                _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
                _tournament("rvv-3", "2026-12-19", "12:00", "C", "Gamma"),
            ]
        }
    )
    # rvv-2 existed canonically at publication but was omitted from the artifact;
    # rvv-3 was materialized after publication (attested).
    omission = _tp({"tournaments": [_tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta")]})
    materialization = _tp({"tournaments": [_tournament("rvv-3", "2026-12-19", "12:00", "C", "Gamma")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[_move_event("rvv-1", start_time="12:30")],
        attested_additions={**omission, **materialization},
    )
    assert report["ok"] is True
    assert report["applied_mutation_count"] == 1


def test_reconciliation_fails_closed_on_unexplained_delta() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp(
        {
            "tournaments": [
                _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
                _tournament("rvv-9", "2026-12-19", "12:00", "Z", "Zeta"),
            ]
        }
    )
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[],
        attested_additions={},
    )
    assert report["ok"] is False
    assert report["unexplained_delta"]["added_tournament_ids"] == ["rvv-9"]


def test_reconciliation_detects_unexplained_placement_change() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "13:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[],
        attested_additions={},
    )
    assert report["ok"] is False
    assert report["unexplained_delta"]["placement_changes"][0]["tournament_id"] == "rvv-1"


def test_reconciliation_replays_generic_repair_after_records() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "13:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[
            {
                "event": "repair_option_applied",
                "tournament_id": "rvv-1",
                "details": {
                    "option_id": "repair-1",
                    "after_records": {
                        "rvv-1": {
                            "id": "rvv-1",
                            "date": "2026-10-11",
                            "start_time": "13:00",
                            "arena": "A",
                            "host_club": "Alpha",
                            "age_group": "U10",
                            "participants": baseline["rvv-1"]["participants"],
                        }
                    },
                },
            }
        ],
        attested_additions={},
    )
    assert report["ok"] is True


def test_reconciliation_fails_closed_on_unknown_history_event() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=baseline,
        history=[{"event": "mystery_schedule_mutation"}],
        attested_additions={},
    )
    assert report["ok"] is False
    assert "unknown canonical history event" in report["unexplained_delta"]["replay_error"]


# ---------------------------------------------------------------------------
# Lifecycle sealing and guards
# ---------------------------------------------------------------------------


def _tournaments_abc() -> list[dict]:
    return [
        _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
        _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
        _tournament("rvv-3", "2026-12-19", "12:00", "C", "Gamma"),
    ]


def _seal_abc(root: Path, *, materialize_three: bool = True) -> dict:
    published = _tp({"tournaments": _tournaments_abc()[:1]})
    publication_canonical = _tp({"tournaments": _tournaments_abc()[:2]})
    return CanonicalSeasonService(root=root).seal_published_season(
        season="2026-2027",
        publication_id="2026-09-21T0908",
        canonical_revision="rev-published",
        published_at="2026-09-21T09:14:53+00:00",
        published_projection=published,
        publication_canonical_projection=publication_canonical,
        materializations=(
            [{"tournament_id": "rvv-3", "provenance": "materialized by durable repository history"}]
            if materialize_three
            else []
        ),
        actor="tester",
    )


def test_seal_records_baseline_and_state(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    report = _seal_abc(root)
    assert report["state"] == "published_sealed"
    assert report["reconciliation"]["publication_omissions"] == ["rvv-2"]
    assert report["reconciliation"]["materializations"] == ["rvv-3"]

    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    assert is_published_sealed(snapshot.decisions)
    lifecycle = snapshot.decisions["season_lifecycle"]
    assert lifecycle["published_baseline"]["tournament_count"] == 1
    assert len(lifecycle["publication_history"]) == 1
    # The immutable baseline remains the actual published artifact (1 row).
    assert len(lifecycle["published_baseline"]["tournaments"]) == 1

    status = CanonicalSeasonService(root=root).season_lifecycle_report("2026-2027")
    assert status["state"] == "published_sealed"
    assert status["reconciliation"]["ok"] is True


def test_seal_fails_closed_on_unsupported_materialization(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    published = _tp({"tournaments": _tournaments_abc()[:1]})
    publication_canonical = _tp({"tournaments": _tournaments_abc()[:2]})
    with pytest.raises(PublishedBaselineError):
        CanonicalSeasonService(root=root).seal_published_season(
            season="2026-2027",
            publication_id="2026-09-21T0908",
            canonical_revision="rev-published",
            published_at="2026-09-21T09:14:53+00:00",
            published_projection=published,
            publication_canonical_projection=publication_canonical,
            # rvv-3 is not attested -> unexplained added tournament.
            materializations=[],
        )


def test_sealed_season_guards_global_regeneration(tmp_path: Path) -> None:
    from tournament_scheduler.canonical_replan import replan_around_baseline
    from tournament_scheduler.season_state import apply_candidate, normalize_placements

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)

    with pytest.raises(SeasonSealedError):
        apply_candidate(season="2026-2027", candidate={"tournaments": []}, root=root)
    with pytest.raises(SeasonSealedError):
        apply_candidate(
            season="2026-2027",
            candidate={"tournaments": []},
            root=root,
            operation="targeted_mutation",
        )
    with pytest.raises(SeasonSealedError):
        normalize_placements(season="2026-2027", root=root, dry_run=False)
    with pytest.raises(SeasonSealedError):
        replan_around_baseline(
            season="2026-2027",
            config={},
            scraping_result=None,
            start_date=__import__("datetime").date(2026, 9, 1),
            end_date=__import__("datetime").date(2027, 4, 30),
            root=root,
        )

    # Targeted, decision-only maintenance still works.
    from tournament_scheduler.season_state import approve_tournament

    approve_tournament(season="2026-2027", tournament_id="rvv-1", root=root, actor="tester")
    status = CanonicalSeasonService(root=root).season_lifecycle_report("2026-2027")
    assert status["reconciliation"]["ok"] is True


def test_sealed_season_allows_scoped_repair_with_replayable_history(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    candidate = copy.deepcopy(snapshot.schedule["plan"])
    candidate["tournaments"][0]["start_time"] = "11:30"
    contract = make_scoped_mutation_contract(
        schedule=snapshot.schedule,
        decisions=snapshot.decisions,
        candidate=candidate,
        affected_tournament_ids=["rvv-1"],
    )

    service.apply_candidate(
        season="2026-2027",
        candidate=candidate,
        actor="tester",
        operation="targeted_repair",
        _targeted_contract=contract,
        _history_event={
            "event": "repair_option_applied",
            "tournament_id": "rvv-1",
            "details": {
                "option_id": "repair-1",
                "finding_id": "finding-1",
                "changed_tournament_ids": ["rvv-1"],
                **contract_history_details(contract),
            },
        },
    )

    status = service.season_lifecycle_report("2026-2027")
    assert status["reconciliation"]["ok"] is True
    latest = service.load("2026-2027")
    assert latest.schedule["plan"]["tournaments"][1:] == snapshot.schedule["plan"]["tournaments"][1:]
    assert latest.decisions["history"][-1]["event"] == "repair_option_applied"


def test_sealed_scoped_mutation_enforces_expected_revision_and_scope(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    candidate = copy.deepcopy(snapshot.schedule["plan"])
    candidate["tournaments"][0]["start_time"] = "11:30"
    contract = make_scoped_mutation_contract(
        schedule=snapshot.schedule,
        decisions=snapshot.decisions,
        candidate=candidate,
        affected_tournament_ids=["rvv-1"],
    )
    forged_candidate = copy.deepcopy(candidate)
    forged_candidate["tournaments"][1]["start_time"] = "11:45"

    with pytest.raises(Exception, match="outside its declared scope"):
        service.apply_candidate(
            season="2026-2027",
            candidate=forged_candidate,
            operation="targeted_repair",
            _targeted_contract=contract,
        )

    service.approve_tournament(season="2026-2027", tournament_id="rvv-1", actor="tester")
    with pytest.raises(Exception, match="expected canonical revision"):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            operation="targeted_repair",
            _targeted_contract=contract,
        )


def test_sealed_move_reconciles_immediately(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    service.move_tournament(
        season="2026-2027",
        tournament_id="rvv-1",
        start_time="11:00",
        actor="tester",
    )
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True


def test_sealed_roster_swap_reconciles_immediately(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    before = service.load("2026-2027").schedule["plan"]

    service.swap_participants(
        season="2026-2027",
        tournament_a_id="rvv-1",
        team_a_label="G-rvv-1",
        tournament_b_id="rvv-2",
        team_b_label="G-rvv-2",
        actor="tester",
    )

    latest = service.load("2026-2027")
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True
    assert latest.schedule["plan"]["tournaments"][2] == before["tournaments"][2]
    assert latest.decisions["history"][-1]["event"] == "participant_swap"


def test_sealed_scoped_batch_reconciles_immediately(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    service.batch_maintenance(
        season="2026-2027",
        operations=[{"op": "move", "tournament_id": "rvv-1", "start_time": "11:00"}],
        scope=["rvv-1"],
        request_id="request-1",
        actor="tester",
    )
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True
    assert service.load("2026-2027").decisions["history"][-1]["event"] == "batch_maintenance"


def test_normalize_placements_dry_run_is_diagnostic_on_sealed_season(tmp_path: Path) -> None:
    from tournament_scheduler.season_state import normalize_placements

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    schedule, _decisions = normalize_placements(season="2026-2027", root=root, dry_run=True)
    assert schedule is not None


def test_reopen_planning_requires_confirmation_and_reason(tmp_path: Path) -> None:
    from tournament_scheduler.season_state import apply_candidate, reopen_planning

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)

    with pytest.raises(Exception):
        reopen_planning(season="2026-2027", root=root, reason="", confirm_break_published_baseline=True)
    with pytest.raises(Exception):
        reopen_planning(season="2026-2027", root=root, reason="restructure", confirm_break_published_baseline=False)

    report = reopen_planning(
        season="2026-2027",
        root=root,
        reason="fundamental restructuring",
        confirm_break_published_baseline=True,
        actor="operator",
    )
    assert report["state"] == "promoted"
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    assert not is_published_sealed(snapshot.decisions)
    # The prior baseline remains auditable.
    assert snapshot.decisions["season_lifecycle"]["reopen_history"][-1]["reason"] == "fundamental restructuring"
    # Planning regeneration is possible again.
    apply_candidate(season="2026-2027", candidate={"tournaments": []}, root=root)


# ---------------------------------------------------------------------------
# Publication-boundary auto-seal
# ---------------------------------------------------------------------------


def _first_publication_export(
    tmp_path: Path,
    root: Path,
    *,
    drift: bool = False,
    projection: dict | None = None,
    canonical_revision: str | None = None,
    season: str = "2026-2027",
) -> Path:
    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    exported_projection = projection if projection is not None else _tp({"tournaments": _tournaments_abc()})
    if drift:
        exported_projection = _tp(
            {"tournaments": [_tournament("rvv-1", "2026-10-11", "23:00", "A", "Alpha")]}
        )
    if canonical_revision is None:
        snapshot = CanonicalSeasonStore(root).load(season)
        canonical_revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season=season,
        canonical_revision=canonical_revision,
        schedule_projection=exported_projection,
    )
    return export_dir


def test_record_publication_seal_seals_season(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _first_publication_export(tmp_path, root)

    report = record_publication_seal(export_dir, repo_dir=tmp_path)
    assert report is not None
    assert report["state"] == "published_sealed"
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    assert is_published_sealed(snapshot.decisions)


def test_publication_refuses_manifest_season_without_canonical_state(tmp_path: Path) -> None:
    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision="rev-missing",
        schedule_projection=_tp({"tournaments": _tournaments_abc()}),
    )

    with pytest.raises(RuntimeError, match="cannot be loaded"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_publication_refuses_missing_or_malformed_schedule_projection(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    current_revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=current_revision,
        schedule_projection={},
    )
    with pytest.raises(RuntimeError, match="schedule_projection"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)

    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=current_revision,
        schedule_projection={"rvv-1": {"id": "rvv-1"}},
    )
    with pytest.raises(RuntimeError, match="malformed"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_publication_refuses_stale_canonical_revision_before_push(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _first_publication_export(tmp_path, root, canonical_revision="stale-revision")

    with pytest.raises(RuntimeError, match="current canonical revision"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_record_publication_seal_propagates_persistence_failure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _first_publication_export(tmp_path, root)

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(CanonicalSeasonStore, "write", fail_write)
    with pytest.raises(RuntimeError, match="disk full"):
        record_publication_seal(export_dir, repo_dir=tmp_path)


def test_publication_refused_when_export_no_longer_matches_canonical(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    export_dir = _first_publication_export(tmp_path, root, drift=True)
    with pytest.raises(RuntimeError):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_second_publication_appends_history_without_rewriting_first(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    export_dir = _first_publication_export(tmp_path, root)
    record_publication_seal(export_dir, repo_dir=tmp_path)

    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    lifecycle = snapshot.decisions["season_lifecycle"]
    assert lifecycle["state"] == "published_sealed"
    assert len(lifecycle["publication_history"]) == 2
    # The historical Sep-21 baseline is still the first entry, unchanged.
    assert lifecycle["publication_history"][0]["publication_id"] == "2026-09-21T0908"
    assert lifecycle["published_baseline"]["publication_id"] == "2026-09-28T0908"


def test_full_run_refused_for_sealed_season(tmp_path: Path) -> None:
    from datetime import date

    from tournament_scheduler.cli.pipeline_orchestrator.new_run_guard import guard_sealed_season_run

    sealed_root = tmp_path / "season"
    _write_canonical(sealed_root, _tournaments_abc())
    _seal_abc(sealed_root)
    sealed_cfg = {"canonical_season": "2026-2027", "canonical_season_root": str(sealed_root)}
    assert guard_sealed_season_run(sealed_cfg, date(2026, 9, 1), date(2027, 4, 30)) is True

    open_root = tmp_path / "season-open"
    _write_canonical(open_root, _tournaments_abc())
    open_cfg = {"canonical_season": "2026-2027", "canonical_season_root": str(open_root)}
    assert guard_sealed_season_run(open_cfg, date(2026, 9, 1), date(2027, 4, 30)) is False


# ---------------------------------------------------------------------------
# Promote --force guard (integration through the real promotion path)
# ---------------------------------------------------------------------------


def _candidate() -> dict:
    teams = [
        {"club": "A", "label": "A1", "age_group": "U10"},
        {"club": "B", "label": "B1", "age_group": "U10"},
        {"club": "C", "label": "C1", "age_group": "U10"},
        {"club": "D", "label": "D1", "age_group": "U10"},
    ]
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            {
                "id": "u10-a-20260912",
                "date": "2026-09-12",
                "arena": "Arena A",
                "age_group": "U10",
                "host_club": "A",
                "teams": teams,
                "games": [
                    {"home": "A1", "away": "B1", "parallel_slot": 0, "round_number": 1},
                    {"home": "C1", "away": "D1", "parallel_slot": 1, "round_number": 1},
                    {"home": "A1", "away": "C1", "parallel_slot": 0, "round_number": 2},
                    {"home": "B1", "away": "D1", "parallel_slot": 1, "round_number": 2},
                    {"home": "A1", "away": "D1", "parallel_slot": 0, "round_number": 3},
                    {"home": "B1", "away": "C1", "parallel_slot": 1, "round_number": 3},
                ],
                "start_time": "10:00",
            }
        ],
    }


def test_promote_force_cannot_replace_sealed_season(tmp_path: Path) -> None:
    from tournament_scheduler.season_state import promote_from_stage3, seal_published_season

    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _candidate()}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)

    schedule, _decisions = promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    seal_published_season(
        season=schedule["season"],
        publication_id="2026-09-28T0908",
        canonical_revision=str(schedule.get("revision")),
        published_at="2026-09-28T09:14:53+00:00",
        published_projection=tournament_projection(schedule.get("plan") or {}),
        root=root,
        actor="tester",
    )
    with pytest.raises(SeasonSealedError):
        promote_from_stage3(work_dir=work_dir, root=root, actor="tester", force=True)

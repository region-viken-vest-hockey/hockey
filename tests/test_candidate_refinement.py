"""Refinement of a finalized, unpromoted Stage 3/Stage 4 candidate.

An interactive run can review/export a hard-valid candidate and then discover a
localized defect during the semantic audit, before promotion. These tests
prove the normal refinement path: the exact reviewed candidate is reopened, a
repository-owned repair produces a new verified revision, the reviewed
candidate stays the baseline, and Stage 4 re-exports with provenance to the
superseded export -- all without promotion, a Stage 3 reset or a Stage 1/2
rerun.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tournament_scheduler.application.candidate_refinement import (
    RefinementError,
    export_is_pending,
    load_finalized_candidate,
    materialize_review_export,
    refinement_findings,
    refinement_options,
    refine_finalized_candidate,
)
from tournament_scheduler.application.stage3_session import (
    STATUS_FINALIZED,
    STATUS_REFINING,
    TRANSITION_REFINE_CANDIDATE,
    Stage3Session,
)
from tournament_scheduler.application.stage3_session_store import (
    Stage3SessionStore,
    finalize_stage3_plan,
)
from tournament_scheduler.pipeline.export_lifecycle import (
    PUBLISHED_STATUS,
    SUPERSEDED_STATUS,
    read_export_manifest,
    write_draft_manifest,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.planning_contract import build_planning_problem

FINDING = "unplaced_placement:U10:2026-10-10:1"


# ---------------------------------------------------------------------------
# Fixtures (synthetic; no planner/optimizer)
# ---------------------------------------------------------------------------


def _teams(clubs: Iterable[str], age_group: str = "U10") -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{club} {index}", "age_group": age_group}
        for club in clubs
        for index in (1, 2)
    ]


def _problem(
    teams: List[Dict[str, str]], *, start: date, end: date
) -> Dict[str, Any]:
    clubs = sorted({team["club"] for team in teams})
    config = {
        "teams": teams,
        "age_groups": sorted({team["age_group"] for team in teams}),
        "parallel_games": {"U10": 2, "U12": 2},
        "round_length_minutes": {"U10": 30, "U12": 30},
        "ice_time_minutes": {"U10": 120, "U12": 90},
        "rounds_per_tournament": {"U10": 3, "U12": 3},
    }
    problem = build_planning_problem(config, None, start, end)
    problem["clubs"] = {club: f"{club} Arena" for club in clubs}
    problem["club_calendar_status"] = {club: "known" for club in clubs}
    return problem


def _obligation(host: str, roster: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "id": FINDING,
        "age_group": "U10",
        "date": "2026-10-10",
        "period": "before_christmas",
        "responsible_host": host,
        "participant_teams": [dict(team) for team in roster],
        "participant_team_count": len(roster),
        "required_duration_minutes": 90,
        "category": "manual_tournament_placement",
        "search_attempted": True,
        "bounded_repair_exhausted": True,
        "reason": "no_participant_host_slot",
        "source_tournament_id": "rvv-9001",
    }


def _reviewed_plan(roster: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "tournaments": [],
        "unresolved_tournament_placements": [_obligation("Sorby", roster)],
    }


def _seed_finalized_run(work_dir: Path, plan: Dict[str, Any], run_id: str = "run-1") -> None:
    state = PipelineState(str(work_dir))
    RunManifest(str(work_dir)).start_run("objective", run_id=run_id)
    checkpoint = {"plan": plan, "source": "reviewed"}
    state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
    finalize_stage3_plan(
        str(work_dir), checkpoint, action_id="apply_candidate", rationale="reviewed", run_id=run_id
    )


def _seed_prior_export(work_dir: Path, *, status: Optional[str] = None) -> Path:
    prior_dir = work_dir / "export" / "2026-09-30T1200"
    prior_dir.mkdir(parents=True, exist_ok=True)
    write_draft_manifest(
        prior_dir,
        export_id=prior_dir.name,
        generated_at="2026-09-30T12:00:00+00:00",
        export_fingerprint="fp-prior",
        source_run_id="run-1",
    )
    if status == PUBLISHED_STATUS:
        manifest = read_export_manifest(prior_dir)
        manifest["lifecycle_status"] = PUBLISHED_STATUS
        (prior_dir / "export_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    PipelineState(str(work_dir)).write_stage(
        StageName.EXPORT,
        {"export_dir": str(prior_dir), "export_fingerprint": "fp-prior"},
        status=StageStatus.DONE,
    )
    return prior_dir


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_begin_refinement_reopens_reviewed_candidate_as_baseline() -> None:
    session = Stage3Session(run_id="run-1", candidate={"plan": {"tournaments": [{"id": "a"}]}})
    session.candidate_fingerprint = "fp-reviewed"
    session.candidate_revision = 4
    session.finalize(
        transition="apply_candidate", action_id="apply_candidate", rationale="reviewed", at="T"
    )
    assert session.status == STATUS_FINALIZED

    session.begin_refinement(
        export_provenance={"export_id": "2026-09-30T1200"},
        rationale="refine due to audit finding",
        at="T2",
    )

    assert session.status == STATUS_REFINING
    assert session.is_finalized() is False
    assert session.finalized_fingerprint is None
    assert session.baseline_fingerprint == "fp-reviewed"
    assert session.baseline_revision == 4
    assert session.refinement is not None
    assert session.refinement["origin_finalized_fingerprint"] == "fp-reviewed"
    assert TRANSITION_REFINE_CANDIDATE in [entry["transition"] for entry in session.decision_history]


def test_begin_refinement_requires_a_finalized_session() -> None:
    session = Stage3Session(run_id="run-1")
    try:
        session.begin_refinement(export_provenance=None, rationale="x", at="T")
    except ValueError as exc:
        assert "finalized" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("begin_refinement accepted a non-finalized session")


# ---------------------------------------------------------------------------
# Finding-directed refinement
# ---------------------------------------------------------------------------


def _refinement_fixture(tmp_path: Path) -> tuple[Path, Dict[str, Any], Dict[str, Any]]:
    teams = _teams(["Nordby", "Sorby"])
    plan = _reviewed_plan(teams)
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    _seed_finalized_run(tmp_path, plan)
    return tmp_path, plan, problem


def test_finalized_candidate_exposes_findings_and_options_without_promotion(tmp_path: Path) -> None:
    work_dir, plan, problem = _refinement_fixture(tmp_path)

    findings = refinement_findings(plan, problem)
    assert [finding["finding_id"] for finding in findings] == [FINDING]

    report = refinement_options(plan, problem, FINDING)
    assert report["option_count"] >= 1
    option = report["options"][0]
    assert option["family"] == "unplaced_placement"
    assert option["evidence"]["responsible_host"] == "Sorby"


def test_refine_applies_verified_repair_and_creates_new_revision(tmp_path: Path) -> None:
    work_dir, plan, problem = _refinement_fixture(tmp_path)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]

    result = refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id=option_id,
        finding_id=FINDING,
        export=False,
        run_id="run-1",
    )

    assert result["ok"] is True, result
    assert result["session_revision_after"] == result["session_revision_before"] + 1
    assert result["candidate_fingerprint_before"] != result["candidate_fingerprint_after"]
    assert result["delta"]["unresolved_placement_obligations_after"] == 0

    session = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert session.is_finalized() is True
    assert session.finalized_fingerprint == result["candidate_fingerprint_after"]
    assert session.refinement is not None
    assert session.refinement["origin_finalized_fingerprint"] == result["candidate_fingerprint_before"]
    # The reviewed candidate is retained as the baseline keep_baseline restores.
    assert session.baseline_fingerprint == result["candidate_fingerprint_before"]

    from tournament_scheduler.application.stage3_session_store import extract_candidate_body

    persisted = extract_candidate_body(
        PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
    )
    assert persisted["unresolved_tournament_placements"] == []
    assert len(persisted["tournaments"]) == 1
    assert persisted["tournaments"][0]["host_club"] == "Sorby"


def test_refine_rejects_stale_or_unknown_option_without_mutating(tmp_path: Path) -> None:
    work_dir, plan, problem = _refinement_fixture(tmp_path)
    before = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")

    result = refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id="does-not-exist",
        finding_id=FINDING,
        export=False,
        run_id="run-1",
    )

    assert result["ok"] is False
    assert result["reason"] == "unknown_or_stale_option"
    after = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert after.is_finalized() is True
    assert after.finalized_fingerprint == before.finalized_fingerprint


def test_refine_requires_finalized_session(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    plan = _reviewed_plan(teams)
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    PipelineState(str(tmp_path)).write_stage(
        StageName.PLANNING, {"plan": plan}, status=StageStatus.DONE
    )
    try:
        load_finalized_candidate(str(tmp_path))
    except RefinementError as exc:
        assert "finalized" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("load_finalized_candidate accepted a non-finalized session")


# ---------------------------------------------------------------------------
# Export provenance
# ---------------------------------------------------------------------------


def test_refine_without_export_marks_one_review_handoff_pending(tmp_path: Path) -> None:
    work_dir, plan, problem = _refinement_fixture(tmp_path)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]

    result = refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id=option_id,
        finding_id=FINDING,
        export=False,
        run_id="run-1",
    )

    assert result["ok"] is True
    assert result["export_required"] is True
    session = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert session.is_finalized() is True
    assert session.export_pending is True
    assert export_is_pending(str(work_dir), run_id="run-1") is True


def test_materialize_review_export_produces_one_fingerprint_bound_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    from tournament_scheduler.pipeline import stage4_export as stage4_module

    work_dir, plan, problem = _refinement_fixture(tmp_path)
    prior_dir = _seed_prior_export(work_dir)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]

    # Batched convergence commits the verified revision without exporting.
    committed = refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id=option_id,
        finding_id=FINDING,
        export=False,
        run_id="run-1",
    )
    assert committed["ok"] is True
    assert export_is_pending(str(work_dir), run_id="run-1") is True

    def _fake_stage4(plan_checkpoint, state, **kwargs):
        new_dir = Path(kwargs["export_dir"]) / "2026-10-01T1200"
        new_dir.mkdir(parents=True, exist_ok=True)
        manifest = write_draft_manifest(
            new_dir,
            export_id=new_dir.name,
            generated_at="2026-10-01T12:00:00+00:00",
            export_fingerprint="fp-batch",
            source_run_id="run-1",
            supersedes=kwargs.get("supersedes"),
        )
        return {
            "export_dir": str(new_dir),
            "export_fingerprint": "fp-batch",
            "export_lifecycle": manifest,
            "output_files": {},
        }

    monkeypatch.setattr(stage4_module, "run", _fake_stage4)

    result = materialize_review_export(work_dir, problem=problem, run_id="run-1")

    assert result["ok"] is True, result
    assert result["export_fingerprint"] == "fp-batch"
    assert result["export_required"] is False
    # The candidate is still the exact verified revision that was committed.
    session = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert session.finalized_fingerprint == committed["candidate_fingerprint_after"]
    assert session.export_pending is False
    assert export_is_pending(str(work_dir), run_id="run-1") is False
    # The prior reviewed export is superseded exactly once, by the new handoff.
    prior_manifest = read_export_manifest(prior_dir)
    assert prior_manifest["lifecycle_status"] == SUPERSEDED_STATUS
    assert prior_manifest["superseded_by"]["export_fingerprint"] == "fp-batch"
    assert read_export_manifest(result["export_dir"])["supersedes"]["export_id"] == prior_dir.name


def test_export_is_pending_reconciles_with_an_already_materialized_export(tmp_path: Path) -> None:
    from tournament_scheduler.application.stage3_session_store import extract_candidate_body
    from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

    work_dir, plan, problem = _refinement_fixture(tmp_path)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]
    refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id=option_id,
        finding_id=FINDING,
        export=False,
        run_id="run-1",
    )
    # Simulate a crash after Stage 4 wrote the new export but before the session
    # cleared ``export_pending``: the owed handoff already exists on disk.
    body = extract_candidate_body(
        PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
    )
    export_dir = work_dir / "export" / "2026-10-01T1200"
    export_dir.mkdir(parents=True)
    PipelineState(str(work_dir)).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "export_fingerprint": stable_payload_sha256(body.get("tournaments", [])),
        },
        status=StageStatus.DONE,
    )
    store = Stage3SessionStore(str(work_dir))
    session = store.load(expected_run_id="run-1")
    session.export_pending = True
    store.save(session)

    assert export_is_pending(str(work_dir), run_id="run-1") is False


def test_refine_reexport_links_new_export_to_superseded_prior(tmp_path: Path, monkeypatch) -> None:
    from tournament_scheduler.pipeline import stage4_export as stage4_module

    work_dir, plan, problem = _refinement_fixture(tmp_path)
    prior_dir = _seed_prior_export(work_dir)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]

    def _fake_stage4(plan_checkpoint, state, **kwargs):
        new_dir = Path(kwargs["export_dir"]) / "2026-10-01T1200"
        new_dir.mkdir(parents=True, exist_ok=True)
        manifest = write_draft_manifest(
            new_dir,
            export_id=new_dir.name,
            generated_at="2026-10-01T12:00:00+00:00",
            export_fingerprint="fp-new",
            source_run_id="run-1",
            supersedes=kwargs.get("supersedes"),
        )
        return {
            "export_dir": str(new_dir),
            "export_fingerprint": "fp-new",
            "export_lifecycle": manifest,
            "output_files": {},
        }

    monkeypatch.setattr(stage4_module, "run", _fake_stage4)

    result = refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id=option_id,
        finding_id=FINDING,
        export=True,
        run_id="run-1",
    )

    assert result["ok"] is True, result
    assert result["export_fingerprint"] == "fp-new"

    new_manifest = read_export_manifest(result["export_dir"])
    assert new_manifest["supersedes"]["export_id"] == "2026-09-30T1200"
    assert new_manifest["supersedes"]["export_fingerprint"] == "fp-prior"

    prior_manifest = read_export_manifest(prior_dir)
    assert prior_manifest["lifecycle_status"] == SUPERSEDED_STATUS
    assert prior_manifest["superseded_by"]["export_fingerprint"] == "fp-new"

    session = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert session.refinement["result_export"]["export_fingerprint"] == "fp-new"


def test_refine_refuses_to_supersede_a_published_export(tmp_path: Path, monkeypatch) -> None:
    from tournament_scheduler.pipeline import stage4_export as stage4_module

    work_dir, plan, problem = _refinement_fixture(tmp_path)
    _seed_prior_export(work_dir, status=PUBLISHED_STATUS)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]

    called = {"export": False}

    def _fake_stage4(*args, **kwargs):  # pragma: no cover - must not run
        called["export"] = True
        return {}

    monkeypatch.setattr(stage4_module, "run", _fake_stage4)

    try:
        refine_finalized_candidate(
            work_dir,
            problem=problem,
            option_id=option_id,
            finding_id=FINDING,
            export=True,
            run_id="run-1",
        )
    except RefinementError as exc:
        assert "published" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("refinement superseded a published export")
    assert called["export"] is False


def test_superseded_exports_are_protected_from_draft_retention(tmp_path: Path) -> None:
    from tournament_scheduler.pipeline.export_lifecycle import prune_draft_exports

    root = tmp_path / "export"
    superseded = root / "2026-09-30T1200"
    superseded.mkdir(parents=True)
    write_draft_manifest(
        superseded,
        export_id=superseded.name,
        generated_at="2026-09-30T12:00:00+00:00",
        export_fingerprint="fp",
        source_run_id="run-1",
    )
    manifest = read_export_manifest(superseded)
    manifest["lifecycle_status"] = SUPERSEDED_STATUS
    (superseded / "export_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    newer = root / "2026-10-01T1200"
    newer.mkdir(parents=True)
    write_draft_manifest(
        newer,
        export_id=newer.name,
        generated_at="2026-10-01T12:00:00+00:00",
        export_fingerprint="fp2",
        source_run_id="run-1",
    )

    removed = prune_draft_exports(sorted(root.iterdir()), keep=1)
    assert removed == []
    assert superseded.exists()


def test_refine_dry_run_does_not_mutate_session_or_checkpoint(tmp_path: Path) -> None:
    work_dir, plan, problem = _refinement_fixture(tmp_path)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]
    before = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")

    result = refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id=option_id,
        finding_id=FINDING,
        dry_run=True,
        export=False,
        run_id="run-1",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["candidate_fingerprint_before"] != result["candidate_fingerprint_after"]
    after = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert after.finalized_fingerprint == before.finalized_fingerprint
    assert after.is_finalized() is True


def test_refine_export_failure_keeps_committed_candidate_and_prior_export(
    tmp_path: Path, monkeypatch
) -> None:
    from tournament_scheduler.pipeline import stage4_export as stage4_module

    work_dir, plan, problem = _refinement_fixture(tmp_path)
    prior_dir = _seed_prior_export(work_dir)
    report = refinement_options(plan, problem, FINDING)
    option_id = report["options"][0]["option_id"]

    def _boom(*args, **kwargs):
        raise RuntimeError("export backend exploded")

    monkeypatch.setattr(stage4_module, "run", _boom)

    result = refine_finalized_candidate(
        work_dir,
        problem=problem,
        option_id=option_id,
        finding_id=FINDING,
        export=True,
        run_id="run-1",
    )

    assert result["ok"] is False
    assert result["reason"] == "export_failed"
    assert result["candidate_committed"] is True
    # The reviewed export stays current; it is not superseded by a failed export.
    prior_manifest = read_export_manifest(prior_dir)
    assert prior_manifest["lifecycle_status"] != SUPERSEDED_STATUS
    # The refined revision is still durable and re-exportable.
    session = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert session.is_finalized() is True
    assert session.finalized_fingerprint == result["candidate_fingerprint_after"]


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _args(work_dir: Path, **overrides: Any) -> argparse.Namespace:
    base = dict(
        work_dir=str(work_dir),
        input=None,
        finding=FINDING,
        option_id=None,
        search=False,
        dry_run=False,
        no_export=False,
        export_dir=None,
        flat_export=False,
        non_strict=False,
        rationale="",
        json=True,
        stage3_command="refine",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_refine_subcommand_is_registered() -> None:
    from tournament_scheduler.cli.args import build_parser

    args = build_parser().parse_args(["stage3", "refine", "--finding", "f", "--json"])
    assert args.command == "stage3"
    assert args.stage3_command == "refine"
    assert args.finding == "f"


def test_refine_cli_lists_findings_and_options(tmp_path: Path, capsys, monkeypatch) -> None:
    from tournament_scheduler.cli.pipeline_orchestrator import stage3_refine_command as cmd

    work_dir, plan, problem = _refinement_fixture(tmp_path)
    monkeypatch.setattr(cmd, "_refinement_problem", lambda state, args: ({}, {}, None, None, problem))

    rc = cmd._cmd_stage3_refine(_args(work_dir, finding=None))
    assert rc == 0
    findings_payload = json.loads(capsys.readouterr().out)
    assert findings_payload["finding_count"] == 1

    rc = cmd._cmd_stage3_refine(_args(work_dir))
    assert rc == 0
    options_payload = json.loads(capsys.readouterr().out)
    assert options_payload["option_count"] >= 1

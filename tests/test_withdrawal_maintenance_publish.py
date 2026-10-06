"""Published-season withdrawal maintenance must stay publishable.

Regression for issue #624 follow-up: retiring a team (cancel its home
tournaments, replace it in away tournaments) on an already `published_sealed`
season produces a canonical-season export (`season export`) that is verified
and bound entirely against canonical `season/<season>/` state. That export
must never be gated by the original Stage 1-4 *planning* pipeline's own
`input.xlsx` staleness cascade: an operator correcting the registration
workbook days or weeks earlier (long after the season was first built) must
not block publishing today's unrelated canonical maintenance.
"""

from __future__ import annotations

import subprocess

from tournament_scheduler.pipeline.audit_result import write_audit_result
from tournament_scheduler.pipeline.export_lifecycle import write_draft_manifest
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.pipeline.operator_action import DEFAULT_REGISTRY
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.pipeline.verification_context import (
    build_verification_context,
    resolve_publish_verification_context,
)
from tournament_scheduler.planning_contract import extract_candidate, verify_candidate


def _team(club: str, label: str, age: str) -> dict:
    return {"club": club, "label": label, "age_group": age}


def _round_robin_games(labels: list[str]) -> list[dict]:
    pairs = [
        (home, away)
        for index, home in enumerate(labels)
        for away in labels[index + 1 :]
    ]
    return [
        {"home": home, "away": away, "parallel_slot": 0, "round_number": i + 1}
        for i, (home, away) in enumerate(pairs)
    ]


def _withdrawal_maintenance_plan() -> dict:
    """One cancelled home tournament + one away tournament with a replacement.

    Mirrors the real retire-team shape: the withdrawn team's own home
    tournament is cancelled (games cleared) with ``cancellation_reason:
    team_retirement``, and a separate, unrelated host's away tournament has
    the withdrawn team's roster slot filled by a same-age replacement team
    instead.
    """

    home_tournament = {
        "id": "rvv-home-1",
        "date": "2026-11-22",
        "arena": "Kongsberghallen",
        "age_group": "JU12",
        "host_club": "Kongsberg",
        "teams": [],
        "games": [],
        "cancelled": True,
        "cancellation_reason": "team_retirement",
    }
    away_teams = [
        _team("Skien", "Skien", "JU12"),
        _team("Ringerike", "Ringerike 1", "JU12"),
        _team("Frisk Asker", "Frisk Asker Svart", "JU12"),
    ]
    away_tournament = {
        "id": "rvv-away-1",
        "date": "2026-12-12",
        "arena": "Skien ishall",
        "age_group": "JU12",
        "host_club": "Skien",
        "teams": away_teams,
        "games": _round_robin_games([team["label"] for team in away_teams]),
    }
    return {
        "schema_version": 1,
        "source": {"planner": "test"},
        "tournaments": [home_tournament, away_tournament],
    }


def _init_repo(repo_dir) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    (repo_dir / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)


def _write_canonical_season_export(work_dir, *, plan: dict, problem: dict, canonical_revision: str) -> dict:
    """Write a `season export`-shaped checkpoint, exactly as its CLI command does.

    Sets ``is_canonical_season_export``/``stale_export``/``canonical_revision``
    the same way ``_cmd_season``'s export handler does, so this reproduces the
    real artifact a canonical-season maintenance export produces -- not a
    hand-simplified stand-in.
    """

    candidate = extract_candidate({"plan": plan})
    verify_result = verify_candidate(candidate, problem)
    assert verify_result["ok"], verify_result["violations"]
    fingerprint = stable_payload_sha256(candidate.get("tournaments", []))
    context = build_verification_context(
        run_id=RunManifest(work_dir).read().get("run_id"),
        candidate=candidate,
        problem=problem,
        verify_result=verify_result,
    )
    export_dir = work_dir / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "season_plan.html").write_text("<h1>plan</h1>", encoding="utf-8")
    data = {
        "output_files": {"html": str(export_dir / "season_plan.html")},
        "export_fingerprint": fingerprint,
        "verify_result": verify_result,
        "verification_context": context,
        "reviewed_plan": dict(candidate),
        "canonical_season": "2026-2027",
        "canonical_revision": canonical_revision,
        "is_canonical_season_export": True,
        "stale_export": False,
        "errors": [],
    }
    PipelineState(work_dir).write_stage(StageName.EXPORT, data, status=StageStatus.DONE)
    write_draft_manifest(
        export_dir,
        export_id="test-export",
        generated_at="2026-01-01T00:00:00+00:00",
        export_fingerprint=fingerprint,
        source_run_id=context.get("run_id"),
        canonical_season="2026-2027",
        canonical_revision=canonical_revision,
        schedule_projection={"rvv-home-1": {}, "rvv-away-1": {}},
    )
    return data


def _write_pass_audit(work_dir) -> None:
    checkpoint = PipelineState(work_dir).read_stage(StageName.EXPORT)
    payload = {
        "schema_version": 1,
        "audit_id": "audit-withdrawal-maintenance",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "run_id": "",
        "export_fingerprint": checkpoint.get("export_fingerprint"),
        "source_fingerprints": {},
        "prompt_version": 1,
        "runbook_version": "v1",
        "backend": "test",
        "execution_mode": "headless",
        "status": "PASS",
        "checklist_findings": [
            {
                "item_id": i,
                "question": f"q{i}",
                "finding": "ok",
                "severity": "info",
                "confidence": "high",
                "evidence": [],
                "could_not_establish": False,
            }
            for i in range(1, 10)
        ],
        "potential_missing_rule": [],
        "could_not_independently_establish": [],
        "raw_response_ref": None,
    }
    errors = write_audit_result(work_dir, payload)
    assert not errors, errors


def _simulate_stale_stage1_input_workbook(work_dir) -> None:
    """Reproduce the exact real-world corruption: an edited registration
    workbook, from long before today's canonical maintenance, cascades a
    stale/failed status onto every downstream Stage 1-4 checkpoint --
    including the (unrelated) canonical-season export checkpoint this test
    already wrote above.
    """

    state = PipelineState(work_dir)
    # A prior, already-completed CONFIG checkpoint is required for the write
    # below to actually cascade (``write_stage`` only invalidates downstream
    # stages on a DONE/FAILED write).
    state.write_stage(
        StageName.CONFIG,
        {"input_path": "input.xlsx"},
        status=StageStatus.DONE,
    )
    state.write_stage(
        StageName.CONFIG,
        {
            "input_path": "input.xlsx",
            "error": "Stale etter endring i input: Input workbook changed",
        },
        status=StageStatus.FAILED,
    )


def _publish(work_dir, **overrides):
    kwargs = dict(work_dir=str(work_dir), repo_dir=str(work_dir), push=False, confirm_public=True)
    kwargs.update(overrides)
    action = DEFAULT_REGISTRY.build("publish_pages", **kwargs)
    return DEFAULT_REGISTRY.execute(action, approved=True)


class TestWithdrawalMaintenancePublishableDespiteStaleStage1:
    def test_canonical_export_survives_stage1_input_staleness(self, tmp_path):
        plan = _withdrawal_maintenance_plan()
        problem = {
            "teams": [
                _team("Skien", "Skien", "JU12"),
                _team("Ringerike", "Ringerike 1", "JU12"),
                _team("Frisk Asker", "Frisk Asker Svart", "JU12"),
            ],
            "ice_time_minutes": {"JU12": 120},
            "parallel_games": {"JU12": 2},
        }
        canonical_revision = "deadbeef" * 8

        _write_canonical_season_export(tmp_path, plan=plan, problem=problem, canonical_revision=canonical_revision)

        # Corrupt the Stage 1-4 run chain exactly like the real incident: an
        # input.xlsx edit from long before today cascades stale/failed onto
        # every downstream checkpoint, including EXPORT.
        _simulate_stale_stage1_input_workbook(tmp_path)
        export_envelope = PipelineState(tmp_path).read_envelope(StageName.EXPORT)
        assert export_envelope["status"] == StageStatus.FAILED.value
        assert export_envelope["stale"] is True

        # The canonical-season export must resolve for publication anyway: its
        # own `is_canonical_season_export`/`stale_export` markers are
        # authoritative, not the Stage 1 planning-pipeline cascade.
        context = resolve_publish_verification_context(work_dir=str(tmp_path))
        assert context["canonical_season"] == "2026-2027"
        assert context["canonical_revision"] == canonical_revision

        # The full publication preflight must not cite Stage 1/input-workbook
        # staleness either -- it may still legitimately refuse for an unrelated
        # reason in this minimal fixture (no real season_plan.xlsx/Spond
        # bundle was generated here; that artifact-parity gate has its own
        # dedicated coverage in test_export_parity.py), but never for the
        # bug this regression locks in.
        _init_repo(tmp_path)
        _write_pass_audit(tmp_path)
        result = _publish(tmp_path)
        assert "input" not in result.summary.lower(), result.summary
        assert "stage 1" not in result.summary.lower(), result.summary
        assert "workbook" not in result.summary.lower(), result.summary

    def test_stale_canonical_export_itself_still_blocks(self, tmp_path):
        """The bypass is scoped to Stage 1 staleness, not export freshness."""

        plan = _withdrawal_maintenance_plan()
        problem = {
            "teams": [
                _team("Skien", "Skien", "JU12"),
                _team("Ringerike", "Ringerike 1", "JU12"),
                _team("Frisk Asker", "Frisk Asker Svart", "JU12"),
            ],
            "ice_time_minutes": {"JU12": 120},
            "parallel_games": {"JU12": 2},
        }
        _write_canonical_season_export(
            tmp_path, plan=plan, problem=problem, canonical_revision="cafebabe" * 8
        )
        # Canonical state advanced past this export (e.g. another mutation
        # landed after it was generated): mark it stale the way `season
        # export` itself would.
        data = PipelineState(tmp_path).read_stage(StageName.EXPORT)
        data["stale_export"] = True
        PipelineState(tmp_path).write_stage(StageName.EXPORT, data, status=StageStatus.DONE)

        import pytest

        from tournament_scheduler.pipeline.verification_context import VerificationContextError

        with pytest.raises(VerificationContextError):
            resolve_publish_verification_context(work_dir=str(tmp_path))

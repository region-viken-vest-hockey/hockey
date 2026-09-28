"""Publication preflight must verify the reviewed export against its bound
verification context, not a problem rebuilt from mutable Stage 1/2 state.

The regression these tests lock in: after hundreds of legitimate canonical
mutations, the publish preflight rebuilt a planning problem from the live
Stage 1 config and reported a hundred-plus false hard violations -- host-
confirmed shorter bookings as ``ice_time_governing_minimum``, canonical roster
renames as ``unregistered_team``, durable participation withdrawals as round/
bye shape violations -- while ``season findings`` correctly reported none for
the same canonical revision. The preflight now resolves the same
provenance-bound problem promotion uses, and fails closed when it cannot.
"""

from __future__ import annotations

import subprocess

from tournament_scheduler.final_verification import verify_final_candidate
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.pipeline.operator_action import DEFAULT_REGISTRY
from tournament_scheduler.pipeline.publish_hard_verification import (
    current_hard_verification,
    current_hard_violations,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.pipeline.stage4_export_verification import (
    _build_export_verification_problem,
)
from tournament_scheduler.pipeline.verification_context import (
    VerificationContextError,
    build_verification_context,
    resolve_publish_verification_context,
)
from tournament_scheduler.planning_contract import extract_candidate, verify_candidate


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _init_repo(repo_dir) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    (repo_dir / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)


def _publish(work_dir, **overrides):
    kwargs = dict(
        work_dir=str(work_dir),
        repo_dir=str(work_dir),
        push=False,
        confirm_public=True,
    )
    kwargs.update(overrides)
    action = DEFAULT_REGISTRY.build("publish_pages", **kwargs)
    return DEFAULT_REGISTRY.execute(action, approved=True)


def _write_bound_export(work_dir, *, plan: dict, problem: dict) -> str:
    """Write a reviewed, provenance-bound Stage 4 checkpoint.

    The stored ``verify_result`` uses the planning-contract verifier, exactly
    as Stage 4 itself does; the publish gate layers the stricter final verifier
    on the same bound problem.
    """
    candidate = extract_candidate({"plan": plan})
    verify_result = verify_candidate(candidate, problem)
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
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {
            "output_files": {"html": str(export_dir / "season_plan.html")},
            "export_fingerprint": fingerprint,
            "verify_result": verify_result,
            "verification_context": context,
            "reviewed_plan": dict(candidate),
        },
        status=StageStatus.DONE,
    )
    return fingerprint


def _write_pass_audit(work_dir) -> None:
    checkpoint = PipelineState(work_dir).read_stage(StageName.EXPORT)
    payload = {
        "schema_version": 1,
        "audit_id": "audit-pass",
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
    from tournament_scheduler.pipeline.audit_result import write_audit_result

    errors = write_audit_result(work_dir, payload)
    assert not errors, errors


def _games(*pairs: tuple[str, str]) -> list[dict]:
    return [
        {"home": home, "away": away, "parallel_slot": 0, "round_number": index + 1}
        for index, (home, away) in enumerate(pairs)
    ]


def _tournament(teams: list[dict], *, tid: str = "rvv-0001", age: str = "U10", host: str = "Jar", arena: str = "Jar Isforum") -> dict:
    labels = [team["label"] for team in teams]
    pairs = []
    for index, home in enumerate(labels):
        for away in labels[index + 1 :]:
            pairs.append((home, away))
    return {
        "id": tid,
        "date": "2026-01-05",
        "arena": arena,
        "age_group": age,
        "host_club": host,
        "teams": teams,
        "games": _games(*pairs),
    }


def _plan(tournament: dict) -> dict:
    return {"schema_version": 1, "source": {"planner": "test"}, "tournaments": [tournament]}


def _team(club: str, label: str, age: str) -> dict:
    return {"club": club, "label": label, "age_group": age}


# ---------------------------------------------------------------------------
# Bound problem is authoritative
# ---------------------------------------------------------------------------


class TestPreflightUsesBoundProblemNotRebuiltConfig:
    def test_shorter_accepted_booking_is_not_a_false_governing_minimum(self, tmp_path):
        """A host-confirmed per-tournament override must reach the preflight.

        The Stage 1 config carries the age-group default below the governing
        floor; only the export's bound problem carries the accepted override.
        A naive rebuild flags it; the provenance-bound preflight must not.
        """
        _init_repo(tmp_path)
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Skien", "Skien 2", "U10"),
        ]
        plan = _plan(_tournament(teams))
        tournament_id = plan["tournaments"][0]["id"]
        # 115 < governing 120, but the host confirmed a 120-minute window.
        bound_problem = {
            "teams": teams,
            "ice_time_minutes": {"U10": 115},
            "ice_time_minutes_overrides": {tournament_id: 120},
            "parallel_games": {"U10": 2},
        }

        # The mutable Stage 1 config (below the floor, no override) is what the
        # old preflight rebuilt from.
        config = {
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "teams": teams,
            "age_groups": ["U10"],
            "ice_time_minutes": {"U10": 115},
        }
        PipelineState(tmp_path).write_stage(StageName.CONFIG, config, status=StageStatus.DONE)
        state = PipelineState(tmp_path)
        rebuilt = _build_export_verification_problem(
            {"start_date": "2026-01-01", "end_date": "2026-12-31", **config}, state
        )
        candidate = extract_candidate({"plan": plan})
        rebuilt_result = verify_final_candidate(candidate, rebuilt)
        assert not rebuilt_result["ok"]
        assert "ice_time_governing_minimum" in {
            v.get("code") for v in rebuilt_result["violations"]
        }

        # The provenance-bound preflight verifies against the accepted override.
        _write_bound_export(tmp_path, plan=plan, problem=bound_problem)
        _write_pass_audit(tmp_path)
        report = current_hard_verification(str(tmp_path))
        assert report["verifiable"] is True and report["ok"] is True, report

        result = _publish(tmp_path)
        assert result.status == "ok", result.summary

    def test_newly_proposed_short_slot_without_override_is_not_permitted(self, tmp_path):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Skien", "Skien 2", "U10"),
        ]
        candidate = extract_candidate({"plan": _plan(_tournament(teams))})
        proposed_problem = {
            "teams": teams,
            "ice_time_minutes": {"U10": 115},
            "parallel_games": {"U10": 2},
        }
        result = verify_final_candidate(candidate, proposed_problem)
        assert not result["ok"]
        assert "ice_time_governing_minimum" in {v.get("code") for v in result["violations"]}


class TestBoundWithdrawalAndRenameOverlays:
    def test_withdrawal_projection_prevents_false_bye_violation(self, tmp_path):
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Skien", "Skien 2", "U10"),
        ]
        registered = teams + [_team("Skien", "Skien 3", "U10")]
        plan = _plan(_tournament(teams))
        candidate = extract_candidate({"plan": plan})

        withdrawal = {
            "scope": "tournament",
            "tournament_ids": [plan["tournaments"][0]["id"]],
            "club": "Skien",
            "label": "Skien 3",
            "age_group": "U10",
            "effective_from": "2026-01-01",
        }
        bound_problem = {
            "teams": registered,
            "ice_time_minutes": {"U10": 120},
            "parallel_games": {"U10": 2},
            "withdrawn_tournament_teams": [withdrawal],
            "withdrawn_ineligible_teams": [withdrawal],
        }
        naive_problem = {
            "teams": registered,
            "ice_time_minutes": {"U10": 120},
            "parallel_games": {"U10": 2},
        }

        assert verify_final_candidate(candidate, bound_problem)["ok"]
        naive = verify_final_candidate(candidate, naive_problem)
        assert not naive["ok"]
        assert "bye_team_not_allowed" in {v.get("code") for v in naive["violations"]}

    def test_canonical_roster_rename_prevents_false_unregistered_team(self, tmp_path):
        teams = [
            _team("Ringerike", "Ringerike Svart", "U11"),
            _team("Ringerike", "Ringerike Gul", "U11"),
            _team("Frisk Asker", "Frisk Asker 1", "U11"),
        ]
        plan = _plan(
            _tournament(teams, age="U11", host="Ringerike", arena="Ringerike Arena")
        )
        candidate = extract_candidate({"plan": plan})
        bound_problem = {
            "teams": teams,
            "ice_time_minutes": {"U11": 120},
            "parallel_games": {"U11": 2},
        }
        stale_roster_problem = {
            "teams": [
                _team("Ringerike", "Ringerike 1", "U11"),
                _team("Ringerike", "Ringerike 2", "U11"),
                _team("Frisk Asker", "Frisk Asker 1", "U11"),
            ],
            "ice_time_minutes": {"U11": 120},
            "parallel_games": {"U11": 2},
        }

        assert verify_final_candidate(candidate, bound_problem)["ok"]
        stale = verify_final_candidate(candidate, stale_roster_problem)
        assert not stale["ok"]
        assert "unregistered_team" in {v.get("code") for v in stale["violations"]}


# ---------------------------------------------------------------------------
# Fail closed on missing/stale/inconsistent provenance
# ---------------------------------------------------------------------------


class TestPreflightFailsClosed:
    def _write_valid_bound_export(self, tmp_path) -> None:
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Skien", "Skien 2", "U10"),
        ]
        _write_bound_export(
            tmp_path,
            plan=_plan(_tournament(teams)),
            problem={
                "teams": teams,
                "ice_time_minutes": {"U10": 120},
                "parallel_games": {"U10": 2},
            },
        )

    def test_malformed_schema_version_fails_closed(self, tmp_path):
        """A corrupt (non-numeric) schema_version must be a typed failure, not
        an uncaught ValueError/TypeError from int()."""
        import pytest

        _init_repo(tmp_path)
        self._write_valid_bound_export(tmp_path)
        checkpoint = PipelineState(tmp_path).read_stage(StageName.EXPORT)
        checkpoint["verification_context"]["schema_version"] = "not-a-number"
        PipelineState(tmp_path).write_stage(
            StageName.EXPORT, checkpoint, status=StageStatus.DONE
        )

        with pytest.raises(VerificationContextError):
            resolve_publish_verification_context(work_dir=str(tmp_path))

        report = current_hard_verification(str(tmp_path))
        assert report["verifiable"] is False
        assert "invalid verification-context schema_version" in report["error"]
        assert current_hard_violations(str(tmp_path)) == [report["error"]]

        result = _publish(tmp_path)
        assert result.status == "blocked"
        assert any("invalid verification-context schema_version" in p for p in result.problems)

    def test_unsupported_schema_version_fails_closed(self, tmp_path):
        import pytest

        _init_repo(tmp_path)
        self._write_valid_bound_export(tmp_path)
        checkpoint = PipelineState(tmp_path).read_stage(StageName.EXPORT)
        checkpoint["verification_context"]["schema_version"] = 999
        PipelineState(tmp_path).write_stage(
            StageName.EXPORT, checkpoint, status=StageStatus.DONE
        )

        with pytest.raises(VerificationContextError):
            resolve_publish_verification_context(work_dir=str(tmp_path))
        report = current_hard_verification(str(tmp_path))
        assert report["verifiable"] is False
        assert "unsupported verification-context schema_version=999" in report["error"]

    def test_missing_verification_context_blocks_with_actionable_error(self, tmp_path):
        _init_repo(tmp_path)
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        (export_dir / "season_plan.html").write_text("<h1>plan</h1>", encoding="utf-8")
        plan = _plan(_tournament([_team("Jar", "Jar 1", "U10"), _team("Skien", "Skien 1", "U10"), _team("Skien", "Skien 2", "U10")]))
        candidate = extract_candidate({"plan": plan})
        PipelineState(tmp_path).write_stage(
            StageName.EXPORT,
            {
                "output_files": {"html": str(export_dir / "season_plan.html")},
                "export_fingerprint": stable_payload_sha256(candidate.get("tournaments", [])),
                "verify_result": {"ok": True, "violations": []},
                "reviewed_plan": dict(candidate),
            },
            status=StageStatus.DONE,
        )

        report = current_hard_verification(str(tmp_path))
        assert report["verifiable"] is False
        assert "verification-context provenance" in report["error"]
        assert current_hard_violations(str(tmp_path)) == [report["error"]]

    def test_tampered_reviewed_plan_blocks(self, tmp_path):
        _init_repo(tmp_path)
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Skien", "Skien 2", "U10"),
        ]
        plan = _plan(_tournament(teams))
        problem = {
            "teams": teams,
            "ice_time_minutes": {"U10": 120},
            "parallel_games": {"U10": 2},
        }
        _write_bound_export(tmp_path, plan=plan, problem=problem)

        checkpoint = PipelineState(tmp_path).read_stage(StageName.EXPORT)
        tampered = dict(checkpoint["reviewed_plan"])
        tampered["tournaments"] = [
            {**tampered["tournaments"][0], "date": "2026-02-06"}
        ]
        checkpoint["reviewed_plan"] = tampered
        PipelineState(tmp_path).write_stage(StageName.EXPORT, checkpoint, status=StageStatus.DONE)

        report = current_hard_verification(str(tmp_path))
        assert report["verifiable"] is False
        assert "no longer matches the reviewed Stage 4 export" in report["error"]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


class TestPreflightProvenanceReport:
    def test_report_exposes_revision_fingerprints_and_rule_set(self, tmp_path):
        _init_repo(tmp_path)
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Skien", "Skien 2", "U10"),
        ]
        plan = _plan(_tournament(teams))
        problem = {
            "teams": teams,
            "ice_time_minutes": {"U10": 120},
            "parallel_games": {"U10": 2},
        }
        _write_bound_export(tmp_path, plan=plan, problem=problem)
        _write_pass_audit(tmp_path)

        report = current_hard_verification(str(tmp_path))
        assert report["verifiable"] and report["ok"]
        assert report["export_fingerprint"] == report["candidate_fingerprint"]
        assert report["problem_fingerprint"]
        assert report["rule_set"] == "final_verification.verify_final_candidate"

        result = _publish(tmp_path)
        assert result.status == "ok", result.summary

    def test_blocked_preflight_exposes_structured_provenance(self, tmp_path):
        """A blocked preflight must explain which revision/ruleset it checked."""
        _init_repo(tmp_path)
        teams = [
            _team("Jar", "Jar 1", "U10"),
            _team("Skien", "Skien 1", "U10"),
            _team("Skien", "Skien 2", "U10"),
        ]
        plan = _plan(_tournament(teams))
        plan["tournaments"][0]["games"] = plan["tournaments"][0]["games"][:2]  # missing pair
        problem = {
            "teams": teams,
            "ice_time_minutes": {"U10": 120},
            "parallel_games": {"U10": 2},
        }
        _write_bound_export(tmp_path, plan=plan, problem=problem)
        _write_pass_audit(tmp_path)

        result = _publish(tmp_path)
        assert result.status == "blocked"
        evidence = list(result.evidence)
        assert any(e == "hard_verification_rule_set=final_verification.verify_final_candidate" for e in evidence)
        assert any(e.startswith("hard_verification_export_fingerprint=") for e in evidence)
        assert any(e.startswith("hard_verification_problem_fingerprint=") for e in evidence)
        assert any(
            e.startswith("hard_verification_violation_counts=") and "round_robin_missing_pair" in e
            for e in evidence
        )

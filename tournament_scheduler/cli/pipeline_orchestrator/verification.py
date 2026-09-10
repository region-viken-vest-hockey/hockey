"""Mid-planning decision-problem construction and hard-verification checks."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from rich.console import Console


def _mid_planning_decision_problem(
    cfg: "dict[str, Any]", scraping: "dict[str, Any]", start: "Any", end: "Any"
) -> "dict[str, Any] | None":
    """Best-effort ``planning_problem`` for mid-planning critic decisions.

    Returns ``None`` (rather than raising) when it can't be built from *cfg*/
    *scraping* — the A/B report and decision prompt both degrade gracefully
    to self-consistency-only verification in that case
    (``planning_contract.verify_candidate``), matching how ``plan ab``
    already treats a missing ``--problem``.
    """
    try:
        from ...planning_contract import build_planning_problem

        return build_planning_problem(cfg, scraping, start.date(), end.date())
    except Exception:
        return None


def _baseline_hard_violations_for_plan(
    plan: "dict[str, Any] | None", problem: "dict[str, Any] | None"
) -> "list[str]":
    """Independently verify *plan* against the canonical hard verifier and
    return its violations as ``"code: message"`` strings (empty when *plan*
    passes or can't be extracted/verified).

    Used to populate ``DecisionContext.baseline_hard_violations`` for the
    Stage 3 interactive/Pareto contexts that construct a
    :class:`~..application.decisions.DecisionContext` directly rather than
    via :func:`..stage3_decision.build_stage3_decision_context` (which
    derives it from an A/B report's ``old`` verification instead) -- a
    ``keep_baseline`` decision must not finalize a plan that already fails
    hard verification (issue #264 real-run finding).
    """
    if plan is None:
        return []
    try:
        from ...planning_contract import extract_candidate, verify_candidate

        candidate = extract_candidate(plan)
    except (ValueError, KeyError):
        return []
    try:
        result = verify_candidate(candidate, problem)
    except Exception:
        return []
    if result.get("ok", True):
        return []
    return [f"{v.get('code')}: {v.get('message')}" for v in (result.get("violations") or [])]


def _assert_hard_verification_before_export(
    plan: "dict[str, Any] | None",
    problem: "dict[str, Any] | None",
    strict: bool,
    console: "Console",
    log_fn: "Any",
) -> bool:
    """Hard-verifier gate immediately before Stage 4 materializes a
    production export (issue #264 real-run finding).

    A `2026-09-07T0525` production export shipped a `keep_baseline` decision
    whose `final_verify_result.ok` was `False`. Blocking that at decision
    time (``_BASELINE_HARD_VIOLATION_BLOCKED_ACTIONS`` in
    ``application.decisions``) is necessary but not sufficient on its own --
    a resumed ``--resume-from 4`` run reaches Stage 4 directly without
    re-emitting a decision, so this independently re-verifies *plan* right
    before export regardless of how it got here.

    External calendar conflicts and participation-target mismatches (the
    original motivating case above) are no longer hard violations --
    `planning_contract.verify_candidate` now surfaces them as non-blocking
    `manual_external_conflict_placements`/`manual_participation_placements`,
    routed to manual placement (`manual_schedule.html`) instead, mirroring
    `host_calendar_status_unknown`/`unresolved_hosting_obligations`. This
    gate remains generic over whatever `violations` *are* still hard (e.g.
    duplicate participation, arena double-booking within the plan itself).

    Returns True when export should proceed. In strict mode (the default) a
    hard-failing plan blocks export outright; ``--non-strict`` logs a
    warning and continues, matching every other pipeline gate's non-strict
    posture (e.g. :func:`_run_approval_gate`).
    """
    violations = _baseline_hard_violations_for_plan(plan, problem)
    if not violations:
        return True
    console.print(
        f"  [red]✗[/red] Planen feiler hard verifisering ({len(violations)} brudd) — "
        "kan ikke materialiseres som produksjonseksport."
    )
    for violation in violations:
        console.print(f"    • {violation}")
    log_fn(f"Stage 4 hard-verification gate FAILED: {'; '.join(violations)}")
    if strict:
        return False
    console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
    return True


def _reconcile_verified_manual_state(
    plan: "dict[str, Any] | None",
    problem: "dict[str, Any] | None",
    log_fn: "Any",
) -> None:
    """Make the final hard-verifier's manual/unresolved findings authoritative
    on *plan* before Stage 4 renders it (issue #274 P0).

    ``SeasonPlanner`` computes ``unresolved_hosting_obligations``/
    ``unresolved_external_conflicts``/``unresolved_participation_shortfalls``
    while it builds the plan, but later pipeline steps (optimizer passes,
    A/B adoption, mid-planning decisions) can change the final candidate
    without recomputing those lists. ``_assert_hard_verification_before_export``
    already independently re-runs :func:`planning_contract.verify_candidate`
    on the true final candidate for the hard-violation gate; this reuses that
    same canonical recomputation and writes its non-blocking findings back
    onto *plan* so ``manual_schedule.html`` can never omit something the
    verifier -- and the evidence bundle's ``final_verify_result`` -- already
    found. Best-effort: any failure leaves *plan* untouched rather than
    blocking export, matching :func:`_write_run_evidence_bundle`'s posture.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("plan"), dict):
        return
    try:
        from ...planning_contract import extract_candidate, verify_candidate

        candidate = extract_candidate(plan)
        result = verify_candidate(candidate, problem)
    except Exception as exc:  # noqa: BLE001 - best-effort, never blocks export
        log_fn(f"Stage 4 manual-state reconciliation skipped: {exc}")
        return

    plan_dict = plan["plan"]
    plan_dict["unresolved_hosting_obligations"] = [
        {
            "club": item.get("club", ""),
            "age_group": item.get("age_group", ""),
            "reason": (
                "required hosting obligation has no verified feasible automatic slot"
            ),
        }
        for item in result.get("unresolved_hosting_obligations") or []
    ]
    plan_dict["unresolved_external_conflicts"] = [
        {
            "tournament_id": item.get("tournament_id", ""),
            "host_club": item.get("host_club", ""),
            "age_group": item.get("age_group", ""),
            "date": item.get("date", ""),
            "reason": "external calendar conflict requires manual resolution",
        }
        for item in result.get("manual_external_conflict_placements") or []
    ]
    plan_dict["unresolved_participation_shortfalls"] = [
        {
            "club": item.get("club", ""),
            "label": item.get("label", ""),
            "age_group": item.get("age_group", ""),
            "actual": item.get("actual", ""),
            "target": item.get("target", ""),
            "reason": "actual participation count does not match target",
        }
        for item in result.get("manual_participation_placements") or []
    ]
    log_fn(
        "Stage 4 manual-state reconciled from final verification: "
        f"{len(plan_dict['unresolved_hosting_obligations'])} unresolved hosting, "
        f"{len(plan_dict['unresolved_external_conflicts'])} external conflicts, "
        f"{len(plan_dict['unresolved_participation_shortfalls'])} participation shortfalls"
    )


def _write_run_evidence_bundle(
    args: "argparse.Namespace",
    state: "Any",
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    start: "Any",
    end: "Any",
    plan: "dict[str, Any] | None",
    log_fn: "Any",
) -> None:
    """Write the sanitized per-run provenance/evidence bundle (issue #264 P0)
    alongside the Stage 4 export, best-effort -- a failure here must never
    fail or roll back an otherwise-successful export, since the bundle is
    an audit artifact layered on top of the pipeline, not a dependency of it
    (same posture as ``_manifest_record``/``_manifest_finalize``).
    """
    try:
        from ...pipeline.evidence_bundle import build_run_evidence_bundle, read_stage3_attempt_log
        from ...pipeline.run_manifest import RunManifest
        from ...pipeline.state import StageName
        from ...planning_contract import extract_candidate, score_candidate, verify_candidate

        manifest = RunManifest(state.work_dir).read()
        export_checkpoint = state.read_stage(StageName.EXPORT) or {}

        final_candidate = None
        if plan is not None:
            try:
                final_candidate = extract_candidate(plan)
            except ValueError:
                final_candidate = None

        problem = _mid_planning_decision_problem(cfg, scraping, start, end)
        verify_result = verify_candidate(final_candidate, problem) if final_candidate is not None else None
        score_result = score_candidate(final_candidate, problem=problem) if final_candidate is not None else None

        bundle = build_run_evidence_bundle(
            run_id=str(manifest.get("run_id") or ""),
            input_fingerprint=manifest.get("input_fingerprint"),
            decision_log=manifest.get("decision_log"),
            scraping_checkpoint=scraping,
            stage3_attempt_log=read_stage3_attempt_log(state.work_dir),
            final_candidate=final_candidate,
            final_verify_result=verify_result,
            final_score_result=score_result,
            export_dir=export_checkpoint.get("export_dir"),
            export_output_files=export_checkpoint.get("output_files"),
            export_fingerprint=export_checkpoint.get("export_fingerprint"),
            export_verify_result=export_checkpoint.get("verify_result"),
        )

        export_dir = export_checkpoint.get("export_dir")
        target_dir = Path(export_dir) if export_dir else Path(args.work_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        import json as _json

        (target_dir / "evidence_bundle.json").write_text(
            _json.dumps(bundle, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        log_fn(f"Evidence bundle written to {target_dir / 'evidence_bundle.json'}")
    except Exception as exc:
        log_fn(f"Could not write run evidence bundle: {exc}")

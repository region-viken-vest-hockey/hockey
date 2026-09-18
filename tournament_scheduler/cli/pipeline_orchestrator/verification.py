"""Mid-planning decision-problem construction and hard-verification checks."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .hard_verification_gate import (
    _assert_hard_verification_before_export,
    _baseline_hard_violations_for_plan,
)
from ...plan_derived_state import reconcile_plan_derived_state

__all__ = [
    "_assert_hard_verification_before_export",
    "_baseline_hard_violations_for_plan",
    "_mid_planning_decision_problem",
    "_reconcile_verified_manual_state",
    "_write_run_evidence_bundle",
]


def _mid_planning_decision_problem(
    cfg: "dict[str, Any]", scraping: "dict[str, Any]", start: "Any", end: "Any", work_dir: "str | None" = None
) -> "dict[str, Any] | None":
    """Best-effort ``planning_problem`` for mid-planning critic decisions.

    Returns ``None`` (rather than raising) when it can't be built from *cfg*/
    *scraping* — the A/B report and decision prompt both degrade gracefully
    to self-consistency-only verification in that case
    (``planning_contract.verify_candidate``), matching how ``plan ab``
    already treats a missing ``--problem``.

    *work_dir*, when given, folds this run's active operator waivers into the
    problem so the same explicit exceptions the operator authorized are
    visible to every mid-planning verification/repair decision.
    """
    try:
        from ...canonical_baseline import resolve_canonical_baseline
        from ...operator_waivers import load_active_waivers
        from ...planning_contract import build_planning_problem

        waivers = load_active_waivers(work_dir) if work_dir else None
        # Baseline-aware planning (issue #355): a promoted canonical season's
        # locks belong in every mid-planning decision/verification problem, so
        # the same constraints the export gate enforces are visible to each
        # repair/optimize/apply decision as well.
        canonical_baseline = resolve_canonical_baseline(cfg, start.date(), end.date())
        return build_planning_problem(
            cfg,
            scraping,
            start.date(),
            end.date(),
            waivers=waivers,
            canonical_baseline=canonical_baseline,
        )
    except Exception:
        return None


def _reconcile_verified_manual_state(
    plan: "dict[str, Any] | None",
    problem: "dict[str, Any] | None",
    log_fn: "Any",
) -> None:
    """Make final verification's manual/unresolved findings authoritative
    on *plan* before Stage 4 renders it (issue #274 P0).

    ``SeasonPlanner`` computes ``unresolved_hosting_obligations``/
    ``unresolved_external_conflicts``/``unresolved_participation_shortfalls``
    while it builds the plan, but later pipeline steps (optimizer passes,
    A/B adoption, mid-planning decisions) can change the final candidate
    without recomputing those lists. Final verification is therefore rerun on
    the true candidate and its non-blocking findings plus publication
    readiness are written back onto *plan*. Best-effort: any failure leaves
    *plan* untouched rather than blocking export.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("plan"), dict):
        return
    try:
        from ...final_verification import verify_final_candidate
        from ...planning_contract import extract_candidate

        candidate = extract_candidate(plan)
        result = verify_final_candidate(candidate, problem)
    except Exception as exc:  # noqa: BLE001 - best-effort, never blocks export
        log_fn(f"Stage 4 manual-state reconciliation skipped: {exc}")
        return

    plan_dict = plan["plan"]
    # Refresh every verifier-derived projection (hosting/readiness,
    # external-calendar conflicts, participation shortfalls and the
    # operator-waiver audit rows) through the single shared reconciliation, so
    # Stage 4 can never render a snapshot that disagrees with the verifier that
    # owns the rule. Planner-time facts such as unresolved_tournament_placements
    # are deliberately not touched here.
    reconcile_plan_derived_state(plan_dict, result, problem=problem)
    log_fn(
        "Stage 4 manual-state reconciled from final verification: "
        f"{len(plan_dict['unresolved_hosting_obligations'])} unresolved hosting, "
        f"{len(plan_dict['unresolved_external_conflicts'])} external conflicts, "
        f"{len(plan_dict['unresolved_participation_shortfalls'])} participation shortfalls, "
        f"readiness={plan_dict['publication_readiness'].get('status', 'unknown')}"
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
        from ...final_verification import verify_final_candidate
        from ...pipeline.evidence_bundle import (
            build_final_operator_evidence,
            build_run_evidence_bundle,
            read_stage3_attempt_log,
        )
        from ...pipeline.controller_trace import controller_trace_reference
        from ...pipeline.run_manifest import RunManifest
        from ...pipeline.state import StageName
        from ...planning_contract import extract_candidate, score_candidate

        manifest = RunManifest(state.work_dir).read()
        export_checkpoint = state.read_stage(StageName.EXPORT) or {}

        final_candidate = None
        if plan is not None:
            try:
                final_candidate = extract_candidate(plan)
            except ValueError:
                final_candidate = None

        problem = _mid_planning_decision_problem(cfg, scraping, start, end, state.work_dir)
        verify_result = (
            verify_final_candidate(final_candidate, problem)
            if final_candidate is not None
            else None
        )
        score_result = score_candidate(final_candidate, problem=problem) if final_candidate is not None else None

        run_id = str(manifest.get("run_id") or "")
        final_candidate_fingerprint = None
        if final_candidate is not None:
            from ...pipeline.fingerprints import stable_payload_sha256

            final_candidate_fingerprint = stable_payload_sha256(final_candidate.get("tournaments", []))
        final_operator_evidence = build_final_operator_evidence(
            run_id=run_id,
            plan_dict=(plan or {}).get("plan") if isinstance(plan, dict) else None,
            final_candidate_fingerprint=final_candidate_fingerprint,
            export_fingerprint=export_checkpoint.get("export_fingerprint"),
            final_verify_result=verify_result,
            approval_status=(
                export_checkpoint.get("approval_status")
                if isinstance(export_checkpoint.get("approval_status"), dict)
                else None
            ),
        )

        bundle = build_run_evidence_bundle(
            run_id=run_id,
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
            final_operator_evidence=final_operator_evidence,
            controller_trace=controller_trace_reference(state.work_dir, run_id),
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

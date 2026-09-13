"""Mid-planning decision-problem construction and hard-verification checks."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .hard_verification_gate import (
    _assert_hard_verification_before_export,
    _baseline_hard_violations_for_plan,
)

__all__ = [
    "_assert_hard_verification_before_export",
    "_baseline_hard_violations_for_plan",
    "_mid_planning_decision_problem",
    "_reconcile_verified_manual_state",
    "_write_run_evidence_bundle",
]


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

    # issue #321: `SeasonPlanner` already computed a richer
    # `unresolved_participation_shortfalls` (category -- e.g.
    # `participation_under_target_same_date_capacity` -- plain-language
    # reason, and half/period) plus `same_date_capacity_evidence` (the #318
    # structured same-date-capacity proof). The independent final verifier
    # re-derives *which* findings exist from the true final candidate (the
    # authoritative count/membership after any post-planning changes), but
    # it only reports club/label/age_group/half/actual/target -- it does not
    # carry SeasonPlanner's cause classification or #318 evidence. Look each
    # verifier finding up against the plan's own prior finding (by
    # club/label/age_group/half) to recover that provenance instead of
    # degrading every mismatch to the same generic reason.
    previous_shortfalls = [
        item for item in (plan_dict.get("unresolved_participation_shortfalls") or []) if isinstance(item, dict)
    ]
    shortfall_lookup: dict[tuple, dict] = {}
    shortfall_lookup_no_label: dict[tuple, dict] = {}
    for item in previous_shortfalls:
        half = item.get("period") or item.get("half")
        shortfall_lookup[(item.get("club", ""), item.get("label", ""), item.get("age_group", ""), half)] = item
        shortfall_lookup_no_label.setdefault((item.get("club", ""), item.get("age_group", ""), half), item)

    evidence_by_age_group_half: dict[tuple, list] = {}
    for entry in plan_dict.get("same_date_capacity_evidence") or []:
        if not isinstance(entry, dict):
            continue
        evidence_by_age_group_half.setdefault((entry.get("age_group"), entry.get("period")), []).append(entry)

    reconciled_participation: list[dict[str, Any]] = []
    for item in result.get("manual_participation_placements") or []:
        club = item.get("club", "")
        label = item.get("label", "")
        age_group = item.get("age_group", "")
        half = item.get("half")
        source = shortfall_lookup.get((club, label, age_group, half)) or shortfall_lookup_no_label.get(
            (club, age_group, half)
        )
        actual_raw, target_raw = item.get("actual", ""), item.get("target", "")
        try:
            under_target = int(actual_raw) < int(target_raw)
        except (TypeError, ValueError):
            under_target = True
        category = (source or {}).get(
            "category", "participation_under_target" if under_target else "participation_over_target"
        )
        reason = (source or {}).get("reason", "actual participation count does not match target")
        reconciled_entry: dict[str, Any] = {
            "club": club,
            "label": label,
            "age_group": age_group,
            "actual": actual_raw,
            "target": target_raw,
            "category": category,
            "reason": reason,
        }
        if half:
            reconciled_entry["half"] = half
        if category == "participation_under_target_same_date_capacity":
            evidence = evidence_by_age_group_half.get((age_group, half))
            if evidence:
                reconciled_entry["same_date_capacity_evidence"] = evidence
        reconciled_participation.append(reconciled_entry)

    plan_dict["unresolved_participation_shortfalls"] = reconciled_participation
    readiness = dict(result.get("publication_readiness") or {})
    # issue #323 P0: unresolved_tournament_placements is a baseline-planner-
    # time fact about tournaments that were never created -- there is no
    # candidate tournament for `verify_final_candidate` to recompute this
    # from, so it can't come from `result`. Fold it into the readiness
    # reasons here from the plan's own (untouched) list instead, the same
    # way the other unresolved_* findings block PUBLISHABLE status.
    unresolved_placements = plan_dict.get("unresolved_tournament_placements") or []
    if unresolved_placements and readiness.get("status") != "INVALID":
        reasons = list(readiness.get("reasons") or [])
        reasons.append({"code": "tournament_placement_shortfall", "count": len(unresolved_placements)})
        readiness["reasons"] = reasons
        readiness["status"] = "REVIEW_REQUIRED"
        readiness["publishable"] = False
    plan_dict["publication_readiness"] = readiness
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
        from ...pipeline.evidence_bundle import build_run_evidence_bundle, read_stage3_attempt_log
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

        problem = _mid_planning_decision_problem(cfg, scraping, start, end)
        verify_result = (
            verify_final_candidate(final_candidate, problem)
            if final_candidate is not None
            else None
        )
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
